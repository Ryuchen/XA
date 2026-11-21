"""Read the same commission snapshots used when approving a provider report."""
from users.models import EscortProfile
from django.conf import settings

from .models import ProviderReport, get_commission_rate


def formal_points_report_fields(report):
    """Return (verified share, funding shortfall), or None outside the formal path."""
    from orders.points_business_access import formal_service_read_candidate, formal_service_read_scope_locked
    from orders.points_checkout_test import report_enabled
    if (not report.order_id or report_enabled()
            or not formal_service_read_candidate(report.order, phase='report')):
        return None

    from django.db import DatabaseError
    from rest_framework.exceptions import ValidationError
    from orders.models import Order
    from orders.points_funding_evidence import verify_order_funding
    from orders.points_funding_plan import PointsFundingPlanError
    from orders.settlement import SettlementError
    from stores.db import transaction
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=report.order_id)
            with formal_service_read_scope_locked(order, phase='report') as active:
                if not active:
                    return None
                locked = ProviderReport.objects.select_for_update().get(
                    pk=report.pk, order=order, provider_id=report.provider_id,
                    provider_account_id=report.provider_account_id, status=report.status,
                    amount=report.amount, commission_rate=report.commission_rate, payout_amount=report.payout_amount)
                locked.order = order
                snapshot = points_report_snapshot(locked)
                if not snapshot:
                    return None, None
                if locked.status != ProviderReport.Status.APPROVED:
                    return snapshot, False
                if order.status != Order.Status.COMPLETED:
                    return None, None
                try:
                    funding = verify_order_funding(order.pk, allow_finalized=True)
                except (PointsFundingPlanError, SettlementError, ValidationError):
                    return snapshot, None
                return snapshot, not funding.fully_funded
    except (PointsFundingPlanError, SettlementError, ValidationError, DatabaseError,
            Order.DoesNotExist, ProviderReport.DoesNotExist):
        return None, None


def points_report_snapshot(report):
    """Return the report share proven by the current capability."""
    if not report.order_id:
        return None
    from orders.points_checkout_test import report_enabled
    if not report_enabled():
        return None
    from orders.points_funding_plan import PointsFundingPlanError
    from orders.points_report_test import verified_report_snapshot
    try:
        snapshot = verified_report_snapshot(report.order, report.provider_account_id, report.provider_id,
                                            allow_finalized=True)
    except PointsFundingPlanError:
        return None
    expected_payout = snapshot.income_at_entry
    if report.status == ProviderReport.Status.APPROVED and report.order.refund_records.exists():
        from rest_framework.exceptions import ValidationError
        from orders.models import PointsNegotiationPosting
        from orders.negotiation_settlement import report_income
        negotiated, expected_payout = report_income(report, display_only=True)
        if not negotiated or expected_payout is None:
            return None
        if report.order.refund_records.count() == 2:
            posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=report.order,
                proposal__points_terms__prior_refund__isnull=False).first()
            share = posting.shares.filter(account_id=report.provider_account_id).first() if posting else None
            if share:
                from orders.points_followup_settlement import expected_report_payout
                try:
                    expected_payout = expected_report_payout(report, posting, share)
                except ValidationError:
                    return None
    if (report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate
            or (report.status == ProviderReport.Status.APPROVED and report.payout_amount != expected_payout)):
        return None
    return snapshot


def subsidy_agreement_evidence_valid(order):
    """Do not display adjusted subsidy as fact if its agreement is damaged."""
    from orders.models import OrderSegmentSettlementManifest
    if OrderSegmentSettlementManifest.objects.filter(order=order, order__platform_subsidy__isnull=False).exists():
        from django.core.exceptions import ObjectDoesNotExist
        from django.db import DatabaseError
        from stores.db import transaction
        from orders.models import Order
        from orders.settlement import SettlementError
        from orders.subsidy_segment_settlement import verify_locked
        try:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(order=locked)
                verify_locked(locked, manifest, finalized=bool(locked.settled_at))
        except (SettlementError, ObjectDoesNotExist, DatabaseError, TypeError, ValueError, KeyError, AttributeError):
            return False
    from orders.subsidy_execution import table_available, verify_execution
    if not table_available():
        return not getattr(settings, 'VIP_SAFE_BOX_NEGOTIATION_ENABLED', False)
    from orders.models import SubsidyAgreementExecution
    execution = SubsidyAgreementExecution.objects.filter(order=order).select_related('order', 'proposal__case').first()
    if not execution:
        return True
    from django.core.exceptions import ObjectDoesNotExist
    from django.db import DatabaseError
    from rest_framework.exceptions import ValidationError
    from stores.db import transaction
    from orders.models import Order
    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            from orders.financial_lock import lock_order_money
            lock_order_money()
            execution = SubsidyAgreementExecution.objects.select_for_update().select_related(
                'order', 'proposal__case').get(pk=execution.pk, order=locked)
            verify_execution(execution)
        return True
    except (ValidationError, ObjectDoesNotExist, DatabaseError, TypeError, ValueError):
        return False


def _assignment_subsidy(share, *, order=None):
    if not share:
        return False, None
    if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
        from orders.models import OrderPlatformSubsidyShare
        cached = getattr(order or share.order, '_display_subsidies', None)
        funded = cached.get(share.pk) if cached is not None else OrderPlatformSubsidyShare.objects.filter(assignment=share).first()
        if funded:
            return True, funded.amount if subsidy_agreement_evidence_valid(order or share.order) else None
    return False, None


def assignment_platform_subsidy(share, *, order=None):
    return _assignment_subsidy(share, order=order)[1]


def report_subsidy_snapshot(report):
    """Return (exists, approved amount, paid time), or an unverified amount."""
    if not getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False) or not report.order_id:
        return False, None, None
    share = report_order_share(report)
    if not share:
        return False, None, None
    from orders.models import OrderPlatformSubsidyShare
    cached = getattr(report.order, '_display_subsidies', None)
    funded = cached.get(share.pk) if cached is not None else OrderPlatformSubsidyShare.objects.filter(
        assignment=share).select_related('subsidy').first()
    if not funded:
        return False, None, None
    if not subsidy_agreement_evidence_valid(report.order):
        return True, None, None
    if funded.subsidy.order_id != report.order_id or share.provider_income:
        return True, None, None
    income, expense = funded.income_transaction, funded.expense_transaction
    if not funded.paid_at:
        return (True, funded.amount, None) if not income and not expense and not share.settled_at else (True, None, None)
    if not share.settled_at:
        return True, None, None
    if not funded.amount:
        return (True, 0, funded.paid_at) if not income and not expense else (True, None, None)
    if (not income or not expense or income.order_id != report.order_id or expense.order_id != report.order_id
            or income.tx_type != 'INCOME' or income.status != 'SUCCESS' or income.amount != funded.amount
            or expense.tx_type != 'SUBSIDY_EXPENSE' or expense.status != 'SUCCESS' or expense.amount != -funded.amount
            or income.wallet.user_id != report.provider_id or income.wallet.account_id != report.provider_account_id
            or not expense.wallet.user or expense.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
            or income.balance_after != income.balance_before + income.amount
            or expense.balance_after != expense.balance_before + expense.amount
            or income.reversal_of_id or expense.reversal_of_id or income.reversals.exists() or expense.reversals.exists()):
        return True, None, None
    return True, funded.amount, funded.paid_at


def assignment_expected_income(share, *, order=None):
    if not share:
        return 0
    present, subsidy = _assignment_subsidy(share, order=order)
    return None if present and subsidy is None else share.provider_income + (subsidy or 0)


def report_platform_funding_insufficient(report, context):
    """Identify a ready subsidy report waiting specifically for platform funds."""
    if report.order_id:
        from orders.points_service_payment import display_has_points
        from orders.points_checkout_test import ordinary_settlement_order
        if display_has_points(report.order) or ordinary_settlement_order(report.order):
            from orders.points_checkout_test import ordinary_settlement_enabled, settlement_enabled
            if not (settlement_enabled() or (ordinary_settlement_order(report.order)
                                              and ordinary_settlement_enabled())):
                return None
            if report.status != ProviderReport.Status.APPROVED:
                return False
            from django.db import DatabaseError
            from rest_framework.exceptions import ValidationError
            from orders.points_funding_evidence import verify_order_funding
            from orders.points_funding_plan import PointsFundingPlanError
            from orders.settlement import SettlementError
            try:
                from orders.negotiation_followup_accounting import marked_followup_id
                from orders.models import NegotiationFollowupLink
                from orders.ordinary_addon_followup_settlement import is_followup
                followup_id = marked_followup_id(report.order)
                link = NegotiationFollowupLink.objects.filter(pk=followup_id, order_id=report.order_id).first() if followup_id else None
                if link and is_followup(link):
                    if not getattr(settings, 'ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING', False):
                        return None
                    from stores.db import transaction
                    from orders.models import Order
                    from orders.ordinary_addon_followup_settlement import funding_status_locked
                    with transaction.atomic():
                        order = Order.objects.select_for_update().get(pk=report.order_id)
                        _, funding = funding_status_locked(order, link)
                    return not funding.fully_funded
                if report.order.refund_records.count() == 2:
                    from orders.points_followup_settlement import enabled as followup_enabled
                    from orders.points_followup_funding import verify_followup_funding
                    if followup_enabled():
                        return not verify_followup_funding(report.order_id).fully_funded
                return not verify_order_funding(report.order_id, allow_finalized=True).fully_funded
            except (PointsFundingPlanError, SettlementError, DatabaseError, ValidationError):
                return None
    if not getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False) or not report.order_id or report.status != 'APPROVED':
        return False
    order = report.order
    if order.settled_at or not report_has_valid_order_share(report):
        return False
    share = report_order_share(report)
    has_subsidy, amount, paid_at = report_subsidy_snapshot(report)
    if not has_subsidy or amount is None or paid_at:
        return False
    from orders.subsidies import _share_ready
    from django.utils import timezone
    if not _share_ready(order, share, now=timezone.now()):
        return False
    from orders.models import OrderPlatformSubsidyShare
    from django.db.models import Sum
    needed = OrderPlatformSubsidyShare.objects.filter(subsidy__order=order, paid_at__isnull=True).aggregate(total=Sum('amount'))['total'] or 0
    if not needed:
        return False
    if '_subsidy_available' not in context:
        from .models import Wallet
        from orders.subsidies import subsidy_funds
        platform = Wallet.objects.filter(user__username=settings.PLATFORM_SYSTEM_USERNAME).first()
        context['_subsidy_available'] = subsidy_funds(platform)['available'] if platform else None
    available = context['_subsidy_available']
    return None if available is None else available < needed


def report_order_share(report):
    if not report.order_id:
        return None
    rows = list(report.order.providers.all())
    for row in rows:
        if row.provider_id != report.provider_id:
            continue
        if row.provider_account_id and report.provider_account_id and row.provider_account_id != report.provider_account_id:
            continue
        if report.order.required_provider_count == 2 and (not row.provider_account_id or not report.provider_account_id):
            continue
        return row
    return None


def report_has_valid_order_share(report):
    if report.order_id:
        from orders.points_service_payment import display_has_points
        if display_has_points(report.order):
            return points_report_snapshot(report) is not None
    if report.provider_account_id:
        if hasattr(report, '_display_report_identity'):
            valid_identity = report._display_report_identity
        else:
            from club_accounts.models import LegacyAccountMap
            valid_identity = LegacyAccountMap.objects.filter(
                legacy_user_id=report.provider_id, account_id=report.provider_account_id).exists()
        if not valid_identity:
            return False
    if not report.order_id:
        return True
    rows = list(report.order.providers.all())
    if rows:
        share = report_order_share(report)
        if share is None:
            return False
        if report.status == ProviderReport.Status.APPROVED:
            return report.payout_amount == share.provider_income
        return True
    # Old single-provider orders may not have an assignment row.
    return bool(report.order.required_provider_count == 1 and report.order.provider_id == report.provider_id
                and not (report.order.provider_account_id and report.provider_account_id
                         and report.order.provider_account_id != report.provider_account_id))


def legacy_report_commission(report):
    """Preserve the existing approval priority, including an approved zero rate."""
    if report.status == ProviderReport.Status.APPROVED:
        return report.commission_rate, '审核快照'
    if report.commission_rate:
        return report.commission_rate, '报单填写'
    try:
        profile = report.provider.escort_profile
    except EscortProfile.DoesNotExist:
        profile = None
    level = getattr(profile, 'level', None)
    if level is not None and level.is_active:
        return level.commission_rate, f'等级抽成（{level.name}）'
    return get_commission_rate(), '全局默认'


def report_financial_snapshot(report, *, formal_fields=None):
    approved = report.status == ProviderReport.Status.APPROVED
    if report.order_id:
        from orders.points_service_payment import display_has_points
        if display_has_points(report.order):
            snapshot = formal_fields[0] if formal_fields is not None else points_report_snapshot(report)
            if snapshot:
                income = snapshot.income_at_entry
                if formal_fields is None and report.order.refund_records.exists():
                    from orders.negotiation_settlement import report_income
                    negotiated, retained = report_income(report, display_only=True)
                    if negotiated:
                        income = retained
                return {'commission_type': snapshot.commission_type, 'commission_fixed': snapshot.commission_fixed,
                        'suggested_commission_rate': snapshot.commission_rate, 'commission_source_label': '积分订单分成快照',
                        'expected_income': income, 'platform_subsidy_income': None}
            return {'commission_type': None, 'commission_fixed': None, 'suggested_commission_rate': None,
                    'commission_source_label': '积分订单收益待核对' if formal_fields is not None else '积分订单收益待接通',
                    'expected_income': None, 'platform_subsidy_income': None}
        from orders.segment_financial_capture import transfer_income_pending
        if transfer_income_pending(report.order, report.provider_account_id):
            return {'commission_type': None, 'commission_fixed': None,
                    'suggested_commission_rate': None, 'commission_source_label': '转单收益待核对',
                    'expected_income': None, 'platform_subsidy_income': None}
        if not report_has_valid_order_share(report):
            return {'commission_type': None, 'commission_fixed': None,
                    'suggested_commission_rate': None, 'commission_source_label': '收益待核对',
                    'expected_income': None, 'platform_subsidy_income': None}
        share = report_order_share(report)
        source = share or report.order
        from orders.negotiation_settlement import report_income
        negotiated, negotiated_income = report_income(report, display_only=True)
        return {
            'commission_type': share.commission_type if share else 'PERCENT',
            'commission_fixed': share.commission_fixed if share else 0,
            'suggested_commission_rate': report.commission_rate if approved else source.commission_rate,
            'commission_source_label': '审核快照' if approved else '订单分账快照',
            'expected_income': negotiated_income if negotiated else assignment_expected_income(share, order=report.order) if share else report.payout_amount if approved else source.provider_income,
            'platform_subsidy_income': assignment_platform_subsidy(share, order=report.order),
        }
    rate, label = legacy_report_commission(report)
    return {
        'commission_type': 'PERCENT',
        'commission_fixed': 0,
        'suggested_commission_rate': rate,
        'commission_source_label': label,
        'expected_income': report.payout_amount if approved else report.amount * (100 - rate) // 100,
        'platform_subsidy_income': None,
    }
