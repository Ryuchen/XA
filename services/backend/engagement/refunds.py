"""Reverse refundable order benefits without erasing already-consumed history."""
import re
from stores.db import transaction
from django.utils import timezone
from club_accounts.models import ClubAccount, LegacyAccountMap
from coupons.models import Coupon, UserCoupon
from users.models import CustomUser
from .models import ConsumptionConfirmation, MemberTag, RewardClaim, RewardItem, VipTier, GiftNamingGrant, ProviderNaming
from .services import completed_consumption


def notify_claim_changes(account_id, count, *, business_cycle, restored=False):
    if not account_id or not count:
        return
    recipient_id = LegacyAccountMap.objects.filter(account_id=account_id).values_list('legacy_user_id', flat=True).first()
    from orders.services import push_message_safe
    title = '权益已恢复' if restored else '权益状态已调整'
    preview = (f'重新达标，{count} 项未交付权益已恢复，可在权益记录中查看。' if restored
               else f'退款后，{count} 项未交付权益已撤销，可在权益记录中查看。')
    transaction.on_commit(lambda: push_message_safe(recipient_id=recipient_id, recipient_account_id=account_id,
        title=title, preview=preview, msg_type='SYSTEM', action_url='/pages/points/index?tab=claims',
        recipient_business_cycle=business_cycle, require_cycle=True))


def lock_refund_vip_operation():
    """Lock current terms after the refund's wallet locks, before coupon writes."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('会员退款条款须在退款事务中锁定')
    from .models import ClubOperationConfig
    from .services import config
    config()
    return ClubOperationConfig.objects.select_for_update().get(pk=1)


def reverse_refund_benefits(order, refund, operator, *, vip_operation=None):
    now = timezone.now()
    effects = {'confirmation_reversed':False,'expired_coupon_ids':[], 'cancelled_claim_ids':[], 'gift_claim_results':[],
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
    legacy_gift = order.transactions.filter(tx_type='GIFT',amount__gte=0,reversal_of__isnull=True).exists()
    # A refund changes money, not the fact that an already-counted service was
    # performed. In particular an all-refunded order must not erase a
    # participant's previously recorded completed-order count.
    # Gift entitlements, including an entitlement earned by a later cumulative
    # gift, remain valid even when this refund makes the threshold no longer
    # hold. Preserve their existing delivery and moderation state verbatim.
    if order.is_gift_snapshot or legacy_gift:
        effects['gift_rights_preserved'] = True
    naming_grant = GiftNamingGrant.objects.filter(order=order).first()
    if naming_grant:
        naming = ProviderNaming.objects.select_for_update().get(pk=naming_grant.naming_id)
        effects['gift_naming'] = {'grant_id':naming_grant.pk, 'preserved':True,
                                  'expires_at':naming.expires_at.isoformat()}
    if not confirmation:
        # Without a confirmed consumption there is no earned VIP change to
        # undo. Unrelated cancellations and gifts cannot downgrade a VIP.
        return effects
    account = order.customer_account
    if not account:
        return effects
    return recalculate_vip_after_refund(account, order.business_cycle, effects=effects, vip_operation=vip_operation)


def recalculate_vip_after_refund(account, business_cycle, *, effects=None, vip_operation=None):
    """Apply the same net-consumption downgrade to main and add-on refunds."""
    effects = effects if effects is not None else {'deactivated_tag_ids': []}
    account.refresh_from_db(fields=['business_cycle','boss_type'])
    if account.business_cycle != business_cycle:
        effects['old_business_cycle'] = True
        return effects
    total = completed_consumption(account)
    top = VipTier.objects.filter(active=True,boss_type__is_active=True,recharge_threshold__lte=total).order_by('-level').first()
    current = VipTier.objects.filter(boss_type_id=account.boss_type_id).first()
    level = top.level if top else 0
    effects.update(net_consumption=total,previous_boss_type_id=account.boss_type_id)
    if current and current.level > level:
        from .vip_transition import freeze_existing_vip_terms, transition_guard_available, transition_guard_enabled
        if transition_guard_enabled() or vip_operation is not None:
            if transition_guard_enabled() and not transition_guard_available():
                from rest_framework.exceptions import ValidationError
                raise ValidationError('本店会员权益规则不可用，请先核对后退款')
            if vip_operation is None:
                from .models import ClubOperationConfig
                vip_enabled = ClubOperationConfig.objects.get(pk=1).vip_enabled
            else:
                # The ordinary refund transaction has already locked the
                # actual singleton before it reaches coupon and grade writes.
                vip_enabled = vip_operation.vip_enabled
            freeze_existing_vip_terms(vip_enabled=vip_enabled, account_id=account.pk)
        # VIP grant coupons have a private grant_key per account. Lock both
        # unused and already-used coupons so a later refund restoring a used
        # coupon cannot silently replace its original terms on the next visit.
        from django.db.models import Q
        cycle = account.business_cycle
        vip_keys = Q()
        for kind in ('upgrade', 'month', 'safe', 'birthday'):
            vip_keys |= Q(coupon__grant_key__startswith=f'vip-{kind}:{account.pk}:{cycle}:')
        issued = UserCoupon.objects.filter(vip_keys, account=account, business_cycle=cycle)
        coupon_ids = list(issued.order_by('coupon_id').values_list('coupon_id', flat=True).distinct())
        # Coupon administration locks the template before checking this flag.
        # Serialize with it so a simultaneous edit cannot overwrite the terms
        # that were current at the point of downgrade.
        list(Coupon.objects.select_for_update().filter(pk__in=coupon_ids).order_by('pk').values_list('pk', flat=True))
        locked_ids = list(issued.select_for_update().order_by('pk').values_list('pk', flat=True))
        issued.filter(pk__in=locked_ids).update(vip_terms_locked_by_downgrade=True)
        effects['vip_rights_preserved'] = True
        effects['vip_terms_locked_coupon_ids'] = locked_ids
        new_type = top.boss_type_id if top else None
        ClubAccount.objects.filter(pk=account.pk).update(boss_type_id=new_type)
        mapping = LegacyAccountMap.objects.filter(account=account).first()
        if mapping:
            CustomUser.objects.filter(pk=mapping.legacy_user_id).update(boss_type_id=new_type)
        account.boss_type_id = new_type
    effects['boss_type_id'] = account.boss_type_id
    eligible_tier_ids = set(VipTier.objects.filter(active=True, boss_type__is_active=True,
        recharge_threshold__lte=total).values_list('pk', flat=True))
    tag_prefix = f'vip:{account.pk}:{account.business_cycle}:'
    for tag in MemberTag.objects.select_for_update().filter(account=account,event_key__startswith=tag_prefix,is_active=True):
        parts = tag.event_key[len(tag_prefix):].split(':')
        if parts[0].isdigit() and int(parts[0]) not in eligible_tier_ids:
            tag.is_active = False
            tag.save(update_fields=['is_active'])
            effects['deactivated_tag_ids'].append(tag.pk)
    return effects


def restore_requalified_benefits(account, top):
    """Restore only unused rights explicitly revoked by a prior refund."""
    from orders.models import OrderRefund
    effects = list(OrderRefund.objects.filter(order__customer_account=account,
                   order__business_cycle=account.business_cycle).values_list('benefit_effects',flat=True))
    if not effects:
        return
    now, level = timezone.now(), top.level
    from .vip_policy import issuance_policy
    policy = issuance_policy(top)
    coupon_ids = {pk for item in effects for pk in item.get('expired_coupon_ids',[])}
    prefix = f'{account.pk}:{account.business_cycle}:'
    eligible_tier_ids = set(VipTier.objects.filter(active=True, boss_type__is_active=True,
        level__lte=level).values_list('pk', flat=True))
    count = top.monthly_coupon_count
    for row in UserCoupon.objects.select_for_update().filter(pk__in=coupon_ids,account=account,status='EXPIRED').select_related('coupon'):
        key = row.coupon.grant_key
        upgrade = re.match(r'^vip-upgrade:'+re.escape(prefix)+r'(\d+):',key)
        month = re.match(r'^vip-month:'+re.escape(prefix)+r'\d{4}-\d{2}:(\d+):',key)
        eligible = bool((upgrade and int(upgrade[1]) in eligible_tier_ids) or (month and int(month[1]) < count)
                        or key.startswith('vip-safe:'+prefix) or (policy['birthday'] and key.startswith('vip-birthday:'+prefix)))
        if eligible and (row.valid_to is None or row.valid_to > now) and row.coupon.is_active:
            row.status = 'UNUSED'
            row.save(update_fields=['status'])
    targets = ({} if policy['template_level'] is None else
        {family:values[policy['template_level']-1] for family,values in {
            'TAG':[1,1,2,2,3,5],'FRIEND':[0,0,1,2,5,10],'NAMING':[3,6,6,6,10,12]}.items()})
    for tier in VipTier.objects.filter(active=True,level__lte=level).order_by('level'):
        for bundle in tier.upgrade_rewards:
            reward = RewardItem.objects.get(pk=bundle['reward'])
            family = reward.kind if reward.kind in ('TAG','NAMING') else (reward.vip_quota_kind or reward.code)
            if policy['template_level'] is None or family not in targets:
                targets[family] = bundle['quantity']
    claim_ids = {pk for item in effects for pk in item.get('cancelled_claim_ids',[])}
    restored_claims = 0
    for claim in RewardClaim.objects.select_for_update().filter(pk__in=claim_ids,account=account,status='CANCELLED',source='VIP',source_order__isnull=True):
        quota = re.match(r'^vip-quota:'+re.escape(prefix)+r'([^:]+):(\d+)$',claim.event_key)
        eligible = bool(quota and int(quota[2]) < targets.get(quota[1],0))
        eligible |= bool(policy['birthday_reward_quantity'] and claim.event_key.startswith('vip-birthday-gift:'+prefix))
        if eligible and claim.details.get('refund_revoked_by'):
            claim.status, claim.operator = 'PENDING', None
            claim.delivery_note = '重新达标，恢复原未交付权益'
            claim.details = {**claim.details,'refund_rights_restored_at':now.isoformat()}
            claim.save(update_fields=['status','operator','delivery_note','details'])
            restored_claims += 1
    if restored_claims:
        from .notifier import notify_claims_after_commit
        notify_claims_after_commit()
        notify_claim_changes(account.pk, restored_claims, business_cycle=account.business_cycle, restored=True)
    tag_ids = {pk for item in effects for pk in item.get('deactivated_tag_ids',[])}
    for tag in MemberTag.objects.select_for_update().filter(pk__in=tag_ids,account=account,is_active=False):
        match = re.match(r'^vip:'+re.escape(prefix)+r'(\d+):tag$',tag.event_key)
        if match and int(match[1]) in eligible_tier_ids and (not tag.expires_at or tag.expires_at > now):
            tag.is_active = True
            tag.save(update_fields=['is_active'])
