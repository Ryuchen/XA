"""Evidence that a real refund invalidated an unaccepted transfer handover."""
import hashlib
import json

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def permits_other_payment_locked(order, refund_payment_id, *, allow_same_payment=False):
    """Recognize a pending same-team split without granting an allocation."""
    from stores.db import transaction
    from .models import (OrderAddonHandover, OrderAddonPayment, OrderAddonFundingReleaseRun,
                         OrderOriginalShareTransfer, OrderPrestartHandoff, OrderProvider,
                         OrderProviderCompletionFact, PointsNegotiationPosting)
    from .points_transfer_followup_handover_scope import _verify_rejected
    from .addon_handover import current_responsibilities
    if (not transaction.get_connection().in_atomic_block or order.required_provider_count != 2
            or order.refund_records.count() > 1 or order.settled_at
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or not OrderAddonPayment.objects.filter(pk=refund_payment_id, intent__order=order).exists()):
        return False
    pending = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
                   .select_related('payment__intent', 'source_segment', 'target_segment'))
    if (len(pending) != 1 or (pending[0].payment_id == refund_payment_id and not allow_same_payment)
            or not pending[0].previous_id):
        return False
    handover = pending[0]
    posting = None
    if order.refund_records.exists():
        if not allow_same_payment or handover.payment_id != refund_payment_id:
            return False
        posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
        if (not posting or not posting.termination_in_service or not posting.termination_processed_at
                or posting.termination_processed_at > handover.created_at
                or handover.source_segment.released_at != posting.accepted_at
                or not posting.shares.filter(account_id=handover.source_segment.provider_account_id,
                    assignment__released_at=posting.accepted_at).exists()):
            return False
    previous, shares = current_responsibilities(handover.payment)
    active = [row for row in shares if row['is_current'] and row['basis'] > 0]
    if (not previous or previous.pk != handover.previous_id or len(active) != 1
            or active[0]['segment_id'] != handover.source_segment_id
            or active[0]['basis'] != handover.source_basis_before
            or (not posting and handover.source_segment.released_at) or handover.target_segment.released_at
            or not handover.source_segment.started_at or not handover.target_segment.started_at
            or OrderProviderCompletionFact.objects.filter(order=order,
                segment_id__in=[handover.source_segment_id, handover.target_segment_id]).exists()
            or order.providers.filter(settled_at__isnull=False).exists()
            or not OrderProvider.objects.filter(order=order,
                provider_account_id=handover.target_segment.provider_account_id,
                released_at__isnull=True, settled_at__isnull=True).exists()
            or (not posting and not OrderProvider.objects.filter(order=order,
                provider_account_id=handover.source_segment.provider_account_id,
                released_at__isnull=True, settled_at__isnull=True).exists())):
        return False
    run = OrderAddonFundingReleaseRun.objects.filter(handover=previous,
        points_handover_snapshot__kind='SAME_TEAM_V1').first()
    if not run or run.created_at >= handover.created_at or (posting and run.created_at >= posting.created_at):
        return False
    _verify_rejected(handover.payment, handover, pending=True)
    return True


def capture(handover, refund):
    from .addon_handover import current_responsibilities, _income
    from .models import PointsNegotiationPosting
    payment, order = handover.payment, handover.payment.intent.order
    same_payment = refund.payment_id == payment.pk
    mains = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        created_at__lt=refund.created_at).order_by('created_at', 'pk'))
    posting = mains[0] if len(mains) == 1 else None
    postmain = bool(same_payment and posting and posting.termination_in_service
        and posting.termination_processed_at and posting.termination_processed_at <= handover.created_at
        and handover.source_segment.released_at == posting.accepted_at
        and posting.shares.filter(account_id=handover.source_segment.provider_account_id,
            assignment__released_at=posting.accepted_at).exists())
    if (handover.status != 'SUPERSEDED' or handover.accepted_at
            or refund.order_id != order.pk
            or not handover.created_at or not refund.created_at or refund.created_at <= handover.created_at
            or (mains and not postmain) or len(mains) > 1
            or order.refund_records.filter(created_at__lte=refund.created_at).count() != len(mains)
            or payment.refund_records.filter(created_at__gte=handover.created_at,
                created_at__lte=refund.created_at).exclude(pk=refund.pk).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                refund__created_at__lt=refund.created_at).exists()):
        raise ValidationError('接替失效与退款时序异常')
    previous, parts = current_responsibilities(payment, before=handover.created_at)
    current = [row for row in parts if row['is_current']]
    if ((previous.pk if previous else None) != handover.previous_id
            or len(current) != 1 or current[0]['segment_id'] != handover.source_segment_id):
        raise ValidationError('接替失效前的原始责任异常')
    if previous:
        from .models import OrderAddonFundingReleaseRun
        from .addon_funding_release import verify_run
        prior_run = OrderAddonFundingReleaseRun.objects.filter(handover=previous).first()
        if not prior_run or prior_run.created_at >= handover.created_at:
            raise ValidationError('接替失效缺少真实前版资金事件')
        verify_run(prior_run, _skip_usage=True)
    source = current[0]
    if (source['basis'] <= 0 or handover.transferred_basis <= 0
            or handover.retained_basis + handover.transferred_basis != source['basis']):
        raise ValidationError('接替失效前的剩余份额异常')
    rows = list(refund.earning_deductions.filter(payment=payment).select_related('participant', 'segment').order_by('pk'))
    if same_payment:
        from .addon_refund_share_allocation import verify as verify_share_allocations
        verify_share_allocations(refund)
        allocations = list(refund.share_allocations.filter(payment=payment).order_by('pk'))
        if not allocations or sum(row.refund_basis for row in allocations) != refund.amount:
            raise ValidationError('同付款退款缺少真实逐份额退回凭证')
    else:
        allocations = []
    if ((not rows and not same_payment) or any((not same_payment and row.segment_id != handover.source_segment_id) or row.amount <= 0
                        or row.participant.proposal_id != refund.proposal_id
                        or row.segment.provider_account_id != row.participant.account_id
                        or (not same_payment and row.participant.account_id != handover.source_segment.provider_account_id)
                        for row in rows) or (not same_payment and sum(row.amount for row in rows) > source['income'])
            or handover.source_basis_before != source['basis']):
        raise ValidationError('退款未真实改变待确认接替的收益')
    shares = list(handover.shares.order_by('position'))
    source_income = _income(source['basis'], source['commission_snapshot'])
    retained = (_income(handover.retained_basis, source['commission_snapshot'])
                if source['income'] == source_income else
                source['income'] * handover.retained_basis // source['basis'])
    expected = [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], False)
                for row in parts if not row['is_current']]
    if handover.retained_basis:
        expected.append((handover.source_segment_id, handover.retained_basis, retained,
                         source['commission_snapshot'], False))
    target = next((row for row in shares if row.is_current), None)
    if not target:
        raise ValidationError('接替失效的目标份额缺失')
    expected.append((handover.target_segment_id, handover.transferred_basis,
                     _income(handover.transferred_basis, target.commission_snapshot),
                     target.commission_snapshot, True))
    if ([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in shares]
            != expected or [row.position for row in shares] != list(range(len(shares)))):
        raise ValidationError('接替失效前的份额快照异常')
    decisions = list(handover.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected_parties = {(payment.intent.buyer_id, 'BOSS'),
        (handover.source_segment.provider_account_id, 'PROVIDER'),
        (handover.target_segment.provider_account_id, 'PROVIDER')}
    if (len(decisions) != len(expected_parties) or {(row[0], row[1]) for row in decisions} != expected_parties
            or any(accepted is False or ((accepted is None) != (at is None))
                   or (at and at > refund.created_at) for _, _, accepted, at in decisions)
            or all(accepted is True for _, _, accepted, _ in decisions)):
        raise ValidationError('失效前接替确认记录异常')
    result = {'order_id': order.pk, 'payment_id': payment.pk, 'handover_id': handover.pk,
        'refund_id': refund.pk, 'basis_before': source['basis'], 'income_before': source['income'],
        'deductions': [[row.pk, row.participant_id, row.segment_id, row.amount] for row in rows],
        'shares': [[row.pk, row.segment_id, row.position, row.basis, row.income,
                    row.commission_snapshot, row.is_current] for row in shares],
        'decisions': [[account, role, accepted, at.isoformat() if at else None]
                      for account, role, accepted, at in decisions]}
    if previous:
        result['previous_handover_id'] = previous.pk
        result['previous_funding_event'] = [prior_run.pk, prior_run.points_handover_hash]
    if same_payment:
        result['same_payment_refund_basis'] = [[row.pk, row.participant_id, row.share_id,
            row.refund_basis, row.earning_deduction] for row in allocations]
    if postmain:
        result['main_posting_id'] = posting.pk
        result['main_refund_id'] = posting.points_refund.refund_id
    return result


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def provisional_structure(order):
    """Check persisted source rows without treating this as RELEASE proof."""
    from .models import OrderAddonHandover, PointsHandoverRefundInvalidation
    rows = list(OrderAddonHandover.objects.filter(payment__intent__order=order).order_by('pk'))
    if not rows or any(row.status != 'SUPERSEDED' for row in rows):
        return False
    for handover in rows:
        record = PointsHandoverRefundInvalidation.objects.filter(handover=handover).select_related(
            'handover__payment__intent', 'refund').first()
        if not record or not isinstance(record.snapshot, dict) or record.evidence_hash != digest(record.snapshot):
            return False
        try:
            if capture(record.handover, record.refund) != record.snapshot:
                return False
        except (ValidationError, RestValidationError, ValueError, TypeError, ZeroDivisionError):
            return False
    return True


def verify_current_read(record, *, receipt_id):
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _require_current_unlocked_reads
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    return _verify_facts(record, receipt_id=receipt_id)


def verify_order_locked(record):
    """Verify a real zero-allocation event without manufacturing a receipt."""
    from django.db.models import Q
    from stores.db import transaction
    from .models import Order, OrderPointsFundingAllocation
    from .financial_lock import lock_order_money
    from .points_transfer_refund_access import permits, verification_scope
    order = record.handover.payment.intent.order
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('无到账的失效凭证须在订单事务内核对')
    if permits(order, phase='transfer'):
        with verification_scope(order):
            return verify_order_locked(record)
    if not permits(order, phase='refund'):
        raise ValidationError('无到账的失效凭证缺少本单资金权限')
    if not Order.objects.select_for_update().filter(pk=order.pk).exists():
        raise ValidationError('失效凭证订单不存在')
    lock_order_money()
    if OrderPointsFundingAllocation.objects.filter(Q(redemption__order=order) | Q(service_order=order)).exists():
        raise ValidationError('订单已有到账分配，须使用真实到账见证')
    from users.receipt_usage_sources import _require_current_unlocked_reads
    _require_current_unlocked_reads()
    return _verify_facts(record, receipt_id=None)


def _verify_facts(record, *, receipt_id):
    from .models import OrderAddonFundingReleaseRun
    from .addon_funding_release import verify_run
    from .points_addon_receipt_usage import facts as refund_facts
    if (not isinstance(record.snapshot, dict) or record.evidence_hash != digest(record.snapshot)
            or capture(record.handover, record.refund) != record.snapshot
            or record.created_at < record.refund.created_at):
        raise ValidationError('退款导致接替失效的凭证不一致')
    run = OrderAddonFundingReleaseRun.objects.filter(refund=record.refund).first()
    if (not run or not isinstance(run.points_addon_snapshot, dict)
            or (receipt_id is None and (run.allocation_snapshot or run.released_amount or run.releases.exists()))):
        raise ValidationError('接替失效缺少实际退款 RELEASE')
    try:
        if refund_facts(record.refund, read_receipt_id=receipt_id) != run.points_addon_snapshot:
            raise ValidationError('退款来源与 RELEASE 快照不一致')
        verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
    except RestValidationError as exc:
        raise ValidationError('接替失效的退款 RELEASE 异常') from exc
    return record.snapshot
