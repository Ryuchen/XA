"""Provider retirement preparation; execution remains closed pending full barriers.

The observations here are conservative and must be recomputed under the
exclusive lifecycle lock. They do not make the unfinished coverage safe.
"""
import hashlib
import re

from django.core import signing
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction
from .models import ClubAccount
from .provider_lifecycle import ensure_provider_lifecycle, lock_provider_closure


_CONFIRMATION_SALT = 'provider-retirement-v1'


def retirement_enabled():
    from django.conf import settings
    return bool(getattr(settings, 'PROVIDER_RETIREMENT_ENABLED', False)
                and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def _authorize(account, operator):
    from console.permissions import get_account_permissions, is_console_account
    store = current_store()
    if (store is None or not isinstance(account, ClubAccount) or not isinstance(operator, ClubAccount)
            or account._state.db != store.database_alias or operator._state.db != store.database_alias):
        raise ValidationError('无法确定本店账号或操作人')
    actor = ClubAccount.objects.filter(pk=operator.pk, account_type=ClubAccount.AccountType.STAFF).first()
    if not actor or not is_console_account(actor) or 'escort:retire' not in get_account_permissions(actor):
        raise ValidationError('无权办理本店陪玩注销')
    if account.account_type != ClubAccount.AccountType.PROVIDER:
        raise ValidationError('仅可办理本店陪玩注销')
    return actor


def clearance_facts(account, *, aftersale_windows=None):
    """Collect conservative, store-local blockers without changing funds."""
    from engagement.models import MemberProfile
    from orders.applications_models import OrderApplication
    from orders.models import (Order, Reservation, OrderContinuation, OrderGrabSingleAdmission,
                               OrderGrabTeamDraft, OrderGrabReceiptProbe)
    from users.models import EscortProfile
    from wallet.models import Wallet, WithdrawRequest, DepositRefundApplication
    from wallet.refund_deposit_models import RefundDepositDeficit

    if (current_store() is None or account.account_type != ClubAccount.AccountType.PROVIDER
            or account._state.db != current_store().database_alias):
        raise ValidationError('无法确定本店陪玩账号')
    profile = EscortProfile.objects.filter(account=account).first()
    wallet = Wallet.objects.filter(account=account).first()
    member = MemberProfile.objects.filter(account=account).first()
    blockers = []
    # Delivery and cancellation already serialize on this lifecycle lock.
    # Retain an actual undelivered entitlement until staff can finish it or
    # return its points to the same membership cycle.
    from engagement.models import RewardClaim
    pending_claims, claims_to_review = [], []
    for claim in RewardClaim.objects.filter(account=account, status__in=['PENDING', 'FULFILLING']).order_by('pk'):
        parts = claim.event_key.split(':', 3) if isinstance(claim.event_key, str) else []
        if (claim.source != 'REDEMPTION' or len(parts) != 4 or parts[0] != 'redeem'
                or not parts[1].isascii() or not parts[1].isdigit() or int(parts[1]) != account.pk
                or not parts[2].isascii() or not parts[2].isdigit() or int(parts[2]) < 1 or not parts[3]):
            claims_to_review.append(claim.pk)
        elif int(parts[2]) == account.business_cycle:
            pending_claims.append({'id': claim.pk, 'name': claim.name_snapshot, 'status': claim.status})
    if pending_claims:
        blockers.append('仍有积分兑换权益待交付或退回')
    if claims_to_review:
        blockers.append('未交付权益来源周期待核对')
    if aftersale_windows is None:
        from .retirement_aftersale import lock_candidate_orders, window_facts
        with transaction.atomic():
            aftersale_windows = window_facts(account, lock_candidate_orders(account))
    if aftersale_windows['open_order_ids']:
        blockers.append('仍有本人售后窗口未结束')
    if aftersale_windows['review_order_ids']:
        blockers.append('售后窗口或终止范围凭证待核对')
    if not account.license_membership_active:
        blockers.append('陪玩已退出门店')
    if not profile:
        blockers.append('陪玩资料不存在，请先核对')
    if not wallet:
        blockers.append('陪玩钱包不存在，请先核对')
    if profile and wallet:
        if wallet.user_id not in (None, profile.user_id):
            blockers.append('钱包归属不一致，请先核对')
        if Wallet.objects.filter(user_id=profile.user_id).exclude(pk=wallet.pk).exists():
            blockers.append('存在其他关联钱包，请先核对')
    if wallet and (wallet.balance or wallet.frozen_amount):
        blockers.append('钱包余额或冻结资金未结清')
    if profile and profile.deposit_paid:
        blockers.append('已缴押金尚未退清')
    pending_withdrawals = WithdrawRequest.objects.filter(
        Q(account=account) | (Q(user_id=profile.user_id) if profile else Q(pk__in=[])),
        status__in=['PENDING', 'APPROVED', 'STORE_APPROVED', 'PLATFORM_APPROVED', 'PAYMENT_UNKNOWN']).count()
    if pending_withdrawals:
        blockers.append('仍有待处理提现')
    pending_deposit_applications = DepositRefundApplication.objects.filter(
        account=account, business_cycle=account.business_cycle, status='PENDING').count()
    if pending_deposit_applications:
        blockers.append('仍有待处理退押金申请')
    reservations = Reservation.objects.filter(Q(provider_account=account) | Q(seats__provider_account=account),
        status__in=['PENDING', 'CONFIRMED']).distinct().count()
    if reservations:
        blockers.append('仍有未完成预约')
    applications = OrderApplication.objects.filter(Q(applicant=account) | Q(partner=account), status='PENDING').count()
    if applications:
        blockers.append('仍有待处理接单申请')
    active_single = OrderGrabSingleAdmission.objects.filter(
        Q(round__single_state__status__in=['OPEN', 'PARKED', 'REVIEW']) | Q(round__single_state__isnull=True),
        account=account, status='ACTIVE', round__pool__order__status='PENDING').count()
    active_team = OrderGrabTeamDraft.objects.filter(Q(captain=account) | Q(partner=account),
        Q(round__team_state__status__in=['OPEN', 'PARKED', 'REVIEW']) | Q(round__team_state__isnull=True),
        status__in=['INVITING', 'UNVERIFIED'], round__pool__order__status='PENDING').distinct().count()
    pending_receipts = OrderGrabReceiptProbe.objects.filter(actor=account, kind__in=['SINGLE', 'INVITE', 'CONFIRM'],
        state__in=['PENDING', 'WAIT_OPENING', 'REVIEW'], round__pool__order__status='PENDING').count()
    if active_single or active_team or pending_receipts:
        blockers.append('仍有抢单或组队待处理')
    continuation_scope = Q(continuation_order__providers__provider_business_cycle=account.business_cycle) | Q(
        continuation_order__providers__provider_business_cycle__isnull=True)
    pending_continuations = OrderContinuation.objects.filter(continuation_scope,
        continuation_order__providers__provider_account=account,
        status='PENDING').exclude(reward_state__in=['PAID', 'INELIGIBLE']).distinct().count()
    # Verification persists the original reward participants. The current
    # assignment can subsequently change, so it cannot identify every person
    # whose verified reward still needs a decision or receipt check.
    current_claim_ids = set(OrderContinuation.objects.filter(continuation_scope,
        continuation_order__providers__provider_account=account, status='VERIFIED'
        ).values_list('pk', flat=True))
    verified_claims = []
    for claim in OrderContinuation.objects.filter(status='VERIFIED').iterator(chunk_size=200):
        participants = claim.participants if isinstance(claim.participants, list) else []
        if claim.pk in current_claim_ids or any(isinstance(item, dict) and item.get('provider_account_id') == account.pk
               and item.get('provider_business_cycle') == account.business_cycle for item in participants):
            verified_claims.append(claim)
    continuations = pending_continuations + sum(
        claim.reward_state not in ('PAID', 'INELIGIBLE') for claim in verified_claims)
    if continuations:
        blockers.append('仍有续单核实或奖励待处理')
    paid_review = 0
    paid_claims = (claim for claim in verified_claims if claim.reward_state == 'PAID')
    for claim in paid_claims:
        matches = [item for item in (claim.participants if isinstance(claim.participants, list) else [])
                   if isinstance(item, dict) and item.get('provider_account_id') == account.pk]
        rewards = list(claim.rewards.filter(provider=account).select_related('transaction__wallet'))
        if len(matches) != 1 or len(rewards) != 1:
            paid_review += 1
            continue
        reward, tx = rewards[0], rewards[0].transaction
        if (matches[0].get('provider_business_cycle') != reward.provider_business_cycle
                or tx.order_id != claim.continuation_order_id or tx.wallet.account_id != account.pk
                or tx.wallet.user_id != matches[0].get('provider_id') or tx.tx_type != 'REWARD'
                or tx.status != 'SUCCESS' or reward.amount != 500 or tx.amount != reward.amount
                or tx.reversal_of_id or tx.reversals.exists()
                or tx.balance_after != tx.balance_before + tx.amount):
            paid_review += 1
    if paid_review:
        blockers.append('续单奖励到账凭证待核对')
    referrals = Order.objects.filter(inviter_account=account, inviter_commission__gt=0,
        settled_at__isnull=True, payment_status__in=['PAID', 'PARTIALLY_REFUNDED']).count()
    if referrals:
        blockers.append('仍有未结推荐佣金')
    deficits = RefundDepositDeficit.objects.filter(account=account, remaining__gt=0).count()
    if deficits:
        blockers.append('退款押金欠款尚未结清')
    income = None
    history = None
    if profile and wallet:
        from console.deposit_refund import deposit_refund_eligibility
        from console.deposit_clearance import clearance_evidence
        evidence = deposit_refund_eligibility(profile, wallet)
        income = {key: evidence[key] for key in ('active_order_count', 'open_aftersale_count',
            'pending_income_count', 'unreleased_income_count')}
        if any(income.values()):
            blockers.append('仍有履约、售后或未结收益')
        # Deposit eligibility deliberately skips its waiting-clock check after
        # the deposit reaches zero. Verify the business evidence independently
        # so a completed deposit refund does not hide inconsistent history.
        verified = clearance_evidence(profile, wallet)
        needs_review = verified.needs_review or (verified.has_business and not verified.last_closed_at)
        history = {'has_business': verified.has_business, 'needs_review': needs_review,
            'last_closed_at': verified.last_closed_at.isoformat() if verified.last_closed_at else None}
        if needs_review:
            blockers.append('订单、售后或收益结清证据待核对')
    return {'account_id': account.pk, 'business_cycle': account.business_cycle,
        'wallet_balance': wallet.balance if wallet else None,
        'wallet_frozen': wallet.frozen_amount if wallet else None,
        'deposit_paid': profile.deposit_paid if profile else None,
        'points_balance': member.points_balance if member else None,
        'pass_expires_at': profile.pass_expires_at.isoformat() if profile and profile.pass_expires_at else None,
        'pending_withdrawals': pending_withdrawals, 'pending_deposit_applications': pending_deposit_applications,
        'reservations': reservations,
        'pending_entitlement_claims': pending_claims, 'entitlement_claims_to_review': claims_to_review,
        'applications': applications, 'unsettled_referrals': referrals,
        'active_grab_single': active_single, 'active_grab_team': active_team,
        'pending_grab_receipts': pending_receipts, 'pending_continuations': continuations,
        'continuation_receipts_to_review': paid_review,
        'refund_deposit_deficits': deficits, 'income': income, 'history': history,
        'aftersale_windows': aftersale_windows,
        'blocking_reasons': blockers}


def preview_retirement(account, operator):
    actor = _authorize(account, operator)
    account = ClubAccount.objects.get(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER)
    facts = clearance_facts(account)
    token = signing.dumps({'store': current_store().slug, 'operator': actor.pk, 'facts': facts},
                          salt=_CONFIRMATION_SALT, compress=True)
    available = retirement_enabled()
    return {'facts': facts, 'can_retire': available and not facts['blocking_reasons'], 'confirmation_token': token,
            'reason': '' if available else '陪玩注销尚未开放，资金与履约入口仍需完成核验'}


def retire_provider(account, operator, reason, confirmation_token, *, confirm, request_id):
    """Prepare the atomic transition; unavailable until all barriers are ready."""
    if not retirement_enabled():
        raise ValidationError('陪玩注销尚未开放，资金与履约入口仍需完成核验')
    actor = _authorize(account, operator)
    if confirm is not True:
        raise ValidationError('请确认陪玩注销内容')
    if not isinstance(reason, str) or not 2 <= len(reason.strip()) <= 500:
        raise ValidationError('请填写2至500字注销说明')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', request_id):
        raise ValidationError('请提供有效的操作凭证')
    if not isinstance(confirmation_token, str):
        raise ValidationError('请重新预览并确认注销内容')
    try:
        signed = signing.loads(confirmation_token, salt=_CONFIRMATION_SALT)
    except (signing.BadSignature, TypeError):
        raise ValidationError('请重新预览并确认注销内容') from None
    if (not isinstance(signed, dict) or signed.get('store') != current_store().slug
            or signed.get('operator') != actor.pk or not isinstance(signed.get('facts'), dict)
            or signed['facts'].get('account_id') != account.pk
            or type(signed['facts'].get('business_cycle')) is not int):
        raise ValidationError('注销确认信息与本店操作不一致')
    token_hash = hashlib.sha256(confirmation_token.encode('utf-8')).hexdigest()
    ensure_provider_lifecycle(account)
    with transaction.atomic():
        lock_provider_closure(account)
        from .retirement_aftersale import lock_candidate_orders, window_facts
        orders = lock_candidate_orders(account)
        from .license_quota import lock_category, release_locked_quota
        quota = lock_category('PROVIDER')
        locked = ClubAccount.objects.select_for_update().get(pk=account.pk)
        from engagement.models import RetirementAudit
        cycle = signed['facts']['business_cycle']
        audit = RetirementAudit.objects.filter(account=locked, business_cycle=cycle).first()
        if audit:
            receipt = audit.snapshot.get('receipt', {}) if isinstance(audit.snapshot, dict) else {}
            if (audit.operator_id != actor.pk or audit.reason != reason.strip()
                    or receipt.get('request_id') != request_id or receipt.get('confirmation_hash') != token_hash):
                raise ValidationError('该账号周期已有不同注销记录，请核对')
            return {'account_id': locked.pk, 'retired_cycle': cycle, 'business_cycle': cycle + 1,
                    'replayed': True}
        try:
            signing.loads(confirmation_token, salt=_CONFIRMATION_SALT, max_age=900)
        except (signing.BadSignature, TypeError):
            raise ValidationError('注销确认已过期，请重新预览') from None
        if locked.business_cycle != cycle:
            raise ValidationError('账号周期已变化，请重新核对')
        facts = clearance_facts(locked, aftersale_windows=window_facts(locked, orders))
        if signed['facts'] != facts:
            raise ValidationError('注销内容已变化，请重新预览并确认')
        if facts['blocking_reasons']:
            raise ValidationError(facts['blocking_reasons'])
        RetirementAudit.objects.create(account=locked, business_cycle=locked.business_cycle,
            operator=actor, reason=reason.strip(), snapshot={'facts': facts,
                'receipt': {'request_id': request_id, 'confirmation_hash': token_hash}})
        locked.business_cycle += 1
        release_locked_quota(quota)
        locked.license_membership_active = False
        locked._save_account(update_fields=['business_cycle', 'license_membership_active', 'updated_at'])
        return {'account_id': locked.pk, 'retired_cycle': cycle, 'business_cycle': cycle + 1,
                'replayed': False}
