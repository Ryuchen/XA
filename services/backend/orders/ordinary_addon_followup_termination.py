"""Isolated atomic refund, store funding rebalance and case closure."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .points_checkout_test import require_isolated_checkout


def zero_enabled():
    from .ordinary_followup_capability import stage_enabled
    return bool(stage_enabled('ORDINARY_ADDON_FOLLOWUP_ZERO_INTERNAL_TESTING')
                and getattr(settings, 'NEGOTIATION_ZERO_EXECUTION_ENABLED', False))


def endpoint_enabled(order):
    """Expose execution only when the complete isolated settlement chain is on."""
    from .ordinary_addon_followup_preview import applies
    from .ordinary_followup_capability import production_enabled
    if production_enabled():
        from .points_checkout_test import ordinary_settlement_enabled
        return bool(applies(order) and ordinary_settlement_enabled()
                    and getattr(settings, 'REFUND_DEPOSIT_DEFICIT_ENABLED', False)
                    and getattr(settings, 'REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED', False))
    switches = ('ORDINARY_ADDON_FOLLOWUP_ENDPOINT_INTERNAL_TESTING',
        'ORDINARY_ADDON_FOLLOWUP_PREVIEW_INTERNAL_TESTING', 'ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING',
        'ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING', 'ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING',
        'ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING', 'ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING',
        'ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING', 'ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING',
        'ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING', 'ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING',
        'NEGOTIATION_PROPOSAL_ENABLED', 'NEGOTIATION_REFUND_EXECUTION_ENABLED', 'ADDON_SETTLEMENT_INTERNAL_TESTING',
        'REFUND_DEPOSIT_DEFICIT_ENABLED', 'REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED')
    if not all(getattr(settings, key, False) for key in switches) or not applies(order):
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    from .points_checkout_test import ordinary_settlement_enabled
    return ordinary_settlement_enabled()


def endpoint_available(case):
    """Advertise only when the existing settlement reservation reconciles."""
    if case.status != 'OPEN' or not endpoint_enabled(case.order):
        return False
    from .ordinary_addon_followup_preview import preview_case_locked
    from .settlement import SettlementError
    try:
        with transaction.atomic():
            preview_case_locked(case.pk, refund_units=0)
    except (SettlementError, ValidationError, ValueError):
        return False
    return True


@transaction.atomic
def execute_and_close(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .ordinary_followup_capability import require_access, stage_enabled
    require_access()
    if not stage_enabled('ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING'):
        raise ValidationError('带加购的追加售后结案尚未开放')
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    from .negotiation_proposals import _lock_case
    from .financial_lock import lock_order_money
    from .ordinary_addon_followup_posting import post_locked, verify_posting_locked
    from .ordinary_addon_followup_release import record_locked
    from .negotiation_followup_accounting import FOLLOWUP_MARKER, marked_followup_id, verify_followup_termination
    from .models import NegotiationExecution, OrderParticipationSegment
    order, case = _lock_case(case_id)
    lock_order_money()
    link = post_locked(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator, request_id=request_id)
    current = NegotiationExecution.objects.select_for_update().get(pk=link.execution_id)
    if current.termination_processed_at:
        verify_posting_locked(link)
        verify_followup_termination(link)
        record_locked(link)
        link.execution = current
        return link
    if (marked_followup_id(order) is not None or order.status != order.Status.COMPLETED
            or not order.completed_at or case.status != 'OPEN' or not current.accepted_at
            or current.accepted_at > timezone.now()):
        raise ValidationError('追加售后结案状态或时间待核对')
    shares = list(current.shares.select_related('assignment'))
    active = list(order.providers.select_for_update().filter(released_at__isnull=True))
    if (len(shares) != 1 or len(active) != 1 or active[0].pk != shares[0].assignment_id
            or active[0].settled_at):
        raise ValidationError('受影响陪玩或结算状态已变化')
    assignment = active[0]
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, provider_account_id=assignment.provider_account_id,
        released_at__isnull=True).order_by('pk'))
    if len(segments) != 1 or not segments[0].started_at or segments[0].started_at > order.completed_at:
        raise ValidationError('陪玩履约片段待核对')
    segments[0].released_at = order.completed_at
    segments[0].save(update_fields=['released_at'])
    assignment.released_at = order.completed_at
    assignment.save(update_fields=['released_at'])
    order.funding_terms = {**order.funding_terms, FOLLOWUP_MARKER: {'link_id': link.pk}}
    order.save(update_fields=['funding_terms'])
    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    amount = current.refund.amount if current.refund_id else 0
    case.status = ('RESOLVED' if not amount else 'REFUNDED'
                   if link.prior_refund_units + amount == order.amount else 'PARTIALLY_REFUNDED')
    case.operator, case.resolution, case.resolved_at = operator, current.proposal.explanation, timezone.now()
    append_history(case)
    from wallet.income_priority_evidence import attach_closure
    attach_closure(case)
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    current.termination_processed_at, current.completed_by_negotiation = timezone.now(), False
    current.save(update_fields=['termination_processed_at', 'completed_by_negotiation'])
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales', object_id=str(case.pk),
        request_body={'proposal_id': proposal_id, 'execution_id': current.pk, 'link_id': link.pk,
            'previous_execution_id': link.previous_id, 'refund_units': amount,
            'client_request_id': request_id}, status_code=200)
    verify_posting_locked(link)
    verify_followup_termination(link)
    # The receipt-local proof needs the actual case closure and audit. All
    # of them, the refund and the release remain in this same transaction.
    record_locked(link)
    from .services import refresh_escort_status
    refresh_escort_status(provider_ids=[assignment.provider_id], account_ids=[assignment.provider_account_id],
                          exclude_order_ids=[order.pk])
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    from wallet.models import Wallet
    from wallet.notifier import notify_wallet_update
    share = shares[0]
    wallet_ids = {pk for pk in (current.customer_transaction.wallet_id if current.customer_transaction_id else None,
                                share.wallet_transaction.wallet_id if share.wallet_transaction_id else None) if pk}
    for wallet in Wallet.objects.filter(pk__in=wallet_ids):
        transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
    link.execution = current
    return link
