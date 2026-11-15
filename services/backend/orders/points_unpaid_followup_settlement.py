"""Isolated sequential settlement when neither provider was paid before refund two."""
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount, LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction, Wallet
from .models import (Order, OrderPointsRefundSettlement, OrderPointsRefundSettlementFunding,
                     OrderPointsRefundSettlementPerson, OrderProvider, OrderSegmentFinancialSnapshot,
                     PointsNegotiationPosting)
from .points_checkout_test import require_isolated_checkout
from .settlement import SettlementError


def enabled():
    from .points_business_access import permits
    return bool(any(permits(phase='settlement', kind=kind) for kind in ('service', 'addon'))
                or getattr(settings, 'SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING', False))


def _addon_data(order, revision, first):
    if not revision.addon_snapshot:
        return (0, 0, 0, 0, 0, 0), ()
    history = revision.addon_snapshot.get('addon_history') if isinstance(revision.addon_snapshot, dict) else None
    if not isinstance(history, list) or not history or any(not isinstance(item, dict) for item in history):
        raise ValidationError('后续退款的加购历史快照缺失')
    ids = [item.get('payment_id') for item in history]
    if any(type(pk) is not int or pk <= 0 for pk in ids) or len(set(ids)) != len(ids):
        raise ValidationError('后续退款加购付款编号异常')
    from .models import OrderAddonPayment
    from .addon_payment import verify_receipt
    from .addon_funding import AddonFundingPart
    payments = {item.pk: item for item in OrderAddonPayment.objects.select_for_update().filter(
        intent__order=order, pk__in=ids).select_related('intent', 'buyer_transaction__wallet')}
    from .points_followup_addon_version import EffectiveRevision
    if isinstance(revision, EffectiveRevision):
        from django.utils.dateparse import parse_datetime
        boundary = parse_datetime(revision.snapshot.get('captured_at', ''))
        if not boundary:
            raise ValidationError('后续加购资金版本时间缺失')
    else:
        boundary = revision.posting.created_at
    if (len(payments) != len(ids) or OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at__lt=boundary).exclude(pk__in=ids).exists()):
        raise ValidationError('后续退款时的加购付款版本待核对')
    fields = ('paid_units', 'principal_units', 'gift_units', 'compensation_units', 'recovery_units', 'provider_units')
    totals, parts = [0] * len(fields), []
    for fact in history:
        payment = payments[fact['payment_id']]
        verify_receipt(payment)
        if (payment.buyer_transaction.created_at >= boundary
                or any(type(fact.get(key)) is not int or fact[key] < 0 for key in fields)):
            raise ValidationError('后续退款加购时间或来源异常')
        handovers = list(payment.handovers.filter(status='ACCEPTED', accepted_at__lt=boundary)
                         .order_by('accepted_at', 'pk'))
        team_run = None
        if payment.handovers.exists():
            from .models import OrderAddonFundingReleaseRun
            if (not handovers or (revision.paid_person_id and
                    (not revision.paid_person.settled_at
                     or handovers[-1].accepted_at >= revision.paid_person.settled_at))):
                raise ValidationError('后续退款的加购接替范围待核对')
            runs = list(OrderAddonFundingReleaseRun.objects.filter(handover__in=handovers,
                points_handover_snapshot__kind='SAME_TEAM_V1').select_related('handover')
                .order_by('handover__accepted_at', 'pk'))
            from .points_team_addon_handover_facts import has_ordered_funding_chain
            if (len(runs) != len(handovers) or not has_ordered_funding_chain(runs)
                    or fact.get('handover_id') != handovers[-1].pk):
                raise ValidationError('后续退款的同单接替版本待核对')
            team_run = runs[-1]
            from .addon_funding_release import verify_run
            verify_run(team_run)

        refunds = list(payment.refund_records.filter(created_at__lt=boundary).order_by('pk'))
        if payment.refund_records.filter(created_at=boundary).exists():
            raise ValidationError('加购退款与后续退款时间无法区分')
        returned = {}
        for refund in refunds:
            for item in refund.proposal.source_plan:
                returned[item['allocation_id']] = returned.get(item['allocation_id'], 0) + item['amount_raw']
        source_values = {kind: 0 for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        for source in payment.allocations.all():
            if source.kind not in source_values:
                raise ValidationError('后续退款加购付款来源异常')
            amount = returned.get(source.pk, 0)
            if not 0 <= amount <= source.amount:
                raise ValidationError('后续退款前加购退款来源异常')
            source_values[source.kind] += source.amount - amount
        if (fact['paid_units'] != payment.intent.amount - sum(item.amount for item in refunds)
                or fact['recovery_units'] != sum(item.wallet_deduction + item.deposit_deduction for item in refunds)
                or (fact['principal_units'], fact['gift_units'], fact['compensation_units']) !=
                   (source_values['PREPAID'], source_values['GIFT'], source_values['COMPENSATION'])
                or fact.get('refund_ids') != [item.pk for item in refunds]):
            raise ValidationError('后续退款加购净付款或退款不一致')
        if team_run:
            from .points_addon_handover_refund_facts import settlement_parts
            from django.core.exceptions import ValidationError as DjangoValidationError
            if refunds:
                try:
                    shares = settlement_parts(refunds[0], before=boundary)
                except DjangoValidationError as exc:
                    raise ValidationError('后续退款的同单加购个人收益待核对') from exc
                parts.extend(AddonFundingPart(payment.pk, row['provider_account_id'], row['segment_id'],
                                              row['provider_income_units']) for row in shares)
                income = sum(row['provider_income_units'] for row in shares)
            else:
                rows = list(handovers[-1].shares.select_related('segment').order_by('position'))
                income = 0
                grouped = {}
                for row in rows:
                    key = (row.segment_id, row.segment.provider_account_id)
                    grouped[key] = grouped.get(key, 0) + row.income
                for (segment_id, account_id), amount in sorted(grouped.items()):
                    used = sum(payment.refund_earning_deductions.filter(segment_id=segment_id,
                        participant__account_id=account_id,
                        refund__created_at__gt=handovers[-1].accepted_at,
                        refund__created_at__lt=boundary).values_list('amount', flat=True))
                    retained = amount - used
                    if retained < 0:
                        raise ValidationError('接替加购历史占用超过本人收益')
                    parts.append(AddonFundingPart(payment.pk, account_id, segment_id, retained))
                    income += retained
        else:
            deducted = payment.refund_earning_deductions.filter(refund__created_at__lt=boundary).aggregate(
                total=Sum('amount'))['total'] or 0
            income = payment.provider_income - deducted
            parts.append(AddonFundingPart(payment.pk, payment.intent.provider_id,
                                          payment.intent.segment_id, fact['provider_units']))
        if income != fact['provider_units']:
            raise ValidationError('后续退款加购收益与快照不一致')
        for index, field in enumerate(fields):
            totals[index] += fact[field]
    return tuple(totals), tuple(parts)


def _facts(revision, addon=(0, 0, 0, 0, 0, 0)):
    paid, principal, gift, compensation, recovery, provider = addon
    return (revision.net_paid_units - paid, revision.principal_units - principal,
            revision.gift_units - gift, revision.compensation_units - compensation,
            revision.recovered_units - recovery, revision.required_units, revision.provider_units - provider,
            revision.inviter_units, revision.store_units, *addon)


def _recorded(settlement):
    return (settlement.paid_units, settlement.principal_units, settlement.gift_units,
            settlement.compensation_units, settlement.recovered_units, settlement.funding_units,
            settlement.provider_units, settlement.inviter_units, settlement.store_units,
            settlement.addon_paid_units, settlement.addon_principal_units, settlement.addon_gift_units,
            settlement.addon_compensation_units, settlement.addon_recovered_units, settlement.addon_provider_units)


def verify_progress_locked(order, revision, first, second, snapshots, assignments):
    """Check local payout evidence only; the caller separately verifies all funding."""
    from .points_followup_settlement import verify_person_locked
    from .points_refunded_settlement import _check_destinations, _funding_rows
    shares = {share.account_id: (posting, share) for posting in (first, second)
              for share in posting.shares.all()}
    addon_snapshot = revision.addon_snapshot
    addon_units = sum(item['provider_units'] for item in addon_snapshot['addon_history']) if addon_snapshot else 0
    from .points_refunded_settlement import _person_amount
    if (len(shares) != 2 or len(snapshots) != 2 or set(shares) != set(assignments)
            or {snapshot.segment.provider_account_id for snapshot in snapshots} != set(assignments)
            or sum(_person_amount(shares[snapshot.segment.provider_account_id][0], snapshot)
                   for snapshot in snapshots) + addon_units != revision.provider_units):
        raise ValidationError('后续退款陪玩份额不完整')
    settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=second).first()
    persons = list(settlement.persons.select_for_update().select_related(
        'segment', 'report', 'income_transaction__wallet')) if settlement else []
    if settlement:
        addon, parts = _addon_data(order, revision, first)
        funding_rows = _funding_rows(order)
        if (_recorded(settlement) != _facts(revision, addon) or sum(amount for _, amount in funding_rows) != revision.required_units
                or list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
                   != funding_rows):
            raise ValidationError('后续退款收益或补资快照不一致')
        if parts or settlement.addon_settlements.exists():
            from .addon_settlement import verify_addon_records
            actual = verify_addon_records(order, settlement, parts, persons)
            expected = {(part.payment_id, part.segment_id) for part in parts
                        if part.segment_id in {person.segment_id for person in persons}}
            if actual != expected:
                raise ValidationError('后续退款已结算加购凭证不一致')
    if len({person.segment_id for person in persons}) != len(persons):
        raise ValidationError('后续退款陪玩结算重复')
    by_segment = {snapshot.segment_id: snapshot for snapshot in snapshots}
    credited = set()
    for person in persons:
        snapshot = by_segment.get(person.segment_id)
        if not snapshot or snapshot.segment.provider_account_id not in shares:
            raise ValidationError('后续退款陪玩结算归属异常')
        account = snapshot.segment.provider_account_id
        posting, share = shares[account]
        verify_person_locked(order, posting, share, assignments[account], person,
                             earliest=second.termination_processed_at)
        credited.add(account)
    for account, assignment in assignments.items():
        if account in credited:
            continue
        if assignment.settled_at or order.transactions.filter(
                tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                remark=f'订单收入：{order.order_no}', wallet__user_id=assignment.provider_id).exists():
            raise ValidationError('未结算陪玩存在收入流水')
    recorded_income_ids = {person.income_transaction_id for person in persons if person.income_transaction_id}
    actual_income_ids = set(order.transactions.filter(tx_type=Transaction.TxType.INCOME,
        status=Transaction.Status.SUCCESS, remark=f'订单收入：{order.order_no}').values_list('pk', flat=True))
    if recorded_income_ids != actual_income_ids:
        raise ValidationError('后续退款陪玩收入流水数量异常')
    if order.settled_at:
        if (not settlement or settlement.finalized_at != order.settled_at or len(persons) != len(snapshots)
                or any(person.settled_at > order.settled_at for person in persons)):
            raise ValidationError('后续退款整单结算状态异常')
        try:
            _check_destinations(order, settlement, SimpleNamespace(
                inviter_income_units=revision.inviter_units, store_income_units=revision.store_units))
        except SettlementError as exc:
            raise ValidationError('后续退款门店或推荐收益异常') from exc
    elif (settlement and (settlement.finalized_at or settlement.inviter_transaction_id or settlement.store_transaction_id)
          or order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
              remark=f'推荐分佣：{order.order_no}').exists()
          or order.transactions.filter(tx_type=Transaction.TxType.SHOP_INCOME, status=Transaction.Status.SUCCESS).exists()):
        raise ValidationError('未完成结算存在门店或推荐收益')
    return settlement, persons


def settle_unpaid_followup_locked(order, revision, *, trace_id=None):
    """Reserve full funding, release each eligible person and finalize only after both."""
    from .points_business_access import permits
    formal = any(permits(order, phase='settlement', kind=kind) for kind in ('service', 'addon'))
    if not formal:
        require_isolated_checkout()
    if not enabled() or not transaction.get_connection().in_atomic_block:
        raise SettlementError('未结算双陪后续退款收益尚未开放')
    from .aftersale_scope import applies_to
    from .points_followup_funding import verify_followup_funding
    from .points_followup_settlement import verified_report_payout, verify_followup_settlement_locked
    from .points_refunded_settlement import _deadline, _funding_rows, _person_amount
    from .services import _credit_wallet, missing_report_evidence, refresh_escort_status
    from wallet.income import hold_provider_income
    from wallet.models import RefundDepositDeficit, RefundDepositRecoveryPermit

    funding = verify_followup_funding(order.pk)
    if not funding.fully_funded:
        return []
    from .points_followup_addon_version import EffectiveRevision, later_runs
    versions = later_runs(order, revision)
    if versions:
        revision = EffectiveRevision(revision, versions[-1].points_followup_snapshot)
    second = revision.posting
    first = PointsNegotiationPosting.objects.get(points_refund_id=second.proposal.points_terms.prior_refund_id)
    if revision.addon_snapshot and not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_ADDON_UNPAID_SETTLEMENT_TESTING', False):
        raise SettlementError('带加购的后续退款收益结算尚未开放')
    addon, parts = _addon_data(order, revision, first)
    from .points_service_participation import service_snapshots
    snapshots = service_snapshots(order)
    assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update().filter(
        order=order, provider_account_id__in=[snap.segment.provider_account_id for snap in snapshots])}
    settlement, persons = verify_progress_locked(order, revision, first, second, snapshots, assignments)
    if order.settled_at:
        verify_followup_settlement_locked(order)
        from engagement.models import ConsumptionConfirmation, RefundedPointsConsumptionEvidence
        benefit = RefundedPointsConsumptionEvidence.objects.filter(settlement=settlement).first()
        confirmed = bool(order.consumption_confirmed_at or ConsumptionConfirmation.objects.filter(order=order).exists())
        if (confirmed or order.funding_benefits_resolved_at) and not benefit:
            raise SettlementError('后续退款会员实销凭证缺失')
        if benefit:
            from engagement.refunded_points_confirmation import verify_evidence_locked
            verify_evidence_locked(benefit)
        return []
    from engagement.models import ConsumptionConfirmation, PointLedger
    if (order.consumption_confirmed_at or order.funding_benefits_resolved_at
            or ConsumptionConfirmation.objects.filter(order=order).exists()
            or PointLedger.objects.filter(order=order, kind='CONSUMPTION',
                                          event_key=f'order:{order.pk}:points').exists()):
        raise SettlementError('后续退款已有会员实销或消费积分')
    wallet = Wallet.objects.select_for_update().filter(pk=second.points_refund.redemption.payment.wallet_id).first()
    buyer = ClubAccount.objects.select_for_update().filter(pk=order.customer_account_id).first()
    if (not wallet or not wallet.is_active or wallet.account_id != order.customer_account_id
            or wallet.user_id != order.customer_id or not buyer or not buyer.is_active
            or buyer.account_type != ClubAccount.AccountType.BOSS or buyer.business_cycle != order.business_cycle
            or (order.customer_id and not LegacyAccountMap.objects.filter(
                account_id=buyer.pk, legacy_user_id=order.customer_id).exists())):
        raise SettlementError('后续退款老板钱包身份待核对')
    if (order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100):
        raise SettlementError('后续退款结算时限或押金快照待核对')
    if not settlement:
        facts = _facts(revision, addon)
        settlement = OrderPointsRefundSettlement.objects.create(posting=second, paid_units=facts[0],
            principal_units=facts[1], gift_units=facts[2], compensation_units=facts[3], recovered_units=facts[4],
            funding_units=facts[5], provider_units=facts[6], inviter_units=facts[7], store_units=facts[8],
            addon_paid_units=facts[9], addon_principal_units=facts[10], addon_gift_units=facts[11],
            addon_compensation_units=facts[12], addon_recovered_units=facts[13], addon_provider_units=facts[14])
        OrderPointsRefundSettlementFunding.objects.bulk_create([
            OrderPointsRefundSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount)
            for pk, amount in _funding_rows(order)])
    shares = {share.account_id: (posting, share) for posting in (first, second) for share in posting.shares.all()}
    credited = {person.segment_id for person in persons}
    reports = {row.provider_account_id: row for row in ProviderReport.objects.select_for_update().filter(order=order)}
    cases, now = list(order.aftersales.filter(status='OPEN')), timezone.now()
    if now < order.completed_at:
        return []
    from common.logging_utils import new_trace_id
    trace_id = trace_id or new_trace_id()
    settled, notified = [], []
    for snapshot in snapshots:
        if snapshot.segment_id in credited:
            continue
        account = snapshot.segment.provider_account_id
        assignment, report = assignments[account], reports.get(account)
        posting, share = shares[account]
        if (not assignment.provider_id or not LegacyAccountMap.objects.filter(
                account_id=account, legacy_user_id=assignment.provider_id).exists()):
            raise SettlementError('后续退款陪玩账号待核对')
        if (any(applies_to(case, account) for case in cases) or now < _deadline(order, posting, account)
                or not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at
                or report.submitted_at + timedelta(hours=order.service_freeze_hours) > now):
            continue
        if (report.provider_id != assignment.provider_id or not report.audited_at
                or report.audited_at < report.submitted_at or report.audited_at > now
                or report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate
                or report.payout_amount != verified_report_payout(order, report, posting, share, snapshot)
                or missing_report_evidence(report)):
            raise SettlementError('后续退款报单或陪玩收益待核对')
        profile = EscortProfile.objects.select_for_update().filter(user_id=assignment.provider_id, account_id=account).first()
        if not profile:
            raise SettlementError('后续退款陪玩档案待核对')
        from wallet.refund_deposit import enabled as deficit_enabled
        from wallet.refund_recovery_order import tracking_enabled
        has_gap = RefundDepositDeficit.objects.filter(profile=profile, remaining__gt=0).exists()
        has_permit = RefundDepositRecoveryPermit.objects.filter(profile=profile, closed_at__isnull=True).exists()
        if ((has_gap or has_permit) and not deficit_enabled()) or (has_permit and not tracking_enabled()):
            raise SettlementError('陪玩退款押金缺口或补差机会尚未接通')
        amount = _person_amount(posting, snapshot)
        income = _credit_wallet(order=order, amount=amount, tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}', event='order.points_followup_settle.provider', trace_id=trace_id,
            user_id=assignment.provider_id, account_id=account, log_after_commit=True)
        hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                             apply_without_hold=True)
        person = OrderPointsRefundSettlementPerson.objects.create(settlement=settlement, segment=snapshot.segment,
            report=report, income_transaction=income, amount=amount, settled_at=now)
        if parts:
            from .addon_settlement import settle_person_addons
            settle_person_addons(order, settlement, parts, snapshot=snapshot, assignment=assignment,
                                 report=report, profile=profile, now=now, trace_id=trace_id)
        assignment.settled_at = now
        assignment.save(update_fields=['settled_at'])
        if not amount:
            from wallet.refund_recovery_order import close_for_zero_points_refund
            close_for_zero_points_refund(profile, person)
        settled.append(assignment.provider_id)
        notified.append((account, report))
    if settled:
        EscortProfile.objects.filter(user_id__in=settled).update(completed_order_count=F('completed_order_count') + 1)
        refresh_escort_status(provider_ids=settled, account_ids=[item[0] for item in notified],
                              exclude_order_ids=[order.pk])
        from .notifier import notify_report_update
        for _, report in notified:
            transaction.on_commit(lambda report=report: notify_report_update(report))
    if not cases and settlement.persons.count() == len(snapshots):
        if settlement.addon_settlements.count() != len(parts):
            raise SettlementError('后续退款加购收益尚未全部结算')
        if order.inviter_id or order.inviter_account_id:
            settlement.inviter_transaction = _credit_wallet(order=order, amount=revision.inviter_units,
                tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}',
                event='order.points_followup_settle.inviter', trace_id=trace_id,
                user_id=order.inviter_id, account_id=order.inviter_account_id, log_after_commit=True)
            tx = settlement.inviter_transaction
            if tx and tx.wallet.account_id and ClubAccount.objects.filter(
                    pk=tx.wallet.account_id, account_type=ClubAccount.AccountType.BOSS).exists():
                from engagement.models import PointFundingLot
                PointFundingLot.objects.create(account_id=tx.wallet.account_id,
                    source_key=f'points-refund-settlement-referral:{settlement.pk}',
                    kind=PointFundingLot.Kind.PREPAID, remaining=tx.amount)
        from .points_rounding_settlement import post as post_rounding
        rounding = post_rounding(order, revision.store_units, trace_id)
        settlement.store_transaction = _credit_wallet(order=order, amount=revision.store_units - rounding,
            tx_type=Transaction.TxType.SHOP_INCOME, remark=f'平台收入：{order.order_no}',
            event='order.points_followup_settle.store', trace_id=trace_id, platform=True, log_after_commit=True)
        settlement.finalized_at = now
        settlement.save(update_fields=['inviter_transaction', 'store_transaction', 'finalized_at'])
        order.settled_at = now
        order.save(update_fields=['settled_at'])
        verify_followup_settlement_locked(order)
    if settled or settlement.finalized_at:
        from engagement.confirmation import notify_committed_order
        notify_committed_order(order.pk)
    return settled
