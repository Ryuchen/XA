from datetime import timedelta
from django.db import transaction
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


@transaction.atomic
def claim(account, user, coupon_id):
    from wallet.services import get_wallet
    get_wallet(account=account, user=user, for_update=True)
    account.refresh_from_db(fields=['business_cycle'])
    coupon = Coupon.objects.select_for_update().get(pk=coupon_id)
    if not coupon.is_claimable:
        raise ValidationError('该券已抢光、未开放或已过期')
    if coupon.experience_kind and ExperienceUse.objects.filter(**activity_scope(account, coupon.experience_kind, coupon.platform)).exists():
        raise ValidationError('该项资格或今日额度已使用')
    period, expires, key = claim_identity(account, coupon)
    if already_claimed(account, coupon):
        raise ValidationError('本周期或今日已领取该项资格券')
    row = UserCoupon.objects.create(user=user, account=account, coupon=coupon,
        business_cycle=account.business_cycle, claim_period=period, claim_key=key, expires_at=expires)
    coupon.claimed_qty += 1
    coupon.save(update_fields=['claimed_qty'])
    return row
