"""Current-read validation for VIP reward JSON references."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def referenced_tiers(rows, reward_id):
    upgrade, birthday = [], []
    for tier_id, bundles, benefits in rows:
        if isinstance(bundles, list) and any(isinstance(item, dict)
                and str(item.get('reward')) == str(reward_id) for item in bundles):
            upgrade.append(tier_id)
        issuance = benefits.get('issuance') if isinstance(benefits, dict) else None
        if isinstance(issuance, dict) and str(issuance.get('birthday_reward_id')) == str(reward_id):
            birthday.append(tier_id)
    return upgrade, birthday


def lock_and_validate_upgrade_rewards(values):
    """Caller holds the store VIP configuration lock and an atomic block."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('VIP 奖励引用校验必须在本店事务中执行')
    from .models import RewardItem
    bundles = values.get('upgrade_rewards', [])
    upgrade_ids = {int(item['reward']) for item in bundles}
    benefits = values.get('benefits')
    issuance = benefits.get('issuance') if isinstance(benefits, dict) else None
    birthday_id = issuance.get('birthday_reward_id') if isinstance(issuance, dict) else None
    ids = sorted(upgrade_ids | ({birthday_id} if birthday_id else set()))
    rows = {row.pk: row for row in RewardItem.objects.select_for_update().filter(pk__in=ids).order_by('pk')}
    for reward_id in upgrade_ids:
        row = rows.get(reward_id)
        if not row or row.in_shop or row.kind not in ('SERVICE', 'NAMING', 'TAG') or row.vip_quota_kind == 'BIRTHDAY_GIFT':
            raise ValidationError('VIP 升级权益奖励已变化或不可用，请刷新后重新选择')
    if birthday_id:
        row = rows.get(birthday_id)
        if not row or row.in_shop or not row.is_active or row.vip_quota_kind != 'BIRTHDAY_GIFT':
            raise ValidationError('VIP 生日奖励已变化或不可用，请刷新后重新选择')
