"""Bind a previewed discount to store, buyer and current benefit terms.

The ordinary-order payment integration is experimental and default-off. Its
caller must acquire locks in the existing order: service/provider, then buyer
wallet, before invoking the locked validator and consuming the returned
voucher in that same transaction.
"""
from dataclasses import asdict
from django.core import signing
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from coupons.models import Coupon, UserCoupon
from engagement.models import ClubOperationConfig
from promotions.models import Promotion
from stores.context import current_store
from stores.db import transaction
from users.models import BossType
from .discount_recommendation import service_discount_options

SALT = 'orders.discount-option.v1'


def _store_slug():
    store = current_store()
    if store is None:
        raise ValidationError('请从所属门店入口重新报价')
    return store.slug


def _date(value):
    return value.isoformat() if value else None


def _vip_terms(tier):
    if tier is None:
        return None
    return {'id': tier.pk, 'level': tier.level, 'active': tier.active,
            'monthly_discount': tier.monthly_discount, 'season_discount': tier.season_discount,
            'safe_box_service_ids': tier.safe_box_service_ids, 'benefits': tier.benefits}


def _terms(account, service, units, option, reservation_minutes=None):
    operation = ClubOperationConfig.objects.filter(pk=1).first()
    if not operation:
        raise ValidationError('门店会员规则尚未配置')
    boss_type = account.boss_type
    result = {'version': 1, 'store': _store_slug(), 'account': account.pk,
        'business_cycle': account.business_cycle, 'service': service.pk, 'units': units,
        'service_updated_at': _date(service.updated_at), 'vip_enabled': operation.vip_enabled,
        'boss_type': account.boss_type_id,
        'boss_rate': boss_type.discount_rate if boss_type else None,
        'boss_active': boss_type.is_active if boss_type else None, 'option': asdict(option)}
    from engagement.models import VipTier
    result['vip_tier'] = _vip_terms(VipTier.objects.filter(boss_type_id=account.boss_type_id).first()
                                    if account.boss_type_id else None)
    if reservation_minutes is not None:
        result['reservation_minutes'] = reservation_minutes
    if option.promotion_id:
        promo = Promotion.objects.filter(pk=option.promotion_id).first()
        if promo is None:
            raise ValidationError('活动已变化，请重新报价')
        result['promotion'] = {'id': promo.pk, 'updated_at': _date(promo.updated_at),
            'discount_rate': promo.discount_rate, 'commission_type': promo.commission_type,
            'commission_rate': promo.commission_rate, 'commission_fixed': promo.commission_fixed,
            'start_at': _date(promo.start_at), 'end_at': _date(promo.end_at),
            'scope': promo.scope, 'category_id': promo.category_id, 'active': promo.is_active}
    if option.coupon_id:
        owned = UserCoupon.objects.select_related('coupon').filter(pk=option.coupon_id, account_id=account.pk).first()
        if owned is None:
            raise ValidationError('优惠券已变化，请重新报价')
        coupon = owned.coupon
        from coupons.pricing_terms import effective_coupon
        effective = effective_coupon(account, owned, vip_enabled=operation.vip_enabled)
        result['voucher'] = {'id': owned.pk, 'coupon_id': coupon.pk, 'status': owned.status,
            'cycle': owned.business_cycle, 'expires_at': _date(owned.expires_at),
            'terms_locked': owned.vip_terms_locked_by_downgrade, 'kind': coupon.discount_type,
            'threshold': coupon.threshold, 'amount': coupon.amount, 'rate': coupon.discount_rate,
            'max_discount': coupon.max_discount, 'limit_units': coupon.limit_units,
            'scope': coupon.scope, 'scope_variant': coupon.scope_variant, 'service_ids': coupon.service_ids,
            'experience_kind': coupon.experience_kind, 'platform': coupon.platform,
            'active': coupon.is_active, 'valid_to': _date(coupon.valid_to),
            'effective_rate': effective.discount_rate, 'effective_max_discount': effective.max_discount,
            'effective_service_ids': effective.service_ids,
            'effective_valid_to': _date(effective.valid_to)}
    return result


def signed_option(account, service, units, option, reservation_minutes=None):
    return signing.dumps(_terms(account, service, units, option, reservation_minutes), salt=SALT, compress=True)


def _read_token(account, service, units, token, reservation_minutes=None):
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise ValidationError('优惠确认信息无效，请重新报价')
    try:
        expected = signing.loads(token, salt=SALT)
    except signing.BadSignature as exc:
        raise ValidationError('优惠确认信息无效，请重新报价') from exc
    if (not isinstance(expected, dict) or expected.get('store') != _store_slug()
            or expected.get('account') != account.pk or expected.get('service') != service.pk
            or expected.get('units') != units or expected.get('reservation_minutes') != reservation_minutes
            or not isinstance(expected.get('option'), dict)):
        raise ValidationError('优惠确认信息与当前订单不一致，请重新报价')
    return expected


def _resolve_selection(account, service, units, expected, reservation_minutes=None):
    options = service_discount_options(account, service, units, reservation_minutes)
    selected = next((item for item in options if item.key == expected['option'].get('key')), None)
    if selected is None or _terms(account, service, units, selected, reservation_minutes) != expected:
        raise ValidationError('优惠条件或金额已变化，请重新报价')
    coupon = UserCoupon.objects.filter(pk=selected.coupon_id, account_id=account.pk).first() if selected.coupon_id else None
    promotion = Promotion.objects.filter(pk=selected.promotion_id).first() if selected.promotion_id else None
    return selected, promotion, coupon


def preview_selected_discount(account, service, units, token, reservation_minutes=None):
    """Nonlocking check for a preview; payment must still call the lock path."""
    return _resolve_selection(account, service, units, _read_token(account, service, units, token, reservation_minutes), reservation_minutes)


def lock_selected_discount(account, service, units, token, reservation_minutes=None):
    """Recheck a selected option while its relevant rows and wallet are locked."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('优惠选择必须在订单付款事务内核对')
    expected = _read_token(account, service, units, token, reservation_minutes)
    from .catalog import visible_service_q
    service = type(service).objects.select_for_update().filter(visible_service_q(), pk=service.pk).first()
    if service is None:
        raise ValidationError('服务已下架或不可用，请重新报价')
    operation = ClubOperationConfig.objects.select_for_update().filter(pk=1).first()
    if not operation:
        raise ValidationError('门店会员规则尚未配置')
    account = ClubAccount.objects.select_for_update().filter(pk=account.pk, is_active=True, can_login=True).first()
    if account is None:
        raise ValidationError('老板账号不可用，请重新核对')
    if account.business_cycle != expected.get('business_cycle'):
        raise ValidationError('账户业务周期已变化，请重新报价')
    boss_type = tier = None
    if account.boss_type_id:
        boss_type = BossType.objects.select_for_update().filter(pk=account.boss_type_id).first()
        from engagement.models import VipTier
        tier = VipTier.objects.select_for_update().filter(boss_type_id=account.boss_type_id).first()
        if boss_type:
            account.boss_type = boss_type
    if (expected.get('service_updated_at') != _date(service.updated_at)
            or expected.get('vip_enabled') != operation.vip_enabled
            or expected.get('boss_type') != account.boss_type_id
            or expected.get('boss_rate') != (boss_type.discount_rate if boss_type else None)
            or expected.get('boss_active') != (boss_type.is_active if boss_type else None)
            or expected.get('vip_tier') != _vip_terms(tier)):
        raise ValidationError('服务或会员规则已变化，请重新报价')
    option = expected['option']
    locked_promotion = locked_coupon = owned = None
    if option.get('promotion_id'):
        locked_promotion = Promotion.objects.select_for_update().filter(pk=option['promotion_id']).first()
        promo = expected.get('promotion')
        if (not locked_promotion or not isinstance(promo, dict) or promo != {
                'id': locked_promotion.pk, 'updated_at': _date(locked_promotion.updated_at),
                'discount_rate': locked_promotion.discount_rate, 'commission_type': locked_promotion.commission_type,
                'commission_rate': locked_promotion.commission_rate, 'commission_fixed': locked_promotion.commission_fixed,
                'start_at': _date(locked_promotion.start_at), 'end_at': _date(locked_promotion.end_at),
                'scope': locked_promotion.scope, 'category_id': locked_promotion.category_id,
                'active': locked_promotion.is_active}):
            raise ValidationError('活动规则已变化，请重新报价')
    if option.get('coupon_id'):
        coupon_id = UserCoupon.objects.filter(pk=option['coupon_id'], account_id=account.pk).values_list('coupon_id', flat=True).first()
        if coupon_id:
            locked_coupon = Coupon.objects.select_for_update().filter(pk=coupon_id).first()
            owned = UserCoupon.objects.select_for_update().filter(pk=option['coupon_id'], account_id=account.pk).first()
            if owned and owned.coupon_id != coupon_id:
                raise ValidationError('优惠券已变化，请重新报价')
        voucher = expected.get('voucher')
        if not owned or not locked_coupon or not isinstance(voucher, dict):
            raise ValidationError('优惠券已变化，请重新报价')
        actual = {'id': owned.pk, 'coupon_id': locked_coupon.pk, 'status': owned.status,
            'cycle': owned.business_cycle, 'expires_at': _date(owned.expires_at),
            'terms_locked': owned.vip_terms_locked_by_downgrade, 'kind': locked_coupon.discount_type,
            'threshold': locked_coupon.threshold, 'amount': locked_coupon.amount, 'rate': locked_coupon.discount_rate,
            'max_discount': locked_coupon.max_discount, 'limit_units': locked_coupon.limit_units,
            'scope': locked_coupon.scope, 'scope_variant': locked_coupon.scope_variant,
            'service_ids': locked_coupon.service_ids, 'experience_kind': locked_coupon.experience_kind,
            'platform': locked_coupon.platform, 'active': locked_coupon.is_active, 'valid_to': _date(locked_coupon.valid_to)}
        if any(voucher.get(key) != value for key, value in actual.items()):
            raise ValidationError('优惠券条款已变化，请重新报价')
    options = service_discount_options(account, service, units, reservation_minutes,
                                       locked=True, coupon_id=option.get('coupon_id'),
                                       promotion_id=option.get('promotion_id'))
    selected = next((item for item in options if item.key == option.get('key')), None)
    if selected is None or asdict(selected) != option:
        raise ValidationError('优惠条件或金额已变化，请重新报价')
    if owned:
        from coupons.pricing_terms import effective_coupon
        owned.coupon = locked_coupon
        effective = effective_coupon(account, owned, vip_enabled=operation.vip_enabled, locked=True)
        if (voucher.get('effective_rate') != effective.discount_rate
                or voucher.get('effective_max_discount') != effective.max_discount
                or voucher.get('effective_service_ids') != effective.service_ids
                or voucher.get('effective_valid_to') != _date(effective.valid_to)):
            raise ValidationError('优惠券生效条款已变化，请重新报价')
        from coupons.pricing_terms import materialize_effective_coupon
        materialize_effective_coupon(account, owned, vip_enabled=operation.vip_enabled)
    return selected, locked_promotion, owned
