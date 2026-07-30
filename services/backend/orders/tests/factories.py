"""公共测试夹具工厂。

集中提供用户 / 陪玩档案 / 服务 / 订单 / 钱包 / 评价的构造函数，
供 orders、console、wallet 各 app 的单元测试复用，避免重复样板。
"""

import itertools
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from console.models import AdminMembership, AdminRole
from coupons.models import Coupon, UserCoupon
from orders.models import Evaluation, Order, ServiceCategory, ServiceItem
from promotions.models import Promotion
from users.models import Achievement, BossType, EscortLevel, EscortProfile
from wallet.models import Transaction, Wallet

User = get_user_model()

_seq = itertools.count(1)


def _next() -> int:
    return next(_seq)


def make_user(role=User.Role.CUSTOMER, password='pass1234', **kwargs):
    """创建用户。username 自增唯一，role 默认 CUSTOMER。"""
    n = _next()
    fields = {
        'username': f'user{n}',
        'nickname': kwargs.pop('nickname', f'用户{n}'),
        'role': role,
    }
    fields.update(kwargs)
    user = User(**fields)
    user.set_password(password)
    user.save()
    return user


def make_superuser(password='pass1234', **kwargs):
    """创建超级管理员，用于 console 接口鉴权（HasConsolePerm 对超管放行）。"""
    n = _next()
    user = User(username=f'admin{n}', is_staff=True, is_superuser=True, **kwargs)
    user.set_password(password)
    user.save()
    return user


def make_console_user(perms=None, password='pass1234'):
    """创建持有指定权限点的后台用户（非超管），用于细粒度 RBAC 测试。"""
    n = _next()
    role = AdminRole.objects.create(
        name=f'角色{n}', code=f'role{n}', permissions=list(perms or [])
    )
    user = make_user(role=User.Role.OPERATOR, password=password)
    membership = AdminMembership.objects.create(user=user)
    membership.roles.set([role])
    return user


def make_escort_profile(user, **kwargs):
    """为陪玩用户创建陪玩档案。"""
    fields = {
        'display_name': kwargs.pop('display_name', user.nickname or user.username),
    }
    fields.update(kwargs)
    return EscortProfile.objects.create(user=user, **fields)


def make_provider(with_profile=True, **profile_kwargs):
    """创建陪玩用户（默认附带陪玩档案）。返回 user。"""
    user = make_user(role=User.Role.PROVIDER)
    if with_profile:
        make_escort_profile(user, **profile_kwargs)
    return user


def make_service(name='上分陪玩', price=10000, **kwargs):
    """创建服务项（price 单位：分）。"""
    return ServiceItem.objects.create(name=name, price=price, **kwargs)


def make_service_category(name=None, is_gift=False, **kwargs):
    """创建分类字典条目。"""
    n = _next()
    return ServiceCategory.objects.create(
        name=name or f'分类{n}', is_gift=is_gift, **kwargs
    )


def make_order(customer, service=None, provider=None, status=Order.Status.PENDING, **kwargs):
    """创建订单。amount 默认取服务价格。"""
    service = service or make_service()
    fields = {
        'customer': customer,
        'service': service,
        'provider': provider,
        'amount': kwargs.pop('amount', service.price),
        'status': status,
    }
    fields.update(kwargs)
    return Order.objects.create(**fields)


def make_completed_order(customer, provider, service=None, **kwargs):
    """直接创建一条已完成订单（绕过状态机，供评价测试使用）。"""
    return make_order(
        customer,
        service=service,
        provider=provider,
        status=Order.Status.COMPLETED,
        **kwargs,
    )


def make_wallet(user, balance=0, frozen_amount=0):
    """设置钱包余额（单位：分）。

    用户创建时已由 post_save signal 自动建空钱包，这里更新其余额，
    避免与 OneToOne 唯一约束冲突。
    """
    wallet, _ = Wallet.objects.update_or_create(
        user=user,
        defaults={'balance': balance, 'frozen_amount': frozen_amount},
    )
    return wallet


def make_evaluation(order, customer=None, provider=None, score=5, **kwargs):
    """创建评价。customer / provider 默认取订单上的值；三维分缺省回落综合分。"""
    kwargs.setdefault('skill_score', score)
    kwargs.setdefault('attitude_score', score)
    kwargs.setdefault('communication_score', score)
    return Evaluation.objects.create(
        order=order,
        customer=customer or order.customer,
        provider=provider or order.provider,
        score=score,
        **kwargs,
    )


def make_coupon(
    name='满减券',
    discount_type=Coupon.DiscountType.THRESHOLD,
    threshold=10000,
    amount=2000,
    valid_to=None,
    total_qty=0,
    **kwargs,
):
    """创建优惠券模板（金额单位：分）。

    valid_to 默认未来 7 天；total_qty=0 表示不限量。
    """
    n = _next()
    fields = {
        'name': f'{name}{n}',
        'discount_type': discount_type,
        'threshold': threshold,
        'amount': amount,
        'valid_to': valid_to or (timezone.now() + timedelta(days=7)),
        'total_qty': total_qty,
    }
    fields.update(kwargs)
    return Coupon.objects.create(**fields)


def make_user_coupon(user, coupon=None, status=UserCoupon.Status.UNUSED, **kwargs):
    """为用户领取一张券。coupon 缺省时自动创建。"""
    coupon = coupon or make_coupon()
    return UserCoupon.objects.create(
        user=user,
        coupon=coupon,
        status=status,
        **kwargs,
    )


def make_achievement(
    code=None,
    title='初出茅庐',
    metric=Achievement.Metric.ORDERS,
    target=1,
    **kwargs,
):
    """创建老板成就配置。target：orders 维度为单数，amount 维度为分。"""
    n = _next()
    fields = {
        'code': code or f'ach{n}',
        'title': title,
        'metric': metric,
        'target': target,
    }
    fields.update(kwargs)
    return Achievement.objects.create(**fields)


def make_boss_type(name=None, discount_rate=100, **kwargs):
    """创建老板分级（discount_rate 单位 %，100=原价）。"""
    n = _next()
    return BossType.objects.create(
        name=name or f'分级{n}', discount_rate=discount_rate, **kwargs
    )


def make_escort_level(name=None, commission_rate=20, **kwargs):
    """创建陪玩等级（commission_rate 单位 %）。"""
    n = _next()
    return EscortLevel.objects.create(
        name=name or f'等级{n}', commission_rate=commission_rate, **kwargs
    )


def make_promotion(
    title=None,
    discount_rate=None,
    commission_rate=None,
    scope=Promotion.Scope.ALL,
    start_at=None,
    end_at=None,
    priority=0,
    items=None,
    **kwargs,
):
    """创建促销活动。

    discount_rate：活动折扣率(%)，None 表示不影响实付。
    commission_rate：覆盖抽成率(%)，None 表示不覆盖抽成。
    时间窗默认覆盖当前（昨天~明天）。items 为 ServiceItem 列表（scope=ITEMS 时关联）。
    """
    n = _next()
    now = timezone.now()
    fields = {
        'title': title or f'活动{n}',
        'discount_rate': discount_rate,
        'commission_rate': commission_rate,
        'scope': scope,
        'start_at': start_at or (now - timedelta(days=1)),
        'end_at': end_at or (now + timedelta(days=1)),
        'priority': priority,
    }
    fields.update(kwargs)
    promo = Promotion.objects.create(**fields)
    if items:
        promo.items.set(items)
    return promo


__all__ = [
    'User',
    'Transaction',
    'make_user',
    'make_superuser',
    'make_console_user',
    'make_escort_profile',
    'make_provider',
    'make_service',
    'make_service_category',
    'make_order',
    'make_completed_order',
    'make_wallet',
    'make_evaluation',
    'make_coupon',
    'make_user_coupon',
    'make_achievement',
    'make_boss_type',
    'make_escort_level',
    'make_promotion',
]
