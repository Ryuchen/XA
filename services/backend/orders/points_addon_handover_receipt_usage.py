"""Receipt-local proof of a points add-on handover after service transfer."""
from django.core.exceptions import ValidationError


def facts(run, receipt_id):
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .points_addon_receipt_usage import payment_facts
    from .points_transfer_refund_evidence import _facts_current_read, _facts_without_receipt_locked
    from .points_transfer_receipt_usage import historical_plan
    from .addon_funding_history import combined_at
    from .models import OrderOriginalShareTransfer, OrderAddonHandover, OrderPrestartHandoff
    _require_current_unlocked_reads()
    handover = run.handover
    if not handover or handover.status != 'ACCEPTED' or not handover.accepted_at:
        raise ValidationError('积分加购接替确认凭证缺失')
    payment, order = handover.payment, handover.payment.intent.order
    later = handover.previous_id is not None
    prior_refunds = (list(payment.refund_records.filter(created_at__lt=handover.created_at).order_by('created_at', 'pk'))
                     if later else [])
    if receipt_id:
        from .models import OrderPointsFundingAllocation
        if not OrderPointsFundingAllocation.objects.filter(redemption__order=order, receipt_id=receipt_id).exists():
            raise ValidationError('积分加购接替到账见证不属于本单')
    if (order.refund_records.filter(created_at__lte=run.created_at).exists()
            or payment.refund_records.filter(created_at__gte=handover.created_at,
                                             created_at__lte=run.created_at).exists()
            or payment.handovers.filter(status='ACCEPTED', accepted_at__lte=run.created_at).count() != (2 if later else 1)
            or handover.accepted_at >= run.created_at):
        raise ValidationError('积分加购接替的退款或时序尚未接入')
    payment_snapshot = payment_facts(payment, read_receipt_id=receipt_id)
    from .addon_handover import verify_handover
    from rest_framework.exceptions import ValidationError as RestValidationError
    try:
        verify_handover(payment, handover, as_of=run.created_at, receipt_id=receipt_id)
    except RestValidationError as exc:
        raise ValidationError('积分加购接替真实履约或确认凭证异常') from exc
    decisions = list(handover.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True),
                (handover.source_segment.provider_account_id, 'PROVIDER', True),
                (handover.target_segment.provider_account_id, 'PROVIDER', True)}
    if (set((account, role, accepted) for account, role, accepted, _ in decisions) != expected
            or any(not at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at
            or handover.source_segment.attribution.order_id != order.pk
            or handover.target_segment.attribution.order_id != order.pk):
        raise ValidationError('积分加购接替双方与负责人确认异常')
    shares = list(handover.shares.select_related('segment').order_by('position'))
    remaining = (payment.intent.amount - sum(row.amount for row in prior_refunds)
                 if later else handover.source_basis_before)
    if (not shares or sum(row.is_current for row in shares) != 1
            or sum(row.basis for row in shares) != remaining
            or any(row.segment.attribution.order_id != order.pk or row.income > row.basis for row in shares)):
        raise ValidationError('积分加购接替份额异常')
    prior = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
        accepted_at__lt=handover.accepted_at).select_related('payment__intent', 'source_segment__attribution',
            'target_segment__attribution').order_by('accepted_at', 'pk'))
    if OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
            accepted_at=handover.accepted_at).exclude(pk=handover.pk).exists():
        raise ValidationError('加购接替事件时序无法区分')
    prior_facts = []
    for item in prior:
        try:
            prior_shares = verify_handover(item.payment, item, as_of=handover.accepted_at, receipt_id=receipt_id)
        except RestValidationError as exc:
            raise ValidationError('此前加购接替凭证异常') from exc
        prior_decisions = list(item.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
        prior_facts.append([item.pk, item.payment_id, item.accepted_at.isoformat(), item.source_segment_id,
            item.target_segment_id, [[a, b, c, d.isoformat()] for a, b, c, d in prior_decisions],
            [[part.pk, part.segment_id, part.basis, part.income, part.commission_snapshot, part.is_current]
             for part in prior_shares]])
    event = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
        created_at__lt=handover.accepted_at).order_by('-pk').first()
    if not event:
        raise ValidationError('积分加购接替缺少真实转单')
    history = (_facts_current_read(order, receipt_id) if receipt_id
               else _facts_without_receipt_locked(order))
    sources = list(order.funding_allocations.order_by('pk'))
    paid = {kind: sum(item.amount for item in sources if item.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    main, lines = historical_plan(history, order.points_redemption, paid, event.pk)
    combined = combined_at(order, main, run.created_at, segment_shares=lines)
    if combined.get('additional_store_funding_units') != run.required_amount:
        raise ValidationError('积分加购接替整单资金不一致')
    result = {'version': 1, 'payment': payment_snapshot, 'handover_id': handover.pk,
        'transfer_id': event.pk, 'request_id': handover.request_id, 'request_hash': handover.request_hash,
        'source_segment_id': handover.source_segment_id, 'target_segment_id': handover.target_segment_id,
        'accepted_at': handover.accepted_at.isoformat(), 'completed_work': handover.completed_work,
        'remaining_work': handover.remaining_work,
        'decisions': [[a, b, c, d.isoformat()] for a, b, c, d in decisions],
        'shares': [[row.pk, row.segment_id, row.position, row.basis, row.income,
                    row.commission_snapshot, row.is_current] for row in shares], 'funding': combined}
    if prior_refunds:
        from .points_addon_handover_refund_facts import verify_refunds_before_second_handover
        result['prior_refunds'] = verify_refunds_before_second_handover(prior_refunds, handover.previous,
            before=event.created_at, receipt_id=receipt_id)
    elif not later and order.addon_refunds.filter(created_at__lt=handover.accepted_at).exists():
        from .points_transfer_handover_prior_refunds import facts as prior_refund_facts
        result['prior_refunds'] = prior_refund_facts(payment, handover, receipt_id=receipt_id)
    prestart = OrderPrestartHandoff.objects.filter(order=order, created_at__lt=handover.accepted_at).order_by('-pk').first()
    if prestart:
        from .points_prestart_release import capture
        from .points_transfer_refund_access import _prestart_slot_counts
        if prestart.created_at >= payment.buyer_transaction.created_at:
            raise ValidationError('改派与加购付款时序异常')
        result['prestart'] = capture(order, prestart)
        result['prestart_slot_counts'] = {str(slot): count for slot, count in
                                         _prestart_slot_counts(order, prestart).items()}
    if prior_facts:
        result['prior_handovers'] = prior_facts
    if later:
        result.update(version=2, previous_handover_id=handover.previous_id)
    return result


def postmain_facts(run, receipt_id):
    """Rebuild a handover after the verified main refund and a later transfer."""
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from users.receipt_usage_ledger import _locked_receipt
    from .addon_handover import verify_handover
    from .models import OrderPointsFundingAllocation, PointsNegotiationPosting, PointsPostRefundTransferRevision
    from .points_addon_receipt_usage import payment_facts
    from .refunded_addon_revision import verify as verify_revision
    from rest_framework.exceptions import ValidationError as RestValidationError
    if receipt_id:
        _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    handover, snapshot = run.handover, run.refunded_main_snapshot
    if not handover or not isinstance(snapshot, dict) or not handover.accepted_at:
        raise ValidationError('主单退款后的接替资金依据缺失')
    payment, order = handover.payment, handover.payment.intent.order
    if not receipt_id:
        from .points_transfer_refund_evidence import _facts_without_receipt_locked
        _facts_without_receipt_locked(order)
    posting = PointsNegotiationPosting.objects.filter(pk=snapshot.get('posting_id'),
        points_refund__redemption__order=order).first()
    version = PointsPostRefundTransferRevision.objects.filter(pk=snapshot.get('transfer_revision_id'),
                                                               order=order).first()
    if (not posting or not version or version.created_at >= handover.accepted_at
            or handover.accepted_at >= run.created_at or run.refund_id or run.payment_id
            or order.refund_records.filter(created_at__lte=handover.accepted_at).count() != 1):
        raise ValidationError('主单退款后接替的事件顺序异常')
    try:
        shares = verify_handover(payment, handover, as_of=run.created_at, receipt_id=receipt_id)
    except RestValidationError as exc:
        raise ValidationError('主单退款后的接替履约及确认异常') from exc
    sources = {row.pk: row for row in OrderPointsFundingAllocation.objects.filter(
        pk__in=[item[0] for item in run.allocation_snapshot])}
    witness = next((row for row in sources.values() if row.receipt_id == receipt_id), None)
    if receipt_id and not witness:
        # A receipt allocated after this handover is a valid current-read
        # witness, but was never part of the handover's frozen allocation.
        from django.db.models import Q
        witness = OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=order) | Q(service_order=order),
            receipt_id=receipt_id).order_by('pk').first()
    if receipt_id and not witness:
        raise ValidationError('主单退款后的接替到账见证缺失')
    net = []
    for item in run.allocation_snapshot:
        if not isinstance(item, list) or len(item) != 6 or item[0] not in sources:
            raise ValidationError('主单退款后的接替分配快照异常')
        used = item[2] + sum(row[1] for group in item[3:6] for row in group)
        if used > item[1]:
            raise ValidationError('主单退款后的接替释放超额')
        net.append([item[0], item[1] - used, sources[item[0]].receipt_id])
    try:
        verified = verify_revision(run, net, receipt_local=bool(receipt_id), witness_allocation=witness)
    except RestValidationError as exc:
        raise ValidationError('主单退款后的接替资金版本异常') from exc
    history = verified.get('addon_history')
    matching = [row for row in history if row.get('payment_id') == payment.pk] if isinstance(history, list) else []
    if len(matching) != 1 or matching[0].get('handover_id') != handover.pk:
        raise ValidationError('接替事件与整单资金版本不一致')
    decisions = list(handover.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    return {'version': 1, 'payment': payment_facts(payment, read_receipt_id=receipt_id),
            'posting_id': posting.pk, 'transfer_revision_id': version.pk, 'handover_id': handover.pk,
            'accepted_at': handover.accepted_at.isoformat(), 'funding': verified,
            'decisions': [[account, role, accepted, at.isoformat()] for account, role, accepted, at in decisions],
            'shares': [[row.pk, row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current]
                       for row in shares]}
