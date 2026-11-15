"""Atomic later ordinary negotiation, case closure and funding rebalance."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


@transaction.atomic
def execute_and_close(case_id, proposal_id, *, operator, legacy_operator, request_id):
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    from .negotiation_proposals import _lock_case
    from .financial_lock import lock_order_money
    from .models import NegotiationProposal, OrderParticipationSegment, OrderWorkStint, OrdinaryTransferFollowupRound
    order, case = _lock_case(case_id)
    lock_order_money()
    from .points_addon_receipt_usage import prelock as prelock_receipts
    prelock_receipts(order)
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    if not proposal:
        raise ValidationError('协商方案不存在')
    previous = OrdinaryTransferFollowupRound.objects.select_for_update().filter(
        proposal=proposal, order=order, execution__isnull=False).select_related('execution__refund').first()
    if previous:
        receipt = previous.execution
        key = receipt.refund.client_request_id if receipt.refund_id else receipt.zero_request_id
        if receipt.operator_id != operator.pk or key != request_id or not receipt.termination_processed_at:
            raise ValidationError('该方案已处理，请核对原退款记录')
        if receipt.refund_id:
            from .negotiation_execution import verify_execution
            from .negotiation_termination import verify_termination_evidence
        else:
            from .ordinary_transfer_followup_zero import verify_execution, verify_termination as verify_termination_evidence
        from .ordinary_transfer_followup_revision import verify_locked
        verify_execution(receipt)
        verify_termination_evidence(receipt)
        verify_locked(previous.settlement_revision)
        from .models import OrderAddonFundingReleaseRun
        run = OrderAddonFundingReleaseRun.objects.filter(negotiation=receipt).first()
        if run:
            from .addon_funding_release import verify_run
            verify_run(run)
            from .models import OrderPointsFundingAllocation
            from users.receipt_usage_sources import verify_allocations_locked
            ids = [item[0] for item in run.allocation_snapshot]
            receipt_ids = OrderPointsFundingAllocation.objects.filter(pk__in=ids,
                receipt__usage_version__isnull=False).order_by('receipt_id').values_list('receipt_id', flat=True).distinct()
            for receipt_id in receipt_ids:
                verify_allocations_locked(receipt_id)
        return receipt
    from .ordinary_transfer_followup_posting import post_locked
    receipt = post_locked(order, case, proposal, operator=operator, legacy_operator=legacy_operator,
                          request_id=request_id)
    round_record = OrdinaryTransferFollowupRound.objects.select_for_update().get(execution=receipt, order=order)
    from .ordinary_transfer_followup_revision import record_locked as preserve_manifest
    preserve_manifest(round_record)
    affected = set(proposal.affected_accounts)
    if order.status == order.Status.IN_SERVICE:
        assignments = list(order.providers.select_for_update().filter(released_at__isnull=True).order_by('pk'))
        selected = set(proposal.affected_segment_ids or [])
        active_segment_accounts = set(OrderParticipationSegment.objects.filter(
            attribution__order=order, pk__in=selected, released_at__isnull=True)
            .values_list('provider_account_id', flat=True))
        active = [item for item in assignments if item.provider_account_id in active_segment_accounts]
        if {item.provider_account_id for item in active} != active_segment_accounts or not active_segment_accounts.issubset(affected):
            raise ValidationError('受影响履约段的陪玩状态已变化')
        for assignment in active:
            segments = list(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, provider_account_id=assignment.provider_account_id,
                released_at__isnull=True).order_by('pk'))
            stints = list(OrderWorkStint.objects.select_for_update().filter(
                order=order, account_id=assignment.provider_account_id, released_at__isnull=True).order_by('pk'))
            if len(segments) != 1 or len(stints) != 1 or not segments[0].started_at or stints[0].started_at != segments[0].started_at:
                raise ValidationError('陪玩当前履约段待核对')
            segments[0].released_at = proposal.accepted_at
            segments[0].save(update_fields=['released_at'])
            stints[0].released_at = proposal.accepted_at
            stints[0].save(update_fields=['released_at'])
            assignment.released_at = proposal.accepted_at
            assignment.save(update_fields=['released_at'])
        remaining = [item for item in assignments if item.provider_account_id not in active_segment_accounts]
        from .services import ensure_fulfillment_reports, refresh_escort_status
        ensure_fulfillment_reports(order)
        if active:
            from .room_completion_review import invalidate_for_transfer_locked
            invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
        if remaining:
            if order.provider_account_id in active_segment_accounts:
                order.provider_id, order.provider_account_id = remaining[0].provider_id, remaining[0].provider_account_id
                order.provider_name_snapshot = remaining[0].provider_name_snapshot
                order.save(update_fields=['provider', 'provider_account', 'provider_name_snapshot', 'updated_at'])
            if active:
                from .state_machine import log_only
                log_only(order, action='REFUND', operator=legacy_operator, operator_account=operator,
                         reason='追加协商退款，受影响陪玩终止履约')
        else:
            from .state_machine import transition
            from .services import complete_fulfillment
            def finish(current):
                current.completed_at = proposal.accepted_at
                complete_fulfillment(current, operator=legacy_operator, operator_account=operator)
            order = transition(order.pk, order.Status.COMPLETED, operator=legacy_operator,
                operator_account=operator, action='COMPLETE', reason='双方确认协商终止', side_effect=finish,
                update_fields=['completed_at'], room_negotiation_execution_id=receipt.pk)
            receipt.completed_by_negotiation = True
        refresh_escort_status(provider_ids=[item.provider_id for item in active],
                              account_ids=[item.provider_account_id for item in active], exclude_order_ids=[order.pk])
    elif order.status != order.Status.COMPLETED:
        raise ValidationError('订单状态不支持追加协商结案')
    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    case.status = ('RESOLVED' if not receipt.refund_id else 'REFUNDED'
                   if order.payment_status == order.PaymentStatus.REFUNDED else 'PARTIALLY_REFUNDED')
    case.operator, case.resolution, case.resolved_at = operator, proposal.explanation, timezone.now()
    append_history(case)
    if getattr(settings, 'REFUND_DEPOSIT_DEFICIT_ENABLED', False):
        from wallet.income_priority_evidence import record_closure
        evidence = record_closure(case)
        if evidence:
            case.history[-1]['income_priority_closure_id'] = evidence.pk
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    receipt.termination_processed_at = timezone.now()
    receipt.save(update_fields=['termination_processed_at', 'completed_by_negotiation'])
    from console.models import AdminAuditLog
    audit_body = ({'proposal_id': proposal.pk, 'refund_id': receipt.refund_id,
                   'refund_units': receipt.refund.amount, 'client_request_id': request_id} if receipt.refund_id else
                  {'proposal_id': proposal.pk, 'execution_id': receipt.pk, 'refund_units': 0,
                   'client_request_id': request_id})
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales',
        object_id=str(case.pk), request_body=audit_body, status_code=200)
    if receipt.refund_id:
        from .negotiation_termination import verify_termination_evidence
    else:
        from .ordinary_transfer_followup_zero import verify_termination as verify_termination_evidence
    verify_termination_evidence(receipt)
    if receipt.refund_id:
        from .ordinary_transfer_followup_release import record_locked as release_funding
        release_funding(order, receipt)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    from wallet.models import Wallet
    from wallet.notifier import notify_wallet_update
    wallet_ids = {receipt.customer_transaction.wallet_id} if receipt.customer_transaction_id else set()
    wallet_ids.update(Wallet.objects.filter(account_id__in=proposal.affected_accounts).values_list('pk', flat=True))
    for wallet in Wallet.objects.filter(pk__in=wallet_ids):
        transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
    return receipt
