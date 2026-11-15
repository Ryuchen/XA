"""Period cost events for a verified untransferred two-refund points order."""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def _unpaid_revision_partly_settled(order, first, second, revision, start, end):
    """Verify one payout after a previously unpaid follow-up funding version."""
    from django.utils import timezone
    from orders.models import OrderAddonPayment, OrderProvider
    from orders.points_refunded_settlement import _person_amount
    from orders.points_service_participation import service_snapshots
    from orders.points_unpaid_followup_settlement import _addon_data, verify_progress_locked

    snapshots = service_snapshots(order)
    assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update().filter(
        order=order, provider_account_id__in=[snap.segment.provider_account_id for snap in snapshots])}
    settlement, people = verify_progress_locked(order, revision, first, second, snapshots, assignments)
    if not settlement or len(people) != 1 or len(snapshots) != 2:
        raise ValidationError('积分双陪部分收益记录待核对')
    person = people[0]
    waiting = [snap for snap in snapshots if snap.segment_id != person.segment_id]
    if len(waiting) != 1:
        raise ValidationError('积分双陪待结算履约段待核对')
    _, parts = _addon_data(order, revision, first)
    history = revision.addon_snapshot.get('addon_history', []) if revision.addon_snapshot else []
    payments = list(OrderAddonPayment.objects.filter(intent__order=order))
    if (not isinstance(history, list) or len(history) != len(payments)
            or {row['payment_id'] for row in history} != {row.pk for row in payments}):
        raise ValidationError('积分双陪加购资金版本待核对')
    for payment in payments:
        fact = next(row for row in history if row['payment_id'] == payment.pk)
        if fact['refund_ids'] != list(payment.refund_records.order_by('pk').values_list('pk', flat=True)):
            raise ValidationError('积分双陪加购退款版本待核对')
    records = list(settlement.addon_settlements.select_related('income_transaction__wallet', 'segment'))
    for record in records:
        tx = record.income_transaction
        if (record.segment_id != person.segment_id or record.settled_at != person.settled_at
                or (tx and (tx.reversal_of_id or tx.reversals.exists()
                            or tx.wallet.user_id != person.report.provider_id))):
            raise ValidationError('积分双陪加购收益流水待核对')
    account = waiting[0].segment.provider_account_id
    relevant = [posting for posting in (first, second) if posting.shares.filter(account_id=account).exists()]
    if len(relevant) != 1:
        raise ValidationError('积分双陪待结算协商份额待核对')
    pending_main = _person_amount(relevant[0], waiting[0])
    pending_addons = sum(part.provider_income_units for part in parts if part.segment_id == waiting[0].segment_id)
    if person.amount + sum(row.amount for row in records) + pending_main + pending_addons != revision.provider_units:
        raise ValidationError('积分双陪部分结算收益不守恒')
    addon_ids = {row.income_transaction_id for row in records if row.income_transaction_id}
    if set(order.transactions.filter(status='SUCCESS', tx_type='INCOME',
            remark__startswith=f'订单收入：{order.order_no} 加购 ').values_list('pk', flat=True)) != addon_ids:
        raise ValidationError('积分双陪加购收入流水未关联')
    paid = ([person.income_transaction] if person.income_transaction_id else [])
    paid += [row.income_transaction for row in records if row.income_transaction_id]
    return {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
            'settled_segment_id': person.segment_id, 'settled_at': person.settled_at.isoformat(),
            'income_posted_in_period_units': sum(tx.amount for tx in paid if start <= tx.created_at < end),
            'pending_segment_id': waiting[0].segment_id,
            'pending_main_units': pending_main, 'pending_addon_units': pending_addons}


def _partly_settled_evidence(order, first, second, start, end):
    """Read the first person's posted income and the other person's liability."""
    from django.db.models import Q
    from django.utils import timezone
    from orders.addon_settlement import verify_addon_records
    from orders.models import OrderAddonPayment, OrderPointsRefundSettlement, OrderPointsRefundSettlementPerson
    from orders.points_followup_addon_version import EffectiveRevision, later_runs
    from orders.points_followup_funding import verify_followup_funding
    from orders.points_refunded_settlement import _person_amount
    from orders.points_service_participation import service_snapshots
    from orders.points_unpaid_followup_settlement import _addon_data

    revision = second.followup_funding_revision
    funding = verify_followup_funding(order.pk)
    versions = later_runs(order, revision)
    if not funding.fully_funded:
        raise ValidationError('积分双陪部分结算资金待核对')
    if not revision.paid_person_id:
        effective = EffectiveRevision(revision, versions[-1].points_followup_snapshot) if versions else revision
        return _unpaid_revision_partly_settled(order, first, second, effective, start, end)
    if (not revision.prior_settlement_id or versions or OrderPointsRefundSettlement.objects.filter(posting=second).exists()):
        raise ValidationError('积分双陪部分结算资金或后续版本待核对')
    person, settlement = revision.paid_person, revision.prior_settlement
    if (settlement.finalized_at or settlement.inviter_transaction_id or settlement.store_transaction_id
            or person.settlement_id != settlement.pk or settlement.persons.count() != 1
            or OrderPointsRefundSettlementPerson.objects.filter(
                settlement__posting__points_refund__redemption__order=order).count() != 1):
        raise ValidationError('积分双陪个人结算状态待核对')
    _, parts = _addon_data(order, revision, first)
    addons = list(OrderAddonPayment.objects.filter(intent__order=order))
    if revision.addon_snapshot is not None and not isinstance(revision.addon_snapshot, dict):
        raise ValidationError('积分双陪加购快照待核对')
    history = revision.addon_snapshot.get('addon_history', []) if revision.addon_snapshot else []
    if (not isinstance(history, list) or len(history) != len(addons)
            or {row['payment_id'] for row in history} != {addon.pk for addon in addons}):
        raise ValidationError('积分双陪加购范围或换人待核对')
    for addon in addons:
        fact = next(row for row in history if row['payment_id'] == addon.pk)
        if fact['refund_ids'] != list(addon.refund_records.order_by('pk').values_list('pk', flat=True)):
            raise ValidationError('积分双陪加购退款版本待核对')
    actual = verify_addon_records(order, settlement, parts, [person])
    expected = {(part.payment_id, part.segment_id) for part in parts if part.segment_id == person.segment_id}
    if actual != expected:
        raise ValidationError('积分双陪已结算加购收益待核对')
    records = list(settlement.addon_settlements.select_related('income_transaction__wallet', 'segment'))
    for record in records:
        tx = record.income_transaction
        if (record.settled_at != person.settled_at or (tx and (tx.reversal_of_id or tx.reversals.exists()
                or tx.wallet.user_id != person.report.provider_id))):
            raise ValidationError('积分双陪加购收益流水待核对')
    snapshots = service_snapshots(order)
    share = second.shares.get()
    waiting = [item for item in snapshots if item.segment.provider_account_id == share.account_id]
    if (len(snapshots) != 2 or len(waiting) != 1 or waiting[0].segment_id == person.segment_id
            or share.assignment.settled_at):
        raise ValidationError('积分双陪待结算履约份额待核对')
    pending_main = _person_amount(second, waiting[0])
    pending_addons = sum(part.provider_income_units for part in parts if part.segment_id == waiting[0].segment_id)
    if (pending_main + pending_addons != revision.remaining_provider_units
            or person.amount + sum(record.amount for record in records) != revision.paid_provider_units):
        raise ValidationError('积分双陪已结算和待结算金额不守恒')
    main_ids = {person.income_transaction_id} if person.income_transaction_id else set()
    addon_ids = {record.income_transaction_id for record in records if record.income_transaction_id}
    txs = order.transactions.filter(status='SUCCESS', tx_type='INCOME')
    if (set(txs.filter(remark=f'订单收入：{order.order_no}').values_list('pk', flat=True)) != main_ids
            or set(txs.filter(remark__startswith=f'订单收入：{order.order_no} 加购 ').values_list('pk', flat=True)) != addon_ids
            or order.transactions.filter(status='SUCCESS').filter(
                Q(tx_type='SHOP_INCOME') | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}')).exists()):
        raise ValidationError('积分双陪个人或整单收益流水待核对')
    paid = ([person.income_transaction] if person.income_transaction_id else [])
    paid += [record.income_transaction for record in records if record.income_transaction_id]
    return {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
            'settled_segment_id': person.segment_id, 'settled_at': person.settled_at.isoformat(),
            'income_posted_in_period_units': sum(tx.amount for tx in paid if start <= tx.created_at < end),
            'pending_segment_id': waiting[0].segment_id,
            'pending_main_units': pending_main, 'pending_addon_units': pending_addons}


def costs(order_id, start, end):
    from orders.models import (Order, OrderAddonPayment, OrderOriginalShareTransfer, OrderPrestartHandoff,
                               PointsNegotiationPosting)
    from orders.financial_lock import lock_order_money
    from orders.points_business_access import case_refund_scope
    from orders.points_negotiation_execution import verify_posting_locked
    from orders.points_followup_termination import verify_followup_termination_locked
    from orders.referral_evidence import paid_referral_credits, ReferralEvidenceError
    from orders.settlement import SettlementError
    from orders.points_funding_plan import PointsFundingPlanError
    from engagement.models import OrderFundingAllocation
    from engagement.funding_wallet import validate_order_funding_sources
    from .roi_period import _verified_addon_costs
    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if (not order or order.is_gift_snapshot or not order.amount
                    or OrderPrestartHandoff.objects.filter(order=order).exists()
                    or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                return None
            lock_order_money()
            refunds = list(order.refund_records.order_by('pk'))
            if len(refunds) != 2:
                return None
            postings = [PointsNegotiationPosting.objects.filter(points_refund__refund=refund)
                        .select_related('points_refund__redemption__payment', 'proposal__case').first()
                        for refund in refunds]
            if any(posting is None for posting in postings):
                return None
            with case_refund_scope(postings[-1].proposal.case_id):
                verified = [verify_posting_locked(posting) for posting in postings]
                verify_followup_termination_locked(verified[-1])
                if (not isinstance(verified[0].participants, list)
                        or any(not isinstance(item, dict) or type(item.get('income_at_entry')) is not int
                               for item in verified[0].participants)):
                    return None
                payment = verified[0].points_refund.redemption.payment
                if (not payment or payment.order_id != order.pk or payment.tx_type != 'PAY'
                        or payment.status != 'SUCCESS' or payment.amount != -order.amount
                        or payment.wallet.account_id != order.customer_account_id
                        or payment.wallet.user_id != order.customer_id
                        or payment.balance_after != payment.balance_before + payment.amount
                        or payment.reversal_of_id or payment.reversals.exists()):
                    return None
                sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
                if not sources or sum(row.amount for row in sources) != order.amount:
                    return None
                validate_order_funding_sources(order, sources)
                refunds_by_kind = {kind: sum(getattr(post.points_refund, f'{kind}_units') for post in verified)
                                   for kind in ('prepaid', 'gift', 'compensation')}
                for kind in ('prepaid', 'gift', 'compensation'):
                    if sum(row.refunded_amount for row in sources if row.kind == kind.upper()) != refunds_by_kind[kind]:
                        return None
                rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
                if type(rate) is not int or not 0 <= rate <= 100:
                    return None
                original_referral = order.amount * rate // 100
                if order.inviter_commission != original_referral:
                    return None
                credits = paid_referral_credits(order, list(order.transactions.filter(status='SUCCESS',
                    tx_type__in=['INCOME', 'GIFT'], amount__gt=0, reversal_of__isnull=True).select_related('wallet')))
                remaining = order.amount - sum(refund.amount for refund in refunds)
                if remaining < 0 or any(tx.reversals.exists() for tx in credits):
                    return None
                expected_referral = remaining * rate // 100
                from .roi_period import points_refund_event_time
                settlement = None
                partial_evidence = None
                if order.settled_at:
                    from orders.points_followup_settlement import verify_followup_settlement_locked
                    settlement = verify_followup_settlement_locked(order)
                else:
                    from orders.models import OrderPointsRefundSettlementPerson
                    if OrderPointsRefundSettlementPerson.objects.filter(
                            settlement__posting__points_refund__redemption__order=order).exists():
                        partial_evidence = _partly_settled_evidence(order, verified[0], verified[-1], start, end)
                if credits:
                    if (len(credits) != 1 or not settlement or settlement.posting_id != verified[-1].pk
                            or settlement.inviter_transaction_id != credits[0].pk
                            or settlement.inviter_units != expected_referral
                            or credits[0].amount != expected_referral
                            or credits[0].created_at <= points_refund_event_time(verified[-1].points_refund)):
                        return None
                elif order.settled_at and (expected_referral or settlement.inviter_transaction_id):
                    return None
                addons = list(OrderAddonPayment.objects.filter(intent__order=order)
                              .select_related('intent', 'buyer_transaction__wallet'))
                handovers = {addon.pk: list(addon.handovers.filter(status='ACCEPTED')
                    .select_related('source_segment__attribution', 'target_segment__attribution')) for addon in addons}
                handover_delta = 0
                if any(handovers.values()):
                    from orders.models import OrderAddonFundingReleaseRun
                    from orders.addon_funding_release import verify_run
                    from orders.points_followup_addon_version import later_runs
                    from orders.points_team_addon_handover_facts import verify_locked
                    revision = verified[-1].followup_funding_revision
                    if not order.settled_at and partial_evidence is None:
                        return None
                    later_runs(order, revision)
                    for addon in addons:
                        accepted = handovers[addon.pk]
                        if len(accepted) > 1:
                            return None
                        if not accepted:
                            continue
                        handover = accepted[0]
                        runs = list(OrderAddonFundingReleaseRun.objects.filter(handover=handover,
                            points_handover_snapshot__kind='SAME_TEAM_V1'))
                        if len(runs) != 1:
                            return None
                        run = runs[0]
                        first_refund_at = verified[0].points_refund.refund.created_at
                        second_refund_at = verified[-1].points_refund.refund.created_at
                        if not handover.accepted_at or handover.accepted_at == first_refund_at:
                            return None
                        if handover.accepted_at < first_refund_at:
                            if run.refunded_main_snapshot is not None:
                                return None
                            verify_locked(order, addon, handover, before=first_refund_at)
                        else:
                            snapshot = run.refunded_main_snapshot
                            if (handover.accepted_at >= second_refund_at or not isinstance(snapshot, dict)
                                    or snapshot.get('posting_id') != verified[0].pk):
                                return None
                        # The post-main run verifies the first real refund,
                        # terminated source, remaining teammate, consent,
                        # audit, individual shares and funding history.
                        verify_run(run)
                        deductions = list(addon.refund_earning_deductions.select_related('refund'))
                        if (addon.refund_records.filter(created_at=handover.accepted_at).exists()
                                or any(row.refund.created_at == handover.accepted_at for row in deductions)):
                            return None
                        income_before = addon.provider_income - sum(row.amount for row in deductions
                                                                   if row.refund.created_at < handover.accepted_at)
                        if income_before < 0:
                            return None
                        if start <= handover.accepted_at < end:
                            handover_delta += sum(row.income for row in handover.shares.all()) - income_before
                for addon in addons:
                    if addon.buyer_transaction.created_at < payment.created_at:
                        return None
                    for refund in addon.refund_records.select_related('buyer_transaction'):
                        tx = refund.buyer_transaction
                        if tx.reversal_of_id or tx.reversals.exists():
                            return None
                addon_costs = _verified_addon_costs(addons, start, end)
                if addon_costs is None:
                    return None
                addon_costs['provider'] += handover_delta
                paid = start <= payment.created_at < end
                provider = sum(item['income_at_entry'] for item in verified[0].participants) if paid else 0
                referral_cost = original_referral if paid else 0
                gift = sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION')) if paid else 0
                running_paid = order.amount
                for posting in verified:
                    refund = posting.points_refund.refund
                    if refund.amount > running_paid:
                        return None
                    referral_delta = running_paid * rate // 100 - (running_paid - refund.amount) * rate // 100
                    running_paid -= refund.amount
                    if start <= points_refund_event_time(posting.points_refund) < end:
                        provider -= sum(posting.shares.values_list('burden', flat=True))
                        referral_cost -= referral_delta
                        gift -= posting.points_refund.gift_units + posting.points_refund.compensation_units
                result = {'provider': provider + addon_costs['provider'], 'referral': referral_cost,
                          'gift_compensation': gift + addon_costs['gift_compensation']}
                if partial_evidence is not None:
                    result['partial_settlement'] = partial_evidence
                return result
    except (DjangoValidationError, ValidationError, ReferralEvidenceError, SettlementError, PointsFundingPlanError, ObjectDoesNotExist,
            ValueError, KeyError, TypeError):
        return None
