"""Capture proven entry economics; this does not authorize or post payouts."""
from copy import deepcopy

from django.conf import settings
from rest_framework.exceptions import ValidationError as RestValidationError
from stores.db import transaction

from .models import (OrderPlatformSubsidy, OrderProvider, OrderSegmentFinancialCoverage,
                     OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis)
from .settlement import compute_provider_income

TRANSFER_REVIEW = '已记录成交基数，接入抽成及结算待核对'
TRANSFER_RATE_REVIEW = '已记录成交基数及接入抽成，逐段结算待核对'

def enabled():
    from .principal_segments import enabled as principal_segments_enabled
    return bool((getattr(settings, 'SEGMENT_ECONOMICS_CAPTURE_ENABLED', False)
                 and getattr(settings, 'ORIGINAL_ATTRIBUTION_ENABLED', False)) or principal_segments_enabled())


def enforcement_enabled():
    return enabled() or getattr(settings, 'SEGMENT_ECONOMICS_ENFORCE_ENABLED', False)


def subsidy_capture_enabled():
    return bool(enabled() and getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False)
                and getattr(settings, 'VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED', False))


def _locked():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('分段资金快照须在订单事务内记录')


def payment_created(attribution, order):
    if not enabled():
        return
    _locked()
    from .points_service_payment import MARKER, needs_integrated_processing
    if needs_integrated_processing(order):
        from .models import OrderPointsRedemption
        from .points_service_refund import _plan as validate_payment_assets
        from .settlement import SettlementError
        receipt = OrderPointsRedemption.objects.select_for_update().filter(order=order).first()
        if (not receipt or not isinstance(order.funding_terms, dict)
                or order.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}
                or order.refund_records.exists()
                or receipt.deal_units != (order.original_amount - order.boss_discount
                                          - order.promo_discount - order.coupon_discount)
                or receipt.paid_units != order.amount):
            raise SettlementError('积分订单成交与付款凭证不一致')
        # Reuse the actual asset/source validator, rather than infer the
        # pre-points amount from a client quote or the ordinary order split.
        validate_payment_assets(order, None, receipt.deal_units)
        deal_amount, source = receipt.deal_units, 'WALLET_POINTS'
    else:
        deal_amount, source = order.amount, 'WALLET_NO_POINTS'
    OrderSegmentFinancialCoverage.objects.create(attribution=attribution,
        deal_amount=deal_amount, source=source)


def register_subsidy(order, subsidy):
    """Use the verified coupon's funded basis, not the boss's zero payment."""
    if not subsidy_capture_enabled():
        return
    _locked()
    from .state_machine import IllegalTransitionError
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution__order=order).first()
    if (not coverage or coverage.status != 'PENDING' or coverage.source != 'WALLET_NO_POINTS'
            or coverage.deal_amount != 0 or order.amount != 0 or subsidy.order_id != order.pk
            or subsidy.basis <= 0 or coverage.attribution.status != 'PENDING'):
        raise IllegalTransitionError('安全箱原价和补贴基数待核对')
    coverage.source, coverage.deal_amount = 'PLATFORM_SUBSIDY', subsidy.basis
    coverage.save(update_fields=['source', 'deal_amount'])


def _review(coverage, reason):
    if coverage and coverage.status != 'REVIEW':
        coverage.status = 'REVIEW'; coverage.review_reason = reason[:255]
        coverage.save(update_fields=['status', 'review_reason'])


def mark_review(attribution, reason):
    if not enabled():
        from .principal_segments import enabled_for_order
        if not enabled_for_order(attribution.order):
            return
    _locked()
    _review(OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution=attribution).first(), reason)


def transfer_settlement_pending(order):
    """Do not settle legacy split amounts for captured, unfinished transfers."""
    _locked()
    from .models import OrderOriginalShareTransfer
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution__order=order).first()
    # Any service transfer requires the new segment settlement path, even if
    # recording was temporarily disabled and the coverage still says ACTIVE.
    return bool(coverage and OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution=coverage.attribution).exists())


def covered_transfers(order_id, account_id=None):
    from django.db.models import Q
    from .models import OrderOriginalShareTransfer
    events = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=order_id,
        previous_segment__attribution__financial_coverage__isnull=False)
    if account_id is not None:
        events = events.filter(Q(previous_segment__provider_account_id=account_id)
                               | Q(next_segment__provider_account_id=account_id))
    return events


def transfer_income_pending(order, account_id=None):
    """Read-only presentation check; already settled historical shares remain visible."""
    if not order or not order.pk:
        return False
    from .segment_settlement import protection_enabled
    if protection_enabled():
        from .models import OrderSegmentSettlementManifest
        has_manifest = getattr(order, '_provider_has_manifest', None)
        if has_manifest is None:
            has_manifest = OrderSegmentSettlementManifest.objects.filter(order=order).exists()
        if has_manifest:
            return True  # Old person/order amounts cannot represent segmented credits.
    covered = (order._provider_has_covered_transfer
               if account_id and account_id == getattr(order, '_provider_display_account_id', None) else None)
    if order.settled_at or covered is False:
        return False
    if account_id is not None:
        if order.providers.filter(provider_account_id=account_id, settled_at__isnull=False).exists():
            return False
    if covered is True:
        return True
    return covered_transfers(order.pk, account_id).exists()


def retained_transfer_accounts(order):
    """People with proved prior participation and a nonzero agreed share."""
    if not order or not order.pk:
        return set()
    from .models import OrderOriginalShareTransfer
    return set(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
        previous_segment__attribution__financial_coverage__isnull=False,
        previous_segment__started_at__isnull=False, retained_share__gt=0)
        .values_list('previous_segment__provider_account_id', flat=True))


def _matches(assignment):
    if assignment.commission_type not in OrderProvider.CommissionType.values:
        return False
    if not 0 <= assignment.commission_rate <= 100 or not isinstance(assignment.game_commission_snapshot, dict):
        return False
    return assignment.provider_income == compute_provider_income(assignment.settlement_base,
        assignment.commission_type, assignment.commission_rate, assignment.commission_fixed)


def _capture(segment, assignment, *, fixed_rule_total, commission_source, share=None):
    share = share or assignment
    return OrderSegmentFinancialSnapshot.objects.create(segment=segment,
        incoming_basis=share.settlement_base, commission_type=share.commission_type,
        commission_rate=share.commission_rate, commission_fixed=share.commission_fixed,
        fixed_rule_total=fixed_rule_total, commission_source=commission_source,
        income_at_entry=share.provider_income, game_commission_snapshot=deepcopy(assignment.game_commission_snapshot))


def _points_shares(order, coverage, assignments, segments):
    """Verify payment and convert the already-frozen assignment rules to deal basis."""
    from .commission import fixed_specs
    from .models import OrderPointsRedemption
    from .points_service_payment import MARKER
    from .points_service_refund import _plan as validate_payment_assets
    from .points_funding_plan import plan_points_funding
    from .settlement import build_provider_shares
    receipt = OrderPointsRedemption.objects.select_for_update().filter(order=order).first()
    if (not receipt or not isinstance(order.funding_terms, dict)
            or order.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}
            or receipt.deal_units != coverage.deal_amount or receipt.paid_units != order.amount
            or receipt.deal_units != (order.original_amount - order.boss_discount
                                      - order.promo_discount - order.coupon_discount)
            or order.refund_records.exists()):
        raise ValueError('积分付款和成交基数不一致')
    validate_payment_assets(order, None, receipt.deal_units)
    by_account = {row.provider_account_id: row for row in assignments}
    ordered = [by_account[segment.provider_account_id] for segment in segments]
    from .delta_personal_commission import verify_assignment_evidence
    if (any(row.settled_at for row in ordered) or sum(row.settlement_base for row in ordered) != receipt.paid_units
            or sum(row.provider_income for row in ordered) != order.provider_income
            or order.shop_income != max(receipt.paid_units - order.provider_income - order.inviter_commission, 0)
            or any(not _matches(row) or not verify_assignment_evidence(order, row) for row in ordered)):
        raise ValueError('派单份额或抽成快照不完整')
    modes = {row.commission_type for row in ordered}
    if modes == {'FIXED'}:
        if (order.commission_type != 'FIXED' or any(row.commission_rate for row in ordered)
                or [row.commission_fixed for row in ordered] !=
                    [item['commission_fixed'] for item in fixed_specs(order.amount, len(ordered), order.commission_fixed)]):
            raise ValueError('固定抽成快照不完整')
        specs = fixed_specs(receipt.deal_units, len(ordered), order.commission_fixed)
    elif (modes == {'PERCENT'} and order.commission_type == 'PERCENT'
          and all(not row.commission_fixed for row in ordered)
          and (not order.commission_override or all(row.commission_rate == order.commission_rate for row in ordered))):
        specs = [{'commission_type': 'PERCENT', 'commission_rate': row.commission_rate} for row in ordered]
    else:
        raise ValueError('抽成规则无法核对')
    shares = build_provider_shares(receipt.deal_units, specs)
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    plan_points_funding(deal_units=receipt.deal_units, point_discount_units=receipt.discount_units,
                        shares=shares, inviter_rate=rate)
    return dict(zip((row.provider_account_id for row in ordered), shares))


def assigned(attribution, order):
    if not enabled():
        from .principal_segments import enabled_for_order
        if not enabled_for_order(order):
            return
    _locked()
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution=attribution).first()
    if not coverage or coverage.status != 'PENDING':
        _review(coverage, '初始分段资金记录状态异常'); return
    if coverage.source == 'PLATFORM_SUBSIDY':
        from .models import OrderPlatformSubsidyShare
        segments = list(attribution.segments.order_by('slot'))
        assignments = list(order.providers.filter(released_at__isnull=True).order_by('pk'))
        terms = getattr(order, '_subsidy_entry_terms', None)
        subsidy = OrderPlatformSubsidy.objects.filter(order=order).first()
        funded = (OrderPlatformSubsidyShare.objects.filter(subsidy=subsidy, assignment=assignments[0]).first()
                  if subsidy and len(assignments) == 1 else None)
        from .delta_personal_commission import verify_assignment_evidence
        if (not subsidy_capture_enabled() or not subsidy or len(assignments) != 1 or len(segments) != 1
                or not funded or funded.paid_at or funded.retained_amount or order.amount != 0
                or coverage.deal_amount != subsidy.basis or not isinstance(terms, dict)
                or terms.get('assignment_id') != assignments[0].pk or not terms.get('share')
                or segments[0].provider_account_id != assignments[0].provider_account_id
                or terms['share'].settlement_base != subsidy.basis
                or terms['share'].provider_income != funded.amount
                or terms['share'].commission_rate != assignments[0].commission_rate
                or not verify_assignment_evidence(order, assignments[0])):
            _review(coverage, '安全箱接单补贴抽成快照不完整'); return
        share = terms['share']
        _capture(segments[0], assignments[0], share=share,
                 fixed_rule_total=(share.commission_fixed if share.commission_type == 'FIXED' else None),
                 commission_source=order.commission_source)
        coverage.status = 'ACTIVE'; coverage.save(update_fields=['status'])
        return
    # Current wallet checkout has no points-redemption integration. Subsidized
    # zero-pay orders use a distinct funding protocol and cannot be inferred.
    if OrderPlatformSubsidy.objects.filter(order=order).exists() or order.amount <= 0:
        _review(coverage, '零支付或补贴订单的成交基数尚待核对'); return
    assignments = list(order.providers.filter(released_at__isnull=True).order_by('pk'))
    segments = list(attribution.segments.order_by('slot'))
    by_account = {item.provider_account_id: item for item in assignments}
    if (len(assignments) != len(segments) or len(by_account) != len(assignments)
            or (coverage.source != 'WALLET_POINTS' and sum(item.settlement_base for item in assignments) != coverage.deal_amount)
            or any(segment.provider_account_id not in by_account for segment in segments)
            or any(not _matches(item) for item in assignments)
            or any(item.commission_type == 'FIXED' and
                   (order.commission_type != 'FIXED' or item.commission_fixed > order.commission_fixed)
                   for item in assignments)):
        _review(coverage, '初始人员、成交基数或抽成快照不完整'); return
    points_shares = None
    if coverage.source == 'WALLET_POINTS':
        try:
            points_shares = _points_shares(order, coverage, assignments, segments)
        except (ValueError, TypeError, RestValidationError):
            _review(coverage, '积分订单付款或抽成快照不完整'); return
    for segment in segments:
        assignment = by_account[segment.provider_account_id]
        _capture(segment, assignment, fixed_rule_total=(order.commission_fixed if assignment.commission_type == 'FIXED' else None),
                 commission_source=order.commission_source,
                 share=points_shares.get(segment.provider_account_id) if points_shares else None)
    coverage.status = 'ACTIVE'; coverage.save(update_fields=['status'])


def prestart_reassigned(attribution, previous, incoming, order, plan):
    if not enabled():
        from .principal_segments import enabled_for_order
        if not enabled_for_order(order):
            return
    _locked()
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution=attribution).first()
    if not coverage or coverage.status != 'ACTIVE':
        return
    if coverage.source == 'PLATFORM_SUBSIDY':
        from .models import OrderPlatformSubsidyShare, OrderPrestartHandoff
        from .state_machine import IllegalTransitionError
        prior = OrderSegmentFinancialSnapshot.objects.select_for_update().filter(segment=previous).first()
        replacement = order.providers.filter(provider_account_id=incoming.provider_account_id, released_at__isnull=True).first()
        handoff = OrderPrestartHandoff.objects.filter(order=order, old_provider_id=previous.provider_account_id,
            new_provider_id=incoming.provider_account_id).order_by('-pk').first()
        funded = OrderPlatformSubsidyShare.objects.filter(subsidy__order=order, assignment=replacement).first()
        subsidy = OrderPlatformSubsidy.objects.filter(order=order).first()
        mode, rate, fixed = plan.get('commission_type'), plan.get('commission_rate'), plan.get('commission_fixed')
        if (not subsidy_capture_enabled() or not prior or prior.retained_basis is not None or not replacement
                or not funded or funded.paid_at or funded.retained_amount or not subsidy or not handoff
                or coverage.deal_amount != subsidy.basis or prior.incoming_basis != subsidy.basis
                or mode not in ('PERCENT', 'FIXED') or type(rate) is not int or not 0 <= rate <= 100
                or type(fixed) is not int or fixed < 0 or (mode == 'PERCENT' and fixed)
                or (mode == 'FIXED' and (rate or fixed > subsidy.basis))
                or funded.amount != compute_provider_income(subsidy.basis, mode, rate, fixed)
                or handoff.old_snapshot.get('subsidy_amount') != prior.income_at_entry
                or handoff.new_snapshot.get('subsidy_amount') != funded.amount):
            raise IllegalTransitionError('安全箱改派抽成和补贴快照待核对')
        prior.retained_basis = 0; prior.save(update_fields=['retained_basis'])
        OrderSegmentFinancialSnapshot.objects.create(segment=incoming, incoming_basis=subsidy.basis,
            commission_type=mode, commission_rate=rate, commission_fixed=fixed,
            fixed_rule_total=fixed if mode == 'FIXED' else None, commission_source=plan.get('commission_source', ''),
            income_at_entry=funded.amount, game_commission_snapshot=deepcopy(plan.get('game_commission_snapshot', {})))
        return
    if coverage.source == 'WALLET_POINTS':
        from .commission import fixed_specs
        from .models import OrderPrestartHandoff
        from .settlement import split_settlement_bases
        from .state_machine import IllegalTransitionError
        from .team_rules import required_team_size
        prior = OrderSegmentFinancialSnapshot.objects.select_for_update().filter(segment=previous).first()
        replacement = order.providers.filter(provider_account_id=incoming.provider_account_id, released_at__isnull=True).first()
        handoff = OrderPrestartHandoff.objects.filter(order=order, old_provider_id=previous.provider_account_id,
            new_provider_id=incoming.provider_account_id).order_by('-pk').first()
        count, slot = required_team_size(order), incoming.slot - 1
        mode, rate, total = plan.get('commission_type'), plan.get('commission_rate'), plan.get('commission_fixed_total')
        from .points_business_access import permits
        prestart_enabled = (permits(order, phase='assignment', kind='service')
                            or getattr(settings, 'SERVICE_POINTS_PRESTART_TESTING', False))
        if (not prestart_enabled or not prior
                or prior.retained_basis is not None or not replacement or not handoff or not _matches(replacement)
                or count not in (1, 2) or not 0 <= slot < count or previous.slot != incoming.slot
                or prior.incoming_basis != split_settlement_bases(coverage.deal_amount, count)[slot]
                or replacement.settlement_base != split_settlement_bases(order.amount, count)[slot]
                or mode != replacement.commission_type or rate != replacement.commission_rate
                or replacement.game_commission_snapshot != plan.get('game_commission_snapshot', {})
                or handoff.old_snapshot.get('game_commission_snapshot') != prior.game_commission_snapshot):
            raise IllegalTransitionError('积分订单服务前改派的收益依据待核对')
        if mode == 'FIXED':
            if type(total) is not int or total < 0:
                raise IllegalTransitionError('积分订单改派固定抽成待核对')
            expected_paid = fixed_specs(order.amount, count, total)[slot]['commission_fixed']
            fixed = fixed_specs(coverage.deal_amount, count, total)[slot]['commission_fixed']
            if replacement.commission_fixed != expected_paid:
                raise IllegalTransitionError('积分订单改派固定抽成与付款份额不一致')
        elif mode == 'PERCENT' and type(rate) is int and 0 <= rate <= 100 and not replacement.commission_fixed:
            fixed, total = 0, None
        else:
            raise IllegalTransitionError('积分订单改派抽成规则无效')
        prior.retained_basis = 0
        prior.save(update_fields=['retained_basis'])
        OrderSegmentFinancialSnapshot.objects.create(segment=incoming, incoming_basis=prior.incoming_basis,
            commission_type=mode, commission_rate=rate, commission_fixed=fixed, fixed_rule_total=total,
            commission_source=plan.get('commission_source', ''),
            income_at_entry=compute_provider_income(prior.incoming_basis, mode, rate, fixed),
            game_commission_snapshot=deepcopy(replacement.game_commission_snapshot))
        return
    prior = OrderSegmentFinancialSnapshot.objects.select_for_update().filter(segment=previous).first()
    replacement = order.providers.filter(provider_account_id=incoming.provider_account_id, released_at__isnull=True).first()
    fixed_total = plan.get('commission_fixed_total')
    if (not prior or prior.retained_basis is not None or not replacement or not _matches(replacement)
            or (replacement.commission_type == 'FIXED' and (type(fixed_total) is not int
                or fixed_total < replacement.commission_fixed))
            or replacement.settlement_base != prior.incoming_basis):
        _review(coverage, '服务前改派的成交基数或抽成快照不完整'); return
    prior.retained_basis = 0; prior.save(update_fields=['retained_basis'])
    _capture(incoming, replacement, fixed_rule_total=fixed_total,
             commission_source=plan.get('commission_source', ''))


def _basis_before_transfer(attribution, current_event, expected_total):
    """Reconcile every previous segment; never invent a missed transfer."""
    segments = list(attribution.segments.exclude(pk=current_event.next_segment_id).order_by('pk'))
    snapshots = {item.segment_id: item for item in OrderSegmentFinancialSnapshot.objects.select_for_update()
                 .filter(segment__attribution=attribution)}
    allocations = list(OrderSegmentTransferBasis.objects.select_for_update()
        .filter(transfer__previous_segment__attribution=attribution).select_related('transfer'))
    outgoing = {item.transfer.previous_segment_id: item for item in allocations}
    incoming = {item.transfer.next_segment_id: item for item in allocations}
    total, current = 0, None
    for segment in segments:
        snap, entered, left = snapshots.get(segment.pk), incoming.get(segment.pk), outgoing.get(segment.pk)
        if snap and entered and snap.incoming_basis != entered.transferred_basis:
            raise ValueError('接入成交基数与转单记录不一致')
        initial = snap.incoming_basis if snap else entered.transferred_basis if entered else None
        if initial is None:
            raise ValueError('缺少此前履约段的成交基数')
        if left:
            if left.basis_before != initial or (snap and snap.retained_basis != left.retained_basis):
                raise ValueError('历史成交基数分配不一致')
            effective = left.retained_basis
        elif snap and snap.retained_basis is not None:
            effective = snap.retained_basis
        elif segment.pk == current_event.previous_segment_id or not segment.released_at:
            effective = initial
        else:
            raise ValueError('此前转出的成交份额缺少记录')
        if segment.pk == current_event.previous_segment_id:
            if left or (snap and snap.retained_basis is not None):
                raise ValueError('转出履约段已结束')
            current = initial
        total += effective
    if current is None or total != expected_total:
        raise ValueError('订单各履约段成交基数合计不一致')
    return current, snapshots.get(current_event.previous_segment_id)


def service_transferred(attribution, order, event, *, game_plan=None, commission_plan=None):
    """Persist agreed deal allocation while keeping settlement explicitly pending."""
    if not enabled():
        from .principal_segments import enabled_for_order
        from .ordinary_transfer_capability import permits as refunded_transfer_permits
        if not enabled_for_order(order) and not refunded_transfer_permits(order):
            return
    _locked()
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution=attribution).first()
    if not coverage or (coverage.status != 'ACTIVE' and
                        not (coverage.status == 'REVIEW' and coverage.review_reason in (TRANSFER_REVIEW, TRANSFER_RATE_REVIEW))):
        if game_plan is not None or commission_plan is not None:
            from .state_machine import IllegalTransitionError
            raise IllegalTransitionError('分段资金记录不完整，请先核对')
        return
    points_valid = False
    subsidy_valid = False
    if coverage.source == 'PLATFORM_SUBSIDY':
        from .subsidy_segment_capture import transfer_testing_enabled
        subsidy = OrderPlatformSubsidy.objects.select_for_update().filter(order=order).first()
        subsidy_valid = bool(transfer_testing_enabled() and subsidy and order.amount == 0
                             and subsidy.basis == coverage.deal_amount)
    if coverage.source == 'WALLET_POINTS':
        from .points_transfer_capture import enabled as points_enabled, locked_deal
        from .points_transfer_revision_scope import active as funding_revision_active
        from .points_transfer_refund_access import permits as points_transfer_permits
        # For a no-refund revision, transfer_capture already verified the
        # earlier add-on handover and the outgoing account under the order lock.
        if (points_transfer_permits(order, phase='transfer')
                and (not funding_revision_active(order) or order.refund_records.exists())):
            from .models import OrderAddonPayment
            if OrderAddonPayment.objects.filter(intent__order=order, handovers__status='ACCEPTED',
                    handovers__shares__segment_id=event.previous_segment_id,
                    handovers__shares__is_current=True).exists():
                from .state_machine import IllegalTransitionError
                raise IllegalTransitionError('加购负责人转单须先完成加购交接')
        if order.refund_records.exists() and points_transfer_permits(order, phase='transfer'):
            points_valid = coverage.deal_amount == order.original_amount - order.boss_discount - order.promo_discount - order.coupon_discount
        elif points_enabled():
            try:
                points_valid = locked_deal(order, coverage,
                    allow_existing_allocations=funding_revision_active(order)) == coverage.deal_amount
            except (RestValidationError, ValueError):
                points_valid = False
    ordinary_after_refund = False
    if order.refund_records.exists() and coverage.source == 'WALLET_NO_POINTS':
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        ordinary_after_refund = ordinary_transfer_permits(order)
    if ((not points_valid and not subsidy_valid and (coverage.source != 'WALLET_NO_POINTS' or coverage.deal_amount != order.amount))
            or (order.refund_records.exists() and not ordinary_after_refund and not points_valid)):
        if game_plan is not None or commission_plan is not None:
            from .state_machine import IllegalTransitionError
            raise IllegalTransitionError('订单成交及退款来源待核对')
        coverage.status = 'REVIEW'; coverage.review_reason = '订单成交及退款来源待核对'
        coverage.save(update_fields=['status', 'review_reason']); return
    if (points_valid or subsidy_valid) and game_plan is None and commission_plan is None:
        from .state_machine import IllegalTransitionError
        raise IllegalTransitionError('订单转入抽成快照尚未接通')
    from .transfer_economics import allocate_transfer_basis
    try:
        before, snapshot = _basis_before_transfer(attribution, event, coverage.deal_amount)
        plan = allocate_transfer_basis(before, event.original_share_before,
                                       event.retained_share, event.transferred_share)
    except ValueError as exc:
        if game_plan is not None or commission_plan is not None:
            from .state_machine import IllegalTransitionError
            raise IllegalTransitionError(str(exc)) from exc
        coverage.status = 'REVIEW'; coverage.review_reason = str(exc)[:255]
        coverage.save(update_fields=['status', 'review_reason']); return
    if commission_plan is not None and snapshot is None:
        from .state_machine import IllegalTransitionError
        raise IllegalTransitionError('原履约段抽成快照缺失，请先核对')
    same_rule_fixed = None
    fixed_fee_store_shortfall = None
    if (commission_plan is not None and commission_plan['commission_type'] == 'FIXED'
            and snapshot.commission_type == 'FIXED'
            and snapshot.fixed_rule_total == commission_plan.get('commission_fixed_total')
            and coverage.deal_amount > 0):
        from .transfer_economics import allocate_same_rule_fixed_seat
        try:
            _, same_rule_fixed, fixed_fee_store_shortfall = allocate_same_rule_fixed_seat(coverage.deal_amount, snapshot.fixed_rule_total,
                snapshot.commission_fixed, plan.retained, plan.transferred)
        except ValueError as exc:
            from .state_machine import IllegalTransitionError
            raise IllegalTransitionError(str(exc)) from exc
    OrderSegmentTransferBasis.objects.create(transfer=event, basis_before=plan.total,
        retained_basis=plan.retained, transferred_basis=plan.transferred,
        remainder_numerator=plan.remainder_numerator, fixed_fee_store_shortfall=fixed_fee_store_shortfall)
    if snapshot:
        snapshot.retained_basis = plan.retained
        snapshot.save(update_fields=['retained_basis'])
    if game_plan is not None:
        rate = game_plan['rate']
        OrderSegmentFinancialSnapshot.objects.create(segment=event.next_segment,
            incoming_basis=plan.transferred, commission_type='PERCENT', commission_rate=rate,
            commission_fixed=0, fixed_rule_total=None, commission_source='game_commission',
            income_at_entry=compute_provider_income(plan.transferred, 'PERCENT', rate, 0),
            game_commission_snapshot=deepcopy(game_plan))
    elif commission_plan is not None:
        mode = commission_plan['commission_type']
        rate = commission_plan['commission_rate']
        fixed_total = commission_plan.get('commission_fixed_total')
        if mode == 'FIXED':
            if type(fixed_total) is not int or fixed_total < 0 or coverage.deal_amount <= 0:
                from .state_machine import IllegalTransitionError
                raise IllegalTransitionError('接替陪玩固定抽成快照无效')
            # An unchanged rule preserves the original seat's recorded fee;
            # a changed rule uses the new fee proportion, rounded upward.
            fixed = (same_rule_fixed if same_rule_fixed is not None else
                (fixed_total * plan.transferred + coverage.deal_amount - 1) // coverage.deal_amount)
        else:
            fixed = 0
            fixed_total = None
        OrderSegmentFinancialSnapshot.objects.create(segment=event.next_segment,
            incoming_basis=plan.transferred, commission_type=mode, commission_rate=rate,
            commission_fixed=fixed, fixed_rule_total=fixed_total,
            commission_source=commission_plan['commission_source'],
            income_at_entry=compute_provider_income(plan.transferred, mode, rate, fixed),
            game_commission_snapshot=deepcopy(commission_plan.get('game_commission_snapshot', {})))
    coverage.status = 'REVIEW'; coverage.review_reason = TRANSFER_RATE_REVIEW if game_plan or commission_plan else TRANSFER_REVIEW
    coverage.save(update_fields=['status', 'review_reason'])
