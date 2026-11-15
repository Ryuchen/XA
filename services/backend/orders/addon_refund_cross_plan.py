"""Freeze cross-payment pending-handover deductions before consent."""
from collections import defaultdict

from rest_framework.exceptions import ValidationError


def capture(order, payment, people):
    from .addon_handover import current_responsibilities
    from .models import OrderAddonPayment, OrderAddonSettlement, OrderSegmentAddonSettlement
    from .points_transfer_followup_addon_version import refund_preflight_active
    from .points_followup_handover_invalidation import permits_locked
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    if ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True):
        return _ordinary_capture(order, payment, people)
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.count() >= 3:
        from .points_transfer_refund_access import permits
        if not permits(order, phase='refund'):
            raise ValidationError('第三轮后加购退款须在本单资金事务中确认')
        return _ordinary_capture(order, payment, people, points_third=True)
    from .points_transfer_addon_liability import first_refund_active
    first_refund = first_refund_active(order, payment.pk)
    if not refund_preflight_active(order, payment) and not first_refund:
        return None
    addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                  .select_related('intent').order_by('pk'))
    addons.sort(key=lambda row: (row.pk != payment.pk, row.pk))
    responsibilities = {item.pk: current_responsibilities(item, lock=True, with_share_id=True)[1] for item in addons}
    items = []
    for person in people:
        account_id = person['account_id'] if isinstance(person, dict) else person.account_id
        outstanding = person['burden'] if isinstance(person, dict) else person.burden
        for addon in addons:
            grouped = defaultdict(list)
            for share in responsibilities[addon.pk]:
                if share['provider_id'] == account_id:
                    grouped[share['segment_id']].append(share)
            for segment_id, shares in sorted(grouped.items()):
                if (OrderAddonSettlement.objects.filter(payment=addon, segment_id=segment_id).exists()
                        or OrderSegmentAddonSettlement.objects.filter(payment=addon, segment_id=segment_id).exists()):
                    continue
                amount = min(outstanding, sum(share['income'] for share in shares))
                if not amount:
                    continue
                pending = addon.handovers.select_for_update().filter(status='PENDING').first()
                if pending and addon.pk != payment.pk:
                    if (pending.kind != 'STANDARD' or pending.source_segment_id != segment_id
                            or pending.source_segment.provider_account_id != account_id
                            or not permits_locked(order, pending, payment)):
                        raise ValidationError('跨付款待确认接替责任不能核验')
                    if not first_refund:
                        from .points_transfer_followup_handover_scope import source_locked
                        source_locked(order, addon, pending.target_segment.provider_account_id,
                                      pending_handover_id=pending.pk)
                    ordered = sorted(shares, key=lambda row: (not row['is_current'], row.get('share_id') or 0))
                    if not ordered or not ordered[0]['is_current'] or ordered[0]['income'] <= 0:
                        raise ValidationError('待接替当前份额没有可承担收益')
                    remaining, chosen = amount, []
                    for share in ordered:
                        take = min(remaining, share['income'])
                        if take:
                            chosen.append([share.get('share_id'), share['income'], take])
                            remaining -= take
                        if not remaining:
                            break
                    if remaining:
                        raise ValidationError('跨付款待结收益不足')
                    items.append({'account_id': account_id, 'payment_id': addon.pk,
                                  'handover_id': pending.pk, 'segment_id': segment_id,
                                  'amount': amount, 'shares': chosen})
                outstanding -= amount
                if not outstanding:
                    break
            if not outstanding:
                break
    return {'version': 1, 'items': items}


def _ordinary_capture(order, payment, people, *, points_third=False):
    """Freeze the same payment/segment order used by the refund transaction."""
    if points_third:
        from .points_third_addon_responsibilities import current as current_responsibilities
    else:
        from .addon_handover import current_responsibilities
    from .models import OrderAddonPayment, OrderAddonSettlement, OrderSegmentAddonSettlement
    addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                  .select_related('intent').order_by('pk'))
    addons.sort(key=lambda item: (item.pk != payment.pk, item.pk))
    responsibilities = {item.pk: current_responsibilities(item, lock=True, with_share_id=True)[1] for item in addons}
    items, remaining = [], []
    for person in people:
        account = person['account_id'] if isinstance(person, dict) else person.account_id
        outstanding = person['burden'] if isinstance(person, dict) else person.burden
        for addon in addons:
            grouped = defaultdict(list)
            for share in responsibilities[addon.pk]:
                if share['provider_id'] == account:
                    grouped[share['segment_id']].append(share)
            for segment, shares in sorted(grouped.items()):
                if (OrderAddonSettlement.objects.filter(payment=addon, segment_id=segment).exists()
                        or OrderSegmentAddonSettlement.objects.filter(payment=addon, segment_id=segment).exists()):
                    continue
                income = sum(row['income'] for row in shares)
                amount = min(outstanding, income)
                if amount:
                    left, chosen = amount, []
                    for share in shares:
                        take = min(left, share['income'])
                        if take:
                            chosen.append([share.get('share_id'), share['income'], take])
                            left -= take
                        if not left:
                            break
                    items.append({'account_id': account, 'payment_id': addon.pk, 'segment_id': segment,
                                  'income': income, 'amount': amount, 'shares': chosen})
                    outstanding -= amount
                if not outstanding:
                    break
            if not outstanding:
                break
        remaining.append({'account_id': account, 'amount': outstanding})
    return {'version': 'POINTS_THIRD_V1' if points_third else 'ORDINARY_TRANSFER_V1',
            'items': items, 'remaining': remaining}


def require_current(proposal, people):
    frozen = proposal.cross_pending_plan
    if frozen is None:
        return
    actual = capture(proposal.payment.intent.order, proposal.payment, people)
    if actual != frozen:
        raise ValidationError('跨付款待结收益或接替方案已变化，请重新确认退款方案')


def require_ordinary_execution(proposal, plans):
    frozen = proposal.cross_pending_plan
    if not isinstance(frozen, dict) or frozen.get('version') not in ('ORDINARY_TRANSFER_V1', 'POINTS_THIRD_V1'):
        return
    expected = [(item['account_id'], item['payment_id'], item['segment_id'], item['amount'])
                for item in frozen['items']]
    actual = [(person.account_id, addon.pk, segment_id, amount)
              for person, _, _, deductions, _, _, _ in plans
              for addon, segment_id, amount in deductions]
    if actual != expected:
        raise ValidationError('实际逐付款收益承担与确认方案不一致，请重新确认')


def invalidate_changed_ordinary(order, *, changed_payments, changed_accounts, event_kind, event_id,
                                actor, legacy_actor=None):
    """Invalidate only proposals whose frozen obligations actually changed."""
    from console.models import AdminAuditLog
    from .addon_refund import _participants, _source_plan
    from .models import OrderAddonRefundProposal
    candidates = list(OrderAddonRefundProposal.objects.select_for_update().filter(
        payment__intent__order=order, status__in=('PENDING', 'ACCEPTED'), refund__isnull=True)
        .select_related('payment__intent', 'case').order_by('pk'))
    for proposal in candidates:
        people = list(proposal.participants.order_by('account_id').values(
            'account_id', 'basis', 'burden', 'excess_burden', 'share_plan'))
        if proposal.payment_id not in changed_payments and not {
                item['account_id'] for item in people}.intersection(changed_accounts):
            continue
        frozen = proposal.cross_pending_plan
        if not isinstance(frozen, dict) or frozen.get('version') != 'ORDINARY_TRANSFER_V1':
            raise ValidationError('受影响退款方案缺少已确认的逐付款承担，请先处理')
        try:
            same = (proposal.source_plan == _source_plan(proposal.payment, proposal.amount)
                    and people == _participants(proposal.payment, proposal.case, proposal.amount,
                                                [item['account_id'] for item in people])
                    and frozen == _ordinary_capture(order, proposal.payment, people))
        except ValidationError:
            same = False
        if same:
            continue
        proposal.status = 'SUPERSEDED'
        proposal.save(update_fields=['status'])
        AdminAuditLog.objects.create(operator=legacy_actor, operator_account=actor,
            operator_name=(actor.nickname or actor.username)[:150], method='POST',
            path=f'/api/admin/orders/{order.pk}/addons/refund-proposals/{proposal.pk}/invalidate/',
            resource='addon-refund-proposal', object_id=str(proposal.pk),
            request_body={'order_id': order.pk, 'proposal_id': proposal.pk,
                          'event_kind': event_kind, 'event_id': event_id}, status_code=200)
        from site_messages.addon_refund_proposal_notice import refresh_delivered
        refresh_delivered(proposal)


def invalidate_changed_third(order, refund, *, actor, legacy_actor=None):
    """Invalidate only pending schemes whose real confirmed terms changed."""
    from console.models import AdminAuditLog
    from .addon_refund import _participants, _source_plan
    from .models import OrderAddonRefundProposal
    candidates = (OrderAddonRefundProposal.objects.select_for_update().filter(
        payment__intent__order=order, status__in=('PENDING', 'ACCEPTED'), refund__isnull=True)
        .select_related('payment__intent', 'case').order_by('pk'))
    for proposal in candidates:
        people = list(proposal.participants.order_by('account_id').values(
            'account_id', 'basis', 'burden', 'excess_burden', 'share_plan'))
        try:
            same = (proposal.source_plan == _source_plan(proposal.payment, proposal.amount)
                    and people == _participants(proposal.payment, proposal.case, proposal.amount,
                                                [item['account_id'] for item in people])
                    and proposal.cross_pending_plan == capture(order, proposal.payment, people))
        except ValidationError:
            same = False
        if same:
            continue
        proposal.status = 'SUPERSEDED'
        proposal.save(update_fields=['status'])
        AdminAuditLog.objects.create(operator=legacy_actor, operator_account=actor,
            operator_name=(actor.nickname or actor.username)[:150], method='POST',
            path=f'/api/admin/orders/{order.pk}/addons/refund-proposals/{proposal.pk}/invalidate/',
            resource='addon-refund-proposal', object_id=str(proposal.pk),
            request_body={'order_id': order.pk, 'proposal_id': proposal.pk,
                          'event_kind': 'ADDON_REFUND', 'event_id': refund.pk}, status_code=200)
        from site_messages.addon_refund_proposal_notice import refresh_delivered
        refresh_delivered(proposal)


def verify_posted(refund):
    """Check that every agreed cross deduction really invalidated its scheme."""
    from .models import PointsHandoverRefundInvalidation
    from .points_followup_handover_invalidation import capture as capture_invalidation, digest
    frozen = refund.proposal.cross_pending_plan
    if frozen is None:
        return
    if isinstance(frozen, dict) and frozen.get('version') == 'POINTS_THIRD_V1':
        expected = [(item['account_id'], item['payment_id'], item['segment_id'], item['amount'])
                    for item in frozen.get('items', [])]
        actual = list(refund.earning_deductions.order_by('participant__account_id', 'pk')
                      .values_list('participant__account_id', 'payment_id', 'segment_id', 'amount'))
        if actual != expected:
            raise ValidationError('第三轮后实际跨付款承担与确认方案不一致')
        from .addon_refund_share_allocation import verify
        verify(refund)
        return
    if not isinstance(frozen, dict) or frozen.get('version') != 1 or not isinstance(frozen.get('items'), list):
        raise ValidationError('跨付款已确认计划异常')
    for item in frozen['items']:
        if not isinstance(item, dict):
            raise ValidationError('跨付款已确认计划异常')
        records = list(PointsHandoverRefundInvalidation.objects.filter(refund=refund,
            handover_id=item.get('handover_id'), handover__payment_id=item.get('payment_id'))
            .select_related('handover__payment__intent', 'refund__payment__intent'))
        if len(records) != 1:
            raise ValidationError('实际跨付款扣减缺少待确认方案失效记录')
        record = records[0]
        actual = capture_invalidation(record.handover, refund)
        if record.snapshot != actual or record.evidence_hash != digest(actual):
            raise ValidationError('跨付款接替失效事实不一致')
        deductions = refund.earning_deductions.filter(payment_id=item.get('payment_id'),
            participant__account_id=item.get('account_id'), segment_id=item.get('segment_id'))
        if sum(deductions.values_list('amount', flat=True)) != item.get('amount'):
            raise ValidationError('跨付款实际扣减与已确认计划不一致')
