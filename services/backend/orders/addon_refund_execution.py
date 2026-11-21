"""Post an agreed add-on refund in the same transaction as its funding deductions."""
from django.conf import settings
from django.db.models import F
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from engagement.models import PointFundingLot
from users.models import EscortProfile
from wallet.models import Transaction
from wallet.services import get_wallet
from .addon_refund import _locked, _source_plan
from .financial_lock import lock_order_money
from .models import (Order, OrderAddonPayment, OrderAddonRefund, OrderAddonRefundEarningDeduction, OrderAddonSettlement,
                     OrderSegmentAddonSettlement, OrderAddonRefundProposal, OrderOriginalShareTransfer)


def enabled():
    from .points_business_access import permits
    return bool(permits(phase='refund', kind='addon')
                or getattr(settings, 'ADDON_REFUND_EXECUTION_INTERNAL_TESTING', False))


def _unaffected_service_refunds(order, provider_id):
    """A teammate's completed negotiation does not consume this person's share."""
    refunds = list(order.refund_records.select_for_update().order_by('pk'))
    if not refunds:
        return
    from .models import NegotiationExecution
    from .negotiation_execution import verify_execution
    from .negotiation_termination import verify_termination_evidence
    for refund in refunds:
        receipt = NegotiationExecution.objects.select_for_update().filter(refund=refund).first()
        if not receipt:
            from .models import PointsNegotiationPosting
            points = PointsNegotiationPosting.objects.select_for_update().filter(points_refund__refund=refund).first()
            if points:
                from .points_negotiation_termination import verify_termination_locked
                verify_termination_locked(points)
                if points.shares.filter(account_id=provider_id).exists():
                    raise ValidationError('陪玩已确认终止的服务部分不能重新协商')
                continue
        if not receipt or not receipt.termination_processed_at:
            raise ValidationError('主单退款的陪玩收益归属尚待核对')
        shares = verify_execution(receipt)
        verify_termination_evidence(receipt)
        if any(share.account_id == provider_id for share in shares):
            raise ValidationError('陪玩已确认终止的服务部分不能重新协商')


def _main_pending_income(order, provider_id):
    """Read the person's actual unpaid service share, including transfer segments."""
    from .models import OrderPointsSettlementPerson, OrderSegmentPersonSettlement
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    if transferred:
        if order.refund_records.exists():
            from .points_service_payment import needs_integrated_processing
            if not needs_integrated_processing(order):
                from .ordinary_transfer_capability import permits as ordinary_transfer_permits
                if not ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True):
                    raise ValidationError('主单转单后的退款收益分摊尚待接通')
                from .addon_main_segment_deduction import remaining_locked
                return sum(amount for _, amount in remaining_locked(order, provider_id))
            from .points_transfer_refund_access import permits
            if not permits(order, phase='refund'):
                raise ValidationError('主单转单后的退款收益分摊尚待接通')
            from .addon_main_segment_deduction import remaining_locked
            return sum(amount for _, amount in remaining_locked(order, provider_id))
        from .points_service_payment import needs_integrated_processing
        from .segment_settlement_plan import build_segment_settlement_plan
        from .settlement import SettlementError
        try:
            from .points_transfer_refund_access import addon_payment_read
            with addon_payment_read(order):
                plan = build_segment_settlement_plan(order, allow_recorded=True,
                                                      _allow_points=needs_integrated_processing(order))
        except SettlementError as exc:
            raise ValidationError(str(exc)) from exc
        person = next((item for item in plan.persons if item.account_id == provider_id), None)
        if not person:
            raise ValidationError('主单陪玩收益不存在')
        settled = OrderSegmentPersonSettlement.objects.filter(manifest__order=order, account_id=provider_id).exists()
        return 0 if settled else person.income
    assignment = order.providers.select_for_update().filter(provider_account_id=provider_id).first()
    if not assignment:
        raise ValidationError('主单陪玩收益不存在')
    if assignment.settled_at or OrderPointsSettlementPerson.objects.select_for_update().filter(
            settlement__order=order, segment__provider_account_id=provider_id).exists():
        return 0
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    if order.refund_records.exists() and ordinary_addon_eligible(order, allow_open=True):
        from .models import NegotiationExecution
        from .negotiation_execution import verify_execution
        from .negotiation_termination import verify_termination_evidence
        execution = NegotiationExecution.objects.select_for_update().get(refund__order=order)
        shares = verify_execution(execution)
        verify_termination_evidence(execution)
        matches = [share for share in shares if share.account_id == provider_id]
        if len(matches) > 1:
            raise ValidationError('主单退款后陪玩剩余收益待核对')
        from .addon_main_deduction import deductions_at
        prior_addon = deductions_at(order, before=execution.created_at).get(provider_id, 0)
        if matches and (matches[0].assignment_id != assignment.pk
                or matches[0].retained_income != assignment.provider_income - prior_addon - matches[0].pending_deduction
                or matches[0].retained_income < 0):
            raise ValidationError('主单退款后陪玩剩余收益待核对')
        if matches:
            return matches[0].retained_income
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.exists():
        from .models import OrderSegmentFinancialSnapshot, PointsNegotiationPosting
        from .points_negotiation_termination import verify_termination_locked
        postings = list(PointsNegotiationPosting.objects.filter(
            points_refund__redemption__order=order).order_by('created_at', 'pk'))
        if not postings:
            raise ValidationError('积分主单退款凭证不存在')
        posting = postings[0]
        verify_termination_locked(posting, allow_verified_later_final=len(postings) == 2)
        if len(postings) == 2:
            from .points_followup_termination import verify_followup_termination_locked
            verify_followup_termination_locked(postings[1])
            matches = [item for item in postings if item.shares.filter(account_id=provider_id).exists()]
            if len(matches) != 1:
                raise ValidationError('两次主单退款后的陪玩收益归属待核对')
            posting = matches[0]
        elif len(postings) != 1:
            raise ValidationError('积分主单退款次数待核对')
        from .points_service_participation import service_snapshots
        matches = [row for row in service_snapshots(order) if row.segment.provider_account_id == provider_id]
        if len(matches) != 1:
            raise ValidationError('积分主单陪玩收益快照不存在')
        snapshot = matches[0]
        share = posting.shares.filter(account_id=provider_id).first()
        if share:
            return share.retained_income
        from .addon_main_deduction import deductions_at
        prior = deductions_at(order, before=posting.created_at).get(provider_id, 0)
        if prior > snapshot.income_at_entry:
            raise ValidationError('主单退款前的加购收益占用异常')
        return snapshot.income_at_entry - prior
    _unaffected_service_refunds(order, provider_id)
    if needs_integrated_processing(order) and not order.refund_records.exists():
        from .points_funding_plan import PointsFundingPlanError, verified_person_income
        try:
            return verified_person_income(order.pk, provider_id)
        except PointsFundingPlanError as exc:
            raise ValidationError('积分主单陪玩收益待核对') from exc
    return assignment.provider_income


def _require_refund_operator(operator):
    from .refunds import authorized_refund
    if (not operator or operator._state.db != transaction.get_connection().alias
            or not operator.is_active or not operator.can_login or not authorized_refund(operator)):
        raise ValidationError('仅有退款权限的有效后台账号可以执行退款')


def _has_handover_refund_receipt(order, payment_id, proposal_id):
    recorded = OrderAddonRefund.objects.filter(order=order, payment_id=payment_id, proposal_id=proposal_id)
    if order.room_required_snapshot:
        return False
    refund = recorded.filter(
            payment__handovers__status='ACCEPTED', payment__handovers__kind='STANDARD',
            payment__handovers__previous__isnull=True, payment__handovers__accepted_at__lt=F('created_at')).first()
    if not refund or order.refund_records.exists() or order.prestart_handoffs.exists():
        return False
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
    if len(payments) == 2:
        from .points_transfer_refund_access import unaffected_addon_for_return
        original = transfers.select_related('previous_segment').order_by('created_at', 'pk').first()
        if (not original or original.created_at >= refund.created_at
                or original.previous_segment_id != refund.payment.intent.segment_id
                or not unaffected_addon_for_return(order, refund.payment, payments, original, boundary=refund.created_at)):
            return False
    elif len(payments) != 1 or not transfers.exists():
        return False
    # A refund posted before the first allocation can still use the original refund path.
    # An accepted second handover, however, requires its original funded proof on readback.
    return (recorded.filter(funding_release_run__isnull=False).exists()
            or recorded.filter(payment__handovers__status='ACCEPTED',
                               payment__handovers__previous__isnull=False).exists())


def _replay_handover_refund(order_id, payment_id, proposal_id, operator):
    from console.models import AdminAuditLog
    from .addon_funding_release import verify_run
    from .points_transfer_refund_access import after_transfer

    _require_refund_operator(operator)
    with after_transfer(order_id, phase='report', pending_payment_id=payment_id, read_only=True) as order:
        refund = OrderAddonRefund.objects.select_related('funding_release_run').filter(
            order=order, payment_id=payment_id, proposal_id=proposal_id).first()
        if not refund or order.refund_records.exists():
            raise ValidationError('原加购退款状态已变化，请先核对')
        audits = list(AdminAuditLog.objects.filter(resource='addon-refund', object_id=str(refund.pk),
            method='POST', path=f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/execute/',
            status_code=200))
        expected = {'order_id': order_id, 'payment_id': payment_id, 'proposal_id': proposal_id, 'amount': refund.amount}
        if len(audits) != 1 or audits[0].operator_account_id != refund.operator_id or audits[0].request_body != expected:
            raise ValidationError('原加购退款审计凭证待核对')
        run = getattr(refund, 'funding_release_run', None)
        if not run or not run.points_addon_hash or not run.points_addon_snapshot:
            raise ValidationError('原加购退款补资凭证待核对')
        verify_run(run, _skip_usage=True)
        history = run.points_addon_snapshot['funding']['addon_history']
        income = [row['provider_units'] for row in history if row['payment_id'] == payment_id]
        if len(income) != 1:
            raise ValidationError('原加购退款收益凭证待核对')
        return refund, income[0]


@transaction.atomic
def execute(order_id, payment_id, proposal_id, *, operator, legacy_operator):
    """Return the refund and any frozen income for an original handover refund receipt."""
    from .points_service_payment import needs_integrated_processing
    order = Order.objects.filter(pk=order_id).first()
    if (order and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and needs_integrated_processing(order)):
        from .points_business_access import service_order, transferred_refunded_addon
        if _has_handover_refund_receipt(order, payment_id, proposal_id):
            return _replay_handover_refund(order_id, payment_id, proposal_id, operator)
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            raise ValidationError('订单不存在')
        # A same-proposal request can commit while this request waits for the order.
        if _has_handover_refund_receipt(order, payment_id, proposal_id):
            return _replay_handover_refund(order_id, payment_id, proposal_id, operator)
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        scope = (transferred_refunded_addon(order_id, payment_id, replay_proposal_id=proposal_id)
                 if transferred and order.refund_records.exists()
                 else service_order(order_id, phase='refund', addon=True, refund_payment_id=payment_id))
        with scope:
            return _execute(order_id, payment_id, proposal_id, operator=operator, legacy_operator=legacy_operator), None
    return _execute(order_id, payment_id, proposal_id, operator=operator, legacy_operator=legacy_operator), None


def _execute(order_id, payment_id, proposal_id, *, operator, legacy_operator):
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    _require_refund_operator(operator)
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('订单不存在')
    lock_order_money()
    from .points_addon_receipt_usage import prelock as prelock_addon_receipts
    usage_receipts = prelock_addon_receipts(order)
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    production_addon = ordinary_addon_eligible(order, allow_open=True)
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    # Pending handovers are checked under lock below and invalidated only when
    # this refund actually changes their responsibility or earnings.
    transferred_addon = ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True)
    if not (enabled() or production_addon or transferred_addon):
        raise ValidationError('加购退款和整单结算尚未开放')
    previous = (OrderAddonRefund.objects.select_for_update().filter(
        order=order, payment_id=payment_id, proposal_id=proposal_id).first())
    if previous:
        from .models import OrderAddonFundingReleaseRun
        from .models import OrdinaryAddonPaidRefundEvidence
        evidence = OrdinaryAddonPaidRefundEvidence.objects.select_related(
            'settlement', 'refund__proposal', 'run').filter(refund=previous).first()
        if evidence:
            from .ordinary_addon_paid_refund import verify_recorded
            verify_recorded(evidence)
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order) and order.refund_records.count() >= 3:
            from .models import PointsThirdAddonFundingRevision
            version = PointsThirdAddonFundingRevision.objects.select_related('run').filter(
                order=order, refund=previous).first()
            if not version:
                raise ValidationError('已执行退款缺少第三轮后的实际资金版本')
            from .points_third_addon_receipt_usage import verify_run_locked
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                verify_run_locked(version.run)
            except DjangoValidationError as exc:
                raise ValidationError('已执行退款的实际资金凭证待核对') from exc
            return previous
        if transferred_addon:
            from .addon_payment import verify_receipt
            from .addon_refund_share_allocation import verify as verify_share_allocations
            from django.core.exceptions import ValidationError as DjangoValidationError
            from .models import OrdinaryPostRefundTransferRevision, OrderPointsFundingAllocation
            verify_receipt(previous.payment)
            try:
                verify_share_allocations(previous)
            except DjangoValidationError as exc:
                raise ValidationError('已执行加购退款的逐份额凭证异常') from exc
            run = OrderAddonFundingReleaseRun.objects.filter(refund=previous).first()
            revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                source_kind='ADDON_REFUND', source_id=previous.pk).first()
            if revision:
                from .ordinary_post_refund_transfer import verify_locked as verify_revision
                verify_revision(revision)
                if run:
                    raise ValidationError('加购退款存在重复资金版本')
                run = OrderAddonFundingReleaseRun.objects.filter(ordinary_transfer_revision=revision).first()
            funded_at_refund = OrderPointsFundingAllocation.objects.filter(service_order=order,
                created_at__lte=previous.created_at).exists()
            if funded_at_refund and not run:
                raise ValidationError('已执行转单加购退款的资金版本缺失')
            if run:
                from .addon_funding_release import verify_run
                from users.receipt_usage_sources import verify_allocations_locked
                verify_run(run)
                receipt_ids = OrderPointsFundingAllocation.objects.filter(
                    pk__in=[item[0] for item in run.allocation_snapshot], receipt__usage_version__isnull=False
                    ).order_by('receipt_id').values_list('receipt_id', flat=True).distinct()
                for receipt_id in receipt_ids:
                    verify_allocations_locked(receipt_id)
            from .models import OrdinaryPaidAddonRefundEvidence
            paid_evidence = OrdinaryPaidAddonRefundEvidence.objects.filter(refund=previous).first()
            if paid_evidence:
                from .ordinary_paid_addon_refund import verify_locked as verify_paid
                verify_paid(paid_evidence)
            return previous
        team_prior = OrderAddonFundingReleaseRun.objects.filter(handover__payment_id=payment_id,
            points_handover_snapshot__kind='SAME_TEAM_V1').exists()
        run = OrderAddonFundingReleaseRun.objects.filter(refund=previous).first()
        ordinary_receipt = bool(run and production_addon and run.ordinary_prestart_snapshot
                                and run.ordinary_prestart_hash)
        ordinary_after_main = bool(run and production_addon
            and isinstance(run.ordinary_refund_snapshot, dict)
            and run.ordinary_refund_snapshot.get('kind') == 'ordinary_addon_after_main_refund_v1'
            and run.ordinary_refund_hash)
        if production_addon and not run and order.refund_records.count() == 1:
            from .models import NegotiationExecution, OrderPointsFundingAllocation
            main = NegotiationExecution.objects.filter(refund__order=order,
                termination_processed_at__isnull=False).select_related('refund').first()
            if (main and previous.created_at < main.created_at
                    and not OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__lte=previous.created_at).exists()):
                from .ordinary_addon_prior_refund import verify_locked as verify_prior
                verify_prior(order, boundary=main.created_at)
                return previous
        if usage_receipts or team_prior or ordinary_receipt or ordinary_after_main:
            from .addon_funding_release import verify_run
            if not run or not (run.points_addon_snapshot or run.points_followup_snapshot
                               or ordinary_receipt or ordinary_after_main):
                raise ValidationError('加购退款到账凭证缺失')
            verify_run(run)
            if ordinary_receipt or ordinary_after_main:
                from .models import OrderPointsFundingAllocation
                from users.receipt_usage_sources import verify_allocations_locked
                allocation_ids = [item[0] for item in run.allocation_snapshot]
                receipt_ids = set(OrderPointsFundingAllocation.objects.filter(pk__in=allocation_ids,
                                  receipt__usage_version__isnull=False)
                                  .values_list('receipt_id', flat=True))
                for receipt_id in sorted(receipt_ids):
                    if ordinary_after_main:
                        from .ordinary_after_main_addon_receipt import verified_release
                        verified_release(run, receipt_id)
                    verify_allocations_locked(receipt_id)
            if ordinary_after_main:
                from .models import OrdinaryRefundedAddonPaidRevision
                revision = OrdinaryRefundedAddonPaidRevision.objects.filter(refund=previous, run=run).first()
                if revision:
                    from .ordinary_refunded_addon_paid_receipt import verify as verify_refunded_paid
                    verify_refunded_paid(run)
        return previous
    from .points_service_payment import needs_integrated_processing
    if (needs_integrated_processing(order)
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            and order.refund_records.exists() and OrderAddonRefund.objects.filter(order=order).exists()):
        from .points_transfer_refund_access import permits as transfer_permits
        from .points_business_access import permits as business_permits
        if not (transfer_permits(order, phase='refund') and business_permits(order, phase='refund', kind='addon')):
            raise ValidationError('主单退款后的多次加购退款尚未接通')
    from .points_business_access import permits
    if production_addon and OrderAddonRefund.objects.filter(payment_id=payment_id).exists():
        repeat_ordinary = not needs_integrated_processing(order) and order.refund_records.count() in (0, 1)
        if not repeat_ordinary:
            raise ValidationError('追加加购退款尚未开放')
    case_id = OrderAddonRefundProposal.objects.filter(pk=proposal_id, payment_id=payment_id).values_list('case_id', flat=True).first()
    order, case, payment = _locked(order_id, payment_id, case_id=case_id)
    from .points_voluntary_handover_event import entry_matches
    from .points_third_addon_refund import active as third_refund_active
    third_active = third_refund_active(order, payment)
    paid_before = None
    from .points_transfer_followup_addon_version import refund_preflight_active
    if not third_active and (entry_matches(order, payment.pk) or refund_preflight_active(order, payment)):
        from .points_voluntary_paid_evidence import capture_before_locked
        paid_before = capture_before_locked(order, payment)
    from .models import OrderAddonFundingReleaseRun
    team_event = OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
        points_handover_snapshot__kind='SAME_TEAM_V1').first()
    payment_team = OrderAddonFundingReleaseRun.objects.filter(handover__payment=payment,
        points_handover_snapshot__kind='SAME_TEAM_V1').exists()
    other_team_payment = bool(team_event and not payment_team)
    from .points_team_other_addon_refund import continuing_purchase
    if not third_active and other_team_payment and (order.refund_records.count() not in (0, 1, 2) or payment.handovers.exists()
            or (order.refund_records.exists() and payment.buyer_transaction.created_at >=
                order.refund_records.order_by('created_at', 'pk').first().created_at
                and not continuing_purchase(order, payment))):
        raise ValidationError('同单接替后其他加购的退款范围待核对')
    points_unaffected_paid = (needs_integrated_processing(order)
        and order.providers.filter(settled_at__isnull=False).exists()
        and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
    paid_followup_event = False
    if points_unaffected_paid:
        from .models import OrderPointsFollowupFundingRevision
        revision = OrderPointsFollowupFundingRevision.objects.filter(
            posting__points_refund__redemption__order=order, paid_person__isnull=False).first()
        if order.refund_records.count() == 2 and revision:
            shares = list(revision.posting.shares.select_related('assignment'))
            if (len(shares) != 1 or shares[0].assignment.settled_at
                    or revision.paid_person.segment.provider_account_id == shares[0].account_id):
                raise ValidationError('后续退款已付与未付陪玩范围待核对')
            assignment = shares[0].assignment
            paid_followup_event = True
        else:
            from .points_zero_followup import prepare_locked as prepare_unaffected_settlement
            _, assignment, _, _, _ = prepare_unaffected_settlement(order, case)
        from .addon_handover import current_responsibilities
        _, unpaid_responsibilities = current_responsibilities(payment, lock=True)
        if (case.affected_provider_account_ids != [assignment.provider_account_id]
                or not any(row['provider_id'] == assignment.provider_account_id and row['basis'] > 0
                           for row in unpaid_responsibilities)
                or (payment.handovers.exists() and not payment_team)
                or (not payment.handovers.exists() and payment.intent.provider_id != assignment.provider_account_id)):
            raise ValidationError('已结算陪玩不承担本次加购退款')
        from .models import PointsZeroFollowupTermination
        zero = PointsZeroFollowupTermination.objects.select_for_update().filter(order=order).first()
        if zero:
            from .points_zero_followup import verify_record_locked
            verify_record_locked(zero)
    first_transfer_handover = False
    if (needs_integrated_processing(order) and payment.handovers.exists()
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        from .models import OrderAddonHandover
        from .points_transfer_refund_access import first_handover_refund, single_post_refund_handover, post_refund_handover_history
        from .points_followup_handover_invalidation import other_pending_payment_id
        from .points_voluntary_handover_event import entry_matches
        from .points_transfer_followup_addon_version import refund_preflight_active
        from .points_third_addon_refund import active as third_refund_active
        transfers = list(OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).order_by('pk'))
        other_pending = other_pending_payment_id(order, payment.pk)
        from .points_transfer_addon_liability import first_refund_active
        if first_refund_active(order, payment.pk):
            other_pending = frozenset(OrderAddonHandover.objects.filter(
                payment__intent__order=order, status='PENDING').values_list('payment_id', flat=True))
        first_handover = first_handover_refund(order, transfers)
        if not (first_handover or single_post_refund_handover(order, pending_payment_id=other_pending)
                or post_refund_handover_history(order, pending_payment_id=other_pending)
                or entry_matches(order, payment.pk) or refund_preflight_active(order, payment)
                or third_refund_active(order, payment)):
            raise ValidationError('已接替加购的独立退款资金尚未接入')
        first_transfer_handover = (first_handover and len(transfers) == 1 and not order.refund_records.exists()
            and payment.handovers.filter(status='ACCEPTED', kind='STANDARD', previous__isnull=True,
                source_segment_id=transfers[0].previous_segment_id, target_segment_id=transfers[0].next_segment_id).exists())
        if first_transfer_handover:
            payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
            from .points_transfer_refund_access import unaffected_addon_for_return
            first_transfer_handover = (len(payments) == 1
                or unaffected_addon_for_return(order, payment, payments, transfers[0]))
    proposal = OrderAddonRefundProposal.objects.select_for_update().filter(
        pk=proposal_id, payment=payment, case=case).first()
    if (not proposal or proposal.status != 'ACCEPTED' or proposal.case_opened_at != case.created_at
            or proposal.case_request_id != case.client_request_id):
        raise ValidationError('退款方案尚未获得双方同意或售后已变化')
    participants = list(proposal.participants.select_for_update().order_by('account_id'))
    if points_unaffected_paid and [person.account_id for person in participants] != [assignment.provider_account_id]:
        raise ValidationError('仅未结算的实际履约陪玩可以承担本次加购退款')
    decisions = list(proposal.decisions.select_for_update().values_list('account_id', 'role', 'accepted'))
    if not participants or set(decisions) != ({(payment.intent.buyer_id, 'BOSS', True)} |
            {(row.account_id, 'PROVIDER', True) for row in participants}):
        raise ValidationError('退款方案尚未获得双方同意或售后已变化')
    from .addon_refund import _participants
    saved = [{'account_id': row.account_id, 'basis': row.basis, 'burden': row.burden, 'excess_burden': row.excess_burden,
              'share_plan': row.share_plan} for row in participants]
    if saved != _participants(payment, case, proposal.amount, [row.account_id for row in participants]):
        raise ValidationError('加购履约份额已变化，请重新确认')
    ordinary_refunded_paid_snapshot = None
    if (production_addon and not needs_integrated_processing(order) and order.refund_records.count() == 1
            and order.providers.filter(settled_at__isnull=False).exists()
            and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        if case.addon_payment_id != payment.pk:
            raise ValidationError('已付后加购退款须使用该笔付款的售后记录')
        from .ordinary_refunded_addon_paid_revision import capture_locked
        ordinary_refunded_paid_snapshot = capture_locked(order, case, [row.account_id for row in participants])
    if (production_addon and (not 0 < proposal.amount <= payment.intent.amount or order.settled_at
            or (order.providers.filter(settled_at__isnull=False).exists() and not ordinary_refunded_paid_snapshot))):
        raise ValidationError('当前加购退款范围尚未开放')
    ordinary_direct_paid_snapshot = None
    if (production_addon and not needs_integrated_processing(order) and not order.refund_records.exists()
            and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        from .ordinary_addon_paid_refund import capture_locked
        ordinary_direct_paid_snapshot = capture_locked(order, case, [row.account_id for row in participants])
    ordinary_paid_snapshot = None
    if (not needs_integrated_processing(order) and not order.refund_records.exists()
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        if ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True):
            from .ordinary_paid_addon_refund import capture_locked
            ordinary_paid_snapshot = capture_locked(order, case, [row.account_id for row in participants])
            if ordinary_paid_snapshot is not None and case.addon_payment_id != payment.pk:
                raise ValidationError('已付后加购退款须使用原加购售后记录')
    from .addon_refund_cross_plan import require_current
    require_current(proposal, participants)
    from .points_transfer_zero_addon_settlement import guard_unpaid_participants_locked
    guard_unpaid_participants_locked(payment, saved)
    if proposal.source_plan != _source_plan(payment, proposal.amount):
        raise ValidationError('退款来源已变化，请重新确认方案')
    from .addon_payment import verify_receipt
    verify_receipt(payment)

    # Each agreed participant first contributes their own unsettled earnings,
    # then their wallet, then their individually evidenced deposit.
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    addons = list(OrderAddonPayment.objects.select_for_update().filter(
        intent__order=order).select_related('intent').order_by('pk'))
    addons.sort(key=lambda row: (row.pk != payment.pk, row.pk))
    if needs_integrated_processing(order) and order.refund_records.count() >= 3:
        from .points_third_addon_responsibilities import current as current_responsibilities
    responsibility = {addon.pk: current_responsibilities(addon, lock=True)[1] for addon in addons}
    profiles = {profile.account_id: profile for profile in EscortProfile.objects.select_for_update()
                .filter(account_id__in=[row.account_id for row in participants]).order_by('account_id')}
    plans = []
    pending_handover_invalidations = {}
    ordinary_pending_invalidations = set()
    own_pending = payment.handovers.select_for_update().filter(status='PENDING').first()
    if own_pending:
        from .points_followup_handover_invalidation import permits_locked as permits_followup_invalidation
        from .ordinary_pending_handover_refund import permits as ordinary_pending_permits
        from .points_handover_refund_invalidation import permits_other_payment_locked as permits_team_invalidation
        from .points_third_handover_invalidation import permits_for_round_locked as permits_third_invalidation
        team_same_payment = (needs_integrated_processing(order)
            and permits_team_invalidation(order, payment.pk, allow_same_payment=True))
        if (not permits_followup_invalidation(order, own_pending, payment)
                and not ordinary_pending_permits(order, payment, own_pending) and not team_same_payment
                and not permits_third_invalidation(order, own_pending)):
            assert_no_pending_handover(payment)
        if ordinary_pending_permits(order, payment, own_pending):
            ordinary_pending_invalidations.add(own_pending.pk)
        pending_handover_invalidations[own_pending.pk] = own_pending
    zero_wallet_states = {}
    main_segment_parts = {}
    for person in participants:
        outstanding, deductions = person.burden, []
        for addon in addons:
            grouped = {}
            for share in responsibility[addon.pk]:
                if share['provider_id'] == person.account_id:
                    grouped[share['segment_id']] = grouped.get(share['segment_id'], 0) + share['income']
            for segment_id, income in sorted(grouped.items()):
                if (OrderAddonSettlement.objects.filter(payment=addon, segment_id=segment_id).exists()
                        or OrderSegmentAddonSettlement.objects.filter(payment=addon, segment_id=segment_id).exists()):
                    continue
                take = min(outstanding, income)
                if take:
                    # Another add-on's unresolved handover matters only when
                    # this refund would actually consume its pending income.
                    # In that case its proposed split must settle first.
                    pending = addon.handovers.select_for_update().filter(status='PENDING').first()
                    if pending:
                        from .points_transfer_pending_handover_events import permits_other_refund
                        from .points_followup_handover_invalidation import permits_locked as permits_followup_invalidation
                        from .points_handover_refund_invalidation import permits_other_payment_locked as permits_team_invalidation
                        from .points_third_handover_invalidation import permits_for_round_locked as permits_third_invalidation
                        from .ordinary_pending_handover_refund import permits as ordinary_pending_permits
                        transfers = list(OrderOriginalShareTransfer.objects.filter(
                            previous_segment__attribution__order=order).select_related('previous_segment').order_by('pk'))
                        followup_allowed = permits_followup_invalidation(order, pending, payment)
                        ordinary_allowed = ordinary_pending_permits(order, payment, pending)
                        third_pending = third_active and permits_third_invalidation(order, pending)
                        if (pending.payment_id != payment.pk and segment_id != pending.source_segment_id
                                and not ordinary_allowed and not third_pending):
                            raise ValidationError('待确认接替的其他履约份额需先核对')
                        team_allowed = (needs_integrated_processing(order) and not transfers
                                        and permits_team_invalidation(order, payment.pk, allow_same_payment=True))
                        if (not ordinary_allowed and not team_allowed and not third_pending
                                and (not transfers or not (permits_other_refund(order, transfers, payment.pk)
                                                  or followup_allowed)
                                or (pending.payment_id == payment.pk and not followup_allowed))):
                            assert_no_pending_handover(addon)
                        if ordinary_allowed:
                            ordinary_pending_invalidations.add(pending.pk)
                        pending_handover_invalidations[pending.pk] = pending
                    deductions.append((addon, segment_id, take))
                    outstanding -= take
                if not outstanding:
                    break
            if not outstanding:
                break
        main_pending = 0
        if outstanding:
            from .points_service_payment import needs_integrated_processing
            if not needs_integrated_processing(order):
                from .addon_main_segment_deduction import remaining_locked, allocate_locked
                available_segments = remaining_locked(order, person.account_id)
                main_pending = min(outstanding, sum(amount for _, amount in available_segments))
                main_segment_parts[person.account_id] = allocate_locked(order, person.account_id, main_pending)
                outstanding -= main_pending
            else:
                main_pending = None
        if outstanding and main_pending is None:
            from .models import OrderAddonRefundParticipant
            prior_rows = OrderAddonRefundParticipant.objects.filter(proposal__payment__intent__order=order,
                proposal__refund__isnull=False, account_id=person.account_id)
            from .points_service_payment import needs_integrated_processing
            transferred_after_refund = (needs_integrated_processing(order) and order.refund_records.exists()
                and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
            if needs_integrated_processing(order) and order.refund_records.exists() and not transferred_after_refund:
                from .models import PointsNegotiationPosting
                main_postings = list(PointsNegotiationPosting.objects.filter(
                    points_refund__redemption__order=order).order_by('created_at', 'pk'))
                if len(main_postings) == 2:
                    matches = [item for item in main_postings if item.shares.filter(account_id=person.account_id).exists()]
                    main_posting = matches[0] if len(matches) == 1 else None
                else:
                    main_posting = main_postings[0] if len(main_postings) == 1 else None
                if not main_posting:
                    raise ValidationError('积分主单退款凭证不存在或陪玩份额异常')
                # The retained share already includes add-on deductions before
                # the main refund; subtract only subsequent deductions here.
                prior_rows = prior_rows.filter(proposal__refund__created_at__gt=main_posting.created_at)
            elif (production_addon and order.refund_records.count() == 1 and order.addon_intents.count() >= 2
                    and not OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).exists()):
                from .models import NegotiationExecution
                main_refund = order.refund_records.order_by('created_at', 'pk').first()
                if NegotiationExecution.objects.filter(refund=main_refund, shares__account_id=person.account_id).exists():
                    # This person's retained amount already incorporates any
                    # add-on deductions preceding the main refund.
                    prior_rows = prior_rows.filter(proposal__refund__created_at__gt=main_refund.created_at)
            prior = sum(prior_rows.values_list('main_pending_deduction', flat=True))
            income = _main_pending_income(order, person.account_id)
            # remaining_locked has already deducted the verified per-segment
            # add-on history for this transferred order.
            available = income if transferred_after_refund else income - prior if income else 0
            if available < 0:
                raise ValidationError('主单待结算收益待核对')
            main_pending = min(outstanding, available)
            outstanding -= main_pending
            if main_pending:
                from .addon_main_segment_deduction import allocate_locked
                main_segment_parts[person.account_id] = allocate_locked(order, person.account_id, main_pending)
        if main_pending is None:
            main_pending = 0
        profile = profiles.get(person.account_id)
        if not profile:
            raise ValidationError('陪玩资料不存在')
        provider_wallet = get_wallet(account_id=person.account_id, user_id=profile.user_id, for_update=True, create=False)
        available_wallet = provider_wallet.balance if provider_wallet and provider_wallet.is_active else 0
        wallet_deduction = min(outstanding, available_wallet)
        outstanding -= wallet_deduction
        deposit_deduction = min(outstanding, profile.deposit_paid)
        outstanding -= deposit_deduction
        if outstanding:
            raise ValidationError('陪玩待结算收益、钱包及押金不足，请先补足后退款')
        from .models import OrdinaryTransferFollowupRound
        from .points_third_addon_refund import active as third_refund_active
        ordinary_followup = (transferred_addon and not needs_integrated_processing(order)
            and OrdinaryTransferFollowupRound.objects.filter(order=order,
                execution__termination_processed_at__isnull=False).exists())
        if deposit_deduction and not wallet_deduction:
            requires_zero_wallet = ((production_addon and not needs_integrated_processing(order)
                and order.refund_records.count() in (0, 1)
                and (order.addon_intents.count() >= 2 or order.refund_records.count() == 1
                     or payment.handovers.filter(status='ACCEPTED').exists()))
                or ordinary_followup or third_refund_active(order, payment))
            has_zero_wallet = (provider_wallet and provider_wallet.is_active and provider_wallet.balance == 0
                and provider_wallet.account_id == person.account_id and provider_wallet.user_id == profile.user_id
                and profile.deposit_business_cycle and profile.deposit_business_cycle == profile.account.business_cycle)
            if requires_zero_wallet and not has_zero_wallet:
                raise ValidationError('退款时本人钱包或押金业务周期无法核对')
            # Same-seat return may need this evidence later. Cash deposits can exist without a wallet.
            if has_zero_wallet and (requires_zero_wallet or first_transfer_handover):
                zero_wallet_states[person.pk] = (provider_wallet.pk, person.account_id, profile.deposit_business_cycle,
                    provider_wallet.balance, provider_wallet.frozen_amount, provider_wallet.is_active)
        plans.append((person, profile, provider_wallet, deductions, main_pending, wallet_deduction, deposit_deduction))

    from .points_business_access import permits
    if permits(order, phase='refund', kind='addon'):
        from .points_third_addon_refund import active as third_refund_active
        from .models import OrderAddonFundingReleaseRun
        team_refund = (order.refund_records.count() <= 2 and not OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).exists() and OrderAddonFundingReleaseRun.objects.filter(
            handover__payment=payment, points_handover_snapshot__kind='SAME_TEAM_V1').exists())
        from .points_voluntary_handover_event import entry_matches
        voluntary_refund = bool(entry_matches(order, payment.pk) and payment.handovers.filter(
            kind='VOLUNTARY', status='ACCEPTED').exists())
        from .points_transfer_refund_access import permits as transfer_permits
        from .points_transfer_followup_addon_version import refund_preflight_active
        followup_refund = bool(order.refund_records.count() == 2 and transfer_permits(order, phase='refund')
            and refund_preflight_active(order, payment)
            and OrderAddonFundingReleaseRun.objects.filter(handover__payment=payment,
                handover__status='ACCEPTED', points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').exists())
        if (len(plans) != 1 and not (team_refund and len(plans) == 2) and not voluntary_refund
                and not followup_refund and not third_refund_active(order, payment)):
            raise ValidationError('积分加购退款仅支持一名受影响陪玩')
        if not third_active and other_team_payment and (len(plans) != 1 or plans[0][0].account_id != payment.intent.provider_id):
            raise ValidationError('其他加购退款须由实际履约人承担')
    if (production_addon or permits(order, phase='refund', kind='addon')) and any(
            deposit_deduction for _, _, _, _, _, _, deposit_deduction in plans):
        from wallet.refund_deposit import enabled as deposit_enabled
        from wallet.refund_recovery_order import enabled as recovery_enabled
        if not deposit_enabled() or not recovery_enabled():
            raise ValidationError('退款押金扣款或后续补差尚未启用')
    if (transferred_addon and (sum(person.burden for person in participants) != proposal.amount
            or order.addon_refunds.filter(rounding_surplus__gt=0).exists())):
        from .models import NegotiationExecution, OrderPointsFundingAllocation
        from django.db.models import Q
        before_main = not NegotiationExecution.objects.filter(Q(refund__order=order) | Q(zero_order=order),
            termination_processed_at__isnull=False).exists()
        has_funding = OrderPointsFundingAllocation.objects.filter(service_order=order).exists()
        if before_main and has_funding:
            from .models import OrderAddonFundingRelease
            allocations = OrderPointsFundingAllocation.objects.filter(service_order=order)
            releases = OrderAddonFundingRelease.objects.filter(allocation__in=allocations).select_related(
                'run__ordinary_transfer_revision')
            def supported_release(row):
                snapshot = row.run.ordinary_prestart_snapshot
                if isinstance(snapshot, dict) and snapshot.get('kind') in (
                        'ORDINARY_TRANSFER_PRE_MAIN_ADDON_V1', 'ORDINARY_TRANSFER_PRE_MAIN_PAYMENT_V1',
                        'ORDINARY_TRANSFER_PRE_MAIN_HANDOVER_V1'):
                    return True
                revision = row.run.ordinary_transfer_revision
                return bool(revision and revision.order_id == order.pk and revision.source_kind in
                            ('ZERO_AGREEMENT', 'ADDON_PAYMENT', 'ADDON_REFUND', 'ADDON_HANDOVER', 'TRANSFER'))
            from .ordinary_transfer_event_scope import transfers_at
            from django.core.exceptions import ValidationError as DjangoValidationError
            from django.utils import timezone
            try:
                transfers_at(order, timezone.now())
            except DjangoValidationError as exc:
                raise ValidationError('转单加购退款转单时序异常') from exc
            if (allocations.filter(receipt__usage_version__isnull=True).exists()
                    or any(not supported_release(row) for row in releases)):
                raise ValidationError('转单加购退款前的到账释放来源尚未接通')
        from .ordinary_transfer_addon_rounding import facts as verified_rounding
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            verified_rounding(order)
        except DjangoValidationError as exc:
            raise ValidationError('转单加购退款历史取整事实待核对') from exc
    from .addon_refund_cross_plan import require_ordinary_execution
    require_ordinary_execution(proposal, plans)
    from .addon_refund_share_allocation import build as build_share_allocations
    share_allocations = build_share_allocations(payment, participants,
        [(person, addon, segment_id, amount)
         for person, _, _, deductions, _, _, _ in plans for addon, segment_id, amount in deductions])
    if proposal.cross_pending_plan is not None:
        from .addon_refund_cross_plan import require_current
        require_current(proposal, participants)
    buyer = get_wallet(account_id=payment.intent.buyer_id, user_id=order.customer_id, for_update=True, create=False)
    if not buyer or not buyer.is_active:
        raise ValidationError('老板钱包不可用')
    from engagement.refunds import lock_refund_vip_operation
    vip_operation = lock_refund_vip_operation()
    allocations = {row.pk: row for row in payment.allocations.select_for_update().order_by('pk')}
    lots = {row.pk: row for row in PointFundingLot.objects.select_for_update().filter(
        pk__in={item.lot_id for item in allocations.values()}, account_id=payment.intent.buyer_id).order_by('pk')}
    if len(lots) != len({item.lot_id for item in allocations.values()}):
        raise ValidationError('原付款资金来源不存在')
    for part in proposal.source_plan:
        allocation = allocations[part['allocation_id']]
        lot, amount = lots[allocation.lot_id], part['amount_raw']
        lot.remaining += amount
        fields = ['remaining']
        if lot.kind == PointFundingLot.Kind.RECHARGE and allocation.kind == 'GIFT':
            lot.gift_remaining += amount
            fields.append('gift_remaining')
        lot.save(update_fields=fields)
        allocation.refunded_amount += amount
        allocation.save(update_fields=['refunded_amount'])
    before = buyer.balance
    buyer.balance += proposal.amount
    buyer.save(update_fields=['balance', 'updated_at'])
    buyer_tx = Transaction.objects.create(wallet=buyer, amount=proposal.amount, tx_type='REFUND',
        balance_before=before, balance_after=buyer.balance, operator=legacy_operator, operator_account=operator,
        remark=f'订单 {order.order_no} 加购退款')
    for person, profile, provider_wallet, deductions, main_pending, wallet_deduction, deposit_deduction in plans:
        provider_tx = None
        if wallet_deduction:
            before = provider_wallet.balance
            provider_wallet.balance -= wallet_deduction
            provider_wallet.save(update_fields=['balance', 'updated_at'])
            provider_tx = Transaction.objects.create(wallet=provider_wallet, amount=-wallet_deduction, tx_type='REFUND',
                balance_before=before, balance_after=provider_wallet.balance, operator=legacy_operator,
                operator_account=operator, remark=f'订单 {order.order_no} 加购退款承担')
        person.addon_pending_deduction = sum(row[2] for row in deductions)
        person.main_pending_deduction = main_pending
        person.wallet_deduction = wallet_deduction
        person.deposit_deduction = deposit_deduction
        person.provider_transaction = provider_tx
        person.save(update_fields=['addon_pending_deduction', 'main_pending_deduction', 'wallet_deduction',
                                   'deposit_deduction', 'provider_transaction'])
    refund = OrderAddonRefund.objects.create(order=order, payment=payment, proposal=proposal,
        operator=operator, legacy_operator=legacy_operator, amount=proposal.amount,
        addon_pending_deduction=sum(row.addon_pending_deduction for row in participants),
        main_pending_deduction=sum(row.main_pending_deduction for row in participants),
        wallet_deduction=sum(row.wallet_deduction for row in participants),
        deposit_deduction=sum(row.deposit_deduction for row in participants),
        rounding_surplus=sum(row.burden for row in participants) - proposal.amount,
        buyer_transaction=buyer_tx, provider_transaction=participants[0].provider_transaction if len(participants) == 1 else None)
    if zero_wallet_states:
        from .models import AddonRefundZeroWalletEvidence, AddonRefundParticipantZeroWalletEvidence
        if len(participants) == 1:
            wallet_id, account_id, cycle, balance, frozen, active = zero_wallet_states[participants[0].pk]
            AddonRefundZeroWalletEvidence.objects.create(refund=refund, wallet_id=wallet_id, account_id=account_id,
                business_cycle=cycle, balance=balance, frozen_amount=frozen, is_active=active)
        else:
            for person in participants:
                if person.pk not in zero_wallet_states:
                    continue
                wallet_id, account_id, cycle, balance, frozen, active = zero_wallet_states[person.pk]
                AddonRefundParticipantZeroWalletEvidence.objects.create(refund=refund, participant=person,
                    wallet_id=wallet_id, account_id=account_id, business_cycle=cycle, balance=balance,
                    frozen_amount=frozen, is_active=active)
    OrderAddonRefundEarningDeduction.objects.bulk_create([
        OrderAddonRefundEarningDeduction(refund=refund, payment=addon, participant=person,
                                         segment_id=segment_id, amount=amount)
        for person, _, _, deductions, _, _, _ in plans for addon, segment_id, amount in deductions])
    from .models import OrderAddonRefundShareAllocation
    OrderAddonRefundShareAllocation.objects.bulk_create([
        OrderAddonRefundShareAllocation(refund=refund, payment_id=addon_id, participant_id=person_id,
            share_id=share_id, refund_basis=basis, earning_deduction=income)
        for addon_id, person_id, share_id, basis, income in share_allocations])
    from .addon_refund_share_allocation import verify as verify_share_allocations
    verify_share_allocations(refund)
    if pending_handover_invalidations:
        from .models import PointsHandoverRefundInvalidation
        from django.core.exceptions import ValidationError as DjangoValidationError
        for pending in pending_handover_invalidations.values():
            if ((production_addon or transferred_addon) and not needs_integrated_processing(order)
                    and pending.pk in ordinary_pending_invalidations):
                from .ordinary_pending_handover_refund import capture
                from .points_handover_refund_invalidation import digest
            elif (needs_integrated_processing(order) and order.refund_records.count() >= 3
                  and pending.kind == 'STANDARD'):
                from .points_third_handover_invalidation import capture, digest
            elif (pending.kind == 'VOLUNTARY' or order.refund_records.count() == 2
                  or (needs_integrated_processing(order) and order.refund_records.count() == 1
                      and OrderOriginalShareTransfer.objects.filter(
                          previous_segment__attribution__order=order).exists())):
                from .points_followup_handover_invalidation import capture, digest
            else:
                from .points_handover_refund_invalidation import capture, digest
            pending.status = 'SUPERSEDED'
            pending.save(update_fields=['status'])
            from site_messages.addon_handover_proposal_notice import refresh_delivered
            refresh_delivered(pending)
            try:
                frozen = capture(pending, refund)
            except DjangoValidationError as exc:
                raise ValidationError('退款导致接替失效的实际责任待核对') from exc
            PointsHandoverRefundInvalidation.objects.create(handover=pending, refund=refund,
                snapshot=frozen, evidence_hash=digest(frozen))
    from .models import OrderAddonRefundMainSegmentDeduction
    OrderAddonRefundMainSegmentDeduction.objects.bulk_create([
        OrderAddonRefundMainSegmentDeduction(participant=person, segment_id=segment_id, amount=amount)
        for person, _, _, _, _, _, _ in plans
        for segment_id, amount in main_segment_parts.get(person.account_id, [])])
    if transferred_addon and not needs_integrated_processing(order):
        from .addon_refund_cross_plan import invalidate_changed_ordinary
        changed_payments = {refund.payment_id} | set(refund.earning_deductions.values_list('payment_id', flat=True))
        invalidate_changed_ordinary(order, changed_payments=changed_payments,
            changed_accounts={person.account_id for person in participants}, event_kind='ADDON_REFUND',
            event_id=refund.pk, actor=operator, legacy_actor=legacy_operator)
    from contextlib import nullcontext
    from .ordinary_refunded_team_handover import posting_refund
    scope = posting_refund(refund) if production_addon and not needs_integrated_processing(order) else nullcontext()
    with scope:
        for person, profile, _, _, _, _, deposit_deduction in plans:
            if deposit_deduction:
                from wallet.refund_deposit import charge_refund_deposit
                try:
                    charge_refund_deposit(refund=refund, profile=profile, amount=deposit_deduction,
                                          operator=legacy_operator, operator_account=operator)
                except ValueError as exc:
                    raise ValidationError(str(exc)) from exc
    from .addon_consumption import reverse_addon_consumption
    reverse_addon_consumption(refund, vip_operation=vip_operation)
    from .negotiation_zero import ordinary_funding_enabled
    if ordinary_funding_enabled(order):
        # The receipt-local RELEASE verifier runs inside this transaction.
        # Persist its real staff audit before that verifier, so rollback still
        # removes both the audit and the refund together on failure.
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
            operator_name=(operator.nickname or operator.username)[:150], method='POST',
            path=f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/refund-proposals/{proposal.pk}/execute/',
            resource='addon-refund', object_id=str(refund.pk),
            request_body={'order_id': order.pk, 'payment_id': payment.pk, 'proposal_id': proposal.pk,
                          'amount': refund.amount}, status_code=200)
    from .addon_funding_release import record_addon_funding_release
    from .ordinary_post_refund_transfer import refresh_for_event
    from .models import OrdinaryTransferFollowupRound
    ordinary_followup_refund = (not needs_integrated_processing(order) and transferred_addon
        and OrdinaryTransferFollowupRound.objects.filter(order=order,
            execution__termination_processed_at__lt=refund.created_at).exists())
    from .points_funding_evidence import ordinary_addon_refund_read_scope
    if ordinary_direct_paid_snapshot is not None:
        from .ordinary_addon_paid_refund import posting_scope as ordinary_paid_posting_scope
        paid_scope = ordinary_paid_posting_scope(refund, ordinary_direct_paid_snapshot)
    elif ordinary_refunded_paid_snapshot is not None:
        from .ordinary_refunded_addon_paid_revision import posting_scope as ordinary_refunded_paid_scope
        paid_scope = ordinary_refunded_paid_scope(refund, ordinary_refunded_paid_snapshot)
    else:
        paid_scope = nullcontext()
    with ordinary_addon_refund_read_scope(order.pk, refund.pk), paid_scope:
        if ordinary_followup_refund:
            record_addon_funding_release(order, refund)
        elif not refresh_for_event(order, source_kind='ADDON_REFUND', source_id=refund.pk):
            record_addon_funding_release(order, refund)
        if ordinary_direct_paid_snapshot is not None:
            from .ordinary_addon_paid_refund import record_locked as record_direct_paid
            paid_record = record_direct_paid(refund, ordinary_direct_paid_snapshot)
            from .addon_funding_release import verify_run as verify_funding_run
            from .ordinary_addon_paid_receipt import verify_run as verify_paid_run
            verify_funding_run(paid_record.run, _skip_usage=True)
            verify_paid_run(paid_record.run)
        if ordinary_refunded_paid_snapshot is not None:
            from .models import OrderAddonFundingReleaseRun, OrderPointsFundingAllocation
            from .ordinary_refunded_addon_paid_revision import record_posting_locked, advance_locked
            run = OrderAddonFundingReleaseRun.objects.get(refund=refund)
            revision = record_posting_locked(order, refund, run, ordinary_refunded_paid_snapshot)
            from .ordinary_after_main_addon_receipt import verified_release
            receipt_id = OrderPointsFundingAllocation.objects.filter(
                pk__in=[item[0] for item in run.allocation_snapshot]).order_by('receipt_id').values_list(
                    'receipt_id', flat=True).first()
            if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is not True:
                verified_release(run, None, order_locked=True)
            else:
                verified_release(run, receipt_id)
            from .ordinary_refunded_addon_paid_receipt import verify as verify_refunded_paid
            verify_refunded_paid(run)
            advance_locked(order, revision)
    if third_active:
        from .addon_refund_cross_plan import invalidate_changed_third
        invalidate_changed_third(order, refund, actor=operator, legacy_actor=legacy_operator)
    if (needs_integrated_processing(order) and order.refund_records.count() == 1
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        from .points_transfer_addon_liability import record_locked as record_liability
        record_liability(order, refund)
        from .points_transfer_addon_liability import first_refund_active
        if first_refund_active(order, payment.pk):
            from .models import PointsHandoverRefundInvalidation
            from .points_followup_handover_invalidation import verify as verify_invalidation
            records = list(PointsHandoverRefundInvalidation.objects.filter(refund=refund,
                snapshot__kind='TRANSFER_FIRST_INVALIDATION_V1').select_related(
                    'handover__payment__intent', 'refund__payment__intent').order_by('pk'))
            if {row.handover_id for row in records} != set(pending_handover_invalidations):
                raise ValidationError('本次退款缺少待接替方案失效记录')
            for record in records:
                verify_invalidation(record)
    if (needs_integrated_processing(order) and order.refund_records.count() == 2
            and refund_preflight_active(order, payment)):
        from .models import OrderPointsTransferFollowupFundingRevision, PointsHandoverRefundInvalidation
        from .points_transfer_addon_liability import record_locked as record_liability
        from .points_followup_handover_invalidation import verify as verify_invalidation
        followup = OrderPointsTransferFollowupFundingRevision.objects.filter(
            posting__points_refund__redemption__order=order).first()
        frozen = followup.pending_handover_snapshot if followup else None
        preserved = {item.get('handover_id') for item in frozen if isinstance(item, dict)} if isinstance(frozen, list) else set()
        affected = preserved.intersection(pending_handover_invalidations)
        repeated = [item for item in frozen if isinstance(item, dict) and item.get('payment_id') == payment.pk
                    and item.get('handover_id') not in pending_handover_invalidations
                    and PointsHandoverRefundInvalidation.objects.filter(
                        handover_id=item.get('handover_id'), handover__payment=payment,
                        refund__payment=payment, refund__created_at__lt=refund.created_at).exists()] \
                    if isinstance(frozen, list) else []
        if affected or repeated:
            liabilities = record_liability(order, refund)
            by_payment = {item.payment_id: item for item in liabilities}
            for handover_id in affected:
                pending = pending_handover_invalidations[handover_id]
                record = PointsHandoverRefundInvalidation.objects.filter(handover_id=handover_id,
                    refund=refund).select_related('handover__payment__intent', 'refund__payment__intent').first()
                if not record or pending.payment_id not in by_payment:
                    raise ValidationError('二次退款后的接替失效缺少真实责任版本')
                verify_invalidation(record)
            for item in repeated:
                liability = by_payment.get(payment.pk)
                prior = PointsHandoverRefundInvalidation.objects.filter(handover_id=item['handover_id'],
                    refund__payment=payment, refund__created_at__lt=refund.created_at).select_related(
                        'handover__payment__intent', 'refund__payment__intent').first()
                if not liability or liability.transfer_revision_id != item.get('transfer_revision_id') or not prior:
                    raise ValidationError('再次退款缺少原待交责任和失效事实')
                verify_invalidation(prior)
                for pending in pending_handover_invalidations.values():
                    if pending.payment_id != payment.pk:
                        continue
                    current = PointsHandoverRefundInvalidation.objects.filter(handover=pending, refund=refund).select_related(
                        'handover__payment__intent', 'refund__payment__intent').first()
                    if not current:
                        raise ValidationError('再次退款缺少当前接替方案失效事实')
                    verify_invalidation(current)
    if ordinary_paid_snapshot is not None:
        from .ordinary_paid_addon_refund import record_locked as record_ordinary_paid, advance_locked
        paid_evidence = record_ordinary_paid(refund, ordinary_paid_snapshot)
        advance_locked(order, paid_evidence)
    if paid_before is not None:
        from .points_voluntary_paid_evidence import record_locked
        paid_evidence = record_locked(refund, paid_before)
        if (paid_evidence and order.status == order.Status.IN_SERVICE
                and refund_preflight_active(order, payment)):
            from .points_followup_paid_handover import transition_refund_in_service_locked
            transition_refund_in_service_locked(order, paid_evidence)
    if needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).exists():
        from .points_transfer_zero_addon_settlement import advance_after_refund_locked
        advance_after_refund_locked(order)
    if points_unaffected_paid and not paid_followup_event:
        from .points_zero_followup import reconcile_unaffected_settlement_locked
        reconcile_unaffected_settlement_locked(order)
    if needs_integrated_processing(order) and order.refund_records.count() < 3 and payment.handovers.exists():
        from .models import OrderPointsFundingAllocation
        from django.db.models import Q
        if not OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order)).exists():
            from .points_addon_handover_refund_facts import funding_facts, post_followup_facts
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                if order.refund_records.count() == 2:
                    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
                        funding_facts(refund, receipt_id=None)
                    else:
                        post_followup_facts(refund, receipt_id=None)
                else:
                    funding_facts(refund, receipt_id=None)
            except DjangoValidationError as exc:
                raise ValidationError('无到账加购退款资金事实待核对') from exc
    from site_messages.addon_refund_notice import record_all as record_refund_notices
    record_refund_notices(refund)
    from wallet.notifier import notify_wallet_update
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_wallet_update(buyer))
    for person, _, provider_wallet, _, _, _, _ in plans:
        if person.provider_transaction_id:
            transaction.on_commit(lambda wallet=provider_wallet: notify_wallet_update(wallet))
    transaction.on_commit(lambda: notify_order_update(order))
    return refund
