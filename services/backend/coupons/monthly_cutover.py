"""Store-local monthly coupon initialization and durable issuance."""
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .monthly import issue_monthly_coupons
from .models import VipMonthlyCutover


def initialize_new_store_rules(store_slug):
    """Initialize the first-edition rule for an empty new store only."""
    from club_accounts.models import ClubAccount
    from engagement.models import ConsumptionConfirmation
    from .models import UserCoupon, VipMonthlyCouponGrant
    from stores.db import transaction
    with transaction.atomic():
        from engagement.services import config
        from engagement.models import ClubOperationConfig
        config()
        ClubOperationConfig.objects.select_for_update().get(pk=1)
        if VipMonthlyCutover.objects.filter(pk=1).exists():
            return
        if (ClubAccount.objects.filter(account_type='BOSS').exists() or ConsumptionConfirmation.objects.exists()
                or UserCoupon.objects.exists() or VipMonthlyCouponGrant.objects.exists()):
            raise ValidationError('新店已有会员或券记录，不能自动初始化会员发券规则')
        VipMonthlyCutover.objects.create(pk=1, activated_at=timezone.now(),
            platform_actor_id='store-initialization', operation_id=f'initial:{store_slug}')


def cutover_status():
    row = VipMonthlyCutover.objects.filter(pk=1).first()
    return {'configured': bool(row), 'activated_at': row.activated_at.isoformat() if row else None,
            'available': True}


def activate_cutover(*, platform_actor_id, operation_id):
    """Write the current server time once, while holding the store config lock."""
    from engagement.models import ClubOperationConfig
    from engagement.services import config
    from stores.db import transaction
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('月券切换必须在门店事务内执行')
    config()
    ClubOperationConfig.objects.select_for_update().get(pk=1)
    if VipMonthlyCutover.objects.filter(pk=1).exists():
        raise ValueError('本店月券切换时间已经登记，不能修改')
    now = timezone.now()
    row = VipMonthlyCutover.objects.create(pk=1, activated_at=now,
        platform_actor_id=str(platform_actor_id), operation_id=operation_id)
    return {'configured': True, 'activated_at': row.activated_at.isoformat(), 'available': True}


def grant_current_month(account, tier, *, now=None):
    row = VipMonthlyCutover.objects.filter(pk=1).first()
    cutover = row.activated_at if row else None
    now = now or timezone.now()
    if not cutover or timezone.is_naive(cutover) or timezone.is_naive(now) or now < cutover:
        raise ValidationError('本店会员月券切换时间未配置或尚未生效')
    # Existing decisions and actual coupon rows remain authoritative.  A
    # missing decision for the current first-edition period can be issued;
    # never manufacture a zero decision from an activation timestamp.
    return issue_monthly_coupons(account, tier.pk, now=now, allow_new_period=True)
