from rest_framework.permissions import BasePermission

from .context import current_database
from .models import StorePlatformGrant


def is_platform_super_admin(account):
    if (account is None or current_database() != 'default'
            or getattr(account._state, 'db', None) != 'default'
            or account.account_type != 'STAFF'
            or not account.is_active or not account.can_login):
        return False
    from console.permissions import is_console_account
    membership = getattr(account, 'admin_membership', None)
    return bool(membership and membership.is_active and is_console_account(account)
                and membership.roles.filter(code='super_admin', is_active=True).exists())


def may_manage_stores(account):
    return bool(is_platform_super_admin(account)
                and StorePlatformGrant.objects.filter(account_id=account.pk, is_active=True).exists())


def is_platform_session(request):
    """A signed platform session is verified against the original grant on every request."""
    auth = getattr(request, 'auth', None)
    return bool(auth and callable(getattr(auth, 'get', None)) and auth.get('auth_method') == 'platform')


class IsPlatformStoreManager(BasePermission):
    message = '该操作需要独立的平台门店管理授权，请在平台所属门店登录'

    def has_permission(self, request, view):
        return may_manage_stores(getattr(request, 'account', None)) or is_platform_session(request)
