from stores.db import transaction
from django.utils import timezone
from django.core import signing
from django.db.models import Q
from stores.context import current_store
from rest_framework.exceptions import ValidationError
from club_accounts.models import ClubAccount, LegacyAccountMap
from orders.models import Order, Reservation, AfterSale
from coupons.models import UserCoupon
from wallet.services import get_wallet
from .models import MemberTag, RewardClaim, PointFundingLot, ProviderNaming, RetirementAudit
from .services import locked_profile, change_points


def _has_unsettled_orders(orders):
    """Identify paid or negotiated orders whose earnings still need settlement."""
    outstanding = Q(status='COMPLETED', payment_status__in=['PAID', 'PARTIALLY_REFUNDED'])
    outstanding |= Q(status='COMPLETED', payment_status='REFUNDED',
                     points_redemption__refunds__negotiation_posting__termination_processed_at__isnull=False)
    outstanding |= Q(status='COMPLETED', payment_status='REFUNDED',
                     refund_records__negotiation_execution__termination_processed_at__isnull=False)
    outstanding |= Q(status='CANCELLED', payment_status='REFUNDED',
                     refund_records__benefit_effects__subsidy_negotiated=True)
    return orders.filter(outstanding, settled_at__isnull=True).exists()


def retirement_facts(account, wallet, profile):
    from users.models import CheckinMonthProgress
    cycle = account.business_cycle
    today = timezone.localdate()
    progress = CheckinMonthProgress.objects.select_for_update().filter(
        account=account, year=today.year, month=today.month).first()
    coupons = list(UserCoupon.objects.filter(account=account, business_cycle=cycle, status='UNUSED')
                   .order_by('pk').values('id', 'coupon_id'))
    claims = list(RewardClaim.objects.filter(account=account, status__in=['PENDING', 'FULFILLING'])
                  .order_by('pk').values('id', 'name_snapshot', 'kind_snapshot', 'status'))
    tags = list(MemberTag.objects.filter(account=account, is_active=True).order_by('pk').values('id', 'title'))
    namings = list(ProviderNaming.objects.filter(sponsor=account, is_visible=True).order_by('pk')
                   .values('id', 'title', 'provider_id', 'expires_at'))
    for naming in namings:
        naming['expires_at'] = naming['expires_at'].isoformat()
    orders = Order.objects.filter(customer_account=account)
    blocked = []
    if not account.license_membership_active:
        blocked.append('该老板已经退店')
    if orders.exclude(status__in=['COMPLETED', 'CANCELLED']).exists():
        blocked.append('请先完成或取消所有进行中的订单')
    if Reservation.objects.filter(customer_account=account, status__in=['PENDING', 'CONFIRMED']).exists():
        blocked.append('请先完成或取消预约')
    if AfterSale.objects.filter(account=account, status='OPEN').exists():
        blocked.append('请先处理完成客诉')
    if _has_unsettled_orders(orders):
        blocked.append('订单资金尚未结算，请先结清')
    if wallet.balance or wallet.frozen_amount:
        blocked.append('钱包余额或冻结资金未结清')
    from wallet.models import RechargeGiftApproval
    if RechargeGiftApproval.objects.filter(recharge__account=account, status='PENDING').exists():
        blocked.append('充值赠送审批尚未处理，请先结清')
    return {'account_id': account.pk, 'business_cycle': cycle, 'boss_type_id': account.boss_type_id,
            'wallet_balance': wallet.balance, 'wallet_frozen': wallet.frozen_amount,
            'points': profile.points_balance, 'selected_tags': profile.selected_tags,
            'unused_makeup_cards': {'year': today.year, 'month': today.month,
                                    'count': progress.makeup_cards if progress else 0},
            'unused_coupons': coupons, 'undelivered_claims': claims,
            'active_tags': tags, 'active_namings': namings, 'blocking_reasons': blocked}


def preview_retirement(account, operator):
    if account.account_type != 'BOSS' or current_store() is None:
        raise ValidationError('无法确定本店老板账户')
    with transaction.atomic():
        from club_accounts.license_quota import lock_category
        lock_category('BOSS')
        wallet = get_wallet(account_id=account.pk, for_update=True)
        account = ClubAccount.objects.select_for_update().get(pk=account.pk)
        profile = locked_profile(account)
        facts = retirement_facts(account, wallet, profile)
    token = signing.dumps({'store': current_store().slug, 'operator': operator.pk, 'facts': facts},
                          salt='boss-retirement-v1', compress=True)
    return {'facts': facts, 'can_retire': not facts['blocking_reasons'], 'confirmation_token': token}


@transaction.atomic
def retire(account, operator, reason, confirmation_token):
    if account.account_type != 'BOSS':
        raise ValidationError('仅老板账户可以执行退店清空')
    if not isinstance(reason, str) or not 2 <= len(reason.strip()) <= 500:
        raise ValidationError('请填写2至500字退店清空说明')
    from club_accounts.license_quota import lock_category, release_locked_quota
    quota = lock_category('BOSS')
    wallet = get_wallet(account_id=account.pk, for_update=True)
    from .vip_transition import transition_guard_enabled
    if transition_guard_enabled():
        from .models import ClubOperationConfig
        from .services import config
        config()
        ClubOperationConfig.objects.select_for_update().get(pk=1)
    account = ClubAccount.objects.select_for_update().get(pk=account.pk)
    profile = locked_profile(account)
    try:
        evidence = signing.loads(confirmation_token, salt='boss-retirement-v1', max_age=900)
    except (signing.BadSignature, TypeError):
        raise ValidationError('请重新预览退店清空内容并确认') from None
    facts = retirement_facts(account, wallet, profile)
    if (current_store() is None or evidence.get('store') != current_store().slug
            or evidence.get('operator') != operator.pk or evidence.get('facts') != facts):
        raise ValidationError('退店内容已变化，请重新预览并确认')
    if facts['blocking_reasons']:
        raise ValidationError(facts['blocking_reasons'])
    if not account.license_membership_active:
        raise ValidationError('该老板已经退店')
    orders = Order.objects.filter(customer_account=account)
    if orders.exclude(status__in=['COMPLETED','CANCELLED']).exists():
        raise ValidationError('请先完成或取消所有进行中的订单')
    if Reservation.objects.filter(customer_account=account, status__in=['PENDING','CONFIRMED']).exists():
        raise ValidationError('请先完成或取消预约')
    if AfterSale.objects.filter(account=account, status='OPEN').exists():
        raise ValidationError('请先处理完成客诉')
    if _has_unsettled_orders(orders):
        raise ValidationError('订单资金尚未结算，请先结清')
    if wallet.balance or wallet.frozen_amount:
        raise ValidationError('钱包余额或冻结资金未结清，不能清空权益')
    cycle = account.business_cycle
    RetirementAudit.objects.create(account=account, business_cycle=cycle, operator=operator,
        reason=reason.strip(), snapshot=facts)
    if profile.points_balance:
        change_points(profile, -profile.points_balance, f'retire:{account.pk}:{cycle}:points', 'RETIREMENT',
            '退店清空会员积分', operator=operator, allow_debt=True)
    profile.selected_tags = []
    profile.save(update_fields=['selected_tags','updated_at'])
    from users.models import CheckinMonthProgress
    card_snapshot = facts['unused_makeup_cards']
    CheckinMonthProgress.objects.filter(account=account, year=card_snapshot['year'],
        month=card_snapshot['month'], makeup_cards__gt=0).update(makeup_cards=0, updated_at=timezone.now())
    UserCoupon.objects.filter(account=account, business_cycle=cycle, status='UNUSED').update(status='EXPIRED')
    MemberTag.objects.filter(account=account, is_active=True).update(is_active=False)
    changed_claims = RewardClaim.objects.filter(account=account, status__in=['PENDING','FULFILLING']).update(
        status='CANCELLED', delivery_note='退店清空：未交付业务权益已回收', operator=operator)
    if changed_claims:
        from .notifier import notify_claims_after_commit
        notify_claims_after_commit()
    ProviderNaming.objects.filter(sponsor=account, is_visible=True).update(is_visible=False, expires_at=timezone.now())
    PointFundingLot.objects.filter(account=account, kind=PointFundingLot.Kind.RECHARGE).update(remaining=0, gift_remaining=0)
    PointFundingLot.objects.filter(account=account).exclude(kind=PointFundingLot.Kind.RECHARGE).update(remaining=0)
    release_locked_quota(quota)
    ClubAccount.objects.filter(pk=account.pk).update(
        business_cycle=cycle+1, boss_type=None, license_membership_active=False)
    from users.models import CustomUser
    mapping = LegacyAccountMap.objects.filter(account=account).first()
    if mapping:
        CustomUser.objects.filter(pk=mapping.legacy_user_id).update(boss_type=None)
    account.refresh_from_db()
    return account
