from rest_framework.permissions import BasePermission
from django.conf import settings

from .services import get_or_create_account_for_legacy_user, link_legacy_relations


def resolve_club_account(request):
    """Resolve an optional account without making a public read require login."""
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
    return account if account and account.is_active and account.can_login else None


class IsClubAccountAuthenticated(BasePermission):
    message = '请先登录俱乐部业务账户'

    def has_permission(self, request, view):
        return resolve_club_account(request) is not None
