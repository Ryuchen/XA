"""Ordinary add-on settlement after one pre-service seat replacement."""
from .models import OrderPrestartHandoff, OrderProvider, OrderSegmentFinancialSnapshot
from .settlement import compute_provider_income, split_settlement_bases


def supports(order):
    """Prove the original and current individual shares for every seat."""
    if not order or order.required_provider_count not in (1, 2) or order.refund_records.exists():
        return False
    count = order.required_provider_count
    handoffs = list(OrderPrestartHandoff.objects.filter(order=order).order_by('pk')[:2])
    if len(handoffs) != 1:
        return False
    handoff = handoffs[0]
    assignments = list(OrderProvider.objects.filter(order=order).order_by('pk'))
    if len(assignments) != count + 1:
        return False
    old = next((row for row in assignments if row.provider_account_id == handoff.old_provider_id), None)
    incoming = next((row for row in assignments if row.provider_account_id == handoff.new_provider_id), None)
    current = [row for row in assignments if not row.released_at]
    if (not old or not incoming or not old.released_at or old.settlement_base or old.provider_income
            or old.commission_fixed or old.settled_at or incoming.released_at or len(current) != count
            or len({row.provider_account_id for row in current}) != count
            or order.provider_account_id not in {row.provider_account_id for row in current}
            or not any(row.provider_account_id == order.provider_account_id and row.provider_id == order.provider_id
                       for row in current)):
        return False
    rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
                .select_related('segment', 'segment__attribution').order_by('pk'))
    if len(rows) != count + 1:
        return False
    prior = next((row for row in rows if row.segment.provider_account_id == old.provider_account_id), None)
    replacement = next((row for row in rows if row.segment.provider_account_id == incoming.provider_account_id), None)
    active = [row for row in rows if not row.segment.released_at]
    if (not prior or not replacement or not prior.segment.released_at or prior.segment.started_at
            or prior.retained_basis != 0 or replacement.segment.released_at
            or prior.segment.slot != replacement.segment.slot or len(active) != count
            or {row.segment.slot for row in active} != set(range(1, count + 1))
            or {row.segment.provider_account_id for row in active} != {row.provider_account_id for row in current}):
        return False
    bases = split_settlement_bases(order.amount, count)
    if prior.incoming_basis != bases[prior.segment.slot - 1]:
        return False
    from .delta_personal_commission import verify_segment_evidence
    if not verify_segment_evidence(prior.segment, prior):
        return False
    by_account = {row.provider_account_id: row for row in current}
    for snapshot in active:
        segment, assignment = snapshot.segment, by_account[snapshot.segment.provider_account_id]
        basis = bases[segment.slot - 1]
        if (not assignment.provider_id or snapshot.retained_basis is not None
                or snapshot.incoming_basis != basis or assignment.settlement_base != basis
                or snapshot.commission_type != assignment.commission_type
                or snapshot.commission_rate != assignment.commission_rate
                or snapshot.commission_fixed != assignment.commission_fixed
                or snapshot.game_commission_snapshot != assignment.game_commission_snapshot
                or snapshot.income_at_entry != assignment.provider_income
                or assignment.provider_income != compute_provider_income(basis, assignment.commission_type,
                    assignment.commission_rate, assignment.commission_fixed)
                or not verify_segment_evidence(segment, snapshot)):
            return False
    if sum(row.provider_income for row in current) != order.provider_income:
        return False
    fields = ('provider_account_id', 'settlement_base', 'provider_income', 'commission_type',
              'commission_rate', 'commission_fixed', 'game_commission_snapshot')
    previous, following = handoff.old_snapshot, handoff.new_snapshot
    if not isinstance(previous, dict) or not isinstance(following, dict):
        return False
    expected_old = {'provider_account_id': old.provider_account_id, 'settlement_base': prior.incoming_basis,
        'provider_income': prior.income_at_entry, 'commission_type': prior.commission_type,
        'commission_rate': prior.commission_rate, 'commission_fixed': prior.commission_fixed,
        'game_commission_snapshot': prior.game_commission_snapshot}
    return (all(previous.get(key) == expected_old[key] for key in fields)
            and all(following.get(key) == getattr(incoming, key) for key in fields))


def addon_eligible(order, *, allow_unpaid=False, proposal=None, allow_open=False, payment_intent_id=None):
    """Permit paid add-ons on either current seat, with no main refund."""
    from .models import NegotiationExecution, OrderAddonHandover, OrderAddonPayment
    if (proposal is not None or not supports(order)
            or NegotiationExecution.objects.filter(proposal__case__order=order).exists()
            or (not allow_open and order.aftersales.filter(status='OPEN').exists())):
        return False
    people = set(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
    intents_query = order.addon_intents.select_related('segment')
    if allow_unpaid and payment_intent_id is not None:
        from django.db.models import Q
        intents_query = intents_query.filter(Q(payment__isnull=False) | Q(pk=payment_intent_id))
        if not order.addon_intents.filter(pk=payment_intent_id).exists():
            return False
    elif not allow_unpaid:
        intents_query = intents_query.filter(payment__isnull=False)
    intents = list(intents_query.order_by('pk'))
    if not intents:
        return False
    paid_count = 0
    for intent in intents:
        if (intent.provider_id not in people or intent.segment.provider_account_id != intent.provider_id
                or intent.segment.attribution.order_id != order.pk or intent.segment.released_at):
            return False
        payments = list(OrderAddonPayment.objects.filter(intent=intent).order_by('pk')[:2])
        if len(payments) > 1:
            return False
        if not payments:
            continue
        paid_count += 1
        payment = payments[0]
        refunds = list(payment.refund_records.select_related('proposal').order_by('pk')[:2])
        if (payment.inviter_rate or payment.inviter_income
                or OrderAddonHandover.objects.filter(payment=payment).exists()
                or len(refunds) > 1 or any(not 0 < item.amount <= intent.amount
                    or item.proposal.status != 'ACCEPTED' for item in refunds)):
            return False
    return bool(paid_count or allow_unpaid)
