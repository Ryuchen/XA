"""Authorize a tenant staff identity, then activate only its store's code."""
from club_accounts.tokens import account_for_claims
from console.permissions import is_console_account
from rest_framework_simplejwt.exceptions import TokenError

from .context import current_database, store_context
from .license_codes import _activate_after_verified_store_auth
from .models import StoreLicenseActivatorGrant


class LicenseActivationForbidden(ValueError):
    pass


def activate_for_authenticated_staff(*, store, claims, account_id, code):
    if current_database() != store.database_alias or claims.get('auth_method') == 'platform':
        raise LicenseActivationForbidden('门店身份无效')

    def authorize(locked_store):
        with store_context(locked_store):
            try:
                account = account_for_claims(claims)
            except TokenError as error:
                raise LicenseActivationForbidden('后台登录已失效') from error
            if (account.pk != account_id or account._state.db != locked_store.database_alias
                    or not is_console_account(account)):
                raise LicenseActivationForbidden('无门店授权激活权限')
            membership = account.admin_membership
            is_super = membership.roles.filter(code='super_admin', is_active=True).exists()
            delegated = StoreLicenseActivatorGrant.objects.filter(store=locked_store, account_id=account.pk,
                account_no=account.account_no, is_active=True).exists()
            if not (is_super or delegated):
                raise LicenseActivationForbidden('无门店授权激活权限')

    return _activate_after_verified_store_auth(store=store, actor_id=account_id, code=code, authorize=authorize)
