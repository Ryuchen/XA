from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from django.db import DatabaseError, InterfaceError
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import APIException, AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .tokens import ACCOUNT_TOKEN_KIND, account_for_claims


class ClubAccountAuthenticationUnavailable(APIException):
    status_code = 503
    default_detail = '门店账号服务暂不可用，请稍后重试'
    default_code = 'club_account_auth_unavailable'


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

        try:
            account = account_for_claims(token)
            mapping = getattr(account, 'legacy_mapping', None)
            legacy_user = (get_user_model().objects.filter(pk=mapping.legacy_user_id).first()
                           if mapping is not None else None)
        except TokenError as exc:
            raise AuthenticationFailed(str(exc)) from exc
        except (DatabaseError, InterfaceError) as exc:
            raise ClubAccountAuthenticationUnavailable() from exc

        request.account = account
        request.legacy_user = legacy_user
        if hasattr(request, '_request'):
            request._request.account = account
            request._request.legacy_user = legacy_user
        return AnonymousUser(), token
