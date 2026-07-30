from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .models import ClubAccount
from .tokens import ACCOUNT_TOKEN_KIND


class ClubAccountAuthentication(BaseAuthentication):
    """Authenticate business JWTs without touching Django request.user."""

    keyword = b'Bearer'

    def authenticate_header(self, request):
        return 'Bearer realm="club-account"'

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower():
            return None
        if len(header) != 2:
            raise AuthenticationFailed('无效的 Authorization 请求头')

        try:
            token = AccessToken(header[1])
        except TokenError as exc:
            raise AuthenticationFailed('登录已过期，请重新登录') from exc

        if token.get('token_kind') != ACCOUNT_TOKEN_KIND:
            return None

        account_id = token.get('account_id')
        try:
            account = ClubAccount.objects.get(pk=account_id)
        except ClubAccount.DoesNotExist as exc:
            raise AuthenticationFailed('业务账户不存在') from exc

        if not account.is_active or not account.can_login:
            raise AuthenticationFailed('业务账户已被禁用')

        request.account = account
        if hasattr(request, '_request'):
            request._request.account = account
        mapping = getattr(account, 'legacy_mapping', None)
        legacy_user = None
        if mapping is not None:
            legacy_user = get_user_model().objects.filter(
                pk=mapping.legacy_user_id,
            ).first()
        request.legacy_user = legacy_user
        if hasattr(request, '_request'):
            request._request.legacy_user = legacy_user
        return AnonymousUser(), token
