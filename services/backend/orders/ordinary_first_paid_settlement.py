"""Evidence for paid people when the first transfer refund is agreed."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def capture_locked(order, plan, scope):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('已结算人员快照必须在订单事务内生成')
    from .models import OrderSegmentSettlementManifest, OrderSegmentPersonSettlement
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if not manifest:
        return None
    if manifest.finalized_at or not isinstance(manifest.plan, dict) or manifest.plan_hash != _hash(manifest.plan):
        raise ValidationError('原逐段结算清单待核对')
    people = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(manifest=manifest).order_by('pk'))
    if not people:
        return None
    if any(row.account_id in scope for row in people):
        raise ValidationError('已结算的受影响陪玩不能重新协商')
    try:
        _validate_records(order, manifest, plan)
    except SettlementError as exc:
        raise ValidationError('已结算人员流水或报单凭证待核对') from exc
    addons = list(manifest.addon_settlements.select_for_update().select_related('segment').order_by('pk'))
    paid_segments = {segment_id for row in people for segment_id in row.segment_ids}
    if any(row.segment_id not in paid_segments for row in addons):
        raise ValidationError('已付加购不属于已结算履约段')
    return {'manifest_id': manifest.pk, 'plan': manifest.plan, 'plan_hash': manifest.plan_hash,
            'people': [{'id': row.pk, 'account_id': row.account_id, 'amount': row.amount,
                        'segment_ids': row.segment_ids, 'report_versions': row.report_versions,
                        'income_transaction_id': row.income_transaction_id,
                        'deposit_debit_id': row.deposit_debit_id, 'deposit_credit_id': row.deposit_credit_id,
                        'settled_at': row.settled_at.isoformat()} for row in people],
            'addons': [{'id': row.pk, 'payment_id': row.payment_id, 'segment_id': row.segment_id,
                        'account_id': row.segment.provider_account_id, 'report_id': row.report_id,
                        'amount': row.amount, 'income_transaction_id': row.income_transaction_id,
                        'settled_at': row.settled_at.isoformat()} for row in addons]}


def verify_receipt_snapshot(order, execution):
    """Re-read frozen paid evidence without acquiring order or money locks."""
    return verify_saved_snapshot(order, execution.proposal.ordinary_paid_settlement_snapshot,
                                 execution.proposal.affected_accounts)


def verify_saved_snapshot(order, snapshot, affected_accounts):
    if snapshot is None:
        return
    if not isinstance(snapshot, dict):
        raise ValidationError('首轮协商已付快照异常')
    from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest
    from .segment_settlement import _hash, _report_versions, _valid_tx
    from .settlement import SettlementError
    from types import SimpleNamespace
    manifest = OrderSegmentSettlementManifest.objects.filter(pk=snapshot.get('manifest_id'), order=order).first()
    people = snapshot.get('people')
    if (not manifest or not isinstance(snapshot.get('plan'), dict)
            or snapshot.get('plan_hash') != _hash(snapshot['plan'])
            or not isinstance(people, list) or not people):
        raise ValidationError('首轮协商已付快照异常')
    ids = {item.get('id') for item in people if isinstance(item, dict)}
    rows = list(OrderSegmentPersonSettlement.objects.filter(manifest=manifest, pk__in=ids).select_related(
        'income_transaction__wallet', 'deposit_debit__wallet', 'deposit_credit__wallet__user').order_by('pk'))
    if len(rows) != len(people):
        raise ValidationError('首轮协商已付记录缺失')
    for row, saved in zip(rows, people):
        current = {'id': row.pk, 'account_id': row.account_id, 'amount': row.amount,
                   'segment_ids': row.segment_ids, 'report_versions': row.report_versions,
                   'income_transaction_id': row.income_transaction_id,
                   'deposit_debit_id': row.deposit_debit_id, 'deposit_credit_id': row.deposit_credit_id,
                   'settled_at': row.settled_at.isoformat()}
        if current != saved or row.account_id in affected_accounts:
            raise ValidationError('首轮协商已付记录已变化')
        try:
            if row.report_versions != _report_versions(SimpleNamespace(segment_ids=row.segment_ids)):
                raise ValidationError('首轮协商已付报单已变化')
        except SettlementError as exc:
            raise ValidationError('首轮协商已付报单待核对') from exc
        if row.amount and not _valid_tx(row.income_transaction, order=order, account_id=row.account_id,
                                        amount=row.amount, kind='INCOME'):
            raise ValidationError('首轮协商已付收益流水异常')
        if bool(row.deposit_debit_id) != bool(row.deposit_credit_id):
            raise ValidationError('首轮协商已付押金流水缺失')
        if row.deposit_debit_id:
            from django.conf import settings
            debit, credit = row.deposit_debit, row.deposit_credit
            if (debit.amount >= 0 or -debit.amount > row.amount
                    or not _valid_tx(debit, order=order, account_id=row.account_id,
                                     amount=debit.amount, kind='DEPOSIT')
                    or not credit.wallet.user or credit.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                    or credit.order_id != order.pk or credit.tx_type != 'DEPOSIT_INCOME'
                    or credit.amount != -debit.amount or credit.status != 'SUCCESS' or credit.reversal_of_id
                    or credit.reversals.exists() or credit.balance_after != credit.balance_before + credit.amount):
                raise ValidationError('首轮协商已付押金流水异常')
    addons = snapshot.get('addons')
    if not isinstance(addons, list):
        raise ValidationError('首轮协商已付加购快照异常')
    from .models import OrderSegmentAddonSettlement
    addon_ids = {item.get('id') for item in addons if isinstance(item, dict)}
    actual = list(OrderSegmentAddonSettlement.objects.filter(manifest=manifest, pk__in=addon_ids)
                  .select_related('segment', 'payment__intent', 'income_transaction__wallet').order_by('pk'))
    if len(actual) != len(addons):
        raise ValidationError('首轮协商已付加购记录缺失')
    for row, saved in zip(actual, addons):
        value = {'id': row.pk, 'payment_id': row.payment_id, 'segment_id': row.segment_id,
                 'account_id': row.segment.provider_account_id, 'report_id': row.report_id,
                 'amount': row.amount, 'income_transaction_id': row.income_transaction_id,
                 'settled_at': row.settled_at.isoformat()}
        if (value != saved or row.payment.intent.order_id != order.pk
                or row.segment.provider_account_id in affected_accounts
                or (row.amount and not _valid_tx(row.income_transaction, order=order,
                    account_id=row.segment.provider_account_id, amount=row.amount, kind='INCOME'))
                or (not row.amount and row.income_transaction_id)):
            raise ValidationError('首轮协商已付加购流水异常')


def transition_locked(order, manifest, plan, terms, execution):
    from .negotiation_transfer_evidence import verify_locked
    from .segment_settlement import _hash, _validate_records
    evidence = verify_locked(execution)
    snapshot = execution.proposal.ordinary_paid_settlement_snapshot
    if (not evidence or not isinstance(snapshot, dict)
            or evidence.snapshot.get('ordinary_paid_settlement_snapshot') != snapshot
            or snapshot.get('manifest_id') != manifest.pk
            or snapshot.get('plan') != manifest.plan or snapshot.get('plan_hash') != manifest.plan_hash):
        raise ValidationError('首轮协商前的已结算清单已变化')
    if capture_locked(order, plan, execution.proposal.affected_accounts) != snapshot:
        raise ValidationError('首轮协商前的已结算流水已变化')
    manifest.plan, manifest.plan_hash = terms, _hash(terms)
    manifest.save(update_fields=['plan', 'plan_hash'])
    return _validate_records(order, manifest, plan)
