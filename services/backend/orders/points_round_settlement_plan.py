"""Final plan for verified later points-refund rounds and paid add-ons."""
from dataclasses import asdict, replace
from copy import deepcopy

from stores.db import transaction
from .settlement import SettlementError


def prepare_locked(order_id, *, now=None, allow_finalized=False):
    from .models import Order, OrderAddonPayment, PointsNegotiationPosting, OrderPointsFundingAllocation
    from .points_transfer_refund_access import permits, verification_scope
    from .points_round_funding import current_locked
    from .points_transfer_refund_evidence import _reconstruct_from_verified_facts
    from .points_transfer_refunded_plan import build_from_verified_evidence
    from .segment_settlement import _terms
    from .points_round_receipt_usage import verify_locked
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if (not transaction.get_connection().in_atomic_block or not order
            or not any(permits(order, phase=phase) for phase in ('refund', 'settlement'))
            or order.status != Order.Status.COMPLETED or not order.completed_at
            or (order.settled_at and not allow_finalized) or order.refund_records.count() < 4):
        raise SettlementError('多轮退款结算范围尚未接通')
    with verification_scope(order):
        state = current_locked(order)
        latest, funding = state['round'], state['funding']
        verify_locked(latest)
        if funding.remaining_units or funding.allocated_units != funding.required_units:
            raise SettlementError('多轮退款整单补资尚未足额')
        transfer = state['transfer'] or latest.transfer_revision
        facts = deepcopy(transfer.snapshot['facts'])
        from .models import OrderParticipationSegment
        for item in facts['segments']:
            if not item.get('started_at'):
                segment = OrderParticipationSegment.objects.filter(pk=item['id'], attribution__order=order).first()
                if segment and segment.started_at:
                    item['started_at'], item['start_source'] = segment.started_at.isoformat(), segment.start_source
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
            .select_related('proposal', 'points_refund').order_by('created_at', 'pk'))
        history = _reconstruct_from_verified_facts(order.pk, facts,
            refund_id=postings[0].points_refund.refund_id, through_transfer_id=transfer.transfer_id)
        addon_parts, addon_totals, addon_history, addon_refunds = [], {}, None, []
        has_addons = OrderAddonPayment.objects.filter(intent__order=order).exists()
        plan_funding = funding
        if has_addons:
            from .models import PointsRefundRoundEvidence
            from .negotiation_models import PointsNegotiationAddonDeduction
            from .points_third_refund_settlement_plan import _addon_history_locked, _third_addon_events_locked
            third = PointsRefundRoundEvidence.objects.filter(order=order).order_by('posting__created_at', 'pk').first()
            if not third:
                raise SettlementError('多轮退款加购缺少第三轮来源')
            addon_history = _addon_history_locked(order, third)
            addon_events = _third_addon_events_locked(order, third)
            deductions = {}
            for payment_id, amount in PointsNegotiationAddonDeduction.objects.filter(
                    share__posting__in=postings).values_list('payment_id', 'amount'):
                deductions[payment_id] = deductions.get(payment_id, 0) + amount
            addon_income = sum(item['provider_units'] - deductions.get(item['payment_id'], 0)
                               for item in addon_history)
            if addon_income < 0 or addon_income > funding.provider_income_units:
                raise SettlementError('多轮退款加购未结收益异常')
            plan_funding = replace(funding, provider_income_units=funding.provider_income_units - addon_income)
        plan, _ = build_from_verified_evidence(order, facts, history, postings, plan_funding, now=now)
        if has_addons:
            from .addon_payment import verify_order_debit_links, verify_receipt
            from .addon_handover import assert_no_pending_handover
            from .points_third_addon_responsibilities import current as current_responsibilities
            from .addon_completion import verify_accepted
            from .addon_main_segment_deduction import verified_deductions
            keys = ('paid_units', 'principal_units', 'gift_units', 'compensation_units',
                    'provider_units', 'inviter_units', 'recovery_units')
            addon_totals = {key: 0 for key in keys}
            ids = [item['payment_id'] for item in addon_history]
            if len(ids) != len(set(ids)) or set(ids) != set(OrderAddonPayment.objects.filter(
                    intent__order=order).values_list('pk', flat=True)):
                raise SettlementError('多轮退款加购付款历史不完整')
            eligible = {(segment_id, person.account_id) for person in plan.persons for segment_id in person.segment_ids}
            verify_order_debit_links(order, table_exists=True, lock=True)
            for expected in addon_history:
                payment = OrderAddonPayment.objects.select_for_update().select_related('intent').get(
                    pk=expected['payment_id'], intent__order=order)
                verify_receipt(payment)
                assert_no_pending_handover(payment)
                handover, parts = current_responsibilities(payment, lock=True, with_share_id=True)
                refunds = list(payment.refund_records.order_by('created_at', 'pk'))
                net_expected = expected['provider_units'] - deductions.pop(payment.pk, 0)
                if ([row.pk for row in refunds] != expected['refund_ids'] or net_expected < 0
                        or sum(row['basis'] for row in parts) != expected['paid_units']
                        or sum(row['income'] for row in parts) != net_expected
                        or (handover.pk if handover else None) != expected['handover_id']):
                    raise SettlementError('多轮退款加购责任与真实资金不一致')
                addon_refunds.extend(row.pk for row in refunds)
                for part in parts:
                    if part['income'] < 0 or (part['segment_id'], part['provider_id']) not in eligible:
                        raise SettlementError('多轮退款加购实际履约段异常')
                    endings = [p for p in postings if part['provider_id'] in p.proposal.affected_accounts
                        and (not p.proposal.affected_segment_ids or part['segment_id'] in p.proposal.affected_segment_ids)]
                    if len(endings) > 1:
                        raise SettlementError('加购履约段存在重复协商终止')
                    if endings and part['is_current']:
                        verify_accepted(order, payment, endings[0], part)
                    addon_parts.append({'payment_id': payment.pk, 'segment_id': part['segment_id'],
                        'provider_account_id': part['provider_id'], 'provider_income_units': part['income']})
                for key in keys:
                    addon_totals[key] += expected[key]
            if deductions:
                raise SettlementError('多轮退款加购承担缺少原付款')
            merged = {}
            for part in addon_parts:
                key = (part['payment_id'], part['segment_id'], part['provider_account_id'])
                if key in merged:
                    merged[key]['provider_income_units'] += part['provider_income_units']
                else:
                    merged[key] = dict(part)
            addon_parts = list(merged.values())
            addon_totals['provider_units'] = sum(row['provider_income_units'] for row in addon_parts)
            main_deductions = verified_deductions(order)
        if (funding.net_principal_units + funding.planned_provider_external_units + funding.required_units
                != sum(person.income for person in plan.persons) + sum(part['provider_income_units'] for part in addon_parts)
                   + plan.inviter_income + plan.store_income):
            raise SettlementError('多轮退款逐人收益与真实资金不守恒')
        amounts = {row.pk: row for row in OrderPointsFundingAllocation.objects.filter(
            pk__in=[item[0] for item in state['allocations']])}
        allocations = []
        for allocation_id, amount, released in state['allocations']:
            allocation = amounts.get(allocation_id)
            if not allocation or amount < released:
                raise SettlementError('多轮退款实际到账余额异常')
            allocations.append({'id': allocation_id, 'receipt_id': allocation.receipt_id, 'amount': amount - released})
        calculation = asdict(funding)
        for key in ('allocated_units', 'release_units', 'remaining_units'):
            calculation.pop(key)
        calculation['additional_store_funding_units'] = funding.required_units
        terms = {'calculation': calculation, 'allocations': allocations,
            'round_posting_ids': [row.pk for row in postings],
            'round_version_id': latest.pk, 'round_version_hash': latest.evidence_hash}
        if addon_history is not None:
            terms.update(addons=addon_parts, addon_history=addon_history, addon_totals=addon_totals,
                         addon_refund_ids=addon_refunds,
                         main_segment_deductions=[[key, value] for key, value in sorted(main_deductions.items())])
            terms['third_addon_rounding'] = [[event.refund_id, event.refund.rounding_surplus]
                for event in addon_events if event.refund_id and event.refund.rounding_surplus]
        return plan, _terms(order, plan, points_funding=terms)


def verify_manifest_locked(order, manifest, terms, *, finalized=False):
    from .segment_settlement import _hash
    if (not transaction.get_connection().in_atomic_block or manifest.order_id != order.pk
            or manifest.plan != terms or manifest.plan_hash != _hash(terms)
            or (finalized and (not manifest.finalized_at or order.settled_at != manifest.finalized_at))
            or (not finalized and manifest.finalized_at)):
        raise SettlementError('多轮退款最终清单异常')
    _, expected = prepare_locked(order.pk, allow_finalized=finalized)
    if expected != terms:
        raise SettlementError('多轮退款实际资金与最终清单不一致')
