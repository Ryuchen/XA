"""Verified period costs for points team add-ons with same-team handovers."""
from contextlib import nullcontext

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def costs(order_id, start, end):
    from club_accounts.models import LegacyAccountMap
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation
    from orders.addon_funding_release import addon_release_totals, verify_run
    from orders.addon_settlement import combined_terms, settlement_facts, verify_addon_records
    from orders.financial_lock import lock_order_money
    from orders.models import (Order, OrderAddonFundingReleaseRun, OrderAddonPayment, OrderOriginalShareTransfer,
                               OrderPointsRedemption, OrderPointsSettlement, OrderPrestartHandoff, OrderProvider,
                               OrderSegmentFinancialSnapshot)
    from orders.points_business_access import configured, service_order
    from orders.points_funding_evidence import verified_order_allocations_locked, verify_order_funding
    from orders.points_funding_plan import PointsFundingPlanError, plan_verified_order
    from orders.points_settlement_test import _verify_person
    from orders.points_team_addon_handover_facts import verify_locked as verify_handover
    from orders.referral_evidence import ReferralEvidenceError, paid_referral_credits
    from orders.settlement import SettlementError
    from .roi_period import _verified_addon_costs

    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            scope = service_order(order_id, phase='settlement', addon=True) if configured() else nullcontext()
            with scope:
                order = Order.objects.select_for_update().filter(pk=order_id).first()
                if (not order or not order.amount or order.is_gift_snapshot or order.required_provider_count != 2
                        or order.refund_records.exists() or OrderPrestartHandoff.objects.filter(order=order).exists()
                        or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                    return None
                lock_order_money()
                redemption = OrderPointsRedemption.objects.select_related('payment__wallet').filter(order=order).first()
                if not redemption:
                    return None
                payment = redemption.payment
                if (not payment or payment.order_id != order.pk or payment.tx_type != 'PAY' or payment.status != 'SUCCESS'
                        or payment.amount != -order.amount or payment.wallet.account_id != order.customer_account_id
                        or payment.wallet.user_id != order.customer_id or payment.reversal_of_id or payment.reversals.exists()
                        or payment.balance_after != payment.balance_before + payment.amount):
                    return None
                sources = list(OrderFundingAllocation.objects.select_related('lot').filter(order=order))
                if not sources or sum(row.amount for row in sources) != order.amount or any(row.refunded_amount for row in sources):
                    return None
                validate_order_funding_sources(order, sources)
                plan = plan_verified_order(order.pk, allow_finalized=True)
                combined = combined_terms(order, plan)
                funding = verify_order_funding(order.pk, allow_finalized=True)
                settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
                if not combined or not funding.fully_funded or not settlement or settlement.redemption_id != redemption.pk:
                    return None
                facts = settlement_facts(plan, combined)
                recorded = (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
                    settlement.gift_units, settlement.compensation_units, settlement.funding_units,
                    settlement.provider_units, settlement.inviter_units, settlement.store_units)
                allocations = verified_order_allocations_locked(order)
                released = addon_release_totals(allocations)
                uses = [(row.pk, row.amount - released[row.pk]) for row in allocations if row.amount > released[row.pk]]
                if (recorded != facts or settlement.finalized_at != order.settled_at
                        or sum(amount for _, amount in uses) != combined.additional_store_funding_units
                        or list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount')) != uses):
                    return None
                snapshots = list(OrderSegmentFinancialSnapshot.objects.select_for_update().filter(
                    segment__attribution__order=order, segment__released_at__isnull=True).select_related('segment'))
                assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update().filter(
                    order=order, released_at__isnull=True)}
                people = list(settlement.persons.select_for_update().select_related('report', 'segment', 'income_transaction__wallet'))
                if (len(snapshots) != 2 or len(assignments) != 2 or len(people) != (2 if order.settled_at else 1)
                        or any(not row.provider_id or not LegacyAccountMap.objects.filter(
                            account_id=row.provider_account_id, legacy_user_id=row.provider_id).exists()
                               for row in assignments.values())):
                    return None
                by_segment = {row.segment_id: row for row in snapshots}
                deductions = dict(combined.main_deductions)
                for person in people:
                    snap = by_segment.get(person.segment_id)
                    assignment = assignments.get(snap.segment.provider_account_id) if snap else None
                    if not assignment or assignment.settled_at != person.settled_at:
                        return None
                    _verify_person(order, person, snap, assignment, deduction=deductions.get(assignment.provider_account_id, 0))
                actual = verify_addon_records(order, settlement, combined.addon_parts, people)
                expected = {(part.payment_id, part.segment_id) for part in combined.addon_parts
                            if part.segment_id in {person.segment_id for person in people}}
                if actual != expected:
                    return None
                people_by_segment = {person.segment_id: person for person in people}
                records = list(settlement.addon_settlements.select_related('income_transaction__wallet', 'segment'))
                for record in records:
                    person = people_by_segment.get(record.segment_id)
                    tx = record.income_transaction
                    if (not person or record.settled_at != person.settled_at
                            or (tx and (tx.wallet.user_id != person.report.provider_id
                                       or tx.reversal_of_id or tx.reversals.exists()))):
                        return None
                main_ids = {person.income_transaction_id for person in people if person.income_transaction_id}
                addon_ids = {row.income_transaction_id for row in records if row.income_transaction_id}
                income_txs = order.transactions.filter(status='SUCCESS', tx_type='INCOME')
                if (set(income_txs.filter(remark=f'订单收入：{order.order_no}').values_list('pk', flat=True)) != main_ids
                        or set(income_txs.filter(remark__startswith=f'订单收入：{order.order_no} 加购 ')
                               .values_list('pk', flat=True)) != addon_ids):
                    return None
                credits = paid_referral_credits(order, list(order.transactions.filter(status='SUCCESS',
                    tx_type__in=['INCOME', 'GIFT'], amount__gt=0, reversal_of__isnull=True).select_related('wallet')))
                if (len(credits) > 1 or any(tx.amount != plan.inviter_income_units or tx.reversals.exists()
                    or tx.balance_after != tx.balance_before + tx.amount for tx in credits)):
                    return None
                if order.settled_at:
                    contribution = max(0, combined.addon_principal_units + combined.addon_provider_recovery_units
                                       - combined.addon_provider_income_units - combined.addon_inviter_income_units)
                    if (order.settlement_store_funding != facts[5]
                            or order.settlement_addon_contribution != contribution
                            or order.aftersales.filter(status='OPEN').exists()):
                        return None
                    if ((plan.inviter_income_units and (len(credits) != 1 or settlement.inviter_transaction_id != credits[0].pk))
                            or (not plan.inviter_income_units and (credits or settlement.inviter_transaction_id))):
                        return None
                    if credits and credits[0].wallet.account_id:
                        from club_accounts.models import ClubAccount
                        from engagement.models import PointFundingLot
                        if (ClubAccount.objects.filter(pk=credits[0].wallet.account_id, account_type='BOSS').exists()
                                and not PointFundingLot.objects.filter(account_id=credits[0].wallet.account_id,
                                    source_key=f'points-settlement-referral:{settlement.pk}', kind=PointFundingLot.Kind.PREPAID,
                                    remaining__lte=credits[0].amount).exists()):
                            return None
                    store = settlement.store_transaction
                    if (combined.store_income_units and (not store or store.order_id != order.pk
                            or store.tx_type != 'SHOP_INCOME' or store.status != 'SUCCESS'
                            or store.amount != combined.store_income_units or store.reversal_of_id or store.reversals.exists()
                            or not store.wallet.user or store.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                            or store.balance_after != store.balance_before + store.amount)
                            or (not combined.store_income_units and store)):
                        return None
                elif credits or settlement.inviter_transaction_id or settlement.store_transaction_id:
                    return None
                expected_dest = {pk for pk in (settlement.inviter_transaction_id, settlement.store_transaction_id) if pk}
                actual_dest = set(order.transactions.filter(status='SUCCESS').filter(
                    Q(tx_type='SHOP_INCOME') | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}'))
                                  .values_list('pk', flat=True))
                if actual_dest != expected_dest:
                    return None
                addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                              .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
                addon_cost = _verified_addon_costs(addons, start, end)
                if addon_cost is None or any(row.buyer_transaction.created_at < payment.created_at for row in addons):
                    return None
                delta, found_handover = 0, False
                for addon in addons:
                    for refund in addon.refund_records.select_related('buyer_transaction'):
                        tx = refund.buyer_transaction
                        if tx.reversal_of_id or tx.reversals.exists():
                            return None
                    handovers = list(addon.handovers.filter(status='ACCEPTED')
                                     .select_related('source_segment__attribution', 'target_segment__attribution'))
                    if len(handovers) > 1:
                        return None
                    if not handovers:
                        continue
                    found_handover = True
                    handover = handovers[0]
                    run = OrderAddonFundingReleaseRun.objects.filter(handover=handover,
                        points_handover_snapshot__kind='SAME_TEAM_V1').first()
                    if not run or run.refunded_main_snapshot is not None:
                        return None
                    verify_handover(order, addon, handover)
                    verify_run(run)
                    prior = list(addon.refund_earning_deductions.select_related('refund'))
                    if (addon.refund_records.filter(created_at=handover.accepted_at).exists()
                            or any(row.refund.created_at == handover.accepted_at for row in prior)):
                        return None
                    income_before = addon.provider_income - sum(row.amount for row in prior
                                                                 if row.refund.created_at < handover.accepted_at)
                    if income_before < 0:
                        return None
                    if start <= handover.accepted_at < end:
                        delta += sum(row.income for row in handover.shares.all()) - income_before
                if not found_handover:
                    return None
                paid_in_period = start <= payment.created_at < end
                result = {'provider': (plan.provider_income_units if paid_in_period else 0) + addon_cost['provider'] + delta,
                          'referral': plan.inviter_income_units if paid_in_period else 0,
                          'gift_compensation': (sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION'))
                                                if paid_in_period else 0) + addon_cost['gift_compensation']}
                if not order.settled_at:
                    person = people[0]
                    waiting = [row for row in snapshots if row.segment_id != person.segment_id]
                    if len(waiting) != 1 or assignments[waiting[0].segment.provider_account_id].settled_at:
                        return None
                    pending_main = waiting[0].income_at_entry - deductions.get(waiting[0].segment.provider_account_id, 0)
                    pending_addon = sum(part.provider_income_units for part in combined.addon_parts
                                        if part.segment_id == waiting[0].segment_id)
                    if (pending_main < 0 or person.amount + sum(row.amount for row in records) + pending_main + pending_addon
                            != settlement.provider_units):
                        return None
                    main_ids = {person.income_transaction_id} if person.income_transaction_id else set()
                    addon_ids = {row.income_transaction_id for row in records if row.income_transaction_id}
                    txs = order.transactions.filter(status='SUCCESS', tx_type='INCOME')
                    if (set(txs.filter(remark=f'订单收入：{order.order_no}').values_list('pk', flat=True)) != main_ids
                            or set(txs.filter(remark__startswith=f'订单收入：{order.order_no} 加购 ').values_list('pk', flat=True)) != addon_ids):
                        return None
                    paid_txs = ([person.income_transaction] if person.income_transaction_id else [])
                    paid_txs += [row.income_transaction for row in records if row.income_transaction_id]
                    result['partial_settlement'] = {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
                        'settled_segment_id': person.segment_id, 'settled_at': person.settled_at.isoformat(),
                        'income_posted_in_period_units': sum(tx.amount for tx in paid_txs if start <= tx.created_at < end),
                        'pending_segment_id': waiting[0].segment_id,
                        'pending_main_units': pending_main, 'pending_addon_units': pending_addon}
                return result
    except (DjangoValidationError, ValidationError, PointsFundingPlanError, SettlementError, ReferralEvidenceError,
            ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None
