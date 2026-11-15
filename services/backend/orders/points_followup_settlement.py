"""Isolated final settlement after a second agreed points-order refund."""
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction
from .models import (Order, OrderPointsRefundSettlement, OrderPointsRefundSettlementFunding,
                     OrderPointsRefundSettlementPerson, OrderProvider, OrderSegmentFinancialSnapshot,
                     PointsNegotiationPosting)
from .points_checkout_test import require_isolated_checkout
from .settlement import SettlementError


def enabled():
    from .points_business_access import permits
    if any(permits(phase=phase, kind=kind) for kind in ('service', 'addon')
           for phase in ('refund', 'report', 'settlement')):
        return True
    if not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING', False):
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    from .points_refunded_settlement import enabled as refunded_enabled
    return refunded_enabled()


def expected_report_payout(report, posting, share):
    """Preserve the payout approved on the appropriate side of the refund."""
    if not report.audited_at or not posting.termination_processed_at:
        raise ValidationError('后续退款报单审核时间缺失')
    if report.audited_at < posting.termination_processed_at:
        return share.original_income
    if report.audited_at > posting.termination_processed_at:
        return share.retained_income
    if share.original_income == share.retained_income:
        return share.retained_income
    raise ValidationError('报单与售后审核时间相同，须先核对')


def verified_report_payout(order, report, posting, share, snapshot):
    """Keep the report's audited amount at its actual refund boundary."""
    from .addon_settlement import has_paid_addons
    if not has_paid_addons(order):
        return expected_report_payout(report, posting, share)
    if not report.audited_at or not posting.termination_processed_at:
        raise ValidationError('后续退款报单审核时间缺失')
    terms = posting.proposal.points_terms
    if terms.prior_refund_id and report.audited_at >= posting.termination_processed_at:
        if report.audited_at == posting.termination_processed_at and share.original_income != share.retained_income:
            raise ValidationError('报单与售后审核时间相同，须先核对')
        return share.retained_income
    from .points_refunded_settlement import _report_amount
    base = (PointsNegotiationPosting.objects.get(points_refund_id=terms.prior_refund_id)
            if terms.prior_refund_id else posting)
    return _report_amount(base, snapshot, report)


def verify_followup_person_locked(order, revision, share, assignment):
    """Verify B's payout locally, without re-entering receipt verification.

    B's report was approved before this aftersale. Its audited payout remains
    unchanged; the separately agreed refund share proves the reduced credit.
    """
    from .services import missing_report_evidence
    settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=revision.posting).first()
    people = list(OrderPointsRefundSettlementPerson.objects.select_for_update().filter(settlement=settlement)
                  .select_related('report', 'income_transaction__wallet', 'segment')) if settlement else []
    if len(people) != 1:
        raise ValidationError('后续退款陪玩结算凭证缺失')
    person = people[0]
    addon_paid = sum(settlement.addon_settlements.filter(segment_id=person.segment_id).values_list('amount', flat=True))
    if (not order.settled_at or order.settled_at != settlement.finalized_at
            or person.settled_at != settlement.finalized_at
            or person.settled_at < revision.paid_person.settled_at
            or person.amount + addon_paid != revision.remaining_provider_units):
        raise ValidationError('后续退款陪玩结算时间或金额异常')
    return verify_person_locked(order, revision.posting, share, assignment, person,
                                earliest=revision.posting.termination_processed_at)


def verify_person_locked(order, posting, share, assignment, person, *, earliest):
    """Verify one independently released share without recursing through funding."""
    from .services import missing_report_evidence
    from .points_refunded_settlement import _deadline
    report = person.report
    snapshot = OrderSegmentFinancialSnapshot.objects.filter(segment=person.segment).first()
    from .points_refunded_settlement import _person_amount
    amount = _person_amount(posting, snapshot) if snapshot else None
    if (not snapshot or person.segment.attribution.order_id != order.pk
            or person.segment.provider_account_id != assignment.provider_account_id
            or share.account_id != assignment.provider_account_id or share.assignment_id != assignment.pk
            or person.amount != amount or assignment.settled_at != person.settled_at
            or person.settled_at > timezone.now() or not earliest or person.settled_at < earliest
            or not order.completed_at or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or person.settled_at < order.completed_at
            or person.settled_at < _deadline(order, posting, assignment.provider_account_id)
            or report.order_id != order.pk or report.provider_id != assignment.provider_id
            or report.provider_account_id != assignment.provider_account_id
            or report.status != ProviderReport.Status.APPROVED or not report.submitted_at or not report.audited_at
            or report.audited_at < report.submitted_at or report.audited_at > person.settled_at
            or report.submitted_at + timedelta(hours=order.service_freeze_hours) > person.settled_at
            or report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate
            or report.payout_amount != verified_report_payout(order, report, posting, share, snapshot)
            or missing_report_evidence(report)):
        raise ValidationError('后续退款陪玩报单或收益凭证不一致')
    credits = set(order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
        remark=f'订单收入：{order.order_no}', wallet__user_id=assignment.provider_id).values_list('pk', flat=True))
    income = person.income_transaction
    if person.amount:
        if (not income or income.order_id != order.pk or income.wallet.user_id != assignment.provider_id
                or income.wallet.account_id != assignment.provider_account_id or income.tx_type != Transaction.TxType.INCOME
                or income.status != Transaction.Status.SUCCESS or income.amount != person.amount or income.reversal_of_id
                or income.balance_after != income.balance_before + income.amount or income.reversals.exists()
                or credits != {income.pk}):
            raise ValidationError('后续退款陪玩收入流水不一致')
    elif income or credits:
        raise ValidationError('后续退款零收益不应有收入流水')
    from wallet.models import RefundDepositRecoveryPermit
    pending_recovery = False
    if income:
        from django.db.models import Q
        from wallet.models import IncomeHold, RefundDepositDeficit, RefundDepositIncomeApplication
        hold = IncomeHold.objects.select_related('release_transaction').filter(income_transaction=income).first()
        source = income
        application = RefundDepositIncomeApplication.objects.select_related('profile').filter(source_transaction=income).first()
        if not application and hold and hold.released_at and hold.release_transaction_id:
            source = hold.release_transaction
            if (source.wallet_id != income.wallet_id or source.order_id != income.order_id
                    or source.tx_type != 'EARNINGS_RELEASE' or source.status != 'SUCCESS'
                    or source.amount != hold.amount - hold.recovered_amount
                    or source.balance_after != source.balance_before + source.amount
                    or source.reversal_of_id or source.reversals.exists()):
                raise ValidationError('后续退款收益解冻流水异常')
            application = RefundDepositIncomeApplication.objects.select_related('profile').filter(
                source_transaction=source).first()
        if application:
            from wallet.refund_deposit import _verify_application
            if application.profile.user_id != assignment.provider_id or application.profile.account_id != share.account_id:
                raise ValidationError('后续退款收益补押金账户不一致')
            try:
                _verify_application(application, source=source, profile=application.profile)
            except ValueError as exc:
                raise ValidationError('后续退款收益补押金流水异常') from exc
        elif RefundDepositDeficit.objects.filter(account_id=share.account_id, created_at__lte=income.created_at).filter(
                Q(closed_at__isnull=True) | Q(closed_at__gte=income.created_at)).exists():
            hold = IncomeHold.objects.filter(income_transaction=income, wallet_id=income.wallet_id,
                released_at__isnull=True, cancelled_at__isnull=True, amount=income.amount,
                deposit_amount=0, recovered_amount=0, priority_opening_evidence__isnull=False).first()
            if not hold:
                raise ValidationError('后续退款收益缺少押金补差或待处理冻结凭证')
            pending_recovery = True
    if RefundDepositRecoveryPermit.objects.filter(account_id=assignment.provider_account_id,
            order=order, closed_at__isnull=True).exists() and not pending_recovery:
        raise ValidationError('后续退款押金补差机会尚未关闭')
    zero = RefundDepositRecoveryPermit.objects.filter(zero_points_refund_person=person).first()
    if zero and (person.amount or zero.account_id != assignment.provider_account_id
            or zero.profile.user_id != assignment.provider_id or zero.order_id != order.pk
            or zero.close_reason != 'ZERO_POINTS_REF_SETTLED' or not zero.closed_at
            or zero.income_application_id or zero.zero_income_settlement_id or zero.zero_negotiated_share_id):
        raise ValidationError('后续退款零收益补差凭证异常')
    return person


def verify_followup_settlement_locked(order):
    from .points_business_access import permits
    if not any(permits(order, phase=phase, kind=kind) for kind in ('service', 'addon')
               for phase in ('refund', 'report', 'settlement')):
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('后续退款结算须在门店事务内核对')
    from .points_followup_funding import verify_followup_funding
    from .points_refunded_settlement import _check_destinations, _funding_rows
    funding = verify_followup_funding(order.pk)
    posting = PointsNegotiationPosting.objects.select_related('followup_funding_revision').get(
        points_refund__redemption__order=order, proposal__points_terms__prior_refund__isnull=False)
    revision = posting.followup_funding_revision
    from .points_followup_addon_version import EffectiveRevision, later_runs
    versions = later_runs(order, revision)
    if versions:
        revision = EffectiveRevision(revision, versions[-1].points_followup_snapshot)
    settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=posting).first()
    from .points_unpaid_followup_settlement import _addon_data, _facts, _recorded
    first = PointsNegotiationPosting.objects.get(points_refund_id=posting.proposal.points_terms.prior_refund_id)
    addon, parts = _addon_data(order, revision, first)
    facts = _facts(revision, addon)
    if (not settlement or not funding.fully_funded or not settlement.finalized_at
            or order.settled_at != settlement.finalized_at
            or _recorded(settlement) != facts
            or list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
                != _funding_rows(order)):
        raise ValidationError('后续退款最终结算或整单补资凭证不一致')
    if not revision.paid_person_id:
        from .points_unpaid_followup_settlement import verify_progress_locked
        from .points_service_participation import service_snapshots
        snapshots = service_snapshots(order)
        assignments = {item.provider_account_id: item for item in OrderProvider.objects.select_for_update().filter(
            order=order, provider_account_id__in=[snap.segment.provider_account_id for snap in snapshots])}
        verify_progress_locked(order, revision, first, posting, snapshots, assignments)
        return settlement
    share = posting.shares.get()
    assignment = OrderProvider.objects.select_for_update().get(pk=share.assignment_id, order=order)
    verify_followup_person_locked(order, revision, share, assignment)
    if parts:
        from .addon_settlement import verify_addon_records
        prior_parts = {(part.payment_id, part.segment_id) for part in parts
                       if part.segment_id == revision.paid_person.segment_id}
        remaining_parts = {(part.payment_id, part.segment_id) for part in parts
                           if part.segment_id != revision.paid_person.segment_id}
        prior = verify_addon_records(order, revision.prior_settlement, parts, [revision.paid_person])
        final_person = settlement.persons.get()
        current = verify_addon_records(order, settlement, parts, [final_person])
        if prior != prior_parts or current != remaining_parts:
            raise ValidationError('后续退款加购收入归属待核对')
    plan = SimpleNamespace(inviter_income_units=revision.inviter_units, store_income_units=revision.store_units)
    try:
        _check_destinations(order, settlement, plan)
    except SettlementError as exc:
        raise ValidationError('后续退款门店或推荐收益凭证不一致') from exc
    return settlement


def verified_person_display(report):
    """Return a provider's status only after checking both refund histories."""
    if not enabled() or not report.order_id:
        return 'REVIEW', None
    from django.db import DatabaseError
    from .points_followup_funding import verify_followup_funding
    from .points_funding_plan import PointsFundingPlanError
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=report.order_id)
            from .financial_lock import lock_order_money
            lock_order_money()
            verify_followup_funding(order.pk)
            posting = PointsNegotiationPosting.objects.select_related('followup_funding_revision__paid_person').get(
                points_refund__redemption__order=order, proposal__points_terms__prior_refund__isnull=False)
            revision = posting.followup_funding_revision
            from .points_followup_addon_version import EffectiveRevision, later_runs
            versions = later_runs(order, revision)
            if versions:
                revision = EffectiveRevision(revision, versions[-1].points_followup_snapshot)
            if not revision.paid_person_id:
                from .points_unpaid_followup_settlement import verify_progress_locked
                first = PointsNegotiationPosting.objects.get(points_refund_id=posting.proposal.points_terms.prior_refund_id)
                from .points_service_participation import service_snapshots
                snapshots = service_snapshots(order)
                assignments = {item.provider_account_id: item for item in OrderProvider.objects.filter(
                    order=order, provider_account_id__in=[snap.segment.provider_account_id for snap in snapshots])}
                _, people = verify_progress_locked(order, revision, first, posting, snapshots, assignments)
                if (report.provider_account_id not in assignments
                        or assignments[report.provider_account_id].provider_id != report.provider_id):
                    return 'REVIEW', None
                mine = next((person for person in people if person.report_id == report.pk), None)
                return ('SETTLED', mine.settled_at) if mine else ('REVIEW' if order.settled_at else 'WAITING', None)
            if order.settled_at:
                settlement = verify_followup_settlement_locked(order)
                final_person = settlement.persons.get()
                for person in (revision.paid_person, final_person):
                    if (person.report_id == report.pk and person.report.provider_id == report.provider_id
                            and person.report.provider_account_id == report.provider_account_id):
                        return 'SETTLED', person.settled_at
                return 'REVIEW', None
            if revision.paid_person.report_id == report.pk:
                person = revision.paid_person
                if (person.report.provider_id == report.provider_id
                        and person.report.provider_account_id == report.provider_account_id):
                    return 'SETTLED', person.settled_at
                return 'REVIEW', None
            share = posting.shares.get()
            if (share.account_id != report.provider_account_id or share.assignment.provider_id != report.provider_id
                    or report.order_id != order.pk):
                return 'REVIEW', None
            return 'WAITING', None
    except (ValidationError, PointsFundingPlanError, SettlementError, DatabaseError, Order.DoesNotExist,
            PointsNegotiationPosting.DoesNotExist, PointsNegotiationPosting.MultipleObjectsReturned,
            OrderPointsRefundSettlementPerson.DoesNotExist):
        return 'REVIEW', None


def settle_followup_order(order, *, operator=None, operator_account=None, trace_id=None):
    if not enabled() or not transaction.get_connection().in_atomic_block:
        raise SettlementError('后续积分退款收益结算尚未开放')
    with transaction.atomic():
        return _settle_followup_order(order, trace_id=trace_id)


def _settle_followup_order(order, *, trace_id=None):
    from .financial_lock import lock_order_money
    from .points_followup_funding import verify_followup_funding
    from .points_funding_plan import PointsFundingPlanError
    from .points_refunded_settlement import _funding_rows
    from .services import _credit_wallet, missing_report_evidence, refresh_escort_status
    from wallet.income import hold_provider_income
    from wallet.models import RefundDepositDeficit, RefundDepositRecoveryPermit, Wallet
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.Status.COMPLETED or not order.completed_at:
        return []
    lock_order_money()
    try:
        funding = verify_followup_funding(order.pk)
    except (PointsFundingPlanError, ValidationError) as exc:
        raise SettlementError('后续退款的结案或整单补资凭证待核对') from exc
    if not funding.fully_funded:
        return []
    followup = PointsNegotiationPosting.objects.select_related('followup_funding_revision').get(
        points_refund__redemption__order=order, proposal__points_terms__prior_refund__isnull=False)
    if not followup.followup_funding_revision.paid_person_id:
        from .points_unpaid_followup_settlement import settle_unpaid_followup_locked
        try:
            return settle_unpaid_followup_locked(order, followup.followup_funding_revision, trace_id=trace_id)
        except (ValidationError, PointsFundingPlanError) as exc:
            raise SettlementError('未结算双陪后续退款的资金或收益凭证待核对') from exc
    if order.settled_at:
        try:
            settlement = verify_followup_settlement_locked(order)
        except ValidationError as exc:
            raise SettlementError('后续退款结算凭证待核对') from exc
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
        raise SettlementError('后续退款已有会员实销或消费积分记录')
    posting = PointsNegotiationPosting.objects.select_related('followup_funding_revision').get(
        points_refund__redemption__order=order, proposal__points_terms__prior_refund__isnull=False)
    revision, share = posting.followup_funding_revision, posting.shares.get()
    from .points_followup_addon_version import EffectiveRevision, later_runs
    versions = later_runs(order, revision)
    if versions:
        revision = EffectiveRevision(revision, versions[-1].points_followup_snapshot)
    from .points_business_access import permits
    formal = any(permits(order, phase='settlement', kind=kind) for kind in ('service', 'addon'))
    if revision.addon_snapshot and not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_ADDON_PAID_SETTLEMENT_TESTING', False):
        raise SettlementError('已有陪玩结算的加购后续退款尚未开放')
    from .points_unpaid_followup_settlement import _addon_data, _facts
    first = PointsNegotiationPosting.objects.get(points_refund_id=posting.proposal.points_terms.prior_refund_id)
    addon, parts = _addon_data(order, revision, first)
    if OrderPointsRefundSettlement.objects.filter(posting=posting).exists():
        raise SettlementError('后续退款存在未完成的结算凭证')
    assignment = OrderProvider.objects.select_for_update().get(pk=share.assignment_id, order=order)
    from .points_service_participation import service_snapshots
    snapshot = next((item for item in service_snapshots(order)
                     if item.segment.provider_account_id == share.account_id), None)
    if not snapshot:
        raise SettlementError('后续退款陪玩履约快照缺失')
    remaining_parts = [part for part in parts if part.segment_id == snapshot.segment_id]
    report = ProviderReport.objects.select_for_update().filter(order=order, provider_account_id=share.account_id).first()
    redemption = posting.points_refund.redemption
    wallet = Wallet.objects.select_for_update().filter(pk=redemption.payment.wallet_id).first()
    from club_accounts.models import ClubAccount
    buyer = ClubAccount.objects.select_for_update().filter(pk=order.customer_account_id).first()
    if (not wallet or not wallet.is_active or wallet.account_id != order.customer_account_id
            or wallet.user_id != order.customer_id or not buyer or not buyer.is_active
            or buyer.account_type != ClubAccount.AccountType.BOSS or buyer.business_cycle != order.business_cycle
            or (order.customer_id and not LegacyAccountMap.objects.filter(
                account_id=buyer.pk, legacy_user_id=order.customer_id).exists()) or not assignment.provider_id
            or not LegacyAccountMap.objects.filter(account_id=assignment.provider_account_id,
                                                    legacy_user_id=assignment.provider_id).exists()):
        raise SettlementError('后续退款老板钱包或陪玩身份待核对')
    if (assignment.settled_at or order.transactions.filter(tx_type=Transaction.TxType.INCOME,
            status=Transaction.Status.SUCCESS, remark=f'订单收入：{order.order_no}',
            wallet__user_id=assignment.provider_id).exists()):
        raise SettlementError('后续退款陪玩已有收入但缺少结算凭证')
    if order.aftersales.filter(status='OPEN').exists():
        return []
    now = timezone.now()
    if (order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100):
        raise SettlementError('后续退款结算时限或押金快照待核对')
    if (now < order.completed_at + timedelta(hours=order.service_aftersale_hours)
            or not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at
            or report.submitted_at + timedelta(hours=order.service_freeze_hours) > now):
        return []
    try:
        approved_payout = verified_report_payout(order, report, posting, share, snapshot)
    except ValidationError as exc:
        raise SettlementError('后续退款报单审核时间待核对') from exc
    from .points_refunded_settlement import _person_amount
    remaining_main = _person_amount(posting, snapshot)
    if (report.provider_id != assignment.provider_id or not report.audited_at or report.audited_at < report.submitted_at
            or report.audited_at > now or report.amount != snapshot.incoming_basis
            or report.commission_rate != snapshot.commission_rate
            or report.payout_amount != approved_payout
            or remaining_main + sum(part.provider_income_units for part in remaining_parts)
                != revision.remaining_provider_units or missing_report_evidence(report)):
        raise SettlementError('后续退款报单或陪玩收益待核对')
    profile = EscortProfile.objects.select_for_update().filter(user_id=assignment.provider_id,
                                                                account_id=assignment.provider_account_id).first()
    if not profile:
        raise SettlementError('后续退款陪玩档案待核对')
    from wallet.refund_deposit import enabled as deficit_enabled
    from wallet.refund_recovery_order import tracking_enabled
    has_gap = RefundDepositDeficit.objects.filter(profile=profile, remaining__gt=0).exists()
    has_permit = RefundDepositRecoveryPermit.objects.filter(profile=profile, closed_at__isnull=True).exists()
    if ((has_gap or has_permit) and not deficit_enabled()) or (has_permit and not tracking_enabled()):
        raise SettlementError('后续退款押金缺口或补差机会尚未接通')
    facts = _facts(revision, addon)
    settlement = OrderPointsRefundSettlement.objects.create(posting=posting, paid_units=facts[0],
        principal_units=facts[1], gift_units=facts[2], compensation_units=facts[3], recovered_units=facts[4],
        funding_units=facts[5], provider_units=facts[6], inviter_units=facts[7], store_units=facts[8],
        addon_paid_units=facts[9], addon_principal_units=facts[10], addon_gift_units=facts[11],
        addon_compensation_units=facts[12], addon_recovered_units=facts[13], addon_provider_units=facts[14])
    OrderPointsRefundSettlementFunding.objects.bulk_create([
        OrderPointsRefundSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount)
        for pk, amount in _funding_rows(order)])
    from common.logging_utils import new_trace_id
    trace_id = trace_id or new_trace_id()
    income = _credit_wallet(order=order, amount=remaining_main, tx_type=Transaction.TxType.INCOME,
        remark=f'订单收入：{order.order_no}', event='order.points_followup_settle.provider', trace_id=trace_id,
        user_id=assignment.provider_id, account_id=assignment.provider_account_id, log_after_commit=True)
    hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                         apply_without_hold=True)
    person = OrderPointsRefundSettlementPerson.objects.create(settlement=settlement, segment=snapshot.segment,
        report=report, income_transaction=income, amount=remaining_main, settled_at=now)
    if remaining_parts:
        from .addon_settlement import settle_person_addons
        settle_person_addons(order, settlement, remaining_parts, snapshot=snapshot, assignment=assignment,
                             report=report, profile=profile, now=now, trace_id=trace_id)
    assignment.settled_at = now; assignment.save(update_fields=['settled_at'])
    if not remaining_main:
        from wallet.refund_recovery_order import close_for_zero_points_refund
        close_for_zero_points_refund(profile, person)
    if order.transactions.filter(status=Transaction.Status.SUCCESS, reversal_of__isnull=True,
            tx_type=Transaction.TxType.SHOP_INCOME).exists() or order.transactions.filter(
                status=Transaction.Status.SUCCESS, reversal_of__isnull=True, tx_type=Transaction.TxType.INCOME,
                remark=f'推荐分佣：{order.order_no}').exists():
        raise SettlementError('后续退款门店或推荐收益已有流水')
    if order.inviter_id or order.inviter_account_id:
        settlement.inviter_transaction = _credit_wallet(order=order, amount=revision.inviter_units,
            tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}',
            event='order.points_followup_settle.inviter', trace_id=trace_id,
            user_id=order.inviter_id, account_id=order.inviter_account_id, log_after_commit=True)
        tx = settlement.inviter_transaction
        if tx and tx.wallet.account_id:
            if ClubAccount.objects.filter(pk=tx.wallet.account_id, account_type=ClubAccount.AccountType.BOSS).exists():
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
    order.settled_at = now; order.save(update_fields=['settled_at'])
    verify_followup_settlement_locked(order)
    EscortProfile.objects.filter(pk=profile.pk).update(completed_order_count=F('completed_order_count') + 1)
    refresh_escort_status(provider_ids=[assignment.provider_id], account_ids=[assignment.provider_account_id],
                          exclude_order_ids=[order.pk])
    from .notifier import notify_report_update
    from engagement.confirmation import notify_committed_order
    transaction.on_commit(lambda: notify_report_update(report))
    notify_committed_order(order.pk)
    return [assignment.provider_id]
