import hashlib
import json
import secrets
import string
from datetime import timedelta
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import (
    Broadcast, BroadcastRule, ClubOperationConfig, LotteryCampaign, LotteryDraw,
    LotteryPrize, MemberProfile, MemberTag, PointLedger, RewardClaim, RewardItem,
    VipTier, MemberBenefitGrant,
    RechargePackage, PointFundingLot, OrderPointAllocation, GiftBenefitRule,
)


DEFAULT_BROADCASTS = {
    'NAMING': '{nickname} 获得了冠名权益「{reward}」',
    'RECHARGE': '{nickname} 充值成功，感谢对俱乐部的支持',
    'UPGRADE': '恭喜 {nickname} 升级为 {level}',
    'REVIEW': '{nickname} 给 {provider} 送上了好评',
    'BIRTHDAY': '今天是 {nickname} 的生日，兴安电竞祝你生日快乐！',
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
    rule, _ = BroadcastRule.objects.get_or_create(kind=kind, defaults={'template': DEFAULT_BROADCASTS[kind]})
    if not rule.enabled:
        return None
    nickname = (account.nickname or account.account_no) if account else '俱乐部老板'
    if anonymous:
        nickname = '一位老板'
    elif rule.mask_nickname:
        nickname = nickname[:1] + '**'
    values = {key: str(context.get(key, ''))[:100] for key in BROADCAST_FIELDS}
    values['nickname'] = nickname
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
    return PointLedger.objects.create(account=profile.account, event_key=key, kind=kind,
        amount=amount, balance_after=balance, remark=remark, order=order, operator=operator)


def award_order_points(order):
    if not order.customer_account_id or order.amount <= 0:
        return
    if order.customer_account.business_cycle != order.business_cycle:
        return
    profile = locked_profile(order.customer_account)
    key = f'order:{order.pk}:points'
    if PointLedger.objects.filter(event_key=key).exists():
        return
    allocations = list(order.point_allocations.filter(released=False))
    points = sum(row.points_amount for row in allocations) if allocations else (order.amount if config().points_enabled else 0)
    if not points:
        return
    change_points(profile, points, key, 'CONSUMPTION', '订单消费积分（按支付时余额批次及生日倍率）', order=order)


def allocate_order_points(order, wallet, balance_before):
    """调用时已锁钱包。FIFO 记录消费来源，只有履约完成才真正发积分。"""
    if not order.customer_account_id or order.point_allocations.exists() or order.amount <= 0:
        return
    profile = locked_profile(order.customer_account)
    lots = list(PointFundingLot.objects.select_for_update().filter(account_id=order.customer_account_id, remaining__gt=0).order_by('id'))
    tracked = sum(lot.remaining for lot in lots)
    # 旧钱包、赠币、积分兑换余额等非预存来源，默认 1 倍。
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
        lot.remaining -= removed
        excess -= removed
        lot.save(update_fields=['remaining'])
    day = timezone.localtime(order.created_at).date()
    birthday = profile.birthday
    multiplier = config().birthday_multiplier if birthday and (birthday.month, birthday.day) == (day.month, day.day) else 1
    if not config().points_enabled:
        multiplier = 0
    left = order.amount
    for lot in lots:
        amount = min(left, lot.remaining)
        if amount:
            OrderPointAllocation.objects.create(order=order, lot=lot, amount=amount,
                points_amount=amount * lot.points_rate * multiplier // 100)
            lot.remaining -= amount
            lot.save(update_fields=['remaining'])
            left -= amount
        if not left:
            break
    if left:
        raise ValidationError('积分来源余额核对失败，请重试')


def release_order_allocations(order):
    for allocation in order.point_allocations.select_for_update().filter(released=False).order_by('lot_id'):
        lot = PointFundingLot.objects.select_for_update().get(pk=allocation.lot_id)
        lot.remaining += allocation.amount
        lot.save(update_fields=['remaining'])
        allocation.released = True
        allocation.save(update_fields=['released'])


def reverse_order_points(order):
    if not order.customer_account_id:
        return
    original = PointLedger.objects.filter(event_key=f'order:{order.pk}:points').first()
    if original:
        profile = locked_profile(order.customer_account)
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


def create_claim(account, reward, key, *, source, points_spent=0, details=None, request_fingerprint='', profile=None, wallet=None):
    reserve_reward(reward)
    claim = RewardClaim.objects.create(account=account, reward=reward, event_key=key, source=source,
        name_snapshot=reward.name, kind_snapshot=reward.kind, duration_days=reward.duration_days,
        duration_months=reward.duration_months, highlighted=reward.highlighted,
        points_spent=points_spent, details=details or {}, request_fingerprint=request_fingerprint,
        balance_amount=reward.balance_amount)
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
        Transaction.objects.create(wallet=wallet, amount=reward.balance_amount,
            tx_type=Transaction.TxType.GIFT, balance_before=before, balance_after=wallet.balance,
            remark=f'积分兑换：{reward.name}（兑换记录 {claim.pk}）')
        claim.status = RewardClaim.Status.COMPLETED
    elif reward.code == 'lottery-experience-68':
        raise ValidationError('体验单不参与抽奖优惠，请调整奖池配置')
    if claim.status == RewardClaim.Status.COMPLETED:
        claim.completed_at = timezone.now()
        claim.save(update_fields=['status', 'completed_at', 'delivery_note'])
    return claim


@transaction.atomic
def redeem(account, reward_id, client_request_id, details=None):
    key = f'redeem:{account.pk}:{account.business_cycle}:{request_key(client_request_id)}'
    fp = fingerprint({'reward_id': reward_id, 'details': details or {}})
    existing = RewardClaim.objects.filter(event_key=key).first()
    if existing:
        if existing.request_fingerprint != fp:
            raise ValidationError('同一操作凭证不能兑换不同内容')
        return existing
    reward = RewardItem.objects.select_for_update().get(pk=reward_id, in_shop=True)
    if reward.audience not in ['ALL',account.account_type]:
        raise ValidationError('此兑换商品不适用于当前身份')
    wallet = reward_wallet(account, reward)
    profile = locked_profile(account)
    existing = RewardClaim.objects.filter(event_key=key).first()
    if existing:
        if existing.request_fingerprint != fp:
            raise ValidationError('同一操作凭证不能兑换不同内容')
        return existing
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
    if any(p.reward.code == 'lottery-experience-68' for p in prizes if p.probability):
        raise ValidationError('体验单不参与抽奖优惠，请调整奖池配置')
    if any(not p.reward.is_active for p in prizes if p.probability):
        raise ValidationError('具有中奖概率的奖品必须全部启用')
    if any(p.reward.stock is not None and p.reward.stock < 1 for p in prizes if p.probability):
        raise ValidationError('奖品库存不足，抽奖暂时关闭，请联系俱乐部补货')


@transaction.atomic
def draw(account, campaign_id, client_request_id, expected_version=None):
    key = f'draw:{account.pk}:{account.business_cycle}:{request_key(client_request_id)}'
    campaign = LotteryCampaign.objects.select_for_update().get(pk=campaign_id)
    existing = LotteryDraw.objects.select_related('claim').filter(event_key=key).first()
    if existing:
        if existing.campaign_id != campaign.pk:
            raise ValidationError('同一操作凭证不能用于不同活动')
        return existing
    now = timezone.now()
    if not campaign.enabled or (campaign.starts_at and now < campaign.starts_at) or (campaign.ends_at and now >= campaign.ends_at):
        raise ValidationError('抽奖活动尚未开始或已经结束')
    if expected_version is not None and expected_version != campaign.version:
        raise ValidationError('抽奖配置已更新，请刷新奖池后再试')
    prizes = list(campaign.prizes.select_related('reward').order_by('sort_order', 'id'))
    validate_campaign(campaign, prizes)
    if campaign.daily_limit and LotteryDraw.objects.filter(account=account, business_cycle=account.business_cycle, campaign=campaign,
            created_at__date=timezone.localdate()).count() >= campaign.daily_limit:
        raise ValidationError('今天的抽奖次数已用完')
    ticket = secrets.randbelow(10000)
    upper = 0
    selected = None
    for prize in prizes:
        upper += prize.probability
        if ticket < upper:
            selected = prize
            break
    reward = RewardItem.objects.select_for_update().get(pk=selected.reward_id)
    wallet = reward_wallet(account, reward)
    profile = locked_profile(account)
    existing = LotteryDraw.objects.select_related('claim').filter(event_key=key).first()
    if existing:
        if existing.campaign_id != campaign.pk:
            raise ValidationError('同一操作凭证不能用于不同活动')
        return existing
    change_points(profile, -campaign.point_cost * 100, key, 'LOTTERY_COST', f'参与 {campaign.name}')
    claim = create_claim(account, reward, key, source='LOTTERY', profile=profile, wallet=wallet)
    result = LotteryDraw.objects.create(account=account, business_cycle=account.business_cycle, campaign=campaign, prize=selected, claim=claim,
        event_key=key, point_cost=campaign.point_cost, campaign_version=campaign.version, random_ticket=ticket,
        probability_snapshot=[{'id': p.pk, 'reward': p.reward_id, 'name': p.reward.name, 'probability': p.probability} for p in prizes])
    kind = {'SERVICE': 'LOTTERY_SERVICE', 'NAMING': 'LOTTERY_NAMING', 'TAG': 'LOTTERY_TAG'}.get(reward.kind, 'LOTTERY')
    if kind:
        broadcast(kind, key, account=account, reward=reward.name)
    return result


@transaction.atomic
def process_claim(claim_id, status, note, operator):
    # 先锁奖品再锁领取记录，与兑换/抽奖库存锁顺序一致。
    initial = RewardClaim.objects.get(pk=claim_id)
    reward = RewardItem.objects.select_for_update().get(pk=initial.reward_id)
    claim = RewardClaim.objects.select_for_update().select_related('account').get(pk=claim_id)
    transitions = {'PENDING': {'FULFILLING', 'COMPLETED', 'CANCELLED'}, 'FULFILLING': {'COMPLETED', 'CANCELLED'}}
    if claim.status == status:
        return claim
    if status not in transitions.get(claim.status, set()):
        raise ValidationError('该兑换记录不能执行此操作')
    if not note.strip():
        raise ValidationError('请填写交付或取消说明')
    if status == 'CANCELLED':
        if claim.source != 'REDEMPTION':
            raise ValidationError('抽奖奖励不得直接取消，请处理实际交付')
        profile = locked_profile(claim.account)
        change_points(profile, claim.points_spent * 100, f'claim:{claim.pk}:return', 'RETURN', '兑换取消退回积分', operator=operator)
        if reward.stock is not None:
            reward.stock += 1
        reward.redeemed_count = max(0, reward.redeemed_count - 1)
        reward.save(update_fields=['stock', 'redeemed_count'])
    if status == 'COMPLETED':
        claim.completed_at = timezone.now()
        if claim.duration_days:
            claim.expires_at = claim.completed_at + timedelta(days=claim.duration_days)
        if claim.duration_months:
            import calendar
            end = timezone.localtime(claim.completed_at)
            year, month = divmod(end.year * 12 + end.month - 1 + claim.duration_months, 12)
            claim.expires_at = end.replace(year=year, month=month+1, day=min(end.day, calendar.monthrange(year, month+1)[1]))
        if claim.kind_snapshot == 'TAG':
            title = str(claim.details.get('tag', '')).strip()
            if not title or len(title) > 30:
                raise ValidationError('请先填写用户希望展示的 TAG（最多 30 字）')
            MemberTag.objects.get_or_create(event_key=f'claim:{claim.pk}:tag', defaults={
                'account': claim.account, 'title': title, 'priority': 100,
                'highlighted': claim.highlighted, 'expires_at': claim.expires_at,
            })
        if claim.kind_snapshot == 'NAMING':
            broadcast('NAMING', f'claim:{claim.pk}:naming', account=claim.account, reward=claim.name_snapshot)
    claim.status = status
    claim.delivery_note = note[:255]
    claim.operator = operator
    claim.save(update_fields=['status', 'delivery_note', 'operator', 'completed_at', 'expires_at'])
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
    PointFundingLot.objects.get_or_create(source_key=f'recharge:{record.pk}', defaults={
        'account': account, 'remaining': record.amount + record.gift_amount,
        'points_rate': package.points_rate if package else 100,
    })
    broadcast('RECHARGE', f'recharge:{record.pk}', account=account)


def completed_consumption(account):
    """Only matured net paid non-gift consumption in the current business cycle."""
    from .models import ConsumptionConfirmation
    return ConsumptionConfirmation.objects.filter(account=account, business_cycle=account.business_cycle,
        order__payment_status='PAID').aggregate(value=Sum('amount'))['value'] or 0


@transaction.atomic
def update_vip(account):
    from club_accounts.models import ClubAccount
    from users.models import CustomUser
    if account.account_type != 'BOSS':
        return
    conf = config()
    if not conf.vip_enabled:
        return
    # 同一老板的升级串行化；结单与充值均遵循钱包、会员资料的锁顺序。
    from wallet.services import get_wallet
    get_wallet(account_id=account.pk, for_update=True)
    account.refresh_from_db(fields=['boss_type', 'business_cycle'])
    profile = locked_profile(account)
    total = completed_consumption(account)
    tiers = list(VipTier.objects.select_related('boss_type').filter(
        active=True, boss_type__is_active=True, recharge_threshold__lte=total,
    ).order_by('level'))
    if not tiers:
        return
    top = tiers[-1]
    if account.boss_type_id != top.boss_type_id:
        current = VipTier.objects.filter(boss_type_id=account.boss_type_id).first()
        if not current or current.level < top.level:
            ClubAccount.objects.filter(pk=account.pk).update(boss_type=top.boss_type)
            from club_accounts.models import LegacyAccountMap
            mapping = LegacyAccountMap.objects.filter(account=account).first()
            if mapping:
                CustomUser.objects.filter(pk=mapping.legacy_user_id).update(boss_type=top.boss_type)
            account.boss_type = top.boss_type
            broadcast('UPGRADE', f'vip:{account.pk}:{account.business_cycle}:{top.level}', account=account, level=top.boss_type.name)
    for tier in tiers:
        grant, new = MemberBenefitGrant.objects.get_or_create(event_key=f'vip:{account.pk}:{account.business_cycle}:{tier.level}:upgrade', defaults={
            'account': account, 'tier': tier, 'name': f'{tier.boss_type.name} 升级权益', 'kind': 'UPGRADE', 'details': tier.benefits,
        })
        if new:
            MemberTag.objects.get_or_create(event_key=f'vip:{account.pk}:{account.business_cycle}:{tier.level}:tag', defaults={
                'account': account, 'title': tier.boss_type.name, 'priority': tier.recharge_threshold,
                'highlighted': tier.level >= 4,
            })
            from coupons.benefits import issue
            variants = ['娱乐', '技术', '王者', '传奇', '传奇', '荣耀']
            quantities = [2, 2, 2, 2, 3, 4]
            if 1 <= tier.level <= 6:
                issue(account, f'vip-upgrade:{account.pk}:{account.business_cycle}:{tier.level}:companion',
                    name=f'{tier.boss_type.name} 陪玩赠券', rate=0, scope='COMPANION',
                    variant=variants[tier.level-1], quantity=quantities[tier.level-1], limit_units=1,
                    service_ids=tier.companion_service_ids or [])
    sync_vip_entitlements(account, top)
    from coupons.benefits import grant_periodic
    grant_periodic(account)


def sync_vip_entitlements(account, tier):
    """Keep delivery records; top up total quotas rather than repeating bundles."""
    level = tier.level
    prefix = f'vip-quota:{account.pk}:{account.business_cycle}:'
    configured = {}
    for prior in VipTier.objects.filter(active=True, level__lte=level).order_by('level'):
        for bundle in prior.upgrade_rewards:
            reward = RewardItem.objects.get(pk=bundle['reward'])
            family = reward.kind if reward.kind in ('TAG', 'NAMING') else (reward.vip_quota_kind or reward.code)
            configured[family] = (reward, bundle['quantity'])
    quotas = {'TAG':[1,1,2,2,3,5], 'FRIEND':[0,0,1,2,5,10], 'NAMING':[3,6,6,6,10,12]}
    for family, (reward, configured_count) in configured.items():
        if family == 'BIRTHDAY_GIFT':
            continue  # issued on the birthday, not on every upgrade
        target = quotas[family][level-1] if family in quotas else configured_count
        for index in range(target):
            grant_entitlement(account, reward, f'{prefix}{family}:{index}', 'VIP',
                {'business_cycle': account.business_cycle, 'quota_kind': family})
        claims = RewardClaim.objects.filter(account=account, event_key__startswith=f'{prefix}{family}:').order_by('pk')
        if family == 'NAMING':
            claims.filter(status__in=['PENDING','FULFILLING']).update(duration_days=7 if level < 4 else 0,
                duration_months=0 if level < 4 else 1)
        elif family == 'TAG':
            high = [0,0,0,1,2,5][level-1]
            for index, claim in enumerate(claims):
                claim.highlighted = index < high
                claim.duration_days = claim.duration_months = 0
                claim.save(update_fields=['highlighted','duration_days','duration_months'])
                MemberTag.objects.filter(event_key=f'claim:{claim.pk}:tag').update(highlighted=claim.highlighted)


def grant_entitlement(account, reward, key, source, details=None, source_order=None):
    """VIP/赠礼权益不占积分商品库存；仍进入同一客服交付闭环。"""
    return RewardClaim.objects.get_or_create(event_key=key, defaults={
        'account': account, 'reward': reward, 'source': source, 'name_snapshot': reward.name,
        'kind_snapshot': reward.kind, 'duration_days': reward.duration_days,
        'duration_months': reward.duration_months, 'highlighted': reward.highlighted,
        'details': details or {}, 'source_order': source_order,
    })[0]


def award_gift_benefits(order):
    from orders.models import Order
    broadcast('GIFT', f'gift:{order.pk}', account=order.customer_account,
              provider=order.provider_name_snapshot,
              reward=f'{order.service_name_snapshot or order.service.name} × {order.game_rounds}')
    now = order.created_at
    rules = GiftBenefitRule.objects.filter(enabled=True).select_related('reward').filter(
        Q(category__isnull=True) | Q(category_id=order.service.service_category_id)).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    for rule in rules:
        total = order.amount
        if rule.cumulative:
            history = Order.objects.filter(customer_account=order.customer_account, service__service_category__is_gift=True,
                status='COMPLETED', payment_status='PAID', created_at__lte=now)
            if rule.category_id: history=history.filter(service__service_category_id=rule.category_id)
            if rule.starts_at: history=history.filter(created_at__gte=rule.starts_at)
            total=history.aggregate(value=Sum('amount'))['value'] or 0
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
    now = timezone.now()
    naming, _ = ProviderNaming.objects.get_or_create(provider=order.provider_account, sponsor=order.customer_account,
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
    broadcast('NAMING', f'gift-naming:{order.pk}', account=order.customer_account,
        provider=order.provider_name_snapshot, reward=f'{order.provider_name_snapshot} · {title}，延长 {days} 天')
    return grant


@transaction.atomic
def record_external_points(account, *, kind, source_id, day, units, operator):
    """人工核对的 KOOK 明细；未来机器人复用同一事件键，重复回传不重复加分。"""
    if kind not in ('KOOK_ONLINE', 'KOOK_CHECKIN'):
        raise ValidationError('积分来源不受支持')
    from .kook_points import enabled_config
    if enabled_config():
        raise ValidationError('KOOK自动计分已开启，请勿人工重复记分')
    if day > timezone.localdate() or units < 1 or units > 86400:
        raise ValidationError('日期或有效在线秒数不合法')
    amount = 500 if kind == 'KOOK_CHECKIN' else (units // 7200) * 100
    if amount <= 0:
        raise ValidationError('有效在线不足 2 小时')
    profile = locked_profile(account)
    key = f'{kind}:{account.pk}:{day.isoformat()}'
    return change_points(profile, amount, key, kind, f'KOOK 记录 {source_id[:80]}', operator=operator)
