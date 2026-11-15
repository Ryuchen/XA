"""Preview and atomically apply a complete store-local VIP ordering."""
from dataclasses import replace

from django.core import signing
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import ClubOperationConfig, VipTier
from .vip_recalculation_plan import plan_changes, read_store_snapshot
from .vip_threshold_change import (_apply_plan, _audit_actor, _audit_body, _lock_inputs,
                                   _lock_vip_coupons, _snapshot, _store)

SALT = 'engagement.vip-reorder.v1'


def _mapping(value, rules):
    if not isinstance(value, list) or not value or len(value) > 32767:
        raise ValidationError('请提交全部等级的顺序映射')
    rows = {}
    for item in value:
        if (not isinstance(item, dict) or set(item) != {'id', 'level'}
                or type(item['id']) is not int or item['id'] <= 0
                or type(item['level']) is not int or not 1 <= item['level'] <= 32767
                or item['id'] in rows):
            raise ValidationError('等级顺序映射无效')
        rows[item['id']] = item['level']
    if set(rows) != {rule.tier_id for rule in rules} or len(set(rows.values())) != len(rows):
        raise ValidationError('请提交每个等级且顺序不能重复')
    return rows


def _facts(mapping):
    rules, bosses = read_store_snapshot()
    rows = _mapping(mapping, rules)
    after = tuple(replace(rule, level=rows[rule.tier_id]) for rule in rules)
    try:
        plan = plan_changes(rules, after, bosses)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    first = min(rules, key=lambda rule: rule.tier_id)
    _, digest, _ = _snapshot(first.tier_id, first.threshold)
    canonical = [{'id': pk, 'level': rows[pk]} for pk in sorted(rows)]
    return rules, bosses, plan, digest, canonical


def preview(mapping):
    rules, bosses, plan, digest, canonical = _facts(mapping)
    enabled = ClubOperationConfig.objects.get(pk=1).vip_enabled
    return {'token': signing.dumps({'store': _store(), 'mapping': canonical, 'digest': digest}, salt=SALT, compress=True),
            'account_count': len(bosses), 'affected_count': len(plan), 'deferred': not enabled,
            'candidate_upgrade_rewards': sum(len(item.newly_eligible_tier_ids) for item in plan)}


def apply(mapping, token, request, locked_accounts):
    from club_accounts.models import ClubAccount
    from console.models import AdminAuditLog
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('等级批量改序须在门店事务内执行')
    if not isinstance(token, str) or len(token) > 8192:
        raise ValidationError('请先核对等级顺序')
    try:
        expected = signing.loads(token, salt=SALT, max_age=300)
    except signing.BadSignature:
        raise ValidationError('核对信息无效或已过期，请重新核对') from None
    _lock_inputs()
    rules, bosses, plan, digest, canonical = _facts(mapping)
    if expected != {'store': _store(), 'mapping': canonical, 'digest': digest}:
        raise ValidationError('会员或等级数据已变化，请重新核对')
    if list(ClubAccount.objects.filter(account_type='BOSS').order_by('pk').values_list('pk', flat=True)) != locked_accounts:
        raise ValidationError('老板账号已变化，请重新核对')
    vip_enabled = ClubOperationConfig.objects.get(pk=1).vip_enabled
    if vip_enabled:
        for change in plan:
            if change.old_boss_type_id != change.new_boss_type_id:
                _lock_vip_coupons(ClubAccount.objects.get(pk=change.account_id), downgrade=True)
    desired = {row['id']: row['level'] for row in canonical}
    changed = [rule for rule in rules if desired[rule.tier_id] != rule.level]
    occupied = {rule.level for rule in rules} | set(desired.values())
    temporary = iter(number for number in range(32767, 0, -1) if number not in occupied)
    for rule in changed:
        try:
            number = next(temporary)
        except StopIteration:
            raise ValidationError('没有足够的临时等级顺序，请联系平台核对') from None
        tier = VipTier.objects.get(pk=rule.tier_id)
        benefits = dict(tier.benefits or {})
        if 1 <= rule.level <= 6 and 'issuance' not in benefits:
            benefits.setdefault('_issuance_template_level', rule.level)
        VipTier.objects.filter(pk=tier.pk).update(level=number, benefits=benefits)
    for rule in changed:
        VipTier.objects.filter(pk=rule.tier_id).update(level=desired[rule.tier_id])
    if not vip_enabled:
        from .vip_deferred import mark_deferred
        mark_deferred()
    elif changed:
        _apply_plan(plan, VipTier.objects.get(pk=changed[0].tier_id), request, operation='batch-reorder',
                    creation_data=canonical)
    AdminAuditLog.objects.create(**_audit_actor(request), method=request.method, path=request.path[:255],
        resource='vip-batch-reorder', object_id='', status_code=200,
        request_body=_audit_body(request, {'mapping': canonical, 'affected_count': len(plan), 'deferred': not vip_enabled}))
    return {'affected_count': len(plan), 'deferred': not vip_enabled}
