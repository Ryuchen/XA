"""Paid-person evidence captured in the actual add-on refund transaction."""
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.context import current_store
from stores.db import transaction


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _verify_mixed_people(before, after, paid):
    old = {row['account_id']: row for row in before.get('people', [])}
    new = {row['account_id']: row for row in after.get('people', [])}
    if (len(old) != len(before.get('people', [])) or len(new) != len(after.get('people', []))
            or set(old) != set(new) or any(old[key].get('segment_ids') != value.get('segment_ids')
                or value.get('income', -1) < 0 or value['income'] > old[key]['income'] for key, value in new.items())
            or any(old.get(row['account_id']) != new.get(row['account_id']) for row in paid['people'])):
        raise ValidationError('混合付款退款改变了已付收益或异常增加收益')


def _verify_removed_payments(order_id, removed, refund_ids, *, before=None):
    from .models import OrderAddonPayment
    payments = list(OrderAddonPayment.objects.filter(intent__order_id=order_id, pk__in=removed).select_related('intent'))
    if len(payments) != len(removed):
        raise ValidationError('清单移除的加购付款缺失')
    for payment in payments:
        refunds = payment.refund_records.all()
        if before is not None:
            refunds = refunds.filter(created_at__lt=before)
        rows = list(refunds.values_list('pk', 'amount'))
        if sum(amount for _, amount in rows) != payment.intent.amount or not set(refund_ids).intersection(
                pk for pk, _ in rows):
            raise ValidationError('清单移除的加购缺少对应全额退款')


def capture_before_locked(order, payment=None):
    """Capture only actual credited people, before changing refund assets."""
    from .models import OrderSegmentPersonSettlement
    from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
    from .points_voluntary_handover_event import entry_matches
    from .points_transfer_followup_addon_version import refund_preflight_active
    if (not transaction.get_connection().in_atomic_block or not (entry_matches(order)
            or (payment is not None and refund_preflight_active(order, payment)))):
        raise ValidationError('已付快照须在本单退款事务中核对')
    if not OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists():
        return None
    snapshot = _prior_settlement(order)
    if not snapshot:
        raise ValidationError('已结算人员的真实清单缺失')
    from .models import OrderSegmentSettlementManifest
    from .segment_addon_settlement_revision import verify_revisions
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=snapshot['manifest_id'])
    verify_revisions(manifest)
    _verify_prior_settlement(order, snapshot)
    return snapshot


def capture_before_payment_locked(order):
    """Capture real credited people while the payment scope owns the order lock."""
    from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest
    from .points_business_access import permits
    from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
    from .segment_addon_settlement_revision import verify_revisions
    if not transaction.get_connection().in_atomic_block or not permits(order, phase='payment', kind='transfer'):
        raise ValidationError('加购付款的已付快照须在本单事务核对')
    if not OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists():
        return None
    snapshot = _prior_settlement(order)
    if not snapshot:
        raise ValidationError('加购付款前缺少真实已付清单')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=snapshot['manifest_id'])
    verify_revisions(manifest)
    _verify_prior_settlement(order, snapshot)
    return snapshot


def record_payment_locked(payment, before):
    """Bind the paid snapshot to the committed payment and actual release run."""
    if before is None:
        return None
    from .models import OrderAddonFundingReleaseRun, OrderSegmentSettlementManifest, PointsVoluntaryPaidPaymentEvidence
    from .addon_funding_release import verify_run
    from .points_business_access import permits
    from .points_transfer_refund_evidence import _verify_prior_settlement
    order = payment.intent.order
    if not transaction.get_connection().in_atomic_block or not permits(order, phase='payment', kind='transfer'):
        raise ValidationError('加购付款的已付凭证与事务不一致')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    run = OrderAddonFundingReleaseRun.objects.select_for_update().filter(payment=payment).first()
    if (not manifest or not run or manifest.pk != before.get('manifest_id')
            or not payment.buyer_transaction.created_at or run.created_at < payment.buyer_transaction.created_at):
        raise ValidationError('加购付款缺少真实清单或资金事件')
    _verify_prior_settlement(order, before)
    verify_run(run, _skip_usage=True)
    snapshot = {'version': 1, 'order_id': order.pk, 'payment_id': payment.pk,
                'payment_at': payment.buyer_transaction.created_at.isoformat(), 'funding_run_id': run.pk,
                'funding_hash': run.refunded_main_hash, 'payment_hash': run.points_payment_hash,
                'followup_hash': run.points_followup_hash, 'paid': before}
    signature = _digest(snapshot)
    existing = PointsVoluntaryPaidPaymentEvidence.objects.select_for_update().filter(payment=payment).first()
    if existing:
        if (existing.manifest_id != manifest.pk or existing.funding_run_id != run.pk
                or existing.snapshot != snapshot or existing.evidence_hash != signature):
            raise ValidationError('加购付款已付凭证与原操作不一致')
        return existing
    return PointsVoluntaryPaidPaymentEvidence.objects.create(payment=payment, manifest=manifest,
        funding_run=run, snapshot=snapshot, evidence_hash=signature)


def verify_payment_current_read(run):
    """Check payment-time paid rows without re-entering the funding replay."""
    from datetime import timedelta
    from types import SimpleNamespace
    from django.conf import settings
    from django.utils.dateparse import parse_datetime
    from wallet.models import Transaction
    from .addon_payment import verify_receipt
    from .models import OrderSegmentPersonSettlement, PointsNegotiationPosting, PointsVoluntaryPaidPaymentEvidence
    from .points_transfer_paid_addons import verify as verify_addons
    from .segment_settlement import _hash, _report_versions, _valid_tx
    from .settlement import SettlementError
    evidence = PointsVoluntaryPaidPaymentEvidence.objects.filter(funding_run=run).first()
    if not evidence:
        return None
    payment = run.payment
    saved = evidence.snapshot
    if (not payment or not isinstance(saved, dict) or evidence.payment_id != payment.pk
            or evidence.manifest.order_id != payment.intent.order_id or evidence.evidence_hash != _digest(saved)
            or saved.get('version') != 1 or saved.get('order_id') != payment.intent.order_id
            or saved.get('payment_id') != payment.pk or saved.get('funding_run_id') != run.pk
            or not payment.buyer_transaction.created_at
            or saved.get('payment_at') != payment.buyer_transaction.created_at.isoformat()
            or saved.get('funding_hash') != run.refunded_main_hash
            or saved.get('payment_hash') != run.points_payment_hash
            or saved.get('followup_hash') != run.points_followup_hash
            or run.created_at < payment.buyer_transaction.created_at):
        raise ValidationError('加购付款已付资金事件异常')
    verify_receipt(payment)
    return verify_historical_paid(payment.intent.order, saved.get('paid'),
                                  payment.buyer_transaction.created_at, evidence.manifest_id)


def _paid_aftersale_start(order, account_id, segment_ids, cutoff):
    """Use each actual terminated segment's clock; the whole order must be complete."""
    from .models import PointsNegotiationPosting
    if (not order.completed_at or not isinstance(segment_ids, list) or not segment_ids
            or len(set(segment_ids)) != len(segment_ids)
            or any(type(item) is not int or item <= 0 for item in segment_ids)):
        raise ValidationError('已付人员的履约范围或整单完成时间异常')
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        created_at__lte=cutoff, termination_in_service=True).select_related('proposal').order_by('created_at', 'pk'))
    endings = []
    for segment_id in segment_ids:
        matches = []
        for posting in postings:
            participants = posting.participants
            rows = (participants.get('facts', {}).get('segments', []) if isinstance(participants, dict)
                    else participants if isinstance(participants, list) else [])
            ids = {(item.get('id') if isinstance(participants, dict) else item.get('segment_id'))
                   for item in rows if isinstance(item, dict) and item.get('account_id') == account_id
                   and item.get('started_at')}
            selected = set(posting.proposal.affected_segment_ids or [])
            if (account_id in posting.proposal.affected_accounts and segment_id in ids
                    and (not selected or segment_id in selected)):
                if posting.created_at == cutoff:
                    raise ValidationError('已付时点与协商终止顺序无法区分')
                if (not posting.accepted_at or not posting.termination_processed_at
                        or posting.termination_processed_at > cutoff):
                    raise ValidationError('已付人员的协商终止凭证或时序异常')
                matches.append(posting.accepted_at)
        if len(matches) > 1:
            raise ValidationError('同一履约段存在重复协商终止')
        endings.append(matches[0] if matches else order.completed_at)
    return max(endings)


def verify_historical_paid(order, paid, cutoff, manifest_id):
    """Verify credited people and immutable ledgers at a real event time."""
    from datetime import timedelta
    from types import SimpleNamespace
    from django.conf import settings
    from django.utils.dateparse import parse_datetime
    from wallet.models import Transaction
    from .models import OrderSegmentPersonSettlement, PointsNegotiationPosting
    from .points_transfer_paid_addons import verify as verify_addons
    from .segment_settlement import _hash, _report_versions, _valid_tx
    from .settlement import SettlementError
    if (not isinstance(paid, dict) or paid.get('manifest_id') != manifest_id
            or not isinstance(paid.get('plan'), dict) or paid.get('plan_hash') != _hash(paid['plan'])
            or not isinstance(paid.get('people'), list) or not paid['people']):
        raise ValidationError('加购付款前已付清单异常')
    rows = list(OrderSegmentPersonSettlement.objects.filter(manifest_id=manifest_id,
        settled_at__lte=cutoff).select_related('income_transaction__wallet', 'deposit_debit__wallet',
        'deposit_credit__wallet__user').order_by('pk'))
    if [row.pk for row in rows] != [item.get('id') for item in paid['people']]:
        raise ValidationError('加购付款前已付人员不完整')
    people = {item['account_id']: item for item in paid['plan'].get('people', [])}
    for row, expected in zip(rows, paid['people']):
        actual = {'id': row.pk, 'account_id': row.account_id, 'amount': row.amount,
            'segment_ids': row.segment_ids, 'report_versions': row.report_versions,
            'income_transaction_id': row.income_transaction_id, 'deposit_debit_id': row.deposit_debit_id,
            'deposit_credit_id': row.deposit_credit_id,
            'settled_at': row.settled_at.isoformat() if row.settled_at else None}
        person = people.get(row.account_id)
        start = _paid_aftersale_start(order, row.account_id, row.segment_ids,
                                       min(cutoff, row.settled_at) if row.settled_at else cutoff)
        if (actual != expected or not person or person.get('income') != row.amount
                or person.get('segment_ids') != row.segment_ids or not row.settled_at or not start
                or order.service_aftersale_hours is None or row.settled_at < order.completed_at
                or row.settled_at < start + timedelta(hours=order.service_aftersale_hours)):
            raise ValidationError('加购付款前已付人员收益异常')
        try:
            if _report_versions(SimpleNamespace(segment_ids=tuple(row.segment_ids))) != row.report_versions:
                raise ValidationError('加购付款前报单版本异常')
        except SettlementError as exc:
            raise ValidationError('加购付款前报单凭证异常') from exc
        for report in row.report_versions:
            submitted = parse_datetime(report.get('submitted_at') or '')
            reviewed = parse_datetime(report.get('reviewed_at') or '')
            if (not submitted or not reviewed or reviewed < submitted or reviewed > row.settled_at
                    or order.service_freeze_hours is None
                    or submitted + timedelta(hours=order.service_freeze_hours) > row.settled_at):
                raise ValidationError('加购付款前报单时钟异常')
        if row.amount:
            if (not _valid_tx(row.income_transaction, order=order, account_id=row.account_id,
                    amount=row.amount, kind=Transaction.TxType.INCOME)
                    or not row.income_transaction.created_at or row.income_transaction.created_at > row.settled_at):
                raise ValidationError('加购付款前收益流水异常')
        elif row.income_transaction_id:
            raise ValidationError('零收益人员存在收益流水')
        debit, credit = row.deposit_debit, row.deposit_credit
        if bool(debit) != bool(credit):
            raise ValidationError('加购付款前押金流水缺少配对')
        if debit and (not _valid_tx(debit, order=order, account_id=row.account_id,
                amount=debit.amount, kind=Transaction.TxType.DEPOSIT)
                or not -row.amount <= debit.amount < 0 or credit.order_id != order.pk
                or credit.tx_type != Transaction.TxType.DEPOSIT_INCOME or credit.status != 'SUCCESS'
                or not credit.wallet.user_id or credit.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or credit.amount != -debit.amount or credit.balance_after != credit.balance_before + credit.amount
                or not debit.created_at or debit.created_at > row.settled_at
                or not credit.created_at or credit.created_at > row.settled_at
                or credit.reversal_of_id or credit.reversals.exists()):
            raise ValidationError('加购付款前押金流水异常')
    verify_addons(order, paid, cutoff=cutoff)
    return paid


def record_locked(refund, before):
    """Bind the captured paid ledger to the real refund and its release run."""
    if before is None:
        return None
    from .models import OrderAddonFundingReleaseRun, OrderSegmentSettlementManifest
    from .addon_funding_release import verify_run
    from .points_transfer_refund_evidence import _verify_prior_settlement
    from .points_voluntary_handover_event import entry_matches
    from .points_transfer_followup_addon_version import refund_preflight_active
    order = refund.order
    if (not transaction.get_connection().in_atomic_block or current_store() is None
            or not (entry_matches(order, refund.payment_id) or refund_preflight_active(order, refund.payment))):
        raise ValidationError('已付退款凭证与当前事务不一致')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    run = OrderAddonFundingReleaseRun.objects.select_for_update().filter(refund=refund).first()
    if (not manifest or not run or before.get('manifest_id') != manifest.pk
            or not refund.created_at or run.created_at < refund.created_at):
        raise ValidationError('已付退款缺少原清单或真实资金事件')
    _verify_prior_settlement(order, before)
    verify_run(run, _skip_usage=True)
    return _save_refund_evidence(refund, run, manifest, before)


def recover_standard_refunds_locked(order, manifest, refund_ids):
    """Reconcile committed standard add-on refunds against the still-open original manifest."""
    from .addon_funding_release import verify_run
    from .models import OrderAddonFundingReleaseRun, OrderSegmentPersonSettlement
    from .points_transfer_refund_access import permits
    from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement

    connection = transaction.get_connection()
    if (not connection.in_atomic_block or not permits(order, phase='settlement')
            or order._state.db != connection.alias or manifest._state.db != connection.alias
            or manifest.order_id != order.pk or manifest.finalized_at or order.settled_at
            or order.refund_records.exists()):
        raise ValidationError('已付加购退款须在未结清的转单事务中核对')
    prior = _prior_settlement(order)
    if not prior or prior['manifest_id'] != manifest.pk:
        raise ValidationError('已付加购退款缺少原结算清单')
    _verify_prior_settlement(order, prior)
    accounts = {item['account_id'] for item in prior['people']}
    runs = list(OrderAddonFundingReleaseRun.objects.filter(
        refund__order=order, refund_id__in=refund_ids).select_related('refund__proposal__case')
        .order_by('refund__created_at', 'refund_id'))
    if len(runs) != len(refund_ids):
        raise ValidationError('已付加购退款缺少真实资金版本')
    for run in runs:
        refund = run.refund
        proposal = refund.proposal
        affected = set(proposal.participants.values_list('account_id', flat=True))
        if (not refund.created_at or run.created_at < refund.created_at
                or not _standard_funding_run(run)
                or proposal.payment_id != refund.payment_id or proposal.case.order_id != order.pk
                or not affected or accounts.intersection(affected)
                or OrderSegmentPersonSettlement.objects.filter(manifest=manifest, settled_at=refund.created_at).exists()):
            raise ValidationError('已付人员与原加购退款范围不一致')
        verify_run(run)
        verify_historical_paid(order, prior, refund.created_at, manifest.pk)
        _save_refund_evidence(refund, run, manifest, prior)
    return prior


def _standard_funding_run(run):
    return bool(run.refund_id is not None and run.points_addon_snapshot and run.points_addon_hash
                and run.refunded_main_snapshot is None and run.points_followup_snapshot is None)


def verify_standard_refund_current_read(run, *, in_revision=False):
    """Require the paid witness once a standard refund is included in the manifest history."""
    from .models import (OrderSegmentAddonSettlementRevision, OrderSegmentPersonSettlement,
                         OrderSegmentSettlementManifest)

    if not _standard_funding_run(run):
        return
    paid = verify_current_read(run)
    refund = run.refund
    if paid is None and not OrderSegmentPersonSettlement.objects.filter(
            manifest__order_id=refund.order_id, settled_at__lte=refund.created_at).exists():
        return
    if not in_revision:
        versions = OrderSegmentAddonSettlementRevision.objects.filter(manifest__order_id=refund.order_id)
        if not any(refund.pk in ids for ids in versions.values_list('refund_ids', flat=True) if isinstance(ids, list)):
            ids = OrderSegmentSettlementManifest.objects.filter(order_id=refund.order_id).values_list(
                'plan__points_funding__addon_refund_ids', flat=True).first()
            if isinstance(ids, list) and refund.pk in ids:
                raise ValidationError('已付加购退款缺少清单版本')
            return
    if paid is None:
        raise ValidationError('已付加购退款缺少人员结算凭证')


def _save_refund_evidence(refund, run, manifest, before):
    from .models import PointsVoluntaryPaidRefundEvidence

    snapshot = {'version': 1, 'order_id': refund.order_id, 'payment_id': refund.payment_id,
                'refund_id': refund.pk, 'refund_at': refund.created_at.isoformat(),
                'funding_run_id': run.pk, 'funding_hash': run.refunded_main_hash,
                'addon_hash': run.points_addon_hash, 'followup_hash': run.points_followup_hash,
                'paid': before}
    signature = _digest(snapshot)
    existing = PointsVoluntaryPaidRefundEvidence.objects.select_for_update().filter(refund=refund).first()
    if existing:
        if (existing.manifest_id != manifest.pk or existing.funding_run_id != run.pk
                or existing.snapshot != snapshot or existing.evidence_hash != signature):
            raise ValidationError('已付退款凭证与原操作不一致')
        return existing
    return PointsVoluntaryPaidRefundEvidence.objects.create(refund=refund, manifest=manifest,
        funding_run=run, snapshot=snapshot, evidence_hash=signature)


def verify_current_read(run):
    """Verify the refund-time paid ledger without acquiring order locks.

    The receipt replay already holds its receipt lock and verifies the funding
    event separately; calling its funding verifier here would recurse.
    """
    from datetime import timedelta
    from types import SimpleNamespace
    from django.conf import settings
    from django.utils.dateparse import parse_datetime
    from wallet.models import Transaction
    from .models import OrderSegmentPersonSettlement, PointsVoluntaryPaidRefundEvidence
    from .points_transfer_paid_addons import verify as verify_addons
    from .segment_settlement import _hash, _report_versions, _valid_tx
    from .settlement import SettlementError

    evidence = PointsVoluntaryPaidRefundEvidence.objects.filter(funding_run=run).first()
    if not evidence:
        return None
    refund = run.refund
    snapshot = evidence.snapshot
    if (not refund or not isinstance(snapshot, dict) or evidence.refund_id != refund.pk
            or evidence.manifest.order_id != refund.order_id or evidence.evidence_hash != _digest(snapshot)
            or snapshot.get('version') != 1 or snapshot.get('order_id') != refund.order_id
            or snapshot.get('payment_id') != refund.payment_id or snapshot.get('refund_id') != refund.pk
            or snapshot.get('refund_at') != refund.created_at.isoformat()
            or snapshot.get('funding_run_id') != run.pk or snapshot.get('funding_hash') != run.refunded_main_hash
            or snapshot.get('addon_hash') != run.points_addon_hash
            or snapshot.get('followup_hash') != run.points_followup_hash or run.created_at < refund.created_at):
        raise ValidationError('已付退款事件凭证异常')
    paid = snapshot.get('paid')
    if (not isinstance(paid, dict) or paid.get('manifest_id') != evidence.manifest_id
            or not isinstance(paid.get('plan'), dict) or paid.get('plan_hash') != _hash(paid['plan'])
            or not isinstance(paid.get('people'), list) or not paid['people']):
        raise ValidationError('已付退款清单凭证异常')
    order = refund.order
    rows = list(OrderSegmentPersonSettlement.objects.filter(manifest_id=evidence.manifest_id,
        settled_at__lte=refund.created_at).select_related('income_transaction__wallet',
        'deposit_debit__wallet', 'deposit_credit__wallet__user').order_by('pk'))
    if [row.pk for row in rows] != [item.get('id') for item in paid['people']]:
        raise ValidationError('已付退款人员清单不完整')
    people = {item['account_id']: item for item in paid['plan'].get('people', [])}
    for row, saved in zip(rows, paid['people']):
        actual = {'id': row.pk, 'account_id': row.account_id, 'amount': row.amount,
            'segment_ids': row.segment_ids, 'report_versions': row.report_versions,
            'income_transaction_id': row.income_transaction_id, 'deposit_debit_id': row.deposit_debit_id,
            'deposit_credit_id': row.deposit_credit_id,
            'settled_at': row.settled_at.isoformat() if row.settled_at else None}
        person = people.get(row.account_id)
        start = _paid_aftersale_start(order, row.account_id, row.segment_ids,
                                       min(refund.created_at, row.settled_at) if row.settled_at else refund.created_at)
        if (actual != saved or not person or person.get('income') != row.amount
                or person.get('segment_ids') != row.segment_ids or not row.settled_at
                or not start or order.service_aftersale_hours is None or row.settled_at < order.completed_at
                or row.settled_at < start + timedelta(hours=order.service_aftersale_hours)):
            raise ValidationError('已付退款人员收益异常')
        try:
            if _report_versions(SimpleNamespace(segment_ids=tuple(row.segment_ids))) != row.report_versions:
                raise ValidationError('已付退款报单版本异常')
        except SettlementError as exc:
            raise ValidationError('已付退款报单凭证异常') from exc
        for report in row.report_versions:
            submitted = parse_datetime(report.get('submitted_at') or '')
            reviewed = parse_datetime(report.get('reviewed_at') or '')
            if (not submitted or not reviewed or reviewed < submitted or reviewed > row.settled_at
                    or order.service_freeze_hours is None
                    or submitted + timedelta(hours=order.service_freeze_hours) > row.settled_at):
                raise ValidationError('已付退款报单时间异常')
        if row.amount:
            if (not _valid_tx(row.income_transaction, order=order, account_id=row.account_id,
                    amount=row.amount, kind=Transaction.TxType.INCOME)
                    or not row.income_transaction.created_at or row.income_transaction.created_at > row.settled_at):
                raise ValidationError('已付退款收益流水异常')
        elif row.income_transaction_id:
            raise ValidationError('零收益人员存在收益流水')
        debit, credit = row.deposit_debit, row.deposit_credit
        if bool(debit) != bool(credit):
            raise ValidationError('已付退款押金流水缺少配对')
        if debit and (not _valid_tx(debit, order=order, account_id=row.account_id,
                amount=debit.amount, kind=Transaction.TxType.DEPOSIT)
                or not -row.amount <= debit.amount < 0 or credit.order_id != order.pk
                or credit.tx_type != Transaction.TxType.DEPOSIT_INCOME or credit.status != 'SUCCESS'
                or not credit.wallet.user_id or credit.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or credit.amount != -debit.amount or credit.balance_after != credit.balance_before + credit.amount
                or not debit.created_at or debit.created_at > row.settled_at
                or not credit.created_at or credit.created_at > row.settled_at
                or credit.reversal_of_id or credit.reversals.exists()):
            raise ValidationError('已付退款押金流水异常')
    verify_addons(order, paid, cutoff=refund.created_at)
    return paid


def transition_locked(order, manifest, plan, terms):
    """Append a funded add-on refund version without changing paid earnings."""
    from .models import OrderAddonFundingReleaseRun, OrderSegmentAddonSettlementRevision
    from .points_transfer_paid_addons import unchanged
    from .segment_addon_settlement_revision import verify_revisions
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError

    if (not transaction.get_connection().in_atomic_block or manifest.order_id != order.pk
            or manifest.finalized_at or order.settled_at or manifest.plan_hash != _hash(manifest.plan)):
        raise SettlementError('已付退款结算上下文异常')
    verify_revisions(manifest)
    before = manifest.plan
    old_ids = before.get('points_funding', {}).get('addon_refund_ids', [])
    new_ids = terms.get('points_funding', {}).get('addon_refund_ids', [])
    if (not isinstance(old_ids, list) or not isinstance(new_ids, list)
            or len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids)
            or not set(old_ids).issubset(new_ids)):
        raise SettlementError('已付退款结算事件序列异常')
    added = sorted(set(new_ids) - set(old_ids))
    runs = list(OrderAddonFundingReleaseRun.objects.filter(refund_id__in=added,
        refund__order=order).select_related('refund__order').order_by('refund__created_at', 'refund_id'))
    if not added or len(runs) != len(added):
        raise SettlementError('已付退款缺少真实资金事件')
    stable = ('deal_amount', 'segments', 'fixed_fee_shortfall', 'buyer_account_id',
              'inviter_account_id', 'inviter_id', 'inviter_rate', 'deposit_percent',
              'aftersale_hours', 'freeze_hours', 'completed_at')
    if any(before.get(key) != terms.get(key) for key in stable):
        raise SettlementError('加购退款改变了主单履约事实')
    old_people = {item['account_id']: item for item in before.get('people', [])}
    new_people = {item['account_id']: item for item in terms.get('people', [])}
    if (len(old_people) != len(before.get('people', [])) or len(new_people) != len(terms.get('people', []))
            or set(old_people) != set(new_people) or any(
                old_people[key].get('segment_ids') != value.get('segment_ids')
                or value.get('income', -1) < 0 or value['income'] > old_people[key]['income']
                for key, value in new_people.items())):
        raise SettlementError('加购退款改变了履约人员或异常增加收益')
    try:
        for run in runs:
            paid = verify_current_read(run)
            if not paid or paid['plan'] != before:
                raise ValidationError('退款没有对应当前清单的已付凭证')
            unchanged(paid, terms.get('points_funding', {}).get('addons', []))
            if any(old_people.get(item['account_id']) != new_people.get(item['account_id'])
                   for item in paid['people']):
                raise ValidationError('退款改变了已付主单收益')
            _validate_records(order, manifest, plan, snapshot_ids=[item['id'] for item in paid['people']],
                paid_addon_snapshot=paid if before.get('points_funding', {}).get('addons') else None,
                paid_addon_cutoff=run.refund.created_at)
    except ValidationError as exc:
        raise SettlementError('退款前已付收益凭证待核对') from exc
    with transaction.atomic():
        OrderSegmentAddonSettlementRevision.objects.create(manifest=manifest,
            version=manifest.addon_revisions.count() + 1, refund_ids=added,
            before_plan=before, before_plan_hash=manifest.plan_hash,
            after_plan=terms, after_plan_hash=_hash(terms))
        manifest.plan, manifest.plan_hash = terms, _hash(terms)
        manifest.save(update_fields=['plan', 'plan_hash'])
        return _validate_records(order, manifest, plan)


def pending_payment_evidence(manifest):
    """Find real payments not yet included in a saved settlement version."""
    from .models import PointsVoluntaryPaidPaymentEvidence
    used = set()
    for ids in manifest.voluntary_payment_revisions.values_list('payment_ids', flat=True):
        if not isinstance(ids, list):
            raise ValidationError('加购付款结算版本格式异常')
        used.update(ids)
    return [item for item in PointsVoluntaryPaidPaymentEvidence.objects.filter(manifest=manifest)
        .select_related('funding_run__payment__intent__order').order_by('payment__buyer_transaction__created_at', 'payment_id')
        if item.payment_id not in used and isinstance(item.snapshot, dict)
        and item.snapshot.get('paid', {}).get('plan') == manifest.plan]


def transition_payment_locked(order, manifest, plan, terms):
    """Advance an unpaid plan after actual separately funded add-on payments."""
    from .models import (PointsVoluntaryPaidRefundEvidence, PointsVoluntaryPaidPaymentSettlementRevision)
    from .points_transfer_paid_addons import unchanged
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    if (not transaction.get_connection().in_atomic_block or manifest.order_id != order.pk
            or manifest.finalized_at or order.settled_at or manifest.plan_hash != _hash(manifest.plan)):
        raise SettlementError('加购付款结算上下文异常')
    before = manifest.plan
    old_funding, new_funding = before.get('points_funding', {}), terms.get('points_funding', {})
    old_addons, new_addons = old_funding.get('addons', []), new_funding.get('addons', [])
    if not isinstance(old_addons, list) or not isinstance(new_addons, list):
        raise SettlementError('加购付款的结算份额异常')
    old_ids = {row.get('payment_id') for row in old_addons if isinstance(row, dict)}
    new_ids = {row.get('payment_id') for row in new_addons if isinstance(row, dict)}
    evidence = pending_payment_evidence(manifest)
    added = sorted(item.payment_id for item in evidence)
    old_refunds, new_refunds = old_funding.get('addon_refund_ids', []), new_funding.get('addon_refund_ids', [])
    if (not added or not isinstance(old_refunds, list) or not isinstance(new_refunds, list)
            or len(set(new_refunds)) != len(new_refunds) or not set(old_refunds).issubset(new_refunds)
            or not new_ids.issubset(old_ids | set(added))):
        raise SettlementError('加购付款与退款事件版本异常')
    refund_ids = sorted(set(new_refunds) - set(old_refunds))
    _verify_removed_payments(order.pk, old_ids - new_ids, refund_ids)
    for item in evidence:
        if item.payment_id not in new_ids:
            refunded = sum(item.payment.refund_records.values_list('amount', flat=True))
            if refunded != item.payment.intent.amount:
                raise SettlementError('未列入结算清单的加购付款尚未全额退款')
    stable = ('deal_amount', 'segments', 'fixed_fee_shortfall', 'buyer_account_id',
              'inviter_account_id', 'inviter_id', 'inviter_rate', 'deposit_percent',
              'aftersale_hours', 'freeze_hours', 'completed_at')
    if any(before.get(key) != terms.get(key) for key in stable):
        raise SettlementError('加购付款改变了主单履约事实')
    old_people = {row['account_id']: row for row in before.get('people', [])}
    new_people = {row['account_id']: row for row in terms.get('people', [])}
    if (len(old_people) != len(before.get('people', [])) or len(new_people) != len(terms.get('people', []))
            or set(old_people) != set(new_people) or any(old_people[key].get('segment_ids') != value.get('segment_ids')
                or value.get('income', -1) < 0 or value['income'] > old_people[key]['income']
                for key, value in new_people.items())):
        raise SettlementError('加购退款改变了主单履约人员或增加收益')
    refunds = list(PointsVoluntaryPaidRefundEvidence.objects.filter(manifest=manifest,
        refund_id__in=refund_ids).select_related('funding_run__refund__order'))
    if len(refunds) != len(refund_ids):
        raise SettlementError('混合结算版本缺少实际退款凭证')
    try:
        for item in evidence:
            paid = verify_payment_current_read(item.funding_run)
            if not paid or paid['plan'] != before:
                raise ValidationError('加购付款前清单已变化')
            unchanged(paid, new_addons)
            if any(old_people.get(row['account_id']) != new_people.get(row['account_id']) for row in paid['people']):
                raise ValidationError('加购退款改变了已付主单收益')
            _validate_records(order, manifest, plan, snapshot_ids=[row['id'] for row in paid['people']],
                paid_addon_snapshot=paid if old_addons else None,
                paid_addon_cutoff=item.payment.buyer_transaction.created_at)
        for item in refunds:
            paid = verify_current_read(item.funding_run)
            if not paid or paid['plan'] != before:
                raise ValidationError('退款前已付清单不一致')
            unchanged(paid, new_addons)
            if any(old_people.get(row['account_id']) != new_people.get(row['account_id']) for row in paid['people']):
                raise ValidationError('加购退款改变了已付主单收益')
    except ValidationError as exc:
        raise SettlementError('加购付款前已付收益待核对') from exc
    with transaction.atomic():
        PointsVoluntaryPaidPaymentSettlementRevision.objects.create(manifest=manifest, evidence=evidence[0],
            payment_ids=added, refund_ids=refund_ids, before_plan=before, before_plan_hash=manifest.plan_hash,
            after_plan=terms, after_plan_hash=_hash(terms))
        manifest.plan, manifest.plan_hash = terms, _hash(terms)
        manifest.save(update_fields=['plan', 'plan_hash'])
        return _validate_records(order, manifest, plan)


def verify_payment_revisions(manifest):
    """Verify each saved payment-to-settlement change and its paid ledgers."""
    from .models import PointsVoluntaryPaidPaymentEvidence, PointsVoluntaryPaidRefundEvidence
    from .points_transfer_paid_addons import unchanged
    from .segment_settlement import _hash
    from .settlement import SettlementError
    for version in manifest.voluntary_payment_revisions.select_for_update().order_by('created_at', 'pk'):
        before, after, ids, refund_ids = version.before_plan, version.after_plan, version.payment_ids, version.refund_ids
        if (not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(ids, list)
                or not ids or any(type(item) is not int or item <= 0 for item in ids)
                or ids != sorted(set(ids)) or not isinstance(refund_ids, list)
                or refund_ids != sorted(set(refund_ids)) or version.before_plan_hash != _hash(before)
                or version.after_plan_hash != _hash(after)):
            raise SettlementError('加购付款结算版本异常')
        old = before.get('points_funding', {}).get('addons', [])
        new = after.get('points_funding', {}).get('addons', [])
        if not isinstance(old, list) or not isinstance(new, list):
            raise SettlementError('加购付款结算份额异常')
        old_ids = {item.get('payment_id') for item in old if isinstance(item, dict)}
        new_ids = {item.get('payment_id') for item in new if isinstance(item, dict)}
        if not new_ids.issubset(old_ids | set(ids)):
            raise SettlementError('加购付款与结算版本不一致')
        stable = ('deal_amount', 'segments', 'fixed_fee_shortfall', 'buyer_account_id',
                  'inviter_account_id', 'inviter_id', 'inviter_rate', 'deposit_percent',
                  'aftersale_hours', 'freeze_hours', 'completed_at')
        old_refunds = before.get('points_funding', {}).get('addon_refund_ids', [])
        new_refunds = after.get('points_funding', {}).get('addon_refund_ids', [])
        if (any(before.get(key) != after.get(key) for key in stable) or not isinstance(old_refunds, list)
                or not isinstance(new_refunds, list) or not set(old_refunds).issubset(new_refunds)
                or sorted(set(new_refunds) - set(old_refunds)) != refund_ids):
            raise SettlementError('加购付款改变了主单或退款事实')
        evidence = list(PointsVoluntaryPaidPaymentEvidence.objects.filter(manifest=manifest,
            payment_id__in=ids).select_related('funding_run__payment__intent__order'))
        if len(evidence) != len(ids) or version.evidence_id not in {item.pk for item in evidence}:
            raise SettlementError('加购付款结算版本缺少真实凭证')
        refunds = list(PointsVoluntaryPaidRefundEvidence.objects.filter(manifest=manifest,
            refund_id__in=refund_ids).select_related('funding_run__refund__order'))
        if len(refunds) != len(refund_ids):
            raise SettlementError('混合结算版本缺少真实退款凭证')
        try:
            _verify_removed_payments(manifest.order_id, old_ids - new_ids, refund_ids, before=version.created_at)
            for item in evidence:
                paid = verify_payment_current_read(item.funding_run)
                if not paid or paid['plan'] != before or item.funding_run.created_at > version.created_at:
                    raise ValidationError('加购付款前清单凭证异常')
                if (item.payment_id not in new_ids and
                        sum(item.payment.refund_records.filter(created_at__lt=version.created_at).values_list('amount', flat=True))
                        != item.payment.intent.amount):
                    raise ValidationError('加购付款未全退但不在结算清单')
                unchanged(paid, new)
                _verify_mixed_people(before, after, paid)
            for item in refunds:
                paid = verify_current_read(item.funding_run)
                if not paid or paid['plan'] != before or item.funding_run.created_at > version.created_at:
                    raise ValidationError('退款前已付清单凭证异常')
                unchanged(paid, new)
                _verify_mixed_people(before, after, paid)
        except ValidationError as exc:
            raise SettlementError('加购付款结算流水异常') from exc


def verify_revisions_current_read(manifest, *, start_plan, start_hash, receipt_id, before=None, after=None):
    """Replay paid refund versions in time order under the caller's receipt lock."""
    from django.core.exceptions import ValidationError as DjangoValidationError
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _require_current_unlocked_reads, after_main_addon_release_facts
    from .models import OrderAddonFundingReleaseRun, PointsVoluntaryPaidPaymentEvidence, PointsVoluntaryPaidRefundEvidence
    from .points_transfer_addon_zero_receipt import facts as zero_facts
    from .points_transfer_paid_addons import unchanged
    from .segment_settlement import _hash
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    query = manifest.addon_revisions.order_by('version')
    if after is not None:
        if query.filter(created_at=after).exists():
            raise DjangoValidationError('主单与加购结算版本时间无法区分')
        query = query.filter(created_at__gt=after)
    if before is not None:
        if query.filter(created_at=before).exists():
            raise DjangoValidationError('退款与结算版本时间无法区分')
        query = query.filter(created_at__lt=before)
    payment_query = manifest.voluntary_payment_revisions.all()
    if after is not None:
        if payment_query.filter(created_at=after).exists():
            raise DjangoValidationError('付款与主单结算版本时间无法区分')
        payment_query = payment_query.filter(created_at__gt=after)
    if before is not None:
        if payment_query.filter(created_at=before).exists():
            raise DjangoValidationError('付款与退款结算版本时间无法区分')
        payment_query = payment_query.filter(created_at__lt=before)
    events = [(row.created_at, 'refund', row) for row in query]
    events += [(row.created_at, 'payment', row) for row in payment_query]
    handover_query = manifest.paid_handover_revisions.all()
    if after is not None:
        if handover_query.filter(created_at=after).exists():
            raise DjangoValidationError('接替与主单结算版本时间无法区分')
        handover_query = handover_query.filter(created_at__gt=after)
    if before is not None:
        if handover_query.filter(created_at=before).exists():
            raise DjangoValidationError('接替与结算版本时间无法区分')
        handover_query = handover_query.filter(created_at__lt=before)
    events += [(row.created_at, 'handover', row) for row in handover_query]
    completion_query = manifest.paid_completion_revisions.all()
    if after is not None:
        if completion_query.filter(created_at=after).exists():
            raise DjangoValidationError('订单完成与其他结算版本时间无法区分')
        completion_query = completion_query.filter(created_at__gt=after)
    if before is not None:
        if completion_query.filter(created_at=before).exists():
            raise DjangoValidationError('订单完成与结算边界时间无法区分')
        completion_query = completion_query.filter(created_at__lt=before)
    events += [(row.created_at, 'completion', row) for row in completion_query]
    if len({item[0] for item in events}) != len(events):
        raise DjangoValidationError('加购结算版本时间无法区分')
    previous, current_hash = start_plan, start_hash
    previous_version = None
    for _, kind, revision in sorted(events, key=lambda item: item[0]):
        old, new = revision.before_plan, revision.after_plan
        if kind == 'completion':
            from .points_followup_paid_handover import _check_completion, _digest as paid_digest
            if (old != previous or revision.before_plan_hash != current_hash
                    or revision.before_plan_hash != _hash(old) or revision.after_plan_hash != _hash(new)
                    or revision.paid_hash != paid_digest(revision.paid_snapshot)):
                raise DjangoValidationError('订单完成结算版本链异常')
            try:
                _check_completion(manifest.order, revision.completion_log, old, new,
                                  revision.paid_snapshot, revision.created_at)
            except ValidationError as exc:
                raise DjangoValidationError('订单完成结算来源异常') from exc
            previous, current_hash = new, revision.after_plan_hash
            continue
        if kind == 'handover':
            from .points_followup_paid_handover import _check_revision
            if (old != previous or revision.before_plan_hash != current_hash
                    or revision.before_plan_hash != _hash(old) or revision.after_plan_hash != _hash(new)
                    or revision.evidence.manifest_id != manifest.pk
                    or revision.evidence.funding_run.created_at > revision.created_at):
                raise DjangoValidationError('已付接替结算版本链异常')
            try:
                _check_revision(revision.evidence, old, new, receipt_id=receipt_id)
            except ValidationError as exc:
                raise DjangoValidationError('已付接替结算来源异常') from exc
            previous, current_hash = new, revision.after_plan_hash
            continue
        if kind == 'payment':
            ids, refund_ids = revision.payment_ids, revision.refund_ids
            if (old != previous or revision.before_plan_hash != current_hash
                    or revision.before_plan_hash != _hash(old) or revision.after_plan_hash != _hash(new)
                    or not isinstance(ids, list) or not ids or ids != sorted(set(ids))
                    or not isinstance(refund_ids, list) or refund_ids != sorted(set(refund_ids))):
                raise DjangoValidationError('加购付款结算版本链异常')
            old_addons = old.get('points_funding', {}).get('addons', [])
            new_addons = new.get('points_funding', {}).get('addons', [])
            if not isinstance(old_addons, list) or not isinstance(new_addons, list):
                raise DjangoValidationError('加购付款结算份额异常')
            old_payments = {item.get('payment_id') for item in old_addons if isinstance(item, dict)}
            new_payments = {item.get('payment_id') for item in new_addons if isinstance(item, dict)}
            if not new_payments.issubset(old_payments | set(ids)):
                raise DjangoValidationError('加购付款结算来源不一致')
            stable = ('deal_amount', 'segments', 'fixed_fee_shortfall', 'buyer_account_id',
                      'inviter_account_id', 'inviter_id', 'inviter_rate', 'deposit_percent',
                      'aftersale_hours', 'freeze_hours', 'completed_at')
            old_refunds = old.get('points_funding', {}).get('addon_refund_ids', [])
            new_refunds = new.get('points_funding', {}).get('addon_refund_ids', [])
            if (any(old.get(key) != new.get(key) for key in stable) or not isinstance(old_refunds, list)
                    or not isinstance(new_refunds, list) or not set(old_refunds).issubset(new_refunds)
                    or sorted(set(new_refunds) - set(old_refunds)) != refund_ids):
                raise DjangoValidationError('加购付款改变了主单或退款事实')
            payment_sources = list(PointsVoluntaryPaidPaymentEvidence.objects.filter(
                manifest=manifest, payment_id__in=ids).select_related('funding_run__payment__intent__order'))
            if len(payment_sources) != len(ids) or revision.evidence_id not in {item.pk for item in payment_sources}:
                raise DjangoValidationError('加购付款结算版本凭证不完整')
            refund_sources = list(PointsVoluntaryPaidRefundEvidence.objects.filter(
                manifest=manifest, refund_id__in=refund_ids).select_related('funding_run__refund__order'))
            if len(refund_sources) != len(refund_ids):
                raise DjangoValidationError('混合结算版本退款凭证不完整')
            try:
                _verify_removed_payments(manifest.order_id, old_payments - new_payments, refund_ids,
                                         before=revision.created_at)
                for item in payment_sources:
                    run = item.funding_run
                    paid = verify_payment_current_read(run)
                    if not paid or paid['plan'] != old or run.created_at > revision.created_at:
                        raise ValidationError('加购付款前已付凭证异常')
                    if (item.payment_id not in new_payments and
                            sum(item.payment.refund_records.filter(created_at__lt=revision.created_at).values_list('amount', flat=True))
                            != item.payment.intent.amount):
                        raise ValidationError('加购付款未全退但不在结算清单')
                    unchanged(paid, new.get('points_funding', {}).get('addons', []))
                    _verify_mixed_people(old, new, paid)
                    release = run.releases.select_related('run', 'allocation').order_by('pk').first()
                    if isinstance(run.points_followup_snapshot, dict) and run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1':
                        from .points_transfer_followup_addon_version import _verify_prior_run
                        _verify_prior_run(run, receipt_id=receipt_id)
                    elif release:
                        after_main_addon_release_facts(release, witness_receipt_id=receipt_id)
                    else:
                        zero_facts(run, receipt_id=receipt_id)
                for item in refund_sources:
                    run = item.funding_run
                    paid = verify_current_read(run)
                    if not paid or paid['plan'] != old or run.created_at > revision.created_at:
                        raise ValidationError('退款前已付凭证异常')
                    unchanged(paid, new.get('points_funding', {}).get('addons', []))
                    _verify_mixed_people(old, new, paid)
                    release = run.releases.select_related('run', 'allocation').order_by('pk').first()
                    if isinstance(run.points_followup_snapshot, dict) and run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1':
                        from .points_transfer_followup_addon_version import _verify_prior_run
                        _verify_prior_run(run, receipt_id=receipt_id)
                    elif release:
                        after_main_addon_release_facts(release, witness_receipt_id=receipt_id)
                    else:
                        zero_facts(run, receipt_id=receipt_id)
            except (ValidationError, KeyError, TypeError) as exc:
                raise DjangoValidationError('加购付款资金来源异常') from exc
            previous, current_hash = new, revision.after_plan_hash
            continue
        old_ids = old.get('points_funding', {}).get('addon_refund_ids', [])
        new_ids = new.get('points_funding', {}).get('addon_refund_ids', [])
        if ((previous_version is not None and revision.version != previous_version + 1)
                or old != previous or revision.before_plan_hash != current_hash
                or revision.before_plan_hash != _hash(old) or revision.after_plan_hash != _hash(new)
                or not isinstance(old_ids, list) or not isinstance(new_ids, list)
                or len(set(new_ids)) != len(new_ids) or not set(old_ids).issubset(new_ids)
                or sorted(set(new_ids) - set(old_ids)) != revision.refund_ids):
            raise DjangoValidationError('已付加购退款结算版本链异常')
        runs = list(OrderAddonFundingReleaseRun.objects.filter(refund_id__in=revision.refund_ids,
            refund__order_id=manifest.order_id).select_related('refund__order').order_by('refund__created_at', 'refund_id'))
        if len(runs) != len(revision.refund_ids):
            raise DjangoValidationError('已付加购退款资金事件缺失')
        try:
            old_people = {item['account_id']: item for item in old.get('people', [])}
            new_people = {item['account_id']: item for item in new.get('people', [])}
            for run in runs:
                paid = verify_current_read(run)
                if not paid or paid['plan'] != old or run.created_at > revision.created_at:
                    raise ValidationError('已付退款与结算版本不一致')
                unchanged(paid, new.get('points_funding', {}).get('addons', []))
                if any(old_people.get(item['account_id']) != new_people.get(item['account_id'])
                       for item in paid['people']):
                    raise ValidationError('已付主单收益发生变化')
                release = run.releases.select_related('run', 'allocation').order_by('pk').first()
                if isinstance(run.points_followup_snapshot, dict) and run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1':
                    from .points_transfer_followup_addon_version import _verify_prior_run
                    _verify_prior_run(run, receipt_id=receipt_id)
                elif release:
                    after_main_addon_release_facts(release, witness_receipt_id=receipt_id)
                else:
                    zero_facts(run, receipt_id=receipt_id)
        except (ValidationError, KeyError, TypeError) as exc:
            raise DjangoValidationError('已付加购退款来源异常') from exc
        previous, current_hash = new, revision.after_plan_hash
        previous_version = revision.version
    if before is None and (manifest.plan != previous or manifest.plan_hash != current_hash):
        raise DjangoValidationError('已付加购结算版本与当前清单不一致')
    return previous, current_hash
