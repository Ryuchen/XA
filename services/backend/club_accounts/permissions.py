from rest_framework.permissions import BasePermission
from django.conf import settings

from .services import get_or_create_account_for_legacy_user, link_legacy_relations


class IsClubAccountAuthenticated(BasePermission):
    message = '请先登录俱乐部业务账户'

    def has_permission(self, request, view):
        account = getattr(request, 'account', None)
        if (
            account is None
            and getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
            and getattr(request.user, 'is_authenticated', False)
        ):
            account = get_or_create_account_for_legacy_user(request.user)
            if account is not None:
                request.account = account
                request.legacy_user = request.user
                if hasattr(request, '_request'):
                    request._request.account = account
                    request._request.legacy_user = request.user
                link_legacy_relations(request.user, account)
        return bool(account and account.is_active and account.can_login)


class HasAccountType(BasePermission):
    allowed_account_types = ()

    def has_permission(self, request, view):
        account = getattr(request, 'account', None)
        return bool(
            account
            and account.is_active
            and account.can_login
            and account.account_type in self.allowed_account_types
        )
