"""Review and atomically retire a VIP tier while preserving issued rights."""
import hashlib
import json

from django.core import signing
from django.db.models import F, Q
from rest_framework.exceptions import ValidationError
from stores.context import current_store
from stores.db import transaction

from club_accounts.models import ClubAccount, LegacyAccountMap
from console.models import AdminAuditLog
from coupons.models import Coupon, UserCoupon, VipMonthlyCouponGrant
from engagement.models import ClubOperationConfig, VipTier
from engagement.services import completed_consumption
from users.models import CustomUser
from .vip_threshold_change import _audit_actor, _audit_body

SALT = 'engagement.tier-retirement.v1'


def _store():
    store = current_store()
    if store is None:
        raise ValidationError('缺少门店上下文，请重新登录')
    return store.slug


def _facts(tier):
    accounts = list(ClubAccount.objects.filter(account_type='BOSS', boss_type_id=tier.boss_type_id)
                    .order_by('pk'))
    account_rows = [(account.pk, account.business_cycle, completed_consumption(account)) for account in accounts]
    vouchers = list(UserCoupon.objects.filter(account_id__in=[a.pk for a in accounts],
        business_cycle=F('account__business_cycle'), coupon__grant_key__startswith='vip-').order_by('pk')
        .values_list('pk', 'account_id', 'business_cycle', 'status', 'vip_terms_locked_by_downgrade',
            'coupon_id', 'expires_at', 'coupon__discount_rate', 'coupon__service_ids', 'coupon__valid_to'))
    grants = list(VipMonthlyCouponGrant.objects.filter(account_id__in=[a.pk for a in accounts])
                  .order_by('pk').values())
    tiers = list(VipTier.objects.order_by('pk').values_list('pk', 'boss_type_id', 'boss_type__is_active', 'level',
        'recharge_threshold', 'active', 'monthly_discount', 'season_discount', 'safe_box_service_ids'))
    operation = ClubOperationConfig.objects.filter(pk=1).first()
    if operation is None:
        raise ValidationError('本店会员配置尚未初始化，请先核对')
    from dataclasses import asdict
    from .vip_recalculation_plan import read_store_snapshot
    rules, bosses = read_store_snapshot()
    all_bosses = [{**asdict(boss), 'issued_tier_ids': sorted(boss.issued_tier_ids)} for boss in bosses]
    facts = {'store': _store(), 'tier': tier.pk, 'accounts': account_rows, 'vouchers': vouchers, 'grants': grants,
             'tiers': tiers, 'vip_enabled': operation.vip_enabled,
             'all_bosses': all_bosses, 'all_rules': [asdict(rule) for rule in rules]}

    digest = hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest()
    from dataclasses import replace
    from .vip_recalculation_plan import plan_changes
    try:
        plan = plan_changes(rules, tuple(replace(rule, active=False) if rule.tier_id == tier.pk else rule
                                         for rule in rules), bosses)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    return accounts, {'digest': digest, 'account_count': len(accounts), 'coupon_count': len(vouchers),
                      'affected_count': len(plan), 'upgrade_count': sum(p.new_level > p.old_level for p in plan),
                      'downgrade_count': sum(p.new_level < p.old_level for p in plan)}


def preview_retirement(tier):
    if not tier.active:
        raise ValidationError('该等级已经停用')
    _, data = _facts(tier)
    return {'account_count': data['account_count'], 'coupon_count': data['coupon_count'],
            'affected_count': data['affected_count'], 'upgrade_count': data['upgrade_count'],
            'downgrade_count': data['downgrade_count'],
            'token': signing.dumps({'store': _store(), 'tier': tier.pk, 'digest': data['digest']}, salt=SALT, compress=True)}


def retire_tier(tier, token, request, *, locked_account_ids=None):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('VIP 等级停用须在门店事务内执行')
    if not isinstance(token, str) or len(token) > 8192:
        raise ValidationError('停用核对信息无效或已过期，请重新核对')
    from users.models import BossType
    type_ids = list(VipTier.objects.values_list('boss_type_id', flat=True))
    list(BossType.objects.select_for_update().filter(pk__in=type_ids).order_by('pk').values_list('pk', flat=True))
    list(VipTier.objects.select_for_update().order_by('pk').values_list('pk', flat=True))
    coupon_ids = list(UserCoupon.objects.filter(coupon__grant_key__startswith='vip-')
                      .order_by('coupon_id').values_list('coupon_id', flat=True).distinct())
    list(Coupon.objects.select_for_update().filter(pk__in=coupon_ids).order_by('pk').values_list('pk', flat=True))
    list(UserCoupon.objects.select_for_update().filter(coupon_id__in=coupon_ids)
         .order_by('coupon_id', 'pk').values_list('pk', flat=True))
    try:
        expected = signing.loads(token, salt=SALT, max_age=300)
    except (signing.BadSignature, TypeError):
        raise ValidationError('停用核对信息无效或已过期，请重新核对') from None
    accounts, facts = _facts(tier)
    if expected != {'store': _store(), 'tier': tier.pk, 'digest': facts['digest']}:
        raise ValidationError('等级、老板或已发券已变化，请重新核对')
    from .vip_transition import freeze_existing_vip_terms, resume_guard_available as transition_guard_available
    if not transition_guard_available():
        raise ValidationError('本店会员权益规则不可用，请先联系平台核对')
    operation = ClubOperationConfig.objects.get(pk=1)
    if not operation.vip_enabled:
        from .vip_deferred import mark_deferred
        tier.active = False
        tier.save(update_fields=['active'])
        deferred = mark_deferred()
        AdminAuditLog.objects.create(**_audit_actor(request), method=request.method, path=request.path[:255],
            resource='vip-deferred-tier-retirement', object_id=str(tier.pk), status_code=200,
            request_body=_audit_body(request, {'epoch': deferred.epoch, 'baseline_known': deferred.baseline_rules is not None,
                          'affected_accounts': facts['account_count']}))
        return {'accounts': 0, 'deferred': True}
    from dataclasses import replace
    from .vip_recalculation_plan import read_store_snapshot, plan_changes
    from .vip_threshold_change import _pre_freeze, _apply_plan
    all_ids = list(ClubAccount.objects.filter(account_type='BOSS').order_by('pk').values_list('pk', flat=True))
    if locked_account_ids is None or all_ids != locked_account_ids:
        raise ValidationError('老板账号已变化，请重新核对')
    before_rules, bosses = read_store_snapshot()
    after_rules = tuple(replace(rule, active=False) if rule.tier_id == tier.pk else rule
                        for rule in before_rules)
    try:
        plan = plan_changes(before_rules, after_rules, bosses)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    _pre_freeze(plan)
    tier.active = False
    tier.save(update_fields=['active'])
    _apply_plan(plan, tier, request, operation='retirement')
    return {'accounts': len(plan), 'coupon_records': facts['coupon_count'], 'deferred': False}
