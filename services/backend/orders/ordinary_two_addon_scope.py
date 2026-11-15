"""Ordinary multi-purchase scope with restricted add-on refund history."""
from django.conf import settings


def eligible(order, providers, intents, *, allow_unpaid=False, proposal=None, allow_open=False):
    from .models import (NegotiationExecution, OrderAddonHandover, OrderAddonPayment,
                         OrderPointsFundingAllocation)
    if (getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is not True
            or getattr(settings, 'ORDINARY_SERVICE_FUNDING_ENABLED', False) is not True
            or len(providers) not in (1, 2) or order.required_provider_count != len(providers)
            or len(intents) < 2):
        return False
    accounts = {row.provider_account_id for row in providers}
    if (len(accounts) != len(providers) or None in accounts
            or any(intent.provider_id not in accounts
           or intent.segment.provider_account_id != intent.provider_id
           or intent.segment.attribution.order_id != order.pk for intent in intents)):
        return False
    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order).select_related('receipt'))
    main_refunds = list(order.refund_records.order_by('created_at', 'pk')[:2])
    if any(row.redemption_id is not None or row.receipt.usage_version is None for row in allocations):
        return False
    payments = []
    for intent in intents:
        rows = list(OrderAddonPayment.objects.filter(intent=intent).order_by('pk')[:2])
        if len(rows) > 1:
            return False
        if not rows:
            if (proposal is not None or (main_refunds and not allow_unpaid)
                    or (not (allow_unpaid or allow_open) and order.aftersales.filter(status='OPEN').exists())):
                return False
            continue
        payment = rows[0]
        from .ordinary_refunded_team_handover import permits as team_handover_permits
        from .ordinary_team_pre_main_handover import permits as pre_main_handover_permits
        if (payment.inviter_rate or payment.inviter_income
                or (OrderAddonHandover.objects.filter(payment=payment).exists()
                    and not (team_handover_permits(order) or pre_main_handover_permits(order)))):
            return False
        payments.append(payment)
    if not payments:
        return False
    addon_refunds = [(payment, item) for payment in payments
                     for item in payment.refund_records.select_related('proposal').all()]
    for payment, refund in addon_refunds:
        if (refund.proposal.status != 'ACCEPTED' or not 0 < refund.amount <= payment.intent.amount):
            return False
    executions = list(NegotiationExecution.objects.filter(proposal__case__order=order)
                      .select_related('proposal').order_by('pk')[:2])
    def multiple_pre_supported(rows):
        if len(rows) <= 1:
            return True
        for payment, refund in rows:
            people = list(refund.proposal.participants.all())
            handover = payment.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at).select_related(
                'source_segment', 'target_segment').first()
            valid_accounts = ({handover.source_segment.provider_account_id, handover.target_segment.provider_account_id}
                              if handover else {payment.intent.provider_id})
            if (not people or len(people) > 2 or not {row.account_id for row in people}.issubset(valid_accounts)
                    or (refund.addon_pending_deduction + refund.main_pending_deduction
                        + refund.wallet_deduction + refund.deposit_deduction != refund.amount + refund.rounding_surplus)
                    or any(person.deposit_deduction and not person.wallet_deduction
                        and not refund.participant_zero_wallet_evidence.filter(participant=person).exists()
                        and not (len(people) == 1 and hasattr(refund, 'zero_wallet_evidence')) for person in people)
                    or order.providers.filter(settled_at__lte=refund.created_at).exists()):
                return False
        return True
    if not main_refunds:
        if executions or not multiple_pre_supported(addon_refunds):
            return False
        if proposal is not None:
            affected = proposal.affected_accounts
            return bool(len(payments) == len(intents)
                        and 0 < proposal.refund_units < order.amount
                        and isinstance(affected, list) and affected
                        and all(type(account) is int for account in affected)
                        and len(affected) == len(set(affected)) and set(affected).issubset(accounts))
        return allow_open or not order.aftersales.filter(status='OPEN').exists()
    if (len(main_refunds) != 1 or (len(payments) != len(intents) and not allow_unpaid)
            or not 0 < main_refunds[0].amount < order.amount
            or len(executions) != 1 or executions[0].refund_id != main_refunds[0].pk
            or not executions[0].termination_processed_at
            or (not allow_open and order.aftersales.filter(status='OPEN').exists())):
        return False
    if len(payments) != len(intents):
        paid_intent_ids = {payment.intent_id for payment in payments}
        if (not allow_unpaid or order.status != order.Status.IN_SERVICE or order.settled_at
                or order.providers.filter(settled_at__isnull=False).exists()
                or order.aftersales.filter(status='OPEN').exists()
                or any(intent.pk not in paid_intent_ids and not order.providers.filter(
                    provider_account_id=intent.provider_id, released_at__isnull=True).exists()
                    for intent in intents)):
            return False
    if (not multiple_pre_supported([(payment, refund) for payment, refund in addon_refunds
                                    if refund.created_at < main_refunds[0].created_at])
            or any(refund.created_at == main_refunds[0].created_at for _, refund in addon_refunds)):
        return False
    for payment, refund in addon_refunds:
        if refund.created_at < main_refunds[0].created_at:
            continue
        people = list(refund.proposal.participants.all())
        handover = payment.handovers.filter(status='ACCEPTED',
                                            accepted_at__lt=refund.created_at).select_related('target_segment').first()
        responsible = ({handover.source_segment.provider_account_id, handover.target_segment.provider_account_id}
                       if handover else {payment.intent.provider_id})
        if (refund.created_at == main_refunds[0].created_at or not people or len(people) > 2
                or not {row.account_id for row in people}.issubset(responsible)
                or (refund.addon_pending_deduction + refund.main_pending_deduction
                    + refund.wallet_deduction + refund.deposit_deduction != refund.amount + refund.rounding_surplus)
                or any(person.deposit_deduction and not person.wallet_deduction
                    and not refund.participant_zero_wallet_evidence.filter(participant=person).exists()
                    and not (len(people) == 1 and hasattr(refund, 'zero_wallet_evidence'))
                    for person in people)
                or order.providers.filter(settled_at__lte=refund.created_at).exists()):
            return False
    affected = executions[0].proposal.affected_accounts
    return bool(isinstance(affected, list) and affected
                and all(type(account) is int for account in affected)
                and len(affected) == len(set(affected)) and set(affected).issubset(accounts))
