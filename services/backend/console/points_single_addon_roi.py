"""Period costs for one points negotiation with verified team add-on handover."""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def costs(order_id, start, end):
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation
    from orders.addon_funding_release import verify_run
    from orders.addon_settlement import verify_addon_records
    from orders.financial_lock import lock_order_money
    from orders.models import (Order, OrderAddonFundingReleaseRun, OrderAddonPayment, OrderOriginalShareTransfer,
                               OrderPointsFundingReleaseEvidence, OrderPointsRefundSettlement, OrderPrestartHandoff,
                               OrderProvider, PointsNegotiationPosting)
    from orders.points_business_access import case_refund_scope
    from orders.points_funding_evidence import verify_order_funding
    from orders.points_funding_plan import PointsFundingPlanError
    from orders.points_negotiation_execution import verify_posting_locked
    from orders.points_negotiation_termination import verify_termination_locked
    from orders.points_refunded_funding import later_addon_runs, plan_refunded_order
    from orders.points_refunded_settlement import (_addon_facts, _check_final, _check_person, _facts,
                                                    _funding_rows, _person_amount)
    from orders.points_service_participation import service_snapshots
    from orders.points_team_addon_handover_facts import verify_locked as verify_team_handover
    from orders.referral_evidence import ReferralEvidenceError, paid_referral_credits
    from orders.settlement import SettlementError
    from .roi_period import _verified_addon_costs

    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if (not order or not order.amount or order.is_gift_snapshot or order.required_provider_count != 2
                    or OrderPrestartHandoff.objects.filter(order=order).exists()
                    or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                return None
            lock_order_money()
            refunds = list(order.refund_records.order_by('pk'))
            if len(refunds) != 1:
                return None
            posting = PointsNegotiationPosting.objects.filter(points_refund__refund=refunds[0]).select_related(
                'proposal__case', 'points_refund__redemption__payment', 'points_refund__coin_transaction').first()
            if not posting:
                return None
            with case_refund_scope(posting.proposal.case_id):
                posting = verify_posting_locked(posting)
                verify_termination_locked(posting)
                plan = plan_refunded_order(order.pk, allow_finalized=True)
                funding = verify_order_funding(order.pk, allow_finalized=True)
                if not funding.fully_funded:
                    return None
                addon_totals, parts = _addon_facts(order, posting)
                settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=posting).first()
                if not settlement:
                    return None
                recorded = (settlement.paid_units, settlement.principal_units, settlement.gift_units,
                    settlement.compensation_units, settlement.recovered_units, settlement.funding_units,
                    settlement.provider_units, settlement.inviter_units, settlement.store_units,
                    settlement.addon_paid_units, settlement.addon_principal_units, settlement.addon_gift_units,
                    settlement.addon_compensation_units, settlement.addon_recovered_units, settlement.addon_provider_units)
                if (recorded != _facts(plan, addon_totals)
                        or list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
                           != _funding_rows(order)):
                    return None
                snapshots = service_snapshots(order)
                assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update().filter(
                    order=order, pk__in=[item['assignment_id'] for item in posting.participants])}
                people = list(settlement.persons.select_for_update().select_related('report', 'segment',
                                                                                   'income_transaction__wallet'))
                if (len(snapshots) != 2 or len(assignments) != 2 or len(people) not in (1, 2)
                        or (order.settled_at and len(people) != 2) or (not order.settled_at and len(people) != 1)):
                    return None
                by_segment = {row.segment_id: row for row in snapshots}
                for person in people:
                    snap = by_segment.get(person.segment_id)
                    assignment = assignments.get(snap.segment.provider_account_id) if snap else None
                    if not assignment:
                        return None
                    _check_person(order, posting, person, snap, assignment)
                paid_parts = verify_addon_records(order, settlement, parts, people)
                expected_parts = {(part.payment_id, part.segment_id) for part in parts
                                  if part.segment_id in {person.segment_id for person in people}}
                if paid_parts != expected_parts:
                    return None
                records = list(settlement.addon_settlements.select_related('income_transaction__wallet', 'segment'))
                people_by_segment = {person.segment_id: person for person in people}
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
                if order.settled_at:
                    _check_final(order, settlement, plan, snapshots, assignments, parts)
                elif (settlement.finalized_at or settlement.inviter_transaction_id or settlement.store_transaction_id
                      or order.transactions.filter(status='SUCCESS').filter(Q(tx_type='SHOP_INCOME')
                          | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}')).exists()):
                    return None

                addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                              .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
                runs = later_addon_runs(order, posting)
                evidence = OrderPointsFundingReleaseEvidence.objects.get(posting=posting)
                history = (runs[-1].refunded_main_snapshot['addon_history'] if runs else evidence.addon_funding_snapshot) or []
                if (not addons or len(history) != len(addons)
                        or {row['payment_id'] for row in history} != {addon.pk for addon in addons}):
                    return None
                handover_delta = 0
                for addon in addons:
                    row = next(item for item in history if item['payment_id'] == addon.pk)
                    addon_refunds = list(addon.refund_records.select_related('buyer_transaction').order_by('pk'))
                    if row['refund_ids'] != [item.pk for item in addon_refunds]:
                        return None
                    for item in addon_refunds:
                        tx = item.buyer_transaction
                        if tx.reversal_of_id or tx.reversals.exists():
                            return None
                    handovers = list(addon.handovers.filter(status='ACCEPTED')
                                     .select_related('source_segment__attribution', 'target_segment__attribution'))
                    if len(handovers) > 1 or row['handover_id'] != (handovers[0].pk if handovers else None):
                        return None
                    if not handovers:
                        continue
                    handover = handovers[0]
                    run = OrderAddonFundingReleaseRun.objects.filter(handover=handover,
                        points_handover_snapshot__kind='SAME_TEAM_V1').first()
                    main_refund_at = posting.points_refund.refund.created_at
                    if (not run or not handover.accepted_at
                            or handover.accepted_at in (main_refund_at, posting.created_at)):
                        return None
                    if handover.accepted_at < main_refund_at:
                        if run.refunded_main_snapshot is not None:
                            return None
                        verify_team_handover(order, addon, handover, before=main_refund_at)
                    elif (handover.accepted_at < posting.created_at or not isinstance(run.refunded_main_snapshot, dict)
                          or run.refunded_main_snapshot.get('posting_id') != posting.pk):
                        return None
                    verify_run(run)
                    deductions = list(addon.refund_earning_deductions.select_related('refund'))
                    if (any(item.created_at == handover.accepted_at for item in addon_refunds)
                            or any(item.refund.created_at == handover.accepted_at for item in deductions)):
                        return None
                    before = addon.provider_income - sum(item.amount for item in deductions
                                                        if item.refund.created_at < handover.accepted_at)
                    if before < 0:
                        return None
                    if start <= handover.accepted_at < end:
                        handover_delta += sum(item.income for item in handover.shares.all()) - before
                if not any(addon.handovers.filter(status='ACCEPTED').exists() for addon in addons):
                    return None
                addon_costs = _verified_addon_costs(addons, start, end)
                if addon_costs is None:
                    return None
                proof, refund = posting.points_refund, refunds[0]
                from .roi_period import points_refund_event_time
                refund_at = points_refund_event_time(proof)
                payment = proof.redemption.payment
                if (not payment or payment.order_id != order.pk or payment.tx_type != 'PAY'
                        or payment.status != 'SUCCESS' or payment.amount != -order.amount
                        or payment.wallet.account_id != order.customer_account_id or payment.wallet.user_id != order.customer_id
                        or payment.reversal_of_id or payment.reversals.exists()
                        or payment.balance_after != payment.balance_before + payment.amount
                        or payment.created_at > refund.created_at
                        or any(addon.buyer_transaction.created_at < payment.created_at for addon in addons)):
                    return None
                sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
                if not sources or sum(row.amount for row in sources) != order.amount:
                    return None
                validate_order_funding_sources(order, sources)
                if any(sum(row.refunded_amount for row in sources if row.kind == kind.upper())
                       != getattr(proof, f'{kind}_units') for kind in ('prepaid', 'gift', 'compensation')):
                    return None
                rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
                if type(rate) is not int or not 0 <= rate <= 100 or order.inviter_commission != order.amount * rate // 100:
                    return None
                credits = paid_referral_credits(order, list(order.transactions.filter(status='SUCCESS',
                    tx_type__in=['INCOME', 'GIFT'], amount__gt=0, reversal_of__isnull=True).select_related('wallet')))
                referral = (order.amount - refund.amount) * rate // 100
                if (len(credits) > 1 or any(tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount
                                            or tx.created_at <= refund_at or tx.amount != referral for tx in credits)
                        or (order.settled_at and ((referral and not credits)
                            or (credits and settlement.inviter_transaction_id != credits[0].pk)))
                        or (not order.settled_at and credits)):
                    return None
                paid, refunded = start <= payment.created_at < end, start <= refund_at < end
                result = {'provider': (sum(item['income_at_entry'] for item in posting.participants) if paid else 0)
                            - (sum(posting.shares.values_list('burden', flat=True)) if refunded else 0)
                            + addon_costs['provider'] + handover_delta,
                          'referral': (order.amount * rate // 100 if paid else 0)
                            - (order.amount * rate // 100 - referral if refunded else 0),
                          'gift_compensation': (sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION')) if paid else 0)
                            - (proof.gift_units + proof.compensation_units if refunded else 0)
                            + addon_costs['gift_compensation']}
                paid_txs = [person.income_transaction for person in people if person.income_transaction_id]
                paid_txs += [row.income_transaction for row in records if row.income_transaction_id]
                verified_settlement = {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
                    'status': 'SETTLED' if order.settled_at else 'PARTIAL',
                    'settled_segment_ids': sorted(person.segment_id for person in people),
                    'income_posted_in_period_units': sum(tx.amount for tx in paid_txs if start <= tx.created_at < end),
                    'pending_segment_id': None, 'pending_main_units': 0, 'pending_addon_units': 0}
                if not order.settled_at:
                    waiting = [row for row in snapshots if row.segment_id != people[0].segment_id]
                    if len(waiting) != 1 or assignments[waiting[0].segment.provider_account_id].settled_at:
                        return None
                    pending_main = _person_amount(posting, waiting[0])
                    pending_addon = sum(part.provider_income_units for part in parts if part.segment_id == waiting[0].segment_id)
                    if people[0].amount + sum(row.amount for row in records) + pending_main + pending_addon != plan.provider_income_units:
                        return None
                    result['partial_settlement'] = {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
                        'settled_segment_id': people[0].segment_id, 'settled_at': people[0].settled_at.isoformat(),
                        'income_posted_in_period_units': sum(tx.amount for tx in paid_txs if start <= tx.created_at < end),
                        'pending_segment_id': waiting[0].segment_id,
                        'pending_main_units': pending_main, 'pending_addon_units': pending_addon}
                    verified_settlement.update(pending_segment_id=waiting[0].segment_id,
                                               pending_main_units=pending_main, pending_addon_units=pending_addon)
                result['verified_settlement'] = verified_settlement
                return result
    except (DjangoValidationError, ValidationError, PointsFundingPlanError, SettlementError, ReferralEvidenceError,
            ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None
