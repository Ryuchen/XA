"""Transactional, per-store License account admission."""
from django.conf import settings
from django.db import DatabaseError, transaction
from rest_framework.exceptions import APIException

from stores.context import current_database, current_store
from stores.license_policy import TIERS


class LicenseAdmissionError(APIException, ValueError):
    status_code = 403
    default_code = 'license_admission_denied'


class LicenseAdmissionUnavailable(LicenseAdmissionError):
    status_code = 503
    default_code = 'license_admission_unavailable'


def enabled():
    return getattr(settings, 'LICENSE_ACCOUNT_ADMISSION_ENABLED', True)


def lock_category(account_type):
    from .models import LicenseQuotaLock
    # A data migration seeds both rows in each new store. The fallback permits
    # test databases built using syncdb to exercise exactly the same lock.
    LicenseQuotaLock.objects.get_or_create(account_type=account_type)
    return LicenseQuotaLock.objects.select_for_update().get(account_type=account_type)


def assert_available(account_type, *, quota):
    from stores.license_snapshot import business_status
    store = current_store()
    if store is None or store.database_alias != current_database():
        raise LicenseAdmissionError('无法确定账号所属门店，请从门店入口重试')
    try:
        status = business_status(store)
    except (DatabaseError, ValueError) as exc:
        raise LicenseAdmissionUnavailable('暂时无法核验门店授权，请联系平台') from exc
    tier = TIERS.get(status['tier'])
    if not status['allow_new_accounts'] or tier is None:
        raise LicenseAdmissionError('门店授权暂不允许新增账号，请联系平台')
    limit = tier.boss_limit if account_type == 'BOSS' else tier.provider_limit
    used = quota.active_count
    if limit is not None and used >= limit:
        raise LicenseAdmissionError('门店该类型账号已达到授权人数上限，请联系门店管理员')


def create_account_with_quota(account, save, *, using=None):
    if using is not None and using != current_database():
        raise LicenseAdmissionError('不能跨门店创建账号')
    if not account.license_membership_active:
        raise LicenseAdmissionError('新账号必须占用门店授权名额')
    with transaction.atomic(using=current_database()):
        quota = lock_category(account.account_type)
        if enabled():
            assert_available(account.account_type, quota=quota)
        result = save()
        quota.active_count += 1
        quota.save(update_fields=['active_count'])
        return result


def release_locked_quota(quota):
    """Caller holds the category lock and commits the retirement together."""
    if quota.active_count < 1:
        raise LicenseAdmissionUnavailable('门店账号名额记录异常，请联系平台核对')
    quota.active_count -= 1
    quota.save(update_fields=['active_count'])


def rejoin_if_retired(account):
    if account.account_type not in ('BOSS', 'PROVIDER') or account.license_membership_active:
        return account
    from .models import ClubAccount
    provider = account.account_type == ClubAccount.AccountType.PROVIDER
    if provider:
        from .provider_lifecycle import (ensure_provider_lifecycle, lock_provider_closure,
                                         ProviderLifecycleError)
        try:
            # Login entry points invoke token issuance outside their business
            # transactions; the lifecycle lock must precede quota/account rows.
            ensure_provider_lifecycle(account)
        except ProviderLifecycleError as exc:
            raise LicenseAdmissionUnavailable('陪玩账号状态待核对，请联系门店管理员') from exc
    with transaction.atomic(using=current_database()):
        if provider:
            try:
                lock_provider_closure(account)
            except ProviderLifecycleError as exc:
                raise LicenseAdmissionUnavailable('陪玩账号状态待核对，请联系门店管理员') from exc
        quota = lock_category(account.account_type)
        locked = ClubAccount.objects.select_for_update().get(pk=account.pk)
        if locked.account_type != account.account_type:
            raise LicenseAdmissionError('账号类型已变更，请重新登录')
        if not locked.is_active or not locked.can_login:
            raise LicenseAdmissionError('账号已停用，请联系门店管理员')
        if not provider and locked.business_cycle != account.business_cycle:
            # Boss retirement advances the cycle before re-entry. An old
            # authenticated request must not rejoin after a later retirement;
            # concurrent re-entry within the same cycle remains idempotent.
            raise LicenseAdmissionError('老板账号周期已变化，请重新登录')
        if provider and locked.business_cycle != account.business_cycle:
            # A simultaneous login may have completed this exact re-entry.
            # An unrelated later cycle requires fresh authentication instead.
            if not (locked.license_membership_active and locked.business_cycle == account.business_cycle + 1):
                raise LicenseAdmissionError('陪玩账号周期已变化，请重新登录')
        if not locked.license_membership_active:
            if enabled():
                assert_available(locked.account_type, quota=quota)
            quota.active_count += 1
            quota.save(update_fields=['active_count'])
            locked.license_membership_active = True
            fields = ['license_membership_active', 'updated_at']
            if locked.account_type == 'PROVIDER':
                # Existing provider tokens cannot become valid again simply
                # because this membership was reactivated.
                locked.business_cycle += 1
                fields.append('business_cycle')
            locked._save_account(update_fields=fields)
        account.license_membership_active = locked.license_membership_active
        account.business_cycle = locked.business_cycle
    return account
