"""Internal test-only allocation of a verified shared receipt to an order.

The platform endpoint is restricted to isolated tests and uses recoverable
operation IDs across the two databases; isolated settlement verifies these
records before use. This internal writer does not perform platform authorization.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum

from stores.db import transaction
from users.models import PassRefundRepaymentReceipt
from users.pass_repayment import _amount, _context, points_funding_table_exists
from .ordinary_addon_production import eligible as _ordinary_addon_eligible
from .models import Order, OrderPointsFundingAllocation, OrderPointsRedemption
from .points_checkout_test import require_order_funding_access, ordinary_refund_production_order
from .points_funding_allocation import PointsFundingAllocationError, plan_shared_receipt_allocation
from .points_funding_plan import PointsFundingPlanError, plan_verified_order


def _verify_replay(existing, receipt, *, order_id, amount, actor):
    if not receipt.verified_at or not receipt.verified_by:
        raise ValidationError('到账尚未核实')
    if (existing.receipt_id, existing.funding_order_id, existing.amount, existing.allocated_by) != (
            receipt.pk, order_id, amount, actor):
        raise ValidationError('请求编号已用于其他积分补资')
    from users.receipt_usage_guard import isolated_allocation_state_locked
    ledger = isolated_allocation_state_locked(receipt)
    if ledger:
        points_used, pass_used = (ledger.order_units + ledger.referral_units - ledger.referral_released_units
                                  - ledger.released_units), ledger.pass_units
    else:
        from .points_funding_release import receipt_points_used_locked
        from rest_framework.exceptions import ValidationError as RestValidationError
        try:
            points_used = receipt_points_used_locked(receipt)
        except RestValidationError as exc:
            raise ValidationError('到账补资释放凭证待核对') from exc
        pass_used = receipt.allocations.aggregate(value=Sum('amount'))['value'] or 0
    if pass_used + points_used > receipt.amount:
        raise ValidationError('已有补资分配超额，请先核对')
    return existing



def _verify_replay_order_locked(order, existing):
    """Reconcile other allocations without reinterpreting a finalized refund.

    Receipt locks are taken in ID order after the order lock, including the
    retried receipt. The current platform operation can still be recovering
    from a cross-database commit, so only its *other* operations must already
    have completed platform evidence.
    """
    from rest_framework.exceptions import ValidationError as RestValidationError
    rows = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).order_by('pk'))
    receipts = {row.pk: row for row in PassRefundRepaymentReceipt.objects.select_for_update()
                .filter(pk__in={item.receipt_id for item in rows}).order_by('pk')}
    if existing.pk not in {row.pk for row in rows} or len(receipts) != len({row.receipt_id for row in rows}):
        raise ValidationError('订单补资凭证缺失，请先核对')
    from users.receipt_usage_guard import isolated_allocation_state_locked
    from .points_funding_release import receipt_points_used_locked
    try:
        for receipt in receipts.values():
            if not receipt.verified_at or not receipt.verified_by:
                raise ValidationError('此前到账尚未核实，请先核对')
            if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True and receipt.usage_version is None:
                raise ValidationError('此前到账用途账本尚未建立，请先核对')
            ledger = isolated_allocation_state_locked(receipt)
            if ledger:
                used, pass_used = (ledger.order_units + ledger.referral_units - ledger.referral_released_units
                                   - ledger.released_units), ledger.pass_units
            else:
                used = receipt_points_used_locked(receipt)
                pass_used = receipt.allocations.aggregate(value=Sum('amount'))['value'] or 0
            if used + pass_used > receipt.amount:
                raise ValidationError('此前到账补资超额，请先核对')
        if any(not row.allocated_by.strip() or not row.request_key.strip()
               or row.amount > receipts[row.receipt_id].amount for row in rows):
            raise ValidationError('此前补资凭证不一致，请先核对')
        # Platform-backed records carry independent, durable action and claim
        # evidence. Synthetic internal-test allocations have no such evidence.
        others = [row for row in rows if row.pk != existing.pk]
        if any(not row.request_key.startswith('points:') for row in others):
            from .points_checkout_test import require_isolated_checkout
            try:
                require_isolated_checkout()
            except RestValidationError as exc:
                raise ValidationError('此前补资操作凭证缺失，请先核对') from exc
        other_platform_ids = [row.pk for row in others if row.request_key.startswith('points:')]
        if other_platform_ids:
            from .points_funding_evidence import verified_order_allocations_locked
            verified_order_allocations_locked(order, allocation_ids=other_platform_ids)
        # Direct internal test allocations lack platform action snapshots.
        # Check their aggregate only while the original order economics still
        # apply; real operations are checked against immutable platform facts.
        if (any(not row.request_key.startswith('points:') for row in rows)
                and not order.settled_at and not order.refund_records.exists()):
            from .points_service_payment import needs_integrated_processing
            if needs_integrated_processing(order):
                plan = plan_verified_order(order.pk)
            else:
                from .service_funding_plan import plan_verified_ordinary_order
                plan = plan_verified_ordinary_order(order.pk)
            from .addon_funding import include_paid_addons
            plan = include_paid_addons(order, plan)
            from .addon_funding_release import addon_release_totals
            released = addon_release_totals(rows)
            if sum(row.amount - released[row.pk] for row in rows) > plan.additional_store_funding_units:
                raise ValidationError('已有补资分配超额，请先核对')
    except (RestValidationError, PointsFundingPlanError) as exc:
        raise ValidationError('订单补资凭证待核对') from exc
    return receipts[existing.receipt_id]

def allocate(*, actor, request_key, receipt_id, order_id, amount):
    from .points_service_payment import needs_integrated_processing
    candidate = Order.objects.filter(pk=order_id).first()
    from .models import OrderOriginalShareTransfer
    has_transfer = bool(candidate and OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=candidate).exists())
    if (candidate and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and needs_integrated_processing(candidate) and has_transfer):
        from .points_transfer_refund_access import after_transfer
        with transaction.atomic(), after_transfer(order_id, phase='settlement', extra_receipt_id=receipt_id):
            return _allocate(actor=actor, request_key=request_key, receipt_id=receipt_id,
                             order_id=order_id, amount=amount)
    if (candidate and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and needs_integrated_processing(candidate)
            and not has_transfer):
        with transaction.atomic():
            from .points_business_access import service_order
            with service_order(order_id, phase='funding', addon=candidate.addon_intents.filter(payment__isnull=False).exists(), extra_receipt_id=receipt_id):
                return _allocate(actor=actor, request_key=request_key, receipt_id=receipt_id, order_id=order_id, amount=amount)
    return _allocate(actor=actor, request_key=request_key, receipt_id=receipt_id, order_id=order_id, amount=amount)


def _allocate(*, actor, request_key, receipt_id, order_id, amount):
    _context(actor, request_key)
    _amount(amount)
    with transaction.atomic():
        if not points_funding_table_exists():
            raise ValidationError('积分补资数据尚未就绪')
        # Settlement already owns the order before it can inspect funding.
        # Both points allocation and settlement therefore lock the order
        # first, then the shared receipt. Pass repayment never locks orders.
        try:
            order = Order.objects.select_for_update().get(pk=order_id)
            if (getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
                    or getattr(settings, 'RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING', False) is True):
                # All participating receipts precede account, funding-lot and
                # calculation locks, including idempotent allocation retries.
                from .financial_lock import lock_order_money
                lock_order_money()
                receipt_ids = set(OrderPointsFundingAllocation.objects.filter(
                    Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk))
                    .values_list('receipt_id', flat=True))
                receipt_ids.add(receipt_id)
                locked_receipts = list(PassRefundRepaymentReceipt.objects.select_for_update()
                                       .filter(pk__in=receipt_ids).order_by('pk'))
                if len(locked_receipts) != len(receipt_ids):
                    raise ValidationError('订单补资到账凭证缺失')
            from rest_framework.exceptions import ValidationError as RestValidationError
            try:
                require_order_funding_access(order)
            except RestValidationError as exc:
                raise ValidationError('订单不在已开放的补资范围，请先核对') from exc
            # A committed allocation is a fact even if the order has since
            # settled or a refund has changed its current funding requirement.
            # Keep the normal order-then-receipt lock order for retries too.
            existing = OrderPointsFundingAllocation.objects.filter(request_key=request_key).first()
            if existing:
                if existing.receipt_id != receipt_id:
                    raise ValidationError('请求编号已用于其他积分补资')
                receipt = _verify_replay_order_locked(order, existing)
                return _verify_replay(existing, receipt, order_id=order_id, amount=amount, actor=actor)
            from .points_service_payment import needs_integrated_processing
            points_order = needs_integrated_processing(order)
            prior_verified = None
            verified_ordinary_negotiation = False
            if points_order:
                if order.refund_records.exists():
                    from .models import OrderOriginalShareTransfer
                    transferred = OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).exists()
                    if order.refund_records.count() in (2, 3) and transferred:
                        from types import SimpleNamespace
                        from .points_funding_evidence import verify_order_funding
                        if order.settled_at:
                            raise PointsFundingPlanError('订单已结算，不能继续分配补资')
                        prior_verified = verify_order_funding(order_id)
                        plan = SimpleNamespace(additional_store_funding_units=prior_verified.required)
                    elif order.refund_records.count() == 2:
                        from .points_business_access import permits as service_permits
                        if not (any(service_permits(order, phase='funding', kind=kind)
                                    for kind in ('service', 'addon'))
                                or getattr(settings, 'SERVICE_POINTS_UNPAID_FOLLOWUP_FUNDING_TESTING', False)):
                            raise PointsFundingPlanError('后续退款补资尚未开放')
                        from types import SimpleNamespace
                        from .points_followup_funding import verify_followup_funding
                        if order.settled_at:
                            raise PointsFundingPlanError('订单已结算，不能继续分配补资')
                        prior_verified = verify_followup_funding(order_id)
                        plan = SimpleNamespace(additional_store_funding_units=prior_verified.required)
                    else:
                        from .points_funding_evidence import verify_order_funding
                        if transferred:
                            from .points_transfer_refunded_funding import plan_refunded_transfer
                            plan = plan_refunded_transfer(order_id)
                        else:
                            from .points_refunded_funding import plan_refunded_order
                            plan = plan_refunded_order(order_id)
                        prior_verified = verify_order_funding(order_id)
                else:
                    from .models import OrderOriginalShareTransfer
                    if OrderOriginalShareTransfer.objects.filter(
                            previous_segment__attribution__order=order).exists():
                        from .points_transfer_funding import plan_points_transfer
                        from .points_funding_evidence import verify_order_funding
                        plan = plan_points_transfer(order_id).funding
                        prior_verified = verify_order_funding(order_id)
                    else:
                        plan = plan_verified_order(order_id)
                redemption = OrderPointsRedemption.objects.select_for_update().get(order_id=order_id)
            else:
                from .negotiation_zero import ordinary_funding_enabled
                from rest_framework.exceptions import ValidationError as RestValidationError
                try:
                    ordinary_zero = ordinary_funding_enabled(order)
                except RestValidationError as exc:
                    raise PointsFundingPlanError('零退款协商标记待核对') from exc
                from .ordinary_transfer_capability import permits as ordinary_transfer_permits
                from .ordinary_followup_capability import permits_funding as ordinary_followup_permits_funding
                from .models import OrderTransferNegotiationEvidence
                transferred_agreement = (ordinary_transfer_permits(order)
                    and OrderTransferNegotiationEvidence.objects.filter(
                        order=order, execution__termination_processed_at__isnull=False).exists())
                # A recorded second negotiation has its own net funding
                # version. It is already authorized by require_order_funding_access;
                # do not send it to the unrefunded order calculator.
                recorded_followup = ordinary_followup_permits_funding(order)
                if (transferred_agreement or recorded_followup or ((order.refund_records.exists() or ordinary_zero)
                        and (getattr(settings, 'ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING', False)
                     or (ordinary_refund_production_order(order) or _ordinary_addon_eligible(order) or ordinary_zero)))):
                    from types import SimpleNamespace
                    from .points_funding_evidence import verify_order_funding
                    prior_verified = verify_order_funding(order_id)
                    plan = SimpleNamespace(additional_store_funding_units=prior_verified.required)
                    verified_ordinary_negotiation = True
                else:
                    from .service_funding_plan import plan_verified_ordinary_order
                    plan = plan_verified_ordinary_order(order_id)
                redemption = None
        except PointsFundingPlanError as exc:
            raise ValidationError(str(exc)) from exc
        except Order.DoesNotExist as exc:
            raise ValidationError('订单不存在') from exc
        try:
            if not (verified_ordinary_negotiation or (order.refund_records.exists() and prior_verified is not None)):
                from .addon_funding import include_paid_addons
                plan = include_paid_addons(order, plan)
        except PointsFundingPlanError as exc:
            raise ValidationError(str(exc)) from exc
        receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=receipt_id)
        if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True and receipt.usage_version is None:
            raise ValidationError('到账用途账本尚未建立，请先核对到账')
        from users.receipt_usage_guard import isolated_allocation_state_locked
        ledger = isolated_allocation_state_locked(receipt)
        if not receipt.verified_at or not receipt.verified_by:
            raise ValidationError('到账尚未核实')
        existing = OrderPointsFundingAllocation.objects.filter(request_key=request_key).first()
        if existing:
            return _verify_replay(existing, receipt, order_id=order_id, amount=amount, actor=actor)
        if ledger:
            pass_used, points_used = ledger.pass_units, (ledger.order_units + ledger.referral_units
                - ledger.referral_released_units - ledger.released_units)
        else:
            pass_used = receipt.allocations.aggregate(value=Sum('amount'))['value'] or 0
            from .points_funding_release import receipt_points_used_locked
            from rest_framework.exceptions import ValidationError as RestValidationError
            try:
                points_used = receipt_points_used_locked(receipt)
            except RestValidationError as exc:
                raise ValidationError('到账补资释放凭证待核对') from exc
        prior = list(OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order_id=order_id) | Q(service_order_id=order_id)).select_related('receipt').order_by('pk'))
        if ledger:
            # An order may be funded by several receipts. All of them were
            # locked in ID order before the funding plan acquired account or
            # wallet locks; check each receipt's own usage before combining
            # its allocations into the order balance.
            for previous_receipt in locked_receipts:
                if previous_receipt.pk == receipt.pk:
                    continue
                if previous_receipt.usage_version is None:
                    raise ValidationError('此前到账用途账本尚未建立，请先核对到账')
                isolated_allocation_state_locked(previous_receipt)
        if any(not row.receipt.verified_at or not row.receipt.verified_by or not row.allocated_by.strip()
               or not row.request_key.strip() or row.amount > row.receipt.amount for row in prior):
            raise ValidationError('此前积分补资凭证不一致，请先核对')
        if prior_verified is not None:
            order_used = prior_verified.allocated
        else:
            from .addon_funding_release import addon_release_totals
            try:
                addon_releases = addon_release_totals(prior)
            except RestValidationError as exc:
                raise ValidationError('加购补资释放凭证待核对') from exc
            order_used = sum(row.amount - addon_releases[row.pk] for row in prior)
        if pass_used + points_used > receipt.amount or order_used > plan.additional_store_funding_units:
            raise ValidationError('已有补资分配超额，请先核对')
        try:
            plan_shared_receipt_allocation(receipt_amount=receipt.amount, pass_allocated=pass_used,
                points_allocated=points_used, order_required=plan.additional_store_funding_units,
                order_allocated=order_used, amount=amount)
        except PointsFundingAllocationError as exc:
            raise ValidationError(str(exc)) from exc
        allocation = OrderPointsFundingAllocation.objects.create(request_key=request_key, receipt=receipt,
            redemption=redemption, service_order=None if redemption else order, amount=amount, allocated_by=actor)
        if ledger:
            from users.receipt_usage_sources import record_order_locked, verify_allocations_locked
            record_order_locked(allocation)
            verify_allocations_locked(receipt.pk)
        return allocation
