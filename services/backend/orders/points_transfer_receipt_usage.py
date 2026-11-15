"""Receipt-local current-read proof for a points transfer funding release."""
from dataclasses import asdict

from django.core.exceptions import ValidationError


def historical_plan(facts, redemption, paid, through):
    """Recompute immutable entry/transfer shares without locking an order."""
    from .points_funding_plan import plan_service_funding, PointsFundingPlanError
    from .settlement import ProviderShare
    transfers, segments = facts['transfers'], facts['segments']
    incoming = {event['next_segment_id']: event for event in transfers}
    outgoing = {event['previous_segment_id']: event for event in transfers}
    shares, lines = [], []
    for item in segments:
        arrived, left = incoming.get(item['id']), outgoing.get(item['id'])
        if arrived and arrived['id'] > through:
            continue
        ended = left is not None and left['id'] <= through
        if item['pre_service_released'] and not left:
            basis = 0
        else:
            basis = left['retained_basis'] if ended else item['incoming_basis']
        fee = ((item['fixed_rule_total'] * basis // facts['deal_amount']) if ended
               else 0 if item['pre_service_released'] else item['commission_fixed']) if item['commission_type'] == 'FIXED' else basis * item['commission_rate'] // 100
        shares.append(ProviderShare(basis, item['commission_type'], item['commission_rate'],
                                    fee if item['commission_type'] == 'FIXED' else 0, basis - fee))
        lines.append((item['id'], item['account_id'], basis, fee, basis - fee))
    try:
        plan = plan_service_funding(deal_units=redemption.deal_units,
            point_discount_units=redemption.discount_units, shares=shares,
            inviter_rate=facts['inviter_commission_rate'] if facts['inviter_id'] or facts['inviter_account_id'] else 0,
            principal_units=paid['PREPAID'], gift_units=paid['GIFT'], compensation_units=paid['COMPENSATION'])
    except (PointsFundingPlanError, TypeError, ValueError) as exc:
        raise ValidationError('积分转单历史收益重算异常') from exc
    return plan, lines


def facts(row):
    from users.receipt_usage_sources import _digest
    from .models import (OrderAddonPayment, OrderPointsFundingAllocation, OrderPointsTransferFundingRelease,
                         OrderPointsTransferFundingRevision)
    from .points_transfer_refund_evidence import _facts_current_read
    from .points_transfer_revision import _digest as revision_digest
    from .points_funding_allocation import plan_rebalance, PointsFundingAllocationError
    from .points_funding_plan import plan_service_funding, PointsFundingPlanError
    from .settlement import ProviderShare
    from engagement.funding_wallet import validate_order_funding_sources
    from rest_framework.exceptions import ValidationError as RestValidationError
    from types import SimpleNamespace

    revision, allocation = row.revision, row.allocation
    order = revision.order
    redemption = allocation.redemption
    if (not redemption or allocation.service_order_id or redemption.order_id != order.pk
            or order.is_gift_snapshot or order.source_order_id or not isinstance(revision.allocations, list)):
        raise ValidationError('积分转单到账释放来源异常')
    facts = _facts_current_read(order, getattr(row, 'read_receipt_id', allocation.receipt_id))
    transfers, segments = facts['transfers'], facts['segments']
    if any(item['pre_service_released'] for item in segments):
        from .models import OrderPrestartHandoff
        from .points_transfer_refund_access import _prestart_slot_counts
        handoff = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
        if not handoff:
            raise ValidationError('积分订单服务前改派凭证缺失')
        prestart_counts = _prestart_slot_counts(order, handoff)
    else:
        prestart_counts = {}
    by_id = {event['id']: event for event in transfers}
    last = by_id.get(revision.last_transfer_id)
    if not last or (revision.previous_transfer_id and revision.previous_transfer_id not in by_id):
        raise ValidationError('积分转单到账版本事件缺失')
    from django.utils.dateparse import parse_datetime
    boundary = parse_datetime(last['created_at'])
    if not boundary or revision.created_at < boundary:
        raise ValidationError('积分转单到账版本时间异常')
    if order.refund_records.filter(created_at__lte=boundary).exists():
        raise ValidationError('转单时主单退款到账版本尚未接入')
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=boundary).select_related('intent', 'buyer_transaction__wallet'))
    if (OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at=boundary).exists()
            or any(item.refund_records.filter(created_at=boundary).exists()
                   or item.handovers.filter(status='ACCEPTED', accepted_at__lte=boundary).exists()
                   for item in addons)):
        raise ValidationError('转单时加购来源或时序尚未接入')
    addon_events = None
    read_receipt_id = getattr(row, 'read_receipt_id', allocation.receipt_id)
    if addons:
        from .points_addon_receipt_usage import payment_facts, facts as addon_refund_facts
        for addon in addons:
            payment_facts(addon, read_receipt_id=read_receipt_id)
            for prior_refund in addon.refund_records.filter(created_at__lt=boundary).order_by('pk'):
                addon_refund_facts(prior_refund, read_receipt_id=read_receipt_id)
        if len(addons) > 1:
            from .points_addon_receipt_usage import _prior_addon_events
            addon_events = _prior_addon_events(order, boundary, read_receipt_id=read_receipt_id)
    original, spent = redemption.payment, redemption.ledger
    if (original.order_id != order.pk or original.wallet.account_id != order.customer_account_id
            or original.tx_type != 'PAY' or original.status != 'SUCCESS' or original.amount != -redemption.paid_units
            or original.reversal_of_id or original.reversals.exists()
            or original.balance_after != original.balance_before + original.amount
            or redemption.paid_units != order.amount or redemption.deal_units != redemption.paid_units + redemption.discount_units
            or spent.order_id != order.pk or spent.account_id != order.customer_account_id
            or spent.kind != 'ORDER_POINTS_REDEEM' or spent.amount != -redemption.points_hundredths
            or spent.event_key != f'order:{order.pk}:points-redemption'):
        raise ValidationError('积分转单原付款或积分流水异常')
    sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('积分转单原付款来源异常') from exc
    paid = {kind: sum(item.amount for item in sources if item.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    incoming = {event['next_segment_id']: event for event in transfers}
    outgoing = {event['previous_segment_id']: event for event in transfers}
    if len(incoming) != len(transfers) or len(outgoing) != len(transfers):
        raise ValidationError('积分转单历史关联重复')
    counts = dict(prestart_counts)
    segment_map = {item['id']: item for item in segments}
    for event in transfers:
        previous, following = segment_map.get(event['previous_segment_id']), segment_map.get(event['next_segment_id'])
        if (not previous or not following or previous['slot'] != following['slot']
                or event['basis_before'] != event['retained_basis'] + event['transferred_basis']
                or event['original_share_before'] != event['retained_share'] + event['transferred_share']):
            raise ValidationError('积分转单原份额不守恒')
        if event['id'] <= revision.last_transfer_id:
            counts[previous['slot']] = counts.get(previous['slot'], 0) + 1
    if any(count > 2 for count in counts.values()):
        raise ValidationError('积分转单超过席位次数上限')

    before_plan, before_lines = historical_plan(facts, redemption, paid, revision.previous_transfer_id)
    after_plan, after_lines = historical_plan(facts, redemption, paid, revision.last_transfer_id)
    if addons:
        from .addon_funding_history import combined_at
        before = combined_at(order, before_plan, boundary, segment_shares=before_lines)
        after = combined_at(order, after_plan, boundary, segment_shares=after_lines)
    else:
        before, after = asdict(before_plan), asdict(after_plan)
    if (before != revision.before_funding or after != revision.after_funding
            or revision.previous_transfer_id >= revision.last_transfer_id):
        raise ValidationError('积分转单到账资金快照异常')
    earlier_events = [event for event in transfers if event['id'] < revision.last_transfer_id
                      and parse_datetime(event['created_at']) < boundary]
    preceding = max((event['id'] for event in earlier_events), default=0)
    if preceding != revision.previous_transfer_id:
        raise ValidationError('积分转单到账版本前驱异常')
    for item in revision.allocations:
        if not isinstance(item, list) or len(item) != 4 or any(type(value) is not int for value in item):
            raise ValidationError('积分转单到账分配快照异常')
        allocation_id, receipt_id, amount, previous = item
        linked = OrderPointsFundingAllocation.objects.select_related('receipt').filter(pk=allocation_id, redemption=redemption).first()
        released = sum(OrderPointsTransferFundingRelease.objects.filter(allocation_id=allocation_id,
            revision__last_transfer_id__lt=revision.last_transfer_id).values_list('amount', flat=True))
        from .models import OrderAddonFundingRelease
        addon_rows = list(OrderAddonFundingRelease.objects.filter(allocation_id=allocation_id,
            run__created_at__lt=boundary).select_related('run', 'allocation__redemption').order_by('pk'))
        from users.receipt_usage_sources import _addon_release_facts
        for addon_row in addon_rows:
            addon_ids = {item.pk for item in addons}
            if not addon_row.run.prestart_handoff_id and (not addons or not (addon_row.run.payment_id in addon_ids
                    or (addon_row.run.refund_id and addon_row.run.refund.payment_id in addon_ids
                        and addon_row.run.refund.created_at < boundary))):
                raise ValidationError('积分转单此前加购释放来源异常')
            _addon_release_facts(addon_row, witness_receipt_id=read_receipt_id)
            released += addon_row.amount
        if (not linked or linked.receipt_id != receipt_id or linked.amount != amount
                or linked.created_at > boundary or previous != released):
            raise ValidationError('积分转单到账分配或前次释放异常')
        from uuid import UUID
        import json
        from stores.context import current_store
        from stores.models import PlatformPointsFundingOperation, PlatformPassRepaymentOperation
        from stores.platform_pass_repayment import _claimed_receipt, _sha, RepaymentError
        from users.models import PassRefundRepaymentAction
        from .models import OrderPointsFundingAction
        try:
            if not linked.request_key.startswith('points:') or len(linked.request_key) != 39:
                raise ValueError('allocation request')
            operation = PlatformPointsFundingOperation.objects.using('default').select_related('claim').get(
                pk=UUID(hex=linked.request_key[7:]), claim__store__slug=current_store().slug)
            action = OrderPointsFundingAction.objects.get(request_key=linked.request_key, allocation=linked)
            expected = {'receipt_id': receipt_id, 'allocation_id': allocation_id, 'order_id': order.pk, 'amount': amount}
            if (not operation.completed_at or operation.completed_at > boundary or operation.rejected_at
                    or operation.voided_at or operation.tenant_action_id != action.pk
                    or action.actor != str(operation.actor_id) or linked.allocated_by != str(operation.actor_id)
                    or action.result != expected or operation.claim.tenant_receipt_id != receipt_id
                    or operation.payload_digest != _sha(json.dumps(
                        [str(operation.claim_id), order.pk, amount], separators=(',', ':')))):
                raise ValueError('allocation proof')
            receipt = _claimed_receipt(operation.claim, current_store().slug, linked.receipt)
            verified = False
            for verify_op in PlatformPassRepaymentOperation.objects.using('default').filter(
                    claim=operation.claim, kind='VERIFY', completed_at__isnull=False,
                    rejected_at__isnull=True, voided_at__isnull=True, completed_at__lte=boundary):
                verify_action = PassRefundRepaymentAction.objects.filter(pk=verify_op.tenant_action_id,
                    request_key=f'platform:{verify_op.pk.hex}', kind='VERIFY', receipt=receipt,
                    actor=str(verify_op.actor_id), allocation__isnull=True).first()
                if verify_action and verify_action.result == {'receipt_id': receipt.pk,
                        'verified_by': receipt.verified_by,
                        'verified_at': receipt.verified_at.isoformat() if receipt.verified_at else None}:
                    verified = True
                    break
            if not verified:
                raise ValueError('receipt verification')
        except (ValueError, TypeError, PlatformPointsFundingOperation.DoesNotExist,
                OrderPointsFundingAction.DoesNotExist, RepaymentError) as exc:
            raise ValidationError('积分转单平台到账或分配授权异常') from exc
    from django.db.models import Q
    historical_empty = (row.pk == 0 and row.amount == 0 and revision.allocations == []
        and allocation.created_at > boundary and not OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=order) | Q(service_order=order), created_at__lte=boundary).exists())
    if (not any(item[0] == allocation.pk and item[1] == allocation.receipt_id for item in revision.allocations)
            and not historical_empty):
        raise ValidationError('积分转单释放未绑定当前到账')
    try:
        plan = plan_rebalance(allocations=[(item[0], item[2], item[3]) for item in revision.allocations],
                              required=after['additional_store_funding_units'])
    except (PointsFundingAllocationError, ValueError, TypeError) as exc:
        raise ValidationError('积分转单到账释放计划异常') from exc
    actual = list(revision.releases.order_by('-allocation_id').values_list('allocation_id', 'amount'))
    draft = SimpleNamespace(last_transfer_id=revision.last_transfer_id, previous_transfer_id=revision.previous_transfer_id,
                            allocations=revision.allocations, before_funding=before, after_funding=after)
    if (revision.evidence_hash != revision_digest(order.pk, draft)
            or actual != [(part.allocation_id, part.amount) for part in plan.release_parts]):
        raise ValidationError('积分转单到账释放凭证异常')
    return ('RELEASE', f'order:{allocation.pk}', f'points-transfer-release:{row.pk}', row.amount,
            _digest(['points-transfer-release', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                     revision.pk, revision.evidence_hash, original.pk, spent.pk]
                    + ([addon_events] if addon_events is not None else [])))
