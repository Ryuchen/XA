"""Verification of whole-order funding and its underlying evidence."""
import json
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction
from users.models import PassRefundRepaymentReceipt
from .ordinary_addon_production import eligible as _ordinary_addon_eligible
from .models import Order, OrderPointsFundingAction, OrderPointsFundingAllocation
from .points_checkout_test import require_order_funding_access, ordinary_refund_production_order
from .points_funding_plan import PointsFundingPlanError, plan_verified_order

_ordinary_handover_read = ContextVar('ordinary_handover_funding_read', default=None)
_ordinary_addon_refund_read = ContextVar('ordinary_addon_refund_funding_read', default=None)


@contextmanager
def ordinary_handover_read_scope(order_id, payment_id, target_id=None, handover_id=None):
    token = _ordinary_handover_read.set((order_id, payment_id, target_id, handover_id))
    try:
        yield
    finally:
        _ordinary_handover_read.reset(token)


@contextmanager
def ordinary_addon_refund_read_scope(order_id, refund_id):
    token = _ordinary_addon_refund_read.set((order_id, refund_id))
    try:
        yield
    finally:
        _ordinary_addon_refund_read.reset(token)


@dataclass(frozen=True)
class VerifiedOrderFunding:
    required: int
    allocated: int
    remaining: int
    fully_funded: bool


@dataclass(frozen=True)
class VerifiedOrderFundingWithAllocations(VerifiedOrderFunding):
    # (allocation ID, receipt ID, net amount), in original allocation order.
    net_allocations: tuple[tuple[int, int, int], ...]


def verified_order_allocations_locked(order, *, pending_followup_posting_id=None, allocation_ids=None,
                                      _reservation_usage=False, _main_refund_usage=False, _addon_refund_usage=False,
                                      _transfer_refund_usage=False):
    """Verify each tenant allocation and its platform receipt authorization."""
    if not transaction.get_connection().in_atomic_block or current_store() is None:
        raise PointsFundingPlanError('补资凭证须在门店事务内核对')
    order = Order.objects.select_for_update().filter(pk=getattr(order, 'pk', None)).first()
    if not order:
        raise PointsFundingPlanError('订单不存在')
    require_order_funding_access(order)
    return _verified_allocations_for_locked_order(order, pending_followup_posting_id=pending_followup_posting_id,
                                                  allocation_ids=allocation_ids, _reservation_usage=_reservation_usage,
                                                  _main_refund_usage=_main_refund_usage,
                                                  _addon_refund_usage=_addon_refund_usage,
                                                  _transfer_refund_usage=_transfer_refund_usage)


def _verified_allocations_for_locked_order(order, *, pending_followup_posting_id=None, allocation_ids=None,
                                           _reservation_usage=False, _main_refund_usage=False, _addon_refund_usage=False,
                                           _transfer_refund_usage=False, ordinary_pending_handover_id=None):
    # Caller holds and authorizes the tenant order before locking receipts.
    if not transaction.get_connection().in_atomic_block or current_store() is None:
        raise PointsFundingPlanError('补资凭证须在门店事务内核对')
    from stores.models import PlatformPointsFundingOperation
    from stores.platform_pass_repayment import RepaymentError, _claimed_receipt, _sha
    from stores.platform_points_funding import _checked_points_action_for_locked_order, _verified_receipt
    owner = Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)
    if allocation_ids is not None:
        if (not isinstance(allocation_ids, (list, tuple, set))
                or any(type(pk) is not int or pk <= 0 for pk in allocation_ids)
                or len(set(allocation_ids)) != len(allocation_ids)):
            raise PointsFundingPlanError('补资快照编号无效')
        owner &= Q(pk__in=allocation_ids)
    # Lock every receipt before allocation rows, in global receipt-ID order.
    # Keyset pages avoid both a large IN clause and a hard allocation cap.
    receipt_ids, receipts, last_receipt = set(), {}, 0
    while True:
        page = list(OrderPointsFundingAllocation.objects.filter(owner, receipt_id__gt=last_receipt)
                    .order_by('receipt_id').values_list('receipt_id', flat=True).distinct()[:256])
        if not page:
            break
        receipt_ids.update(page)
        for row in PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=page).order_by('pk'):
            receipts[row.pk] = row
        last_receipt = page[-1]
    if len(receipts) != len(receipt_ids):
        raise PointsFundingPlanError('到账记录缺失，请先核对')
    from django.core.exceptions import ValidationError as DjangoValidationError
    from users.receipt_usage_guard import capture_allocation_usage_locked, reject_unintegrated_locked_receipt
    verified_usage = {}
    try:
        for receipt in receipts.values():
            if (getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
                    and receipt.usage_version is None):
                raise DjangoValidationError('到账用途账本尚未建立')
            formal_allowed = False
            if receipt.usage_version is not None and getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True:
                from .points_transfer_refund_access import permits
                if any(permits(order, phase=phase) for phase in ('transfer', 'report', 'refund', 'settlement')):
                    formal_allowed = True
                from .points_business_access import permits as service_permits
                if any(service_permits(order, phase=phase, kind='service') for phase in ('assignment', 'funding', 'report', 'refund', 'settlement')):
                    formal_allowed = True
                if any(service_permits(order, phase=phase, kind='addon') for phase in ('payment', 'handover', 'funding', 'refund', 'report', 'settlement')):
                    formal_allowed = True
                from .points_checkout_test import require_ordinary_funding_order
                if not formal_allowed:
                    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
                    formal_allowed = ordinary_transfer_permits(order)
                    scope = _ordinary_handover_read.get()
                    if not formal_allowed and (ordinary_pending_handover_id is not None or scope):
                        from .models import OrderAddonHandover, OrderAddonPayment
                        pending = OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
                        scoped_payment = (scope[1] if scope and scope[0] == order.pk and
                            OrderAddonPayment.objects.filter(pk=scope[1], intent__order=order).exists() else None)
                        own = pending.filter(payment_id=scoped_payment) if scoped_payment else pending.filter(
                            pk=ordinary_pending_handover_id)
                        matching = (ordinary_pending_handover_id is None or
                                    own.filter(pk=ordinary_pending_handover_id).exists())
                        others_allowed = bool(scoped_payment) or not pending.exclude(
                            pk=ordinary_pending_handover_id).exists()
                        independent = False
                        if scoped_payment:
                            from .addon_handover import current_responsibilities
                            from .ordinary_pending_refund_scope import independent as independent_refunds
                            payment = OrderAddonPayment.objects.get(pk=scoped_payment, intent__order=order)
                            _, responsibilities = current_responsibilities(payment)
                            accounts = {item['provider_id'] for item in responsibilities if item['is_current']}
                            target = scope[2]
                            if scope[3]:
                                pair = OrderAddonHandover.objects.filter(pk=scope[3], payment=payment,
                                    status__in=('PENDING', 'ACCEPTED')).values_list(
                                        'source_segment__provider_account_id',
                                        'target_segment__provider_account_id').first()
                                if pair:
                                    accounts.add(pair[0])
                                    target = pair[1]
                            if target:
                                accounts.add(target)
                            if len(accounts) >= 2:
                                independent = independent_refunds(order, changed_payment_ids={scoped_payment},
                                    changed_accounts=accounts, allow_same_payment=True)
                        if matching and own.count() <= 1 and others_allowed and independent:
                            formal_allowed = ordinary_transfer_permits(order, allow_addon_handover=True,
                                                                       allow_addon_refund=True)
                    refund_scope = _ordinary_addon_refund_read.get()
                    if not formal_allowed and refund_scope and refund_scope[0] == order.pk:
                        from .models import OrderAddonHandover, OrderAddonPayment, OrderAddonRefund
                        refund = OrderAddonRefund.objects.filter(pk=refund_scope[1], order=order,
                            proposal__status='ACCEPTED').first()
                        if refund:
                            affected = set(refund.earning_deductions.values_list('payment_id', flat=True))
                            affected.add(refund.payment_id)
                            pending_affected = OrderAddonHandover.objects.filter(
                                payment__intent__order=order, payment_id__in=affected, status='PENDING').exists()
                            from .ordinary_pending_refund_scope import independent as independent_refunds
                            accounts = set(refund.proposal.participants.values_list('account_id', flat=True))
                            if (accounts and not pending_affected and independent_refunds(order,
                                    changed_payment_ids=affected, changed_accounts=accounts)):
                                formal_allowed = ordinary_transfer_permits(order, allow_addon_handover=True,
                                                                           allow_addon_refund=True)
                if not formal_allowed:
                    try:
                        require_ordinary_funding_order(order)
                    except ValidationError:
                        pass
                    else:
                        formal_allowed = True
            if receipt.usage_version is not None and (_reservation_usage or _main_refund_usage
                                                       or _addon_refund_usage or _transfer_refund_usage or formal_allowed):
                from .points_checkout_test import require_isolated_checkout
                if not formal_allowed:
                    require_isolated_checkout()
                from .points_receipt_usage import eligible as main_usage_eligible, followup_eligible, transfer_refund_eligible
                reservation_allowed = (_reservation_usage and getattr(settings, 'SERVICE_POINTS_RESERVATION_CANCEL_USAGE_TESTING', False))
                main_allowed = _main_refund_usage and (main_usage_eligible(order) or followup_eligible(order))
                from .points_addon_receipt_usage import eligible as addon_usage_eligible, reuse_history_locked
                addon_allowed = _addon_refund_usage and addon_usage_eligible(order)
                transfer_allowed = _transfer_refund_usage and transfer_refund_eligible(order)
                if not (reservation_allowed or main_allowed or addon_allowed or transfer_allowed or formal_allowed):
                    raise DjangoValidationError('该到账用途试验尚未开放')
                with reuse_history_locked(order, {receipt.pk}):
                    verified_usage[receipt.pk] = capture_allocation_usage_locked(receipt)
            else:
                reject_unintegrated_locked_receipt(receipt)
    except DjangoValidationError as exc:
        raise PointsFundingPlanError('到账用途账本尚未接入订单核对') from exc
    allocations, last_allocation = [], 0
    while True:
        page = list(OrderPointsFundingAllocation.objects.select_for_update().filter(
            owner, pk__gt=last_allocation).order_by('pk')[:256])
        if not page:
            break
        allocations.extend(page)
        last_allocation = page[-1].pk
    if {row.receipt_id for row in allocations} != receipt_ids:
        raise PointsFundingPlanError('补资记录已变化，请重新核对')
    if allocation_ids is not None and {row.pk for row in allocations} != set(allocation_ids):
        raise PointsFundingPlanError('补资快照记录缺失')
    for allocation in allocations:
        key = allocation.request_key
        try:
            if not key.startswith('points:') or len(key) != 39:
                raise ValueError('invalid operation key')
            operation_id = UUID(hex=key[7:])
        except (ValueError, TypeError, AttributeError):
            raise PointsFundingPlanError('补资操作编号无效') from None
        operation = (PlatformPointsFundingOperation.objects.using('default').select_related('claim')
                     .filter(pk=operation_id, claim__store__slug=current_store().slug).first())
        receipt = receipts[allocation.receipt_id]
        if (not operation or not operation.completed_at or operation.tenant_action_id is None
                or operation.rejected_at or operation.voided_at
                or operation.claim.tenant_receipt_id != receipt.pk
                or not receipt.verified_at or not receipt.verified_by):
            raise PointsFundingPlanError('补资平台记录未完成，请先核对')
        try:
            _claimed_receipt(operation.claim, current_store().slug, receipt)
            usage = verified_usage.get(receipt.pk)
            _verified_receipt(operation.claim, receipt, verified_usage=usage)
            action = _checked_points_action_for_locked_order(operation, receipt, order.pk, allocation.amount,
                                            pending_followup_posting_id=pending_followup_posting_id, verified_usage=usage)
        except RepaymentError as exc:
            raise PointsFundingPlanError('补资凭证不一致，请先核对') from exc
        digest = _sha(json.dumps([str(operation.claim_id), order.pk, allocation.amount], separators=(',', ':')))
        if (not action or action.pk != operation.tenant_action_id or action.allocation_id != allocation.pk
                or not OrderPointsFundingAction.objects.filter(pk=action.pk, allocation=allocation).exists()
                or operation.payload_digest != digest):
            raise PointsFundingPlanError('补资操作结果不一致，请先核对')
    return allocations


def _verified_prestart_cancel_funding(order):
    if (order.status != Order.Status.CANCELLED or order.payment_status != Order.PaymentStatus.REFUNDED):
        return None
    refunds = list(order.refund_records.order_by('pk')[:2])
    if len(refunds) != 1 or not isinstance(refunds[0].benefit_effects, dict):
        return None
    refund = refunds[0]
    effects = refund.benefit_effects
    assigned = effects.get('points_assigned_prestart_cancel') is True
    reserved = effects.get('reservation_points_prestart_cancel') is True
    if effects.get('points_prestart_cancel_funding') is not True or not (assigned or reserved):
        return None

    from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
    from wallet.models import Transaction
    from .models import (OrderOriginalShareTransfer, OrderParticipationSegment, OrderPointsSettlement,
                         OrderStatusLog, Reservation, ReservationStatusLog)
    from .points_prestart_cancel_funding import verify_replay_locked
    from .points_service_refund import verify_refund_history_locked

    if (order.in_service_at or order.completed_at or order.settled_at or order.consumption_confirmed_at
            or not order.refunded_at or order.aftersales.exists() or order.addon_intents.exists()
            or order.providers.filter(settled_at__isnull=False).exists()
            or OrderParticipationSegment.objects.filter(attribution__order=order, started_at__isnull=False).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderPointsSettlement.objects.filter(order=order).exists()
            or Transaction.objects.filter(order=order, status='SUCCESS', amount__gt=0,
                                          tx_type__in=('INCOME', 'GIFT', 'SHOP_INCOME')).exists()):
        raise PointsFundingPlanError('取消订单的履约或收益凭证待核对')
    booking = Reservation.objects.filter(order=order).first()
    if reserved:
        if (assigned or not booking or booking.status != Reservation.Status.CONVERTED
                or not booking.confirmed_at or not booking.converted_at
                or not booking.confirmed_at <= booking.converted_at <= order.refunded_at
                or booking.customer_account_id != order.customer_account_id or booking.customer_id != order.customer_id
                or booking.customer_business_cycle != order.business_cycle or booking.service_id != order.service_id
                or booking.required_provider_count != order.required_provider_count):
            raise PointsFundingPlanError('原预约和已退款订单的转单凭证待核对')
        conversions = list(booking.status_logs.filter(action=ReservationStatusLog.Action.CONVERT).order_by('pk')[:2])
        if len(conversions) != 1:
            raise PointsFundingPlanError('预约转单状态记录待核对')
        conversion = conversions[0]
        if (conversion.from_status != Reservation.Status.CONFIRMED or conversion.to_status != Reservation.Status.CONVERTED
                or conversion.reason != f'转为订单：{order.order_no}' or not conversion.operator_account_id
                or not booking.converted_at <= conversion.created_at <= order.refunded_at
                or not order.status_logs.filter(action=OrderStatusLog.Action.CREATE, reason=f'预约转单：{booking.reservation_no}',
                    operator_account_id=conversion.operator_account_id, created_at__lte=booking.converted_at).exists()):
            raise PointsFundingPlanError('预约与订单的原转单记录待核对')
    elif booking:
        raise PointsFundingPlanError('取消订单的预约归属待核对')
    try:
        _verified_allocations_for_locked_order(order)
        verify_replay_locked(refund)
        proof = verify_refund_history_locked(refund.pk)
    except (DjangoValidationError, ObjectDoesNotExist, ValidationError) as exc:
        raise PointsFundingPlanError('取消订单的补资释放凭证待核对') from exc
    if not proof.is_final:
        raise PointsFundingPlanError('取消订单的全额退款凭证待核对')
    return VerifiedOrderFunding(required=0, allocated=0, remaining=0, fully_funded=True)


def verify_order_funding(order_id, *, allow_finalized=False, allow_unstarted=False, allow_followup_recorded=False,
                         include_prestart_cancelled=False, include_net_allocations=False,
                         _reservation_usage=False, _main_refund_usage=False, _transfer_refund_usage=False):
    """Check durable tenant and platform evidence under receipt/order locks.

    Locks follow allocation's order-then-receipt order. A changed set of
    receipts is rejected, not silently counted as available. This function
    neither releases earnings nor makes a reservation across transactions.
    The optional net rows are for consumption under the caller's existing
    order transaction, where all source locks survive this savepoint.
    """
    if type(order_id) is not int or order_id <= 0 or current_store() is None:
        raise PointsFundingPlanError('订单或门店无效')
    if include_net_allocations and not transaction.get_connection().in_atomic_block:
        raise PointsFundingPlanError('逐笔净补资须在原订单事务内核对')
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            raise PointsFundingPlanError('订单不存在')
        require_order_funding_access(order)
        if include_prestart_cancelled:
            closed_funding = _verified_prestart_cancel_funding(order)
            if closed_funding is not None:
                return closed_funding
        from .points_service_payment import needs_integrated_processing
        from .models import OrderOriginalShareTransfer
        from .negotiation_followup_accounting import marked_followup_id
        followup_id = marked_followup_id(order)
        if followup_id:
            from .models import NegotiationFollowupLink
            from .ordinary_addon_followup_settlement import is_followup, funding_status_locked
            from .settlement import SettlementError
            link = NegotiationFollowupLink.objects.filter(pk=followup_id, order=order).first()
            if link and is_followup(link):
                if order.settled_at and not allow_finalized:
                    raise PointsFundingPlanError('订单已结算，不能继续分配补资')
                try:
                    return funding_status_locked(order, link)[1]
                except (SettlementError, ValidationError) as exc:
                    raise PointsFundingPlanError('追加售后的整单补资凭证待核对') from exc
        refunded_points = needs_integrated_processing(order) and order.refund_records.exists()
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        if refunded_points and transferred and order.refund_records.count() == 3:
            from .points_third_refund_settlement_plan import build_locked
            from .settlement import SettlementError
            try:
                _, funding = build_locked(order.pk, allow_finalized=allow_finalized, funding_only=True,
                    _allow_addons=order.addon_intents.filter(payment__isnull=False).exists())
            except SettlementError as exc:
                raise PointsFundingPlanError('第三轮退款补资凭证待核对') from exc
            return VerifiedOrderFunding(funding.required_units, funding.allocated_units,
                                        funding.remaining_units, funding.remaining_units == 0)
        from .models import OrderTransferNegotiationEvidence
        first_zero = OrderTransferNegotiationEvidence.objects.filter(order=order, execution__zero_order=order,
            execution__termination_processed_at__isnull=False).select_related('execution').first()
        if not needs_integrated_processing(order) and transferred and (order.refund_records.exists() or first_zero):
            from .ordinary_transfer_capability import permits as ordinary_transfer_permits
            if ordinary_transfer_permits(order):
                from .models import OrdinaryPostRefundTransferRevision, OrdinaryTransferFollowupRound, OrderAddonPayment
                revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
                last_refund = order.refund_records.order_by('-created_at', '-pk').first()
                baseline = last_refund.created_at if last_refund else first_zero.execution.termination_processed_at
                last_round = (OrdinaryTransferFollowupRound.objects.filter(order=order, execution__isnull=False)
                              .select_related('execution').order_by('-sequence').first())
                if (revision and baseline and revision.created_at > baseline
                        and (not last_round or (last_round.execution.termination_processed_at
                                                and revision.created_at > last_round.execution.termination_processed_at))):
                    from .ordinary_post_refund_transfer import current_locked
                    from .addon_funding_release import addon_release_totals
                    try:
                        revision = current_locked(order)
                    except ValidationError as exc:
                        raise PointsFundingPlanError('后续转单资金版本待核对') from exc
                    allocations = _verified_allocations_for_locked_order(order)
                    releases = addon_release_totals(allocations)
                    allocated = sum(item.amount - releases[item.pk] for item in allocations)
                    required = revision.snapshot['funding']['required_units']
                    if allocated > required:
                        raise PointsFundingPlanError('后续转单多余补资尚未释放')
                    return VerifiedOrderFunding(required, allocated, max(required - allocated, 0), allocated >= required)
                if last_round:
                    from .ordinary_transfer_followup_funding import plan_locked as followup_plan
                    from .addon_funding_release import addon_release_totals
                    latest = OrdinaryTransferFollowupRound.objects.select_for_update().filter(
                        order=order, execution__isnull=False).order_by('-sequence').first()
                    if not latest:
                        raise PointsFundingPlanError('追加协商逐轮凭证缺失')
                    allocations = _verified_allocations_for_locked_order(order)
                    releases = addon_release_totals(allocations)
                    plan, _ = followup_plan(latest.pk, allocated=sum(
                        allocation.amount - releases[allocation.pk] for allocation in allocations))
                elif first_zero:
                    raise PointsFundingPlanError('首次零退款的资金版本缺失')
                elif OrderAddonPayment.objects.filter(intent__order=order).exists():
                    from .ordinary_transfer_addon_funding import plan_locked as ordinary_transfer_addon_plan
                    plan = ordinary_transfer_addon_plan(order.pk, allow_finalized=allow_finalized).combined
                else:
                    from .ordinary_transfer_refund_funding import plan_locked as ordinary_transfer_plan
                    plan = ordinary_transfer_plan(order.pk, allow_finalized=allow_finalized)
                if plan.release_units:
                    raise PointsFundingPlanError('转单退款后的多余补资尚未释放')
                return VerifiedOrderFunding(plan.required_units, plan.allocated_units,
                                            plan.remaining_units, plan.remaining_units == 0)
        from .negotiation_zero import ordinary_funding_enabled
        try:
            ordinary_zero = not needs_integrated_processing(order) and ordinary_funding_enabled(order)
        except ValidationError as exc:
            raise PointsFundingPlanError('零退款协商凭证待核对') from exc
        from .models import OrdinaryRefundedAddonPaidRevision
        paid_addon_pending = bool(not needs_integrated_processing(order)
            and OrdinaryRefundedAddonPaidRevision.objects.filter(settlement__order=order).exists()
            and _ordinary_addon_eligible(order, allow_open=True))
        if (not needs_integrated_processing(order) and (order.refund_records.exists() or ordinary_zero)
                and (getattr(settings, 'ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING', False)
                     or (ordinary_refund_production_order(order) or _ordinary_addon_eligible(order)
                         or paid_addon_pending or ordinary_zero))):
            from .ordinary_refunded_addon_funding import plan_locked
            # A funded double order may already have paid one provider while
            # another is still waiting for their own clock. Reconcile the paid
            # provider below before exposing the remaining funding position.
            plan = plan_locked(order.pk, _allow_settled=True, _allow_finalized=allow_finalized,
                               _allow_unconfirmed_addons=paid_addon_pending)
            if plan.releasable:
                raise PointsFundingPlanError('退款后多余补资尚未释放，请先核对')
            if order.providers.filter(settled_at__isnull=False).exists():
                from .ordinary_refunded_addon_settlement import verify_recorded_locked
                from .ordinary_refunded_addon_paid_revision import pending_locked
                from .settlement import SettlementError
                from django.core.exceptions import ValidationError as DjangoValidationError
                try:
                    if not pending_locked(order):
                        verify_recorded_locked(order)
                except (SettlementError, ValidationError, DjangoValidationError) as exc:
                    raise PointsFundingPlanError('退款后的个人结算凭证待核对') from exc
            return VerifiedOrderFunding(plan.required, plan.allocated, plan.remaining, plan.remaining == 0)
        if refunded_points:
            if transferred:
                if order.refund_records.count() == 2:
                    from .points_transfer_followup_funding import plan_locked
                    plan = plan_locked(order_id, allow_recorded=allow_followup_recorded,
                                       allow_finalized=allow_finalized and allow_followup_recorded,
                                       allow_in_service=order.status == Order.Status.IN_SERVICE)
                else:
                    from .points_transfer_refunded_funding import plan_refunded_transfer
                    plan = plan_refunded_transfer(order_id, allow_finalized=allow_finalized)
            else:
                if order.refund_records.count() == 2:
                    from .points_followup_funding import verify_followup_funding
                    return verify_followup_funding(order_id)
                from .points_refunded_funding import plan_refunded_order
                plan = plan_refunded_order(order_id, allow_finalized=allow_finalized)
        elif needs_integrated_processing(order):
            if transferred:
                from .points_transfer_funding import plan_points_transfer
                from .points_transfer_refund_access import addon_payment_read
                with addon_payment_read(order):
                    plan = plan_points_transfer(order_id, allow_recorded=allow_finalized,
                                                preview_unstarted=allow_unstarted).funding
            else:
                plan = plan_verified_order(order_id, allow_finalized=allow_finalized)
        else:
            from .service_funding_plan import plan_verified_ordinary_order
            plan = plan_verified_ordinary_order(order_id, allow_finalized=allow_finalized)
        if refunded_points:
            # The refund release already includes the add-ons present at its
            # posting time. Do not count them a second time. Until later add-on
            # changes have their own refund revision, require the facts to match.
            from .models import OrderPointsFundingReleaseEvidence
            evidence = OrderPointsFundingReleaseEvidence.objects.filter(
                posting__points_refund__redemption__order=order).first()
            from .points_refunded_funding import later_addon_runs
            if evidence and transferred:
                from .points_transfer_addon_events import later_runs as transfer_later_runs
                later_runs = transfer_later_runs(order, evidence.posting)
            else:
                later_runs = later_addon_runs(order, evidence.posting) if evidence else []
            followup_addon = None
            if transferred and order.refund_records.count() == 2:
                from .models import OrderPointsTransferFollowupFundingRevision
                from .points_transfer_followup_addon_version import latest_run_locked
                followup_revision = OrderPointsTransferFollowupFundingRevision.objects.filter(
                    posting__points_refund__redemption__order=order).first()
                if followup_revision:
                    followup_addon = latest_run_locked(order, followup_revision)
            from .models import PointsFollowupTransferRevision
            followup_transfer = PointsFollowupTransferRevision.objects.filter(order=order).first() if transferred else None
            if followup_addon or (followup_transfer and followup_transfer.snapshot.get('addon_history') is not None):
                # plan_locked verified the actual transfer and the latest event;
                # their funds already include these add-ons exactly once.
                pass
            elif evidence and (evidence.addon_funding_snapshot is not None or later_runs):
                from django.utils import timezone
                from .addon_funding_history import combined_at
                pending = {row.account_id: row.pending_deduction for row in evidence.posting.shares.all()}
                if transferred:
                    from .models import PointsPostRefundTransferRevision
                    from .points_transfer_refund_access import verification_scope
                    from .points_transfer_refund_evidence import _reconstruct_from_verified_facts
                    version = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
                    facts = version.snapshot['facts'] if version else evidence.posting.participants['facts']
                    last_transfer = version.transfer_id if version else facts['transfers'][-1]['id']
                    with verification_scope(order):
                        reconstructed = _reconstruct_from_verified_facts(order.pk, facts,
                            refund_id=evidence.posting.points_refund.refund_id, through_transfer_id=last_transfer)
                    segment_income = {line[0]: line[4] for line in reconstructed.segment_shares}
                    selected = set(evidence.posting.proposal.affected_segment_ids or [])
                    participants = evidence.posting.participants
                    by_id = ({item['id']: item for item in participants['facts']['segments']}
                             if isinstance(participants, dict) else
                             {item['segment_id']: item for item in participants})
                    for account, amount in pending.items():
                        chosen = [line[0] for line in reconstructed.segment_shares
                                  if line[1] == account and line[0] in by_id and by_id[line[0]]['started_at']
                                  and (not selected or line[0] in selected)]
                        if amount and len(chosen) != 1:
                            raise PointsFundingPlanError('退款主单收益须明确唯一履约段')
                        if amount:
                            segment_income[chosen[0]] -= amount
                    shares = [(line[0], line[1], line[2], line[3], segment_income[line[0]])
                              for line in reconstructed.segment_shares]
                else:
                    from .points_service_participation import service_snapshots
                    snapshots = service_snapshots(order)
                    shares = [(row.segment_id, row.segment.provider_account_id, row.incoming_basis, 0,
                               row.income_at_entry - pending.get(row.segment.provider_account_id, 0)) for row in snapshots]
                history = combined_at(order, plan, timezone.now(), segment_shares=shares)
                latest = later_runs[-1] if later_runs else None
                expected = (latest.refunded_main_snapshot['addon_history'] if latest
                            else evidence.addon_funding_snapshot)
                if history.get('addon_history') != expected:
                    raise PointsFundingPlanError('主单退款后的加购变化尚待重新核对')
            else:
                from .addon_funding import include_paid_addons
                plan = include_paid_addons(order, plan)
        else:
            from .addon_funding import include_paid_addons
            plan = include_paid_addons(order, plan)
        allocations = verified_order_allocations_locked(order, _reservation_usage=_reservation_usage,
                    _main_refund_usage=_main_refund_usage, _transfer_refund_usage=_transfer_refund_usage)
        transfer_releases = {}
        from .points_transfer_revision import revision_tables_exist
        if revision_tables_exist():
            from .models import OrderPointsTransferFundingRevision, OrderPointsTransferFundingRelease
            if refunded_points and transferred:
                from .models import PointsNegotiationPosting
                from .points_transfer_refund_evidence import verify_transfer_revisions_after_refund_locked
                if order.refund_records.count() == 2:
                    from .models import OrderPointsTransferFollowupFundingRevision
                    revision = OrderPointsTransferFollowupFundingRevision.objects.select_related('first_posting').filter(
                        posting__points_refund__redemption__order=order).first()
                    posting = revision.first_posting if revision else None
                else:
                    posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
                if not posting or not isinstance(posting.participants, (dict, list)):
                    raise PointsFundingPlanError('转单退款补资凭证缺失')
                try:
                    if (order.refund_records.count() == 2 and allow_followup_recorded
                            and isinstance(posting.participants, dict)):
                        from .points_transfer_refund_evidence import _verify_facts_locked, _verify_revisions_from_verified_facts
                        _, facts, _ = _verify_facts_locked(order.pk, posting.participants, boundary=posting.created_at)
                        transfer_releases = _verify_revisions_from_verified_facts(
                            order.pk, facts, refund_id=posting.points_refund.refund_id)
                    else:
                        transfer_releases = verify_transfer_revisions_after_refund_locked(
                            order.pk, posting.participants, refund_id=posting.points_refund.refund_id)
                except ValidationError as exc:
                    raise PointsFundingPlanError('退款前转单补资凭证待核对') from exc
            elif (OrderPointsTransferFundingRevision.objects.filter(order=order).exists()
                    or (getattr(settings, 'SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING', False)
                        and allocations and not refunded_points and needs_integrated_processing(order)
                        and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())):
                from .points_transfer_revision import verify_revisions_locked
                from .points_transfer_refund_access import addon_payment_read
                with addon_payment_read(order):
                    transfer_releases, _ = verify_revisions_locked(order,
                        locked_receipt_ids={row.receipt_id for row in allocations})
            elif OrderPointsTransferFundingRelease.objects.filter(allocation__in=allocations).exists():
                raise PointsFundingPlanError('转单补资释放凭证缺失')
        from .addon_funding_release import addon_release_totals
        from .points_addon_receipt_usage import reuse_history_locked
        try:
            with (reuse_history_locked(order, {row.receipt_id for row in allocations})
                  if include_net_allocations else nullcontext()):
                addon_releases = addon_release_totals(allocations)
        except ValidationError as exc:
            raise PointsFundingPlanError('加购补资释放凭证待核对') from exc
        total = 0
        net_allocations = []
        for allocation in allocations:
            released = 0
            if refunded_points:
                from .models import OrderPointsFundingRelease
                released = sum(OrderPointsFundingRelease.objects.filter(allocation=allocation).values_list('amount', flat=True))
                if released > allocation.amount:
                    raise PointsFundingPlanError('补资释放金额超过原分配')
            released += transfer_releases.get(allocation.pk, 0)
            released += addon_releases[allocation.pk]
            if refunded_points and transferred:
                from .models import PointsPostRefundTransferRelease, PointsFollowupTransferRelease
                released += sum(PointsPostRefundTransferRelease.objects.filter(
                    allocation=allocation).values_list('amount', flat=True))
                released += sum(PointsFollowupTransferRelease.objects.filter(
                    allocation=allocation).values_list('amount', flat=True))
            if released > allocation.amount:
                raise PointsFundingPlanError('补资释放金额超过原分配')
            net_amount = allocation.amount - released
            total += net_amount
            if include_net_allocations:
                net_allocations.append((allocation.pk, allocation.receipt_id, net_amount))
        if total > plan.additional_store_funding_units:
            raise PointsFundingPlanError('补资超出订单实际缺口，请先核对')
        remaining = plan.additional_store_funding_units - total
        if include_net_allocations:
            return VerifiedOrderFundingWithAllocations(plan.additional_store_funding_units, total, remaining,
                                                       remaining == 0, tuple(net_allocations))
        return VerifiedOrderFunding(plan.additional_store_funding_units, total, remaining, remaining == 0)
