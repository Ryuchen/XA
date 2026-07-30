from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
from club_accounts.tokens import issue_account_tokens
from users.views import ROLE_REVERSE_MAP

from .models import AuditionLink, AuditionSignup
from .serializers import (
    AuditionPublicSerializer,
    AuditionSignupCreateSerializer,
    MyAuditionSignupSerializer,
)

VALID_ROLES = ('boss', 'provider')


class AuditionPublicView(APIView):
    """免登录试音落地页接口：凭分享 token 拉取试音活动信息。

    GET /api/audition/info?token=xxx&role=boss|provider
    仅做只读校验，不换发登录态，不回传任何 token。
    """

    permission_classes = [AllowAny]

    def get(self, request):
        token = (request.query_params.get('token') or '').strip()
        role = (request.query_params.get('role') or '').strip()
        if not token or role not in VALID_ROLES:
            return Response({'code': 400, 'msg': '链接参数无效'})

        token_field = 'boss_token' if role == 'boss' else 'provider_token'
        try:
            link = AuditionLink.objects.get(**{token_field: token})
        except AuditionLink.DoesNotExist:
            return Response({'code': 404, 'msg': '链接不存在或已被撤销'})

        if not link.is_active:
            return Response({'code': 410, 'msg': '该试音活动已停用'})
        if link.expire_at and link.expire_at < timezone.now():
            return Response({'code': 410, 'msg': '该试音链接已过期'})

        data = AuditionPublicSerializer(link).data
        data['role'] = role
        return Response({'code': 0, 'data': data})


class AuditionExchangeView(APIView):
    """免登录换发 JWT：凭分享 token 换取链接绑定用户的真正登录态。

    POST /api/audition/exchange  body: {token, role}
    校验链接有效性后，签发 boss_user / provider_user 的 JWT，
    返回结构与微信/账号登录完全一致（{token, userInfo}）。
    未绑定用户时返回明确提示，不创建临时用户。
    """

    permission_classes = [AllowAny]

    def post(self, request):
        token = (request.data.get('token') or '').strip()
        role = (request.data.get('role') or '').strip()
        if not token or role not in VALID_ROLES:
            return Response({'code': 400, 'msg': '链接参数无效'})

        token_field = 'boss_token' if role == 'boss' else 'provider_token'
        try:
            link = AuditionLink.objects.select_related(
                'boss_account',
                'provider_account',
            ).get(
                **{token_field: token}
            )
        except AuditionLink.DoesNotExist:
            return Response({'code': 404, 'msg': '链接不存在或已被撤销'})

        if not link.is_active:
            return Response({'code': 410, 'msg': '该试音活动已停用'})
        if link.expire_at and link.expire_at < timezone.now():
            return Response({'code': 410, 'msg': '该试音链接已过期'})

        bound_account = link.boss_account if role == 'boss' else link.provider_account
        if bound_account is None:
            legacy_user = link.boss_user if role == 'boss' else link.provider_user
            if legacy_user is not None:
                bound_account = get_or_create_account_for_legacy_user(legacy_user)
                link_legacy_relations(legacy_user, bound_account)
        if bound_account is None:
            return Response({'code': 409, 'msg': '该入口尚未绑定账号，请联系客服'})
        expected_type = (
            ClubAccount.AccountType.BOSS
            if role == 'boss'
            else ClubAccount.AccountType.PROVIDER
        )
        if bound_account.account_type != expected_type:
            return Response({'code': 409, 'msg': '入口绑定账号类型错误，请联系客服'})
        if not bound_account.is_active or not bound_account.can_login:
            return Response({'code': 403, 'msg': '该账号已被禁用'})

        access_token, refresh_token = issue_account_tokens(bound_account)
        frontend_role = ROLE_REVERSE_MAP.get(bound_account.account_type, 'customer')
        legacy_user = link.boss_user if role == 'boss' else link.provider_user
        response_id = (
            legacy_user.id
            if getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False) and legacy_user
            else bound_account.id
        )
        backend_role = (
            legacy_user.role
            if getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False) and legacy_user
            else bound_account.account_type
        )

        return Response({
            'code': 0,
            'msg': 'success',
            'data': {
                'token': access_token,
                'refreshToken': refresh_token,
                'userInfo': {
                    'id': str(response_id),
                    'username': bound_account.username,
                    'nickname': bound_account.nickname or bound_account.username,
                    'role': frontend_role,
                    'backendRole': backend_role,
                    'openid': bound_account.openid,
                    'phone': bound_account.phone,
                    'bindCode': None,
                    'bindStatus': 'direct' if frontend_role == 'customer' else 'bound',
                },
            },
        })


class AuditionSignupView(APIView):
    """登录态下提交试音报名：陪玩换发 JWT 后，凭 provider_token 报名。

    POST /api/audition/signup  body: {token, contact, game, remark}
    报名人取自登录态，仅 PROVIDER 角色可提交；同一链接同一人只能报名一次。
    审核交由后台，本接口不涉及任何资金动作。
    """

    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩账号可报名试音'})

        token = (request.data.get('token') or '').strip()
        if not token:
            return Response({'code': 400, 'msg': '链接参数无效'})

        try:
            link = AuditionLink.objects.get(provider_token=token)
        except AuditionLink.DoesNotExist:
            return Response({'code': 404, 'msg': '链接不存在或已被撤销'})

        if not link.is_active:
            return Response({'code': 410, 'msg': '该试音活动已停用'})
        if link.expire_at and link.expire_at < timezone.now():
            return Response({'code': 410, 'msg': '该试音链接已过期'})

        serializer = AuditionSignupCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                signup = serializer.save(
                    link=link,
                    applicant=request.legacy_user,
                    applicant_account=request.account,
                )
        except IntegrityError:
            return Response({'code': 409, 'msg': '您已报名该试音活动，请勿重复提交'})

        return Response({
            'code': 0,
            'msg': '报名成功，请等待客服审核',
            'data': {'id': signup.id, 'status': signup.status},
        })


class MyAuditionSignupView(APIView):
    """陪玩查看自己的试音报名记录：含审核状态与客服备注。

    GET /api/audition/my-signups
    仅返回当前登录用户提交过的报名，按提交时间倒序。
    """

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        signups = (
            AuditionSignup.objects.filter(applicant_account=request.account)
            .select_related('link')
            .order_by('-created_at')
        )
        data = MyAuditionSignupSerializer(signups, many=True).data
        return Response({'code': 0, 'data': data})
