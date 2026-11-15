"""Prove other-payment refunds that did not change a pending handover."""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def is_unaffected(refund, handover):
    """A refund cannot change the pending payment's basis or segment income."""
    if refund.payment_id == handover.payment_id:
        return False
    return not refund.earning_deductions.filter(payment_id=handover.payment_id).exists()


def prior_invalidations(order, handover):
    """Structurally bind each earlier retry to its actual refund event."""
    from .models import PointsHandoverRefundInvalidation
    from .points_handover_refund_invalidation import capture, digest
    rows = list(handover.payment.handovers.filter(version__lt=handover.version).order_by('version'))
    if [row.version for row in rows] != list(range(1, handover.version)):
        return None
    events = []
    for row in rows:
        event = PointsHandoverRefundInvalidation.objects.filter(handover=row).select_related(
            'handover__payment__intent', 'refund').first()
        if (row.status != 'SUPERSEDED' or row.previous_id or row.accepted_at or not event
                or not isinstance(event.snapshot, dict) or event.evidence_hash != digest(event.snapshot)
                or not event.created_at or event.created_at >= handover.created_at):
            return None
        try:
            if capture(event.handover, event.refund) != event.snapshot:
                return None
        except (ValidationError, RestValidationError, ValueError, TypeError, ZeroDivisionError):
            return None
        events.append(event)
    return events


def permits_other_refund(order, transfers, payment_id):
    """Recognize one pending first handover under the already-held order lock."""
    from .models import OrderAddonHandover, OrderAddonPayment
    from .addon_handover import current_responsibilities, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    if type(payment_id) is not int or payment_id <= 0 or order.refund_records.exists() or order.settled_at:
        return False
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('intent').order_by('pk'))
    pending = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
                   .select_related('payment__intent', 'source_segment', 'target_segment').order_by('pk'))
    if (len(payments) < 2 or len(payments) != order.addon_intents.count() or len(pending) != 1
            or payment_id not in {item.pk for item in payments} or pending[0].payment_id == payment_id):
        return False
    target = next(item for item in payments if item.pk == payment_id)
    if target.handovers.exists():
        return False
    handover = pending[0]
    if OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED').exists():
        return False
    if OrderAddonHandover.objects.filter(payment__intent__order=order).exclude(
            pk=handover.pk).exclude(payment_id=handover.payment_id).exists():
        return False
    if (handover.previous_id or handover.accepted_at is not None
            or not handover.created_at or handover.payment.intent.order_id != order.pk
            or not any(item.previous_segment_id == handover.source_segment_id
                       and item.created_at < handover.created_at for item in transfers)
            or handover.source_segment.attribution.order_id != order.pk
            or handover.target_segment.attribution.order_id != order.pk
            or handover.source_segment.provider_account_id == handover.target_segment.provider_account_id):
        return False
    if prior_invalidations(order, handover) is None:
        return False
    previous, rows = current_responsibilities(handover.payment)
    current = [row for row in rows if row['is_current']]
    shares = list(handover.shares.order_by('position'))
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    parties = {(handover.payment.intent.buyer_id, 'BOSS'),
               (handover.source_segment.provider_account_id, 'PROVIDER'),
               (handover.target_segment.provider_account_id, 'PROVIDER')}
    if not (previous is None and len(current) == 1 and current[0]['segment_id'] == handover.source_segment_id):
        return False
    if (not handover.target_segment.started_at or handover.target_segment.released_at
            or not order.providers.filter(provider_account_id=handover.target_segment.provider_account_id,
                                           released_at__isnull=True).exists()):
        return False
    try:
        target_snapshot = _commission(handover.target_segment, lock=False)
        source = current[0]
        original_income = _income(source['basis'], source['commission_snapshot'])
        retained_income = (_income(handover.retained_basis, source['commission_snapshot'])
            if source['income'] == original_income else
            source['income'] * handover.retained_basis // source['basis'])
        expected = []
        if handover.retained_basis:
            expected.append((handover.source_segment_id, handover.retained_basis, retained_income,
                             source['commission_snapshot'], False))
        expected.append((handover.target_segment_id, handover.transferred_basis,
                         _income(handover.transferred_basis, target_snapshot), target_snapshot, True))
    except (RestValidationError, AddonSelectionError, ValueError, TypeError, ZeroDivisionError):
        return False
    return bool([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current)
                 for row in shares] == expected and [row.position for row in shares] == list(range(len(shares)))
        and current[0]['basis'] == handover.source_basis_before and handover.transferred_basis > 0
        and handover.retained_basis + handover.transferred_basis == handover.source_basis_before
        and sum(row.basis for row in shares) == handover.source_basis_before
        and sum(row.is_current for row in shares) == 1 and len(decisions) == len(parties)
        and {(account, role) for account, role, _, _ in decisions} == parties
        and all(accepted is not False and ((accepted is None) == (at is None))
                for _, _, accepted, at in decisions)
        and not all(accepted is True for _, _, accepted, _ in decisions))


def verify_accepted_history(handover, *, receipt_id):
    """Rebuild intervening events under a receipt or zero-allocation order lock."""
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .models import OrderAddonFundingReleaseRun
    from .addon_funding_release import verify_run
    from .points_addon_receipt_usage import facts as refund_facts
    from .addon_handover import current_responsibilities
    if receipt_id is None:
        from .points_transfer_refund_evidence import _facts_without_receipt_locked
        _facts_without_receipt_locked(handover.payment.intent.order)
    else:
        _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    if (not handover.created_at or not handover.accepted_at or handover.previous_id
            or handover.created_at >= handover.accepted_at):
        raise ValidationError('待确认接替的历史范围尚未接通')
    payment, order = handover.payment, handover.payment.intent.order
    if order.addon_refunds.filter(created_at=handover.created_at).exists() or order.addon_refunds.filter(
            created_at=handover.accepted_at).exists():
        raise ValidationError('退款与接替方案时间无法区分')
    events = list(order.addon_refunds.filter(created_at__gt=handover.created_at,
        created_at__lt=handover.accepted_at).select_related('payment__intent', 'proposal', 'order')
        .order_by('created_at', 'pk'))
    if len({event.created_at for event in events}) != len(events):
        raise ValidationError('待确认期间退款事件顺序无法区分')
    previous, rows = current_responsibilities(payment, before=handover.created_at)
    current = [row for row in rows if row['is_current']]
    if (previous or len(current) != 1 or current[0]['segment_id'] != handover.source_segment_id
            or current[0]['basis'] != handover.source_basis_before):
        raise ValidationError('待确认接替原始责任已变化')
    result = []
    for refund in events:
        if not is_unaffected(refund, handover):
            raise ValidationError('退款改变了待确认接替，应重新发起接替')
        run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
        if not run or not isinstance(run.points_addon_snapshot, dict) or run.created_at >= handover.accepted_at:
            raise ValidationError('待确认期间退款缺少真实释放凭证')
        try:
            actual = refund_facts(refund, read_receipt_id=receipt_id)
            verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
        except RestValidationError as exc:
            raise ValidationError('待确认期间退款资金凭证异常') from exc
        if actual != run.points_addon_snapshot:
            raise ValidationError('待确认期间退款来源与资金快照不一致')
        result.append([refund.pk, refund.created_at.isoformat(), run.pk, run.points_addon_hash,
                       run.released_amount])
    return {'source_segment_id': handover.source_segment_id, 'source_basis': current[0]['basis'],
            'source_income': current[0]['income'], 'unaffected_refunds': result}
