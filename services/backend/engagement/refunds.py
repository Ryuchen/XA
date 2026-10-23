"""Reverse refundable order benefits without erasing already-consumed history."""
import re
from django.db.models import Q, Sum
from django.utils import timezone
from club_accounts.models import ClubAccount, LegacyAccountMap
from coupons.models import Coupon, UserCoupon
from users.models import CustomUser, EscortProfile
from .models import ConsumptionConfirmation, MemberBenefitGrant, MemberTag, RewardClaim, RewardItem, VipTier, GiftNamingGrant, ProviderNaming, GiftBenefitRule
from .services import completed_consumption


def _locked_refund_claims(order, *, include_vip):
    """Lock affected claims before account/tag updates, like delivery does.

    A cumulative gift claim can belong to a later gift order. Recheck the
    original qualification window after excluding this refunded payment.
    """
    candidates = Q(source='GIFT',source_order=order)
    cumulative = {}
    category = order.service.service_category
    if order.customer_account_id and category and category.is_gift:
        rules = GiftBenefitRule.objects.filter(cumulative=True).filter(
            Q(category__isnull=True)|Q(category=category)).filter(
            Q(starts_at__isnull=True)|Q(starts_at__lte=order.created_at)).filter(
            Q(ends_at__isnull=True)|Q(ends_at__gt=order.created_at))
        cumulative = {rule.pk:rule for rule in rules}
        for rule_id in cumulative:
            candidates |= Q(source='GIFT',account_id=order.customer_account_id,
                            event_key__startswith=f'gift-benefit:{rule_id}:')
    if include_vip:
        candidates |= Q(source='VIP',account_id=order.customer_account_id)
    # No select_related: a locking join would also lock account/source orders.
    claims = list(RewardClaim.objects.select_for_update().filter(candidates).order_by('pk'))
    from orders.refunds import refunded_amount,with_net_amount
    current_net = order.amount-refunded_amount(order)
    invalid_gifts = {claim.pk for claim in claims if claim.source == 'GIFT' and claim.source_order_id == order.pk and current_net == 0}
    from orders.models import Order
    for claim in claims:
        match = re.match(r'^gift-benefit:(\d+):',claim.event_key)
        rule = GiftBenefitRule.objects.filter(pk=int(match[1])).first() if match else None
        if claim.source != 'GIFT' or not rule or not claim.source_order_id or claim.pk in invalid_gifts:
            continue
        awarded_at = claim.source_order.created_at
        if not rule.cumulative:
            if claim.source_order_id == order.pk and (current_net < rule.threshold or (current_net == rule.threshold and not rule.inclusive)):
                invalid_gifts.add(claim.pk)
            continue
        # Later refunds cannot affect an earlier claim's qualification.
        if order.created_at > awarded_at:
            continue
        history = with_net_amount(Order.objects.filter(customer_account_id=order.customer_account_id,
            service__service_category__is_gift=True,status='COMPLETED',payment_status__in=['PAID','PARTIALLY_REFUNDED'],
            created_at__lte=awarded_at))
        if rule.category_id:
            history = history.filter(service__service_category_id=rule.category_id)
        if rule.starts_at:
            history = history.filter(created_at__gte=rule.starts_at)
        total = history.aggregate(value=Sum('_net_paid_amount'))['value'] or 0
        if total < rule.threshold or (total == rule.threshold and not rule.inclusive):
            invalid_gifts.add(claim.pk)
    return claims, invalid_gifts


def reverse_refund_benefits(order, refund, operator):
    now = timezone.now()
    effects = {'confirmation_reversed':False,'expired_coupon_ids':[], 'cancelled_claim_ids':[],
               'consumed_coupon_ids':[],'delivered_claim_ids':[],'deactivated_tag_ids':[],
               'consumed_benefits_policy':'已用券和已完成交付保留原历史并列明；不向其他订单追债'}
    confirmation = ConsumptionConfirmation.objects.select_for_update().filter(order=order).first()
    from orders.refunds import refunded_amount
    cumulative = refunded_amount(order)
    full = cumulative >= order.amount
    if confirmation and not confirmation.reversed_at:
        confirmation.refunded_amount = min(confirmation.amount,cumulative)
        if full:
            confirmation.reversed_at, confirmation.reversed_by_refund = now, refund
            effects['confirmation_reversed'] = True
        confirmation.save(update_fields=['refunded_amount','reversed_at','reversed_by_refund'])
    claims, invalid_gifts = _locked_refund_claims(order,include_vip=bool(confirmation))
    legacy_gift = order.transactions.filter(tx_type='GIFT',amount__gte=0,reversal_of__isnull=True).exists()
    if full and order.settled_at and not legacy_gift:
        provider_ids = {row.provider_id for row in order.providers.all() if row.provider_id}
        if not provider_ids and order.provider_id:
            provider_ids.add(order.provider_id)
        for profile in EscortProfile.objects.select_for_update().filter(user_id__in=provider_ids).order_by('user_id'):
            profile.completed_order_count = max(0, profile.completed_order_count-1)
            profile.save(update_fields=['completed_order_count'])
    for claim in claims:
        if claim.pk not in invalid_gifts:
            continue
        claim.details = {**claim.details,'refund_revoked_by':refund.pk,'refund_revoked_at':now.isoformat()}
        if claim.status in ('PENDING','FULFILLING'):
            claim.status,claim.operator,claim.delivery_note = 'CANCELLED',operator,'赠礼退款，未交付权益已撤销'
            effects['cancelled_claim_ids'].append(claim.pk)
        elif claim.status == 'COMPLETED':
            effects['delivered_claim_ids'].append(claim.pk)
        claim.save(update_fields=['details','status','operator','delivery_note'])
        if claim.kind_snapshot == 'TAG':
            tags = MemberTag.objects.filter(event_key=f'claim:{claim.pk}:tag',is_active=True)
            effects['deactivated_tag_ids'] += list(tags.values_list('pk',flat=True))
            tags.update(is_active=False)
    naming_grant = GiftNamingGrant.objects.filter(order=order).first()
    if naming_grant:
        from datetime import timedelta
        naming = ProviderNaming.objects.select_for_update().get(pk=naming_grant.naming_id)
        grants = list(GiftNamingGrant.objects.filter(naming=naming).select_related('order').order_by('created_at','pk'))
        remaining = [(item,max(0,item.order.amount-refunded_amount(item.order))) for item in grants
                     if item.order.payment_status in ('PAID','PARTIALLY_REFUNDED')]
        remaining = [(item,net) for item,net in remaining if net > 0]
        expiry = grants[0].expires_at-timedelta(days=grants[0].days)
        for item,net in remaining:
            expiry = max(expiry,item.created_at)+timedelta(seconds=item.days*86400*net//item.order.amount)
        effects['gift_naming'] = {'grant_id':naming_grant.pk,'previous_expires_at':naming.expires_at.isoformat(),
                                 'expires_at':expiry.isoformat(),'remaining_paid_grants':len(remaining)}
        naming.expires_at = expiry
        if remaining:
            naming.title = remaining[-1][0].title_snapshot
        naming.save(update_fields=['expires_at','title','updated_at'])
    if not confirmation:
        # Without a confirmed consumption there is no earned VIP change to
        # undo. Unrelated cancellations and gifts cannot downgrade a VIP.
        return effects
    account = order.customer_account
    if not account:
        return effects
    account.refresh_from_db(fields=['business_cycle','boss_type'])
    if account.business_cycle != order.business_cycle:
        effects['old_business_cycle'] = True
        return effects
    total = completed_consumption(account)
    top = VipTier.objects.filter(active=True,boss_type__is_active=True,recharge_threshold__lte=total).order_by('-level').first()
    current = VipTier.objects.filter(boss_type_id=account.boss_type_id).first()
    level = top.level if top else 0
    effects.update(net_consumption=total,previous_boss_type_id=account.boss_type_id)
    if current and current.level > level:
        new_type = top.boss_type_id if top else None
        ClubAccount.objects.filter(pk=account.pk).update(boss_type_id=new_type)
        mapping = LegacyAccountMap.objects.filter(account=account).first()
        if mapping:
            CustomUser.objects.filter(pk=mapping.legacy_user_id).update(boss_type_id=new_type)
        account.boss_type_id = new_type
    effects['boss_type_id'] = account.boss_type_id
    # Keep original grants as audit records. Previously consumed coupons and
    # physically delivered claims are disclosed, never converted to hidden debt.
    for grant in MemberBenefitGrant.objects.select_for_update().filter(account=account,tier__level__gt=level,
                event_key__startswith=f'vip:{account.pk}:{account.business_cycle}:'):
        grant.details = {**grant.details,'refund_revoked_by':refund.pk,'refund_revoked_at':now.isoformat()}
        grant.save(update_fields=['details'])
    upgrade = re.compile(rf'^vip-upgrade:{account.pk}:{account.business_cycle}:(\d+):')
    month = re.compile(rf'^vip-month:{account.pk}:{account.business_cycle}:\d{{4}}-\d{{2}}:(\d+):')
    monthly_count = {0:0,1:0,2:1,3:2,4:4,5:10,6:10}.get(level,0)
    for row in UserCoupon.objects.select_for_update().filter(account=account,business_cycle=account.business_cycle).select_related('coupon'):
        key = row.coupon.grant_key
        match, monthly = upgrade.match(key), month.match(key)
        revoke = bool((match and int(match[1]) > level) or (monthly and int(monthly[1]) >= monthly_count))
        revoke |= level == 0 and key.startswith(f'vip-safe:{account.pk}:{account.business_cycle}:')
        revoke |= level < 4 and key.startswith(f'vip-birthday:{account.pk}:{account.business_cycle}:')
        if revoke:
            if row.status == 'UNUSED':
                row.status = 'EXPIRED'
                row.save(update_fields=['status'])
                effects['expired_coupon_ids'].append(row.pk)
            elif row.status == 'USED':
                effects['consumed_coupon_ids'].append(row.pk)
        elif row.status == 'UNUSED' and monthly:
            Coupon.objects.filter(pk=row.coupon_id).update(discount_rate=85 if level == 6 else 90)
        elif row.status == 'UNUSED' and level and key.startswith(f'vip-safe:{account.pk}:{account.business_cycle}:'):
            Coupon.objects.filter(pk=row.coupon_id).update(discount_rate={1:80,2:80,3:80,4:70,5:60,6:0}[level],
                                                         service_ids=top.safe_box_service_ids or [])
    tag_prefix = f'vip:{account.pk}:{account.business_cycle}:'
    for tag in MemberTag.objects.select_for_update().filter(account=account,event_key__startswith=tag_prefix,is_active=True):
        parts = tag.event_key[len(tag_prefix):].split(':')
        if parts[0].isdigit() and int(parts[0]) > level:
            tag.is_active = False
            tag.save(update_fields=['is_active'])
            effects['deactivated_tag_ids'].append(tag.pk)
    quotas = {'TAG':[0,1,1,2,2,3,5], 'FRIEND':[0,0,0,1,2,5,10], 'NAMING':[0,3,6,6,6,10,12]}
    targets = {family:values[min(level,6)] for family,values in quotas.items()}
    for tier in VipTier.objects.filter(active=True,level__lte=level).order_by('level'):
        for bundle in tier.upgrade_rewards:
            reward = RewardItem.objects.get(pk=bundle['reward'])
            family = reward.kind if reward.kind in ('TAG','NAMING') else (reward.vip_quota_kind or reward.code)
            if family not in quotas:
                targets[family] = bundle['quantity']
    prefix = f'vip-quota:{account.pk}:{account.business_cycle}:'
    for claim in claims:
        if claim.source != 'VIP':
            continue
        revoke = claim.source_order_id == order.pk
        revoke |= level < 4 and claim.event_key.startswith(f'vip-birthday-gift:{account.pk}:{account.business_cycle}:')
        if claim.event_key.startswith(prefix):
            family, _, index = claim.event_key[len(prefix):].partition(':')
            target = targets.get(family,0)
            revoke |= index.isdigit() and int(index) >= target
        if not revoke:
            continue
        claim.details = {**claim.details,'refund_revoked_by':refund.pk,'refund_revoked_at':now.isoformat()}
        if claim.status in ('PENDING','FULFILLING'):
            claim.status, claim.operator = 'CANCELLED', operator
            claim.delivery_note = '订单退款，未交付权益已撤销'
            effects['cancelled_claim_ids'].append(claim.pk)
        elif claim.status == 'COMPLETED':
            effects['delivered_claim_ids'].append(claim.pk)
        claim.save(update_fields=['details','status','operator','delivery_note'])
        if claim.kind_snapshot == 'TAG':
            tags = MemberTag.objects.filter(event_key=f'claim:{claim.pk}:tag',is_active=True)
            effects['deactivated_tag_ids'] += list(tags.values_list('pk',flat=True))
            tags.update(is_active=False)
    return effects


def restore_requalified_benefits(account, top):
    """Restore only unused rights explicitly revoked by a prior refund."""
    from orders.models import OrderRefund
    effects = list(OrderRefund.objects.filter(order__customer_account=account,
                   order__business_cycle=account.business_cycle).values_list('benefit_effects',flat=True))
    if not effects:
        return
    now, level = timezone.now(), top.level
    coupon_ids = {pk for item in effects for pk in item.get('expired_coupon_ids',[])}
    prefix = f'{account.pk}:{account.business_cycle}:'
    count = {1:0,2:1,3:2,4:4,5:10,6:10}.get(level,0)
    for row in UserCoupon.objects.select_for_update().filter(pk__in=coupon_ids,account=account,status='EXPIRED').select_related('coupon'):
        key = row.coupon.grant_key
        upgrade = re.match(r'^vip-upgrade:'+re.escape(prefix)+r'(\d+):',key)
        month = re.match(r'^vip-month:'+re.escape(prefix)+r'\d{4}-\d{2}:(\d+):',key)
        eligible = bool((upgrade and int(upgrade[1]) <= level) or (month and int(month[1]) < count)
                        or key.startswith('vip-safe:'+prefix) or (level >= 4 and key.startswith('vip-birthday:'+prefix)))
        if eligible and (row.valid_to is None or row.valid_to > now) and row.coupon.is_active:
            row.status = 'UNUSED'
            row.save(update_fields=['status'])
    targets = {family:values[level-1] for family,values in {
        'TAG':[1,1,2,2,3,5],'FRIEND':[0,0,1,2,5,10],'NAMING':[3,6,6,6,10,12]}.items()}
    for tier in VipTier.objects.filter(active=True,level__lte=level).order_by('level'):
        for bundle in tier.upgrade_rewards:
            reward = RewardItem.objects.get(pk=bundle['reward'])
            family = reward.kind if reward.kind in ('TAG','NAMING') else (reward.vip_quota_kind or reward.code)
            if family not in targets:
                targets[family] = bundle['quantity']
    claim_ids = {pk for item in effects for pk in item.get('cancelled_claim_ids',[])}
    for claim in RewardClaim.objects.select_for_update().filter(pk__in=claim_ids,account=account,status='CANCELLED',source='VIP',source_order__isnull=True):
        quota = re.match(r'^vip-quota:'+re.escape(prefix)+r'([^:]+):(\d+)$',claim.event_key)
        eligible = bool(quota and int(quota[2]) < targets.get(quota[1],0))
        eligible |= level >= 4 and claim.event_key.startswith('vip-birthday-gift:'+prefix)
        if eligible and claim.details.get('refund_revoked_by'):
            claim.status, claim.operator = 'PENDING', None
            claim.delivery_note = '重新达标，恢复原未交付权益'
            claim.details = {**claim.details,'refund_rights_restored_at':now.isoformat()}
            claim.save(update_fields=['status','operator','delivery_note','details'])
    tag_ids = {pk for item in effects for pk in item.get('deactivated_tag_ids',[])}
    for tag in MemberTag.objects.select_for_update().filter(pk__in=tag_ids,account=account,is_active=False):
        match = re.match(r'^vip:'+re.escape(prefix)+r'(\d+):tag$',tag.event_key)
        if match and int(match[1]) <= level and (not tag.expires_at or tag.expires_at > now):
            tag.is_active = True
            tag.save(update_fields=['is_active'])
