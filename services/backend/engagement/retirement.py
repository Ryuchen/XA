from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from club_accounts.models import ClubAccount, LegacyAccountMap
from orders.models import Order, Reservation, AfterSale
from coupons.models import UserCoupon
from wallet.services import get_wallet
from .models import MemberTag, RewardClaim, PointFundingLot, ProviderNaming, RetirementAudit
from .services import locked_profile, change_points


@transaction.atomic
def retire(account, operator, reason):
    if account.account_type != 'BOSS':
        raise ValidationError('仅老板账户可以执行退店清空')
    if not isinstance(reason, str) or not 2 <= len(reason.strip()) <= 500:
        raise ValidationError('请填写2至500字退店清空说明')
    wallet = get_wallet(account_id=account.pk, for_update=True)
    account.refresh_from_db()
    orders = Order.objects.filter(customer_account=account)
    if orders.exclude(status__in=['COMPLETED','CANCELLED']).exists():
        raise ValidationError('请先完成或取消所有进行中的订单')
    if Reservation.objects.filter(customer_account=account, status__in=['PENDING','CONFIRMED']).exists():
        raise ValidationError('请先完成或取消预约')
    if AfterSale.objects.filter(account=account, status='OPEN').exists():
        raise ValidationError('请先处理完成客诉')
    if orders.filter(status='COMPLETED', payment_status='PAID', settled_at__isnull=True).exists():
        raise ValidationError('订单资金尚未结算，请先结清')
    if wallet.balance or wallet.frozen_amount:
        raise ValidationError('钱包余额或冻结资金未结清，不能清空权益')
    profile = locked_profile(account)
    cycle = account.business_cycle
    RetirementAudit.objects.create(account=account, business_cycle=cycle, operator=operator,
        reason=reason.strip(), snapshot={'boss_type':account.boss_type_id, 'points':profile.points_balance})
    if profile.points_balance:
        change_points(profile, -profile.points_balance, f'retire:{account.pk}:{cycle}:points', 'RETIREMENT',
            '退店清空会员积分', operator=operator, allow_debt=True)
    profile.selected_tags = []
    profile.save(update_fields=['selected_tags','updated_at'])
    UserCoupon.objects.filter(account=account, business_cycle=cycle, status='UNUSED').update(status='EXPIRED')
    MemberTag.objects.filter(account=account, is_active=True).update(is_active=False)
    RewardClaim.objects.filter(account=account, status__in=['PENDING','FULFILLING']).update(
        status='CANCELLED', delivery_note='退店清空：未交付业务权益已回收', operator=operator)
    ProviderNaming.objects.filter(sponsor=account, is_visible=True).update(is_visible=False, expires_at=timezone.now())
    PointFundingLot.objects.filter(account=account).update(remaining=0)
    ClubAccount.objects.filter(pk=account.pk).update(business_cycle=cycle+1, boss_type=None)
    from users.models import CustomUser
    mapping = LegacyAccountMap.objects.filter(account=account).first()
    if mapping:
        CustomUser.objects.filter(pk=mapping.legacy_user_id).update(boss_type=None)
    account.refresh_from_db()
    return account
