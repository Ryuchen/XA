"""Period costs for a verified follow-up negotiation on an ordinary order."""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def _verify_first_team_handover(order, payment, handover, *, before):
    """Prove the pre-refund team handover from its immutable business records."""
    from console.models import AdminAuditLog
    from orders.addon_handover import _income
    from orders.addon_intents import _commission
    from orders.addon_offers import AddonSelectionError
    from orders.models import OrderProvider, OrderProviderCompletionFact

    source, target = handover.source_segment, handover.target_segment
    accounts = {source.provider_account_id, target.provider_account_id}
    accepted = handover.accepted_at
    if (handover.payment_id != payment.pk or handover.status != 'ACCEPTED'
            or handover.previous_id or handover.version != 1 or not handover.created_at or not accepted
            or not handover.created_at <= accepted < before
            or payment.buyer_transaction.created_at >= handover.created_at
            or source.pk != payment.intent.segment_id or source.attribution.order_id != order.pk
            or target.attribution.order_id != order.pk or len(accounts) != 2
            or None in accounts or not source.started_at or not target.started_at
            or source.started_at > accepted or target.started_at > accepted
            or (source.released_at and source.released_at <= accepted)
            or (target.released_at and target.released_at <= accepted)
            or not order.in_service_at or accepted < order.in_service_at
            or (order.completed_at and accepted >= order.completed_at)
            or payment.refund_records.filter(created_at__lte=accepted).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__lte=accepted).exists()
            or payment.provider_income != _income(payment.intent.amount, payment.intent.commission_snapshot)
            or handover.source_basis_before != payment.intent.amount
            or handover.retained_basis + handover.transferred_basis != handover.source_basis_before
            or not handover.transferred_basis or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('同单加购首次接替范围或时序待核对')
    assignments = list(OrderProvider.objects.filter(order=order, provider_account_id__in=accounts))
    if (len(assignments) != 2 or {row.provider_account_id for row in assignments} != accounts
            or any(row.settled_at and row.settled_at <= accepted for row in assignments)
            or OrderProviderCompletionFact.objects.filter(order=order, provider_account_id__in=accounts,
                                                          completed_at__lte=accepted).exists()):
        raise ValidationError('同单接替人员或既有收益待核对')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True),
                (source.provider_account_id, 'PROVIDER', True),
                (target.provider_account_id, 'PROVIDER', True)}
    if (len(expected) != 3 or len(decisions) != 3
            or {(account, role, yes) for account, role, yes, _ in decisions} != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != accepted):
        raise ValidationError('同单接替三方确认待核对')
    try:
        target_terms = _commission(target, lock=False, allow_refund_review=True)
    except AddonSelectionError as exc:
        raise ValidationError('同单接替抽成快照待核对') from exc
    expected_rows = []
    if handover.retained_basis:
        expected_rows.append((source.pk, handover.retained_basis,
                              _income(handover.retained_basis, payment.intent.commission_snapshot),
                              payment.intent.commission_snapshot, False))
    expected_rows.append((target.pk, handover.transferred_basis, _income(handover.transferred_basis, target_terms),
                          target_terms, True))
    shares = list(handover.shares.order_by('position'))
    if ([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in shares]
            != expected_rows or [row.position for row in shares] != list(range(len(shares)))):
        raise ValidationError('同单接替逐段收益待核对')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source.provider_account_id,
            'target_provider': target.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('同单接替客服审计待核对')
    return sum(row.income for row in shares)


def _verify_partly_settled_addon_followup(order, link, start, end):
    """Prove one released person and the other person's unpaid liability."""
    from django.db.models import Q
    from orders.addon_settlement import verify_addon_records
    from orders.ordinary_addon_followup_settlement import _load_locked, _verify_person
    from orders.ordinary_refunded_addon_settlement import _parts, _verify_paid_people

    link, settlement, economics, current_fact = _load_locked(order, link)
    prior_facts = [row for row in economics['people'] if row['affected']]
    if len(economics['people']) != 2 or len(prior_facts) != 1:
        raise ValidationError('双陪待结算份额待核对')
    prior = _verify_paid_people(order, settlement, link.previous, economics,
                                segment_ids=[prior_facts[0]['segment_id']])
    current = _verify_person(order, link, settlement, economics, current_fact)
    people = prior + ([current] if current else [])
    if len(people) != 1 or settlement.persons.count() != 1:
        raise ValidationError('双陪已结算人数待核对')
    waiting = current_fact if prior else prior_facts[0]
    waiting_assignment = order.providers.get(pk=waiting['assignment_id'])
    waiting_receipt = link.execution if prior else link.previous
    waiting_share = waiting_receipt.shares.get(account_id=waiting['account_id'])
    if (waiting_assignment.settled_at or waiting_share.settled_at or waiting_share.income_transaction_id
            or waiting_share.settled_report_id or waiting_share.report_submitted_at):
        raise ValidationError('待结算陪玩已有结算凭证')
    if (settlement.finalized_at or settlement.inviter_transaction_id or settlement.store_transaction_id
            or settlement.ordinary_refund_addon_evidence.rounding_transaction_id
            or link.previous.finalized_at or link.previous.inviter_transaction_id or link.previous.shop_transaction_id
            or link.previous.rounding_transaction_id or link.execution.finalized_at
            or link.execution.inviter_transaction_id or link.execution.shop_transaction_id
            or link.execution.rounding_transaction_id):
        raise ValidationError('整单未结算但已存在最终资金凭证')
    parts = _parts(economics)
    actual = verify_addon_records(order, settlement, parts, people)
    expected = {(part.payment_id, part.segment_id) for part in parts
                if part.segment_id == people[0].segment_id}
    if actual != expected:
        raise ValidationError('已释放加购收益或待结算份额待核对')
    addon_records = list(settlement.addon_settlements.select_related('income_transaction', 'segment'))
    if any(record.settled_at != people[0].settled_at for record in addon_records):
        raise ValidationError('加购与陪玩收益结算时间待核对')
    main_ids = {person.income_transaction_id for person in people if person.income_transaction_id}
    addon_ids = set(settlement.addon_settlements.exclude(income_transaction_id__isnull=True)
                    .values_list('income_transaction_id', flat=True))
    txs = order.transactions.filter(status='SUCCESS', tx_type='INCOME')
    if (set(txs.filter(remark=f'订单收入：{order.order_no}').values_list('pk', flat=True)) != main_ids
            or set(txs.filter(remark__startswith=f'订单收入：{order.order_no} 加购 ').values_list('pk', flat=True)) != addon_ids
            or order.transactions.filter(status='SUCCESS').filter(
                Q(tx_type='SHOP_INCOME') | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}')).exists()):
        raise ValidationError('个人收益或整单资金流水待核对')
    paid_transactions = ([people[0].income_transaction] if people[0].income_transaction_id else [])
    paid_transactions += [record.income_transaction for record in addon_records if record.income_transaction_id]
    pending_main = waiting['main_income'] if prior_facts[0]['segment_id'] == waiting['segment_id'] else waiting_share.retained_income
    pending_addons = sum(part.provider_income_units for part in parts if part.segment_id == waiting['segment_id'])
    from django.utils import timezone
    return {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
            'settled_segment_id': people[0].segment_id,
            'settled_at': people[0].settled_at.isoformat(),
            'income_posted_in_period_units': sum(tx.amount for tx in paid_transactions if start <= tx.created_at < end),
            'pending_segment_id': waiting['segment_id'],
            'pending_main_units': pending_main, 'pending_addon_units': pending_addons}


def costs(order_id, start, end):
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation
    from orders.financial_lock import lock_order_money
    from orders.models import (NegotiationFollowupLink, Order, OrderAddonPayment, OrderOriginalShareTransfer,
                               OrderPlatformSubsidy, OrderPointsRedemption, OrderPrestartHandoff)
    from orders.negotiation_followup_accounting import verify_followup_accounting, verify_followup_termination
    from orders.referral_evidence import ReferralEvidenceError, paid_referral_credits
    from orders.settlement import SettlementError
    from .roi_period import _valid_assignments, _verified_addon_costs

    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if (not order or not order.amount or order.is_gift_snapshot
                    or OrderPointsRedemption.objects.filter(order=order).exists()
                    or OrderPlatformSubsidy.objects.filter(order=order).exists()
                    or OrderPrestartHandoff.objects.filter(order=order).exists()
                    or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                return None
            lock_order_money()
            links = list(NegotiationFollowupLink.objects.select_for_update().filter(order=order)
                         .select_related('previous__refund', 'execution__refund'))
            if len(links) != 1:
                return None
            link = links[0]
            addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                          .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
            addon_cost = {'provider': 0, 'gift_compensation': 0}
            partial_evidence = None
            if addons:
                # This is a separate, fully recorded settlement flow, not the
                # plain follow-up ledger.  Recognize add-ons only after all
                # individual income and paid or pending liabilities are verified.
                from orders.addon_handover import current_responsibilities
                from orders.negotiation_execution import verify_execution
                from orders.negotiation_termination import verify_termination_evidence
                from orders.ordinary_addon_followup_settlement import verify_finalized_locked
                if order.settled_at:
                    verify_finalized_locked(order, link)
                else:
                    partial_evidence = _verify_partly_settled_addon_followup(order, link, start, end)
                verify_termination_evidence(link.previous)
                before_rows = verify_execution(link.previous)
                after_rows = list(link.execution.shares.all())
                if len(before_rows) != 1 or len(after_rows) != 1:
                    return None
                before, after = before_rows[0], after_rows[0]
                facts = order.points_settlement.ordinary_refund_addon_evidence.facts
                history, settled_addons = facts['addon_history'], facts['addons']
                if (not isinstance(history, list) or not isinstance(settled_addons, list)
                        or len(history) != len(addons)
                        or {row['payment_id'] for row in history} != {addon.pk for addon in addons}):
                    return None
                actual_parts = []
                handover_delta = 0
                for addon in addons:
                    rows = [row for row in history if row['payment_id'] == addon.pk]
                    refunds = list(addon.refund_records.select_related('buyer_transaction').order_by('created_at', 'pk'))
                    accepted = list(addon.handovers.filter(status='ACCEPTED')
                                    .select_related('source_segment__attribution', 'target_segment__attribution'))
                    if (len(rows) != 1 or len(accepted) > 1
                            or rows[0]['handover_id'] != (accepted[0].pk if accepted else None)
                            or rows[0]['refund_ids'] != sorted(row.pk for row in refunds)
                            or [row.pk for row in refunds] != sorted(row.pk for row in refunds)):
                        return None
                    for refund in refunds:
                        tx = refund.buyer_transaction
                        if tx.reversal_of_id or tx.reversals.exists():
                            return None
                    deductions = list(addon.refund_earning_deductions.select_related('refund__payment').all())
                    if any(row.refund.payment.intent.order_id != order.pk
                           or row.refund.created_at < addon.buyer_transaction.created_at for row in deductions):
                        return None
                    sources = list(addon.allocations.all())
                    values = {kind: sum(row.amount - row.refunded_amount for row in sources if row.kind == kind)
                              for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
                    if accepted:
                        handover = accepted[0]
                        initial_income = _verify_first_team_handover(order, addon, handover,
                                                                   before=link.previous.accepted_at)
                        income_before = addon.provider_income - sum(row.amount for row in deductions
                                                                   if row.refund.created_at < handover.accepted_at)
                        if income_before < 0:
                            return None
                        if start <= handover.accepted_at < end:
                            handover_delta += initial_income - income_before
                    _, responsibilities = current_responsibilities(addon, lock=True)
                    remaining_income = sum(row['income'] for row in responsibilities)
                    remaining_basis = addon.intent.amount - sum(row.amount for row in refunds)
                    if (remaining_basis < 0 or remaining_income < 0 or remaining_income > remaining_basis
                            or rows[0]['paid_units'] != remaining_basis
                            or rows[0]['principal_units'] != values['PREPAID']
                            or rows[0]['gift_units'] != values['GIFT']
                            or rows[0]['compensation_units'] != values['COMPENSATION']
                            or rows[0]['provider_units'] != remaining_income
                            or rows[0]['inviter_units'] != 0
                            or rows[0]['recovery_units'] != sum(row.wallet_deduction + row.deposit_deduction
                                                                for row in refunds)):
                        return None
                    actual_parts.extend({'payment_id': addon.pk, 'segment_id': row['segment_id'],
                                         'account_id': row['provider_id'], 'basis': row['basis'],
                                         'income': row['income']} for row in responsibilities)
                key = lambda row: (row['payment_id'], row['segment_id'], row['account_id'])
                if (len({key(row) for row in settled_addons}) != len(settled_addons)
                        or sorted(actual_parts, key=key) != sorted(settled_addons, key=key)):
                    return None
                people = {row['account_id']: row['user_id'] for row in facts['people']}
                for record in order.points_settlement.addon_settlements.select_related('income_transaction__wallet', 'segment'):
                    tx = record.income_transaction
                    if tx and (tx.reversal_of_id or tx.reversals.exists()
                               or tx.wallet.user_id != people.get(record.segment.provider_account_id)):
                        return None
                addon_cost = _verified_addon_costs(addons, start, end)
                if addon_cost is None:
                    return None
                addon_cost['provider'] += handover_delta
            else:
                before, after = verify_followup_accounting(link)
                verify_followup_termination(link)
            receipts = [(link.previous, before), (link.execution, after)]
            events = [(receipt.refund, share) for receipt, share in receipts if receipt.refund_id]
            if not events:
                return None
            payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', amount__lt=0,
                                                       reversal_of__isnull=True).select_related('wallet'))
            sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot').order_by('pk'))
            if (not _valid_assignments(order, list(order.providers.all()))
                    or len(payments) != 1 or payments[0].amount != -order.amount
                    or payments[0].wallet.account_id != order.customer_account_id
                    or payments[0].wallet.user_id != order.customer_id or payments[0].reversals.exists()
                    or payments[0].balance_after != payments[0].balance_before + payments[0].amount
                    or not sources or sum(row.amount for row in sources) != order.amount):
                return None
            payment = payments[0]
            if (any(addon.buyer_transaction.created_at < payment.created_at for addon in addons)
                    or any(refund.created_at < payment.created_at for refund, _ in events)):
                return None
            from .roi_period import ordinary_refund_event_time
            validate_order_funding_sources(order, sources)
            rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
            if type(rate) is not int or not 0 <= rate <= 100 or order.inviter_commission != order.amount * rate // 100:
                return None
            credits = paid_referral_credits(order, list(order.transactions.filter(
                status='SUCCESS', tx_type__in=['INCOME', 'GIFT'], amount__gt=0,
                reversal_of__isnull=True).select_related('wallet')))
            if len(credits) > 1 or any(tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount
                                       for tx in credits):
                return None
            credit = credits[0] if credits else None
            if credit:
                if (credit.created_at < payment.created_at
                        or any(credit.created_at == ordinary_refund_event_time(refund) for refund, _ in events)):
                    return None
                refunded_before_credit = sum(refund.amount for refund, _ in events
                                             if ordinary_refund_event_time(refund) < credit.created_at)
                if credit.amount != (order.amount - refunded_before_credit) * rate // 100:
                    return None
            elif order.settled_at and (order.amount - sum(refund.amount for refund, _ in events)) * rate // 100:
                return None
            paid = start <= payment.created_at < end
            provider = order.provider_income if paid else 0
            referral = order.inviter_commission if paid else 0
            gift = sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION')) if paid else 0
            returned = {row.pk: 0 for row in sources}
            running_paid = order.amount
            for refund, share in events:
                refund_at = ordinary_refund_event_time(refund)
                split = plan_refund([RefundablePart(row.pk, row.kind, row.amount, returned[row.pk])
                                     for row in sources], refund.amount)
                if refund.amount > running_paid:
                    return None
                if start <= refund_at < end:
                    provider -= share.burden
                    gift -= sum(split[row.pk] for row in sources if row.kind in ('GIFT', 'COMPENSATION'))
                    if not credit or refund_at < credit.created_at:
                        referral -= (running_paid * rate // 100 - (running_paid - refund.amount) * rate // 100)
                running_paid -= refund.amount
                for row in sources:
                    returned[row.pk] += split[row.pk]
            if any(row.refunded_amount != returned[row.pk] for row in sources):
                return None
            result = {'provider': provider + addon_cost['provider'], 'referral': referral,
                      'gift_compensation': gift + addon_cost['gift_compensation']}
            if partial_evidence is not None:
                result['partial_settlement'] = partial_evidence
            return result
    except (DjangoValidationError, ValidationError, FundingPlanError, ReferralEvidenceError, SettlementError,
            ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None
