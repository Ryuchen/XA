"""Preserve already-paid add-on segment credits across a later main refund."""
from rest_framework.exceptions import ValidationError


def snapshot_rows(manifest):
    return [{'id': row.pk, 'payment_id': row.payment_id, 'segment_id': row.segment_id,
             'account_id': row.segment.provider_account_id, 'amount': row.amount,
             'report_id': row.report_id, 'income_transaction_id': row.income_transaction_id,
             'settled_at': row.settled_at.isoformat() if row.settled_at else None}
            for row in manifest.addon_settlements.select_related('segment').order_by('pk')]


def verify(order, snapshot, *, cutoff):
    """Use current reads only; the caller controls order/receipt locking."""
    from .models import OrderSegmentAddonSettlement, OrderSegmentSettlementManifest
    from .segment_settlement import _valid_tx
    from wallet.models import Transaction
    if not snapshot:
        return 0
    plan = snapshot.get('plan') if isinstance(snapshot, dict) else None
    recorded = snapshot.get('addon_people', []) if isinstance(snapshot, dict) else None
    if not isinstance(plan, dict) or not isinstance(recorded, list):
        raise ValidationError('已付加购原结算快照异常')
    manifest = OrderSegmentSettlementManifest.objects.filter(pk=snapshot.get('manifest_id'), order=order).first()
    if not manifest:
        raise ValidationError('已付加购结算清单缺失')
    people = {row['account_id']: row for row in snapshot.get('people', [])}
    parts = plan.get('points_funding', {}).get('addons', [])
    if not isinstance(parts, list) or len(people) != len(snapshot.get('people', [])):
        raise ValidationError('已付加购履约计划异常')
    expected = {(part['payment_id'], part['segment_id']): part for part in parts
                if part.get('provider_account_id') in people}
    if len(expected) != sum(part.get('provider_account_id') in people for part in parts):
        raise ValidationError('已付加购履约段重复')
    rows = list(OrderSegmentAddonSettlement.objects.filter(manifest=manifest,
        segment__provider_account_id__in=people).select_related('segment', 'payment__intent', 'report',
            'income_transaction__wallet').order_by('pk'))
    if len(rows) != len(recorded) or len(rows) != len(expected):
        raise ValidationError('已付加购收益流水不完整')
    for row, saved in zip(rows, recorded):
        person = people.get(row.segment.provider_account_id)
        part = expected.get((row.payment_id, row.segment_id))
        actual = {'id': row.pk, 'payment_id': row.payment_id, 'segment_id': row.segment_id,
                  'account_id': row.segment.provider_account_id, 'amount': row.amount,
                  'report_id': row.report_id, 'income_transaction_id': row.income_transaction_id,
                  'settled_at': row.settled_at.isoformat() if row.settled_at else None}
        if (actual != saved or not person or not part or row.payment.intent.order_id != order.pk
                or row.segment_id not in person['segment_ids'] or row.report.segment_id != row.segment_id
                or part.get('provider_income_units') != row.amount or not row.settled_at
                or row.settled_at.isoformat() != person['settled_at'] or row.settled_at > cutoff
                or row.payment.settlements.filter(segment_id=row.segment_id).exists()):
            raise ValidationError('已付加购份额或结算时间异常')
        if row.amount:
            if (not _valid_tx(row.income_transaction, order=order, account_id=row.segment.provider_account_id,
                              amount=row.amount, kind=Transaction.TxType.INCOME)
                    or not row.income_transaction.created_at or row.income_transaction.created_at > row.settled_at):
                raise ValidationError('已付加购真实收益流水异常')
        elif row.income_transaction_id:
            raise ValidationError('零收益加购存在付款流水')
    return sum(row.amount for row in rows)


def unchanged(snapshot, addon_parts):
    """Require all previously credited segments to remain exactly unchanged."""
    if not snapshot:
        return
    paid = {row['account_id'] for row in snapshot['people']}
    old = [part for part in snapshot['plan'].get('points_funding', {}).get('addons', [])
           if part.get('provider_account_id') in paid]
    new = [part for part in addon_parts if part.get('provider_account_id') in paid]
    if sorted(old, key=lambda item: (item['payment_id'], item['segment_id'])) != sorted(
            new, key=lambda item: (item['payment_id'], item['segment_id'])):
        raise ValidationError('追加退款改变了已结算陪玩的加购收益')


def parts_before(order, addon_history, boundary):
    from .addon_handover import current_responsibilities
    from .models import OrderAddonPayment
    result = {}
    seen_payments = set()
    for expected in addon_history:
        if (not isinstance(expected, dict) or type(expected.get('payment_id')) is not int
                or expected['payment_id'] <= 0 or expected['payment_id'] in seen_payments):
            raise ValidationError('追加退款前加购付款重复或格式异常')
        seen_payments.add(expected.get('payment_id'))
        payment = OrderAddonPayment.objects.filter(pk=expected.get('payment_id'), intent__order=order).first()
        if not payment:
            raise ValidationError('追加退款前加购付款缺失')
        handover, parts = current_responsibilities(payment, before=boundary)
        if ((handover.pk if handover else None) != expected.get('handover_id')
                or sum(row['basis'] for row in parts) != expected.get('paid_units')
                or sum(row['income'] for row in parts) != expected.get('provider_units')):
            raise ValidationError('追加退款前加购履约份额异常')
        for part in parts:
            key = (payment.pk, part['segment_id'], part['provider_id'])
            if key not in result:
                result[key] = {'payment_id': payment.pk, 'segment_id': part['segment_id'],
                               'provider_account_id': part['provider_id'], 'provider_income_units': 0}
            result[key]['provider_income_units'] += part['income']
    return list(result.values())


def verify_first_refund_receipt(order, posting, snapshot, *, before=None, receipt_id=None):
    """Verify credits predating the first main refund without taking order locks."""
    from datetime import timedelta
    from types import SimpleNamespace
    from django.utils.dateparse import parse_datetime
    from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest, OrderSegmentSettlementRevision
    from .segment_settlement import _hash, _report_versions, _valid_tx
    from .settlement import SettlementError
    from wallet.models import Transaction
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('plan'), dict)
            or snapshot.get('plan_hash') != _hash(snapshot['plan']) or not isinstance(snapshot.get('people'), list)
            or not snapshot['people']):
        raise ValidationError('首次退款前已结算凭证异常')
    manifest = OrderSegmentSettlementManifest.objects.filter(pk=snapshot.get('manifest_id'), order=order).first()
    if not manifest or manifest.plan_hash != _hash(manifest.plan):
        raise ValidationError('首次退款前结算清单异常')
    if manifest.plan_hash != snapshot['plan_hash'] or before is not None:
        query = OrderSegmentSettlementRevision.objects.filter(manifest=manifest)
        if before is not None:
            if query.filter(created_at=before).exists():
                raise ValidationError('结算版本与资金事件时间无法区分')
            query = query.filter(created_at__lt=before)
        versions = list(query.select_related('posting__proposal__case').order_by('pk'))
        paid_accounts = {item['account_id'] for item in snapshot['people']}
        current_hash = snapshot['plan_hash']
        for index, version in enumerate(versions):
            if version.before_plan_hash != current_hash and (manifest.addon_revisions.exists()
                                                              or manifest.voluntary_payment_revisions.exists()):
                if not receipt_id:
                    raise ValidationError('交错结算历史缺少到账见证')
                from .points_voluntary_paid_evidence import verify_revisions_current_read
                from django.core.exceptions import ValidationError as DjangoValidationError
                try:
                    _, current_hash = verify_revisions_current_read(manifest,
                        start_plan=versions[index - 1].after_plan if index else snapshot['plan'],
                        start_hash=current_hash, receipt_id=receipt_id,
                        after=versions[index - 1].created_at if index else None,
                        before=version.created_at)
                except DjangoValidationError as exc:
                    raise ValidationError('交错加购结算历史异常') from exc
            if (version.before_plan_hash != current_hash or version.after_plan_hash != _hash(version.after_plan)
                    or version.posting.proposal.case.order_id != order.pk
                    or (index == 0 and version.posting_id != posting.pk)
                    or paid_accounts.intersection(version.posting.proposal.affected_accounts)):
                raise ValidationError('首次退款后结算版本异常')
            unchanged(snapshot, version.after_plan.get('points_funding', {}).get('addons', []))
            after_people = {item['account_id']: item for item in version.after_plan.get('people', [])}
            if any(after_people.get(item['account_id']) != {'account_id': item['account_id'],
                    'segment_ids': item['segment_ids'], 'income': item['amount']} for item in snapshot['people']):
                raise ValidationError('退款后的结算版本改变已付收益')
            current_hash = version.after_plan_hash
        from .models import PointsTransferZeroSettlementRevision
        zero_query = PointsTransferZeroSettlementRevision.objects.filter(manifest=manifest)
        if before is not None:
            if zero_query.filter(created_at=before).exists():
                raise ValidationError('零退款结算版本与资金事件时间无法区分')
            zero_query = zero_query.filter(created_at__lt=before)
        zero_revision = zero_query.select_related(
            'termination__proposal__case', 'termination__prior_posting', 'termination__assignment',
            'termination__segment', 'termination__operator', 'manifest').first()
        if zero_revision:
            from .points_transfer_zero_settlement import verify_revision
            current_hash = verify_revision(zero_revision, before_hash=current_hash)
            unchanged(snapshot, zero_revision.after_plan.get('points_funding', {}).get('addons', []))
        if not zero_revision and versions and (manifest.addon_revisions.exists()
                                                or manifest.voluntary_payment_revisions.exists()):
            if not receipt_id:
                raise ValidationError('已付加购结算历史缺少到账见证')
            from .points_voluntary_paid_evidence import verify_revisions_current_read
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                _, current_hash = verify_revisions_current_read(manifest,
                    start_plan=versions[-1].after_plan, start_hash=current_hash,
                    receipt_id=receipt_id, before=before, after=versions[-1].created_at)
            except DjangoValidationError as exc:
                raise ValidationError('已付加购结算历史异常') from exc
        if before is None:
            if zero_revision and manifest.addon_revisions.exists():
                if not receipt_id:
                    raise ValidationError('加购结算历史缺少到账见证')
                from .points_transfer_zero_addon_settlement import verify_revisions_current_read
                from django.core.exceptions import ValidationError as DjangoValidationError
                try:
                    current_hash = verify_revisions_current_read(manifest, zero_revision, receipt_id=receipt_id)
                except DjangoValidationError as exc:
                    raise ValidationError('加购结算历史异常') from exc
            if (not versions or current_hash != manifest.plan_hash
                    or (not manifest.addon_revisions.exists() and not manifest.voluntary_payment_revisions.exists()
                        and (zero_revision.after_plan if zero_revision else versions[-1].after_plan) != manifest.plan)):
                raise ValidationError('首次退款后结算版本异常')
    plan_people = {item['account_id']: item for item in snapshot['plan'].get('people', [])}
    saved_ids = [item.get('id') for item in snapshot['people']]
    rows = list(OrderSegmentPersonSettlement.objects.filter(manifest=manifest, settled_at__lte=posting.created_at)
        .select_related('income_transaction__wallet', 'deposit_debit__wallet', 'deposit_credit__wallet').order_by('pk'))
    if [row.pk for row in rows] != saved_ids or len(set(saved_ids)) != len(saved_ids):
        raise ValidationError('首次退款前已付人员不完整')
    for row, saved in zip(rows, snapshot['people']):
        actual = {'id': row.pk, 'account_id': row.account_id, 'amount': row.amount,
            'segment_ids': row.segment_ids, 'report_versions': row.report_versions,
            'income_transaction_id': row.income_transaction_id, 'deposit_debit_id': row.deposit_debit_id,
            'deposit_credit_id': row.deposit_credit_id,
            'settled_at': row.settled_at.isoformat() if row.settled_at else None}
        person = plan_people.get(row.account_id)
        if (actual != saved or not person or row.account_id in posting.proposal.affected_accounts
                or row.amount != person.get('income') or row.segment_ids != person.get('segment_ids')
                or not row.settled_at or not order.completed_at or order.service_aftersale_hours is None
                or row.settled_at < order.completed_at + timedelta(hours=order.service_aftersale_hours)):
            raise ValidationError('首次退款前已付人员收益异常')
        try:
            if _report_versions(SimpleNamespace(segment_ids=tuple(row.segment_ids))) != row.report_versions:
                raise ValidationError('首次退款前报单版本异常')
        except SettlementError as exc:
            raise ValidationError('首次退款前报单凭证异常') from exc
        for report in row.report_versions:
            submitted = parse_datetime(report.get('submitted_at') or '')
            reviewed = parse_datetime(report.get('reviewed_at') or '')
            if (not submitted or not reviewed or reviewed < submitted or reviewed > row.settled_at
                    or order.service_freeze_hours is None
                    or submitted + timedelta(hours=order.service_freeze_hours) > row.settled_at):
                raise ValidationError('首次退款前报单时钟异常')
        if row.amount:
            if (not _valid_tx(row.income_transaction, order=order, account_id=row.account_id,
                              amount=row.amount, kind=Transaction.TxType.INCOME)
                    or not row.income_transaction.created_at or row.income_transaction.created_at > row.settled_at):
                raise ValidationError('首次退款前已付收益流水异常')
        elif row.income_transaction_id:
            raise ValidationError('零收益已付人员存在流水')
        debit, credit = row.deposit_debit, row.deposit_credit
        if bool(debit) != bool(credit):
            raise ValidationError('首次退款前押金流水缺少配对')
        from django.conf import settings
        if debit and (not _valid_tx(debit, order=order, account_id=row.account_id,
                                   amount=debit.amount, kind=Transaction.TxType.DEPOSIT)
                or not -row.amount <= debit.amount < 0 or credit.order_id != order.pk
                or credit.tx_type != Transaction.TxType.DEPOSIT_INCOME or credit.status != 'SUCCESS'
                or not credit.wallet.user_id or credit.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or credit.amount != -debit.amount or credit.balance_after != credit.balance_before + credit.amount
                or not debit.created_at or debit.created_at > row.settled_at
                or not credit.created_at or credit.created_at > row.settled_at
                or credit.reversal_of_id or credit.reversals.exists()):
            raise ValidationError('首次退款前押金流水异常')
    addon_units = verify(order, snapshot, cutoff=posting.created_at)
    return {'people': snapshot['people'], 'addon_people': snapshot.get('addon_people', []),
            'paid_units': sum(row.amount for row in rows) + addon_units}
