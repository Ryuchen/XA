from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

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
            link = AuditionLink.objects.select_related('boss_user', 'provider_user').get(
                **{token_field: token}
            )
        except AuditionLink.DoesNotExist:
            return Response({'code': 404, 'msg': '链接不存在或已被撤销'})

        if not link.is_active:
            return Response({'code': 410, 'msg': '该试音活动已停用'})
        if link.expire_at and link.expire_at < timezone.now():
            return Response({'code': 410, 'msg': '该试音链接已过期'})

        bound_user = link.boss_user if role == 'boss' else link.provider_user
        if bound_user is None:
            return Response({'code': 409, 'msg': '该入口尚未绑定账号，请联系客服'})
        if not bound_user.is_active:
            return Response({'code': 403, 'msg': '该账号已被禁用'})

        refresh = RefreshToken.for_user(bound_user)
        frontend_role = ROLE_REVERSE_MAP.get(bound_user.role, 'customer')

        return Response({
            'code': 0,
            'msg': 'success',
            'data': {
                'token': str(refresh.access_token),
                'userInfo': {
                    'id': str(bound_user.id),
                    'username': bound_user.username,
                    'nickname': bound_user.nickname or bound_user.username,
                    'role': frontend_role,
                    'backendRole': bound_user.role,
                    'openid': bound_user.openid,
                    'phone': bound_user.phone,
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

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role != request.user.Role.PROVIDER:
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
                signup = serializer.save(link=link, applicant=request.user)
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

    permission_classes = [IsAuthenticated]

    def get(self, request):
        signups = (
            AuditionSignup.objects.filter(applicant=request.user)
            .select_related('link')
            .order_by('-created_at')
        )
        data = MyAuditionSignupSerializer(signups, many=True).data
        return Response({'code': 0, 'data': data})
