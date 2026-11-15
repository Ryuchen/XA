"""Zero-refund termination evidence, transactional execution and verification.

The preparation primitive is private: the staff entry point invokes it through
the feature-gated termination transaction, which also closes the case and
records the affected participants and audit evidence.
"""
from copy import copy

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from club_accounts.models import LegacyAccountMap
from .financial_lock import lock_order_money
from .models import NegotiationExecution, NegotiationExecutionShare, OrderRefund
from .negotiation_execution import _allocation_hash, prepare_locked
from .negotiation_proposals import _decisions, _lock_case, _verified, enabled
from .refunds import authorized_refund

MARKER = 'zero_negotiation_v1'


def marked_execution_id(order):
    """Read the persisted marker, rejecting malformed evidence, never guessing."""
    terms = order.funding_terms
    if order.pk:
        from .models import Order
        terms = Order.objects.filter(pk=order.pk).values_list('funding_terms', flat=True).first()
    if not isinstance(terms, dict) or MARKER not in terms:
        return None
    marker = terms[MARKER]
    if (not isinstance(marker, dict) or set(marker) != {'execution_id'}
            or type(marker['execution_id']) is not int or marker['execution_id'] <= 0):
        raise ValidationError('零退款协商标记异常，请先核对')
    return marker['execution_id']


def ordinary_funding_enabled(order, *, has_addons=None):
    """Select the isolated ordinary zero-termination funding path."""
    from django.conf import settings
    from .ordinary_zero_followup_capability import eligible as production_eligible
    if production_eligible(order):
        actual_addons = order.addon_intents.filter(payment__isnull=False).exists()
        return bool((has_addons is None or has_addons == actual_addons)
                    and not order.refund_records.exists() and marked_execution_id(order) is not None)
    if not (getattr(settings, 'ORDINARY_ZERO_ADDON_FUNDING_INTERNAL_TESTING', False)
            or getattr(settings, 'ORDINARY_ZERO_NO_ADDON_FUNDING_INTERNAL_TESTING', False)):
        return False
    if order.refund_records.exists():
        return False
    if has_addons is None:
        has_addons = order.addon_intents.filter(payment__isnull=False).exists()
    flag = ('ORDINARY_ZERO_ADDON_FUNDING_INTERNAL_TESTING' if has_addons
            else 'ORDINARY_ZERO_NO_ADDON_FUNDING_INTERNAL_TESTING')
    return bool(getattr(settings, flag, False) and marked_execution_id(order) is not None)


def verify_zero_execution(execution, *, _allow_linked_refunds=False):
    """Verify immutable agreed shares while the caller holds the order lock."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('零退款协商核验必须在门店事务内执行')
    from .models import Order, OrdinaryTransferFollowupRound, OrderTransferNegotiationEvidence
    if OrdinaryTransferFollowupRound.objects.filter(execution=execution).exists():
        from .ordinary_transfer_followup_zero import verify_execution
        return verify_execution(execution)
    if OrderTransferNegotiationEvidence.objects.filter(execution=execution).exists():
        from .ordinary_transfer_first_zero import verify_execution
        return verify_execution(execution)
    execution = NegotiationExecution.objects.select_for_update().select_related('proposal__case').get(pk=execution.pk)
    proposal = execution.proposal
    order = Order.objects.select_for_update().filter(pk=execution.zero_order_id).first()
    case = proposal.case
    episode = copy(case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    _verified(episode, proposal)
    _decisions(episode, proposal, lock=True)
    if (not order or order.pk != case.order_id or proposal.refund_units != 0
            or proposal.status != 'ACCEPTED' or execution.terms_hash != proposal.terms_hash
            or execution.accepted_at != proposal.accepted_at or execution.refund_id
            or execution.customer_transaction_id or execution.rounding_to_store_units
            or not isinstance(execution.zero_request_id, str)
            or not 1 <= len(execution.zero_request_id) <= 64
            or execution.zero_request_id != execution.zero_request_id.strip()):
        raise ValidationError('零退款协商凭证不一致，请联系管理员核对')
    from wallet.models import Transaction
    payments = list(order.transactions.select_for_update().filter(
        tx_type='PAY', status='SUCCESS', reversal_of__isnull=True).select_related('wallet'))
    if (len(payments) != 1 or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id or payments[0].amount != -order.amount
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversals.exists()):
        raise ValidationError('零退款协商原付款流水待核对')
    from engagement.funding_wallet import validate_order_funding_sources
    funding = list(order.funding_allocations.select_for_update().select_related('lot').order_by('pk'))
    if (order.funding_terms is None or not funding or sum(row.amount for row in funding) != order.amount
            or any((row.refunded_amount and not _allow_linked_refunds)
                   or row.lot.account_id != order.customer_account_id for row in funding)):
        raise ValidationError('零退款协商原付款来源待核对')
    validate_order_funding_sources(order, funding)
    allocations = execution.zero_allocation
    if not isinstance(allocations, list) or execution.allocation_hash != _allocation_hash(allocations):
        raise ValidationError('零退款协商履约份额不一致')
    assignments = list(order.providers.select_for_update().order_by('pk'))
    if (not assignments or len(assignments) != len(allocations)
            or any(not row.provider_id or not row.provider_account_id for row in assignments)
            or sum(row.settlement_base for row in assignments) != order.amount
            or sum(row.provider_income for row in assignments) != order.provider_income
            or order.provider_income + order.inviter_commission + order.shop_income != order.amount):
        raise ValidationError('零退款协商履约份额不完整')
    terms = {item['account_id']: item for item in proposal.provider_terms}
    shares = list(execution.shares.select_for_update().select_related('assignment').order_by('account_id'))
    if [row.account_id for row in shares] != sorted(terms):
        raise ValidationError('零退款协商陪玩份额不完整')
    for assignment, item in zip(assignments, allocations):
        if not isinstance(item, dict):
            raise ValidationError('零退款协商履约份额不一致')
        expected = {'key':f'provider:{assignment.pk}', 'role':'PROVIDER',
            'account_id':assignment.provider_account_id, 'user_id':assignment.provider_id,
            'share_id':assignment.pk, 'entitlement':assignment.provider_income,
            'amount':0, 'retained':assignment.provider_income}
        if type(item.get('settled')) is not bool or {k:v for k,v in item.items() if k != 'settled'} != expected:
            raise ValidationError('零退款协商履约份额不一致')
    from .addon_main_deduction import deductions_at
    prior_deductions = deductions_at(order, before=execution.created_at)
    for share in shares:
        term = terms[share.account_id]
        if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                or share.original_income != share.assignment.provider_income - prior_deductions.get(share.account_id, 0)
                or share.original_income != term['original_income_units']
                or share.retained_income != share.original_income or share.retained_income != term['retained_units']
                or any((term['burden_units'], term['additional_units'], share.burden, share.pending_deduction,
                        share.wallet_deduction, share.deposit_deduction, share.wallet_transaction_id,
                        share.deposit_deficit_id))):
            raise ValidationError('零退款协商陪玩收益不一致')
    return shares


def apply_zero(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Prepare a zero-refund receipt under the order and shared money locks.

    Only an enclosing transactional termination implementation may call it;
    it does not end service, close a case, settle income or emit notifications.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('零退款协商准备必须在完整终止事务内执行')
    if (not enabled() or not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id != request_id.strip()):
        raise ValidationError('协商终止权限或操作凭证无效')
    if legacy_operator is not None and not LegacyAccountMap.objects.filter(
            account=operator, legacy_user_id=legacy_operator.pk).exists():
        raise ValidationError('经办人的业务账号与历史身份不一致')
    order, case = _lock_case(case_id)
    lock_order_money()
    existing = NegotiationExecution.objects.select_for_update().filter(proposal_id=proposal_id, proposal__case=case).first()
    if existing:
        if existing.operator_id != operator.pk or existing.zero_request_id != request_id:
            raise ValidationError('该方案已处理，请核对原记录')
        verify_zero_execution(existing)
        return existing
    if (OrderRefund.objects.filter(order=order, client_request_id=request_id).exists()
            or NegotiationExecution.objects.filter(zero_order=order, zero_request_id=request_id).exists()):
        raise ValidationError('操作凭证已用于其他处理')
    plan = prepare_locked(case_id, proposal_id, operator=operator)
    order, proposal = plan['order'], plan['proposal']
    if proposal.refund_units != 0:
        raise ValidationError('该方案包含退款，不能按零退款终止处理')
    if order.consumption_confirmed_at or order.refunded_at is not None or order.funding_terms is None:
        raise ValidationError('订单实销或原付款来源待核对')
    from django.utils import timezone
    from .models import Order
    from .ordinary_transfer_capability import permits as transfer_permits
    transfer_zero = transfer_permits(order)
    if (not order.in_service_at or not proposal.accepted_at
            or not order.in_service_at <= proposal.accepted_at <= timezone.now()
            or (order.settlement_mode != order.SettlementMode.REPORT_APPROVAL and not (
                order.settlement_mode == order.SettlementMode.COMPLETION and transfer_zero))
            or order.service_clock_version is None or order.service_aftersale_hours is None
            or order.service_freeze_hours is None or order.service_deposit_percent is None
            or not 0 <= order.service_deposit_percent <= 100
            or order.status not in (order.Status.IN_SERVICE, order.Status.COMPLETED)
            or (order.status == order.Status.COMPLETED and
                (not order.completed_at or not order.in_service_at <= order.completed_at <= timezone.now()))):
        raise ValidationError('协商终止时间或结算规则待核对')
    if order.source_order_id or Order.objects.filter(source_order_id=order.pk, is_gift_snapshot=False).exists():
        raise ValidationError('有关联服务订单，须先核对加购或续单')
    from .direct_payment_models import DirectPaymentIntent
    from .models import OrderPlatformSubsidy
    from .points_service_payment import needs_integrated_processing
    if (DirectPaymentIntent.objects.filter(order=order).exists()
            or OrderPlatformSubsidy.objects.filter(order=order).exists() or needs_integrated_processing(order)):
        raise ValidationError('该订单资金类型尚不支持零退款协商终止')
    allocations = []
    for row in order.providers.select_for_update().order_by('pk'):
        allocations.append({'key':f'provider:{row.pk}', 'role':'PROVIDER', 'account_id':row.provider_account_id,
            'user_id':row.provider_id, 'share_id':row.pk, 'entitlement':row.provider_income, 'amount':0,
            'retained':row.provider_income, 'settled':bool(row.settled_at)})
    receipt = NegotiationExecution.objects.create(proposal=proposal, zero_order=order, zero_request_id=request_id,
        zero_allocation=allocations, operator=operator, terms_hash=proposal.terms_hash,
        allocation_hash=_allocation_hash(allocations), accepted_at=proposal.accepted_at)
    terms = {item['account_id']:item for item in proposal.provider_terms}
    for item in plan['providers']:
        term = terms[item['account_id']]
        NegotiationExecutionShare.objects.create(execution=receipt, assignment=item['assignment'],
            account_id=item['account_id'], original_income=term['original_income_units'], burden=0,
            pending_deduction=0, wallet_deduction=0, deposit_deduction=0, retained_income=term['retained_units'])
    from .negotiation_transfer_preview import agreement_testing_available
    if agreement_testing_available(order):
        from .negotiation_transfer_evidence import make_snapshot_locked, persist_locked
        persist_locked(receipt, make_snapshot_locked(order, plan['calculation']))
    verify_zero_execution(receipt)
    return receipt


def verify_zero_termination(execution):
    """Verify scoped termination, marker and its single business audit."""
    from .models import OrdinaryTransferFollowupRound
    if OrdinaryTransferFollowupRound.objects.filter(execution=execution).exists():
        from .ordinary_transfer_followup_zero import verify_termination
        return verify_termination(execution)
    from console.models import AdminAuditLog
    if not execution.termination_processed_at or execution.termination_processed_at < execution.accepted_at:
        raise ValidationError('零退款协商终止记录待核对')
    order, case = execution.zero_order, execution.proposal.case
    if marked_execution_id(order) != execution.pk:
        raise ValidationError('零退款协商标记与凭证不一致')
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime
    if not isinstance(case.history, list):
        raise ValidationError('零退款协商售后记录待核对')
    entries = [row for row in case.history if isinstance(row, dict) and row.get('status') == 'RESOLVED'
               and row.get('created_at') == execution.proposal.case_opened_at.isoformat()
               and row.get('client_request_id') == execution.proposal.case_request_id]
    try:
        resolved_at = parse_datetime(entries[0].get('resolved_at') or '') if len(entries) == 1 else None
    except (ValueError, TypeError):
        resolved_at = None
    if (len(entries) != 1 or not resolved_at or not timezone.is_aware(resolved_at)
            or not execution.accepted_at <= resolved_at <= execution.termination_processed_at
            or entries[0].get('operator_id') != execution.operator_id
            or entries[0].get('resolution') != execution.proposal.explanation
            or entries[0].get('affected_provider_account_ids') != execution.proposal.affected_accounts):
        raise ValidationError('零退款协商售后记录待核对')
    path = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
    rows = list(AdminAuditLog.objects.filter(path=path, resource='aftersales', object_id=str(case.pk)))
    rows = [row for row in rows if isinstance(row.request_body, dict)
            and row.request_body.get('execution_id') == execution.pk]
    expected = {'proposal_id':execution.proposal_id, 'execution_id':execution.pk,
                'client_request_id':execution.zero_request_id, 'refund_units':0}
    if (len(rows) != 1 or rows[0].operator_account_id != execution.operator_id
            or rows[0].method != 'POST' or rows[0].status_code != 200 or rows[0].request_body != expected
            or (rows[0].operator_id and not LegacyAccountMap.objects.filter(
                account_id=execution.operator_id, legacy_user_id=rows[0].operator_id).exists())):
        raise ValidationError('零退款协商审计凭证待核对')


@transaction.atomic
def terminate_zero_internal(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Terminate the agreed scope atomically through the feature-gated staff flow."""
    from django.conf import settings
    if not getattr(settings, 'NEGOTIATION_ZERO_EXECUTION_ENABLED', False):
        raise ValidationError('零退款协商终止尚未开放')
    order, case = _lock_case(case_id)
    lock_order_money()
    receipt = apply_zero(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator,
                         request_id=request_id)
    receipt = NegotiationExecution.objects.select_for_update().get(pk=receipt.pk)
    shares = verify_zero_execution(receipt)
    if receipt.termination_processed_at:
        verify_zero_termination(receipt)
        return receipt
    if (case.status != 'OPEN' or case.client_request_id != receipt.proposal.case_request_id
            or case.created_at != receipt.proposal.case_opened_at or marked_execution_id(order) is not None):
        raise ValidationError('协商售后状态或凭证已变化，请重新核对')
    from .negotiation_termination import _capture_prior_unaffected_settlements
    _capture_prior_unaffected_settlements(order, receipt, shares)
    order.funding_terms = {**order.funding_terms, MARKER:{'execution_id':receipt.pk}}
    order.save(update_fields=['funding_terms'])
    accepted_at = receipt.accepted_at
    completed_by_negotiation = False
    if order.status == order.Status.IN_SERVICE:
        from .models import OrderParticipationSegment
        agreed_ids = {share.account_id for share in shares}
        active = list(order.providers.select_for_update().filter(released_at__isnull=True).order_by('pk'))
        selected_ids = set(receipt.proposal.affected_segment_ids or [])
        if hasattr(receipt, 'transfer_evidence') and selected_ids:
            selected_active = set(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, pk__in=selected_ids, released_at__isnull=True)
                .values_list('provider_account_id', flat=True))
            affected_ids = agreed_ids & selected_active
            for share in shares:
                if share.account_id not in affected_ids and not OrderParticipationSegment.objects.filter(
                        attribution__order=order, provider_account_id=share.account_id, pk__in=selected_ids,
                        started_at__isnull=False, released_at__isnull=False).exists():
                    raise ValidationError('所选历史履约段已变化')
        else:
            affected_ids = agreed_ids
        affected = [row for row in active if row.provider_account_id in affected_ids]
        if (len(affected) != len(affected_ids) or any(row.settled_at for row in affected)
                or any(row.pk not in {share.assignment_id for share in shares} for row in affected)):
            raise ValidationError('受影响陪玩的当前履约状态已变化')
        for assignment in affected:
            segments = list(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, provider_account_id=assignment.provider_account_id,
                released_at__isnull=True).order_by('pk'))
            if len(segments) > 1 or any(segment.started_at is None for segment in segments):
                raise ValidationError('陪玩当前履约片段待核对')
            for segment in segments:
                if hasattr(receipt, 'transfer_evidence'):
                    from .models import OrderWorkStint
                    work = list(OrderWorkStint.objects.select_for_update().filter(
                        order=order, account_id=assignment.provider_account_id, released_at__isnull=True))
                    if len(work) != 1 or work[0].started_at != segment.started_at:
                        raise ValidationError('本人履约段与工时记录不一致')
                    work[0].released_at = accepted_at
                    work[0].save(update_fields=['released_at'])
                segment.released_at = accepted_at; segment.save(update_fields=['released_at'])
            assignment.released_at = accepted_at; assignment.save(update_fields=['released_at'])
        remaining = [row for row in active if row.provider_account_id not in affected_ids]
        if remaining and order.provider_account_id in affected_ids:
            replacement = remaining[0]
            order.provider_id, order.provider_account_id = replacement.provider_id, replacement.provider_account_id
            order.provider_name_snapshot = replacement.provider_name_snapshot
            order.save(update_fields=['provider', 'provider_account', 'provider_name_snapshot', 'updated_at'])
        from .services import ensure_fulfillment_reports, refresh_escort_status
        ensure_fulfillment_reports(order)
        if remaining:
            from .room_completion_review import invalidate_for_transfer_locked
            invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
            from .state_machine import log_only
            log_only(order, action='NEGOTIATED_STOP', operator=legacy_operator, operator_account=operator,
                     reason='零退款协商终止受影响陪玩履约')
        else:
            from .room_completion_review import invalidate_for_transfer_locked
            invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
            from .state_machine import transition
            from .services import complete_fulfillment
            def finish(current):
                current.completed_at = accepted_at
                complete_fulfillment(current, operator=legacy_operator, operator_account=operator)
            order = transition(order.pk, order.Status.COMPLETED, operator=legacy_operator,
                operator_account=operator, action='COMPLETE', reason='双方确认零退款协商终止',
                side_effect=finish, update_fields=['completed_at'], room_negotiation_execution_id=receipt.pk)
            completed_by_negotiation = True
        refresh_escort_status(provider_ids=[row.provider_id for row in affected],
                              account_ids=[row.provider_account_id for row in affected], exclude_order_ids=[order.pk])
    from django.utils import timezone
    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    case.status = 'RESOLVED'; case.operator = operator; case.resolution = receipt.proposal.explanation
    case.resolved_at = timezone.now(); append_history(case)
    from wallet.income_priority_evidence import attach_closure
    attach_closure(case)
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    receipt.termination_processed_at = timezone.now(); receipt.completed_by_negotiation = completed_by_negotiation
    receipt.save(update_fields=['termination_processed_at', 'completed_by_negotiation'])
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales',
        object_id=str(case.pk), request_body={'proposal_id':proposal_id, 'execution_id':receipt.pk,
            'client_request_id':request_id, 'refund_units':0}, status_code=200)
    verify_zero_termination(receipt)
    if hasattr(receipt, 'transfer_evidence'):
        from .models import OrderOriginalShareTransfer
        from .ordinary_post_refund_transfer import record_locked as record_transfer_version
        from .ordinary_post_refund_release import record_locked as release_transfer_funding
        event = (OrderOriginalShareTransfer.objects.select_for_update().filter(
            previous_segment__attribution__order=order).select_related('previous_segment__attribution')
            .order_by('-pk').first())
        if not event:
            raise ValidationError('转单零退款原履约版本缺失')
        revision = record_transfer_version(order, event, source_kind='ZERO_AGREEMENT', source_id=receipt.pk)
        release_transfer_funding(revision)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    return receipt
