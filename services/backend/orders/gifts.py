"""Gift eligibility and pricing, shared by direct and post-service gifting."""
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from users.models import EscortProfile
from .models import Order

# 1 yuan = 100 ledger units = 10 Xing'an coins. The threshold is per gift order.
GIFT_COMMISSION_THRESHOLD = 2000
GIFT_COMMISSION_RATE = 20
MAX_GIFT_QUANTITY = 999
MAX_LEDGER_AMOUNT = 2147483647


def gift_available(service):
    from django.utils import timezone
    now = timezone.now()
    return (not service.gift_festival or service.price >= 2100) and (
        not service.gift_starts_at or service.gift_starts_at <= now) and (
        not service.gift_ends_at or service.gift_ends_at > now)


def naming_title(value, *, required=False):
    if not isinstance(value, str) or len(value.strip()) > 30 or any(ord(char) < 32 for char in value):
        raise ValidationError('冠名文字须为 1 至 30 字的单行文本')
    title = value.strip()
    if required and not title:
        raise ValidationError('请填写给陪玩的冠名文字')
    return title


def gift_commission_rate(service, amount):
    if amount < GIFT_COMMISSION_THRESHOLD:
        return 0
    return GIFT_COMMISSION_RATE if service.commission_rate is None else service.commission_rate


def positive_id(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(label + '无效')
    if isinstance(value, str) and not value.isdigit():
        raise ValidationError(label + '无效')
    number = int(value)
    if not 0 < number < 2 ** 63:
        raise ValidationError(label + '无效')
    return number


def gift_context(request, *, order_id=None, provider_id=None, lock=False):
    """Return source order and currently eligible recipients in account ID space.

    Gifts need no free service slot, deposit, or working schedule. A disabled,
    hidden, unverified or unmapped recipient cannot receive a new gift.
    """
    if request.account.account_type != ClubAccount.AccountType.BOSS:
        raise ValidationError('仅老板可以赠送礼物')
    source = None
    profiles = EscortProfile.objects.select_related('account', 'user').filter(
        is_verified=True, account__account_type=ClubAccount.AccountType.PROVIDER,
        account__is_active=True, account__can_login=True, account__can_view=True,
    )
    if order_id is not None:
        order_id = positive_id(order_id, '原订单编号')
        orders = Order.objects.select_related('service__service_category', 'support_contact')
        if lock:
            orders = orders.select_for_update()
        source = orders.filter(
            Q(customer_account=request.account)
            | Q(customer_account__isnull=True, customer=request.legacy_user), pk=order_id,
        ).first()
        if source is None:
            raise ValidationError('原订单不存在')
        if source.status != Order.Status.COMPLETED or source.payment_status != Order.PaymentStatus.PAID:
            raise ValidationError('服务完成后才可以赠送礼物')
        if source.source_order_id or (source.service.service_category and source.service.service_category.is_gift):
            raise ValidationError('礼物单不能重复打赏')
        users = set(source.providers.filter(settlement_base__gt=0).values_list('provider_id', flat=True))
        if source.provider_id:
            users.add(source.provider_id)
        profiles = profiles.filter(user_id__in=users)
    elif provider_id is None:
        raise ValidationError('请选择收礼陪玩')
    if source is None:
        profiles = profiles.filter(account_id=positive_id(provider_id, '收礼陪玩'))
    recipients = list(profiles.order_by('id'))
    if not recipients:
        raise ValidationError('陪玩当前不可接收礼物')
    if provider_id is not None:
        wanted = positive_id(provider_id, '收礼陪玩')
        if not any(item.account_id == wanted for item in recipients):
            raise ValidationError('请选择本次服务的收礼陪玩')
    return source, recipients


def recipient_data(profile):
    return {'id': profile.account_id,
            'nickname': profile.display_name or profile.account.nickname or profile.account.username,
            'avatar': profile.account.avatar_url or ''}
