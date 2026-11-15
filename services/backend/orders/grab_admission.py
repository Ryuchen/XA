"""Read-only preliminary checks for automatic grab rounds.

These are hints, not a reservation or a final allocation. Callers still need
the candidate-registration and cutoff protocol, then must recheck eligibility
while holding the normal order, admission and provider locks before assignment.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone
from club_accounts.models import ClubAccount
from users.models import EscortProfile
from wallet.refund_deposit import protection_enabled

from .capacity import current_capacity_rules
from .grab_pool_visibility import initial_visibility
from .models import Order, OrderGrabPoolSnapshot, OrderPaidServiceScope, Reservation
from .paid_service_scope import acceptance_error, enabled as scope_enabled
from .services import count_capacity_orders


class GrabAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class AdmissionHint:
    """Entry tier is for visibility only, never the submitted ranking tier."""
    profile_id: int
    account_id: int
    entry_pass_tier: str
    visible_at: datetime


def check_candidate(pool_id, account_id, *, round_id=None, locked_profile=None):
    """Check current eligibility and persisted visibility; no client timestamps."""
    if type(pool_id) is not int or pool_id < 1 or type(account_id) is not int or account_id < 1:
        raise GrabAdmissionError('抢单池或陪玩账号无效')
    pool = OrderGrabPoolSnapshot.objects.select_related('order', 'order__service').filter(pk=pool_id).first()
    profile = locked_profile
    if profile is None:
        profile = (EscortProfile.objects.select_related('account', 'user', 'level')
                   .filter(account_id=account_id, account__account_type=ClubAccount.AccountType.PROVIDER).first())
    elif profile.account_id != account_id or profile.account.account_type != ClubAccount.AccountType.PROVIDER:
        raise GrabAdmissionError('陪玩账号与锁定资料不匹配')
    if not pool or not profile:
        raise GrabAdmissionError('本店抢单池或陪玩账号不存在')
    order = pool.order
    if (order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.PAID
            or order.provider_id or order.provider_account_id or order.providers.exists()
            or order.is_gift_snapshot or order.source_order_id or order.requires_boss_selection
            or Reservation.objects.filter(order=order).exists()):
        raise GrabAdmissionError('订单已离开自动抢单池')
    if pool.required_provider_count not in (1, 2) or pool.required_provider_count != order.required_provider_count:
        raise GrabAdmissionError('订单陪玩人数快照不一致，请先核对')
    from .direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
        raise GrabAdmissionError('网页直付订单尚未接入自动抢单')
    if not scope_enabled() or not OrderPaidServiceScope.objects.filter(order=order).exists():
        raise GrabAdmissionError('订单原服务范围尚未核对，不能自动抢单')
    if not protection_enabled():
        raise GrabAdmissionError('退款押金缺口保护尚未启用')
    reason = profile.wechat_acceptance_error()
    if reason:
        raise GrabAdmissionError(reason)
    reason = acceptance_error(profile, order, allow_refund_recovery=True,
                              lock_refund_recovery=locked_profile is not None)
    if reason:
        raise GrabAdmissionError(reason)
    snapshot = order.game_commission_snapshot
    if order.commission_allocation_pending or (
            isinstance(snapshot, dict) and snapshot.get('personal_rules')):
        from .delta_personal_commission import resolve_order_candidate
        try:
            resolve_order_candidate(order, profile)
        except ValueError as exc:
            raise GrabAdmissionError(str(exc)) from exc
    capacity = current_capacity_rules()
    if capacity['version'] < 1:
        raise GrabAdmissionError('本店接单名额尚未确认')
    if count_capacity_orders(profile.account) >= capacity['max_concurrent_orders']:
        raise GrabAdmissionError('陪玩接单名额已满')
    try:
        round_ = None
        if round_id is not None:
            from .grab_round_facts import current_round
            round_ = current_round(pool, round_id)
        if round_ and round_.simultaneous_visibility:
            from .grab_pool_visibility import InitialVisibility
            from .grab_clock_contract import production_enabled
            if production_enabled():
                from .grab_round_opening import opening_bounds, provisional_opening_lower
                try:
                    lower, upper = opening_bounds(round_)
                    visibility = InitialVisibility('', 0, upper, lower)
                except ValueError:
                    lower = provisional_opening_lower(round_)
                    visibility = InitialVisibility('', 0, lower, lower, True)
            else:
                visibility = InitialVisibility('', 0, round_.opened_at)
        else:
            visibility = initial_visibility(pool.pk, account_id)
    except ValueError as exc:
        raise GrabAdmissionError(str(exc)) from exc
    from .grab_clock_contract import production_enabled
    if production_enabled():
        from .grab_receipt_probe import _db_clock
        now, precision, _ = _db_clock()
        if now + timedelta(microseconds=precision) < (visibility.visible_lower_at or visibility.visible_at):
            raise GrabAdmissionError('尚未到本订单的可见时间')
        return AdmissionHint(profile.pk, account_id, visibility.entry_tier, visibility.visible_at)
    else:
        now = timezone.now()
    if now < visibility.visible_at:
        raise GrabAdmissionError('尚未到本订单的可见时间')
    return AdmissionHint(profile.pk, account_id, visibility.entry_tier, visibility.visible_at)
