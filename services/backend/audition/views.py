from django.conf import settings
from django.db import IntegrityError
from stores.db import transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from .models import AuditionLink, AuditionSignup
from .serializers import (
    AuditionPublicSerializer,
    AuditionSignupCreateSerializer,
    MyAuditionSignupSerializer,
)

VALID_ROLES = ('boss', 'provider')


class AuditionEnterView(APIView):
    """Validate an invitation against the authenticated owner, without issuing credentials."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '请登录对应的老板账号'}, status=403)
        token = request.query_params.get('token', '')
        if not isinstance(token, str) or not token or len(token) > 64 or request.query_params.get('role', 'boss') != 'boss':
            return Response({'code': 400, 'msg': '邀请参数无效'}, status=400)
        link = AuditionLink.objects.filter(boss_token=token).first()
        if not link:
            return Response({'code': 404, 'msg': '邀请不存在或已撤销'}, status=404)
        if not link.is_active or (link.expire_at and link.expire_at <= timezone.now()):
            return Response({'code': 410, 'msg': '邀请已停用或过期'}, status=410)
        owner_id = link.boss_account_id
        if not owner_id and link.boss_user_id:
            from club_accounts.models import LegacyAccountMap
            owner_id = LegacyAccountMap.objects.filter(legacy_user_id=link.boss_user_id).values_list('account_id', flat=True).first()
        if owner_id != request.account.pk:
            return Response({'code': 403, 'msg': '当前老板账号与邀请不匹配，请联系平台客服'}, status=403)
        return Response({'code': 0, 'data': {'matched': True, 'link': AuditionPublicSerializer(link).data}})


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
        if link.expire_at and link.expire_at <= timezone.now():
            return Response({'code': 410, 'msg': '该试音链接已过期'})

        data = AuditionPublicSerializer(link).data
        data['role'] = role
        return Response({'code': 0, 'data': data})


class AuditionExchangeView(APIView):
    """Check a bound invitation against an already authenticated account."""

    permission_classes = [IsClubAccountAuthenticated]

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
        if link.expire_at and link.expire_at <= timezone.now():
            return Response({'code': 410, 'msg': '该试音链接已过期'})

        bound_account = link.boss_account if role == 'boss' else link.provider_account
        if bound_account is None:
            return Response({'code': 409, 'msg': '该入口尚未绑定账号，请联系客服'})
        expected_type = (
            ClubAccount.AccountType.BOSS
            if role == 'boss'
            else ClubAccount.AccountType.PROVIDER
        )
        if bound_account.account_type != expected_type:
            return Response({'code': 409, 'msg': '入口绑定账号类型错误，请联系客服'})
        if request.account.pk != bound_account.pk or request.account.account_type != expected_type:
            return Response({'code': 403, 'msg': '当前账号与邀请不匹配，请使用受邀账号登录'}, status=403)
        if not bound_account.is_active or not bound_account.can_login:
            return Response({'code': 403, 'msg': '该账号已被禁用'}, status=403)
        return Response({'code': 0, 'data': {'matched': True, 'link': AuditionPublicSerializer(link).data}})


class AuditionSignupView(APIView):
    """登录态下提交试音报名：陪玩凭 provider_token 报名。

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
        if link.expire_at and link.expire_at <= timezone.now():
            return Response({'code': 410, 'msg': '该试音链接已过期'})
        if link.provider_account_id and link.provider_account_id != request.account.pk:
            return Response({'code': 403, 'msg': '当前陪玩账号与邀请不匹配'}, status=403)

        serializer = AuditionSignupCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
        if guarded:
            from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
            try:
                ensure_provider_lifecycle(request.account)
            except ProviderLifecycleError:
                return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验'}, status=503)
        try:
            with transaction.atomic():
                if guarded:
                    from club_accounts.provider_lifecycle import lock_active_provider_operation
                    try:
                        if not lock_active_provider_operation(request.account):
                            return Response({'code': 409, 'msg': '陪玩账号状态已变化，请重新登录'}, status=409)
                    except ProviderLifecycleError:
                        return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验'}, status=503)
                # Staff can revoke or rebind an invitation while the request
                # is in flight; serialize the final check with that change.
                link = AuditionLink.objects.select_for_update().filter(pk=link.pk, provider_token=token).first()
                if not link or not link.is_active or (link.expire_at and link.expire_at <= timezone.now()):
                    return Response({'code': 410, 'msg': '该试音链接已停用或过期'}, status=410)
                if link.provider_account_id and link.provider_account_id != request.account.pk:
                    return Response({'code': 403, 'msg': '当前陪玩账号与邀请不匹配'}, status=403)
                signup = serializer.save(
                    link=link,
                    applicant=getattr(request, 'legacy_user', None),
                    applicant_account=request.account,
                    applicant_business_cycle=request.account.business_cycle,
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
