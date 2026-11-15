"""Locked source-ledger operations for the new boss payment flow.

The caller must include these operations, wallet debit/refund, transaction
records and order changes in one outer store transaction. Service payment
entry points use them when the corresponding funding flow is enabled.
"""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from wallet.models import RechargeRecord, Wallet
from .funding_plan import (AvailablePart, FundingPlanError, RefundablePart,
                           plan_payment, plan_refund)
from .models import ClubOperationConfig, OrderFundingAllocation, PointFundingLot, VipTier
from .services import _funding_lot_parts, config, locked_profile, wallet_funding_parts


def parse_funding_selection(data):
    values = []
    for key in ('gift_balance_amount', 'compensation_balance_amount'):
        value = data.get(key, 0)
        if isinstance(value, str) and len(value) <= 10 and value.isascii() and value.isdigit():
            value = int(value)
        if type(value) is not int or value < 0 or value > 2147483647 or value % 10:
            raise ValidationError('赠送和赔付金额须为非负整数店币')
        values.append(value)
    if any(values) and not getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False):
        raise ValidationError('赠送和赔付余额选择暂未开放')
    return tuple(values)


def funding_quote(wallet, amount, gift, compensation):
    if gift + compensation > amount:
        raise ValidationError('赠送和赔付不能超过应付金额')
    enabled = bool(getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False) and amount > 0)
    if enabled:
        if amount % 10:
            raise ValidationError('订单金额不是整数店币，请联系门店核对价格')
        if wallet:
            tracked = sum(PointFundingLot.objects.filter(account_id=wallet.account_id,
                          remaining__gt=0).values_list('remaining', flat=True))
            if not wallet.account_id or tracked != wallet.balance:
                raise ValidationError('钱包余额与资金来源不一致，请联系门店核对')
        balances = wallet_funding_parts(wallet) if wallet else {
            'prepaid_balance': 0, 'gift_balance': 0, 'compensation_balance': 0}
        sufficient = bool(wallet and wallet.is_active and balances['gift_balance'] >= gift
                          and balances['compensation_balance'] >= compensation
                          and balances['prepaid_balance'] >= amount - gift - compensation)
    else:
        if wallet:
            tracked = sum(PointFundingLot.objects.filter(account_id=wallet.account_id,
                          remaining__gt=0).values_list('remaining', flat=True))
            if not wallet.account_id or tracked != wallet.balance:
                raise ValidationError('钱包余额与资金来源不一致，请联系门店核对')
        balances = wallet_funding_parts(wallet) if wallet else {
            'prepaid_balance': 0, 'gift_balance': 0, 'compensation_balance': 0}
        sufficient = bool(wallet and wallet.is_active and balances['prepaid_balance'] >= amount)
    return {'funding_selection_enabled': enabled, 'balance_sufficient': sufficient, **balances}


def _in_transaction():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('资金来源变更必须与钱包及订单处于同一事务')


def _owned_wallet(order, wallet):
    if not order.pk or not order.customer_account_id or wallet.account_id != order.customer_account_id:
        raise ValidationError('订单和钱包的所属账号不一致')
    return Wallet.objects.select_for_update().get(pk=wallet.pk, account_id=order.customer_account_id)


def validate_order_funding_sources(order, rows):
    """Check every original source, including ones not used by this refund."""
    for row in rows:
        lot = row.lot
        allowed_kinds = ({'PREPAID', 'GIFT'} if lot.kind == PointFundingLot.Kind.RECHARGE
                         else {lot.kind} if lot.kind in OrderFundingAllocation.Kind.values else set())
        if (row.order_id != order.pk or lot.account_id != order.customer_account_id
                or row.kind not in allowed_kinds or row.amount <= 0
                or row.refunded_amount > row.amount
                or (lot.kind == PointFundingLot.Kind.RECHARGE and lot.gift_remaining is None)):
            raise ValidationError('订单资金来源类别或归属不一致，请先核对')


def _recharge_gifts(lots):
    ids = []
    for lot in lots:
        if lot.kind == PointFundingLot.Kind.RECHARGE:
            try:
                ids.append(int(lot.source_key.split(':')[1]))
            except (IndexError, ValueError):
                continue
    return {f'recharge:{pk}': gift for pk, gift in
            RechargeRecord.objects.filter(pk__in=ids).values_list('pk', 'gift_amount')}


def snapshot_payment_funding_terms(account_id, paid_at):
    """Freeze the buyer's membership and switch state inside a payment transaction."""
    from club_accounts.models import ClubAccount
    from users.models import BossType
    _in_transaction()
    config()  # Establish the singleton before locking it.
    operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
    account = ClubAccount.objects.select_for_update().get(pk=account_id)
    profile = locked_profile(account)
    tier = None
    if account.boss_type_id:
        boss_type = BossType.objects.select_for_update().filter(pk=account.boss_type_id, is_active=True).first()
        if boss_type:
            tier = VipTier.objects.select_for_update().filter(boss_type_id=boss_type.pk, active=True).first()
    rate = tier.points_rate if tier else operation.base_points_rate
    if type(rate) is not int or not 0 <= rate <= 1000:
        raise ValidationError('门店会员积分倍率无效，请联系管理员核对')
    day = timezone.localtime(paid_at).date()
    birthday = profile.birthday
    from .vip_policy import birthday_points_multiplier
    birthday_multiplier = birthday_points_multiplier(operation, tier, birthday, day)
    return {'vip_enabled': operation.vip_enabled, 'points_enabled': operation.points_enabled,
        'vip_tier_id': tier.pk if tier else None, 'vip_level': tier.level if tier else None,
        'points_rate': rate, 'birthday_multiplier': birthday_multiplier}


def snapshot_service_funding_terms(order, *, paid_at):
    """Freeze membership terms using the actual payment event's time."""
    _in_transaction()
    if not order.funding_allocations.exists():
        raise ValidationError('订单未记录资金来源')
    order.funding_terms = snapshot_payment_funding_terms(order.customer_account_id, paid_at)
    order.save(update_fields=['funding_terms'])


def allocate_locked_order_funding(order, wallet, *, gift=0, compensation=0, points_checkout=False):
    """Record and deduct sources while the caller holds the enclosing txn.

    Wallet remains unchanged: the caller must debit it before committing.
    A failed plan raises and rolls back its savepoint. Unknown funds are never
    inferred to be principal because that would understate the store liability.
    """
    _in_transaction()
    if getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False):
        from orders.points_checkout_test import ordinary_settlement_enabled, require_isolated_checkout
        if points_checkout:
            from orders.points_business_access import bind_checkout_order, permits
            if permits(account=order.customer_account, service=order.service,
                       phase='checkout', kind='service'):
                bind_checkout_order(order, order.customer_account, order.service)
            else:
                require_isolated_checkout()
        elif not ordinary_settlement_enabled():
            raise ValidationError('订单资金结算尚未开放，请联系门店')
    with transaction.atomic():
        wallet = _owned_wallet(order, wallet)
        if not wallet.is_active or order.amount <= 0 or wallet.balance < order.amount:
            raise ValidationError('钱包不可用或余额不足')
        if order.funding_allocations.exists() or order.point_allocations.exists():
            raise ValidationError('订单资金来源已记录，请勿重复扣款')
        lots = list(PointFundingLot.objects.select_for_update().filter(
            account_id=order.customer_account_id, remaining__gt=0).order_by('id'))
        tracked = sum(lot.remaining for lot in lots)
        if tracked != wallet.balance:
            raise ValidationError('钱包余额与资金来源不一致，请先核对')
        gifts = _recharge_gifts(lots)
        balances = {lot.pk: _funding_lot_parts(lot, gifts) for lot in lots}
        by_key = {'PREPAID': 'prepaid_balance', 'GIFT': 'gift_balance', 'COMPENSATION': 'compensation_balance'}
        parts = [AvailablePart(lot.pk, kind, balances[lot.pk][key]) for lot in lots
                 for kind, key in by_key.items() if balances[lot.pk][key]]
        try:
            plan = plan_payment(parts, order.amount, gift=gift, compensation=compensation)
        except FundingPlanError as exc:
            raise ValidationError(str(exc)) from exc
        selected = {}
        for part in plan:
            values = selected.setdefault(part.lot_id, {'total': 0, 'gift': 0})
            values['total'] += part.amount
            if part.kind == 'GIFT':
                values['gift'] += part.amount
        for lot in lots:
            amounts = selected.get(lot.pk)
            if not amounts:
                continue
            fields = ['remaining']
            if lot.kind == PointFundingLot.Kind.RECHARGE:
                lot.gift_remaining = balances[lot.pk]['gift_balance'] - amounts['gift']
                fields.append('gift_remaining')
            lot.remaining -= amounts['total']
            lot.save(update_fields=fields)
        OrderFundingAllocation.objects.bulk_create([
            OrderFundingAllocation(order=order, lot_id=part.lot_id, kind=part.kind, amount=part.amount)
            for part in plan
        ])
        return plan


def refund_locked_order_funding(order, wallet, amount):
    """Restore this order's original source lots; caller credits the wallet."""
    _in_transaction()
    with transaction.atomic():
        _owned_wallet(order, wallet)
        rows = list(order.funding_allocations.select_for_update().order_by('id'))
        if not rows or sum(row.amount for row in rows) != order.amount:
            raise ValidationError('订单资金来源不完整，请先核对')
        try:
            targets = plan_refund([RefundablePart(row.pk, row.kind, row.amount, row.refunded_amount)
                                   for row in rows], amount)
        except FundingPlanError as exc:
            raise ValidationError(str(exc)) from exc
        lots = {lot.pk: lot for lot in PointFundingLot.objects.select_for_update().filter(
            pk__in={row.lot_id for row in rows}, account_id=order.customer_account_id).order_by('id')}
        if len(lots) != len({row.lot_id for row in rows}):
            raise ValidationError('原资金来源不属于订单账号，请先核对')
        # Validate every original allocation against the locked current lot,
        # not just sources consumed by this particular partial refund. Mixed
        # recharge lots can supply only prepaid or gift, never compensation.
        for row in rows:
            row.lot = lots[row.lot_id]
        validate_order_funding_sources(order, rows)
        for row in rows:
            delta = targets[row.pk]
            if not delta:
                continue
            lot = lots[row.lot_id]
            fields = ['remaining']
            if lot.kind == PointFundingLot.Kind.RECHARGE:
                if lot.gift_remaining is None:
                    raise ValidationError('历史混合资金来源缺少快照，请先核对')
                if row.kind == 'GIFT':
                    lot.gift_remaining += delta
                    fields.append('gift_remaining')
            elif lot.kind != row.kind:
                raise ValidationError('资金来源类型已变化，请先核对')
            lot.remaining += delta
            lot.save(update_fields=fields)
            row.refunded_amount += delta
            row.save(update_fields=['refunded_amount'])
        return targets
