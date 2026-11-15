"""Reconstruct the funding need at a pre-service reassignment boundary."""
import hashlib
import json
from dataclasses import asdict

from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from .commission import fixed_specs
from .models import (OrderParticipationSegment, OrderPointsRedemption, OrderPrestartHandoff,
                     OrderSegmentFinancialSnapshot)
from .points_funding_plan import PointsFundingPlanError, plan_points_funding
from .settlement import ProviderShare, compute_provider_income, split_settlement_bases
from .team_rules import required_team_size


def capture(order, handoff):
    """Replay completed prestart handoffs, even after later order changes."""
    receipt = OrderPointsRedemption.objects.filter(order=order).first()
    count = required_team_size(order)
    if not receipt or handoff.order_id != order.pk or count not in (1, 2):
        raise ValidationError('改派补资付款依据缺失')
    segments = list(OrderParticipationSegment.objects.filter(attribution__order=order).order_by('pk'))
    snapshots = {s.segment_id: s for s in OrderSegmentFinancialSnapshot.objects.filter(
        segment__attribution__order=order)}
    chains = {slot: [s for s in segments if s.slot == slot] for slot in range(1, count + 1)}
    if any(not chain for chain in chains.values()):
        raise ValidationError('改派补资履约席位缺失')
    positions = {slot: 0 for slot in chains}
    events = list(OrderPrestartHandoff.objects.filter(order=order, pk__lte=handoff.pk).order_by('pk'))
    if not events or events[-1].pk != handoff.pk:
        raise ValidationError('改派补资变更记录缺失')
    paid_bases = split_settlement_bases(receipt.paid_units, count)
    deal_bases = split_settlement_bases(receipt.deal_units, count)

    def share(segment, data=None):
        snap = snapshots.get(segment.pk)
        slot = segment.slot - 1
        if not snap or snap.incoming_basis != deal_bases[slot]:
            raise ValidationError('改派补资收益快照缺失')
        if snap.commission_type == 'PERCENT':
            if snap.commission_fixed or snap.fixed_rule_total is not None:
                raise ValidationError('改派百分比抽成快照异常')
            paid_fee = 0
        elif snap.commission_type == 'FIXED':
            if type(snap.fixed_rule_total) is not int or snap.fixed_rule_total < 0 or snap.commission_rate:
                raise ValidationError('改派固定抽成快照异常')
            if snap.commission_fixed != fixed_specs(receipt.deal_units, count, snap.fixed_rule_total)[slot]['commission_fixed']:
                raise ValidationError('改派固定抽成成交份额异常')
            paid_fee = fixed_specs(receipt.paid_units, count, snap.fixed_rule_total)[slot]['commission_fixed']
        else:
            raise ValidationError('改派抽成类型异常')
        income = compute_provider_income(snap.incoming_basis, snap.commission_type,
                                         snap.commission_rate, snap.commission_fixed)
        if not 0 <= snap.commission_rate <= 100 or income != snap.income_at_entry:
            raise ValidationError('改派收益快照异常')
        if data is not None:
            expected = {'provider_account_id': segment.provider_account_id, 'settlement_base': paid_bases[slot],
                'commission_type': snap.commission_type, 'commission_rate': snap.commission_rate,
                'commission_fixed': paid_fee, 'game_commission_snapshot': snap.game_commission_snapshot,
                'provider_income': compute_provider_income(paid_bases[slot], snap.commission_type,
                                                           snap.commission_rate, paid_fee)}
            if not isinstance(data, dict) or any(data.get(k) != v for k, v in expected.items()):
                raise ValidationError('改派陪玩及收益凭证异常')
        return ProviderShare(snap.incoming_basis, snap.commission_type, snap.commission_rate,
                             snap.commission_fixed, income)

    for event in events:
        matches = [slot for slot, chain in chains.items()
                   if chain[positions[slot]].provider_account_id == event.old_provider_id]
        if len(matches) != 1:
            raise ValidationError('改派席位顺序异常')
        slot = matches[0]
        chain, index = chains[slot], positions[slot]
        if index + 1 >= len(chain):
            raise ValidationError('改派接替席位缺失')
        old, new = chain[index:index + 2]
        share(old, event.old_snapshot)
        share(new, event.new_snapshot)
        if (new.provider_account_id != event.new_provider_id or old.started_at
                or old.released_at != new.accepted_at or event.created_at < new.accepted_at
                or old.original_share != new.original_share or snapshots[old.pk].retained_basis != 0):
            raise ValidationError('改派履约记录异常')
        positions[slot] += 1
    selected = [chains[slot][positions[slot]] for slot in sorted(chains)]
    if len({row.provider_account_id for row in selected}) != count:
        raise ValidationError('改派陪玩席位重复')
    shares = [share(row) for row in selected]
    sources = {row['kind']: row['total'] for row in order.funding_allocations.values('kind').annotate(total=Sum('amount'))}
    try:
        plan = plan_points_funding(deal_units=receipt.deal_units, point_discount_units=receipt.discount_units,
            shares=shares, inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
            principal_units=sources.get('PREPAID', 0), gift_units=sources.get('GIFT', 0),
            compensation_units=sources.get('COMPENSATION', 0))
    except PointsFundingPlanError as exc:
        raise ValidationError('改派补资计划异常') from exc
    return {'receipt_id': receipt.pk, 'events': [event.pk for event in events],
            'segments': [row.pk for row in selected], 'funding': asdict(plan)}


def digest(run, facts):
    value = [run.prestart_handoff_id, run.required_amount, run.allocated_before,
             run.released_amount, run.allocation_snapshot, facts]
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify(run):
    if (not run.prestart_handoff_id or not isinstance(run.prestart_snapshot, dict)
            or run.prestart_hash != digest(run, run.prestart_snapshot)):
        raise ValidationError('改派补资版本凭证异常')
    facts = capture(run.prestart_handoff.order, run.prestart_handoff)
    if facts != run.prestart_snapshot or facts['funding']['additional_store_funding_units'] != run.required_amount:
        raise ValidationError('改派补资版本与订单不一致')
