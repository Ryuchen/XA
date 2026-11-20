import hashlib
import json
import secrets
import string
from datetime import timedelta
from stores.db import transaction
from django.db.models import Q, Sum, F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.license_benefits import LicenseFeatureDenied, skip_denied_benefit
from .models import (
    Broadcast, BroadcastRule, ClubOperationConfig, LotteryCampaign, LotteryDraw,
    LotteryPrize, LotteryBatch, LotteryPityState, MemberProfile, MemberTag, PointLedger, RewardClaim, RewardItem,
    VipTier, MemberBenefitGrant, LicenseAddonBenefitsReview,
    RechargePackage, PointFundingLot, OrderPointAllocation, GiftBenefitRule,
)


DEFAULT_BROADCASTS = {
    'NAMING': '{nickname} 获得了冠名权益「{reward}」',
    'RECHARGE': '{nickname} 充值成功，感谢对俱乐部的支持',
    'UPGRADE': '恭喜 {nickname} 升级为 {level}',
    'REVIEW': '{nickname} 给 {provider} 送上了好评',
    'BIRTHDAY': '今天是 {nickname} 的生日，声浪电竞祝你生日快乐！',
    'GIFT': '{nickname} 送给 {provider}「{reward}」',
    'LOTTERY': '恭喜 {nickname} 抽中「{reward}」',
    'LOTTERY_SERVICE': '恭喜 {nickname} 抽中「{reward}」',
    'LOTTERY_NAMING': '恭喜 {nickname} 抽中「{reward}」',
    'LOTTERY_TAG': '恭喜 {nickname} 抽中「{reward}」',
}
BROADCAST_FIELDS = {'nickname', 'reward', 'level', 'provider'}


def validate_template(template):
    try:
        for _, field, spec, conversion in string.Formatter().parse(template):
            if field is not None and (field not in BROADCAST_FIELDS or spec or conversion):
                raise ValueError()
    except ValueError:
        raise ValidationError('模板仅支持 {nickname}、{reward}、{level}、{provider} 占位符') from None


def broadcast(kind, event_key, *, account=None, anonymous=False, **context):
    existing = Broadcast.objects.filter(event_key=event_key).first()
    if existing:
        return existing
    from stores.license_benefits import check_new_benefits
    if check_new_benefits('CONTENT_OPERATIONS')[0] != 'ALLOW':
        return None
    from stores.conf import store_name
    template = DEFAULT_BROADCASTS[kind].replace('声浪电竞', store_name().replace('{', '{{').replace('}', '}}'))
    rule, _ = BroadcastRule.objects.get_or_create(kind=kind, defaults={'template': template})
    if not rule.enabled:
        return None
    nickname = (account.nickname or account.account_no) if account else '俱乐部老板'
    if anonymous:
        nickname = '一位老板'
    elif rule.mask_nickname:
        nickname = nickname[:1] + '**'
    values = {key: str(context.get(key, ''))[:100] for key in BROADCAST_FIELDS}
    values['nickname'] = nickname
    from stores.display import provider_display_name
    values['provider'] = provider_display_name(values['provider'])
    validate_template(rule.template)
    now = timezone.now()
    row, created = Broadcast.objects.get_or_create(event_key=event_key, defaults={
        'kind': kind, 'account': account, 'content': rule.template.format(**values)[:255],
        'starts_at': now, 'expires_at': now + timedelta(hours=rule.visible_hours),
        'sort_order': rule.sort_order,
    })
    if created:
        # Durable outbox: only committed business events can enter the queue.
        from .tasks import enqueue_broadcast
        transaction.on_commit(lambda: enqueue_broadcast(row.pk))
    return row


def config():
    return ClubOperationConfig.objects.get_or_create(pk=1)[0]


def locked_profile(account):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('积分写入必须在事务内')
    MemberProfile.objects.get_or_create(account=account)
    return MemberProfile.objects.select_for_update().get(account=account)


def change_points(profile, amount, key, kind, remark, *, order=None, operator=None, allow_debt=False):
    existing = PointLedger.objects.filter(event_key=key).first()
    if existing:
        if existing.account_id != profile.account_id or existing.amount != amount:
            raise ValidationError('积分事件内容与原记录不一致')
        return existing
    balance = profile.points_balance + amount
    if balance < 0 and amount < 0 and not allow_debt:
        raise ValidationError('积分不足，请先累积积分')
    if abs(balance) > 9_000_000_000_000_000:
        raise ValidationError('积分余额超过系统范围')
    profile.points_balance = balance
    profile.save(update_fields=['points_balance', 'updated_at'])
    ledger = PointLedger.objects.create(account=profile.account, event_key=key, kind=kind,
        amount=amount, balance_after=balance, remark=remark, order=order, operator=operator)
    from .notifier import notify_member_points_after_commit
    notify_member_points_after_commit(profile.account_id)
    return ledger


def _award_license_checked_points(profile, points, key, remark, order):
    """Persist the decision alongside the ledger, so renewal cannot back-award."""
    from django.conf import settings
    from stores.certificate_admission import enabled as certificate_enforcement_enabled
    if not getattr(settings, 'LICENSE_BENEFIT_ENFORCEMENT_ENABLED', True) and not certificate_enforcement_enabled():
        return change_points(profile, points, key, 'CONSUMPTION', remark, order=order)
    existing = PointLedger.objects.filter(event_key=key).first()
    if existing:
        return existing
    from .models import LicenseConsumptionPointsDecision
    decision = LicenseConsumptionPointsDecision.objects.filter(order=order).first()
    if decision is None:
        from stores.license_benefits import check_new_benefits
        outcome, evidence = check_new_benefits()
        decision, _ = LicenseConsumptionPointsDecision.objects.get_or_create(order=order, defaults={
            'account': profile.account, 'event_key': key, 'points_amount': points,
            'outcome': outcome, 'license_evidence': evidence,
        })
    if decision.account_id != profile.account_id or decision.event_key != key:
        raise ValidationError('消费积分授权记录与订单不一致，请联系平台核对')
    if decision.outcome != 'ALLOW':
        return None
    if decision.points_amount != points:
        raise ValidationError('消费积分授权金额已变化，请联系平台核对')
    return change_points(profile, points, key, 'CONSUMPTION', remark, order=order)


def license_allows_recorded_points(order, expected_points):
    """Check immutable evidence without re-evaluating a later license state."""
    from .models import LicenseConsumptionPointsDecision
    decision = LicenseConsumptionPointsDecision.objects.filter(order=order).first()
    if decision is None:
        return True  # Enforcement was not enabled for this decision.
    if (decision.account_id != order.customer_account_id or decision.event_key != f'order:{order.pk}:points'
            or decision.points_amount != expected_points):
        raise ValidationError('消费积分授权记录与订单不一致，请联系平台核对')
    return decision.outcome == 'ALLOW'


def award_order_points(order, *, service_confirmed=False, operation=None):
    if not order.customer_account_id or order.amount <= 0:
        return
    if not order.is_gift_snapshot and not service_confirmed:
        return
    # Confirmation callers already hold this row after their other business
    # locks. Reuse that current read instead of an older transaction snapshot.
    operation = operation if operation is not None else config()
    if not operation.points_enabled:
        return
    if order.funding_terms is not None:
        # The new service flow must wait for actual consumption confirmation;
        # a provider share settling earlier is not a points-award event.
        if not service_confirmed or not order.funding_terms['vip_enabled'] or not order.funding_terms['points_enabled']:
            return
        if not operation.vip_enabled or not operation.points_enabled:
            return
        if order.customer_account.business_cycle != order.business_cycle:
            return
        profile = locked_profile(order.customer_account)
        key = f'order:{order.pk}:points'
        if PointLedger.objects.filter(event_key=key).exists():
            return
        from .models import OrderFundingAllocation
        principal = OrderFundingAllocation.objects.filter(order=order, kind='PREPAID').aggregate(
            amount=Sum('amount'), refunded=Sum('refunded_amount'))
        net = (principal['amount'] or 0) - (principal['refunded'] or 0)
        terms = order.funding_terms
        points = net * terms['points_rate'] * terms['birthday_multiplier'] // 100
        if points:
            _award_license_checked_points(profile, points, key, '订单本金消费积分（按付款时会员及生日倍率）', order)
        return
    # Gifts are confirmed immediately at payment; service orders need their
    # own payment-time switch snapshot and retain their existing path for now.
    if (order.is_gift_snapshot and (order.gift_vip_enabled_snapshot is False
            or (order.gift_vip_enabled_snapshot is None and not operation.vip_enabled))):
        return
    if order.customer_account.business_cycle != order.business_cycle:
        return
    profile = locked_profile(order.customer_account)
    key = f'order:{order.pk}:points'
    if PointLedger.objects.filter(event_key=key).exists():
        return
    snapshot = getattr(order, 'default_points_snapshot', None) if not order.is_gift_snapshot else None
    if snapshot is not None:
        if not snapshot.vip_enabled or not operation.vip_enabled:
            return
        from .default_funding_refunds import verified_sources
        sources = verified_sources(order, snapshot)
        principal = sum(row.amount for row in sources if row.kind == 'PREPAID')
        refunded = sum(row.refunded_amount for row in sources if row.kind == 'PREPAID')
        points = (principal - refunded) * snapshot.points_rate * snapshot.birthday_multiplier // 100
        if not snapshot.points_enabled:
            points = 0
        snapshot.awarded_refunded_principal = refunded
        snapshot.save(update_fields=['awarded_refunded_principal'])
    else:
        if not order.is_gift_snapshot:
            return
        allocations = list(order.point_allocations.filter(released=False))
        points = sum(row.points_amount for row in allocations) if allocations else (order.amount if operation.points_enabled else 0)
    if not points:
        return
    remark = ('礼物消费积分（按付款时会员及生日倍率）' if order.is_gift_snapshot and order.gift_points_snapshot
              else '订单消费积分（按支付时余额批次及生日倍率）')
    _award_license_checked_points(profile, points, key, remark, order)


def _funding_lot_parts(lot, recharge_gifts):
    """把一个资金批次的剩余额拆成三种余额，不改写历史退款分配。"""
    parts = {'prepaid_balance': 0, 'gift_balance': 0, 'compensation_balance': 0}
    if lot.kind == PointFundingLot.Kind.RECHARGE:
        # 同一充值批次沿用 FIFO，并约定先抵实充、再抵赠送。历史批次也按
        # 这一固定口径还原，remaining 始终能拆回两个非负部分。
        gift = (lot.gift_remaining if lot.gift_remaining is not None
                else min(recharge_gifts.get(lot.source_key, 0), lot.remaining))
        parts['prepaid_balance'] = lot.remaining - gift
        parts['gift_balance'] = gift
    else:
        key = {
            PointFundingLot.Kind.PREPAID: 'prepaid_balance',
            PointFundingLot.Kind.GIFT: 'gift_balance',
            PointFundingLot.Kind.COMPENSATION: 'compensation_balance',
        }.get(lot.kind, 'prepaid_balance')
        parts[key] = lot.remaining
    return parts


def _decrease_funding_lot(lot, amount):
    """Legacy FIFO consumes principal before gift on an initialized lot."""
    fields = ['remaining']
    if lot.gift_remaining is not None:
        gift_used = max(0, amount - (lot.remaining - lot.gift_remaining))
        lot.gift_remaining -= gift_used
        fields.append('gift_remaining')
    lot.remaining -= amount
    lot.save(update_fields=fields)


def _restore_legacy_funding_lot(lot, amount, gift_amount=0):
    """Restore known gift; unknown historical source is treated as principal."""
    fields = ['remaining']
    if gift_amount:
        if lot.gift_remaining is None or gift_amount > amount:
            raise ValidationError('历史退款资金来源不一致，请先核对')
        lot.gift_remaining += gift_amount
        fields.append('gift_remaining')
    lot.remaining += amount
    lot.save(update_fields=fields)


def wallet_funding_parts(wallet):
    """返回与钱包可用余额严格相加相等的预存、赠送和赔款余额。

    支付仍由 ``allocate_order_points`` 按批次 FIFO 抵扣，退款仍由
    ``release_order_allocations`` 回冲原批次。本函数只读取同一账本；早期未建
    批次的余额作为预存处理， tracked 超过余额时按既有顺序先行冲减展示。
    """
    empty = {'prepaid_balance': 0, 'gift_balance': 0, 'compensation_balance': 0}
    if not wallet.account_id:
        return {**empty, 'prepaid_balance': wallet.balance}
    lots = list(PointFundingLot.objects.filter(account_id=wallet.account_id, remaining__gt=0).order_by('id'))
    recharge_ids = []
    for lot in lots:
        if lot.kind == PointFundingLot.Kind.RECHARGE:
            try:
                recharge_ids.append(int(lot.source_key.split(':')[1]))
            except (IndexError, ValueError):
                continue
    from wallet.models import RechargeRecord
    recharge_gifts = {
        f'recharge:{pk}': gift for pk, gift in
        RechargeRecord.objects.filter(pk__in=recharge_ids).values_list('pk', 'gift_amount')
    }
    rows = [_funding_lot_parts(lot, recharge_gifts) for lot in lots]
    difference = wallet.balance - sum(lot.remaining for lot in lots)
    if difference > 0:
        rows.append({**empty, 'prepaid_balance': difference})
    elif difference < 0:
        excess = -difference
        for parts in rows:
            for key in ('prepaid_balance', 'gift_balance', 'compensation_balance'):
                removed = min(excess, parts[key])
                parts[key] -= removed
                excess -= removed
            if not excess:
                break
    return {key: sum(parts[key] for parts in rows) for key in empty}


def record_wallet_adjustment(*, wallet, tx, kind):
    """把后台调账写入既有 FIFO 资金批次，与支付和退款共用一本账。"""
    valid = {PointFundingLot.Kind.PREPAID, PointFundingLot.Kind.GIFT, PointFundingLot.Kind.COMPENSATION}
    if kind not in valid:
        raise ValueError('请选择有效的资金类型')
    if tx.amount > 0:
        if not wallet.account_id:
            if kind != PointFundingLot.Kind.PREPAID:
                raise ValueError('该旧账号尚未关联门店账户，暂不能登记赠送或赔款')
            return  # 旧账号无批次时由 wallet_funding_parts 作为预存读取。
        PointFundingLot.objects.create(
            account_id=wallet.account_id, source_key=f'wallet-adjust:{tx.pk}',
            remaining=tx.amount, kind=kind, points_rate=100,
        )
        return
    # 负向调账不是新增一种资金来源，按既有批次顺序冲减；未被批次覆盖的
    # 历史预存余额由钱包总额自然减少，无需虚构一条批次。
    left = -tx.amount
    lots = PointFundingLot.objects.select_for_update().filter(
        account_id=wallet.account_id, remaining__gt=0,
    ).order_by('id')
    for lot in lots:
        removed = min(left, lot.remaining)
        _decrease_funding_lot(lot, removed)
        left -= removed
        if not left:
            break


def allocate_order_points(order, wallet, balance_before, *, principal_only=False):
    """调用时已锁钱包。FIFO 记录消费来源，只有履约完成才真正发积分。"""
    if not order.customer_account_id or order.point_allocations.exists() or order.amount <= 0:
        return
    # In the default checkout the buyer has not selected gift or compensation.
    # Those balances must not be silently consumed with the principal.
    if not order.is_gift_snapshot:
        principal_only = True
    from club_accounts.models import ClubAccount
    # The wallet is already locked. Read the current account and tier before
    # freezing the multiplier, rather than using a stale authenticated object.
    customer = ClubAccount.objects.select_for_update().get(pk=order.customer_account_id)
    gift_account = customer if order.is_gift_snapshot and principal_only and order.gift_vip_enabled_snapshot is not None else None
    profile = locked_profile(order.customer_account)
    lots = list(PointFundingLot.objects.select_for_update().filter(account_id=order.customer_account_id, remaining__gt=0).order_by('id'))
    tracked = sum(lot.remaining for lot in lots)
    if not order.is_gift_snapshot and tracked != balance_before:
        raise ValidationError('钱包余额与资金来源不一致，请先核对')
    if not order.is_gift_snapshot and any(lot.kind == PointFundingLot.Kind.RECHARGE and lot.gift_remaining is None for lot in lots):
        raise ValidationError('充值资金来源缺少赠送快照，请先核对')
    # Gift checkout retains its own principal-only handling.
    untracked = max(0, balance_before - tracked)
    if untracked:
        old = PointFundingLot.objects.create(account_id=order.customer_account_id,
            source_key=f'wallet:{wallet.pk}:before-order:{order.pk}', remaining=untracked)
        lots.append(old)
    # 管理员调减余额后不再奖励已被扣除的批次。
    excess = max(0, tracked - balance_before)
    for lot in lots:
        if not excess:
            break
        removed = min(excess, lot.remaining)
        _decrease_funding_lot(lot, removed)
        excess -= removed
    day = timezone.localtime(order.created_at).date()
    birthday = profile.birthday
    operation = config()
    tier = None
    if customer.boss_type_id:
        from users.models import BossType
        boss_type = BossType.objects.select_for_update().filter(pk=customer.boss_type_id, is_active=True).first()
        if boss_type:
            tier = VipTier.objects.select_for_update().filter(boss_type_id=boss_type.pk, active=True).first()
    from .vip_policy import birthday_points_multiplier
    birthday_multiplier = birthday_points_multiplier(operation, tier, birthday, day)
    multiplier = birthday_multiplier
    if not operation.points_enabled or (gift_account is not None and not order.gift_vip_enabled_snapshot):
        multiplier = 0
    # Freeze the current member rate in each payment allocation, independently
    # of the rate on the recharge lot that supplied the principal.
    rate = tier.points_rate if tier and operation.vip_enabled else operation.base_points_rate
    if type(rate) is not int or not 0 <= rate <= 1000:
        raise ValidationError('门店会员积分倍率无效，请联系管理员核对')
    if gift_account is not None:
        order.gift_points_snapshot = {'vip_enabled': order.gift_vip_enabled_snapshot,
            'points_enabled': operation.points_enabled, 'vip_tier_id': tier.pk if tier else None,
            'vip_level': tier.level if tier else None, 'points_rate': rate,
            'birthday_multiplier': birthday_multiplier}
        order.save(update_fields=['gift_points_snapshot'])
    ids = []
    for lot in lots:
        if lot.kind == PointFundingLot.Kind.RECHARGE:
            try:
                ids.append(int(lot.source_key.split(':')[1]))
            except (IndexError, ValueError):
                continue
    from wallet.models import RechargeRecord
    recharge_gifts = {f'recharge:{pk}': gift for pk, gift in
                      RechargeRecord.objects.filter(pk__in=ids).values_list('pk', 'gift_amount')}
    snapshot = None
    if not order.is_gift_snapshot:
        from .models import DefaultPaymentPointsSnapshot
        snapshot = DefaultPaymentPointsSnapshot.objects.create(order=order, points_rate=rate,
            birthday_multiplier=multiplier, points_enabled=operation.points_enabled,
            vip_enabled=operation.vip_enabled, vip_tier_id_snapshot=tier.pk if tier else None,
            vip_level_snapshot=tier.level if tier else None)
    left = order.amount
    allocated, previous_points = 0, 0
    for lot in lots:
        principal_available = _funding_lot_parts(lot, recharge_gifts)['prepaid_balance']
        available = principal_available if principal_only else lot.remaining
        amount = min(left, available)
        if amount:
            allocated += min(amount, principal_available)
            cumulative_points = allocated * rate * multiplier // 100
            points, previous_points = cumulative_points - previous_points, cumulative_points
            known_gift = (max(0, amount - (lot.remaining - lot.gift_remaining))
                          if lot.gift_remaining is not None else None)
            allocation = OrderPointAllocation.objects.create(order=order, lot=lot, amount=amount,
                points_amount=points, source_gift_amount=known_gift)
            if snapshot is not None:
                from .models import DefaultPaymentSource
                principal = min(amount, principal_available)
                if principal:
                    DefaultPaymentSource.objects.create(snapshot=snapshot, allocation=allocation,
                        kind='PREPAID', amount=principal)
                remainder = amount - principal
                if remainder:
                    kind = 'COMPENSATION' if lot.kind == PointFundingLot.Kind.COMPENSATION else 'GIFT'
                    DefaultPaymentSource.objects.create(snapshot=snapshot, allocation=allocation,
                        kind=kind, amount=remainder)
            _decrease_funding_lot(lot, amount)
            left -= amount
        if not left:
            break
    if left:
        raise ValidationError('本金余额不足，请联系门店充值')


def release_order_allocations(order,*,refund=None):
    if refund is not None:
        from orders.refunds import refunded_amount,proportional_targets
        rows = list(order.point_allocations.select_for_update().order_by('lot_id'))
        if not rows:
            return
        targets = proportional_targets({row.pk:row.amount for row in rows},refunded_amount(order))
        for allocation in rows:
            delta = targets[allocation.pk]-allocation.refunded_amount
            if delta:
                lot = PointFundingLot.objects.select_for_update().get(pk=allocation.lot_id)
                gift = allocation.source_gift_amount
                prior_gift = max(0, allocation.refunded_amount - (allocation.amount - gift)) if gift is not None else 0
                target_gift = max(0, targets[allocation.pk] - (allocation.amount - gift)) if gift is not None else 0
                _restore_legacy_funding_lot(lot, delta, target_gift - prior_gift)
                allocation.refunded_amount = targets[allocation.pk]
                allocation.released = allocation.refunded_amount == allocation.amount
                allocation.save(update_fields=['refunded_amount','released'])
        return
    for allocation in order.point_allocations.select_for_update().filter(released=False).order_by('lot_id'):
        lot = PointFundingLot.objects.select_for_update().get(pk=allocation.lot_id)
        remaining = allocation.amount - allocation.refunded_amount
        gift = allocation.source_gift_amount
        prior_gift = max(0, allocation.refunded_amount - (allocation.amount - gift)) if gift is not None else 0
        _restore_legacy_funding_lot(lot, remaining, gift - prior_gift if gift is not None else 0)
        allocation.refunded_amount = allocation.amount
        allocation.released = True
        allocation.save(update_fields=['released', 'refunded_amount'])


def reverse_order_points(order,*,refund=None):
    if not order.customer_account_id:
        return
    from .license_points_review import reverse_reviewed_points
    reverse_reviewed_points(order, refund=refund)
    original = PointLedger.objects.filter(event_key=f'order:{order.pk}:points').first()
    if original:
        profile = locked_profile(order.customer_account)
        if refund is not None:
            from orders.refunds import refunded_amount
            if order.funding_terms is not None:
                from .models import OrderFundingAllocation
                principal = OrderFundingAllocation.objects.filter(order=order, kind='PREPAID').aggregate(
                    amount=Sum('amount'), refunded=Sum('refunded_amount'))
                paid_principal = principal['amount'] or 0
                refunded_principal = principal['refunded'] or 0
                baseline = order.funding_award_refunded_principal
                if baseline is None or not 0 <= baseline <= refunded_principal <= paid_principal:
                    raise ValidationError('订单积分冲回依据不完整，请先核对')
                terms = order.funding_terms
                factor = terms['points_rate'] * terms['birthday_multiplier']
                target = min(original.amount, (paid_principal - baseline) * factor // 100
                             - (paid_principal - refunded_principal) * factor // 100)
            elif not order.is_gift_snapshot and hasattr(order, 'default_points_snapshot'):
                from .default_funding_refunds import verified_sources
                snapshot = order.default_points_snapshot
                sources = verified_sources(order, snapshot)
                principal = sum(row.amount for row in sources if row.kind == 'PREPAID')
                refunded = sum(row.refunded_amount for row in sources if row.kind == 'PREPAID')
                baseline = snapshot.awarded_refunded_principal
                if baseline is None or not 0 <= baseline <= refunded <= principal:
                    raise ValidationError('订单积分退款依据不完整，请先核对')
                factor = snapshot.points_rate * snapshot.birthday_multiplier
                target = min(original.amount, (principal - baseline) * factor // 100
                             - (principal - refunded) * factor // 100)
            elif order.is_gift_snapshot and order.gift_points_snapshot:
                terms = order.gift_points_snapshot
                factor = terms['points_rate'] * terms['birthday_multiplier']
                target = min(original.amount, order.amount * factor // 100
                             - (order.amount - refunded_amount(order)) * factor // 100)
            else:
                target = original.amount*refunded_amount(order)//order.amount
            # Add-on refunds use their own CONSUMPTION/REFUND ledger entries
            # on the same order. They must not count as points already
            # reversed from the main service payment.
            refund_key = f'order:{order.pk}:points-refund'
            previous = -(PointLedger.objects.filter(
                Q(event_key=refund_key) | Q(event_key__startswith=refund_key + ':'),
                order=order, kind='REFUND').aggregate(v=Sum('amount'))['v'] or 0)
            delta = target-previous
            if delta > 0:
                key = f'order:{order.pk}:points-refund' if target == original.amount and not previous else f'order:{order.pk}:points-refund:{refund.pk}'
                change_points(profile,-delta,key,'REFUND','订单退款按累计净额冲回消费积分',order=order,allow_debt=True)
            return
        change_points(profile, -original.amount, f'order:{order.pk}:points-refund', 'REFUND',
                      '订单退款冲回原消费积分', order=order, allow_debt=True)


def request_key(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 64 or not all(c.isalnum() or c in '-_' for c in value):
        raise ValidationError('请提供 8–64 位操作凭证 client_request_id')
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reward_wallet(account, reward):
    if reward.kind == RewardItem.Kind.BALANCE:
        from wallet.services import get_wallet
        wallet = get_wallet(account_id=account.pk, for_update=True)
        if not wallet.is_active:
            raise ValidationError('钱包已停用')
        if not reward.balance_amount or wallet.balance + reward.balance_amount > 2147483647:
            raise ValidationError('奖品余额金额配置有误或超过钱包范围')
        return wallet


def reserve_reward(reward):
    if not reward.is_active:
        raise ValidationError('奖品暂未上架')
    if reward.stock is not None:
        if reward.stock < 1:
            raise ValidationError('奖品已兑完，请稍后再试')
        reward.stock -= 1
    reward.redeemed_count += 1
    reward.save(update_fields=['stock', 'redeemed_count'])
    if reward.in_shop:
        from .notifier import notify_reward_catalog_after_commit
        notify_reward_catalog_after_commit()


def create_claim(account, reward, key, *, source, points_spent=0, details=None, request_fingerprint='', profile=None, wallet=None):
    if source == 'REDEMPTION' and reward.kind == RewardItem.Kind.BALANCE:
        raise ValidationError('积分商城不支持直接兑换钱包余额')
    if reward.code == 'lottery-experience-68' and (source != 'LOTTERY' or reward.kind != 'SERVICE' or reward.in_shop):
        raise ValidationError('68 体验单仅作为抽奖专属服务权益交付，不能在积分商城兑换')
    reserve_reward(reward)
    claim = RewardClaim.objects.create(account=account, reward=reward, event_key=key, source=source,
        name_snapshot=reward.name, kind_snapshot=reward.kind, duration_days=reward.duration_days,
        duration_months=reward.duration_months, highlighted=reward.highlighted,
        points_spent=points_spent, details=details or {}, request_fingerprint=request_fingerprint,
        balance_amount=reward.balance_amount)
    from .notifier import notify_claims_after_commit, notify_member_points_after_commit
    notify_claims_after_commit()
    notify_member_points_after_commit(account.pk)
    if reward.kind == RewardItem.Kind.POINTS:
        if reward.point_value < 1:
            raise ValidationError('积分奖品未配置积分值')
        change_points(profile, reward.point_value * 100, key + ':award', 'LOTTERY_AWARD', f'抽奖获得 {reward.name}')
        claim.status = RewardClaim.Status.COMPLETED
    elif reward.kind == RewardItem.Kind.BALANCE:
        from wallet.models import Transaction
        before = wallet.balance
        wallet.balance += reward.balance_amount
        wallet.total_gift += reward.balance_amount
        wallet.save(update_fields=['balance', 'total_gift'])
        source_label = '积分抽奖' if source == 'LOTTERY' else '权益发放'
        Transaction.objects.create(wallet=wallet, amount=reward.balance_amount,
            tx_type=Transaction.TxType.GIFT, balance_before=before, balance_after=wallet.balance,
            remark=f'{source_label}：{reward.name}（权益记录 {claim.pk}）')
        PointFundingLot.objects.get_or_create(
            source_key=f'reward-claim:{claim.pk}',
            defaults={'account': account, 'remaining': reward.balance_amount,
                      'kind': PointFundingLot.Kind.GIFT, 'points_rate': 100},
        )
        from wallet.notifier import notify_wallet_update
        transaction.on_commit(lambda: notify_wallet_update(wallet))
        claim.status = RewardClaim.Status.COMPLETED
    elif reward.code == 'lottery-experience-68':
        # 专属中奖记录由俱乐部履约；不创建任何通用券或海报体验单抵扣券。
        claim.delivery_note = '68 体验单专属中奖权益，请补充游戏资料后由俱乐部安排服务，不作为通用优惠券使用'
        claim.save(update_fields=['delivery_note'])
    if claim.status == RewardClaim.Status.COMPLETED:
        claim.completed_at = timezone.now()
        claim.save(update_fields=['status', 'completed_at', 'delivery_note'])
    return claim


class RewardPriceChanged(Exception):
    pass


class RewardTermsChanged(Exception):
    pass


class RewardUnavailable(ValidationError):
    def __init__(self, message):
        self.message_text = message
        super().__init__(message)


def _provider_redemption_lifecycle(fn):
    """Serialize provider point/entitlement writes before reward or wallet locks."""
    from functools import wraps

    @wraps(fn)
    def wrapped(account, *args, **kwargs):
        from django.conf import settings
        from club_accounts.models import ClubAccount
        if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                or account.account_type != ClubAccount.AccountType.PROVIDER):
            return fn(account, *args, **kwargs)
        from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
            lock_provider_operation, provider_membership_current)
        try:
            ensure_provider_lifecycle(account)
            with transaction.atomic():
                lock_provider_operation(account)
                same_cycle = ClubAccount.objects.filter(pk=account.pk,
                    account_type=ClubAccount.AccountType.PROVIDER, license_membership_active=True,
                    business_cycle=account.business_cycle).exists()
                if not same_cycle:
                    raise ValidationError('陪玩账号状态或业务周期已变化，请重新登录')
                if not provider_membership_current(account):
                    key = args[1] if len(args) > 1 else kwargs.get('client_request_id')
                    event_key = f'redeem:{account.pk}:{account.business_cycle}:{request_key(key)}'
                    if not RewardClaim.objects.filter(account=account, event_key=event_key).exists():
                        raise ValidationError('陪玩账号当前不可发起新的积分兑换')
                return fn(account, *args, **kwargs)
        except ProviderLifecycleError as exc:
            raise ValidationError('陪玩账号状态暂不可核验，请稍后重试') from exc
    return wrapped


def _lock_points_purchase_context(account, feature):
    """Acquire the common wallet -> VIP configuration -> account order."""
    from club_accounts.models import ClubAccount
    from stores.context import current_database, current_store
    from stores.license_benefits import LicenseDecisionUnavailable, LicenseFeatureDenied, check_new_benefits
    from wallet.services import get_wallet
    store = current_store()
    if store is None or account._state.db != current_database():
        raise ValidationError('无法确定账号所属门店，请重新登录')
    identity = dict(pk=account.pk, account_type=account.account_type,
                    business_cycle=account.business_cycle, license_membership_active=True,
                    is_active=True, can_login=True)
    if not ClubAccount.objects.filter(**identity).exists():
        raise ValidationError('账号状态或业务周期已变化，请重新登录')
    outcome, _ = check_new_benefits(feature)
    if outcome == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if outcome != 'ALLOW':
        raise LicenseFeatureDenied('门店授权暂不允许此项积分权益，请联系平台')
    get_wallet(account_id=account.pk, for_update=True)
    config()
    ClubOperationConfig.objects.select_for_update().get(pk=1)
    if not ClubAccount.objects.select_for_update().filter(**identity).exists():
        raise ValidationError('账号状态或业务周期已变化，请重新登录')



@_provider_redemption_lifecycle
@transaction.atomic
def redeem(account, reward_id, client_request_id, details=None, expected_point_cost=None, expected_terms=None):
    key = f'redeem:{account.pk}:{account.business_cycle}:{request_key(client_request_id)}'
    request_data = {'reward_id': reward_id, 'details': details or {}}
    if expected_point_cost is not None:
        request_data['expected_point_cost'] = expected_point_cost
    if expected_terms is not None:
        request_data['expected_terms'] = expected_terms
    fp = fingerprint(request_data)
    existing = RewardClaim.objects.filter(event_key=key).first()
    if existing:
        if existing.request_fingerprint != fp:
            raise ValidationError('同一操作凭证不能兑换不同内容')
        return existing
    _lock_points_purchase_context(account, 'POINTS_SHOP')
    reward = RewardItem.objects.select_for_update().filter(pk=reward_id).first()
    if reward is None:
        raise RewardUnavailable('商品已不存在，请查看其他权益')
    # Another request with the same key may have completed while this one waited for the item lock.
    existing = RewardClaim.objects.select_for_update().filter(event_key=key).first()
    if existing:
        if existing.request_fingerprint != fp:
            raise ValidationError('同一操作凭证不能兑换不同内容')
        return existing
    if not reward.is_active or not reward.in_shop:
        raise RewardUnavailable('商品已下架，请查看其他权益')
    if reward.kind == RewardItem.Kind.BALANCE:
        raise RewardUnavailable('积分商城不支持直接兑换钱包余额')
    if reward.audience not in ['ALL',account.account_type]:
        raise RewardUnavailable('商品已调整适用身份，请查看其他权益')
    if reward.stock is not None and reward.stock < 1:
        raise RewardUnavailable('商品已兑完，请查看其他权益')
    wallet = reward_wallet(account, reward)
    profile = locked_profile(account)
    existing = RewardClaim.objects.select_for_update().filter(event_key=key).first()
    if existing:
        if existing.request_fingerprint != fp:
            raise ValidationError('同一操作凭证不能兑换不同内容')
        return existing
    from stores.license_benefits import LicenseDecisionUnavailable, LicenseFeatureDenied, check_new_benefits
    admission, _ = check_new_benefits('POINTS_SHOP')
    if admission == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if admission != 'ALLOW':
        raise LicenseFeatureDenied('门店授权暂不允许新的积分商城兑换，请联系平台')
    from .points_usage import assert_usage_allowed
    assert_usage_allowed()
    if expected_point_cost is not None and reward.point_cost != expected_point_cost:
        raise RewardPriceChanged
    if expected_terms is not None:
        terms = {key:getattr(reward,key) for key in ('name','kind','description','balance_amount','point_value','duration_days','duration_months','highlighted','service_id')}
        if expected_terms != terms:
            raise RewardTermsChanged
    if reward.point_cost <= 0:
        raise ValidationError('奖品尚未配置兑换积分')
    change_points(profile, -reward.point_cost * 100, key, 'REDEMPTION', f'兑换 {reward.name}')
    return create_claim(account, reward, key, source='REDEMPTION', points_spent=reward.point_cost,
                        details=details, request_fingerprint=fp, profile=profile, wallet=wallet)


def validate_campaign(campaign, prizes):
    if campaign.point_cost < 1:
        raise ValidationError('请先配置每次抽奖消耗的积分')
    if not prizes or sum(p.probability for p in prizes) != 10000:
        raise ValidationError('所有奖项概率之和必须为 10000（100%）')
    target = lottery_pity_target(campaign, prizes)
    if campaign.pity_enabled and campaign.pity_target_reward_id and target is None:
        raise ValidationError('保底目标必须是本活动的奖品')
    if campaign.pity_enabled and campaign.pity_limit < 1:
        raise ValidationError('保底次数必须大于 0')
    if target and target.reward.code == LOTTERY_PITY_REWARD and (target.reward.kind != 'SERVICE' or target.reward.in_shop):
        raise ValidationError('68 体验单必须配置为不上架商城的专属服务权益')
    eligible = [p for p in prizes if p.probability or p is target]
    if any(not p.reward.is_active for p in eligible):
        raise ValidationError('具有中奖概率的奖品必须全部启用')
    if any(p.reward.audience not in ['ALL', 'BOSS'] for p in eligible):
        raise ValidationError('抽奖奖品必须允许老板领取')
    if any(p.reward.stock is not None and p.reward.stock < 1 for p in eligible):
        raise ValidationError('奖品库存不足，抽奖暂时关闭，请联系俱乐部补货')


LOTTERY_PITY_REWARD = 'lottery-experience-68'
LOTTERY_PITY_LIMIT = 240


def lottery_pity_target(campaign, prizes):
    if not campaign.pity_enabled:
        return None
    if campaign.pity_target_reward_id:
        return next((p for p in prizes if p.reward_id == campaign.pity_target_reward_id), None)
    return next((p for p in prizes if p.reward.code == LOTTERY_PITY_REWARD), None)


def lottery_rarity(reward, campaign=None):
    is_target = (campaign.pity_enabled and reward.pk == campaign.pity_target_reward_id
                 if campaign and campaign.pity_target_reward_id else reward.code == LOTTERY_PITY_REWARD)
    return 'SSR' if is_target else ('R' if reward.kind == 'POINTS' else 'SR')


def historical_pity_count(account, campaign, *, through_id=None):
    rows = LotteryDraw.objects.filter(account=account, business_cycle=account.business_cycle, campaign=campaign)
    if through_id is not None:
        rows = rows.filter(pk__lte=through_id)
    last = rows.filter(Q(rarity='SSR') | Q(prize__reward__code=LOTTERY_PITY_REWARD)).order_by('-id').values_list('id', flat=True).first()
    if last:
        rows = rows.filter(pk__gt=last)
    return min(campaign.pity_limit - 1, rows.count())


def lottery_pity(account, campaign, *, prizes=None, count=None):
    prizes = list(campaign.prizes.select_related('reward')) if prizes is None else prizes
    target = lottery_pity_target(campaign, prizes)
    if count is None:
        state = LotteryPityState.objects.filter(account=account, business_cycle=account.business_cycle, campaign=campaign).first()
        count = state.count if state else (0 if campaign.pity_generation else historical_pity_count(account, campaign))
    return {'enabled': target is not None, 'target_reward_code': target.reward.code if target else None,
        'target_prize_id': target.pk if target else None, 'target_name': target.reward.name if target else None,
        'limit': campaign.pity_limit, 'count': count if target else 0,
        'remaining': campaign.pity_limit - count if target else None}


def lottery_ten_guarantee(campaign, *, prizes=None):
    prizes = list(campaign.prizes.select_related('reward')) if prizes is None else prizes
    candidates = [p for p in prizes if p.probability and p.reward.kind != 'POINTS'
        and p.reward.is_active and p.reward.audience in ['ALL', 'BOSS']
        and (p.reward.stock is None or p.reward.stock > 0)]
    now = timezone.now()
    reason = ''
    if not campaign.enabled or (campaign.starts_at and now < campaign.starts_at) or (campaign.ends_at and now >= campaign.ends_at):
        reason = '抽奖活动尚未开始或已经结束'
    else:
        try:
            validate_campaign(campaign, prizes)
        except ValidationError as error:
            reason = str(error.detail[0])
        if not reason and not candidates:
            reason = '奖池尚未配置正概率非积分奖，暂不支持十连抽'
    return {'enabled': True, 'count': 10, 'min_non_points': 1, 'available': not bool(reason),
        'eligible_prize_ids': [p.pk for p in candidates], 'pity_priority': True, 'unavailable_reason': reason}


def lottery_batch_draws(batch):
    if hasattr(batch, '_legacy_draw'):
        return [batch._legacy_draw]
    return list(batch.draws.select_related('claim', 'prize__reward').order_by('draw_index'))


def _existing_lottery_batch(key, campaign_id, count, fp, expected_version, account, *, lock=False):
    query = LotteryBatch.objects.select_for_update() if lock else LotteryBatch.objects
    existing = query.filter(event_key=key).first()
    if existing:
        if existing.request_fingerprint != fp:
            raise ValidationError('同一操作凭证不能更改活动、抽奖次数或奖池版本')
        return existing
    # 升级前的单抽仍可重放，不改写历史中奖、库存或积分记录。
    legacy = LotteryDraw.objects.select_related('claim', 'prize__reward').filter(event_key=key, batch__isnull=True).first()
    if legacy:
        if legacy.campaign_id != campaign_id or count != 1 or (expected_version is not None and expected_version != legacy.campaign_version):
            raise ValidationError('同一操作凭证不能更改活动、抽奖次数或奖池版本')
        last_ledger = PointLedger.objects.filter(event_key__in=[key, key + ':award']).order_by('-id').first()
        batch = LotteryBatch(account=account, campaign_id=campaign_id, business_cycle=legacy.business_cycle,
            event_key=key, request_fingerprint=fp, count=1, campaign_version=legacy.campaign_version,
            total_point_cost=legacy.point_cost, points_balance=last_ledger.balance_after if last_ledger else 0,
            pity_snapshot={'enabled': False, 'target_reward_code': LOTTERY_PITY_REWARD, 'target_prize_id': None,
                'target_name': '68 体验单', 'limit': LOTTERY_PITY_LIMIT, 'count': 0, 'remaining': None})
        batch._legacy_draw = legacy
        return batch


@transaction.atomic
def draw_batch(account, campaign_id, client_request_id, expected_version=None, count=1):
    if type(count) is not int or count not in (1, 10):
        raise ValidationError('抽奖次数仅支持 1 次或 10 次')
    key = f'draw:{account.pk}:{account.business_cycle}:{request_key(client_request_id)}'
    fp = fingerprint({'campaign_id': campaign_id, 'count': count, 'version': expected_version})
    existing = _existing_lottery_batch(key, campaign_id, count, fp, expected_version, account)
    if existing:
        return existing
    _lock_points_purchase_context(account, 'LOTTERY')
    campaign = LotteryCampaign.objects.select_for_update().get(pk=campaign_id)
    existing = _existing_lottery_batch(key, campaign.pk, count, fp, expected_version, account, lock=True)
    if existing:
        return existing
    now = timezone.now()
    if not campaign.enabled or (campaign.starts_at and now < campaign.starts_at) or (campaign.ends_at and now >= campaign.ends_at):
        raise ValidationError('抽奖活动尚未开始或已经结束')
    if expected_version is not None and expected_version != campaign.version:
        raise ValidationError('抽奖配置已更新，请刷新奖池后再试')
    prizes = list(campaign.prizes.order_by('sort_order', 'id'))
    # 钱包和会员配置已锁定；按主键锁奖品，再锁积分资料。
    rewards = {r.pk: r for r in RewardItem.objects.select_for_update().filter(pk__in=[p.reward_id for p in prizes]).order_by('id')}
    for prize in prizes:
        prize.reward = rewards[prize.reward_id]
    validate_campaign(campaign, prizes)
    ten_candidates = [p for p in prizes if p.probability and p.reward.kind != 'POINTS']
    if count == 10 and not ten_candidates:
        raise ValidationError('奖池尚未配置正概率非积分奖，暂不支持十连抽')
    if campaign.daily_limit and LotteryDraw.objects.filter(account=account, business_cycle=account.business_cycle, campaign=campaign,
            created_at__date=timezone.localdate()).count() + count > campaign.daily_limit:
        raise ValidationError('今天剩余抽奖次数不足，无法完成本次抽奖')
    if any(p.reward.kind == 'BALANCE' and p.probability for p in prizes):
        from wallet.services import get_wallet
        get_wallet(account_id=account.pk, for_update=True)
    profile = locked_profile(account)
    # 同一会员在不同奖池同时使用同一凭证时，在共同的积分锁后再次核对。
    existing = _existing_lottery_batch(key, campaign.pk, count, fp, expected_version, account, lock=True)
    if existing:
        return existing
    from club_accounts.models import ClubAccount
    if ClubAccount.objects.values_list('business_cycle', flat=True).get(pk=account.pk) != account.business_cycle:
        raise ValidationError('会员周期已更新，请刷新后重试')
    from stores.license_benefits import LicenseDecisionUnavailable, LicenseFeatureDenied, check_new_benefits
    admission, _ = check_new_benefits('LOTTERY')
    if admission == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if admission != 'ALLOW':
        raise LicenseFeatureDenied('门店授权暂不允许新的抽奖，请联系平台')
    from .points_usage import assert_usage_allowed
    assert_usage_allowed()
    total_cost = campaign.point_cost * count
    if profile.points_balance < total_cost * 100:
        raise ValidationError('积分不足，请先备足本次全部抽奖积分')
    target = lottery_pity_target(campaign, prizes)
    state = None
    if target:
        state, _ = LotteryPityState.objects.get_or_create(account=account, campaign=campaign, business_cycle=account.business_cycle,
            defaults={'count': 0 if campaign.pity_generation else historical_pity_count(account, campaign)})
        state = LotteryPityState.objects.select_for_update().get(pk=state.pk)
    batch = LotteryBatch.objects.create(account=account, campaign=campaign, business_cycle=account.business_cycle,
        event_key=key, request_fingerprint=fp, count=count, campaign_version=campaign.version,
        total_point_cost=total_cost, points_balance=profile.points_balance)
    snapshot = [{'id': p.pk, 'reward': p.reward_id, 'name': p.reward.name, 'probability': p.probability} for p in prizes]
    has_non_points = False
    for index in range(1, count + 1):
        before = state.count if state else 0
        is_pity = bool(target and before + 1 >= campaign.pity_limit)
        is_ten_guarantee = bool(count == 10 and index == 10 and not has_non_points and not is_pity)
        selection_prizes = ten_candidates if is_ten_guarantee else prizes
        selection_pool = ([{'id': target.pk, 'reward': target.reward_id, 'weight': 1}] if is_pity else
            [{'id': p.pk, 'reward': p.reward_id, 'weight': p.probability} for p in selection_prizes])
        ticket = 0 if is_pity else secrets.randbelow(sum(p.probability for p in selection_prizes))
        selected = target if is_pity else None
        if selected is None:
            upper = 0
            for prize in selection_prizes:
                upper += prize.probability
                if ticket < upper:
                    selected = prize
                    break
        reward = selected.reward
        wallet = reward_wallet(account, reward)
        draw_key = key if count == 1 else f'{key}:{index}'
        change_points(profile, -campaign.point_cost * 100, draw_key, 'LOTTERY_COST', f'参与 {campaign.name}（{index}/{count}）')
        claim = create_claim(account, reward, draw_key, source='LOTTERY', profile=profile, wallet=wallet)
        after = 0 if target and selected.pk == target.pk else (before + 1 if target else 0)
        LotteryDraw.objects.create(account=account, business_cycle=account.business_cycle, campaign=campaign, prize=selected, claim=claim,
            event_key=draw_key, point_cost=campaign.point_cost, campaign_version=campaign.version, random_ticket=ticket,
            probability_snapshot=snapshot, batch=batch, draw_index=index, rarity=lottery_rarity(reward, campaign),
            is_pity=is_pity, pity_before=before, pity_after=after, is_ten_guarantee=is_ten_guarantee,
            selection_pool=selection_pool)
        has_non_points = has_non_points or reward.kind != 'POINTS'
        if state:
            state.count = after
        kind = {'SERVICE': 'LOTTERY_SERVICE', 'NAMING': 'LOTTERY_NAMING', 'TAG': 'LOTTERY_TAG'}.get(reward.kind, 'LOTTERY')
        broadcast(kind, draw_key, account=account, reward=reward.name)
    if state:
        state.save(update_fields=['count', 'updated_at'])
    batch.points_balance = profile.points_balance
    batch.pity_snapshot = lottery_pity(account, campaign, prizes=prizes, count=state.count if state else 0)
    batch.save(update_fields=['points_balance', 'pity_snapshot'])
    return batch


def draw(account, campaign_id, client_request_id, expected_version=None):
    return lottery_batch_draws(draw_batch(account, campaign_id, client_request_id, expected_version, count=1))[0]


class ClaimDetailsChanged(Exception):
    """Delivery details changed since the operator opened the claim."""


class ClaimStatusChanged(Exception):
    """Another operator has changed the claim's delivery status."""


class ClaimTermsChanged(Exception):
    """The entitlement's delivery terms have changed."""


def _provider_claim_processing_lifecycle(fn):
    """Take the recipient's lifecycle lock before staff lock a reward or claim."""
    from functools import wraps

    @wraps(fn)
    def wrapped(claim_id, status, note, operator, *args, **kwargs):
        from django.conf import settings
        from club_accounts.models import ClubAccount
        if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            return fn(claim_id, status, note, operator, *args, **kwargs)
        initial = RewardClaim.objects.select_related('account').get(pk=claim_id)
        account = initial.account
        if account.account_type != ClubAccount.AccountType.PROVIDER:
            return fn(claim_id, status, note, operator, *args, **kwargs)
        from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                       lock_provider_operation)
        try:
            ensure_provider_lifecycle(account)
            with transaction.atomic():
                lock_provider_operation(account)
                if not ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
                        license_membership_active=True, business_cycle=account.business_cycle).exists():
                    raise ValidationError('陪玩账号状态或业务周期已变化，请重新核对')
                # Delivery, tag creation and cancellation all use the original
                # redemption, including when this account has since rejoined.
                from .provider_entitlement_lifecycle import provider_claim_source_current
                if not provider_claim_source_current(initial, account):
                    raise ValidationError('兑换来源周期待核对，请联系客服')
                kwargs['_lifecycle_expected'] = (account.pk, initial.reward_id, initial.event_key, initial.source)
                return fn(claim_id, status, note, operator, *args, **kwargs)
        except ProviderLifecycleError as exc:
            raise ValidationError('陪玩账号状态暂不可核验，请稍后重试') from exc
    return wrapped


@_provider_claim_processing_lifecycle
@transaction.atomic
def process_claim(claim_id, status, note, operator, expected_details=None, expected_status=None, expected_terms=None,
                  tag_reviewed=False, _lifecycle_expected=None):
    # Serialize with redemption, VIP recalculation and account retirement
    # before touching the reward, claim or member-points rows. This processes
    # an existing entitlement and therefore does not request a new License.
    initial = RewardClaim.objects.get(pk=claim_id)
    from wallet.services import get_wallet
    from club_accounts.models import ClubAccount
    get_wallet(account_id=initial.account_id, for_update=True)
    config()
    ClubOperationConfig.objects.select_for_update().get(pk=1)
    account = ClubAccount.objects.select_for_update().get(pk=initial.account_id)
    reward = RewardItem.objects.select_for_update().get(pk=initial.reward_id)
    claim = RewardClaim.objects.select_for_update().get(pk=claim_id)
    if (claim.account_id, claim.reward_id, claim.event_key, claim.source) != (
            initial.account_id, initial.reward_id, initial.event_key, initial.source):
        raise ValidationError('权益归属或来源已变化，请重新核对')
    if (_lifecycle_expected is not None and
            (claim.account_id, claim.reward_id, claim.event_key, claim.source) != _lifecycle_expected):
        raise ValidationError('权益归属或来源已变化，请重新核对')
    claim.account = account
    transitions = {'PENDING': {'FULFILLING', 'COMPLETED', 'CANCELLED'}, 'FULFILLING': {'PENDING', 'COMPLETED', 'CANCELLED'}}
    if expected_status is not None and claim.status != expected_status:
        if claim.status == status and claim.delivery_note == note:
            return claim
        raise ClaimStatusChanged
    tag_revision = claim.status == 'PENDING' and status == 'PENDING' and claim.kind_snapshot == 'TAG' and claim.delivery_note != note
    if claim.status == status and not tag_revision:
        return claim
    if expected_details is not None and claim.details != expected_details:
        raise ClaimDetailsChanged
    if expected_terms is not None and expected_terms != {'duration_days': claim.duration_days,
            'duration_months': claim.duration_months, 'highlighted': claim.highlighted}:
        raise ClaimTermsChanged
    if not tag_revision and status not in transitions.get(claim.status, set()):
        raise ValidationError('该兑换记录不能执行此操作')
    if claim.kind_snapshot == 'TAG' and claim.tag_revision_required and status in ('FULFILLING', 'COMPLETED'):
        raise ValidationError('标签正等待用户修改，请修改后再审核或交付')
    if not note.strip():
        raise ValidationError('请填写交付或取消说明')
    if status == 'CANCELLED':
        if claim.source != 'REDEMPTION':
            raise ValidationError('抽奖奖励不得直接取消，请处理实际交付')
        from site_messages.claim_cycle import recipient_cycle
        if recipient_cycle(claim) != account.business_cycle or not account.license_membership_active:
            raise ValidationError('兑换所属业务周期已结束，请核对原权益')
        if not PointLedger.objects.filter(account_id=account.pk, event_key=claim.event_key,
                kind='REDEMPTION', amount=-claim.points_spent * 100).exists():
            raise ValidationError('原兑换积分流水无法核实，请先核对')
        profile = locked_profile(account)
        change_points(profile, claim.points_spent * 100, f'claim:{claim.pk}:return', 'RETURN', '兑换取消退回积分', operator=operator)
        if reward.stock is not None:
            reward.stock += 1
        reward.redeemed_count = max(0, reward.redeemed_count - 1)
        reward.save(update_fields=['stock', 'redeemed_count'])
        if reward.in_shop:
            from .notifier import notify_reward_catalog_after_commit
            notify_reward_catalog_after_commit()
    if status == 'COMPLETED':
        claim.tag_revision_required = False
        claim.completed_at = timezone.now()
        if claim.duration_days:
            claim.expires_at = claim.completed_at + timedelta(days=claim.duration_days)
        if claim.duration_months:
            import calendar
            end = timezone.localtime(claim.completed_at)
            year, month = divmod(end.year * 12 + end.month - 1 + claim.duration_months, 12)
            claim.expires_at = end.replace(year=year, month=month+1, day=min(end.day, calendar.monthrange(year, month+1)[1]))
        if claim.kind_snapshot == 'TAG':
            if not tag_reviewed:
                raise ValidationError('请先确认已审核 TAG 内容符合要求')
            title = str(claim.details.get('tag', '')).strip()
            if not title or len(title) > 30:
                raise ValidationError('请先填写用户希望展示的 TAG（最多 30 字）')
            MemberTag.objects.get_or_create(event_key=f'claim:{claim.pk}:tag', defaults={
                'account': claim.account, 'title': title, 'priority': 100,
                'highlighted': claim.highlighted, 'expires_at': claim.expires_at,
                'reviewed_at': timezone.now(), 'reviewed_by': operator,
            })
            from .notifier import notify_provider_tags_after_commit
            notify_provider_tags_after_commit(claim.account)
        if claim.kind_snapshot == 'NAMING':
            broadcast('NAMING', f'claim:{claim.pk}:naming', account=claim.account, reward=claim.name_snapshot)
    if claim.kind_snapshot == 'TAG' and status == 'PENDING' and (claim.status == 'FULFILLING' or tag_revision):
        claim.tag_revision_required = True
    claim.status = status
    claim.delivery_note = note[:255]
    claim.operator = operator
    claim.save(update_fields=['status', 'delivery_note', 'operator', 'completed_at', 'expires_at', 'tag_revision_required'])
    from .notifier import notify_claims_after_commit
    notify_claims_after_commit()
    from club_accounts.models import LegacyAccountMap
    recipient_id = LegacyAccountMap.objects.filter(account_id=claim.account_id).values_list('legacy_user_id', flat=True).first()
    from orders.services import push_message_safe
    labels = {'PENDING': '已退回补充资料', 'FULFILLING': '交付中', 'COMPLETED': '已交付', 'CANCELLED': '已取消'}
    title = '权益交付进度更新'
    preview = (f'「{claim.name_snapshot}」已取消，{claim.points_spent} 积分已退回。' if status == 'CANCELLED' and claim.source == 'REDEMPTION'
               else f'「{claim.name_snapshot}」{labels[status]}，可查看处理详情。')
    from site_messages.claim_cycle import recipient_cycle
    claim_cycle = recipient_cycle(claim)
    transaction.on_commit(lambda: push_message_safe(
        recipient_id=recipient_id, recipient_account_id=claim.account_id, title=title,
        preview=preview, msg_type='SYSTEM', action_url=f'/pages/reward-claim/index?id={claim.pk}',
        recipient_business_cycle=claim_cycle, require_cycle=True))
    return claim


def record_recharge(record):
    if not record.account_id or record.account.account_type != 'BOSS':
        return
    account = record.account
    # 新充值前的存量先入批次，保证旧余额不会意外享受新的充值倍率。
    if record.recharge_tx_id and not PointFundingLot.objects.filter(source_key=f'recharge:{record.pk}').exists():
        tracked = PointFundingLot.objects.filter(account=account).aggregate(value=Sum('remaining'))['value'] or 0
        legacy_balance = max(0, record.recharge_tx.balance_before - tracked)
        if legacy_balance:
            PointFundingLot.objects.get_or_create(source_key=f'recharge:{record.pk}:prior-balance',
                defaults={'account': account, 'remaining': legacy_balance, 'points_rate': 100})
    package = RechargePackage.objects.filter(active=True, amount__lte=record.amount).order_by('-amount').first()
    # Deferred gift is a distinct lot when approval resolves; the principal
    # lot must not be reclassified using the later record.gift_amount.
    from wallet.models import RechargeGiftApproval
    delayed_gift = RechargeGiftApproval.objects.filter(recharge_id=record.pk).exists()
    PointFundingLot.objects.get_or_create(source_key=f'recharge:{record.pk}', defaults={
        'account': account, 'remaining': record.amount + record.gift_amount,
        'kind': PointFundingLot.Kind.PREPAID if delayed_gift else PointFundingLot.Kind.RECHARGE,
        'gift_remaining': None if delayed_gift else record.gift_amount,
        'points_rate': package.points_rate if package else 100,
    })
    broadcast('RECHARGE', f'recharge:{record.pk}', account=account)


def completed_consumption(account):
    """VIP growth: confirmed net services and immediately confirmed gifts."""
    from .models import ConsumptionConfirmation
    total = ConsumptionConfirmation.objects.filter(account=account, business_cycle=account.business_cycle,
        order__payment_status__in=['PAID','PARTIALLY_REFUNDED'], reversed_at__isnull=True).aggregate(value=Sum(F('amount')-F('refunded_amount')))['value'] or 0
    from orders.models import OrderAddonConsumption
    addons = OrderAddonConsumption.objects.filter(account=account, business_cycle=account.business_cycle).filter(
        Q(vip_eligible=True) | Q(license_review__vip_applied=True)
    ).aggregate(value=Sum(F('paid_units') - F('refunded_units')))['value'] or 0
    from .models import LicenseRefundVipReview
    reviewed = 0
    for review in LicenseRefundVipReview.objects.filter(decision__account=account,
            decision__business_cycle=account.business_cycle, vip_applied=True).select_related('decision__order'):
        decision, order = review.decision, review.decision.order
        refunds = list(order.refund_records.order_by('pk'))
        from orders.models import OrderPointsRefund
        proofs = {row.refund_id: row for row in OrderPointsRefund.objects.filter(refund_id__in=[r.pk for r in refunds])}
        default_ordinary = (order.funding_terms is None and not order.is_gift_snapshot
                            and hasattr(order, 'default_points_snapshot'))
        formal_ordinary = (order.funding_terms is not None and not order.is_gift_snapshot
                           and not hasattr(order, 'points_redemption'))
        ordinary = default_ordinary or formal_ordinary
        if default_ordinary:
            from .models import DefaultPaymentSource
            source = DefaultPaymentSource.objects.filter(snapshot=order.default_points_snapshot).aggregate(
                paid=Sum('amount'), refunded=Sum('refunded_amount'))
            if (source['paid'] != decision.paid_units
                    or (source['refunded'] or 0) != sum(row.amount for row in refunds)
                    or DefaultPaymentSource.objects.filter(snapshot=order.default_points_snapshot)
                       .exclude(kind='PREPAID').exists()):
                raise ValidationError('人工核对的普通服务退款来源不一致')
        elif formal_ordinary:
            from .models import OrderFundingAllocation
            source = OrderFundingAllocation.objects.filter(order=order).aggregate(
                paid=Sum('amount'), refunded=Sum('refunded_amount'))
            if (source['paid'] != decision.paid_units
                    or (source['refunded'] or 0) != sum(row.amount for row in refunds)):
                raise ValidationError('人工核对的普通服务退款来源不一致')
        if (review.outcome != 'ALLOW' or decision.outcome != 'UNKNOWN'
                or not decision.payment_vip_enabled or not decision.confirmation_vip_enabled
                or (not ordinary and (len(proofs) != len(refunds)
                    or any(proofs[r.pk].redemption.order_id != order.pk or proofs[r.pk].coin_units != r.amount
                           for r in refunds)))):
            raise ValidationError('人工核对的会员成长退款凭证不一致')
        refunded = sum(r.amount for r in refunds)
        if refunded < decision.refunded_units or refunded > decision.paid_units:
            raise ValidationError('人工核对的会员成长退款金额异常')
        reviewed += decision.paid_units - refunded
    return total + addons + reviewed


def confirm_gift_vip_consumption(order):
    """Count a paid gift's principal immediately without marking service achievement."""
    if not order.is_gift_snapshot or not order.customer_account_id or order.amount <= 0 or order.gift_vip_enabled_snapshot is not True:
        return
    # Gift availability and VIP growth are independent SaaS entitlements.
    # A paid gift must not become future VIP growth when VIP is unavailable.
    from stores.license_benefits import check_new_benefits
    decision, evidence = check_new_benefits('VIP_GROWTH')
    if decision == 'UNKNOWN':
        from .models import LicenseGiftVipDecision
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('礼物授权决定须与付款同事务保存')
        record, _ = LicenseGiftVipDecision.objects.get_or_create(order=order, defaults={
            'account_id': order.customer_account_id, 'business_cycle': order.business_cycle,
            'paid_units': order.amount, 'license_evidence': evidence})
        if (record.account_id != order.customer_account_id or record.business_cycle != order.business_cycle
                or record.paid_units != order.amount):
            raise ValidationError('礼物会员授权决定凭证不一致')
        return
    if decision != 'ALLOW':
        return
    account = order.customer_account
    if account.business_cycle != order.business_cycle:
        return
    from .models import ConsumptionConfirmation
    _, created = ConsumptionConfirmation.objects.get_or_create(order=order, defaults={
        'account': account, 'business_cycle': order.business_cycle, 'amount': order.amount,
    })
    if created:
        update_vip(account)


@transaction.atomic
@skip_denied_benefit
def update_vip(account, *, operation=None):
    from club_accounts.models import ClubAccount
    from users.models import CustomUser
    if account.account_type != 'BOSS':
        return
    expected_cycle = account.business_cycle
    conf = operation if operation is not None else config()
    if not conf.vip_enabled:
        return
    from stores.license_benefits import check_new_benefits
    if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
        return
    # 同一老板的升级串行化；结单与充值均遵循钱包、会员资料的锁顺序。
    from wallet.services import get_wallet
    get_wallet(account_id=account.pk, for_update=True)
    from .vip_transition import transition_guard_enabled
    from .models import ClubOperationConfig
    if not ClubOperationConfig.objects.select_for_update().get(pk=1).vip_enabled:
        return
    if not ClubAccount.objects.filter(pk=account.pk, account_type='BOSS',
            business_cycle=expected_cycle, license_membership_active=True).exists():
        return
    account.refresh_from_db(fields=['boss_type', 'business_cycle'])
    profile = locked_profile(account)
    # The license may have expired while this account waited for its locks.
    # Roll the optional VIP transaction back so a later scan can retry.
    if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
        raise LicenseFeatureDenied('门店授权暂不允许发放新的 VIP 权益，请联系平台')
    total = completed_consumption(account)
    tiers_query = VipTier.objects.select_related('boss_type').filter(
        active=True, boss_type__is_active=True, recharge_threshold__lte=total,
    ).order_by('level')
    if operation is not None:
        tiers_query = tiers_query.select_for_update()
    tiers = list(tiers_query)
    if not tiers:
        LicenseAddonBenefitsReview.objects.filter(consumption__account=account,
            consumption__business_cycle=account.business_cycle, vip_recalculation_pending=True).update(
                vip_recalculation_pending=False)
        from .models import LicenseRefundVipReview
        LicenseRefundVipReview.objects.filter(decision__account=account,
            decision__business_cycle=account.business_cycle, vip_recalculation_pending=True).update(
                vip_recalculation_pending=False)
        from .models import LicenseGiftVipReview
        LicenseGiftVipReview.objects.filter(decision__account=account,
            decision__business_cycle=account.business_cycle, vip_recalculation_pending=True).update(
                vip_recalculation_pending=False)
        return
    top = tiers[-1]
    from .refunds import restore_requalified_benefits
    restore_requalified_benefits(account, top)
    if account.boss_type_id != top.boss_type_id:
        current = VipTier.objects.filter(boss_type_id=account.boss_type_id).first()
        if not current or current.level < top.level:
            if current:
                from .vip_transition import transition_guard_available
                if transition_guard_enabled() and not transition_guard_available():
                    from rest_framework.exceptions import ValidationError
                    raise ValidationError('本店会员权益规则不可用，请先核对')
                from coupons.monthly_upgrade import freeze_current_month
                freeze_current_month(account)
            ClubAccount.objects.filter(pk=account.pk).update(boss_type=top.boss_type)
            from club_accounts.models import LegacyAccountMap
            mapping = LegacyAccountMap.objects.filter(account=account).first()
            if mapping:
                CustomUser.objects.filter(pk=mapping.legacy_user_id).update(boss_type=top.boss_type)
            account.boss_type = top.boss_type
            broadcast('UPGRADE', f'vip:{account.pk}:{account.business_cycle}:tier-{top.pk}', account=account, level=top.boss_type.name)
    from .vip_review_guard import review_barriers
    blocked_tiers, quota_floors, unknown_quota = review_barriers(account)
    for tier in tiers:
        if tier.pk in blocked_tiers:
            continue
        grant_key = f'vip:{account.pk}:{account.business_cycle}:{tier.pk}:upgrade'
        if not MemberBenefitGrant.objects.filter(event_key=grant_key).exists():
            if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
                raise LicenseFeatureDenied('门店授权暂不允许发放新的 VIP 权益，请联系平台')
        grant, new = MemberBenefitGrant.objects.get_or_create(event_key=grant_key, defaults={
            'account': account, 'tier': tier, 'name': f'{tier.boss_type.name} 升级权益', 'kind': 'UPGRADE', 'details': tier.benefits,
        })
        if new:
            from .vip_policy import issuance_policy
            policy = issuance_policy(tier)
            MemberTag.objects.get_or_create(event_key=f'vip:{account.pk}:{account.business_cycle}:{tier.pk}:tag', defaults={
                'account': account, 'title': tier.boss_type.name, 'priority': tier.recharge_threshold,
                'highlighted': policy['highlighted'],
            })
            from coupons.benefits import issue
            if policy['companion_quantity']:
                issue(account, f'vip-upgrade:{account.pk}:{account.business_cycle}:{tier.pk}:companion',
                    name=f'{tier.boss_type.name} 陪玩赠券', rate=0, scope='COMPANION',
                    variant=policy['companion_variant'], quantity=policy['companion_quantity'], limit_units=1,
                    service_ids=tier.companion_service_ids or [])
    sync_vip_entitlements(account, top, quota_floors=quota_floors, unknown_quota=unknown_quota)
    from coupons.benefits import try_grant_periodic
    try_grant_periodic(account)
    LicenseAddonBenefitsReview.objects.filter(consumption__account=account,
        consumption__business_cycle=account.business_cycle, vip_recalculation_pending=True).update(
            vip_recalculation_pending=False)
    from .models import LicenseRefundVipReview
    LicenseRefundVipReview.objects.filter(decision__account=account,
        decision__business_cycle=account.business_cycle, vip_recalculation_pending=True).update(
            vip_recalculation_pending=False)
    from .models import LicenseGiftVipReview
    LicenseGiftVipReview.objects.filter(decision__account=account,
        decision__business_cycle=account.business_cycle, vip_recalculation_pending=True).update(
            vip_recalculation_pending=False)


def sync_vip_entitlements(account, tier, *, quota_floors=None, unknown_quota=None, preserve_existing_terms=False):
    """Keep delivery records; top up total quotas rather than repeating bundles."""
    if quota_floors is None or unknown_quota is None:
        from .vip_review_guard import review_barriers
        _, quota_floors, unknown_quota = review_barriers(account)
    level = tier.level
    from .vip_policy import issuance_policy
    policy = issuance_policy(tier)
    template_level = policy['template_level']
    custom = policy['custom'] or template_level is None
    prefix = f'vip-quota:{account.pk}:{account.business_cycle}:'
    configured = {}
    for prior in VipTier.objects.filter(active=True, level__lte=level).order_by('level'):
        for bundle in prior.upgrade_rewards:
            reward = RewardItem.objects.get(pk=bundle['reward'])
            family = reward.kind if reward.kind in ('TAG', 'NAMING') else (reward.vip_quota_kind or reward.code)
            configured[family] = (reward, bundle['quantity'])
    quotas = {'TAG':[1,1,2,2,3,5], 'FRIEND':[0,0,1,2,5,10], 'NAMING':[3,6,6,6,10,12]}
    terms_changed = False
    for family, (reward, configured_count) in configured.items():
        if family == 'BIRTHDAY_GIFT':
            continue  # issued on the birthday, not on every upgrade
        if unknown_quota:
            continue
        target = quotas[family][template_level-1] if not custom and family in quotas else configured_count
        for index in range(quota_floors.get(family, 0), target):
            grant_entitlement(account, reward, f'{prefix}{family}:{index}', 'VIP',
                {'business_cycle': account.business_cycle, 'quota_kind': family})
        if quota_floors.get(family, 0) or preserve_existing_terms:
            continue
        claims = RewardClaim.objects.filter(account=account, event_key__startswith=f'{prefix}{family}:').order_by('pk')
        if family == 'NAMING':
            days, months = ((reward.duration_days, reward.duration_months) if custom
                            else ((7, 0) if template_level < 4 else (0, 1)))
            changed = claims.filter(status__in=['PENDING','FULFILLING']).exclude(duration_days=days, duration_months=months).update(
                duration_days=days, duration_months=months)
            terms_changed |= bool(changed)
        elif family == 'TAG':
            high = (target if reward.highlighted else 0) if custom else [0,0,0,1,2,5][template_level-1]
            for index, claim in enumerate(claims):
                terms_changed |= claim.highlighted != (index < high) or bool(claim.duration_days or claim.duration_months)
                claim.highlighted = index < high
                claim.duration_days = claim.duration_months = 0
                claim.save(update_fields=['highlighted','duration_days','duration_months'])
                MemberTag.objects.filter(event_key=f'claim:{claim.pk}:tag').update(highlighted=claim.highlighted)
    if terms_changed:
        from .notifier import notify_claims_after_commit
        notify_claims_after_commit()


def grant_entitlement(account, reward, key, source, details=None, source_order=None):
    """VIP/赠礼权益不占积分商品库存；仍进入同一客服交付闭环。"""
    existing = RewardClaim.objects.filter(event_key=key).first()
    if existing:
        if existing.account_id != account.pk:
            raise ValidationError('权益发放记录与请求不一致，请先核对')
        return existing
    if source == 'VIP':
        from stores.license_benefits import check_new_benefits
        if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
            raise LicenseFeatureDenied('门店授权暂不允许发放新的 VIP 权益，请联系平台')
    claim, created = RewardClaim.objects.get_or_create(event_key=key, defaults={
        'account': account, 'reward': reward, 'source': source, 'name_snapshot': reward.name,
        'kind_snapshot': reward.kind, 'duration_days': reward.duration_days,
        'duration_months': reward.duration_months, 'highlighted': reward.highlighted,
        'details': details or {}, 'source_order': source_order,
    })
    if claim.account_id != account.pk:
        raise ValidationError('权益发放记录与请求不一致，请先核对')
    if created:
        from .notifier import notify_claims_after_commit
        notify_claims_after_commit()
    return claim


def award_gift_benefits(order):
    from orders.models import Order
    from club_accounts.models import ClubAccount
    if (not transaction.get_connection().in_atomic_block or not order.is_gift_snapshot
            or type(order.gift_category_id_snapshot) is not int or order.gift_category_id_snapshot <= 0
            or not order.customer_account_id or type(order.business_cycle) is not int or order.business_cycle < 1):
        raise ValidationError('礼物权益来源或业务周期待核对')
    account = ClubAccount.objects.select_for_update().filter(pk=order.customer_account_id).first()
    if (not account or account.account_type != ClubAccount.AccountType.BOSS
            or not account.license_membership_active or account.business_cycle != order.business_cycle):
        raise ValidationError('礼物账号业务周期已变化，请重新登录')
    broadcast('GIFT', f'gift:{order.pk}', account=order.customer_account,
              provider=order.provider_name_snapshot,
              reward=f'{order.service_name_snapshot or order.service.name} × {order.game_rounds}')
    now = order.created_at
    rules = GiftBenefitRule.objects.filter(enabled=True).select_related('reward').filter(
        Q(category__isnull=True) | Q(category_id=order.gift_category_id_snapshot)).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    for rule in rules:
        total = order.amount
        if rule.cumulative:
            from orders.refunds import with_net_amount
            history = with_net_amount(Order.objects.filter(customer_account_id=order.customer_account_id,
                business_cycle=order.business_cycle, is_gift_snapshot=True,
                status='COMPLETED', payment_status__in=['PAID','PARTIALLY_REFUNDED'], created_at__lte=now))
            if rule.category_id: history=history.filter(gift_category_id_snapshot=rule.category_id)
            if rule.starts_at: history=history.filter(created_at__gte=rule.starts_at)
            total=history.aggregate(value=Sum('_net_paid_amount'))['value'] or 0
        if total < rule.threshold or (total == rule.threshold and not rule.inclusive):
            continue
        target = f'account:{order.customer_account_id}' if rule.once_per_account else f'order:{order.pk}'
        claim = grant_entitlement(order.customer_account, rule.reward, f'gift-benefit:{rule.pk}:{target}',
            'GIFT', {'provider_name': order.provider_name_snapshot}, order)
        if rule.reward.kind == 'NAMING':
            broadcast('NAMING', f'claim:{claim.pk}:naming', account=order.customer_account, reward=claim.name_snapshot,
                provider=order.provider_name_snapshot)


def grant_gift_naming(order, title):
    """Wallet locks serialize purchases; each paid gift order can extend once."""
    from .models import ProviderNaming, GiftNamingGrant
    days = order.service.gift_naming_days * order.game_rounds
    if not days:
        return None
    if GiftNamingGrant.objects.filter(order=order).exists():
        return GiftNamingGrant.objects.get(order=order)
    if (not order.provider_account_id or not order.gift_provider_business_cycle
            or order.provider_account.business_cycle != order.gift_provider_business_cycle):
        raise ValidationError('礼物冠名的陪玩来源周期待核对')
    now = timezone.now()
    naming, _ = ProviderNaming.objects.get_or_create(provider=order.provider_account, sponsor=order.customer_account,
        provider_business_cycle=order.gift_provider_business_cycle,
        defaults={'title': title, 'expires_at': now})
    naming = ProviderNaming.objects.select_for_update().get(pk=naming.pk)
    base = max(naming.expires_at, now)
    try:
        expires_at = base + timedelta(days=days)
    except OverflowError:
        raise ValidationError('累计冠名时间超过支持范围') from None
    naming.title = title
    naming.expires_at = expires_at
    # Keep moderation decisions intact when extending a hidden title.
    naming.save(update_fields=['title', 'expires_at', 'updated_at'])
    grant = GiftNamingGrant.objects.create(order=order, naming=naming, days=days, title_snapshot=title, expires_at=expires_at)
    from stores.display import provider_display_name
    broadcast('NAMING', f'gift-naming:{order.pk}', account=order.customer_account,
        provider=order.provider_name_snapshot,
        reward=f'{provider_display_name(order.provider_name_snapshot)} · {title}，延长 {days} 天')
    return grant


@transaction.atomic
def record_external_points(account, *, kind, source_id, day, units, operator, observed_at=None,
                           observed_start=None, observed_end=None):
    """人工核对的 KOOK 明细；未来机器人复用同一事件键，重复回传不重复加分。"""
    from club_accounts.models import ClubAccount
    current = ClubAccount.objects.select_for_update().get(pk=account.pk)
    if (current.account_type != ClubAccount.AccountType.BOSS or not current.is_active
            or not current.license_membership_active or current.business_cycle != account.business_cycle):
        raise ValidationError('老板已退店或业务周期发生变化，请重新核对')
    if kind not in ('KOOK_ONLINE', 'KOOK_CHECKIN'):
        raise ValidationError('积分来源不受支持')
    if day > timezone.localdate() or units < 1 or units > 86400:
        raise ValidationError('日期或有效在线秒数不合法')
    if kind == 'KOOK_ONLINE':
        from .kook_manual_online import check_times, existing as existing_manual, defer_if_needed
        check_times(day, units, observed_start, observed_end)
        pending = existing_manual(current, source_id, observed_start, observed_end, units)
        if pending:
            return pending.points_ledger if pending.status == 'COMPLETED' and pending.points_ledger_id else pending
    if kind == 'KOOK_ONLINE':
        import hashlib
        source_digest = hashlib.sha256(source_id.encode('utf-8')).hexdigest()
        key = f'KOOK_ONLINE_MANUAL:{account.pk}:{current.business_cycle}:{source_digest}'
    else:
        key = f'{kind}:{account.pk}:{day.isoformat()}'
    existing = PointLedger.objects.filter(event_key=key).first()
    if existing:
        if existing.account_id != account.pk or existing.kind != kind:
            raise ValidationError('原积分事件归属不一致，请先核对')
        return existing
    if kind == 'KOOK_CHECKIN':
        from .kook_checkin_rules import points_for_manual_day
        points, revision = points_for_manual_day(day, observed_at)
        amount = points * 100
    else:
        from stores.certificate_admission import require_interval_admission
        require_interval_admission(observed_start, observed_end)
        pending, manual_cursor = defer_if_needed(current, operator, source_id, observed_start, observed_end, units)
        if pending:
            return pending
        from .kook_online_rules import initial_rule, earned
        rule = initial_rule()
        amount = earned(manual_cursor.remainder_ms + units * 1000, 0, 0, rule) * 100
        from .models import KookBinding, KookOnlineInterval, KookPointsConfig
        KookPointsConfig.objects.get_or_create(pk=1, defaults={'guild_id': '', 'checkin_channel_id': ''})
        kook_config = KookPointsConfig.objects.select_for_update().get(pk=1)
        if kook_config.enabled:
            raise ValidationError('KOOK自动计分已开启，请勿人工重复记分')
        binding = KookBinding.objects.select_for_update().filter(account=current).first()
        if binding and binding.business_cycle == current.business_cycle:
            binding.account = current
            from .kook_online_queue import lock_cursor
            cursor = lock_cursor(binding)
            if cursor.blocked or KookOnlineInterval.objects.filter(binding=binding,
                    binding_cycle=current.business_cycle, processing_status='WAITING').exists():
                raise ValidationError('该账号仍有 KOOK 授权待核对区间，请联系平台先按时间顺序处理')
    if kind == 'KOOK_ONLINE':
        from .kook_online_balances import verify as verify_balance
        verify_balance(manual_cursor)
        if binding and binding.business_cycle == current.business_cycle:
            from .kook_online_queue import verify_cursor
            verify_cursor(manual_cursor, binding)
    profile = locked_profile(account)
    if not config().points_enabled:
        raise ValidationError('本店积分发放已暂停')
    from stores.license_benefits import LicenseDecisionUnavailable, check_new_benefits
    admission, license_evidence = check_new_benefits('CHECKIN' if kind == 'KOOK_CHECKIN' else None)
    if admission == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if admission != 'ALLOW':
        raise LicenseFeatureDenied('门店授权暂不允许发放新的积分，请联系平台')
    if kind == 'KOOK_ONLINE':
        from .kook_online_rules import interval_segments
        parts = interval_segments(observed_start, observed_end)
        if manual_cursor.rule_version_id != 0 or any(part[2].revision != 0 for part in parts):
            from django.utils.dateparse import parse_datetime
            active = license_evidence.get('current') or {}
            effective = parse_datetime(active.get('effective_at', '')) if active else None
            if effective and observed_start < effective:
                raise ValidationError('原在线时段授权无法确认，请联系平台核对')
            from .kook_manual_online import record_versioned
            return record_versioned(current, manual_cursor, binding, profile, source_id=source_id,
                started_at=observed_start, ended_at=observed_end, units=units, operator=operator, key=key)
    evidence = (f'；规则版本 {revision}；实际签到时间 {observed_at.isoformat()}' if observed_at else f'；规则版本 {revision}') if kind == 'KOOK_CHECKIN' else ''
    if kind == 'KOOK_ONLINE':
        from .kook_online_balances import accrue_interval, consume_current_pending
        pending_after_interval = accrue_interval(manual_cursor, rule, units * 1000)
        if amount // 100 > pending_after_interval:
            raise ValidationError('人工在线积分超过规则余额')
    ledger = change_points(profile, amount, key, kind, f'KOOK 记录 {source_id[:80]}{evidence}', operator=operator)
    if kind == 'KOOK_ONLINE':
        consume_current_pending(manual_cursor, amount // 100, ledger)
        from .models import KookManualOnlineReview
        manual_source = KookManualOnlineReview.objects.create(account=current, business_cycle=current.business_cycle,
            cursor=manual_cursor, source_id=source_id, started_at=observed_start, ended_at=observed_end,
            online_seconds=units, operator=operator, status='COMPLETED', points_ledger=ledger)
        from .kook_manual_online import record_segments
        record_segments(manual_source)
        if binding and binding.business_cycle == current.business_cycle:
            binding.online_milliseconds += units * 1000
            binding.awarded_points += amount // 100
            binding.save(update_fields=['online_milliseconds', 'awarded_points'])
            from .kook_online_queue import sync_cursor
            sync_cursor(manual_cursor, binding)
        else:
            manual_cursor.unbound_online_ms += units * 1000
            manual_cursor.unbound_awarded_points += amount // 100
            manual_cursor.save(update_fields=['unbound_online_ms', 'unbound_awarded_points', 'remainder_ms', 'updated_at'])
            from .kook_online_balances import save as save_balance
            save_balance(manual_cursor)
        manual_cursor.last_ended_at = observed_end
        manual_cursor.save(update_fields=['last_ended_at', 'updated_at'])
    return ledger
