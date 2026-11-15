"""Narrow production scope for one ordinary add-on and verified refunds."""


def eligible(order, *, allow_unpaid=False, proposal=None, allow_open=False, payment_intent_id=None):
    from django.conf import settings
    from .points_checkout_test import ordinary_production_enabled
    from .points_service_payment import needs_integrated_processing
    from .models import (NegotiationExecution, OrderAddonHandover, OrderAddonPayment,
                         OrderOriginalShareTransfer, OrderPlatformSubsidy, OrderPrestartHandoff, OrderStatusLog)
    if (not ordinary_production_enabled() or not order or order.is_gift_snapshot or order.source_order_id
            or not order.customer_account_id or order.funding_terms is None or not order.funding_allocations.exists()
            or needs_integrated_processing(order) or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or order.status_logs.filter(action=OrderStatusLog.Action.TRANSFER).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return False
    if getattr(settings, 'ORDINARY_ZERO_POST_ADDON_MYSQL_ENABLED', False):
        from .negotiation_zero import marked_execution_id
        if marked_execution_id(order):
            from .ordinary_zero_post_addon import eligible as zero_addon_eligible
            return zero_addon_eligible(order, allow_unpaid=allow_unpaid, proposal=proposal, allow_open=allow_open,
                                       payment_intent_id=payment_intent_id)
    prestart = OrderPrestartHandoff.objects.filter(order=order).exists()
    if prestart:
        from .ordinary_prestart_addon import addon_eligible
        return addon_eligible(order, allow_unpaid=allow_unpaid, proposal=proposal, allow_open=allow_open,
                              payment_intent_id=payment_intent_id)
    elif order.providers.filter(released_at__isnull=False).exists():
        # A negotiated termination closes the affected seat; it must not make
        # that seat's already-earned main/add-on settlement unreachable.
        # Verify the original participants at proposal time and require the
        # later closures to have the corresponding refund evidence.
        executions = list(NegotiationExecution.objects.filter(
            proposal__case__order=order, refund__isnull=False,
            termination_processed_at__isnull=False).select_related('proposal')[:2])
        from .ordinary_untransferred_scope import supports as original_seats_supported
        if (len(executions) != 1 or not executions[0].proposal.created_at
                or not original_seats_supported(order, boundary=executions[0].proposal.created_at)):
            return False
        providers = list(order.providers.order_by('pk'))
    else:
        providers = list(order.providers.filter(released_at__isnull=True).order_by('pk'))
    intents_query = order.addon_intents.select_related('segment')
    if allow_unpaid and payment_intent_id is not None:
        from django.db.models import Q
        intents_query = intents_query.filter(Q(payment__isnull=False) | Q(pk=payment_intent_id))
        if not order.addon_intents.filter(pk=payment_intent_id).exists():
            return False
    elif not allow_unpaid:
        # A draft does not alter the paid order or an existing add-on's
        # refund/settlement. Payment preparation still examines its target.
        intents_query = intents_query.filter(payment__isnull=False)
    intents = list(intents_query.order_by('pk'))
    if len(intents) >= 2:
        from .ordinary_two_addon_scope import eligible as two_addon_eligible
        return two_addon_eligible(order, providers, intents, allow_unpaid=allow_unpaid,
                                  proposal=proposal, allow_open=allow_open)
    if (len(providers) not in (1, 2) or order.required_provider_count != len(providers)
            or len({row.provider_account_id for row in providers}) != len(providers)
            or any(not row.provider_account_id for row in providers) or len(intents) != 1):
        return False
    intent = intents[0]
    if (intent.provider_id not in {row.provider_account_id for row in providers}
            or intent.segment.provider_account_id != intent.provider_id
            or intent.segment.attribution.order_id != order.pk):
        return False
    payments = list(OrderAddonPayment.objects.filter(intent=intent).order_by('pk'))
    if len(payments) != 1:
        if allow_unpaid and not payments and order.refund_records.count() == 1:
            # The payment writer rebalances verified store allocations in the
            # same transaction. Keep normal selection and actor checks.
            executions = list(NegotiationExecution.objects.filter(
                proposal__case__order=order, refund__isnull=False,
                termination_processed_at__isnull=False).select_related('refund')[:2])
            return bool(order.status == order.Status.IN_SERVICE and not order.settled_at
                and not order.aftersales.filter(status='OPEN').exists()
                and len(executions) == 1 and executions[0].refund.order_id == order.pk
                and not order.providers.filter(settled_at__isnull=False).exists()
                and order.providers.filter(provider_account_id=intent.provider_id,
                                           released_at__isnull=True).exists())
        return bool(allow_unpaid and not payments and not order.aftersales.filter(status='OPEN').exists()
                    and not order.refund_records.exists() and not NegotiationExecution.objects.filter(proposal__case__order=order).exists())
    payment = payments[0]
    addon_refunds = list(payment.refund_records.select_related('proposal').order_by('created_at', 'pk'))
    main_refunds = list(order.refund_records.order_by('created_at', 'pk')[:2])
    from .ordinary_refunded_team_handover import permits as team_handover_permits
    from .ordinary_team_pre_main_handover import permits as pre_main_handover_permits
    has_handover = OrderAddonHandover.objects.filter(payment=payment).exists()
    multiple_post_main = bool(len(addon_refunds) > 1 and len(main_refunds) == 1
        and all(item.created_at > main_refunds[0].created_at for item in addon_refunds)
        and len({item.created_at for item in addon_refunds}) == len(addon_refunds)
        and sum(item.amount for item in addon_refunds) <= intent.amount)
    multiple_before_or_after = bool(len(addon_refunds) > 1
        and (not main_refunds or addon_refunds[0].created_at < main_refunds[0].created_at)
        and len({item.created_at for item in addon_refunds}) == len(addon_refunds)
        and sum(item.amount for item in addon_refunds) <= intent.amount)
    if (payment.inviter_rate != 0 or payment.inviter_income != 0
            or (has_handover and not (team_handover_permits(order) or pre_main_handover_permits(order)))
            or (len(addon_refunds) > 1 and not (multiple_post_main or multiple_before_or_after))
            or any(not 0 < item.amount <= intent.amount or item.proposal.status != 'ACCEPTED'
                   for item in addon_refunds)):
        return False
    refunds = main_refunds
    if (addon_refunds and len(providers) == 2 and (proposal is not None or refunds)
            and (not refunds or addon_refunds[0].created_at <= refunds[0].created_at)):
        prior = addon_refunds[0]
        external_verified = team_handover_permits(order) if refunds else False
        if (not refunds and proposal is not None and (prior.wallet_deduction or prior.deposit_deduction)):
            try:
                from .ordinary_multi_prior_addon_facts import verify as verify_prior
                verify_prior(order, proposal.created_at)
                external_verified = True
            except Exception as exc:
                from django.core.exceptions import ValidationError as DjangoValidationError
                from rest_framework.exceptions import ValidationError as RestValidationError
                from .points_funding_plan import PointsFundingPlanError
                if not isinstance(exc, (DjangoValidationError, RestValidationError, PointsFundingPlanError, ValueError)):
                    raise
        if (prior.wallet_deduction or prior.deposit_deduction) and not external_verified:
            return False
    if addon_refunds and proposal is not None:
        affected = proposal.affected_accounts
        if refunds or not isinstance(affected, list) or len(affected) != 1:
            return False
        if has_handover and pre_main_handover_permits(order):
            if affected[0] not in {row.provider_account_id for row in providers}:
                return False
        elif affected != [intent.provider_id]:
            if (len(providers) != 2 or affected[0] not in {row.provider_account_id for row in providers}
                    or addon_refunds[0].amount != intent.amount
                    or any(term.get('additional_units', 0) for term in getattr(proposal, 'provider_terms', []))):
                return False
    if not refunds:
        if NegotiationExecution.objects.filter(proposal__case__order=order).exists():
            return False
        if proposal is not None:
            valid_accounts = {row.provider_account_id for row in providers}
            return bool(0 < proposal.refund_units < order.amount and isinstance(proposal.affected_accounts, list)
                        and len(proposal.affected_accounts) == 1
                        and proposal.affected_accounts[0] in valid_accounts)
        return allow_open or not order.aftersales.filter(status='OPEN').exists()
    if (len(refunds) != 1 or not 0 < refunds[0].amount < order.amount
            or (not allow_open and order.aftersales.filter(status='OPEN').exists())):
        return False
    executions = list(NegotiationExecution.objects.filter(proposal__case__order=order).select_related('proposal')[:2])
    if len(executions) != 1 or executions[0].refund_id != refunds[0].pk or not executions[0].termination_processed_at:
        return False
    affected = executions[0].proposal.affected_accounts
    if (not isinstance(affected, list) or len(affected) != 1
            or affected[0] not in {row.provider_account_id for row in providers}):
        return False
    if addon_refunds and addon_refunds[0].created_at <= refunds[0].created_at:
        if addon_refunds[0].created_at == refunds[0].created_at:
            return False
        if affected != [intent.provider_id] and (len(providers) != 2
                or addon_refunds[0].amount != intent.amount
                or executions[0].shares.filter(wallet_deduction__gt=0).exists()
                or executions[0].shares.filter(deposit_deduction__gt=0).exists()):
            return False
    return True

