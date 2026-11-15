"""Controlled production access for ordinary funded follow-up refunds."""
from django.conf import settings
from stores.db import connection


def production_enabled():
    from .points_checkout_test import ordinary_production_enabled
    return bool(connection.vendor == 'mysql'
                and getattr(settings, 'ORDINARY_FOLLOWUP_MYSQL_ENABLED', False)
                and getattr(settings, 'NEGOTIATION_FOLLOWUP_ENABLED', False)
                and getattr(settings, 'NEGOTIATION_PROPOSAL_ENABLED', False)
                and getattr(settings, 'NEGOTIATION_REFUND_EXECUTION_ENABLED', False)
                and ordinary_production_enabled())


def stage_enabled(test_flag):
    return production_enabled() or getattr(settings, test_flag, False) is True


def require_access():
    if production_enabled():
        return
    from .points_checkout_test import require_isolated_checkout
    require_isolated_checkout()


def permits_funding(order):
    """Allow only a recorded ordinary follow-up into existing funding checks."""
    from stores.context import current_store
    from stores.db import transaction
    if not production_enabled() or not transaction.get_connection().in_atomic_block or current_store() is None or not order:
        return False
    from .models import NegotiationFollowupLink, OrderOrdinaryRefundAddonSettlementEvidence
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) or order.is_gift_snapshot or order.source_order_id:
        return False
    evidence = OrderOrdinaryRefundAddonSettlementEvidence.objects.filter(settlement__order=order).first()
    if not evidence:
        return False
    links = list(NegotiationFollowupLink.objects.filter(order=order, previous_id=evidence.execution_id,
        execution__proposal__status='ACCEPTED').select_related('execution__proposal')[:2])
    return (len(links) == 1 and isinstance(links[0].projected_ledger, dict)
            and links[0].projected_ledger.get('ordinary_addon_followup_version') == 1
            and links[0].execution.proposal.case.order_id == order.pk)
