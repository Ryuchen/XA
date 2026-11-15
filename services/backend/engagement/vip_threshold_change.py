"""Opt-in, transactional VIP threshold recalculation for an enabled store."""
import hashlib
import json
from dataclasses import asdict, replace

from django.core import signing
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction

from .vip_recalculation_plan import TierRule, plan_changes, plan_unknown_baseline, read_store_snapshot

SALT = 'engagement.vip-threshold-recalculation.v1'
NEW_TIER_ID = -1


def _audit_actor(request):
    platform = getattr(request, 'platform_setup_actor', None)
    if platform is not None:
        return {'operator': None, 'operator_account': None,
                'operator_name': f'平台超级管理员：{platform.username}'[:150]}
    return {'operator': request.legacy_user, 'operator_account': request.account,
            'operator_name': request.account.username[:150]}


def _audit_body(request, body):
    platform = getattr(request, 'platform_setup_actor', None)
    if platform is not None:
        return {**body, 'actor_kind': 'platform', 'platform_account_id': str(platform.pk),
                'operation_id': request.platform_setup_operation_id}
    return body


def _birthday_reward_id(benefits):
    issuance = benefits.get('issuance') if isinstance(benefits, dict) else None
    value = issuance.get('birthday_reward_id') if isinstance(issuance, dict) else None
    return value if type(value) is int and value > 0 else None


def _store():
    store = current_store()
    if store is None:
        raise ValidationError('缺少门店上下文，请重新登录')
    return store.slug


def creation_spec(validated_data):
    """Normalize the proposed tier, including model defaults, for signing."""
    from .models import VipTier
    candidate = VipTier(**validated_data)
    boss_type = candidate.boss_type
    return {'boss_type_id': boss_type.pk, 'boss_type_name': boss_type.name,
            'boss_type_discount': boss_type.discount_rate, 'boss_type_active': boss_type.is_active,
            'level': candidate.level, 'threshold': candidate.recharge_threshold, 'active': candidate.active,
            'benefits': candidate.benefits, 'upgrade_rewards': candidate.upgrade_rewards,
            'companion_service_ids': candidate.companion_service_ids, 'safe_box_service_ids': candidate.safe_box_service_ids,
            'monthly_discount': candidate.monthly_discount, 'monthly_coupon_count': candidate.monthly_coupon_count,
            'season_discount': candidate.season_discount, 'points_rate': candidate.points_rate}


def _snapshot(tier_id, threshold, *, reactivate=False, new_tier=None, resume=False, new_level=None):
    from coupons.models import UserCoupon, VipMonthlyCouponGrant
    from .models import ClubOperationConfig, RewardItem, VipDeferredRecalculation, VipTier, VipUpgradeReview
    rules, bosses = read_store_snapshot()
    deferred = VipDeferredRecalculation.objects.filter(pk=1, is_open=True).values(
        'epoch', 'baseline_rules', 'pending', 'opened_at').first()
    baseline = deferred['baseline_rules'] if deferred else None
    if resume:
        try:
            plan = (plan_changes(tuple(TierRule(**item) for item in baseline), rules, bosses)
                    if baseline is not None else plan_unknown_baseline(rules, bosses))
        except (TypeError, ValueError, KeyError) as error:
            raise ValidationError('VIP 停用前快照不完整，请先核对') from error
    elif new_tier is None:
        if not any(rule.tier_id == tier_id for rule in rules):
            raise ValidationError('VIP 等级不存在')
        after = tuple(replace(rule, threshold=threshold, level=new_level if new_level is not None else rule.level,
                              active=True if reactivate else rule.active)
                      if rule.tier_id == tier_id else rule for rule in rules)
    else:
        after = (*rules, TierRule(NEW_TIER_ID, new_tier['boss_type_id'], new_tier['level'], threshold,
                                  new_tier['active'], new_tier['boss_type_active']))
    if not resume:
        try:
            plan = plan_changes(rules, after, bosses)
        except ValueError as error:
            raise ValidationError(str(error)) from None
    tiers = list(VipTier.objects.order_by('pk').values('pk', 'boss_type__name', 'boss_type__discount_rate',
        'benefits', 'upgrade_rewards', 'companion_service_ids', 'safe_box_service_ids', 'monthly_discount',
        'monthly_coupon_count', 'season_discount', 'points_rate'))
    reward_ids = {item['reward'] for tier in tiers for item in tier['upgrade_rewards']}
    reward_ids.update(value for tier in tiers if (value := _birthday_reward_id(tier['benefits'])))
    if new_tier is not None:
        reward_ids.update(item['reward'] for item in new_tier['upgrade_rewards'])
        if reward_id := _birthday_reward_id(new_tier['benefits']):
            reward_ids.add(reward_id)
    rewards = list(RewardItem.objects.filter(pk__in=reward_ids).order_by('pk').values(
        'pk', 'name', 'kind', 'vip_quota_kind', 'duration_days', 'duration_months', 'highlighted', 'is_active', 'in_shop'))
    ids = [boss.account_id for boss in bosses]
    coupons = list(UserCoupon.objects.filter(account_id__in=ids, coupon__grant_key__startswith='vip-')
        .order_by('pk').values('pk', 'account_id', 'business_cycle', 'status', 'expires_at', 'order_id', 'restore_on_refund',
            'vip_terms_locked_by_downgrade', 'coupon_id', 'coupon__grant_key', 'coupon__discount_rate',
            'coupon__service_ids', 'coupon__valid_to', 'coupon__is_active', 'coupon__scope', 'coupon__scope_variant',
            'coupon__discount_type', 'coupon__threshold', 'coupon__amount', 'coupon__max_discount', 'coupon__limit_units'))
    grants = list(VipMonthlyCouponGrant.objects.filter(account_id__in=ids).order_by('pk').values())
    reviews = list(VipUpgradeReview.objects.filter(account_id__in=ids).order_by('pk').values())
    operation = ClubOperationConfig.objects.filter(pk=1).values_list('vip_enabled', flat=True).first()
    facts = {'store': _store(), 'tier': tier_id, 'threshold': threshold, 'reactivate': reactivate, 'new_tier': new_tier,
             'resume': resume, 'new_level': new_level, 'vip_enabled': operation, 'deferred': deferred, 'tiers': tiers, 'rewards': rewards,
             'coupons': coupons, 'monthly_grants': grants, 'reviews': reviews,
             'rules': [asdict(rule) for rule in rules],
             'bosses': [{**asdict(boss), 'issued_tier_ids': sorted(boss.issued_tier_ids)} for boss in bosses]}
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return plan, digest, len(bosses)


def preview(tier, threshold, *, reactivate=False, new_level=None):
    plan, digest, account_count = _snapshot(tier.pk, threshold, reactivate=reactivate, new_level=new_level)
    token_data = {'store': _store(), 'tier': tier.pk, 'threshold': threshold,
                  'reactivate': reactivate, 'digest': digest}
    if new_level is not None:
        token_data['new_level'] = new_level
    return {'token': signing.dumps(token_data, salt=SALT, compress=True),
            'account_count': account_count, 'affected_count': len(plan),
            'upgrade_count': sum(item.new_level > item.old_level for item in plan),
            'downgrade_count': sum(item.new_level < item.old_level for item in plan),
            'candidate_upgrade_rewards': sum(len(item.newly_eligible_tier_ids) for item in plan)}


def preview_creation(validated_data):
    spec = creation_spec(validated_data)
    if not spec['active'] or not spec['boss_type_active']:
        raise ValidationError('请选择启用中的老板分级和 VIP 等级')
    plan, digest, count = _snapshot(NEW_TIER_ID, spec['threshold'], new_tier=spec)
    return {'token': signing.dumps({'store': _store(), 'action': 'create', 'digest': digest}, salt=SALT, compress=True),
            'account_count': count, 'affected_count': len(plan),
            'upgrade_count': sum(item.new_level > item.old_level for item in plan),
            'downgrade_count': sum(item.new_level < item.old_level for item in plan),
            'candidate_upgrade_rewards': sum(len(item.newly_eligible_tier_ids) for item in plan)}


def preview_resume():
    from .models import ClubOperationConfig, VipDeferredRecalculation, VipUpgradeReview
    if ClubOperationConfig.objects.get(pk=1).vip_enabled:
        raise ValidationError('VIP 已启用，请刷新后重试')
    row = VipDeferredRecalculation.objects.filter(pk=1, is_open=True).first()
    known = bool(row and row.baseline_rules is not None)
    plan, digest, count = _snapshot(None, None, resume=True)
    candidates = sum(len(item.newly_eligible_tier_ids) for item in plan)
    pending = set(VipUpgradeReview.objects.filter(status='PENDING').values_list('account_id', 'tier_id', 'business_cycle'))
    if not known:
        pending.update((item.account_id, tier_id, item.business_cycle) for item in plan
                       for tier_id in item.newly_eligible_tier_ids)
    return {'token': signing.dumps({'store': _store(), 'action': 'resume', 'digest': digest}, salt=SALT, compress=True),
            'baseline_known': known, 'account_count': count, 'affected_count': len(plan),
            'upgrade_count': sum(item.new_level > item.old_level for item in plan),
            'downgrade_count': sum(item.new_level < item.old_level for item in plan),
            'candidate_upgrade_rewards': candidates if known else 0,
            'manual_review_count': len(pending)}


def lock_all_boss_wallets():
    """Acquire wallet locks before the shared configuration lock."""
    from club_accounts.models import ClubAccount
    from wallet.services import get_wallet
    ids = list(ClubAccount.objects.filter(account_type='BOSS').order_by('pk').values_list('pk', flat=True))
    for account_id in ids:
        get_wallet(account_id=account_id, for_update=True)
    return ids


def _lock_vip_coupons(account, *, downgrade, vip_enabled=True):
    from coupons.models import Coupon, UserCoupon
    from .vip_transition import freeze_existing_vip_terms
    kinds = ('month', 'safe') if downgrade else ('month',)
    freeze_existing_vip_terms(vip_enabled=vip_enabled, account_id=account.pk, kinds=kinds)
    retained = ('upgrade', 'month', 'safe', 'birthday') if downgrade else ('month',)
    keys = Q()
    for kind in retained:
        keys |= Q(coupon__grant_key__startswith=f'vip-{kind}:{account.pk}:{account.business_cycle}:')
    issued = UserCoupon.objects.filter(keys, account=account, business_cycle=account.business_cycle)
    coupon_ids = list(issued.order_by('coupon_id').values_list('coupon_id', flat=True).distinct())
    list(Coupon.objects.select_for_update().filter(pk__in=coupon_ids).order_by('pk').values_list('pk', flat=True))
    locked = list(issued.select_for_update().order_by('pk').values_list('pk', flat=True))
    for row_id, coupon_id in issued.values_list('pk', 'coupon_id'):
        if UserCoupon.objects.filter(coupon_id=coupon_id).exclude(pk=row_id).exists():
            raise ValidationError('存在多人共用或重复领取的 VIP 专属券，请先核对')
    issued.filter(pk__in=locked).update(vip_terms_locked_by_downgrade=True)


def _issue_new_rewards(account, tier_ids, *, exclude_review_id=None, preserve_existing_terms=False):
    """Top up configured new-tier rights without changing existing claims."""
    from stores.license_benefits import LicenseDecisionUnavailable, LicenseFeatureDenied, check_new_benefits

    def require_growth():
        outcome, _ = check_new_benefits('VIP_GROWTH')
        if outcome == 'UNKNOWN':
            raise LicenseDecisionUnavailable()
        if outcome != 'ALLOW':
            raise LicenseFeatureDenied('门店授权暂不允许发放新的 VIP 权益，请联系平台')

    if tier_ids:
        require_growth()
    from coupons.benefits import issue
    from .models import MemberBenefitGrant, MemberTag, VipTier
    from .vip_review_guard import review_barriers
    blocked_tiers, quota_floors, unknown_quota = review_barriers(account, exclude_review_id=exclude_review_id)
    tiers = list(VipTier.objects.filter(pk__in=tier_ids).select_related('boss_type').order_by('level'))
    for tier in tiers:
        if tier.pk in blocked_tiers:
            continue
        grant_key = f'vip:{account.pk}:{account.business_cycle}:{tier.pk}:upgrade'
        if not MemberBenefitGrant.objects.filter(event_key=grant_key).exists():
            require_growth()
        grant, created = MemberBenefitGrant.objects.get_or_create(
            event_key=grant_key,
            defaults={'account': account, 'tier': tier, 'name': f'{tier.boss_type.name} 升级权益',
                      'kind': 'UPGRADE', 'details': tier.benefits})
        if grant.account_id != account.pk or grant.tier_id != tier.pk:
            raise ValidationError('升级权益历史记录与等级不一致，请先核对')
        if created:
            from .vip_policy import issuance_policy
            policy = issuance_policy(tier)
            MemberTag.objects.get_or_create(event_key=f'vip:{account.pk}:{account.business_cycle}:{tier.pk}:tag',
                defaults={'account': account, 'title': tier.boss_type.name,
                          'priority': tier.recharge_threshold, 'highlighted': policy['highlighted']})
            if policy['companion_quantity']:
                issue(account, f'vip-upgrade:{account.pk}:{account.business_cycle}:{tier.pk}:companion',
                      name=f'{tier.boss_type.name} 陪玩赠券', rate=0, scope='COMPANION',
                      variant=policy['companion_variant'], quantity=policy['companion_quantity'], limit_units=1,
                      service_ids=tier.companion_service_ids or [])
    if tiers:
        # An individual historical review authorizes only its own tier. A
        # normal recalculation instead uses the final current tier. Both use
        # the ordinary cumulative quota rules and the same review barriers.
        final_tier = tiers[-1] if exclude_review_id is not None else VipTier.objects.filter(
            boss_type_id=account.boss_type_id, active=True, boss_type__is_active=True).first()
        if final_tier is None:
            raise ValidationError('重算后的会员等级不可用，请重新核对')
        from .services import sync_vip_entitlements
        sync_vip_entitlements(account, final_tier, quota_floors=quota_floors,
                              unknown_quota=unknown_quota,
                              preserve_existing_terms=preserve_existing_terms or exclude_review_id is not None)


def _lock_inputs(*, extra_boss_type_ids=(), extra_reward_ids=()):
    from coupons.models import Coupon, UserCoupon
    from users.models import BossType
    from .models import RewardItem, VipTier
    type_ids = set(VipTier.objects.values_list('boss_type_id', flat=True)) | set(extra_boss_type_ids)
    list(BossType.objects.select_for_update().filter(pk__in=type_ids).order_by('pk').values_list('pk', flat=True))
    list(VipTier.objects.select_for_update().order_by('pk').values_list('pk', flat=True))
    reward_ids = {item['reward'] for bundles in VipTier.objects.values_list('upgrade_rewards', flat=True)
                  for item in bundles} | set(extra_reward_ids)
    reward_ids.update(value for benefits in VipTier.objects.values_list('benefits', flat=True)
                      if (value := _birthday_reward_id(benefits)))
    list(RewardItem.objects.select_for_update().filter(pk__in=reward_ids).order_by('pk').values_list('pk', flat=True))
    coupon_ids = list(UserCoupon.objects.filter(coupon__grant_key__startswith='vip-')
                      .order_by('coupon_id').values_list('coupon_id', flat=True).distinct())
    list(Coupon.objects.select_for_update().filter(pk__in=coupon_ids).order_by('pk').values_list('pk', flat=True))
    list(UserCoupon.objects.select_for_update().filter(coupon_id__in=coupon_ids)
         .order_by('coupon_id', 'pk').values_list('pk', flat=True))


def _pre_freeze(plan, *, reactivated_boss_type=None, vip_enabled=True):
    from club_accounts.models import ClubAccount
    preserved = set()
    if reactivated_boss_type is not None:
        for account in ClubAccount.objects.filter(account_type='BOSS', license_membership_active=True, boss_type_id=reactivated_boss_type).order_by('pk'):
            _lock_vip_coupons(account, downgrade=True, vip_enabled=vip_enabled)
            preserved.add(account.pk)
    for change in plan:
        if (change.old_boss_type_id != change.new_boss_type_id
                and change.account_id not in preserved):
            account = ClubAccount.objects.get(pk=change.account_id)
            if 0 < change.old_level < change.new_level:
                from coupons.monthly_upgrade import freeze_current_month
                freeze_current_month(account, vip_enabled=vip_enabled)
            _lock_vip_coupons(account, downgrade=change.new_level < change.old_level, vip_enabled=vip_enabled)


def _apply_plan(plan, tier, request, *, operation, previous_threshold=None, creation_data=None, previous_level=None):
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from console.models import AdminAuditLog
    from users.models import CustomUser
    from .models import VipTier
    from .services import broadcast
    for change in plan:
        account = ClubAccount.objects.select_for_update().get(pk=change.account_id)
        if (account.account_type != 'BOSS' or not account.license_membership_active
                or (account.business_cycle, account.boss_type_id) != (change.business_cycle, change.old_boss_type_id)):
            raise ValidationError('老板等级已变化，请重新核对')
        if change.old_boss_type_id != change.new_boss_type_id:
            ClubAccount.objects.filter(pk=account.pk).update(boss_type_id=change.new_boss_type_id)
            legacy = LegacyAccountMap.objects.filter(account_id=account.pk).values_list('legacy_user_id', flat=True).first()
            if legacy:
                CustomUser.objects.filter(pk=legacy).update(boss_type_id=change.new_boss_type_id)
            account.boss_type_id = change.new_boss_type_id
        new_ids = tuple(tier.pk if item == NEW_TIER_ID else item for item in change.newly_eligible_tier_ids)
        _issue_new_rewards(account, new_ids, preserve_existing_terms=True)
        if change.new_level > change.old_level:
            from coupons.benefits import grant_periodic
            top = VipTier.objects.get(boss_type_id=change.new_boss_type_id)
            grant_periodic(account)
            broadcast('UPGRADE', f'vip:{account.pk}:{account.business_cycle}:tier-{top.pk}',
                      account=account, level=top.boss_type.name)
        AdminAuditLog.objects.create(**_audit_actor(request), method=request.method, path=request.path[:255],
            resource=f'vip-{operation}-recalculate', object_id=str(account.pk), status_code=200,
            request_body=_audit_body(request, {'tier_id': tier.pk, 'from_boss_type': change.old_boss_type_id,
                          'to_boss_type': change.new_boss_type_id, 'net_consumption': change.net_consumption,
                          'business_cycle': change.business_cycle, 'new_tier_ids': list(new_ids)}))
    resource = {'threshold': 'vip-threshold-change', 'reactivation': 'vip-tier-reactivation',
                'creation': 'vip-tier-creation', 'reorder': 'vip-tier-reorder',
                'retirement': 'vip-tier-retirement', 'batch-reorder': 'vip-batch-reorder-summary'}[operation]
    AdminAuditLog.objects.create(**_audit_actor(request), method=request.method, path=request.path[:255],
        resource=resource, object_id=str(tier.pk), status_code=200,
        request_body=_audit_body(request, {'previous_threshold': previous_threshold, 'threshold': tier.recharge_threshold,
                      'previous_level': previous_level, 'level': tier.level,
                      'affected_count': len(plan), **({'configuration': creation_data} if creation_data is not None else {})}))


def apply(serializer, token, request, locked_account_ids, *, reactivate=False, renumber=False):
    """Caller holds all boss wallet locks, then the store config lock."""
    from club_accounts.models import ClubAccount
    from .models import ClubOperationConfig
    from console.models import AdminAuditLog
    from .vip_transition import resume_guard_available as transition_guard_available

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('VIP 门槛重算必须在门店事务内执行')
    if not transition_guard_available():
        raise ValidationError('会员权益保护尚未就绪，不能调整门槛')
    vip_enabled = ClubOperationConfig.objects.get(pk=1).vip_enabled
    if not isinstance(token, str) or len(token) > 8192:
        raise ValidationError('请先重新核对门槛变更影响')
    try:
        expected = signing.loads(token, salt=SALT, max_age=300)
    except signing.BadSignature:
        raise ValidationError('核对信息无效或已过期，请重新核对') from None
    _lock_inputs()
    threshold = serializer.validated_data.get('recharge_threshold', serializer.instance.recharge_threshold)
    new_level = serializer.validated_data['level'] if renumber else None
    plan, digest, _ = _snapshot(serializer.instance.pk, threshold, reactivate=reactivate, new_level=new_level)
    expected_data = {'store': _store(), 'tier': serializer.instance.pk, 'threshold': threshold,
                     'reactivate': reactivate, 'digest': digest}
    if renumber:
        expected_data['new_level'] = new_level
    if expected != expected_data:
        raise ValidationError('会员或等级数据已变化，请重新核对')
    current_ids = list(ClubAccount.objects.filter(account_type='BOSS').order_by('pk').values_list('pk', flat=True))
    if current_ids != locked_account_ids:
        raise ValidationError('老板账号已变化，请重新核对')
    previous_level = serializer.instance.level
    if renumber and 1 <= previous_level <= 6:
        benefits = dict(serializer.instance.benefits or {})
        if 'issuance' not in benefits:
            benefits.setdefault('_issuance_template_level', previous_level)
            serializer.validated_data['benefits'] = benefits
    if not vip_enabled:
        from .vip_deferred import mark_deferred
        previous_threshold = serializer.instance.recharge_threshold
        previous_active = serializer.instance.active
        tier = serializer.save()
        deferred = mark_deferred()
        AdminAuditLog.objects.create(**_audit_actor(request), method=request.method, path=request.path[:255],
            resource='vip-deferred-tier-change', object_id=str(tier.pk), status_code=200,
            request_body=_audit_body(request, {'epoch': deferred.epoch, 'baseline_known': deferred.baseline_rules is not None,
                          'previous_threshold': previous_threshold, 'threshold': tier.recharge_threshold,
                          'previous_level': previous_level, 'level': tier.level,
                          'previous_active': previous_active, 'active': tier.active}))
        return tier
    _pre_freeze(plan, reactivated_boss_type=serializer.instance.boss_type_id if reactivate else None)
    previous_threshold = serializer.instance.recharge_threshold
    tier = serializer.save()
    _apply_plan(plan, tier, request, operation='reorder' if renumber else ('reactivation' if reactivate else 'threshold'),
                previous_threshold=previous_threshold, previous_level=previous_level)
    return tier


def apply_creation(serializer, token, request, locked_account_ids):
    """Create an active tier and recalculate within the same store transaction."""
    from club_accounts.models import ClubAccount
    from .models import ClubOperationConfig
    from console.models import AdminAuditLog
    from .vip_transition import resume_guard_available as transition_guard_available

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('新增 VIP 重算必须在门店事务内执行')
    if not transition_guard_available():
        raise ValidationError('会员权益保护尚未就绪，不能新增启用等级')
    vip_enabled = ClubOperationConfig.objects.get(pk=1).vip_enabled
    if not isinstance(token, str) or len(token) > 8192:
        raise ValidationError('请先重新核对新增等级影响')
    try:
        expected = signing.loads(token, salt=SALT, max_age=300)
    except signing.BadSignature:
        raise ValidationError('核对信息无效或已过期，请重新核对') from None
    spec = creation_spec(serializer.validated_data)
    reward_ids = [item['reward'] for item in spec['upgrade_rewards']]
    if birthday_reward_id := _birthday_reward_id(spec['benefits']):
        reward_ids.append(birthday_reward_id)
    _lock_inputs(extra_boss_type_ids=[spec['boss_type_id']], extra_reward_ids=reward_ids)
    serializer.validated_data['boss_type'].refresh_from_db()
    spec = creation_spec(serializer.validated_data)
    plan, digest, _ = _snapshot(NEW_TIER_ID, spec['threshold'], new_tier=spec)
    if expected != {'store': _store(), 'action': 'create', 'digest': digest}:
        raise ValidationError('会员或等级数据已变化，请重新核对')
    current_ids = list(ClubAccount.objects.filter(account_type='BOSS').order_by('pk').values_list('pk', flat=True))
    if current_ids != locked_account_ids:
        raise ValidationError('老板账号已变化，请重新核对')
    if not vip_enabled:
        from .vip_deferred import mark_deferred
        tier = serializer.save()
        deferred = mark_deferred()
        AdminAuditLog.objects.create(**_audit_actor(request), method=request.method, path=request.path[:255],
            resource='vip-deferred-tier-creation', object_id=str(tier.pk), status_code=200,
            request_body=_audit_body(request, {'epoch': deferred.epoch, 'baseline_known': deferred.baseline_rules is not None,
                          'configuration': spec}))
        return tier
    _pre_freeze(plan)
    tier = serializer.save()
    _apply_plan(plan, tier, request, operation='creation', creation_data=spec)
    return tier


def apply_resume(serializer, token, request, locked_account_ids):
    """Enable VIP, reconcile grades, and queue unverifiable historic rights."""
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from console.models import AdminAuditLog
    from coupons.models import UserCoupon
    from users.models import CustomUser
    from .models import ClubOperationConfig, VipDeferredRecalculation, VipTier, VipUpgradeReview
    from .services import broadcast
    from .vip_deferred import mark_deferred
    from .vip_transition import resume_guard_available

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('VIP 重新启用必须在门店事务内执行')
    if not resume_guard_available():
        raise ValidationError('会员权益保护尚未就绪')
    if ClubOperationConfig.objects.get(pk=1).vip_enabled:
        raise ValidationError('VIP 已启用，请刷新后重试')
    if not isinstance(token, str) or len(token) > 8192:
        raise ValidationError('请先核对 VIP 重新启用的影响')
    try:
        expected = signing.loads(token, salt=SALT, max_age=300)
    except signing.BadSignature:
        raise ValidationError('核对信息无效或已过期，请重新核对') from None
    _lock_inputs()
    plan, digest, _ = _snapshot(None, None, resume=True)
    if expected != {'store': _store(), 'action': 'resume', 'digest': digest}:
        raise ValidationError('会员或等级数据已变化，请重新核对')
    account_ids = list(ClubAccount.objects.filter(account_type='BOSS').order_by('pk').values_list('pk', flat=True))
    if account_ids != locked_account_ids:
        raise ValidationError('老板账号已变化，请重新核对')
    deferred = VipDeferredRecalculation.objects.select_for_update().filter(pk=1, is_open=True).first()
    if deferred is None:
        deferred = mark_deferred()
    known = deferred.baseline_rules is not None
    if known:
        _pre_freeze(plan, vip_enabled=False)
    else:
        # Without the old tier rules, keep stored coupon terms instead of
        # assuming the current configuration applied when they were issued.
        owners = UserCoupon.objects.filter(account_id__in=account_ids, coupon__grant_key__startswith='vip-')
        owner_ids = list(owners.order_by('account_id').values_list('account_id', flat=True).distinct())
        for account in ClubAccount.objects.filter(pk__in=owner_ids).order_by('pk'):
            _lock_vip_coupons(account, downgrade=True, vip_enabled=False)
    serializer.save()
    review_count = 0
    for change in plan:
        account = ClubAccount.objects.select_for_update().get(pk=change.account_id)
        if (account.account_type != 'BOSS' or not account.license_membership_active
                or (account.business_cycle, account.boss_type_id) != (change.business_cycle, change.old_boss_type_id)):
            raise ValidationError('老板等级已变化，请重新核对')
        if change.old_boss_type_id != change.new_boss_type_id:
            ClubAccount.objects.filter(pk=account.pk).update(boss_type_id=change.new_boss_type_id)
            legacy = LegacyAccountMap.objects.filter(account_id=account.pk).values_list('legacy_user_id', flat=True).first()
            if legacy:
                CustomUser.objects.filter(pk=legacy).update(boss_type_id=change.new_boss_type_id)
            account.boss_type_id = change.new_boss_type_id
        if known:
            _issue_new_rewards(account, change.newly_eligible_tier_ids, preserve_existing_terms=True)
        else:
            for tier_id in change.newly_eligible_tier_ids:
                if VipUpgradeReview.objects.filter(account=account, tier_id=tier_id,
                        business_cycle=change.business_cycle, status='PENDING').exists():
                    continue
                from .vip_review_guard import quota_floors_for_tier
                _, created = VipUpgradeReview.objects.get_or_create(account=account, tier_id=tier_id,
                    business_cycle=change.business_cycle, epoch=deferred.epoch,
                    defaults={'evidence': {'reason': '停用前配置无法核实', 'net_consumption': change.net_consumption,
                                           'old_boss_type_id': change.old_boss_type_id,
                                           'new_boss_type_id': change.new_boss_type_id,
                                           'quota_floors': quota_floors_for_tier(VipTier.objects.get(pk=tier_id))}})
                review_count += int(created)
        if change.new_level > change.old_level and change.old_boss_type_id != change.new_boss_type_id:
            top = VipTier.objects.get(boss_type_id=change.new_boss_type_id)
            if known:
                from coupons.benefits import grant_periodic
                grant_periodic(account)
            broadcast('UPGRADE', f'vip:{account.pk}:{account.business_cycle}:tier-{top.pk}',
                      account=account, level=top.boss_type.name)
        AdminAuditLog.objects.create(**_audit_actor(request), method=request.method, path=request.path[:255],
            resource='vip-resume-recalculate', object_id=str(account.pk), status_code=200,
            request_body=_audit_body(request, {'epoch': deferred.epoch, 'baseline_known': known,
                          'from_boss_type': change.old_boss_type_id, 'to_boss_type': change.new_boss_type_id,
                          'net_consumption': change.net_consumption, 'business_cycle': change.business_cycle,
                          'candidate_tier_ids': list(change.newly_eligible_tier_ids)}))
    deferred.is_open = False
    deferred.pending = False
    from django.utils import timezone
    deferred.closed_at = timezone.now()
    deferred.save(update_fields=['is_open', 'pending', 'closed_at', 'updated_at'])
    pending_total = VipUpgradeReview.objects.filter(status='PENDING').count()
    AdminAuditLog.objects.create(**_audit_actor(request), method=request.method, path=request.path[:255],
        resource='vip-resume', object_id=str(deferred.epoch), status_code=200,
        request_body=_audit_body(request, {'baseline_known': known, 'affected_count': len(plan), 'new_review_count': review_count,
                      'manual_review_count': pending_total}))
    return {'baseline_known': known, 'affected_count': len(plan), 'manual_review_count': pending_total}
