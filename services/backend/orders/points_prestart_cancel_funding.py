"""Return unspent order funding allocations during a full pre-service refund."""
import hashlib
import json

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction
from .models import (OrderAddonFundingRelease, OrderPointsFundingAllocation,
    PointsPrestartCancelFundingEvidence, PointsPrestartCancelFundingRelease)


def _digest(refund_id, slug, facts):
    return hashlib.sha256(json.dumps([refund_id, slug, facts], sort_keys=True,
        ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _prior_releases(allocation_ids):
    return list(OrderAddonFundingRelease.objects.filter(allocation_id__in=allocation_ids)
                .select_related('allocation__redemption', 'run__prestart_handoff').order_by('pk'))


def capture_locked(order):
    """Call after order and money locks, before the buyer's wallet is locked."""
    if not transaction.get_connection().in_atomic_block or not current_store():
        raise ValidationError('取消补资核对必须在本店订单事务中执行')
    owner = Q(redemption__order=order) | Q(service_order=order)
    if not OrderPointsFundingAllocation.objects.filter(owner).exists():
        return None
    from .points_funding_evidence import verified_order_allocations_locked
    from .points_funding_plan import PointsFundingPlanError
    try:
        allocations = verified_order_allocations_locked(order)
    except PointsFundingPlanError as exc:
        raise ValidationError('订单到账分配待核对') from exc
    if not allocations:
        return None
    if any(not item.redemption_id or item.redemption.order_id != order.pk or item.service_order_id for item in allocations):
        raise ValidationError('订单到账分配归属异常')
    prior = _prior_releases([item.pk for item in allocations])
    from .points_prestart_receipt_usage import facts as prestart_facts
    prior_facts = [list(prestart_facts(row)) for row in prior]
    totals = {item.pk: 0 for item in allocations}
    for row in prior:
        totals[row.allocation_id] += row.amount
    entries = [[item.pk, item.receipt_id, item.amount, totals[item.pk]] for item in allocations]
    if any(released > amount for _, _, amount, released in entries):
        raise ValidationError('先前释放超过订单到账分配')
    return {'order_id': order.pk, 'buyer_id': order.customer_account_id,
            'allocations': entries, 'prior_releases': [[row.pk, row.allocation_id, row.amount] for row in prior],
            'prior_facts': prior_facts}


def record_locked(points_refund, facts):
    if facts is None:
        return None
    if not transaction.get_connection().in_atomic_block or not current_store():
        raise ValidationError('取消补资释放必须在本店订单事务中执行')
    redemption, refund = points_refund.redemption, points_refund.refund
    if (facts['order_id'] != redemption.order_id or facts['buyer_id'] != redemption.ledger.account_id
            or not points_refund.is_final or points_refund.equivalent_units != redemption.deal_units
            or points_refund.coin_units != redemption.paid_units
            or points_refund.points_hundredths != redemption.points_hundredths):
        raise ValidationError('取消退款和补资来源不一致')
    slug = current_store().slug
    evidence = PointsPrestartCancelFundingEvidence.objects.create(points_refund=points_refund,
        store_slug=slug, facts=facts, evidence_hash=_digest(points_refund.pk, slug, facts))
    releases = [PointsPrestartCancelFundingRelease(evidence=evidence, allocation_id=pk, amount=amount-released)
                for pk, _, amount, released in facts['allocations'] if amount > released]
    PointsPrestartCancelFundingRelease.objects.bulk_create(releases)
    from users.receipt_usage_sources import record_prestart_cancel_release_locked, verify_allocations_locked
    rows = list(evidence.releases.select_related('allocation__redemption', 'evidence__points_refund__redemption')
                .order_by('allocation__receipt_id', 'pk'))
    for row in rows:
        record_prestart_cancel_release_locked(row)
    for receipt_id in sorted({item[1] for item in facts['allocations']}):
        verify_allocations_locked(receipt_id)
    return evidence


def verify_replay_locked(refund):
    """Recheck the committed release before reporting an idempotent refund."""
    if not transaction.get_connection().in_atomic_block or not current_store():
        raise ValidationError('取消退款核对必须在本店事务中执行')
    effects = refund.benefit_effects if isinstance(refund.benefit_effects, dict) else {}
    proof = getattr(refund, 'points_refund', None)
    evidence = PointsPrestartCancelFundingEvidence.objects.filter(points_refund=proof).first() if proof else None
    if effects.get('points_prestart_cancel_funding') is not True:
        if evidence:
            raise ValidationError('服务前取消退款补资标记异常')
        return
    if (not evidence or evidence.store_slug != current_store().slug or not isinstance(evidence.facts, dict)
            or evidence.evidence_hash != _digest(proof.pk, evidence.store_slug, evidence.facts)
            or evidence.facts.get('order_id') != refund.order_id):
        raise ValidationError('服务前取消补资凭证缺失或不一致')
    entries = evidence.facts.get('allocations')
    if (not isinstance(entries, list) or not entries or any(not isinstance(row, list) or len(row) != 4
            or any(type(value) is not int or value < 0 for value in row) for row in entries)
            or len({row[0] for row in entries}) != len(entries)):
        raise ValidationError('服务前取消到账分配快照异常')
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_sources import verify_allocations_locked
    from .financial_lock import lock_order_money
    lock_order_money()
    ids = {row[1] for row in entries}
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if {item.pk for item in receipts} != ids:
        raise ValidationError('服务前取消原到账记录缺失')
    owner = Q(redemption__order_id=refund.order_id) | Q(service_order_id=refund.order_id)
    allocations = list(OrderPointsFundingAllocation.objects.filter(owner).order_by('pk')
                       .values_list('pk', 'receipt_id', 'amount', 'redemption_id', 'service_order_id'))
    if allocations != sorted((row[0], row[1], row[2], proof.redemption_id, None) for row in entries):
        raise ValidationError('服务前取消原到账分配不一致')
    from .points_prestart_receipt_usage import facts as prestart_facts
    prior = _prior_releases([row[0] for row in entries])
    totals = dict.fromkeys((row[0] for row in entries), 0)
    for row in prior:
        totals[row.allocation_id] += row.amount
    if ([[row.pk, row.allocation_id, row.amount] for row in prior] != evidence.facts.get('prior_releases')
            or [list(prestart_facts(row)) for row in prior] != evidence.facts.get('prior_facts')
            or any(totals[pk] != amount for pk, _, _, amount in entries)):
        raise ValidationError('服务前取消先前释放记录异常')
    expected = sorted((pk, amount - prior) for pk, _, amount, prior in entries if amount > prior)
    actual = list(evidence.releases.order_by('allocation_id').values_list('allocation_id', 'amount'))
    if actual != expected or any(prior > amount for _, _, amount, prior in entries):
        raise ValidationError('服务前取消到账释放不完整')
    for receipt in receipts:
        verify_allocations_locked(receipt.pk)
