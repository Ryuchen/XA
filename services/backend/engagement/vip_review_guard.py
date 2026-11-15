"""Prevent automatic grants from bypassing unresolved historical reviews."""
from django.db.models import Q


def quota_floors_for_tier(tier):
    """Record which cumulative entitlement slots require historical review."""
    from .models import RewardItem, VipTier
    limits = {'TAG':[1, 1, 2, 2, 3, 5], 'FRIEND':[0, 0, 1, 2, 5, 10],
              'NAMING':[3, 6, 6, 6, 10, 12]}
    from .vip_policy import issuance_policy
    policy = issuance_policy(tier)
    template_level = policy['template_level']
    configured = {}
    for prior in VipTier.objects.filter(active=True, level__lte=tier.level).order_by('level'):
        for bundle in prior.upgrade_rewards:
            reward = RewardItem.objects.get(pk=bundle['reward'])
            family = reward.kind if reward.kind in ('TAG', 'NAMING') else (reward.vip_quota_kind or reward.code)
            if family != 'BIRTHDAY_GIFT':
                legacy_limit = limits[family][template_level - 1] if template_level and family in limits else 0
                configured[family] = max(configured.get(family, 0), legacy_limit, bundle['quantity'])
    return configured


def review_barriers(account, *, exclude_review_id=None):
    """Return blocked tiers and slots for this account's current cycle."""
    from .models import VipUpgradeReview
    rows = VipUpgradeReview.objects.filter(account=account, business_cycle=account.business_cycle).filter(
        Q(status='PENDING') | Q(status='RESOLVED', resolution='ALREADY_ISSUED'))
    if exclude_review_id is not None:
        rows = rows.exclude(pk=exclude_review_id)
    tiers, floors, unknown = set(), {}, False
    for row in rows:
        tiers.add(row.tier_id)
        snapshot = row.evidence.get('quota_floors')
        if not isinstance(snapshot, dict):
            unknown = True
            continue
        for family, amount in snapshot.items():
            if not isinstance(family, str) or type(amount) is not int or amount < 0:
                unknown = True
                continue
            floors[family] = max(floors.get(family, 0), amount)
    return tiers, floors, unknown
