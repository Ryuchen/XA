"""Replay one same-team refund's actual income, wallet and deposit priority."""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify(refund, person, *, receipt_id=None):
    from .addon_payment import verify_receipt
    from .addon_main_segment_deduction import verified_deductions
    from .models import (OrderAddonPayment, OrderAddonFundingReleaseRun, OrderAddonSettlement,
                         OrderSegmentAddonSettlement, PointsNegotiationPosting)
    from .points_negotiation_execution import verify_participants
    from .points_team_postmain_handover_facts import verify_event_current
    order, payment, account = refund.order, refund.payment, person.account_id
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        created_at__lt=refund.created_at).order_by('created_at', 'pk'))
    if len(postings) != 1:
        raise ValidationError('同单退款承担顺序的主单事件范围异常')
    posting = postings[0]
    try:
        verify_participants(posting)
    except RestValidationError as exc:
        raise ValidationError('主单退款陪玩履约快照异常') from exc
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=refund.created_at).select_related('intent', 'buyer_transaction').order_by('pk'))
    payments.sort(key=lambda item: (item.pk != payment.pk, item.pk))
    if not payments or payments[0].pk != payment.pk:
        raise ValidationError('退款的原加购付款不存在')
    remaining, expected = person.burden, []
    for addon in payments:
        try:
            verify_receipt(addon)
        except RestValidationError as exc:
            raise ValidationError('本人可用加购付款凭证异常') from exc
        team_runs = list(OrderAddonFundingReleaseRun.objects.filter(handover__payment=addon,
            points_handover_snapshot__kind='SAME_TEAM_V1', handover__accepted_at__lt=refund.created_at)
            .select_related('handover').order_by('handover__accepted_at', 'pk'))
        if team_runs:
            # The latest accepted version carries all earlier completed shares;
            # its event verifier recursively checks the prior funding chain.
            team = team_runs[-1]
            verify_event_current(team, receipt_id=receipt_id)
            rows = [(item.segment_id, item.segment.provider_account_id, item.income)
                    for item in team.handover.shares.select_related('segment').order_by('position')]
            cutoff = team.handover.accepted_at
        else:
            if addon.handovers.filter(status='ACCEPTED', accepted_at__lte=refund.created_at).exists():
                raise ValidationError('本人加购接替资金版本缺失')
            rows = [(addon.intent.segment_id, addon.intent.provider_id, addon.provider_income)]
            cutoff = None
        owners = {(segment, owner) for segment, owner, _ in rows}
        earlier = addon.refund_earning_deductions.filter(refund__created_at__lt=refund.created_at)
        if cutoff:
            earlier = earlier.filter(refund__created_at__gt=cutoff)
        earlier = list(earlier.select_related('refund', 'participant', 'segment__attribution'))
        if any(row.refund.order_id != order.pk or row.segment.attribution.order_id != order.pk
               or (row.segment_id, row.participant.account_id) not in owners
               or row.segment.provider_account_id != row.participant.account_id or row.amount <= 0
               for row in earlier):
            raise ValidationError('本人跨加购历史收益归属异常')
        # A provider can return to the same segment in successive accepted
        # shares. Deductions are stored by payment and segment, so combine the
        # actual share incomes before applying that segment's history once.
        income_by_segment = {}
        for segment_id, owner, income in rows:
            key = (segment_id, owner)
            income_by_segment[key] = income_by_segment.get(key, 0) + income
        for (segment_id, owner), income in sorted(income_by_segment.items()):
            if owner != account:
                continue
            used = sum(row.amount for row in earlier
                       if row.segment_id == segment_id and row.participant.account_id == account)
            available = income - used
            if available < 0:
                raise ValidationError('本人加购历史扣减超出收益')
            if (OrderAddonSettlement.objects.filter(payment=addon, segment_id=segment_id,
                    settled_at__lte=refund.created_at).exists()
                    or OrderSegmentAddonSettlement.objects.filter(payment=addon, segment_id=segment_id,
                    settled_at__lte=refund.created_at).exists()):
                available = 0
            take = min(remaining, available)
            if take:
                expected.append((addon.pk, segment_id, take))
                remaining -= take
    actual = list(person.earning_deductions.order_by('pk').values_list('payment_id', 'segment_id', 'amount'))
    if actual != expected or sum(row[2] for row in expected) != person.addon_pending_deduction:
        raise ValidationError('退款未按本人加购逐付款逐段收益承担')
    snapshots = [row for row in posting.participants if row.get('account_id') == account]
    if len(snapshots) != 1:
        raise ValidationError('主单退款历史缺少本人履约段')
    snapshot = snapshots[0]
    segment_id = snapshot['segment_id']
    share = posting.shares.filter(account_id=account).first()
    prior = verified_deductions(order, before=posting.created_at).get(segment_id, 0)
    now = verified_deductions(order, before=refund.created_at).get(segment_id, 0)
    base = share.retained_income if share else snapshot['income_at_entry'] - prior
    available = base - (now - prior)
    if now < prior or available < 0:
        raise ValidationError('主单退款后的本人剩余收益异常')
    expected_main = min(remaining, available)
    main_rows = list(person.main_segment_deductions.select_related('segment__attribution'))
    if (len(main_rows) != (1 if expected_main else 0) or person.main_pending_deduction != expected_main
            or sum(row.amount for row in main_rows) != expected_main
            or any(row.segment_id != segment_id or row.segment.attribution.order_id != order.pk
                   or row.segment.provider_account_id != account for row in main_rows)):
        raise ValidationError('退款的主单本人收益承担异常')
    remaining -= expected_main
    tx = person.provider_transaction
    if person.wallet_deduction:
        if not tx or person.wallet_deduction != min(remaining, tx.balance_before):
            raise ValidationError('退款钱包扣款未按剩余承担金额计算')
    elif tx:
        raise ValidationError('退款存在多余的钱包扣款')
    remaining -= person.wallet_deduction
    if remaining != person.deposit_deduction:
        raise ValidationError('退款钱包与押金承担不守恒')
