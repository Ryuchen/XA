"""Provider lifecycle serialization primitives; not a cancellation API.

Account creation atomically initializes its row; verify it before starting a
business transaction, then take a shared lock before *any* business row lock.
Cancellation must take the exclusive lock first and hold it until commit.
No caller may upgrade shared to exclusive inside one transaction. The logout
workflow must remain unavailable until all entry points follow this contract.
"""
from django.conf import settings

from stores.context import current_database, current_store
from stores.db import transaction
from .models import ClubAccount, ProviderLifecycleLock


class ProviderLifecycleError(RuntimeError):
    pass


def _connection():
    store = current_store()
    alias = current_database()
    if store is None or store.database_alias != alias:
        raise ProviderLifecycleError('陪玩生命周期操作必须绑定当前门店')
    if getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and alias == 'default':
        raise ProviderLifecycleError('平台控制库不能用于门店陪玩操作')
    return transaction.get_connection()


def ensure_provider_lifecycle(account):
    """Verify the row exists before a business transaction; never create it."""
    connection = _connection()
    if connection.in_atomic_block:
        raise ProviderLifecycleError('陪玩生命周期锁必须在业务事务前初始化')
    if (not isinstance(account, ClubAccount) or not account.pk or account._state.db != connection.alias
            or account.account_type != ClubAccount.AccountType.PROVIDER):
        raise ProviderLifecycleError('陪玩账号与所属门店不一致')
    if not ProviderLifecycleLock.objects.filter(account_id=account.pk).exists():
        raise ProviderLifecycleError('陪玩生命周期锁尚未初始化')


def _lock(account, *, exclusive):
    connection = _connection()
    if not connection.in_atomic_block:
        raise ProviderLifecycleError('陪玩生命周期锁必须在门店业务事务内获取')
    if connection.vendor != 'mysql':
        raise ProviderLifecycleError('陪玩生命周期锁需要 MySQL 行锁')
    if (not isinstance(account, ClubAccount) or not account.pk or account._state.db != connection.alias
            or account.account_type != ClubAccount.AccountType.PROVIDER):
        raise ProviderLifecycleError('陪玩账号与所属门店不一致')
    table = connection.ops.quote_name(ProviderLifecycleLock._meta.db_table)
    mode = 'FOR UPDATE' if exclusive else 'LOCK IN SHARE MODE'
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT account_id FROM {table} WHERE account_id = %s {mode}', [account.pk])
        if cursor.fetchone() is None:
            raise ProviderLifecycleError('陪玩生命周期锁尚未初始化')


def lock_provider_operation(account):
    _lock(account, exclusive=False)


def lock_active_provider_operation(account):
    """Recheck the authenticated owner after waiting for a lifecycle lock."""
    lock_provider_operation(account)
    return provider_membership_current(account)


def provider_membership_current(account):
    """Check an original membership snapshot after its lifecycle lock is held."""
    return ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
        is_active=True, can_login=True, license_membership_active=True, business_cycle=account.business_cycle).exists()


def lock_provider_operations(accounts):
    """For multi-provider work, acquire distinct locks in stable ID order."""
    accounts = list(accounts)
    if any(not isinstance(account, ClubAccount) or not account.pk for account in accounts):
        raise ProviderLifecycleError('陪玩账号无效')
    by_id = {}
    for account in accounts:
        previous = by_id.get(account.pk)
        if previous is not None and previous._state.db != account._state.db:
            raise ProviderLifecycleError('不能跨门店获取陪玩生命周期锁')
        by_id[account.pk] = account
    for account in sorted(by_id.values(), key=lambda item: item.pk):
        _lock(account, exclusive=False)


def lock_active_provider_operations(accounts):
    """Lock all participants first, then verify each original membership cycle."""
    accounts = list(accounts)
    lock_provider_operations(accounts)
    return all(provider_membership_current(account) for account in accounts)


def lock_provider_closure(account):
    _lock(account, exclusive=True)


def closure_facts(account):
    """Show known outstanding facts without authorizing a closure.

    The current business writers do not yet all use the lifecycle barrier.
    These non-locking observations are not a complete eligibility decision.
    """
    connection = _connection()
    if (not isinstance(account, ClubAccount) or account._state.db != connection.alias
            or account.account_type != ClubAccount.AccountType.PROVIDER):
        raise ProviderLifecycleError('陪玩账号与所属门店不一致')
    from django.db.models import Q
    from django.utils import timezone
    from engagement.models import MemberProfile
    from orders.applications_models import OrderApplication
    from orders.models import Order, Reservation
    from users.models import EscortProfile
    from wallet.models import Wallet, WithdrawRequest
    from console.deposit_refund import deposit_refund_eligibility

    profile = EscortProfile.objects.filter(account=account).first()
    wallet = Wallet.objects.filter(account=account).first()
    member = MemberProfile.objects.filter(account=account).first()
    states = (WithdrawRequest.Status.PENDING, WithdrawRequest.Status.APPROVED,
              WithdrawRequest.Status.STORE_APPROVED, WithdrawRequest.Status.PLATFORM_APPROVED,
              WithdrawRequest.Status.PAYMENT_UNKNOWN)
    pending_withdrawals = WithdrawRequest.objects.filter(account=account, status__in=states).count()
    reservations = Reservation.objects.filter(
        Q(provider_account=account) | Q(seats__provider_account=account),
        status__in=(Reservation.Status.PENDING, Reservation.Status.CONFIRMED)).distinct().count()
    applications = OrderApplication.objects.filter(
        Q(applicant=account) | Q(partner=account), status=OrderApplication.Status.PENDING).count()
    unpaid_referrals = Order.objects.filter(inviter_account=account, settled_at__isnull=True,
        payment_status__in=(Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED)).count()
    existing = deposit_refund_eligibility(profile, wallet) if profile else None
    return {
        'closure_available': False,
        'reason': '陪玩注销尚未开放，资金与履约入口仍需完成核验',
        'membership_active': account.license_membership_active,
        'profile_present': profile is not None,
        'deposit_paid': profile.deposit_paid if profile else None,
        'pass_active': bool(profile and profile.pass_expires_at and profile.pass_expires_at > timezone.now()),
        'wallet_balance': wallet.balance if wallet else None,
        'wallet_frozen': wallet.frozen_amount if wallet else None,
        'points_balance': member.points_balance if member else None,
        'pending_withdrawals': pending_withdrawals,
        'pending_reservations': reservations,
        'pending_applications': applications,
        'unsettled_referrals': unpaid_referrals,
        'order_and_income_facts': existing,
    }
