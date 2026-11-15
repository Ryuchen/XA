"""Verify funded safety-box entry snapshots before an entitlement is used."""
from .models import (OrderOriginalAttribution, OrderOriginalShareTransfer, OrderPlatformSubsidy,
                     OrderPrestartHandoff, OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot)
from .settlement import SettlementError, compute_provider_income


def transfer_testing_enabled():
    """The transfer is not production-ready until its segment payout exists."""
    from django.conf import settings
    from .segment_financial_capture import subsidy_capture_enabled
    from .subsidy_transfer_capability import stage_enabled
    return bool(subsidy_capture_enabled() and stage_enabled('VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING')
                and getattr(settings, 'SEGMENT_REPORT_ENABLED', False)
                and getattr(settings, 'PRESTART_SCOPE_SNAPSHOT_ENABLED', False))


def verify_entry_capture(order, shares, execution=None):
    """Pre-service changes retain no earnings; all entry terms must reconcile."""
    attribution = OrderOriginalAttribution.objects.filter(order=order).first()
    coverage = OrderSegmentFinancialCoverage.objects.filter(attribution=attribution).first() if attribution else None
    snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution=attribution)
                     .select_related('segment').order_by('segment_id')) if attribution else []
    if not coverage or coverage.source != 'PLATFORM_SUBSIDY':
        # Legacy wallet captures use the same tables; only fail for a
        # zero-paid subsidy with segment evidence that claims a funded basis.
        if snapshots and order.amount == 0 and any(row.incoming_basis > 0 for row in snapshots):
            raise SettlementError('安全箱补贴基数来源待核对')
        return
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution).exists():
        from .subsidy_transfer_capture import transfer_amounts
        amounts, retained = transfer_amounts(order, allow_recorded=any(row.paid_at for row in shares))
        expected = amounts
        if execution:
            from .subsidy_transfer_capability import stage_enabled
            if not stage_enabled('VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING'):
                raise SettlementError('安全箱转单后协商结算尚未开放')
            expected = {entry['account_id']:entry['retained'] for entry in execution.shares}
        if (set(amounts) - {row.assignment.provider_account_id for row in shares}
                or set(expected) - {row.assignment.provider_account_id for row in shares}
                or any(row.amount != expected.get(row.assignment.provider_account_id)
                       or row.retained_amount != retained[row.assignment.provider_account_id] for row in shares)):
            raise SettlementError('安全箱转单补贴与分段快照不一致')
        return
    subsidy = OrderPlatformSubsidy.objects.filter(order=order).first()
    if (not subsidy or not attribution or attribution.status != 'ACTIVE' or coverage.status != 'ACTIVE'
            or coverage.deal_amount != subsidy.basis or order.amount != 0
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution).exists()):
        raise SettlementError('安全箱分段补贴基数待核对')
    segments = list(attribution.segments.order_by('pk'))
    handoffs = list(OrderPrestartHandoff.objects.filter(order=order).order_by('pk'))
    active_count = sum(row.released_at is None for row in segments)
    if (not segments or len(segments) != len(snapshots) or len(handoffs) != len(segments)-1
            or [row.pk for row in segments] != [row.segment_id for row in snapshots]
            or (active_count != 1 if not execution else active_count not in (0, 1))):
        raise SettlementError('安全箱接单及改派记录待核对')
    amounts = {row.assignment.provider_account_id: 0 for row in shares}
    if len(amounts) != len(shares):
        raise SettlementError('安全箱陪玩补贴身份待核对')
    from .delta_personal_commission import verify_segment_evidence
    personal_order = bool((order.game_commission_snapshot or {}).get('personal_rules'))
    for index, (segment, snapshot) in enumerate(zip(segments, snapshots)):
        if (segment.provider_account_id not in amounts or segment.slot != segments[0].slot
                or segment.original_share != attribution.original_amount
                or snapshot.incoming_basis != subsidy.basis
                or (personal_order and not verify_segment_evidence(segment, snapshot))
                or snapshot.income_at_entry != compute_provider_income(snapshot.incoming_basis,
                    snapshot.commission_type, snapshot.commission_rate, snapshot.commission_fixed)
                or (snapshot.commission_type == 'PERCENT' and
                    (snapshot.commission_fixed or snapshot.fixed_rule_total is not None))
                or (snapshot.commission_type == 'FIXED' and snapshot.fixed_rule_total != snapshot.commission_fixed)):
            raise SettlementError('安全箱接单抽成快照待核对')
        if index < len(segments)-1:
            following, handoff = segments[index+1], handoffs[index]
            if (not segment.released_at or segment.started_at is not None or snapshot.retained_basis != 0
                    or segment.released_at != following.accepted_at
                    or handoff.old_provider_id != segment.provider_account_id
                    or handoff.new_provider_id != following.provider_account_id
                    or not isinstance(handoff.old_snapshot, dict) or not isinstance(handoff.new_snapshot, dict)
                    or handoff.old_snapshot.get('subsidy_amount') != snapshot.income_at_entry
                    or handoff.new_snapshot.get('subsidy_amount') != snapshots[index+1].income_at_entry):
                raise SettlementError('安全箱服务前改派凭证待核对')
        else:
            if (snapshot.retained_basis is not None or (segment.released_at is not None
                    and (not execution or segment.released_at != execution.accepted_at))):
                raise SettlementError('安全箱当前履约段待核对')
            amounts[segment.provider_account_id] = snapshot.income_at_entry
    originals = ({row['share_id']:row['original'] for row in execution.shares} if execution else {})
    if any((originals.get(row.pk) if execution else row.amount) != amounts[row.assignment.provider_account_id]
           for row in shares):
        raise SettlementError('安全箱预期补贴与接单抽成不一致')
