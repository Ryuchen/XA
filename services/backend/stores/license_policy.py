"""Store-license lifecycle rules; per-tier feature permissions are separate."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class LicenseTier:
    code: str
    annual_price_fen: int
    boss_limit: Optional[int]
    provider_limit: Optional[int]
    order_limit: Optional[int] = None


TIERS = {
    'BASIC': LicenseTier('BASIC', 288000, 500, 500),
    'PRO': LicenseTier('PRO', 388000, 1500, 2000),
    'ULTRA': LicenseTier('ULTRA', 588000, None, None),
}

# These are entitlement defaults, not proof that a particular backend feature
# is implemented. Tier settings can change only for these non-core groups.
FEATURE_DEFAULTS = {
    'PROMOTIONS': ('OFF', 'FULL', 'FULL'),
    'VIP_GROWTH': ('READ_ONLY', 'FULL', 'FULL'),
    'POINTS_SHOP': ('OFF', 'FULL', 'FULL'),
    'LOTTERY': ('OFF', 'OFF', 'FULL'),
    'CHECKIN': ('OFF', 'FULL', 'FULL'),
    'ACHIEVEMENTS_RANKINGS': ('OFF', 'OFF', 'FULL'),
    'GIFTS': ('OFF', 'FULL', 'FULL'),
    'PROVIDER_PASS': ('FULL', 'FULL', 'FULL'),
    'CONTENT_OPERATIONS': ('OFF', 'FULL', 'FULL'),
    'PAYROLL': ('OFF', 'BASIC', 'FULL'),
    'OPERATING_COST_ROI': ('OFF', 'BASIC', 'FULL'),
    'AUDIT_LOG_VIEW': ('RECENT', 'FULL', 'FULL'),
    'ADVANCED_ANALYTICS': ('OFF', 'FULL', 'FULL'),
    'WHITE_LABEL_API': ('OFF', 'OFF', 'FULL'),
}
CORE_ACTIONS = frozenset({
    'LOGIN', 'SERVICE_DIRECTORY', 'ORDER', 'DISPATCH', 'RESERVATION', 'REPORT', 'SETTLEMENT',
    'AFTERSALE', 'REFUND', 'WALLET', 'WITHDRAWAL', 'EVALUATION', 'SUPPORT', 'NOTIFICATIONS',
    'BASIC_PROFILE', 'PERSONAL_STATISTICS', 'BASIC_ANNOUNCEMENT', 'EXISTING_COUPON_USE',
    'EXISTING_POINTS_USE', 'EXISTING_BENEFIT_FULFILLMENT',
})
FEATURE_MODES = {feature: frozenset({'OFF', 'READ_ONLY', 'FULL'}) for feature in FEATURE_DEFAULTS}
FEATURE_MODES['PAYROLL'] |= {'BASIC'}
FEATURE_MODES['OPERATING_COST_ROI'] |= {'BASIC'}
FEATURE_MODES['AUDIT_LOG_VIEW'] |= {'RECENT'}


@dataclass(frozen=True)
class LicenseGrant:
    store_slug: str
    tier: str
    starts_at: datetime
    ends_at: datetime
    grace_seconds: int
    revoked: bool = False


@dataclass(frozen=True)
class LicenseDecision:
    status: str
    tier: Optional[LicenseTier]
    allow_existing_business: bool
    allow_existing_benefits: bool
    allow_new_accounts: bool
    allow_new_benefits: bool
    can_open_store: bool


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def evaluate(grant, *, store_slug, now):
    """Evaluate a verified grant or trusted cached snapshot against current time.

    A broken/missing snapshot must not enable registration or new benefits.
    Existing order, balance, aftersale, settlement and earned-benefit use remain
    available. Allowing new benefits here means only that the license *state*
    does not block issuance; the configured tier feature must also permit it.
    A caller must authenticate the snapshot and retain store context.
    """
    if not _aware(now):
        raise ValueError('当前时间必须带时区')
    tier = TIERS.get(grant.tier) if isinstance(grant, LicenseGrant) and isinstance(grant.tier, str) else None
    valid = (tier and isinstance(store_slug, str) and store_slug and grant.store_slug == store_slug
             and _aware(grant.starts_at) and _aware(grant.ends_at)
             and grant.starts_at < grant.ends_at and type(grant.grace_seconds) is int
             and grant.grace_seconds >= 0 and type(grant.revoked) is bool)
    if not valid or grant.revoked:
        return LicenseDecision('UNLICENSED', None, True, True, False, False, False)
    if now < grant.starts_at:
        return LicenseDecision('NOT_YET_ACTIVE', tier, True, True, False, False, False)
    if now < grant.ends_at:
        return LicenseDecision('ACTIVE', tier, True, True, True, True, True)
    elapsed = now - grant.ends_at
    grace_days, grace_seconds = divmod(grant.grace_seconds, 86400)
    if (elapsed.days, elapsed.seconds, elapsed.microseconds) < (grace_days, grace_seconds, 0):
        return LicenseDecision('GRACE', tier, True, True, True, True, False)
    return LicenseDecision('EXPIRED', tier, True, True, False, False, False)


def can_add_account(decision, account_type, *, used):
    """Apply the quota to a caller-supplied, transactionally verified count."""
    if account_type not in ('BOSS', 'PROVIDER') or type(used) is not int or used < 0:
        raise ValueError('账号类型或已用配额无效')
    if not decision.allow_new_accounts or decision.tier is None:
        return False
    limit = decision.tier.boss_limit if account_type == 'BOSS' else decision.tier.provider_limit
    return limit is None or used < limit


def evaluate_scheduled(current, scheduled, *, store_slug, now):
    """Keep the current grant until the scheduled grant's effective instant."""
    if not _aware(now):
        raise ValueError('当前时间必须带时区')
    if scheduled is None:
        return evaluate(current, store_slug=store_slug, now=now)
    if (not isinstance(scheduled, LicenseGrant) or scheduled.store_slug != store_slug
            or not _aware(scheduled.starts_at)):
        return evaluate(None, store_slug=store_slug, now=now)
    if now < scheduled.starts_at:
        return evaluate(current if current is not None else scheduled, store_slug=store_slug, now=now)
    return evaluate(scheduled, store_slug=store_slug, now=now)


def validate_feature_overrides(overrides):
    if not isinstance(overrides, dict) or any(
            key not in FEATURE_DEFAULTS or not isinstance(value, str) or value not in FEATURE_MODES[key]
            for key, value in overrides.items()):
        raise ValueError('档位功能配置无效')
    return dict(overrides)


def feature_mode(decision, feature, *, overrides=None):
    """Resolve license entitlement; callers still enforce role and business rules."""
    if not isinstance(feature, str):
        raise ValueError('未知授权功能')
    if feature in CORE_ACTIONS:
        return 'FULL'
    if feature not in FEATURE_DEFAULTS:
        raise ValueError('未知授权功能')
    if decision.tier is None:
        return 'OFF'
    index = ('BASIC', 'PRO', 'ULTRA').index(decision.tier.code)
    mode = FEATURE_DEFAULTS[feature][index]
    if overrides is not None:
        mode = validate_feature_overrides(overrides).get(feature, mode)
    if not decision.allow_new_benefits:
        return 'OFF' if mode == 'OFF' else 'READ_ONLY'
    return mode
