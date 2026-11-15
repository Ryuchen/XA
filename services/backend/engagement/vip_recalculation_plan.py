"""Pure membership impact calculation; it does not apply a configuration.

The caller supplies the rules before the first change and the final rules.
That distinction matters when several changes are deferred while VIP is off.
An issuance plan is not proof that historical grants are complete.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TierRule:
    tier_id: int
    boss_type_id: int
    level: int
    threshold: int
    active: bool = True
    boss_type_active: bool = True


@dataclass(frozen=True)
class BossFact:
    account_id: int
    business_cycle: int
    boss_type_id: int | None
    net_consumption: int
    issued_tier_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class MembershipChange:
    account_id: int
    business_cycle: int
    net_consumption: int
    old_boss_type_id: int | None
    new_boss_type_id: int | None
    old_level: int
    new_level: int
    newly_eligible_tier_ids: tuple[int, ...]


def _validate_rules(rules):
    for field in ('tier_id', 'boss_type_id', 'level'):
        values = [getattr(rule, field) for rule in rules]
        if len(values) != len(set(values)):
            raise ValueError(f'VIP 配置存在重复的 {field}')
    if any(rule.level < 1 or rule.threshold < 0 for rule in rules):
        raise ValueError('VIP 等级或门槛无效')


def _qualified(rules, total):
    return sorted((rule for rule in rules if rule.active and rule.boss_type_active
                   and rule.threshold <= total), key=lambda rule: rule.level)


def plan_changes(before_rules, after_rules, bosses):
    """Return grade changes and candidate newly reached, unissued upgrades.

    ``before_rules`` for a deferred batch must be the rules at the beginning of
    the disabled period, not the rules already changed while disabled. The
    caller must independently verify issuance evidence and lock/recheck inputs
    before using this read-only plan to mutate accounts or issue benefits.
    """
    before, after, facts = list(before_rules), list(after_rules), list(bosses)
    _validate_rules(before); _validate_rules(after)
    ids = [boss.account_id for boss in facts]
    if len(ids) != len(set(ids)):
        raise ValueError('老板重算数据重复')
    old_types = {rule.boss_type_id: rule for rule in before}
    changes = []
    for boss in sorted(facts, key=lambda fact: fact.account_id):
        if boss.business_cycle < 1 or boss.net_consumption < 0:
            raise ValueError('老板业务周期或净消费无效')
        was = _qualified(before, boss.net_consumption)
        now = _qualified(after, boss.net_consumption)
        # A boss type that only becomes a VIP tier in the proposed rules is
        # still a non-VIP type before the change.
        current = old_types.get(boss.boss_type_id)
        # Non-VIP manually assigned grades must not disappear just because no
        # VIP threshold is met; a retired VIP, however, must be cleared.
        next_type = now[-1].boss_type_id if now else (None if current else boss.boss_type_id)
        before_ids = {rule.tier_id for rule in was}
        gained = {rule.tier_id for rule in now if rule.tier_id not in before_ids}
        if now and (current is None or now[-1].level > current.level):
            gained.update(rule.tier_id for rule in now if current is None or rule.level > current.level)
        # Holding this exact tier is not a newly reached upgrade, including
        # legacy accounts still pointing at a tier that was deactivated.
        if current:
            gained.discard(current.tier_id)
        gained.difference_update(boss.issued_tier_ids)
        reward_ids = tuple(rule.tier_id for rule in now if rule.tier_id in gained)
        old_level = current.level if current else 0
        new_level = now[-1].level if now else 0
        if next_type != boss.boss_type_id or old_level != new_level or reward_ids:
            changes.append(MembershipChange(boss.account_id, boss.business_cycle, boss.net_consumption,
                                            boss.boss_type_id, next_type, old_level, new_level, reward_ids))
    return changes


def plan_unknown_baseline(rules, bosses):
    """Recalculate grades while flagging all unverified eligible grants."""
    from dataclasses import replace

    rules, bosses = tuple(rules), tuple(bosses)
    planned = {item.account_id: item for item in plan_changes(rules, rules, bosses)}
    types = {rule.boss_type_id: rule for rule in rules}
    for boss in bosses:
        eligible = _qualified(rules, boss.net_consumption)
        candidates = tuple(rule.tier_id for rule in eligible if rule.tier_id not in boss.issued_tier_ids)
        if not candidates:
            continue
        if boss.account_id in planned:
            planned[boss.account_id] = replace(planned[boss.account_id], newly_eligible_tier_ids=candidates)
        else:
            current = types.get(boss.boss_type_id)
            level = current.level if current else 0
            planned[boss.account_id] = MembershipChange(boss.account_id, boss.business_cycle, boss.net_consumption,
                boss.boss_type_id, boss.boss_type_id, level, level, candidates)
    return [planned[key] for key in sorted(planned)]


def read_store_snapshot():
    """Read store-local facts for a preview; callers must recheck under locks.

    Issuance rows are evidence of a grant, not evidence that missing rows were
    never issued by a historical system. No writes or automatic issuance occur.
    """
    from club_accounts.models import ClubAccount
    from .models import MemberBenefitGrant, VipTier, VipUpgradeReview

    tiers = tuple(TierRule(row.pk, row.boss_type_id, row.level, row.recharge_threshold,
                           row.active, row.boss_type.is_active)
                  for row in VipTier.objects.select_related('boss_type').order_by('level'))
    accounts = list(ClubAccount.objects.filter(account_type='BOSS', license_membership_active=True)
                    .order_by('pk').values('pk', 'business_cycle', 'boss_type_id'))
    account_ids = [row['pk'] for row in accounts]
    if not account_ids:
        return tiers, ()
    # Use the same verified net-consumption source as live VIP updates.  In
    # addition to confirmed orders and eligible add-ons it includes platform-
    # reviewed UNKNOWN decisions, with their actual refund evidence checked.
    from .services import completed_consumption
    current_accounts = ClubAccount.objects.in_bulk(account_ids)
    from rest_framework.exceptions import ValidationError
    if (len(current_accounts) != len(accounts) or any(
            current_accounts[row['pk']].business_cycle != row['business_cycle']
            or current_accounts[row['pk']].boss_type_id != row['boss_type_id']
            or not current_accounts[row['pk']].license_membership_active for row in accounts)):
        raise ValidationError('老板会员状态已变化，请重新核对')
    totals = {(row['pk'], row['business_cycle']): completed_consumption(current_accounts[row['pk']])
              for row in accounts}
    cycles = {row['pk']: row['business_cycle'] for row in accounts}
    issued = {pk: set() for pk in account_ids}
    for grant in MemberBenefitGrant.objects.filter(account_id__in=account_ids, kind='UPGRADE'):
        prefix = f'vip:{grant.account_id}:{cycles[grant.account_id]}:'
        if grant.event_key.startswith(prefix) and grant.event_key.endswith(':upgrade'):
            issued[grant.account_id].add(grant.tier_id)
    for review in VipUpgradeReview.objects.filter(account_id__in=account_ids, status='RESOLVED', resolution='ALREADY_ISSUED'):
        if review.business_cycle == cycles[review.account_id]:
            issued[review.account_id].add(review.tier_id)
    bosses = tuple(BossFact(row['pk'], row['business_cycle'], row['boss_type_id'],
                            totals.get((row['pk'], row['business_cycle']), 0), frozenset(issued[row['pk']]))
                   for row in accounts)
    return tiers, bosses
