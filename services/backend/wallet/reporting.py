"""Read the same commission snapshots used when approving a provider report."""
from users.models import EscortProfile

from .models import ProviderReport, get_commission_rate


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


def report_financial_snapshot(report):
    approved = report.status == ProviderReport.Status.APPROVED
    if report.order_id:
        if not report_has_valid_order_share(report):
            return {'commission_type': None, 'commission_fixed': None,
                    'suggested_commission_rate': None, 'commission_source_label': '收益待核对',
                    'expected_income': None}
        share = report_order_share(report)
        source = share or report.order
        return {
            'commission_type': share.commission_type if share else 'PERCENT',
            'commission_fixed': share.commission_fixed if share else 0,
            'suggested_commission_rate': report.commission_rate if approved else source.commission_rate,
            'commission_source_label': '审核快照' if approved else '订单分账快照',
            'expected_income': report.payout_amount if approved else source.provider_income,
        }
    rate, label = legacy_report_commission(report)
    return {
        'commission_type': 'PERCENT',
        'commission_fixed': 0,
        'suggested_commission_rate': rate,
        'commission_source_label': label,
        'expected_income': report.payout_amount if approved else report.amount * (100 - rate) // 100,
    }
