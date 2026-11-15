"""Atomic, scoped service termination after a verified negotiated refund."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .financial_lock import lock_order_money
from .models import NegotiationExecution, OrderParticipationSegment
from .negotiation_execution import apply_refund, verify_execution
from .negotiation_proposals import _lock_case


def _capture_prior_unaffected_settlements(order, receipt, shares):
    """Bind already-paid, unaffected shares to their original evidence.

    The caller holds the order and money locks and the enclosing transaction;
    failure therefore rolls back the refund as well as this evidence.
    """
    if receipt.proposal.ordinary_paid_settlement_snapshot is not None:
        from .negotiation_transfer_evidence import verify_locked as verify_transfer
        from .ordinary_first_paid_settlement import capture_locked
        from .segment_settlement_plan import build_segment_settlement_plan
        from .settlement import SettlementError
        evidence = verify_transfer(receipt)
        if (not evidence or evidence.snapshot.get('ordinary_paid_settlement_snapshot')
                != receipt.proposal.ordinary_paid_settlement_snapshot):
            raise ValidationError('已结算逐段方案凭证待核对')
        try:
            plan = build_segment_settlement_plan(order, allow_recorded=True, _allow_recorded_refunds=True)
            paid = capture_locked(order, plan, {share.account_id for share in shares})
        except SettlementError as exc:
            raise ValidationError('已结算逐段流水待核对') from exc
        if paid != receipt.proposal.ordinary_paid_settlement_snapshot:
            raise ValidationError('已结算逐段流水已变化')
        return
    from club_accounts.models import LegacyAccountMap
    from wallet.models import ProviderReport
    from .negotiation_settlement import _verify_settled_assignment
    from .settlement import SettlementError

    affected = {share.account_id for share in shares}
    paid = list(order.providers.select_for_update().filter(settled_at__isnull=False).order_by('pk'))
    if not paid:
        return
    if (order.status != order.Status.COMPLETED or not order.completed_at
            or any(row.provider_account_id in affected or row.settled_at > timezone.now() for row in paid)):
        raise ValidationError('已结算陪玩的履约状态待核对')
    evidence = {}
    for row in paid:
        if not LegacyAccountMap.objects.select_for_update().filter(
                account_id=row.provider_account_id, legacy_user_id=row.provider_id).exists():
            raise ValidationError('已结算陪玩的账号归属待核对')
        reports = list(ProviderReport.objects.select_for_update().filter(
            order=order, provider_id=row.provider_id, provider_account_id=row.provider_account_id))
        credits = list(order.transactions.select_for_update().filter(
            tx_type='INCOME', status='SUCCESS', reversal_of__isnull=True,
            wallet__account_id=row.provider_account_id, wallet__user_id=row.provider_id,
            remark=f'订单收入：{order.order_no}'))
        if len(reports) != 1 or not reports[0].submitted_at or len(credits) != (1 if row.provider_income else 0):
            raise ValidationError('已结算陪玩的报单或收益流水待核对')
        report = reports[0]
        if (not report.audited_at or not report.auditor_id or not report.auditor_account_id
                or not report.submitted_at <= report.audited_at <= row.settled_at
                or not LegacyAccountMap.objects.select_for_update().filter(
                    account_id=report.auditor_account_id, legacy_user_id=report.auditor_id).exists()):
            raise ValidationError('已结算陪玩的报单审核凭证待核对')
        evidence[str(row.pk)] = {'report_id': report.pk, 'submitted_at': report.submitted_at.isoformat(),
            'settled_at': row.settled_at.isoformat(), 'income_transaction_id': credits[0].pk if credits else None,
            'audited_at': report.audited_at.isoformat(), 'auditor_id': report.auditor_id,
            'auditor_account_id': report.auditor_account_id}
        receipt.unaffected_report_evidence = evidence
        try:
            _verify_settled_assignment(order, receipt, row, None, report)
        except SettlementError as exc:
            raise ValidationError('已结算陪玩的报单、时限或收益流水待核对') from exc
    receipt.save(update_fields=['unaffected_report_evidence'])


def verify_termination_evidence(receipt):
    """Ensure a completed refund retained its transactional business audit."""
    if not receipt.termination_processed_at or receipt.termination_processed_at < receipt.accepted_at:
        raise ValidationError('协商终止记录待核对')
    from .points_service_payment import needs_integrated_processing
    order = receipt.refund.order
    if order.funding_terms is None or needs_integrated_processing(order):
        raise ValidationError('协商退款的付款来源待核对')
    from django.utils.dateparse import parse_datetime
    from .models import AfterSale
    case = AfterSale.objects.get(pk=receipt.proposal.case_id)
    if not isinstance(case.history, list):
        raise ValidationError('协商退款售后记录待核对')
    # The final refund can be reached over several agreements. Evaluate the
    # cumulative amount at this receipt, not the amount of this round alone.
    earlier = order.refund_records.filter(created_at__lt=receipt.refund.created_at)
    if order.refund_records.filter(created_at=receipt.refund.created_at).exclude(pk=receipt.refund_id).exists():
        raise ValidationError('协商退款先后顺序待核对')
    cumulative = receipt.refund.amount + sum(earlier.values_list('amount', flat=True))
    if cumulative > order.amount:
        raise ValidationError('协商累计退款超过原付款')
    expected_status = 'REFUNDED' if cumulative == order.amount else 'PARTIALLY_REFUNDED'
    entries = [row for row in case.history if isinstance(row, dict) and row.get('status') == expected_status
               and row.get('created_at') == receipt.proposal.case_opened_at.isoformat()
               and row.get('client_request_id') == receipt.proposal.case_request_id]
    try:
        resolved_at = parse_datetime(entries[0].get('resolved_at') or '') if len(entries) == 1 else None
    except (ValueError, TypeError):
        resolved_at = None
    if (len(entries) != 1 or not resolved_at or not timezone.is_aware(resolved_at)
            or not receipt.accepted_at <= resolved_at <= receipt.termination_processed_at
            or entries[0].get('operator_id') != receipt.operator_id
            or entries[0].get('resolution') != receipt.proposal.explanation
            or entries[0].get('affected_provider_account_ids') != receipt.proposal.affected_accounts
            or entries[0].get('affected_segment_ids') != receipt.proposal.affected_segment_ids):
        raise ValidationError('协商退款售后记录待核对')
    from console.models import AdminAuditLog
    case_id = receipt.proposal.case_id
    path = f'/api/admin/aftersales/{case_id}/negotiation-execution/'
    candidates = AdminAuditLog.objects.filter(path=path, resource='aftersales', object_id=str(case_id))
    rows = [row for row in candidates if isinstance(row.request_body, dict)
            and row.request_body.get('refund_id') == receipt.refund_id]
    expected = {'proposal_id':receipt.proposal_id, 'refund_id':receipt.refund_id,
                'refund_units':receipt.refund.amount, 'client_request_id':receipt.refund.client_request_id}
    if (len(rows) != 1 or rows[0].operator_account_id != receipt.operator_id
            or rows[0].operator_id != receipt.refund.operator_id or rows[0].method != 'POST'
            or rows[0].status_code != 200 or rows[0].request_body != expected):
        raise ValidationError('协商退款审计凭证待核对')


def continuing_team(order, *, verify=False, allow_completed=False):
    """Find the prior scoped negotiation; completed orders require explicit opt-in."""
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.exists():
        if (order.status != order.Status.IN_SERVICE
                and not (allow_completed and order.status == order.Status.COMPLETED)) or (order.escort_mode != order.EscortMode.DOUBLE
                or not order.requires_boss_selection):
            return None
        from .points_negotiation_termination import enabled, verify_termination_locked
        from .models import PointsNegotiationPosting
        if not enabled():
            if verify:
                raise ValidationError('积分协商后的双陪履约尚未接通')
            return None
        with transaction.atomic():
            order = type(order).objects.select_for_update().get(pk=order.pk)
            lock_order_money()
            posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
            try:
                if not posting:
                    raise ValidationError('积分协商凭证缺失')
                posting = verify_termination_locked(posting)
                active = list(order.providers.filter(released_at__isnull=True))
                shares = list(posting.shares.all())
                if len(active) != 1 or len(shares) != 1 or active[0].provider_account_id == shares[0].account_id:
                    raise ValidationError('积分协商双陪履约范围不一致')
                return posting
            except (ValidationError, ValueError) as exc:
                if verify:
                    raise ValidationError('积分协商后的双陪履约凭证待核对') from exc
                return None
    eligible_status = (order.status == order.Status.IN_SERVICE or
                       (allow_completed and order.status == order.Status.COMPLETED))
    if (not eligible_status or order.escort_mode != order.EscortMode.DOUBLE
            or not order.requires_boss_selection or order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED')):
        return None
    if verify:
        lock_order_money()
    from .negotiation_settlement import execution_for_order
    from .settlement import SettlementError
    try:
        receipt = execution_for_order(order, lock=verify)
    except SettlementError as exc:
        if verify:
            raise ValidationError('协商终止凭证待核对') from exc
        return None
    if not receipt or not receipt.termination_processed_at or receipt.completed_by_negotiation:
        return None
    if verify and receipt.zero_order_id:
        from .negotiation_zero import verify_zero_execution, verify_zero_termination
        shares = verify_zero_execution(receipt)
        verify_zero_termination(receipt)
    else:
        shares = verify_execution(receipt) if verify else list(receipt.shares.all())
        if verify:
            verify_termination_evidence(receipt)
    active = list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
    if (receipt.refund_id and isinstance(receipt.refund.benefit_effects, dict)
            and receipt.refund.benefit_effects.get('ordinary_transfer_negotiation_version') == 1):
        affected = {row.account_id for row in shares}
        if len(active) != 1 or not active[0] or active[0] in affected or not shares:
            return None
        return receipt
    if (len(active) != 1 or not active[0] or active[0] in {row.account_id for row in shares}
            or len(shares) != 1 or shares[0].assignment.released_at != receipt.accepted_at):
        return None
    return receipt


@transaction.atomic
def execute_and_terminate(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Post refund and termination together, rolling both back on failure."""
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    order, _ = _lock_case(case_id)
    from .negotiation_followup_accounting import (marked_followup_id, terminate_followup_internal,
                                                  verify_followup_accounting, verify_followup_termination)
    from .negotiation_followup import followup_enabled
    from .models import NegotiationFollowupLink
    followup_id = marked_followup_id(order)
    if followup_id is not None:
        from .refunds import authorized_refund
        from club_accounts.models import LegacyAccountMap
        if (not authorized_refund(operator) or not operator.is_active or not operator.can_login
                or (legacy_operator is not None and not LegacyAccountMap.objects.filter(
                    account=operator, legacy_user_id=legacy_operator.pk).exists())):
            raise ValidationError('退款权限或经办人身份无效')
        lock_order_money()
        link = NegotiationFollowupLink.objects.select_for_update().filter(pk=followup_id, order=order).first()
        if not link:
            raise ValidationError('追加协商凭证缺失')
        if proposal_id == link.execution.proposal_id:
            return terminate_followup_internal(case_id, proposal_id, operator=operator,
                                               legacy_operator=legacy_operator, request_id=request_id).execution
        if proposal_id == link.previous.proposal_id:
            previous = link.previous
            prior_key = previous.refund.client_request_id if previous.refund_id else previous.zero_request_id
            if previous.operator_id != operator.pk or prior_key != request_id:
                raise ValidationError('操作凭证不匹配，请联系原经办人核对')
            from .ordinary_addon_followup_settlement import is_followup
            if is_followup(link):
                from .ordinary_addon_followup_posting import verify_posting_locked
                verify_posting_locked(link)
            else:
                verify_followup_accounting(link)
            verify_followup_termination(link)
            return previous
        raise ValidationError('该订单已有其他追加协商记录，请先核对')
    from .models import NegotiationProposal
    from .negotiation_zero import marked_execution_id
    is_followup = (order.payment_status in (order.PaymentStatus.PARTIALLY_REFUNDED, order.PaymentStatus.REFUNDED)
                   or marked_execution_id(order) is not None)
    already_executed = NegotiationExecution.objects.filter(proposal_id=proposal_id, proposal__case_id=case_id).exists()
    if is_followup and not already_executed:
        from .points_checkout_test import ordinary_production_enabled
        if ordinary_production_enabled() and order.funding_allocations.exists():
            raise ValidationError('普通订单的追加协商资金尚未接通')
        if not followup_enabled():
            raise ValidationError('追加协商处理暂未开放')
        return terminate_followup_internal(case_id, proposal_id, operator=operator,
                                           legacy_operator=legacy_operator, request_id=request_id).execution
    if NegotiationProposal.objects.filter(pk=proposal_id, case_id=case_id, refund_units=0).exists():
        from .points_checkout_test import ordinary_production_enabled
        from .ordinary_zero_followup_capability import eligible as zero_followup_eligible
        if ordinary_production_enabled() and order.funding_allocations.exists() and not zero_followup_eligible(order):
            raise ValidationError('普通订单的零退款协商资金尚未接通')
        from .negotiation_zero import terminate_zero_internal
        return terminate_zero_internal(case_id, proposal_id, operator=operator,
                                       legacy_operator=legacy_operator, request_id=request_id)
    from .points_service_payment import needs_integrated_processing
    from .models import OrderOriginalShareTransfer
    if needs_integrated_processing(order):
        raise ValidationError('积分抵扣订单的协商退款尚未接通')
    if order.funding_terms is None:
        raise ValidationError('历史付款来源尚未完成核对，不能执行协商退款')
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        from .negotiation_transfer_preview import agreement_testing_available
        if not agreement_testing_available(order):
            raise ValidationError('转单后的协商收益尚未接通，不能执行协商退款')
    existing = NegotiationExecution.objects.filter(proposal_id=proposal_id, proposal__case_id=case_id).exists()
    if not existing:
        from .points_checkout_test import ordinary_production_enabled
        if ordinary_production_enabled() and order.funding_allocations.exists():
            from .models import OrderPrestartHandoff, OrderPlatformSubsidy, OrderStatusLog
            from .ordinary_transfer_capability import permits as ordinary_transfer_permits
            proposal = NegotiationProposal.objects.filter(pk=proposal_id, case_id=case_id).first()
            from .ordinary_addon_production import eligible as ordinary_addon_eligible
            if (not proposal or not 0 < proposal.refund_units <= order.amount
                    or (order.addon_intents.filter(payment__isnull=False).exists() and not ordinary_addon_eligible(order, proposal=proposal)
                        and not ordinary_transfer_permits(order))
                    or order.settled_at or (order.providers.filter(settled_at__isnull=False).exists()
                        and not (proposal.ordinary_paid_settlement_snapshot and ordinary_transfer_permits(order)))
                    or order.refund_records.exists() or NegotiationExecution.objects.filter(proposal__case__order=order).exists()
                    or OrderPrestartHandoff.objects.filter(order=order).exists()
                    or OrderPlatformSubsidy.objects.filter(order=order).exists()
                    or (order.status_logs.filter(action=OrderStatusLog.Action.TRANSFER).exists()
                        and not ordinary_transfer_permits(order))):
                raise ValidationError('该普通订单退款资金组合尚未接通，请联系门店核对')
        from .models import Order
        if (order.source_order_id or Order.objects.filter(source_order_id=order.pk, is_gift_snapshot=False).exists()):
            raise ValidationError('有关联服务订单，须先核对加购或续单，暂不能执行协商退款')
    receipt = apply_refund(case_id, proposal_id, operator=operator,
                           legacy_operator=legacy_operator, request_id=request_id)
    order, case = _lock_case(case_id)
    lock_order_money()
    receipt = NegotiationExecution.objects.select_for_update().get(pk=receipt.pk)
    shares = verify_execution(receipt)
    if receipt.termination_processed_at:
        verify_termination_evidence(receipt)
        return receipt
    accepted_at = receipt.accepted_at
    if (case.status != 'OPEN' or case.client_request_id != receipt.proposal.case_request_id
            or case.created_at != receipt.proposal.case_opened_at or not order.in_service_at
            or accepted_at < order.in_service_at or accepted_at > timezone.now()
            or order.settlement_mode != order.SettlementMode.REPORT_APPROVAL
            or order.service_clock_version is None or order.service_aftersale_hours is None
            or order.service_freeze_hours is None or order.service_deposit_percent is None
            or not 0 <= order.service_deposit_percent <= 100
            or order.status not in (order.Status.IN_SERVICE, order.Status.COMPLETED)
            or (order.status == order.Status.COMPLETED and
                (not order.completed_at or order.completed_at < order.in_service_at
                 or order.completed_at > timezone.now()))):
        raise ValidationError('协商终止时间、售后或结算规则待核对')

    _capture_prior_unaffected_settlements(order, receipt, shares)

    completed_by_negotiation = False
    if order.status == order.Status.IN_SERVICE:
        agreed_ids = {share.account_id for share in shares}
        active = list(order.providers.select_for_update().filter(released_at__isnull=True).order_by('pk'))
        selected_ids = set(receipt.proposal.affected_segment_ids or [])
        transfer_scoped = bool(selected_ids and isinstance(receipt.refund.benefit_effects, dict)
                               and receipt.refund.benefit_effects.get('ordinary_transfer_negotiation_version') == 1)
        if transfer_scoped:
            selected_active = set(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, pk__in=selected_ids, released_at__isnull=True)
                .values_list('provider_account_id', flat=True))
            affected_ids = agreed_ids & selected_active
        else:
            affected_ids = agreed_ids
        affected = [row for row in active if row.provider_account_id in affected_ids]
        transfer_receipt = (isinstance(receipt.refund.benefit_effects, dict)
                            and receipt.refund.benefit_effects.get('ordinary_transfer_negotiation_version') == 1)
        historical = [share for share in shares if share.account_id not in {row.provider_account_id for row in affected}]
        historical_ok = (transfer_receipt and all(OrderParticipationSegment.objects.filter(
                attribution__order=order, provider_account_id=share.account_id, pk__in=selected_ids,
                started_at__isnull=False, released_at__isnull=False).exists()
            for share in historical))
        if ((historical and not historical_ok)
                or {row.pk for row in affected} | {share.assignment_id for share in historical} != {share.assignment_id for share in shares}
                or any(row.settled_at for row in affected)):
            raise ValidationError('受影响陪玩的当前履约状态已变化')
        for assignment in affected:
            segments = list(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, provider_account_id=assignment.provider_account_id,
                released_at__isnull=True).order_by('pk'))
            if len(segments) > 1 or any(segment.started_at is None for segment in segments):
                raise ValidationError('陪玩当前履约片段待核对')
            for segment in segments:
                from .models import OrderWorkStint
                work = list(OrderWorkStint.objects.select_for_update().filter(
                    order=order, account_id=assignment.provider_account_id, released_at__isnull=True))
                if len(work) != 1 or work[0].started_at != segment.started_at:
                    raise ValidationError('陪玩当前履约与逐段记录不一致，请先核对')
                segment.released_at = accepted_at
                segment.save(update_fields=['released_at'])
                work[0].released_at = accepted_at
                work[0].save(update_fields=['released_at'])
            assignment.released_at = accepted_at
            assignment.save(update_fields=['released_at'])
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
            log_only(order, action='REFUND', operator=legacy_operator, operator_account=operator,
                     reason='协商退款，受影响陪玩终止履约')
        else:
            from .room_completion_review import invalidate_for_transfer_locked
            invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
            from .state_machine import transition
            from .services import complete_fulfillment
            def finish(current):
                # The two parties' agreement, rather than the later accounting
                # click, starts the negotiated aftersale wait.
                current.completed_at = accepted_at
                complete_fulfillment(current, operator=legacy_operator, operator_account=operator)
            order = transition(order.pk, order.Status.COMPLETED, operator=legacy_operator,
                operator_account=operator, action='COMPLETE', reason='双方确认协商终止', side_effect=finish,
                update_fields=['completed_at'], room_negotiation_execution_id=receipt.pk)
            completed_by_negotiation = True
        refresh_escort_status(provider_ids=[row.provider_id for row in affected],
                              account_ids=[row.provider_account_id for row in affected],
                              exclude_order_ids=[order.pk])

    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    case.status = 'REFUNDED' if order.payment_status == order.PaymentStatus.REFUNDED else 'PARTIALLY_REFUNDED'
    case.operator = operator
    case.resolution = receipt.proposal.explanation
    case.resolved_at = timezone.now()
    append_history(case)
    if getattr(settings, 'REFUND_DEPOSIT_DEFICIT_ENABLED', False):
        from wallet.income_priority_evidence import record_closure
        closure_evidence = record_closure(case)
        if closure_evidence:
            case.history[-1]['income_priority_closure_id'] = closure_evidence.pk
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    receipt.termination_processed_at = timezone.now()
    receipt.completed_by_negotiation = completed_by_negotiation
    receipt.save(update_fields=['termination_processed_at', 'completed_by_negotiation'])
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales',
        object_id=str(case.pk), request_body={'proposal_id':proposal_id, 'refund_id':receipt.refund_id,
            'refund_units':receipt.refund.amount, 'client_request_id':request_id}, status_code=200)
    verify_termination_evidence(receipt)
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    if ordinary_transfer_permits(order):
        from .ordinary_transfer_refund_release import record_locked as release_transfer_funding
        release_transfer_funding(order, receipt)
    from .points_checkout_test import ordinary_refund_production_order
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    production_refund = ordinary_refund_production_order(order) or ordinary_addon_eligible(order)
    if production_refund or getattr(settings, 'ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING', False):
        from .addon_settlement import has_paid_addons
        if production_refund or has_paid_addons(order):
            from .ordinary_refunded_addon_release import record_locked
            record_locked(order, receipt)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    from wallet.models import Wallet
    from wallet.notifier import notify_wallet_update
    wallet_ids = {receipt.customer_transaction.wallet_id}
    wallet_ids.update(Wallet.objects.filter(account_id__in=[share.account_id for share in shares]).values_list('pk', flat=True))
    for wallet in Wallet.objects.filter(pk__in=wallet_ids):
        transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
    return receipt
