"""Reconcile transferred service shares for review; this module never posts funds.

The plan is deliberately rebuilt from tenant-local facts each time. Any caller
that subsequently posts money must hold the order lock and independently check
the plan again in the same transaction.
"""
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import asdict
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (OrderOriginalAttribution, OrderOriginalShareTransfer,
                     OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot,
                     OrderSegmentReport, OrderSegmentTransferBasis)
from .points_funding_plan import PointsFundingPlanError
from .settlement import SettlementError, compute_provider_income
from .transfer_economics import allocate_same_rule_fixed_seat, allocate_transfer_basis

from rest_framework.response import Response
from rest_framework.views import APIView
from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import get_account_permissions, is_console_account
from stores.db import transaction


@dataclass(frozen=True)
class SegmentAmount:
    segment_id: int
    account_id: int
    basis: int
    fee: int
    income: int


@dataclass(frozen=True)
class PersonAmount:
    account_id: int
    segment_ids: tuple
    income: int
    freeze_until: object
    blockers: tuple


@dataclass(frozen=True)
class SegmentSettlementPlan:
    deal_amount: int
    segments: tuple
    persons: tuple
    inviter_income: int
    store_income: int
    fixed_fee_shortfall: int


def _fail(message):
    raise SettlementError(message)


def build_segment_settlement_plan(order, *, now=None, allow_recorded=False, _allow_points=False,
                                  _preview_unstarted=False, _allow_subsidy=False, _allow_recorded_refunds=False):
    """Calculate non-refunded wallet-funded transfer shares, without mutation.

    Includes per-person readiness, but readiness is not permission to pay:
    reconciliation, wallet funding, existing ledgers and locking must all be
    revalidated by the future settlement transaction.
    """
    from .segment_financial_capture import TRANSFER_RATE_REVIEW
    from .aftersale_scope import applies_to
    from engagement.confirmation import aftersale_deadline
    from .models import OrderPlatformSubsidy
    from .direct_payment_models import DirectPaymentIntent
    from wallet.models import Transaction

    from .negotiation_zero import marked_execution_id
    if marked_execution_id(order) and not _allow_recorded_refunds:
        # A zero agreement can close one participant even though it creates
        # no refund row. Never treat that segment as a free, unworked seat.
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
            _fail('零退款后转单须先核对原协商和逐段结算')

    if _allow_recorded_refunds:
        from stores.db import transaction
        from .models import OrderTransferNegotiationEvidence, OrdinaryPostRefundTransferRevision
        if not transaction.get_connection().in_atomic_block or not allow_recorded:
            _fail('退款后的履约段核算须先锁定并核验原协商')
        has_agreement = OrderTransferNegotiationEvidence.objects.filter(order=order).exists()
        if not has_agreement and marked_execution_id(order):
            revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
            has_agreement = bool(revision and isinstance(revision.snapshot, dict)
                                 and revision.snapshot.get('zero_pretransfer_execution_id') == marked_execution_id(order))
            if has_agreement:
                from .ordinary_post_refund_transfer import verify_locked
                from django.core.exceptions import ValidationError as DjangoValidationError
                from rest_framework.exceptions import ValidationError as RestValidationError
                try:
                    verify_locked(revision)
                except (DjangoValidationError, RestValidationError, ValueError):
                    _fail('零退款及转单资金版本待核对')
        if not has_agreement:
            transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
            if transfer and order.refund_records.exists():
                from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
                from django.core.exceptions import ValidationError as DjangoValidationError
                try:
                    has_agreement = bool(verify_positive_pretransfer(order, before=transfer.created_at))
                except DjangoValidationError:
                    _fail('转单前退款原协商待核对')
        if not has_agreement:
            _fail('退款后的履约段核算须先锁定并核验原协商')

    now = now or timezone.now()
    attribution = OrderOriginalAttribution.objects.filter(order=order, status='ACTIVE').first()
    coverage = OrderSegmentFinancialCoverage.objects.filter(attribution=attribution).first() if attribution else None
    points_internal = False
    subsidy_internal = False
    if _allow_points and coverage and coverage.source == 'WALLET_POINTS':
        from .points_transfer_capture import enabled as points_enabled
        points_internal = points_enabled()
    if _allow_subsidy and coverage and coverage.source == 'PLATFORM_SUBSIDY':
        from .subsidy_segment_capture import transfer_testing_enabled
        subsidy_internal = transfer_testing_enabled()
    if _preview_unstarted:
        from django.conf import settings
        from .points_transfer_refund_access import permits
        if not _allow_recorded_refunds and not subsidy_internal and (not points_internal or not (getattr(settings, 'SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING', False)
                or getattr(settings, 'SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING', False)
                or any(permits(order, phase=phase) for phase in ('transfer', 'report', 'refund')))):
            _fail('未开始履约的资金测算尚未开放')
    if (not coverage or coverage.status != 'REVIEW' or coverage.review_reason != TRANSFER_RATE_REVIEW
            or (not points_internal and not subsidy_internal and (coverage.source != 'WALLET_NO_POINTS' or coverage.deal_amount != order.amount))
            or (_allow_points and not points_internal)
            or (_allow_subsidy and not subsidy_internal)
            or (not order.amount and not subsidy_internal)
            or (order.payment_status != order.PaymentStatus.PAID and not (_allow_recorded_refunds
                and order.payment_status in (order.PaymentStatus.PARTIALLY_REFUNDED, order.PaymentStatus.REFUNDED)))
            or (order.refund_records.exists() and not _allow_recorded_refunds) or order.is_gift_snapshot
            or (not subsidy_internal and OrderPlatformSubsidy.objects.filter(order=order).exists())
            or DirectPaymentIntent.objects.filter(order=order).exists()):
        _fail('订单成交、退款或资金来源待核对，不能生成逐段收益')
    if subsidy_internal:
        from .subsidy_agreement import verify_coupon
        from rest_framework.exceptions import ValidationError
        subsidy = OrderPlatformSubsidy.objects.filter(order=order).first()
        payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet'))
        try:
            verify_coupon(order)
        except ValidationError as exc:
            raise SettlementError('安全箱券凭证待核对') from exc
        if (not subsidy or subsidy.basis != coverage.deal_amount or order.amount != 0
                or order.provider_income or order.shop_income or order.inviter_commission
                or len(payments) != 1 or payments[0].amount != 0
                or payments[0].wallet.account_id != order.customer_account_id
                or payments[0].wallet.user_id != order.customer_id
                or payments[0].balance_after != payments[0].balance_before
                or payments[0].reversal_of_id or payments[0].reversals.exists()):
            _fail('安全箱补贴及付款凭证待核对')
    if not allow_recorded and (order.settled_at or order.providers.filter(settled_at__isnull=False).exists()
            or order.transactions.filter(tx_type=Transaction.TxType.INCOME,
                status=Transaction.Status.SUCCESS, reversal_of__isnull=True).exists()):
        _fail('订单已有收益入账或结算记录，须先核对历史流水')
    if order.service_clock_version is None or order.service_freeze_hours is None or order.service_aftersale_hours is None:
        _fail('订单等待时限快照待核对')
    segments = list(attribution.segments.order_by('pk'))
    snapshots = {s.segment_id: s for s in OrderSegmentFinancialSnapshot.objects.filter(segment__attribution=attribution)}
    events = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution)
                  .select_related('previous_segment', 'next_segment').order_by('pk'))
    allocations = {a.transfer_id: a for a in OrderSegmentTransferBasis.objects.filter(transfer__in=events)}
    if not segments or not events or len(snapshots) != len(segments) or len(allocations) != len(events):
        _fail('履约段、转单或抽成快照缺失')
    by_id = {s.pk: s for s in segments}
    outgoing = {e.previous_segment_id: e for e in events}
    incoming = {e.next_segment_id: e for e in events}
    if len(outgoing) != len(events) or len(incoming) != len(events):
        _fail('转单关联重复')
    shortfall = 0
    for event in events:
        before, after = by_id.get(event.previous_segment_id), by_id.get(event.next_segment_id)
        allocation = allocations[event.pk]
        if (not before or not after or before.slot != after.slot or not before.released_at
                or before.released_at != after.accepted_at or event.created_at != before.released_at
                or before.original_share != event.retained_share
                or event.original_share_before != event.retained_share + event.transferred_share):
            _fail('转单人员、时点或原价份额不一致')
        old, new = snapshots[before.pk], snapshots[after.pk]
        next_transfer = outgoing.get(after.pk)
        if ((next_transfer.original_share_before if next_transfer else after.original_share)
                != event.transferred_share):
            _fail('接替履约段原价份额与转单记录不一致')
        try:
            split = allocate_transfer_basis(old.incoming_basis, event.original_share_before,
                                            event.retained_share, event.transferred_share)
        except ValueError as exc:
            raise SettlementError(str(exc)) from exc
        if ((allocation.basis_before, allocation.retained_basis, allocation.transferred_basis, allocation.remainder_numerator)
                != (split.total, split.retained, split.transferred, split.remainder_numerator)
                or old.retained_basis != split.retained or new.incoming_basis != split.transferred):
            _fail('转单成交基数或尾差不一致')
        if old.commission_type == 'FIXED' and new.commission_type == 'FIXED' and old.fixed_rule_total == new.fixed_rule_total:
            try:
                _, expected_fee, expected_shortfall = allocate_same_rule_fixed_seat(
                    coverage.deal_amount, old.fixed_rule_total, old.commission_fixed, split.retained, split.transferred)
            except (TypeError, ValueError) as exc:
                raise SettlementError('固定抽成席位待核对') from exc
            if new.commission_fixed != expected_fee or allocation.fixed_fee_store_shortfall != expected_shortfall:
                _fail('固定抽成席位或门店差额不一致')
            shortfall += expected_shortfall
        elif allocation.fixed_fee_store_shortfall is not None:
            _fail('固定抽成门店差额来源不一致')
        elif new.commission_type == 'FIXED':
            fixed = new.fixed_rule_total
            if fixed is None or new.commission_fixed != (fixed * split.transferred + coverage.deal_amount - 1) // coverage.deal_amount:
                _fail('接替固定抽成快照不一致')
    lines = []
    required = defaultdict(list)
    for segment in segments:
        snap = snapshots[segment.pk]
        if (snap.commission_type not in ('FIXED', 'PERCENT') or not 0 <= snap.commission_rate <= 100
                or snap.income_at_entry != compute_provider_income(snap.incoming_basis, snap.commission_type,
                                                                   snap.commission_rate, snap.commission_fixed)):
            _fail('履约段接入抽成快照不一致')
        if snap.commission_type == 'PERCENT' and (snap.commission_fixed or snap.fixed_rule_total is not None):
            _fail('百分比抽成快照不一致')
        event = outgoing.get(segment.pk)
        if event:
            basis = snap.retained_basis
        elif segment.released_at:
            negotiated_end = False
            if _allow_recorded_refunds and segment.started_at is not None:
                from .models import NegotiationExecution
                from django.db.models import Q
                negotiated_end = NegotiationExecution.objects.filter(
                    Q(refund__order=order) | Q(zero_order=order), shares__account_id=segment.provider_account_id,
                    accepted_at=segment.released_at, termination_processed_at__isnull=False).exists()
            # Pre-service reassignment has no service-transfer event and retains
            # no earnings. Do not infer historical started segments this way.
            if negotiated_end and snap.retained_basis is None:
                basis = snap.incoming_basis
            elif segment.started_at is not None or snap.retained_basis != 0:
                _fail('已转出履约段缺少转单记录')
            else:
                basis = 0
        else:
            if snap.retained_basis is not None:
                _fail('进行中履约段成交基数状态不一致')
            basis = snap.incoming_basis
        if basis is None or basis < 0 or basis > snap.incoming_basis:
            _fail('履约段成交基数无效')
        if snap.commission_type == 'FIXED':
            if snap.fixed_rule_total is None:
                _fail('固定抽成规则快照缺失')
            fee = (snap.fixed_rule_total * basis // coverage.deal_amount if event else
                   0 if segment.released_at and not (_allow_recorded_refunds and basis) else snap.commission_fixed)
            if fee > basis:
                _fail('履约段固定抽成超过成交基数')
        else:
            fee = basis * snap.commission_rate // 100
        if basis and not segment.started_at:
            if not _preview_unstarted or segment.released_at or order.status != order.Status.IN_SERVICE:
                _fail('有收益份额缺少本人实际履约记录')
        lines.append(SegmentAmount(segment.pk, segment.provider_account_id, basis, fee, basis - fee))
        if segment.started_at and (not segment.released_at or segment.original_share > 0):
            required[segment.provider_account_id].append(segment.pk)
    if points_internal and not order.refund_records.exists():
        from .models import OrderAddonHandover
        from .addon_handover import verify_handover
        from rest_framework.exceptions import ValidationError
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.db.models import OuterRef, Subquery
        latest = OrderAddonHandover.objects.filter(payment_id=OuterRef('payment_id'), status='ACCEPTED').order_by('-version')
        handovers = OrderAddonHandover.objects.filter(payment__intent__order=order,
            pk=Subquery(latest.values('pk')[:1])).select_related('payment__intent')
        for handover in handovers:
            if not handover.accepted_at:
                _fail('加购接替缺少实际确认时间')
            try:
                refunds = list(handover.payment.refund_records.order_by('created_at', 'pk'))
                from .models import OrderAddonFundingReleaseRun
                team_run = OrderAddonFundingReleaseRun.objects.filter(handover=handover,
                    points_handover_snapshot__kind='SAME_TEAM_V1').first()
                if refunds:
                    from .points_addon_handover_refund_facts import settlement_parts
                    settlement_parts(refunds[0])
                if team_run:
                    from .addon_funding_release import verify_run
                    from .points_team_addon_handover_facts import verify_locked
                    verify_run(team_run)
                    verify_locked(order, handover.payment, handover,
                                  before=refunds[0].created_at if refunds else None)
                    shares = list(handover.shares.select_related('segment').order_by('position'))
                else:
                    from .models import OrderPointsFundingAllocation
                    witness = OrderPointsFundingAllocation.objects.filter(
                        redemption__order=order).order_by('pk').first()
                    # Earlier refunds reduce the source shares inside verify_handover.
                    # Only a later refund can be the upper bound for this handover.
                    boundary = next((row.created_at for row in refunds if row.created_at > handover.accepted_at), None)
                    shares = verify_handover(handover.payment, handover,
                        as_of=boundary, receipt_id=witness.receipt_id if witness else None)
            except (ValidationError, DjangoValidationError) as exc:
                raise SettlementError('加购接替凭证待核对') from exc
            for share in shares:
                segment = by_id.get(share.segment_id)
                if not segment or (share.basis and not segment.started_at):
                    _fail('加购收益对应的履约段待核对')
                if share.basis and segment.pk not in required[segment.provider_account_id]:
                    required[segment.provider_account_id].append(segment.pk)
    if sum(line.basis for line in lines) != coverage.deal_amount:
        _fail('各履约段成交基数合计与订单不符')
    if any(line.basis and line.account_id not in required
           and not (_preview_unstarted and not by_id[line.segment_id].started_at) for line in lines):
        _fail('有收益份额缺少本人履约报单范围')
    reports = {r.segment_id: r for r in OrderSegmentReport.objects.filter(segment_id__in=[i for ids in required.values() for i in ids])}
    cases = list(order.aftersales.filter(status='OPEN'))
    deadline = aftersale_deadline(order)
    people = []
    for account_id, ids in sorted(required.items()):
        blockers, timestamps = [], []
        if order.status != order.Status.COMPLETED:
            blockers.append('整单尚未完成')
        if not deadline or now < deadline:
            blockers.append('售后等待期尚未结束')
        if any(applies_to(case, account_id) for case in cases):
            blockers.append('本人相关售后尚未结束')
        for segment_id in ids:
            report = reports.get(segment_id)
            submission = report.submissions.order_by('-version').first() if report else None
            segment = by_id[segment_id]
            if (not report or report.status != 'APPROVED' or not submission or submission.status != 'APPROVED'
                    or submission.version != report.version or not submission.submitted_at
                    or submission.submitted_at > now or not submission.reviewed_at or submission.reviewed_at > now
                    or submission.reviewed_at < submission.submitted_at or not submission.reviewer_id
                    or not submission.entry_image or not submission.completion_image or not submission.result_images.exists()
                    or submission.entry_image.name != segment.entry_image.name
                    or submission.completion_image.name != segment.completion_image.name):
                blockers.append('本人履约报单未全部通过审核'); break
            timestamps.append(submission.submitted_at)
        freeze_until = max(timestamps) + timedelta(hours=order.service_freeze_hours) if len(timestamps) == len(ids) else None
        if freeze_until and now < freeze_until:
            blockers.append('本人报单冻结期尚未结束')
        people.append(PersonAmount(account_id, tuple(ids), sum(x.income for x in lines if x.account_id == account_id),
                                   freeze_until, tuple(blockers)))
    rate = order.inviter_commission_rate_snapshot
    if not 0 <= rate <= 100:
        _fail('推荐佣金比例快照无效')
    inviter = order.amount * rate // 100 if order.inviter_account_id or order.inviter_id else 0
    return SegmentSettlementPlan(coverage.deal_amount, tuple(lines), tuple(people), inviter,
                                 max(order.amount - sum(line.income for line in lines) - inviter, 0), shortfall)


class SegmentSettlementPlanView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id):
        from .segment_reports import enabled, supports
        from .models import Order
        from .points_service_payment import needs_integrated_processing
        if not enabled():
            return Response({'code':404, 'msg':'当前不支持逐段收益核对'}, status=404)
        if not is_console_account(request.account) or not {'order:view', 'report:view'}.issubset(get_account_permissions(request.account)):
            return Response({'code':403, 'msg':'查看收益核对需要订单及报单查看权限'}, status=403)
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if not order:
                return Response({'code':404, 'msg':'订单当前不支持逐段收益核对'}, status=404)
            try:
                from .segment_settlement import _ordinary_transfer_refund
                if (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                        and order.status in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
                        and needs_integrated_processing(order)
                        and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                    from .addon_settlement import has_paid_addons
                    from .points_transfer_funding import plan_points_transfer
                    from .points_transfer_refund_access import after_transfer
                    if (order.payment_status != Order.PaymentStatus.PAID or order.refund_records.exists()
                            or has_paid_addons(order)):
                        raise SettlementError('积分转单的付款、退款或加购须先核对，不能生成逐段收益')
                    with after_transfer(order.pk, phase='report', read_only=True):
                        plan = plan_points_transfer(order.pk, read_only=True).segments
                elif not supports(order):
                    return Response({'code':404, 'msg':'订单当前不支持逐段收益核对'}, status=404)
                elif _ordinary_transfer_refund(order):
                    from .negotiation_transfer_settlement import build_locked
                    plan, _ = build_locked(order)
                else:
                    plan = build_segment_settlement_plan(order)
            except ValidationError as exc:
                detail = exc.detail
                message = str(detail[0]) if isinstance(detail, list) and len(detail) == 1 else '积分转单凭证待核对'
                return Response({'code':409, 'msg':message}, status=409)
            except (SettlementError, PointsFundingPlanError) as exc:
                return Response({'code':409, 'msg':str(exc)}, status=409)
            return Response({'code':0, 'data':asdict(plan)})
