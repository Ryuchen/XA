"""Only individually provable ineligibility, under parked allocation locks."""
from stores.db import transaction
from users.models import EscortProfile


def reason(profile, *, membership_valid=True):
    """Caller holds this profile, dispatch mutex and provider lifecycle lock."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('留池资格核验必须在分配事务中进行')
    if not membership_valid:
        return '陪玩门店成员身份已变化'
    account = profile.account
    if (not account or not account.is_active or not account.can_login
            or not account.license_membership_active or not profile.user.is_active):
        return '陪玩账号或门店身份不可用'
    if not profile.is_verified:
        return '陪玩尚未通过认证'
    if profile.status == EscortProfile.Status.OFFLINE:
        return '陪玩当前处于离线状态'
    from wallet.refund_deposit import protection_enabled
    if not protection_enabled():
        return ''  # A global unavailable capability is not candidate disqualification.
    from wallet.models import RefundDepositDeficit
    deficits = list(RefundDepositDeficit.objects.select_for_update().filter(
        account_id=profile.account_id, remaining__gt=0).order_by('created_at', 'pk'))
    if any(item.profile_id != profile.pk for item in deficits):
        raise ValueError('退款押金缺口与陪玩资料不匹配')
    if deficits:
        from wallet.refund_recovery_order import enabled, eligibility_error
        if not enabled():
            return ''
        return eligibility_error(profile, lock_current=True)
    if profile.deposit_mode != 'EARNINGS' and profile.deposit_paid < profile.effective_deposit_required:
        return '陪玩押金未缴齐'
    return ''
