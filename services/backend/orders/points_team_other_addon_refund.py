"""Current-read facts for another add-on after two main refunds.

Callers must hold the order/money locks or a receipt-local READ COMMITTED
witness. Each debit is reconstructed from the actual payment and segment.
"""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def continuing_purchase(order, payment):
    """A real add-on paid for the still-active provider after refund one."""
    from .models import PointsNegotiationPosting, OrderProvider
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                    .order_by('created_at', 'pk'))
    if not postings or len(postings) > 2 or not payment or payment.intent.order_id != order.pk:
        return False
    posting, segment, at = postings[0], payment.intent.segment, payment.buyer_transaction.created_at
    return bool(at and posting.termination_in_service and posting.termination_processed_at
        and at > posting.termination_processed_at and segment.attribution.order_id == order.pk
        and segment.provider_account_id == payment.intent.provider_id and segment.started_at
        and segment.started_at <= at and (not segment.released_at or segment.released_at > at)
        and OrderProvider.objects.filter(order=order, provider_account_id=segment.provider_account_id)
            .exclude(settled_at__lte=at).exists())


def applies(refund):
    from .models import OrderAddonFundingReleaseRun, OrderOriginalShareTransfer, OrderPrestartHandoff, PointsNegotiationPosting
    order, payment = refund.order, refund.payment
    if (order.required_provider_count != 2 or order.is_gift_snapshot or order.source_order_id
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                          created_at__lte=refund.created_at).exists()
            or payment.handovers.filter(status='ACCEPTED', accepted_at__lte=refund.created_at).exists()):
        return False
    team = list(OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
        points_handover_snapshot__kind='SAME_TEAM_V1', created_at__lt=refund.created_at)
        .select_related('handover').order_by('handover__accepted_at', 'pk'))
    from .points_team_addon_handover_facts import has_ordered_funding_chain
    mains = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        created_at__lt=refund.created_at).order_by('created_at', 'pk'))
    segment = payment.intent.segment
    payment_at = payment.buyer_transaction.created_at
    continuing = bool(len(mains) == 1 and continuing_purchase(order, payment))
    continuing_existing = False
    if len(mains) == 1 and payment_at < mains[0].created_at:
        participants = mains[0].participants
        continuing_existing = bool(team and not mains[0].shares.filter(account_id=payment.intent.provider_id).exists()
            and isinstance(participants, list)
            and sum(isinstance(row, dict) and row.get('account_id') == payment.intent.provider_id
                    and row.get('segment_id') == segment.pk for row in participants) == 1
            and order.providers.filter(provider_account_id=payment.intent.provider_id).exists()
            and not order.providers.filter(provider_account_id=payment.intent.provider_id,
                                           settled_at__lte=refund.created_at).exists())
    one_main = bool(len(mains) == 1 and mains[0].termination_in_service and (
        (payment_at < mains[0].created_at and
         (mains[0].shares.filter(account_id=payment.intent.provider_id).exists()
          or continuing_existing)) or continuing))
    main_scope = (not mains or one_main or (len(mains) == 2 and mains[0].created_at < mains[1].created_at
        and mains[1].proposal.points_terms.prior_refund_id == mains[0].points_refund_id
        and (payment_at < mains[0].created_at or
             (continuing_purchase(order, payment) and payment_at < mains[1].created_at))))
    return bool(payment.pk not in {run.handover.payment_id for run in team}
                and has_ordered_funding_chain(team)
                and main_scope and (team or not mains or one_main or
                                    (len(mains) == 2 and continuing_purchase(order, payment))))


def facts(refund, *, receipt_id=None):
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .addon_payment import verify_receipt
    from .addon_refund import _allocate_segment_burden
    from .models import OrderAddonRefund
    from engagement.funding_plan import RefundablePart, plan_refund, FundingPlanError
    _require_current_unlocked_reads()
    order, payment, proposal = refund.order, refund.payment, refund.proposal
    if not applies(refund) or proposal.payment_id != payment.pk or proposal.status != 'ACCEPTED' or proposal.amount != refund.amount:
        raise ValidationError('其他加购退款不在已接通的资金范围')
    try:
        verify_receipt(payment)
    except RestValidationError as exc:
        raise ValidationError('其他加购付款凭证异常') from exc
    earlier = list(OrderAddonRefund.objects.filter(order=order, created_at__lt=refund.created_at)
                   .select_related('proposal').order_by('created_at', 'pk'))
    if (len({item.created_at for item in earlier}) != len(earlier)
            or OrderAddonRefund.objects.filter(order=order, created_at=refund.created_at).exclude(pk=refund.pk).exists()):
        raise ValidationError('加购退款事件顺序无法区分')
    people = list(proposal.participants.select_related('provider_transaction').order_by('pk'))
    decisions = list(proposal.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    account = payment.intent.provider_id
    if (len(people) != 1 or people[0].account_id != account or len(decisions) != 2
            or {(a, role, accepted) for a, role, accepted, _ in decisions} != {
                (payment.intent.buyer_id, 'BOSS', True), (account, 'PROVIDER', True)}
            or not proposal.accepted_at or any(not at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != proposal.accepted_at
            or proposal.accepted_at > refund.created_at):
        raise ValidationError('其他加购退款确认凭证异常')
    person = people[0]
    own = [item for item in earlier if item.payment_id == payment.pk]
    basis = payment.intent.amount - sum(item.amount for item in own)
    prior_rounding = sum(entry['burden'] - entry['refund_basis'] for item in own
        for participant in item.proposal.participants.all() for entry in participant.share_plan
        if participant.account_id == account and entry['segment_id'] == payment.intent.segment_id)
    used = list(payment.refund_earning_deductions.filter(refund__created_at__lt=refund.created_at)
                .select_related('participant', 'segment__attribution'))
    if any(item.segment_id != payment.intent.segment_id or item.participant.account_id != account
           or item.segment.attribution.order_id != order.pk for item in used):
        raise ValidationError('其他加购的历史收益扣减归属异常')
    income = payment.provider_income - sum(item.amount for item in used)
    burden = ((refund.amount + 9) // 10) * 10
    if not 0 < refund.amount <= basis or income < 0:
        raise ValidationError('其他加购退款份额或历史收益异常')
    entries = [{'segment_id': payment.intent.segment_id, 'basis': basis, 'income': income,
                'refund_basis': refund.amount, 'burden_capacity': max(0, min(basis, basis - prior_rounding))}]
    excess = _allocate_segment_burden(entries, burden, [payment.intent.segment_id])
    amounts = [person.addon_pending_deduction, person.main_pending_deduction,
               person.wallet_deduction, person.deposit_deduction]
    if (person.basis != basis or person.burden != burden or person.excess_burden != excess
            or person.share_plan != entries or any(type(value) is not int or value < 0 for value in amounts)
            or sum(amounts) != burden or amounts != [refund.addon_pending_deduction,
                refund.main_pending_deduction, refund.wallet_deduction, refund.deposit_deduction]
            or refund.rounding_surplus != burden - refund.amount):
        raise ValidationError('其他加购退款的本人承担凭证异常')
    from .models import (OrderAddonPayment, OrderAddonFundingReleaseRun, OrderAddonSettlement,
                         OrderSegmentAddonSettlement, PointsNegotiationPosting)
    from .points_negotiation_execution import verify_participants
    from .addon_main_segment_deduction import verified_deductions
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=refund.created_at).select_related('intent', 'buyer_transaction').order_by('pk'))
    payments.sort(key=lambda row: (row.pk != payment.pk, row.pk))
    teams = list(OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
        points_handover_snapshot__kind='SAME_TEAM_V1', created_at__lt=refund.created_at)
        .select_related('handover').order_by('handover__accepted_at', 'pk'))
    from .points_team_addon_handover_facts import has_ordered_funding_chain
    if (not has_ordered_funding_chain(teams)
            or any(not item.handover.accepted_at or item.handover.accepted_at >= refund.created_at for item in teams)):
        raise ValidationError('其他加购退款的同单接替凭证缺失')
    from .points_team_postmain_handover_facts import verify_event_current
    for team in teams:
        verify_event_current(team, receipt_id=receipt_id)
    team_by_payment = {item.handover.payment_id: item for item in teams}
    candidates = []
    for addon in payments:
        try:
            verify_receipt(addon)
        except RestValidationError as exc:
            raise ValidationError('其他加购收益付款凭证异常') from exc
        team = team_by_payment.get(addon.pk)
        if team:
            rows = [(row.segment_id, row.segment.provider_account_id, row.income)
                    for row in team.handover.shares.select_related('segment').order_by('position')]
            cutoff = team.handover.accepted_at
        else:
            if addon.handovers.filter(status='ACCEPTED', accepted_at__lte=refund.created_at).exists():
                raise ValidationError('其他已接替加购收益尚未接入')
            rows = [(addon.intent.segment_id, addon.intent.provider_id, addon.provider_income)]
            cutoff = None
        valid_owners = {(segment_id, owner) for segment_id, owner, _ in rows}
        prior_rows = addon.refund_earning_deductions.filter(refund__created_at__lt=refund.created_at)
        if cutoff:
            prior_rows = prior_rows.filter(refund__created_at__gt=cutoff)
        if any(row.refund.order_id != order.pk or row.segment.attribution.order_id != order.pk
               or (row.segment_id, row.participant.account_id) not in valid_owners
               or row.segment.provider_account_id != row.participant.account_id or row.amount <= 0
               for row in prior_rows.select_related('refund', 'segment__attribution', 'participant')):
            raise ValidationError('跨加购历史扣减付款或履约归属异常')
        income_by_segment = {}
        for segment_id, owner, income in rows:
            key = (segment_id, owner)
            income_by_segment[key] = income_by_segment.get(key, 0) + income
        for (segment_id, owner), original_income in sorted(income_by_segment.items()):
            if owner != account:
                continue
            earlier_deductions = addon.refund_earning_deductions.filter(
                segment_id=segment_id, participant__account_id=account,
                refund__created_at__lt=refund.created_at)
            if cutoff:
                earlier_deductions = earlier_deductions.filter(refund__created_at__gt=cutoff)
            available = original_income - sum(earlier_deductions.values_list('amount', flat=True))
            if available < 0:
                raise ValidationError('跨加购历史扣减超过本人收益')
            if (OrderAddonSettlement.objects.filter(payment=addon, segment_id=segment_id,
                    settled_at__lte=refund.created_at).exists()
                    or OrderSegmentAddonSettlement.objects.filter(payment=addon, segment_id=segment_id,
                    settled_at__lte=refund.created_at).exists()):
                available = 0
            candidates.append((addon.pk, segment_id, available))
    remaining = burden
    expected_deductions = []
    for payment_id, segment_id, available in candidates:
        take = min(remaining, available)
        if take:
            expected_deductions.append((payment_id, segment_id, take))
            remaining -= take
    deductions = list(person.earning_deductions.select_related('payment__intent', 'segment__attribution').order_by('pk'))
    if ([(row.payment_id, row.segment_id, row.amount) for row in deductions] != expected_deductions
            or any(row.refund_id != refund.pk or row.payment.intent.order_id != order.pk
                   or row.segment.attribution.order_id != order.pk
                   or row.segment.provider_account_id != account for row in deductions)
            or refund.earning_deductions.count() != len(deductions)
            or sum(row.amount for row in deductions) != person.addon_pending_deduction):
        raise ValidationError('其他加购退款的逐付款逐段收益承担异常')
    mains = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        created_at__lt=refund.created_at).order_by('created_at', 'pk'))
    try:
        for posting in mains:
            verify_participants(posting)
        if mains:
            owners = [(posting, share) for posting in mains for share in posting.shares.filter(account_id=account)]
            if len(owners) > 1 or (not owners and len(mains) != 1):
                raise ValidationError('主单退款的陪玩收益归属异常')
            posting = owners[0][0] if owners else mains[0]
            share = owners[0][1] if owners else None
            snap = [row for row in posting.participants if row.get('account_id') == account]
            if len(snap) != 1:
                raise ValidationError('主单退款的履约段缺失')
            segment_id = snap[0]['segment_id']
            used_before = verified_deductions(order, before=posting.created_at).get(segment_id, 0)
            base_income = share.retained_income if share else snap[0]['income_at_entry'] - used_before
        else:
            from .points_service_participation import service_snapshots
            matches = [row for row in service_snapshots(order) if row.segment.provider_account_id == account]
            if len(matches) != 1:
                raise ValidationError('主单陪玩原履约段缺失')
            segment_id, base_income, used_before = matches[0].segment_id, matches[0].income_at_entry, 0
        used_now = verified_deductions(order, before=refund.created_at).get(segment_id, 0)
        available_main = base_income - (used_now - used_before)
        if available_main < 0 or used_now < used_before:
            raise ValidationError('两次主单退款后本人可用收益异常')
        expected_main = min(remaining, available_main)
        main_rows = list(person.main_segment_deductions.select_related('segment__attribution'))
        if (len(main_rows) != (1 if expected_main else 0)
                or person.main_pending_deduction != expected_main or sum(row.amount for row in main_rows) != expected_main
                or any(row.segment_id != segment_id or row.segment.attribution.order_id != order.pk
                       or row.segment.provider_account_id != account for row in main_rows)):
            raise ValidationError('其他加购退款的主单段收益承担异常')
        remaining -= expected_main
    except RestValidationError as exc:
        raise ValidationError('其他加购退款的主单收益凭证异常') from exc
    wallet_tx = person.provider_transaction
    if person.wallet_deduction:
        if (not wallet_tx or refund.provider_transaction_id != wallet_tx.pk
                or wallet_tx.wallet.account_id != account or wallet_tx.tx_type != 'REFUND'
                or wallet_tx.status != 'SUCCESS' or wallet_tx.amount != -person.wallet_deduction
                or wallet_tx.balance_after != wallet_tx.balance_before + wallet_tx.amount
                or wallet_tx.operator_account_id != refund.operator_id or wallet_tx.operator_id != refund.legacy_operator_id
                or wallet_tx.reversal_of_id or wallet_tx.reversals.exists()
                or person.wallet_deduction != min(remaining, wallet_tx.balance_before)):
            raise ValidationError('其他加购退款的陪玩钱包流水异常')
    elif wallet_tx or refund.provider_transaction_id:
        raise ValidationError('其他加购退款存在多余钱包流水')
    remaining -= person.wallet_deduction
    deficits = list(refund.deposit_deficits.select_related('profile', 'source_store_transaction__wallet__user').order_by('pk'))
    if remaining != person.deposit_deduction or sum(row.amount for row in deficits) != remaining:
        raise ValidationError('其他加购退款押金与承担金额不守恒')
    from django.conf import settings
    for item in deficits:
        source = item.source_store_transaction
        if (item.account_id != account or item.profile.account_id != account or item.addon_refund_id != refund.pk
                or item.source_kind != 'DIRECT' or item.direct_key != f'addon:{refund.pk}:{item.profile_id}'
                or not source or source.order_id != order.pk or source.tx_type != 'DEPOSIT_INCOME'
                or source.status != 'SUCCESS' or source.amount != -item.amount
                or not source.wallet.user_id or source.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or source.balance_after != source.balance_before + source.amount or source.reversal_of_id
                or source.reversals.exists() or source.operator_account_id != refund.operator_id
                or source.operator_id != refund.legacy_operator_id):
            raise ValidationError('其他加购退款押金真实流水异常')
    allocations = list(payment.allocations.order_by('pk'))
    prior_sources = {}
    for item in own:
        for row in item.proposal.source_plan:
            prior_sources[row['allocation_id']] = prior_sources.get(row['allocation_id'], 0) + row['amount_raw']
    try:
        planned = plan_refund([RefundablePart(row.pk, row.kind, row.amount, prior_sources.get(row.pk, 0))
                               for row in allocations], refund.amount)
    except (FundingPlanError, KeyError, TypeError) as exc:
        raise ValidationError('其他加购原付款来源待核对') from exc
    expected = [{'allocation_id': row.pk, 'kind': row.kind, 'amount_raw': planned[row.pk]}
                for row in allocations if planned[row.pk]]
    if proposal.source_plan != expected:
        raise ValidationError('其他加购退款来源与原支付顺序不一致')
    tx = refund.buyer_transaction
    if (not tx or tx.wallet.account_id != order.customer_account_id or tx.tx_type != 'REFUND'
            or tx.status != 'SUCCESS' or tx.amount != refund.amount
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()
            or tx.operator_account_id != refund.operator_id or tx.operator_id != refund.legacy_operator_id):
        raise ValidationError('其他加购老板退款流水异常')
    result = {'kind': ('TEAM_OTHER_PREMAIN_V1' if not mains else
                       'TEAM_OTHER_AFTER_MAIN_V1' if len(mains) == 1 else 'TEAM_OTHER_AFTER_FOLLOWUP_V1'),
            'refund_id': refund.pk, 'payment_id': payment.pk,
            'at': refund.created_at.isoformat(), 'segment_id': payment.intent.segment_id,
            'account_id': account, 'participant': [person.pk, basis, income, burden, excess, entries],
            'decision': [[a, role, accepted, at.isoformat()] for a, role, accepted, at in decisions],
            'source_plan': expected, 'deductions': [[row.pk, row.payment_id, row.segment_id, row.amount]
                                                   for row in deductions],
            'main_deductions': [[row.pk, row.segment_id, row.amount] for row in main_rows],
            'burden_sources': amounts,
            'wallet_tx': [wallet_tx.pk, wallet_tx.wallet_id, wallet_tx.amount, wallet_tx.balance_before,
                          wallet_tx.balance_after] if wallet_tx else None,
            'deposit_sources': [[item.pk, item.profile_id, item.amount, item.source_store_transaction_id]
                                for item in deficits],
            'buyer_tx': [tx.pk, tx.wallet_id, tx.amount, tx.balance_before, tx.balance_after,
                         tx.operator_id, tx.operator_account_id]}
    if not mains:
        from .points_addon_receipt_usage import payment_facts
        from .points_funding_plan import PointsFundingPlan, PointsFundingPlanError
        from .addon_funding_history import combined_at
        paid = payment_facts(payment, read_receipt_id=receipt_id)
        try:
            main = PointsFundingPlan(**paid['main_plan'])
            shares = paid.get('main_shares') or [paid['share']]
            lines = [(row[1], row[2], row[3], row[6], row[7]) for row in shares]
            funding = combined_at(order, main, refund.created_at, segment_shares=lines,
                                  include_refund_id=refund.pk)
        except (KeyError, TypeError, IndexError, PointsFundingPlanError, RestValidationError) as exc:
            raise ValidationError('主单退款前其他加购资金历史待核对') from exc
        result['main_plan'] = paid['main_plan']
        result['required'] = funding['additional_store_funding_units']
        result['funding'] = funding
    return result
