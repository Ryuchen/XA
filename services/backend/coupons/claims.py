from datetime import timedelta
from stores.db import transaction
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from orders.purchase_benefits import activity_scope, BEIJING
from orders.models import ExperienceUse
from django.utils import timezone
from .models import Coupon, UserCoupon


def claim_identity(account, coupon):
    period = 'ONCE'
    expires = None
    key = None
    if coupon.experience_kind:
        scope = activity_scope(account, coupon.experience_kind, coupon.platform)
        period = scope['period']
        key = f"qualification:{account.pk}:{account.business_cycle}:{coupon.experience_kind}:{scope['platform']}:{period}"
        if coupon.experience_kind == 'DAILY_VALUE':
            now = timezone.localtime(timezone.now(), BEIJING)
            expires = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return period, expires, key


def already_claimed(account, coupon):
    period, _, key = claim_identity(account, coupon)
    if key:
        return UserCoupon.objects.filter(claim_key=key).exists()
    return UserCoupon.objects.filter(account=account, coupon=coupon,
        business_cycle=account.business_cycle, claim_period=period).exists()


def claimed_coupon_ids(account, coupons):
    """Resolve the claim centre's ordinary and shared qualification status in one query."""
    identities = {coupon.pk: claim_identity(account, coupon)[2] for coupon in coupons}
    ordinary_ids = [coupon_id for coupon_id, key in identities.items() if key is None]
    qualification_keys = [key for key in identities.values() if key is not None]
    if not identities:
        return set()
    rows = UserCoupon.objects.filter(
        Q(account=account, business_cycle=account.business_cycle, claim_period='ONCE', coupon_id__in=ordinary_ids)
        | Q(claim_key__in=qualification_keys)
    ).values_list('coupon_id', 'claim_key')
    ordinary_claims, qualification_claims = set(), set()
    for coupon_id, key in rows:
        ordinary_claims.add(coupon_id)
        if key is not None:
            qualification_claims.add(key)
    return {coupon_id for coupon_id, key in identities.items()
            if (key in qualification_claims if key is not None else coupon_id in ordinary_claims)}


def used_qualification_ids(account, coupons):
    """Resolve the same account, platform and period scopes enforced by claim()."""
    scopes = {coupon.pk: activity_scope(account, coupon.experience_kind, coupon.platform)
              for coupon in coupons if coupon.experience_kind}
    if not scopes:
        return set()
    query = Q(pk__in=[])
    for scope in scopes.values():
        query |= Q(**scope)
    used = set(ExperienceUse.objects.filter(query).values_list('kind', 'platform', 'period'))
    return {coupon_id for coupon_id, scope in scopes.items()
            if (scope['kind'], scope['platform'], scope['period']) in used}


def _provider_claim_lifecycle(fn):
    """Protect a provider's original membership before wallet/coupon locks."""
    from functools import wraps

    @wraps(fn)
    def wrapped(account, user, coupon_id):
        from django.conf import settings
        from club_accounts.models import ClubAccount
        if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                or account.account_type != ClubAccount.AccountType.PROVIDER):
            return fn(account, user, coupon_id)
        from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                       lock_provider_operation, provider_membership_current)
        original_cycle = account.business_cycle
        try:
            ensure_provider_lifecycle(account)
            with transaction.atomic():
                lock_provider_operation(account)
                if account.business_cycle != original_cycle or not provider_membership_current(account):
                    raise ValidationError('陪玩账号状态或业务周期已变化，请重新登录')
                return fn(account, user, coupon_id)
        except ProviderLifecycleError as exc:
            raise ValidationError('陪玩账号状态暂不可核验，请稍后重试') from exc
    return wrapped


@_provider_claim_lifecycle
@transaction.atomic
def claim(account, user, coupon_id):
    from stores.license_benefits import LicenseDecisionUnavailable, check_new_benefits
    from wallet.services import get_wallet
    original_cycle = account.business_cycle
    get_wallet(account=account, user=user, for_update=True)
    account.refresh_from_db(fields=['business_cycle'])
    if account.business_cycle != original_cycle:
        raise ValidationError('账号业务周期已变化，请重新登录')
    coupon = Coupon.objects.select_for_update().get(pk=coupon_id)
    period, expires, key = claim_identity(account, coupon)
    # A retry of an already issued coupon is not a new grant. Check the same
    # account, original cycle and exact coupon before current authorization.
    existing = UserCoupon.objects.filter(account=account, coupon=coupon,
        business_cycle=original_cycle, claim_period=period, claim_key=key).first()
    if existing:
        existing._claim_replayed = True
        return existing
    admission, _ = check_new_benefits('PROMOTIONS')
    if admission == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if admission != 'ALLOW':
        raise ValidationError('门店授权暂不允许领取新优惠券，请联系门店管理员')
    if not coupon.is_claimable:
        raise ValidationError('该券已抢光、未开放或已过期')
    if coupon.experience_kind and ExperienceUse.objects.filter(**activity_scope(account, coupon.experience_kind, coupon.platform)).exists():
        raise ValidationError('该项资格或今日额度已使用')
    if already_claimed(account, coupon):
        raise ValidationError('本周期或今日已领取该项资格券')
    row = UserCoupon.objects.create(user=user, account=account, coupon=coupon,
        business_cycle=account.business_cycle, claim_period=period, claim_key=key, expires_at=expires)
    coupon.claimed_qty += 1
    coupon.save(update_fields=['claimed_qty'])
    from .notifier import notify_coupon_claim_changed, notify_owner_coupon_changed
    transaction.on_commit(lambda: notify_coupon_claim_changed(coupon.pk))
    transaction.on_commit(lambda: notify_owner_coupon_changed(account.pk))
    if coupon.total_qty and coupon.claimed_qty >= coupon.total_qty:
        from .notifier import notify_coupon_catalog_changed
        transaction.on_commit(notify_coupon_catalog_changed)
    return row
