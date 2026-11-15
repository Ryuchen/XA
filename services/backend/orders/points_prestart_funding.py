"""Rebuild points funding after an unstarted provider replacement."""
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from .commission import fixed_specs
from .models import (OrderPointsRedemption, OrderPrestartHandoff, OrderProvider,
                     OrderSegmentFinancialSnapshot)
from .points_funding_plan import PointsFundingPlanError, plan_points_funding
from .settlement import ProviderShare, compute_provider_income, split_settlement_bases
from .team_rules import required_team_size


def plan_prestart_order(order, attribution, coverage):
    """Validate each no-service handoff and rebuild the live per-seat shares."""
    from .points_service_payment import MARKER
    from .points_service_refund import _plan as validate_payment_assets
    receipt = OrderPointsRedemption.objects.select_for_update().filter(order=order).first()
    if (not receipt or not isinstance(order.funding_terms, dict)
            or order.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}
            or receipt.paid_units != order.amount or receipt.deal_units != coverage.deal_amount
            or receipt.deal_units != order.original_amount - order.boss_discount - order.promo_discount - order.coupon_discount):
        raise PointsFundingPlanError('积分订单付款凭证待核对')
    try:
        validate_payment_assets(order, None, receipt.deal_units)
    except ValidationError as exc:
        raise PointsFundingPlanError('积分订单付款来源待核对') from exc
    segments = list(attribution.segments.select_for_update().order_by('slot', 'pk'))
    snapshots = {row.segment_id: row for row in OrderSegmentFinancialSnapshot.objects.select_for_update()
                 .filter(segment__attribution=attribution)}
    events = list(OrderPrestartHandoff.objects.select_for_update().filter(order=order).order_by('pk'))
    active = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update()
              .filter(order=order, released_at__isnull=True)}
    count = required_team_size(order)
    if (count not in (1, 2) or len(active) != count or len(snapshots) != len(segments)
            or len(segments) != count + len(events)):
        raise PointsFundingPlanError('积分订单改派记录待核对')
    deal_bases = split_settlement_bases(receipt.deal_units, count)
    paid_bases = split_settlement_bases(receipt.paid_units, count)
    used, shares = set(), []
    for slot in range(1, count + 1):
        chain = [segment for segment in segments if segment.slot == slot]
        if not chain or chain[-1].released_at or any(not item.released_at for item in chain[:-1]):
            raise PointsFundingPlanError('积分订单改派席位待核对')
        for index, segment in enumerate(chain):
            snapshot = snapshots[segment.pk]
            if (snapshot.incoming_basis != deal_bases[slot - 1]
                    or snapshot.income_at_entry != compute_provider_income(snapshot.incoming_basis,
                        snapshot.commission_type, snapshot.commission_rate, snapshot.commission_fixed)
                    or snapshot.commission_type not in ('PERCENT', 'FIXED')
                    or not 0 <= snapshot.commission_rate <= 100
                    or (index < len(chain) - 1 and (segment.started_at or snapshot.retained_basis != 0))
                    or (index == len(chain) - 1 and snapshot.retained_basis is not None)):
                raise PointsFundingPlanError('积分订单改派收益快照待核对')
            if snapshot.commission_type == 'PERCENT':
                if snapshot.commission_fixed or snapshot.fixed_rule_total is not None:
                    raise PointsFundingPlanError('积分订单百分比抽成待核对')
                paid_fee = 0
            else:
                total = snapshot.fixed_rule_total
                if type(total) is not int or total < 0 or snapshot.commission_rate:
                    raise PointsFundingPlanError('积分订单固定抽成待核对')
                if snapshot.commission_fixed != fixed_specs(receipt.deal_units, count, total)[slot - 1]['commission_fixed']:
                    raise PointsFundingPlanError('积分订单成交固定抽成待核对')
                paid_fee = fixed_specs(receipt.paid_units, count, total)[slot - 1]['commission_fixed']

            def valid_paid(data):
                return (isinstance(data, dict) and data.get('provider_account_id') == segment.provider_account_id
                        and data.get('settlement_base') == paid_bases[slot - 1]
                        and data.get('commission_type') == snapshot.commission_type
                        and data.get('commission_rate') == snapshot.commission_rate
                        and data.get('commission_fixed') == paid_fee
                        and data.get('game_commission_snapshot') == snapshot.game_commission_snapshot
                        and data.get('provider_income') == compute_provider_income(paid_bases[slot - 1],
                            snapshot.commission_type, snapshot.commission_rate, paid_fee))

            if index:
                previous = chain[index - 1]
                matches = [event for event in events if event.pk not in used
                           and event.old_provider_id == previous.provider_account_id
                           and event.new_provider_id == segment.provider_account_id]
                if (not matches or previous.released_at != segment.accepted_at
                        or previous.original_share != segment.original_share):
                    raise PointsFundingPlanError('积分订单改派顺序待核对')
                event = matches[0]
                if event.created_at < segment.accepted_at or not valid_paid(event.new_snapshot):
                    raise PointsFundingPlanError('积分订单接替陪玩凭证待核对')
                used.add(event.pk)
            if index < len(chain) - 1:
                following = chain[index + 1]
                matches = [event for event in events if event.old_provider_id == segment.provider_account_id
                           and event.new_provider_id == following.provider_account_id]
                if not matches or not valid_paid(matches[0].old_snapshot):
                    raise PointsFundingPlanError('积分订单原陪玩凭证待核对')
            else:
                assignment = active.get(segment.provider_account_id)
                if not assignment:
                    raise PointsFundingPlanError('积分订单接替陪玩不存在')
                from .prestart_handoff import _snapshot
                if not valid_paid(_snapshot(assignment)):
                    raise PointsFundingPlanError('积分订单当前陪玩份额待核对')
                shares.append(ProviderShare(snapshot.incoming_basis, snapshot.commission_type,
                    snapshot.commission_rate, snapshot.commission_fixed, snapshot.income_at_entry))
    if len(used) != len(events) or sum(row.settlement_base for row in active.values()) != order.amount:
        raise PointsFundingPlanError('积分订单改派记录不完整')
    sources = {row['kind']: row['total'] for row in order.funding_allocations.values('kind').annotate(total=Sum('amount'))}
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    return plan_points_funding(deal_units=receipt.deal_units, point_discount_units=receipt.discount_units,
        shares=shares, inviter_rate=rate, principal_units=sources.get('PREPAID', 0),
        gift_units=sources.get('GIFT', 0), compensation_units=sources.get('COMPENSATION', 0))
