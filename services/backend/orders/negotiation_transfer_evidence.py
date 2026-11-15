"""Evidence for the ordinary-transfer negotiation path under development."""
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderOriginalShareTransfer, OrderSegmentFinancialSnapshot
from .segment_settlement_plan import build_segment_settlement_plan


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _segment_facts(order):
    segments = list(order.original_attribution.segments.select_for_update().order_by('pk'))
    snapshots = {row.segment_id: row for row in OrderSegmentFinancialSnapshot.objects.select_for_update().filter(
        segment__attribution__order=order)}
    if len(snapshots) != len(segments):
        raise ValidationError('转单抽成快照不完整')
    facts = []
    for segment in segments:
        snap = snapshots[segment.pk]
        facts.append({'id': segment.pk, 'account_id': segment.provider_account_id, 'slot': segment.slot,
            'original_share': segment.original_share, 'accepted_at': segment.accepted_at.isoformat(),
            'started_at': segment.started_at.isoformat() if segment.started_at else None,
            'incoming_basis': snap.incoming_basis, 'retained_basis': snap.retained_basis,
            'commission_type': snap.commission_type, 'commission_rate': snap.commission_rate,
            'commission_fixed': snap.commission_fixed, 'fixed_rule_total': snap.fixed_rule_total,
            'income_at_entry': snap.income_at_entry})
    transfers = list(OrderOriginalShareTransfer.objects.select_for_update().filter(
        previous_segment__attribution__order=order).order_by('pk').values(
            'id', 'previous_segment_id', 'next_segment_id', 'original_share_before',
            'retained_share', 'transferred_share'))
    return facts, transfers


def make_snapshot_locked(order, calculation):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转单协商快照须在门店事务内生成')
    plan = build_segment_settlement_plan(order, allow_recorded=True)
    facts, transfers = _segment_facts(order)
    return {'order_id': order.pk, 'paid_units': order.amount, 'original_units': order.original_amount,
            'segments': facts, 'transfers': transfers, 'lines': [asdict(line) for line in plan.segments],
            'provider_terms': calculation['provider_terms'], 'rounding_to_store_units': calculation['rounding_to_store_units'],
            'selected_segment_ids': calculation['selected_segment_ids'],
            'segment_pending_plan': calculation['segment_pending_plan'],
            'ordinary_paid_settlement_snapshot': calculation.get('ordinary_paid_settlement_snapshot'),
            'selected_addon_deductions': {str(k): v for k, v in calculation['selected_addon_deductions'].items()},
            'prior_addon_deductions': {str(k): v for k, v in calculation.get('prior_addon_deductions', {}).items()},
            'funding_if_recovered': calculation['funding_if_recovered'], 'return_sources': calculation['return_sources']}


def persist_locked(execution, snapshot):
    from .models import OrderTransferNegotiationEvidence
    return OrderTransferNegotiationEvidence.objects.create(execution=execution, order_id=snapshot['order_id'],
        snapshot=snapshot, snapshot_hash=_digest(snapshot))


def _verify_historical_prefix(evidence, facts, transfers):
    """Keep the original agreement valid when another seat later transfers."""
    from django.utils.dateparse import parse_datetime
    from .models import OrderSegmentTransferBasis
    snapshot = evidence.snapshot
    old_segments, old_transfers = snapshot.get('segments'), snapshot.get('transfers')
    if not isinstance(old_segments, list) or not isinstance(old_transfers, list):
        return False
    current = {row['id']: row for row in facts}
    present_transfers = {row['id']: row for row in transfers}
    if any(present_transfers.get(row.get('id')) != row for row in old_transfers):
        return False
    old_transfer_ids = {row['id'] for row in old_transfers}
    later = list(OrderOriginalShareTransfer.objects.select_for_update().filter(
        previous_segment__attribution__order_id=evidence.order_id).exclude(pk__in=old_transfer_ids)
        .select_related('previous_segment', 'next_segment').order_by('pk'))
    later_by_previous = {row.previous_segment_id: row for row in later}
    if any(row.created_at <= evidence.created_at for row in later):
        return False
    for old in old_segments:
        now = current.get(old.get('id'))
        if not now:
            return False
        expected = dict(old)
        event = later_by_previous.get(old['id'])
        if event:
            basis = OrderSegmentTransferBasis.objects.select_for_update().filter(transfer=event).first()
            if (not basis or old['retained_basis'] is not None
                    or old['original_share'] != event.original_share_before
                    or now['original_share'] != event.retained_share
                    or basis.basis_before != old['incoming_basis']
                    or now['retained_basis'] != basis.retained_basis):
                return False
            expected['original_share'], expected['retained_basis'] = now['original_share'], now['retained_basis']
        if old['started_at'] is None and now['started_at'] is not None:
            started = parse_datetime(now['started_at'])
            if not started or started <= evidence.created_at:
                return False
            expected['started_at'] = now['started_at']
        if expected != now:
            return False
    known = {row['id'] for row in old_segments}
    new_segments = {row['id'] for row in facts if row['id'] not in known}
    if new_segments != {row.next_segment_id for row in later}:
        return False
    for row in facts:
        if row['id'] in new_segments:
            accepted = parse_datetime(row['accepted_at'])
            if not accepted or accepted <= evidence.created_at:
                return False
    return True


def verify_locked(execution):
    from .models import OrderTransferNegotiationEvidence
    evidence = OrderTransferNegotiationEvidence.objects.select_for_update().filter(execution=execution).first()
    if not evidence:
        return None
    order = execution.refund.order if execution.refund_id else execution.zero_order
    if not order:
        raise ValidationError('转单协商订单凭证缺失')
    snapshot = evidence.snapshot
    if (not isinstance(snapshot, dict) or evidence.order_id != order.pk or snapshot.get('order_id') != order.pk
            or snapshot.get('paid_units') != order.amount or snapshot.get('original_units') != order.original_amount
            or snapshot.get('provider_terms') != execution.proposal.provider_terms
            or snapshot.get('segment_pending_plan') != execution.proposal.ordinary_segment_pending_plan
            or snapshot.get('ordinary_paid_settlement_snapshot') != execution.proposal.ordinary_paid_settlement_snapshot
            or snapshot.get('rounding_to_store_units') != execution.rounding_to_store_units
            or evidence.snapshot_hash != _digest(snapshot)):
        raise ValidationError('普通转单协商快照待核对')
    facts, transfers = _segment_facts(order)
    if not _verify_historical_prefix(evidence, facts, transfers):
        raise ValidationError('转单履约或抽成快照已变化')
    selected = snapshot.get('selected_segment_ids')
    if (not isinstance(selected, list) or not selected or sorted(selected) != selected
            or len(set(selected)) != len(selected) or selected != execution.proposal.affected_segment_ids):
        raise ValidationError('转单协商履约段范围待核对')
    segments = {item['id']: item for item in snapshot['segments'] if item['id'] in selected}
    if (set(segments) != set(selected) or any(not item.get('started_at') for item in segments.values())
            or {item['account_id'] for item in segments.values()} != set(execution.proposal.affected_accounts)):
        raise ValidationError('转单协商履约段归属待核对')
    from .ordinary_retained_segment_shares import verify_plan
    segment_map = defaultdict(list)
    for segment_id, item in segments.items():
        segment_map[str(item['account_id'])].append(segment_id)
    try:
        verify_plan({'segments': dict(segment_map), 'segment_pending_plan': snapshot.get('segment_pending_plan')},
                    list(execution.shares.all()))
    except ValueError as exc:
        raise ValidationError('首轮转单协商逐段扣款凭证异常') from exc
    income = defaultdict(int)
    for line in snapshot.get('lines', []):
        if not isinstance(line, dict) or not all(type(line.get(k)) is int for k in ('segment_id', 'account_id', 'income')):
            raise ValidationError('转单收益快照待核对')
        if line['segment_id'] in segments:
            income[line['account_id']] += line['income']
    from .addon_main_deduction import deductions_at
    prior = {str(k): v for k, v in deductions_at(order, before=execution.created_at).items()}
    if prior != snapshot.get('prior_addon_deductions', {}):
        raise ValidationError('协商前加购退款的主单扣减已变化')
    from .addon_main_segment_deduction import verified_deductions
    per_segment = verified_deductions(order, before=execution.created_at)
    selected_deductions = defaultdict(int)
    for pk, segment in segments.items():
        selected_deductions[segment['account_id']] += per_segment.get(pk, 0)
    if {str(k): v for k, v in selected_deductions.items()} != snapshot.get('selected_addon_deductions'):
        raise ValidationError('所选履约段加购扣减已变化')
    if any(income[term['account_id']] - selected_deductions[term['account_id']]
           != term['original_income_units'] for term in snapshot['provider_terms']):
        raise ValidationError('转单协商收益与所选履约段不一致')
    return evidence
