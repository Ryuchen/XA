"""Read verified partial earnings on an ordinary service with add-on refunds."""
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def evidence(order, addons, sources, start, end):
    from club_accounts.models import LegacyAccountMap
    from orders.addon_funding import include_paid_addons
    from orders.addon_funding_release import addon_release_totals
    from orders.addon_settlement import settlement_facts, verify_addon_records
    from orders.models import OrderPointsSettlement, OrderProvider, OrderSegmentFinancialSnapshot
    from orders.points_funding_evidence import verified_order_allocations_locked
    from orders.points_funding_plan import plan_service_funding
    from orders.points_settlement_test import _ordinary_snapshots_verified, _verify_person
    from orders.settlement import ProviderShare

    if len(addons) < 2 or order.required_provider_count != 2 or order.settled_at:
        raise ValidationError('普通双陪跨加购部分结算范围待核对')
    cross = [deduction for addon in addons for refund in addon.refund_records.all()
             for deduction in refund.earning_deductions.exclude(payment_id=addon.pk)]
    if not cross:
        raise ValidationError('没有跨加购收益扣减凭证')
    for addon in addons:
        for refund in addon.refund_records.select_related('buyer_transaction'):
            tx = refund.buyer_transaction
            if tx.reversal_of_id or tx.reversals.exists():
                raise ValidationError('加购退款流水与期间待核对')
    assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update()
                   .filter(order=order, released_at__isnull=True)}
    snapshots = list(OrderSegmentFinancialSnapshot.objects.select_for_update().filter(
        segment__attribution__order=order, segment__released_at__isnull=True).select_related('segment'))
    _ordinary_snapshots_verified(order, snapshots, assignments)
    if (len(snapshots) != 2 or len(assignments) != 2
            or any(not row.provider_id or not LegacyAccountMap.objects.filter(
                account_id=row.provider_account_id, legacy_user_id=row.provider_id).exists()
                for row in assignments.values())):
        raise ValidationError('普通双陪身份或原收益待核对')
    shares = [ProviderShare(row.settlement_base, row.commission_type, row.commission_rate,
                            row.commission_fixed, row.provider_income) for row in assignments.values()]
    amounts = {kind: sum(row.amount for row in sources if row.kind == kind)
               for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    plan = plan_service_funding(deal_units=order.amount, point_discount_units=0, shares=shares,
        inviter_rate=rate, principal_units=amounts['PREPAID'], gift_units=amounts['GIFT'],
        compensation_units=amounts['COMPENSATION'])
    combined = include_paid_addons(order, plan)
    expected = settlement_facts(plan, combined)
    settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
    if (not settlement or settlement.redemption_id or settlement.finalized_at
            or settlement.inviter_transaction_id or settlement.store_transaction_id):
        raise ValidationError('普通双陪收益预留状态待核对')
    recorded = (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
        settlement.gift_units, settlement.compensation_units, settlement.funding_units,
        settlement.provider_units, settlement.inviter_units, settlement.store_units)
    allocations = verified_order_allocations_locked(order)
    released = addon_release_totals(allocations)
    uses = [(row.pk, row.amount - released[row.pk]) for row in allocations if row.amount > released[row.pk]]
    if (recorded != expected or sum(amount for _, amount in uses) != combined.additional_store_funding_units
            or list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount')) != uses):
        raise ValidationError('普通双陪整单补资或收益预留待核对')
    people = list(settlement.persons.select_for_update().select_related('report', 'income_transaction__wallet', 'segment'))
    if len(people) != 1:
        raise ValidationError('普通双陪已结算人数待核对')
    person = people[0]
    by_segment = {row.segment_id: row for row in snapshots}
    snapshot = by_segment.get(person.segment_id)
    assignment = assignments.get(snapshot.segment.provider_account_id) if snapshot else None
    deductions = dict(combined.main_deductions)
    if not snapshot or not assignment or assignment.settled_at != person.settled_at:
        raise ValidationError('普通双陪已结算履约归属待核对')
    _verify_person(order, person, snapshot, assignment, deduction=deductions.get(assignment.provider_account_id, 0))
    actual = verify_addon_records(order, settlement, combined.addon_parts, people)
    expected_parts = {(part.payment_id, part.segment_id) for part in combined.addon_parts
                      if part.segment_id == person.segment_id}
    if actual != expected_parts:
        raise ValidationError('普通双陪加购已结算份额待核对')
    records = list(settlement.addon_settlements.select_related('income_transaction__wallet'))
    for record in records:
        tx = record.income_transaction
        if (record.segment_id != person.segment_id or record.settled_at != person.settled_at
                or (tx and (tx.wallet.user_id != assignment.provider_id or tx.reversal_of_id or tx.reversals.exists()))):
            raise ValidationError('普通双陪加购收入流水待核对')
    waiting = [row for row in snapshots if row.segment_id != person.segment_id]
    if len(waiting) != 1 or assignments[waiting[0].segment.provider_account_id].settled_at:
        raise ValidationError('普通双陪待结算陪玩状态待核对')
    waiting_account = waiting[0].segment.provider_account_id
    pending_main = waiting[0].income_at_entry - deductions.get(waiting_account, 0)
    pending_addons = sum(part.provider_income_units for part in combined.addon_parts
                         if part.segment_id == waiting[0].segment_id)
    if (pending_main < 0 or person.amount + sum(record.amount for record in records) + pending_main + pending_addons
            != settlement.provider_units):
        raise ValidationError('普通双陪已结算和待结算收益不守恒')
    main_ids = {person.income_transaction_id} if person.income_transaction_id else set()
    addon_ids = {row.income_transaction_id for row in records if row.income_transaction_id}
    txs = order.transactions.filter(status='SUCCESS', tx_type='INCOME')
    if (set(txs.filter(remark=f'订单收入：{order.order_no}').values_list('pk', flat=True)) != main_ids
            or set(txs.filter(remark__startswith=f'订单收入：{order.order_no} 加购 ').values_list('pk', flat=True)) != addon_ids
            or order.transactions.filter(status='SUCCESS').filter(
                Q(tx_type='SHOP_INCOME') | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}')).exists()):
        raise ValidationError('普通双陪个人或整单资金流水待核对')
    paid = ([person.income_transaction] if person.income_transaction_id else [])
    paid += [row.income_transaction for row in records if row.income_transaction_id]
    return {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
            'settled_segment_id': person.segment_id, 'settled_at': person.settled_at.isoformat(),
            'income_posted_in_period_units': sum(tx.amount for tx in paid if start <= tx.created_at < end),
            'pending_segment_id': waiting[0].segment_id,
            'pending_main_units': pending_main, 'pending_addon_units': pending_addons}
