"""Isolated settlement of unrefunded, untransferred service orders."""
from contextlib import ExitStack
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction
from .ordinary_addon_production import eligible as _ordinary_addon_eligible
from .models import (Order, OrderPointsFundingAllocation, OrderPointsRedemption, OrderPointsSettlement,
                     OrderPointsSettlementFunding, OrderPointsSettlementPerson, OrderProvider,
                     OrderSegmentFinancialSnapshot)
from .points_checkout_test import ordinary_settlement_enabled, settlement_enabled
from .points_funding_evidence import verify_order_funding
from .points_funding_plan import PointsFundingPlanError, plan_verified_order
from .settlement import SettlementError


def _ordinary_snapshots_verified(order, snapshots, assignments):
    from .models import OrderOriginalAttribution, OrderSegmentFinancialCoverage
    attribution = OrderOriginalAttribution.objects.select_for_update().filter(order=order, status='ACTIVE').first()
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(
        attribution=attribution, status='ACTIVE', source='WALLET_NO_POINTS', deal_amount=order.amount).first() if attribution else None
    if not coverage or len(snapshots) != order.required_provider_count or len(assignments) != len(snapshots):
        raise SettlementError('服务订单履约与收益快照待核对')
    for snapshot in snapshots:
        assignment = assignments.get(snapshot.segment.provider_account_id)
        if (not assignment or snapshot.segment.attribution_id != attribution.pk or snapshot.segment.released_at
                or snapshot.retained_basis is not None or snapshot.incoming_basis != assignment.settlement_base
                or snapshot.income_at_entry != assignment.provider_income
                or snapshot.commission_type != assignment.commission_type
                or snapshot.commission_rate != assignment.commission_rate
                or snapshot.commission_fixed != assignment.commission_fixed
                or snapshot.game_commission_snapshot != assignment.game_commission_snapshot):
            raise SettlementError('服务订单陪玩收益快照不一致')


def _verify_person(order, person, snapshot, assignment, *, deduction=0):
    from .services import missing_report_evidence
    from engagement.confirmation import aftersale_deadline
    deadline = aftersale_deadline(order)
    if (person.segment_id != snapshot.segment_id or person.amount != snapshot.income_at_entry - deduction
            or person.report.order_id != order.pk or person.report.provider_id != assignment.provider_id
            or person.report.provider_account_id != assignment.provider_account_id
            or person.report.status != ProviderReport.Status.APPROVED
            or person.report.amount != snapshot.incoming_basis
            or person.report.commission_rate != snapshot.commission_rate
            or person.report.payout_amount != snapshot.income_at_entry or not person.report.submitted_at
            or not person.report.audited_at or person.report.audited_at < person.report.submitted_at
            or person.report.audited_at > person.settled_at
            or not deadline or deadline > person.settled_at
            or person.report.submitted_at + timedelta(hours=order.service_freeze_hours) > person.settled_at
            or missing_report_evidence(person.report)):
        raise SettlementError('已结算陪玩报单或份额待核对')
    tx = person.income_transaction
    credits = set(order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
        remark=f'订单收入：{order.order_no}', wallet__user_id=assignment.provider_id).values_list('pk', flat=True))
    if not person.amount:
        if tx or credits:
            raise SettlementError('零收益不应产生收入流水')
        return
    if (not tx or tx.order_id != order.pk or tx.wallet.user_id != assignment.provider_id
            or tx.wallet.account_id != assignment.provider_account_id or tx.tx_type != Transaction.TxType.INCOME
            or tx.status != Transaction.Status.SUCCESS or tx.amount != person.amount or tx.reversal_of_id
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversals.exists() or credits != {tx.pk}):
        raise SettlementError('已结算陪玩收入流水待核对')


def settle_points_order(order, *, operator=None, operator_account=None, trace_id=None):
    from .points_service_payment import needs_integrated_processing
    from django.conf import settings
    if needs_integrated_processing(order) and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
        from .points_business_access import service_order
        with service_order(order.pk, phase='settlement', addon=order.addon_intents.filter(payment__isnull=False).exists()):
            return _settle_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    if (not needs_integrated_processing(order)
            and (not ordinary_settlement_enabled() or order.aftersales.filter(status='OPEN').exists())):
        if order.settled_at:
            if not OrderPointsSettlement.objects.filter(order=order, redemption__isnull=True,
                    finalized_at=order.settled_at).exists():
                raise SettlementError('普通订单结算记录待核对')
            return []
        from .ordinary_funding_continuation import verified_continuation
        paid_addons = order.addon_intents.filter(payment__isnull=False).exists()
        with verified_continuation(order, allow_open_case=True, allow_paid_addons=paid_addons):
            return _settle_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    return _settle_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)


def _settle_points_order(order, *, operator=None, operator_account=None, trace_id=None):
    """Pay mature individuals only after every funding source is verified.

    The caller owns the order lock inside a tenant transaction. New receipts
    reserve the full order before the first person is credited. The separate
    Points-order consumption/VIP confirmation and refund paths remain closed.
    """
    from .points_service_payment import needs_integrated_processing
    is_points = needs_integrated_processing(order)
    from .points_checkout_test import ordinary_settlement_enabled
    from .ordinary_funding_continuation import continuation_active
    if ((is_points and not settlement_enabled()) or
            (not is_points and not ordinary_settlement_enabled() and not continuation_active(order))
            or not transaction.get_connection().in_atomic_block):
        raise SettlementError('积分订单收益结算尚未开放')
    if not is_points:
        from .points_checkout_test import require_order_funding_access
        from rest_framework.exceptions import ValidationError
        try:
            require_order_funding_access(order)
        except ValidationError as exc:
            raise SettlementError(str(exc)) from exc
    if order.settled_at:
        settlement = OrderPointsSettlement.objects.filter(order=order, finalized_at=order.settled_at).first()
        if not settlement:
            raise SettlementError('积分订单结算记录待核对')
        return []
    from engagement.confirmation import aftersale_wait_elapsed
    if order.status != Order.Status.COMPLETED or order.payment_status != Order.PaymentStatus.PAID:
        return []
    if not aftersale_wait_elapsed(order):
        return []
    if (order.service_clock_version is None or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100
            or not order.customer_account_id):
        raise SettlementError('订单结算时限、账号或押金快照待核对')
    from club_accounts.models import ClubAccount
    from wallet.services import get_wallet
    from .financial_lock import lock_order_money
    lock_order_money()
    get_wallet(account_id=order.customer_account_id, user_id=order.customer_id, for_update=True)
    account = ClubAccount.objects.select_for_update().get(pk=order.customer_account_id)
    if (account.business_cycle != order.business_cycle or account.account_type != ClubAccount.AccountType.BOSS
            or (order.customer_id and not LegacyAccountMap.objects.filter(
                account_id=account.pk, legacy_user_id=order.customer_id).exists())):
        raise SettlementError('订单老板账号或业务周期待核对')
    try:
        if is_points:
            plan = plan_verified_order(order.pk)
        else:
            from .service_funding_plan import plan_verified_ordinary_order
            plan = plan_verified_ordinary_order(order.pk)
        from .addon_settlement import combined_terms
        addon = combined_terms(order, plan)
        funding = verify_order_funding(order.pk)
    except PointsFundingPlanError as exc:
        raise SettlementError(str(exc)) from exc
    if not funding.fully_funded:
        return []
    main_deductions = dict(addon.main_deductions) if addon else {}

    redemption = OrderPointsRedemption.objects.get(order=order) if is_points else None
    snapshots = list(OrderSegmentFinancialSnapshot.objects.select_for_update()
                     .filter(segment__attribution__order=order, segment__released_at__isnull=True)
                     .select_related('segment').order_by('segment__slot'))
    assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update()
                   .filter(order=order, released_at__isnull=True)}
    reports = {row.provider_account_id: row for row in ProviderReport.objects.select_for_update().filter(order=order)}
    if len(snapshots) != order.required_provider_count or len(assignments) != len(snapshots):
        raise SettlementError('订单陪玩份额待核对')
    if not is_points:
        _ordinary_snapshots_verified(order, snapshots, assignments)
    allocs = list(OrderPointsFundingAllocation.objects.select_for_update().filter(
        Q(redemption__order=order) | Q(service_order=order)).order_by('pk'))
    from .addon_settlement import settlement_facts, verify_addon_records
    facts = settlement_facts(plan, addon)
    from .addon_funding_release import addon_release_totals
    released = addon_release_totals(allocs)
    funding_uses = [(row.pk, row.amount - released.get(row.pk, 0)) for row in allocs
                    if row.amount - released.get(row.pk, 0) > 0]
    settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
    if settlement:
        if not is_points:
            from .models import OrdinaryAddonPaidRefundEvidence
            from .ordinary_addon_paid_refund import verify_recorded
            from rest_framework.exceptions import ValidationError
            evidence_rows = list(OrdinaryAddonPaidRefundEvidence.objects.select_related(
                'settlement', 'refund__proposal', 'run').filter(settlement=settlement).order_by('pk'))
            try:
                for evidence in evidence_rows:
                    verify_recorded(evidence)
                evidence_ids = {item.refund_id for item in evidence_rows}
                for refund in order.addon_refunds.order_by('pk'):
                    if (settlement.persons.filter(settled_at__lt=refund.created_at).exists()
                            and refund.pk not in evidence_ids):
                        raise ValidationError('加购退款缺少原已付陪玩凭证')
            except ValidationError as exc:
                raise SettlementError('加购退款已付流水待核对') from exc
        if (settlement.redemption_id != (redemption.pk if redemption else None) or settlement.finalized_at
                or settlement.inviter_transaction_id or settlement.store_transaction_id):
            raise SettlementError('积分订单结算快照待核对')
        prior_people = list(settlement.persons.select_for_update().select_related('report', 'income_transaction__wallet'))
        if addon:
            by_segment = {snapshot.segment_id: snapshot for snapshot in snapshots}
            for person in prior_people:
                snapshot = by_segment.get(person.segment_id)
                assignment = assignments.get(snapshot.segment.provider_account_id) if snapshot else None
                if not snapshot or not assignment:
                    raise SettlementError('已结算陪玩履约份额待核对')
                _verify_person(order, person, snapshot, assignment,
                               deduction=main_deductions.get(assignment.provider_account_id, 0))
            recorded_addons = verify_addon_records(order, settlement, addon.addon_parts, prior_people)
            paid_segments = {person.segment_id for person in prior_people}
            expected_addons = {(part.payment_id, part.segment_id) for part in addon.addon_parts
                               if part.segment_id in paid_segments}
            if recorded_addons != expected_addons:
                raise SettlementError('已结算陪玩的加购收益凭证不完整')
        previous_facts = (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
                          settlement.gift_units, settlement.compensation_units, settlement.funding_units,
                          settlement.provider_units, settlement.inviter_units, settlement.store_units)
        if previous_facts != facts:
            if not addon:
                raise SettlementError('积分订单结算快照待核对')
            (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
             settlement.gift_units, settlement.compensation_units, settlement.funding_units,
             settlement.provider_units, settlement.inviter_units, settlement.store_units) = facts
            settlement.save(update_fields=['paid_units', 'principal_units', 'recovery_units', 'gift_units',
                'compensation_units', 'funding_units', 'provider_units', 'inviter_units', 'store_units'])
        uses = list(settlement.funding_uses.select_for_update().order_by('allocation_id'))
        if [(row.allocation_id, row.amount) for row in uses] != funding_uses:
            if not addon:
                raise SettlementError('积分订单补资使用记录待核对')
            settlement.funding_uses.all().delete()
            OrderPointsSettlementFunding.objects.bulk_create([
                OrderPointsSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount)
                for pk, amount in funding_uses])
    else:
        settlement = OrderPointsSettlement.objects.create(order=order, redemption=redemption,
            paid_units=facts[0], principal_units=facts[1], recovery_units=facts[2], gift_units=facts[3],
            compensation_units=facts[4], funding_units=facts[5], provider_units=facts[6],
            inviter_units=facts[7], store_units=facts[8])
        OrderPointsSettlementFunding.objects.bulk_create([
            OrderPointsSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount)
            for pk, amount in funding_uses])
    persons = {row.segment_id: row for row in settlement.persons.select_for_update()
               .select_related('report', 'income_transaction__wallet')}
    if set(persons) - {row.segment_id for row in snapshots}:
        raise SettlementError('积分订单存在额外结算人员')
    from .aftersale_scope import applies_to
    from .services import _credit_wallet, missing_report_evidence, refresh_escort_status
    from wallet.income import hold_provider_income
    from common.logging_utils import new_trace_id
    open_cases = list(order.aftersales.filter(status='OPEN'))
    now = timezone.now()
    eligible = []
    for snapshot in snapshots:
        assignment = assignments.get(snapshot.segment.provider_account_id)
        if (not assignment or not assignment.provider_id or not LegacyAccountMap.objects.filter(
                account_id=assignment.provider_account_id, legacy_user_id=assignment.provider_id).exists()):
            raise SettlementError('陪玩账号归属待核对')
        report = reports.get(assignment.provider_account_id)
        person = persons.get(snapshot.segment_id)
        if person:
            _verify_person(order, person, snapshot, assignment,
                           deduction=main_deductions.get(assignment.provider_account_id, 0))
            continue
        if order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                remark=f'订单收入：{order.order_no}', wallet__user_id=assignment.provider_id).exists():
            raise SettlementError('陪玩已有收入流水但缺少结算凭证')
        if any(applies_to(case, assignment.provider_account_id) for case in open_cases):
            continue
        if not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at:
            continue
        if report.submitted_at + timedelta(hours=order.service_freeze_hours) > now:
            continue
        if (report.provider_id != assignment.provider_id or not report.audited_at
                or report.audited_at < report.submitted_at or report.audited_at > now
                or report.amount != snapshot.incoming_basis
                or report.commission_rate != snapshot.commission_rate or report.payout_amount != snapshot.income_at_entry
                or missing_report_evidence(report)):
            raise SettlementError('陪玩报单金额或履约凭证待核对')
        eligible.append((snapshot, assignment, report))

    trace_id = trace_id or new_trace_id()
    profiles = {row.user_id: row for row in EscortProfile.objects.select_for_update()
                .filter(user_id__in=sorted(item[1].provider_id for item in eligible)).order_by('user_id')}
    settled = []
    for snapshot, assignment, report in eligible:
        profile = profiles.get(assignment.provider_id)
        if not profile or profile.account_id != assignment.provider_account_id:
            raise SettlementError('陪玩档案归属待核对')
        amount = snapshot.income_at_entry - main_deductions.get(assignment.provider_account_id, 0)
        income = _credit_wallet(order=order, amount=amount, tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}', event='order.points_settle.provider', trace_id=trace_id,
            user_id=assignment.provider_id, account_id=assignment.provider_account_id, log_after_commit=bool(addon))
        hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                             apply_without_hold=True)
        person = OrderPointsSettlementPerson.objects.create(settlement=settlement, segment=snapshot.segment, report=report,
            income_transaction=income, amount=amount, settled_at=now)
        if addon:
            from .addon_settlement import settle_person_addons
            settle_person_addons(order, settlement, addon.addon_parts, snapshot=snapshot, assignment=assignment,
                                 report=report, profile=profile, now=now, trace_id=trace_id)
        if is_points and not amount:
            from wallet.refund_recovery_order import close_for_zero_initial_points
            close_for_zero_initial_points(profile, person, addon.addon_parts if addon else [])
        settled.append(assignment.provider_id)
    if settled:
        EscortProfile.objects.filter(user_id__in=settled).update(completed_order_count=F('completed_order_count') + 1)
        refresh_escort_status(provider_ids=settled,
            account_ids=[assignments[s.segment.provider_account_id].provider_account_id for s, _, _ in eligible],
            exclude_order_ids=[order.pk])
    if not open_cases and settlement.persons.count() == len(snapshots):
        if addon and settlement.addon_settlements.count() != len(addon.addon_parts):
            raise SettlementError('加购收益尚未全部结算')
        if order.transactions.filter(status=Transaction.Status.SUCCESS, reversal_of__isnull=True).filter(
                Q(tx_type=Transaction.TxType.SHOP_INCOME) |
                Q(tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}')).exists():
            raise SettlementError('订单已有门店或推荐收益流水，请先核对')
        if order.inviter_id or order.inviter_account_id:
            settlement.inviter_transaction = _credit_wallet(order=order, amount=facts[7],
                tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}',
                event='order.points_settle.inviter', trace_id=trace_id,
                user_id=order.inviter_id, account_id=order.inviter_account_id, log_after_commit=bool(addon))
            inviter_tx = settlement.inviter_transaction
            if inviter_tx and inviter_tx.wallet.account_id:
                from club_accounts.models import ClubAccount
                if ClubAccount.objects.filter(pk=inviter_tx.wallet.account_id,
                                              account_type=ClubAccount.AccountType.BOSS).exists():
                    from engagement.models import PointFundingLot
                    PointFundingLot.objects.create(account_id=inviter_tx.wallet.account_id,
                        source_key=f'points-settlement-referral:{settlement.pk}',
                        kind=PointFundingLot.Kind.PREPAID, remaining=inviter_tx.amount)
        addon_refunds = list(order.addon_refunds.select_for_update().order_by('pk')) if not is_points else []
        addon_rounding = sum(row.rounding_surplus for row in addon_refunds)
        if addon_rounding > facts[8] or any(row.rounding_transaction_id for row in addon_refunds):
            raise SettlementError('加购退款取整差额资金待核对')
        settlement.store_transaction = _credit_wallet(order=order, amount=facts[8] - addon_rounding,
            tx_type=Transaction.TxType.SHOP_INCOME, remark=f'平台收入：{order.order_no}',
            event='order.points_settle.store', trace_id=trace_id, platform=True, log_after_commit=bool(addon))
        for refund in addon_refunds:
            refund.rounding_transaction = _credit_wallet(order=order, amount=refund.rounding_surplus,
                tx_type=Transaction.TxType.SHOP_INCOME, remark=f'加购退款取整差额：{order.order_no}（退款 {refund.pk}）',
                event='order.ordinary_addon_settle.rounding', trace_id=trace_id, platform=True, log_after_commit=True)
            refund.save(update_fields=['rounding_transaction'])
        settlement.finalized_at = now
        settlement.save(update_fields=['inviter_transaction', 'store_transaction', 'finalized_at'])
        order.settlement_store_funding = facts[5]
        order.settlement_addon_contribution = (max(0, addon.addon_principal_units + addon.addon_provider_recovery_units
            - addon.addon_provider_income_units - addon.addon_inviter_income_units) if addon else 0)
        order.settled_at = now
        order.save(update_fields=['settlement_store_funding', 'settlement_addon_contribution', 'settled_at'])
    return settled


def _continued_ordinary_refund_display(order_id, report_id=None):
    """Read a saved ordinary refund's settlement with its original facts."""
    from django.db import DatabaseError
    from rest_framework.exceptions import ValidationError
    from .ordinary_funding_continuation import verified_refunded_continuation
    from .points_service_payment import needs_integrated_processing
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            if needs_integrated_processing(order):
                return 'REVIEW', None
            with verified_refunded_continuation(order, read_only=True):
                from .models import OrderOrdinaryRefundAddonSettlementEvidence
                if (not OrderOrdinaryRefundAddonSettlementEvidence.objects.filter(settlement__order=order).exists()
                        and order.transactions.filter(tx_type__in=['INCOME', 'SHOP_INCOME'],
                            status='SUCCESS', amount__gt=0).exists()):
                    return 'REVIEW', None
                if report_id is None:
                    return ('REVIEW' if order.settled_at else 'WAITING'), None
                report = ProviderReport.objects.get(pk=report_id, order=order)
                from .negotiation_settlement import report_settlement_snapshot
                return report_settlement_snapshot(report) or ('REVIEW', None)
    except (PointsFundingPlanError, SettlementError, DatabaseError, ValidationError,
            Order.DoesNotExist, ProviderReport.DoesNotExist):
        return 'REVIEW', None


def _formal_points_display_candidate(order):
    from .points_service_payment import display_has_points
    return (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and order.status == Order.Status.COMPLETED and display_has_points(order))


def verified_person_settlement_display(report):
    """Return a status/time only after checking persisted settlement evidence."""
    if not report.order_id:
        return 'REVIEW', None
    if not (settlement_enabled() or ordinary_settlement_enabled()
            or _formal_points_display_candidate(report.order)):
        return _continued_ordinary_refund_display(report.order_id, report.pk)
    from django.db import DatabaseError
    from django.db.models import Q
    from rest_framework.exceptions import ValidationError
    try:
        with transaction.atomic(), ExitStack() as scopes:
            order = Order.objects.select_for_update().get(pk=report.order_id)
            from .points_service_payment import needs_integrated_processing
            is_points = needs_integrated_processing(order)
            from .models import OrderOrdinaryRefundAddonSettlementEvidence
            from .points_checkout_test import ordinary_refund_production_order
            if (not is_points and ((ordinary_refund_production_order(order) or _ordinary_addon_eligible(order)) or getattr(settings, 'ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING', False))
                    and OrderOrdinaryRefundAddonSettlementEvidence.objects.filter(settlement__order=order).exists()):
                from .negotiation_settlement import report_settlement_snapshot
                return report_settlement_snapshot(report) or ('REVIEW', None)
            if is_points and order.refund_records.exists():
                if order.refund_records.count() == 2:
                    from .points_followup_settlement import enabled as followup_enabled, verified_person_display
                    if followup_enabled():
                        return verified_person_display(report)
                from .points_refunded_settlement import verified_person_display
                return verified_person_display(report)
            if is_points:
                from .addon_settlement import has_paid_addons
                from .models import OrderOriginalShareTransfer
                from .points_business_access import configured, service_order
                if (not settlement_enabled() and configured() and order.status == Order.Status.COMPLETED
                        and not has_paid_addons(order)
                        and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                    from .financial_lock import lock_existing_order_money
                    lock_existing_order_money()
                    scopes.enter_context(service_order(order.pk, phase='settlement'))
                plan = plan_verified_order(order.pk, allow_finalized=True)
            else:
                from .service_funding_plan import plan_verified_ordinary_order
                if not ordinary_settlement_enabled():
                    return 'REVIEW', None
                plan = plan_verified_ordinary_order(order.pk, allow_finalized=True)
            settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
            if not settlement:
                unexpected = order.transactions.filter(tx_type=Transaction.TxType.INCOME,
                    status=Transaction.Status.SUCCESS, remark=f'订单收入：{order.order_no}').exists()
                return ('REVIEW' if order.settled_at or unexpected else 'WAITING'), None
            from .addon_settlement import combined_terms, settlement_facts, verify_addon_records
            addon = combined_terms(order, plan)
            main_deductions = dict(addon.main_deductions) if addon else {}
            funding = verify_order_funding(order.pk, allow_finalized=True)
            facts = settlement_facts(plan, addon)
            if (not funding.fully_funded
                    or (is_points and (not settlement.redemption_id or settlement.redemption.order_id != order.pk))
                    or (not is_points and settlement.redemption_id is not None)
                    or (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
                        settlement.gift_units, settlement.compensation_units, settlement.funding_units,
                        settlement.provider_units, settlement.inviter_units, settlement.store_units) != facts
                    or settlement.finalized_at != order.settled_at):
                return 'REVIEW', None
            allocations_query = list(OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order)).order_by('pk'))
            from .addon_funding_release import addon_release_totals
            released = addon_release_totals(allocations_query)
            allocations = [(row.pk, row.amount - released.get(row.pk, 0)) for row in allocations_query
                           if row.amount - released.get(row.pk, 0) > 0]
            uses = list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
            if allocations != uses:
                return 'REVIEW', None
            snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
                                                                          segment__released_at__isnull=True)
                             .select_related('segment'))
            assignments = {row.provider_account_id: row for row in OrderProvider.objects.filter(
                order=order, released_at__isnull=True)}
            if not is_points:
                _ordinary_snapshots_verified(order, snapshots, assignments)
            people = list(settlement.persons.select_related('report', 'income_transaction__wallet'))
            by_segment = {row.segment_id: row for row in snapshots}
            if (len(snapshots) != order.required_provider_count or len(assignments) != len(snapshots)
                    or any(row.segment_id not in by_segment for row in people)):
                return 'REVIEW', None
            for person in people:
                snapshot = by_segment[person.segment_id]
                assignment = assignments.get(snapshot.segment.provider_account_id)
                if not assignment or not LegacyAccountMap.objects.filter(
                        account_id=assignment.provider_account_id, legacy_user_id=assignment.provider_id).exists():
                    return 'REVIEW', None
                _verify_person(order, person, snapshot, assignment,
                               deduction=main_deductions.get(assignment.provider_account_id, 0))
            if is_points and not addon:
                income_ids = set(order.transactions.filter(tx_type=Transaction.TxType.INCOME,
                    status=Transaction.Status.SUCCESS, remark=f'订单收入：{order.order_no}').values_list('pk', flat=True))
                if income_ids != {person.income_transaction_id for person in people if person.income_transaction_id}:
                    return 'REVIEW', None
            if addon:
                addon_ids = verify_addon_records(order, settlement, addon.addon_parts, people)
                settled_segments = {person.segment_id for person in people}
                if addon_ids != {(part.payment_id, part.segment_id) for part in addon.addon_parts if part.segment_id in settled_segments}:
                    return 'REVIEW', None
            final = bool(order.settled_at)
            if final and len(people) != len(snapshots):
                return 'REVIEW', None
            destinations = []
            addon_refunds = list(order.addon_refunds.select_related('rounding_transaction').order_by('pk')) if not is_points else []
            addon_rounding = sum(row.rounding_surplus for row in addon_refunds)
            if addon_rounding > facts[8]:
                return 'REVIEW', None
            for tx, amount, kind in ((settlement.inviter_transaction, facts[7], Transaction.TxType.INCOME),
                                     (settlement.store_transaction, facts[8] - addon_rounding, Transaction.TxType.SHOP_INCOME)):
                if not final or not amount:
                    if tx:
                        return 'REVIEW', None
                    continue
                if (not tx or tx.order_id != order.pk or tx.tx_type != kind or tx.status != Transaction.Status.SUCCESS
                        or tx.amount != amount or tx.reversal_of_id or tx.reversals.exists()
                        or tx.balance_after != tx.balance_before + tx.amount):
                    return 'REVIEW', None
                if kind == Transaction.TxType.SHOP_INCOME:
                    if not tx.wallet.user or tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME:
                        return 'REVIEW', None
                elif ((order.inviter_id and tx.wallet.user_id != order.inviter_id)
                      or (order.inviter_account_id and tx.wallet.account_id != order.inviter_account_id)):
                    return 'REVIEW', None
                elif tx.wallet.account_id:
                    from club_accounts.models import ClubAccount
                    if ClubAccount.objects.filter(pk=tx.wallet.account_id,
                                                  account_type=ClubAccount.AccountType.BOSS).exists():
                        from engagement.models import PointFundingLot
                        if not PointFundingLot.objects.filter(account_id=tx.wallet.account_id,
                                source_key=f'points-settlement-referral:{settlement.pk}',
                                kind=PointFundingLot.Kind.PREPAID, remaining__lte=tx.amount).exists():
                            return 'REVIEW', None
                destinations.append(tx.pk)
            for refund in addon_refunds:
                tx = refund.rounding_transaction
                if not final or not refund.rounding_surplus:
                    if tx:
                        return 'REVIEW', None
                    continue
                if (not tx or tx.order_id != order.pk or tx.tx_type != Transaction.TxType.SHOP_INCOME
                        or tx.status != Transaction.Status.SUCCESS or tx.amount != refund.rounding_surplus
                        or tx.reversal_of_id or tx.reversals.exists()
                        or tx.balance_after != tx.balance_before + tx.amount or not tx.wallet.user
                        or tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME):
                    return 'REVIEW', None
                destinations.append(tx.pk)
            actual = set(order.transactions.filter(status=Transaction.Status.SUCCESS).filter(
                Q(tx_type=Transaction.TxType.SHOP_INCOME) |
                Q(tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}')).values_list('pk', flat=True))
            if actual != set(destinations):
                return 'REVIEW', None
            mine = next((person for person in people if person.report_id == report.pk), None)
            return ('SETTLED', mine.settled_at) if mine else ('REVIEW' if final else 'WAITING', None)
    except (PointsFundingPlanError, SettlementError, DatabaseError, ValidationError, Order.DoesNotExist):
        return 'REVIEW', None


def verified_order_settlement_display(order):
    if not (settlement_enabled() or ordinary_settlement_enabled() or _formal_points_display_candidate(order)):
        from django.db import DatabaseError
        try:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                reports = list(ProviderReport.objects.filter(order_id=locked.pk))
                if not reports:
                    return _continued_ordinary_refund_display(locked.pk)[0]
                statuses = [_continued_ordinary_refund_display(locked.pk, report.pk)[0] for report in reports]
                if 'REVIEW' in statuses:
                    return 'REVIEW'
                if locked.settled_at:
                    return 'SETTLED' if len(reports) == locked.required_provider_count and all(
                        status == 'SETTLED' for status in statuses) else 'REVIEW'
                return 'WAITING'
        except (Order.DoesNotExist, DatabaseError):
            return 'REVIEW'
    reports = list(ProviderReport.objects.filter(order_id=order.pk))
    if not reports:
        return 'REVIEW' if order.settled_at else 'WAITING'
    statuses = [verified_person_settlement_display(report)[0] for report in reports]
    if 'REVIEW' in statuses:
        return 'REVIEW'
    if order.settled_at:
        return 'SETTLED' if len(reports) == order.required_provider_count and all(
            status == 'SETTLED' for status in statuses) else 'REVIEW'
    return 'WAITING'
