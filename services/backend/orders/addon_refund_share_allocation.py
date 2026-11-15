"""Allocate posted refund amounts to the confirmed shares in force at that time."""
from django.core.exceptions import ValidationError


def build(payment, participants, deductions, *, before=None, exclude_refund_id=None):
    from .addon_handover import current_responsibilities
    payments = {payment.pk: payment}
    for _, addon, _, _ in deductions:
        payments[addon.pk] = addon
    available = {}
    for addon in payments.values():
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(addon.intent.order) and addon.intent.order.refund_records.count() >= 3:
            from .points_third_addon_responsibilities import current as read_responsibilities
        else:
            read_responsibilities = current_responsibilities
        handover, shares = read_responsibilities(addon, lock=before is None, before=before,
                                                  with_share_id=True, exclude_refund_id=exclude_refund_id)
        if handover:
            available[addon.pk] = shares
    allocated = {}
    frozen = participants[0].proposal.cross_pending_plan if participants else None
    planned = {}
    if isinstance(frozen, dict):
        if frozen.get('version') not in (1, 'ORDINARY_TRANSFER_V1', 'POINTS_THIRD_V1') or not isinstance(frozen.get('items'), list):
            raise ValidationError('已确认跨付款份额计划异常')
        for item in frozen['items']:
            if not isinstance(item, dict):
                raise ValidationError('已确认跨付款份额计划异常')
            key = (item.get('account_id'), item.get('payment_id'), item.get('segment_id'))
            if key in planned or not isinstance(item.get('shares'), list):
                raise ValidationError('已确认跨付款份额计划重复')
            planned[key] = item
    repeated_segments = {addon_id: {segment for segment in {row['segment_id'] for row in shares}
                                     if sum(row['segment_id'] == segment for row in shares) > 1}
                         for addon_id, shares in available.items()}

    def distribute(addon_id, person, segment_id, amount, field):
        shares = available.get(addon_id)
        if shares is None:
            return
        remaining = amount
        key_name = 'basis' if field == 'refund_basis' else 'income'
        frozen_item = planned.get((person.account_id, addon_id, segment_id)) if field == 'earning_deduction' else None
        if frozen_item:
            by_id = {row['share_id']: row for row in shares}
            try:
                shares = [by_id[part[0]] for part in frozen_item['shares']]
            except (KeyError, TypeError, IndexError) as exc:
                raise ValidationError('已确认跨付款份额不存在') from exc
        for share in shares:
            if share['provider_id'] != person.account_id or share['segment_id'] != segment_id:
                continue
            key = (addon_id, person.pk, share['share_id'])
            facts = allocated.setdefault(key, {'refund_basis': 0, 'earning_deduction': 0})
            capacity = share[key_name] - facts[field]
            if field == 'refund_basis' and segment_id in repeated_segments[addon_id]:
                # Preserve the income which remains on this exact share. The
                # agreed participant and total refund do not change.
                remaining_income = share['income'] - facts['earning_deduction']
                capacity = min(capacity, share['basis'] - facts['refund_basis'] - remaining_income)
            take = min(remaining, max(capacity, 0))
            facts[field] += take
            remaining -= take
            if not remaining:
                break
        if remaining:
            raise ValidationError('加购退款逐份额分配超出实际剩余份额')
        if frozen_item:
            actual = [[share['share_id'], share['income'],
                       allocated[(addon_id, person.pk, share['share_id'])]['earning_deduction']] for share in shares]
            if amount != frozen_item.get('amount') or actual != frozen_item['shares']:
                raise ValidationError('跨付款份额与已确认计划不一致')

    for person, addon, segment_id, amount in deductions:
        distribute(addon.pk, person, segment_id, amount, 'earning_deduction')
    for person in participants:
        for entry in person.share_plan:
            distribute(payment.pk, person, entry['segment_id'], entry['refund_basis'], 'refund_basis')
    for addon_id, shares in available.items():
        repeated = repeated_segments[addon_id]
        for share in shares:
            if share['segment_id'] not in repeated:
                continue
            facts = allocated.get((addon_id, next((person.pk for person in participants
                if person.account_id == share['provider_id']), None), share['share_id']),
                {'refund_basis': 0, 'earning_deduction': 0})
            if share['income'] - facts['earning_deduction'] > share['basis'] - facts['refund_basis']:
                raise ValidationError('退款后同一履约段的份额收益需要先核对')
    return [(addon_id, person_id, share_id, values['refund_basis'], values['earning_deduction'])
            for (addon_id, person_id, share_id), values in sorted(allocated.items())
            if values['refund_basis'] or values['earning_deduction']]


def verify(refund):
    participants = list(refund.proposal.participants.order_by('pk'))
    deductions = [(row.participant, row.payment, row.segment_id, row.amount)
                  for row in refund.earning_deductions.select_related('participant', 'payment').order_by('pk')]
    expected = build(refund.payment, participants, deductions, before=refund.created_at, exclude_refund_id=refund.pk)
    actual = list(refund.share_allocations.order_by('payment_id', 'participant_id', 'share_id')
                  .values_list('payment_id', 'participant_id', 'share_id', 'refund_basis', 'earning_deduction'))
    if actual != expected:
        raise ValidationError('加购退款逐份额分配凭证异常')
    return actual


def replay(payment, handover, rows, *, before=None, include_main_third=False):
    """Apply persisted allocations, checking the original segment-level postings."""
    from django.db.models import Q
    from .models import OrderAddonRefund
    events = OrderAddonRefund.objects.filter(
        Q(payment=payment) | Q(earning_deductions__payment=payment),
        created_at__gt=handover.accepted_at).distinct()
    if before is not None:
        events = events.filter(created_at__lt=before)
    timeline = [(event.created_at, 'refund', event)
                for event in events.select_related('proposal').order_by('created_at', 'pk')]
    if include_main_third:
        from .negotiation_models import PointsNegotiationAddonDeduction
        main = PointsNegotiationAddonDeduction.objects.filter(payment=payment,
            addon_share__handover=handover, share__posting__termination_processed_at__isnull=False,
            share__posting__created_at__gt=handover.accepted_at).select_related('share__posting', 'segment')
        if before is not None:
            main = main.filter(share__posting__created_at__lt=before)
        timeline.extend((row.share.posting.created_at, 'main', row) for row in main)
    times = {at for at, _, _ in timeline}
    # Several share rows in the same main posting are one event; a refund at
    # that exact timestamp cannot be safely ordered with it.
    if any({kind for at2, kind, _ in timeline if at2 == at} == {'main', 'refund'} for at in times):
        raise ValidationError('主单与加购退款事件时间无法区分')
    shares = {row['share_id']: row for row in rows}
    for _, kind, event in sorted(timeline, key=lambda item: (item[0], item[2].pk)):
        if kind == 'main':
            share = shares.get(event.addon_share_id)
            if (not share or event.share.posting.proposal.case.order_id != payment.intent.order_id
                    or event.segment_id != share['segment_id'] or event.share.account_id != share['provider_id']
                    or event.amount > share['income']):
                raise ValidationError('主单退款的加购份额承担异常')
            share['income'] -= event.amount
            continue
        expected = {}
        if event.payment_id == payment.pk:
            for person in event.proposal.participants.all():
                for part in person.share_plan:
                    key = (person.pk, part['segment_id'])
                    expected.setdefault(key, [0, 0])[0] += part['refund_basis']
        for deduction in event.earning_deductions.filter(payment=payment):
            key = (deduction.participant_id, deduction.segment_id)
            expected.setdefault(key, [0, 0])[1] += deduction.amount
        expected = {key: value for key, value in expected.items() if any(value)}
        actual = {}
        allocations = list(event.share_allocations.filter(payment=payment).select_related('share', 'participant'))
        for allocation in allocations:
            share = shares.get(allocation.share_id)
            if (not share or allocation.share.handover_id != handover.pk
                    or allocation.participant.proposal_id != event.proposal_id
                    or allocation.participant.account_id != share['provider_id']):
                raise ValidationError('加购退款逐份额归属异常')
            key = (allocation.participant_id, share['segment_id'])
            totals = actual.setdefault(key, [0, 0])
            totals[0] += allocation.refund_basis
            totals[1] += allocation.earning_deduction
            if allocation.refund_basis > share['basis'] or allocation.earning_deduction > share['income']:
                raise ValidationError('加购退款逐份额余额不足')
            share['basis'] -= allocation.refund_basis
            share['income'] -= allocation.earning_deduction
        if actual != expected:
            raise ValidationError('加购退款逐份额与实际退款扣减不一致')
