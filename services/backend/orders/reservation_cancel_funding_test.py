"""Verified release of funding after a full, unstarted reservation refund.

Receipt-first verification can acquire order locks; the whole path therefore
remains limited to the existing isolated SQLite checkout environment.
"""
import hashlib
import json
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.context import current_database, current_store
from stores.db import connection, transaction
from .models import (Order, OrderOriginalAttribution, OrderOriginalShareTransfer, OrderParticipationSegment,
    OrderPointsFundingAction, OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsRefund,
    OrderPointsSettlement, OrderPointsSettlementFunding, OrderPointsRefundSettlementFunding, OrderPointsTransferFundingRelease,
    OrderRefund, OrderSegmentFinancialSnapshot, Reservation, ReservationPointsCancelFundingEvidence,
    ReservationPointsCancelFundingRelease)
from .points_checkout_test import require_isolated_checkout


def tables_exist():
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    required = {ReservationPointsCancelFundingEvidence._meta.db_table,
                ReservationPointsCancelFundingRelease._meta.db_table}
    if required.issubset(tables):
        return True
    if (required.intersection(tables) or 'django_migrations' not in tables
            or MigrationRecorder.Migration.objects.using(current_database()).filter(
                app='orders', name='0149_reservationpointscancelfundingevidence_and_more').exists()):
        raise DatabaseError('预约退款补资释放凭证表缺失，须先核对数据库')
    return False


def _require_enabled():
    require_isolated_checkout()
    if (not transaction.get_connection().in_atomic_block or current_store() is None
            or not getattr(settings, 'SERVICE_POINTS_RESERVATION_CANCEL_FUNDING_TESTING', False)
            or not getattr(settings, 'SERVICE_POINTS_FUNDING_TESTING', False)
            or not getattr(settings, 'SERVICE_POINTS_FUNDING_RELEASE_TESTING', False)
            or not tables_exist()):
        raise ValidationError('预约退款补资释放尚未开放')


def _digest(refund_id, slug, facts):
    return hashlib.sha256(json.dumps([refund_id, slug, facts], sort_keys=True,
                          ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _facts(order, booking, allocations):
    segments = list(OrderParticipationSegment.objects.filter(attribution__order=order).order_by('slot', 'pk'))
    snapshots = {row.segment_id: row for row in OrderSegmentFinancialSnapshot.objects.filter(segment__in=segments)}
    assignments = list(order.providers.order_by('pk'))
    return {'order_id': order.pk, 'booking_id': booking.pk, 'buyer_id': order.customer_account_id,
        'team_size': order.required_provider_count, 'deal': order.original_amount - order.boss_discount
            - order.promo_discount - order.coupon_discount, 'paid': order.amount,
        'allocations': [[row.pk, row.receipt_id, row.amount] for row in allocations],
        'assignments': [[row.pk, row.provider_account_id, row.settlement_base, row.commission_type,
                         row.commission_rate, row.commission_fixed, row.provider_income,
                         row.game_commission_snapshot] for row in assignments],
        'segments': [[row.pk, row.slot, row.provider_account_id, row.original_share,
                      snapshots[row.pk].pk if row.pk in snapshots else None,
                      snapshots[row.pk].incoming_basis if row.pk in snapshots else None,
                      snapshots[row.pk].income_at_entry if row.pk in snapshots else None,
                      snapshots[row.pk].commission_type if row.pk in snapshots else None,
                      snapshots[row.pk].commission_rate if row.pk in snapshots else None,
                      snapshots[row.pk].commission_fixed if row.pk in snapshots else None,
                      snapshots[row.pk].retained_basis if row.pk in snapshots else None,
                      snapshots[row.pk].fixed_rule_total if row.pk in snapshots else None,
                      snapshots[row.pk].commission_source if row.pk in snapshots else None,
                      snapshots[row.pk].game_commission_snapshot if row.pk in snapshots else None] for row in segments]}


def _allocations(order):
    return list(OrderPointsFundingAllocation.objects.select_for_update().filter(
        Q(redemption__order=order) | Q(service_order=order)).select_related('receipt', 'redemption').order_by('pk'))


def _no_other_usage(order, allocations):
    from engagement.models import ConsumptionConfirmation
    from wallet.models import ProviderReport, Transaction
    attribution = OrderOriginalAttribution.objects.filter(order=order).first()
    if (not attribution or OrderParticipationSegment.objects.filter(attribution=attribution).filter(
            Q(started_at__isnull=False) | Q(released_at__isnull=False)).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution).exists()
            or order.providers.filter(Q(released_at__isnull=False) | Q(settled_at__isnull=False)).exists()
            or order.aftersales.exists() or order.consumption_confirmed_at
            or ConsumptionConfirmation.objects.filter(order=order).exists()
            or ProviderReport.objects.filter(order=order).exists() or OrderPointsSettlement.objects.filter(order=order).exists()
            or OrderPointsSettlementFunding.objects.filter(allocation__in=allocations).exists()
            or OrderPointsRefundSettlementFunding.objects.filter(allocation__in=allocations).exists()
            or OrderPointsFundingRelease.objects.filter(allocation__in=allocations).exists()
            or OrderPointsTransferFundingRelease.objects.filter(allocation__in=allocations).exists()
            or Transaction.objects.filter(order=order, status='SUCCESS',
                tx_type__in=['INCOME', 'GIFT', 'SHOP_INCOME'], amount__gt=0).exists()):
        raise ValidationError('预约已履约、结算或有其他补资释放记录')


def capture_locked(order, booking):
    """Reconcile locked platform receipts and take the pre-refund facts."""
    _require_enabled()
    from .points_funding_evidence import verified_order_allocations_locked, verify_order_funding
    from .points_funding_plan import PointsFundingPlanError
    try:
        funding = verify_order_funding(order.pk, _reservation_usage=True)
        allocations = verified_order_allocations_locked(order, _reservation_usage=True)
    except PointsFundingPlanError as exc:
        raise ValidationError('预约补资凭证待核对') from exc
    if (not allocations or sum(row.amount for row in allocations) != funding.allocated
            or any(row.redemption_id is None or row.redemption.order_id != order.pk or row.service_order_id
                   for row in allocations)):
        raise ValidationError('预约补资归属或分配金额异常')
    _no_other_usage(order, allocations)
    return _facts(order, booking, allocations)


def record_locked(points_refund, facts):
    """Write release evidence in the same transaction as the refund."""
    _require_enabled()
    order = points_refund.redemption.order
    booking = Reservation.objects.get(order=order, status=Reservation.Status.CONVERTED)
    allocations = _allocations(order)
    if (facts != _facts(order, booking, allocations)
            or ReservationPointsCancelFundingEvidence.objects.filter(points_refund=points_refund).exists()
            or ReservationPointsCancelFundingRelease.objects.filter(allocation__in=allocations).exists()):
        raise ValidationError('预约退款补资快照已变化')
    _no_other_usage(order, allocations)
    slug = current_store().slug
    evidence = ReservationPointsCancelFundingEvidence.objects.create(points_refund=points_refund, store_slug=slug,
        facts=facts, evidence_hash=_digest(points_refund.pk, slug, facts))
    ReservationPointsCancelFundingRelease.objects.bulk_create([
        ReservationPointsCancelFundingRelease(evidence=evidence, allocation=row, amount=row.amount) for row in allocations])
    if getattr(settings, 'SERVICE_POINTS_RESERVATION_CANCEL_USAGE_TESTING', False):
        from users.receipt_usage_sources import record_reservation_release_locked, verify_allocations_locked
        releases = list(ReservationPointsCancelFundingRelease.objects.filter(evidence=evidence)
                        .select_related('allocation__receipt', 'evidence__points_refund__redemption').order_by('allocation__receipt_id', 'pk'))
        touched = set()
        for row in releases:
            if row.allocation.receipt.usage_version is not None:
                record_reservation_release_locked(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    return evidence


def _verify_platform_allocations(order, allocations):
    """Validate platform authorization without recursively recounting receipts."""
    from stores.models import PlatformPointsFundingOperation
    from stores.platform_pass_repayment import RepaymentError, _claimed_receipt, _sha
    from stores.platform_points_funding import _verified_receipt
    for row in allocations:
        try:
            if not row.request_key.startswith('points:') or len(row.request_key) != 39:
                raise ValueError()
            operation_id = UUID(hex=row.request_key[7:])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError('预约补资操作编号异常') from exc
        operation = PlatformPointsFundingOperation.objects.using('default').select_related('claim').filter(
            pk=operation_id, claim__store__slug=current_store().slug).first()
        action = OrderPointsFundingAction.objects.filter(request_key=row.request_key).first()
        expected = {'receipt_id': row.receipt_id, 'allocation_id': row.pk, 'order_id': order.pk, 'amount': row.amount}
        if (not operation or not operation.completed_at or operation.rejected_at or operation.voided_at
                or operation.claim.voided_at
                or not action or operation.tenant_action_id != action.pk or action.allocation_id != row.pk
                or operation.claim.tenant_receipt_id != row.receipt_id or action.actor != str(operation.actor_id)
                or row.allocated_by != str(operation.actor_id) or action.result != expected
                or operation.payload_digest != _sha(json.dumps([str(operation.claim_id), order.pk, row.amount],
                                                               separators=(',', ':')))):
            raise ValidationError('预约补资原平台凭证异常')
        try:
            _claimed_receipt(operation.claim, current_store().slug, row.receipt)
            _verified_receipt(operation.claim, row.receipt)
        except RepaymentError as exc:
            raise ValidationError('预约补资到账核实记录异常') from exc


def verify_locked(evidence):
    _require_enabled()
    evidence = ReservationPointsCancelFundingEvidence.objects.select_for_update().select_related(
        'points_refund__redemption__order', 'points_refund__refund').get(pk=evidence.pk)
    points_refund = evidence.points_refund
    order = Order.objects.select_for_update().get(pk=points_refund.redemption.order_id)
    booking = Reservation.objects.filter(order=order, status=Reservation.Status.CONVERTED).first()
    if (evidence.store_slug != current_store().slug or not booking or order.status != Order.Status.CANCELLED
            or order.payment_status != Order.PaymentStatus.REFUNDED or not points_refund.is_final
            or points_refund.equivalent_units != points_refund.redemption.deal_units
            or points_refund.coin_units != points_refund.redemption.paid_units
            or points_refund.points_hundredths != points_refund.redemption.points_hundredths
            or order.refund_records.count() != 1 or not isinstance(points_refund.refund.benefit_effects, dict)
            or points_refund.refund.benefit_effects.get('reservation_points_prestart_cancel') is not True):
        raise ValidationError('预约全额退款凭证异常')
    from .points_service_refund import verify_refund_history_locked
    if verify_refund_history_locked(points_refund.refund_id).pk != points_refund.pk:
        raise ValidationError('预约退款流水不一致')
    allocations = _allocations(order)
    _no_other_usage(order, allocations)
    if (not allocations or any(row.redemption_id != points_refund.redemption_id or row.service_order_id for row in allocations)
            or evidence.facts != _facts(order, booking, allocations)
            or evidence.evidence_hash != _digest(points_refund.pk, evidence.store_slug, evidence.facts)):
        raise ValidationError('预约退款补资快照不一致')
    releases = list(evidence.releases.order_by('allocation_id').values_list('allocation_id', 'amount'))
    if releases != [(row.pk, row.amount) for row in allocations]:
        raise ValidationError('预约退款补资释放不完整')
    _verify_platform_allocations(order, allocations)
    return allocations


def receipt_released_locked(receipt):
    """Reconcile all cancellation releases before returning reusable funds."""
    if not tables_exist():
        return 0
    from .points_funding_release import _chunks, _paged
    allocation_orders = {row.redemption.order_id for row in _paged(
        OrderPointsFundingAllocation.objects.filter(receipt=receipt, redemption__isnull=False)
        .select_related('redemption'))}
    marked = {row.order_id for ids in _chunks(allocation_orders) for row in _paged(
        OrderRefund.objects.filter(order_id__in=ids)) if isinstance(row.benefit_effects, dict)
        and row.benefit_effects.get('reservation_points_prestart_cancel') is True}
    evidences = [row for ids in _chunks(marked) for row in _paged(
        ReservationPointsCancelFundingEvidence.objects.filter(
            points_refund__redemption__order_id__in=ids).select_related('points_refund__redemption'))]
    if {row.points_refund.redemption.order_id for row in evidences} != marked:
        raise ValidationError('预约取消补资释放凭证缺失')
    for evidence in evidences:
        verify_locked(evidence)
    evidence_ids = {row.pk for row in evidences}
    total = 0
    for row in _paged(ReservationPointsCancelFundingRelease.objects.filter(allocation__receipt=receipt)):
        if row.evidence_id not in evidence_ids:
            raise ValidationError('预约取消补资释放归属异常')
        total += row.amount
    return total
