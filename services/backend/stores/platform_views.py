from collections.abc import Mapping
from uuid import UUID
from django.conf import settings
from django.db import DatabaseError, InterfaceError, transaction

from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from users.sms_transport import SmsUnavailable
from .hosts import is_admin_host
from .models import PlatformAccount, PlatformAuditEvent, PlatformLoginChallenge, PlatformOtpCredential, PlatformRoleGrant
from .platform_auth import (IsPlatformAuthenticated, PlatformAuthentication, PlatformAuthError,
                            account_for_token, authenticate_platform, issue_tokens,
                            start_challenge, verify_challenge)
from .platform_permissions import platform_may, platform_stores_for
from .serializers import public_store


def fail(message, status=400, code='PLATFORM_LOGIN_INVALID'):
    return Response({'code': status, 'errorCode': code, 'msg': message}, status=status)


def admin_origin(request):
    return is_admin_host(request.get_host())


def profile(account):
    grants = PlatformRoleGrant.objects.filter(account=account, is_active=True).values('role', 'scope_slug')
    capabilities = ['STORE_MANAGEMENT_V1', 'PLATFORM_SETUP_V1', 'PLATFORM_CLOCKS_V1', 'PLATFORM_DEPOSIT_V1', 'PLATFORM_WITHDRAW_V1']
    if getattr(settings, 'PLATFORM_PASS_REPAYMENT_ENABLED', False):
        capabilities.append('PLATFORM_PASS_REPAYMENT_V1')
    return {'id': str(account.pk), 'username': account.username, 'nickname': account.nickname,
            'identity_type': 'platform', 'grants': list(grants), 'capabilities': capabilities}


class PlatformPasswordLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口登录', 403)
        if not settings.PLATFORM_IDENTITY_ENABLED:
            return fail('平台身份登录尚未启用', 503, 'PLATFORM_DISABLED')
        if not isinstance(request.data, Mapping):
            return fail('登录请求格式不正确')
        if any(key in request.data for key in ('store', 'store_slug', 'database_alias')):
            return fail('登录由账号识别身份，不接受门店路由参数')
        try:
            account = authenticate_platform(request.data.get('username'), request.data.get('password'))
            sms, otp = request.data.get('smsChallenge'), request.data.get('otpChallenge')
            if sms and otp:
                return fail('请使用一种身份验证方式')
            if request.data.get('resend') is True:
                try:
                    challenge_id = UUID(str(sms))
                except (ValueError, TypeError):
                    return fail('验证请求无效，请重新登录')
                if not PlatformLoginChallenge.objects.filter(pk=challenge_id, account=account, method='SMS', consumed=False).exists():
                    return fail('验证请求无效，请重新登录')
                result = start_challenge(account, use_sms=True)
                result['identity_kind'] = 'platform'
                return Response({'code': 0, 'data': result})
            if sms or otp:
                method = 'SMS' if sms else 'OTP'
                code = request.data.get('smsCode') if sms else request.data.get('otpCode')
                with transaction.atomic(using='default'):
                    try:
                        account, proof = verify_challenge(account, sms or otp, code, method)
                    except PlatformAuthError as error:
                        # An incorrect code still uses up a verification attempt.
                        return fail(str(error), error.status, error.code)
                    access, refresh = issue_tokens(account, method, proof)
                    return Response({'code': 0, 'data': {
                        'token': access, 'refresh': refresh, 'profile': profile(account),
                    }})
            result = start_challenge(account, use_sms=request.data.get('verificationMethod') == 'sms')
            result['identity_kind'] = 'platform'
            return Response({'code': 0, 'data': result})
        except PlatformAuthError as error:
            return fail(str(error), error.status, error.code)
        except SmsUnavailable:
            return fail('短信服务暂不可用，请稍后重试', 503, 'SMS_UNAVAILABLE')
        except (DatabaseError, InterfaceError):
            return fail('平台登录服务暂不可用，请稍后重试', 503, 'PLATFORM_UNAVAILABLE')


class PlatformRefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if not settings.PLATFORM_IDENTITY_ENABLED:
            return fail('平台身份登录尚未启用', 503, 'PLATFORM_DISABLED')
        if not admin_origin(request) or not isinstance(request.data, Mapping):
            return fail('平台登录无效', 401)
        try:
            token = RefreshToken(request.data.get('refresh', ''))
            account_for_token(token)
            return Response({'code': 0, 'data': {'token': str(token.access_token)}})
        except (TokenError, ValueError, TypeError):
            return fail('平台登录已过期，请重新登录', 401)
        except (DatabaseError, InterfaceError):
            return fail('平台登录服务暂不可用，请稍后重试', 503, 'PLATFORM_UNAVAILABLE')


class PlatformProfileView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        return Response({'code': 0, 'data': profile(request.platform_account)})


class PlatformOtpSetupView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]
    throttle_scope = 'login'

    def get(self, request):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        response = Response({'code': 0, 'data': {'isOtpBound': PlatformOtpCredential.objects.filter(account=request.platform_account).exists()}})
        response['Cache-Control'] = 'no-store'
        return response

    def post(self, request):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        if not isinstance(request.data, Mapping):
            return fail('验证请求格式不正确')
        try:
            from .platform_otp import enroll
            result = enroll(request.auth, request.data)
            response = Response({'code': 0, 'data': result})
            response['Cache-Control'] = 'no-store'
            return response
        except PlatformAuthError as error:
            return fail(str(error), error.status, error.code)


class PlatformStoresView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        account = request.platform_account
        ids = set()
        for action in ('stores:view', 'withdraw:view', 'salary:view'):
            ids.update(platform_stores_for(account, action).values_list('pk', flat=True))
        from .models import Store
        stores = Store.objects.filter(pk__in=ids).order_by('id')
        return Response({'code': 0, 'data': {'results': [public_store(store) for store in stores]}})


class PlatformDashboardView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        from .platform_dashboard import aggregate_dashboard
        try:
            data = aggregate_dashboard(request.platform_account)
            # Store reads can finish after the platform session is invalidated.
            account_for_token(request.auth)
        except PermissionError:
            return fail('无平台汇总权限', 403, 'PLATFORM_FORBIDDEN')
        except (DatabaseError, InterfaceError):
            return fail('平台汇总暂不可用，请稍后重试', 503, 'PLATFORM_UNAVAILABLE')
        except TokenError as error:
            raise AuthenticationFailed('平台登录已失效，请重新登录') from error
        return Response({'code': 0, 'data': data})


class PlatformPassRefundShortfallsView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        from .platform_pass_refunds import ACTION, summary
        if not admin_origin(request) or not platform_stores_for(request.platform_account, ACTION).exists():
            return fail('无通行证退款资金查看权限', 403, 'PLATFORM_FORBIDDEN')
        data = summary(request.platform_account, token=request.auth)
        PlatformAuditEvent.objects.create(account=request.platform_account, action='pass_refunds.view_summary')
        response = Response({'code': 0, 'data': data})
        response['Cache-Control'] = 'no-store'
        return response


class PlatformPassRefundShortfallDetailView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug):
        from .platform_pass_refunds import ACTION, detail
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        store = platform_stores_for(request.platform_account, ACTION).filter(slug=slug).first()
        if store is None:
            return fail('门店不存在或无权查看', 404, 'PLATFORM_STORE_NOT_FOUND')
        try:
            page = int(request.query_params.get('page', '1'))
            if not 1 <= page <= 100000:
                raise ValueError()
        except (TypeError, ValueError):
            return fail('页码无效', 400, 'INVALID_PAGE')
        from .dynamic_database import store_connection_ready
        if not store_connection_ready(store):
            return fail('门店数据暂不可用', 503, 'STORE_UNAVAILABLE')
        try:
            data = detail(store, page)
        except DatabaseError:
            return fail('门店数据暂不可用', 503, 'STORE_UNAVAILABLE')
        PlatformAuditEvent.objects.create(account=request.platform_account,
            action='pass_refunds.view_detail', target_store_slug=store.slug,
            target_type='pass_refund_shortfall', detail={'page': page})
        response = Response({'code': 0, 'data': data})
        response['Cache-Control'] = 'no-store'
        return response


class PlatformAccountsView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not admin_origin(request) or not platform_may(request.platform_account, 'platform_accounts:view'):
            return fail('无平台人员管理权限', 403, 'PLATFORM_FORBIDDEN')
        from .platform_accounts import serialize
        accounts = PlatformAccount.objects.order_by('username').prefetch_related('grants')
        return Response({'code': 0, 'data': {'results': [serialize(account) for account in accounts]}})

    def post(self, request):
        if not admin_origin(request) or not platform_may(request.platform_account, 'platform_accounts:manage'):
            return fail('无平台人员管理权限', 403, 'PLATFORM_FORBIDDEN')
        from .platform_accounts import AccountChangeError, create_account
        try:
            return Response({'code': 0, 'data': create_account(request.auth, request.data)}, status=201)
        except AccountChangeError as error:
            return fail(str(error), error.status, 'PLATFORM_ACCOUNT_INVALID')


class PlatformAccountDetailView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def patch(self, request, account_id):
        if not admin_origin(request) or not platform_may(request.platform_account, 'platform_accounts:manage'):
            return fail('无平台人员管理权限', 403, 'PLATFORM_FORBIDDEN')
        from .platform_accounts import AccountChangeError, update_account
        try:
            return Response({'code': 0, 'data': update_account(request.auth, account_id, request.data)})
        except AccountChangeError as error:
            return fail(str(error), error.status, 'PLATFORM_ACCOUNT_INVALID')


class PlatformAccountAuditView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, account_id):
        if not admin_origin(request) or not platform_may(request.platform_account, 'platform_accounts:view'):
            return fail('无平台人员管理权限', 403, 'PLATFORM_FORBIDDEN')
        if not PlatformAccount.objects.filter(pk=account_id).exists():
            return fail('平台账号不存在', 404, 'PLATFORM_ACCOUNT_INVALID')
        events = PlatformAuditEvent.objects.filter(target_type='platform_account', target_id=str(account_id)).select_related('account').order_by('-created_at', '-id')[:100]
        return Response({'code': 0, 'data': {'results': [{'action': event.action, 'actor': event.account.username,
            'created_at': event.created_at.isoformat(), 'detail': event.detail} for event in events]}})
