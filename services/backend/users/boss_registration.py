"""Phone-verified boss registration within the current store."""
import hmac
import re
import secrets
import uuid
from collections.abc import Mapping
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.tokens import issue_account_tokens
from stores.closure import closure_refusal_response
from stores.context import current_store
from stores.db import transaction
from wallet.models import Wallet

from .boss_registration_models import BossRegistrationChallenge
from .password_login import masked_phone, user_payload
from .sms_transport import SmsUnavailable, send_verification_code
from .views import _lock_wechat_phone


def _phone(value):
    return isinstance(value, str) and bool(re.fullmatch(r'1[3-9][0-9]{9}', value))


def _digest(row, code):
    return salted_hmac('xa:boss-registration:sms:v1',
        f'{row.phone}:{row.nonce}:{code}', algorithm='sha256').hexdigest()


def _phone_taken(phone):
    user = get_user_model()
    return (ClubAccount.objects.select_for_update().filter(account_type='BOSS', phone=phone).exists()
            or user.objects.select_for_update().filter(role=user.Role.CUSTOMER, phone=phone).exists())


def _fail(message, status=400, code='BOSS_REGISTRATION_INVALID'):
    return Response({'code': status, 'errorCode': code, 'msg': message}, status=status)


class BossRegistrationSmsView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if not isinstance(request.data, Mapping) or set(request.data) != {'phone'} or not _phone(request.data.get('phone')):
            return _fail('请填写有效的手机号')
        phone = request.data['phone']
        with transaction.atomic():
            # Shared with both existing WeChat phone-binding entry points.
            _lock_wechat_phone(phone)
            if _phone_taken(phone):
                return _fail('该手机号已关联老板账号，请登录或联系客服', 409, 'PHONE_IN_USE')
            row = BossRegistrationChallenge.objects.select_for_update().filter(phone=phone).first()
            if row is None:
                # No account has been submitted yet: this is a new registration
                # intent, not recovery of an already started verification flow.
                denied = closure_refusal_response(current_store())
                if denied is not None:
                    if denied.status_code == 409:
                        denied.data['msg'] = '本店处于闭店公示或待结清阶段，暂不接受新老板注册'
                    return denied
            now = timezone.now()
            if row and row.sent_at + timedelta(seconds=60) > now:
                return _fail('验证码已发送，请稍后再试', 429, 'SMS_RATE_LIMITED')
            code = f'{secrets.randbelow(1000000):06d}'
            if row is None:
                row = BossRegistrationChallenge(phone=phone)
            row.nonce, row.sent_at, row.expires_at = uuid.uuid4(), now, now + timedelta(minutes=5)
            row.code_hash, row.attempts, row.consumed = _digest(row, code), 0, False
            row.save()
        # Persist the attempt and cooldown before calling an unreliable channel.
        try:
            send_verification_code(phone, code)
        except SmsUnavailable:
            return _fail('短信服务暂不可用，请稍后重试', 503, 'SMS_UNAVAILABLE')
        return Response({'code': 0, 'data': {'smsChallenge': str(row.nonce),
            'maskedPhone': masked_phone(phone), 'retryAfter': 60, 'expiresIn': 300}})


class BossRegistrationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        data = request.data
        if not isinstance(data, Mapping) or set(data) - {'smsChallenge', 'smsCode', 'username', 'password', 'nickname'}:
            return _fail('注册请求格式不正确')
        username, password, nickname = data.get('username'), data.get('password'), data.get('nickname', '')
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}', username):
            return _fail('登录账号须为 3–32 位字母、数字、点、下划线或短横线')
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            return _fail('登录密码须为 8–128 位')
        if not isinstance(nickname, str) or len(nickname.strip()) > 50:
            return _fail('昵称格式不正确')
        try:
            nonce = uuid.UUID(str(data.get('smsChallenge', '')))
        except (ValueError, TypeError, AttributeError):
            return _fail('验证码请求无效，请重新获取')
        phone = BossRegistrationChallenge.objects.filter(nonce=nonce).values_list('phone', flat=True).first()
        if not phone:
            return _fail('验证码请求无效，请重新获取')
        try:
            with transaction.atomic():
                _lock_wechat_phone(phone)
                row = BossRegistrationChallenge.objects.select_for_update().filter(phone=phone, nonce=nonce).first()
                if not row or row.consumed or row.expires_at <= timezone.now() or row.attempts >= 5:
                    return _fail('验证码已失效，请重新获取', code='SMS_CODE_EXPIRED')
                code = data.get('smsCode')
                if (not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code)
                        or not hmac.compare_digest(row.code_hash, _digest(row, code))):
                    row.attempts += 1
                    row.save(update_fields=['attempts'])
                    return _fail('验证码错误，请重新输入', code='SMS_CODE_INVALID')
                from club_accounts.license_quota import lock_category
                lock_category('BOSS')
                if _phone_taken(phone):
                    return _fail('该手机号已关联老板账号，请登录或联系客服', 409, 'PHONE_IN_USE')
                user_model = get_user_model()
                if (ClubAccount.objects.select_for_update().filter(username__iexact=username).exists()
                        or user_model.objects.select_for_update().filter(username__iexact=username).exists()):
                    return _fail('该登录账号已被使用，请更换', 409, 'USERNAME_IN_USE')
                from .boss_types import default_boss_type
                boss_type = default_boss_type()
                if not boss_type.is_active:
                    return _fail('默认老板类型暂不可用，请联系客服', 503, 'DEFAULT_BOSS_TYPE_UNAVAILABLE')
                # A previously issued SMS challenge only restores verification;
                # this write is the actual new-account action and must recheck.
                denied = closure_refusal_response(current_store())
                if denied is not None:
                    if denied.status_code == 409:
                        denied.data['msg'] = '本店处于闭店公示或待结清阶段，暂不接受新老板注册'
                    return denied
                display_name = nickname.strip() or username
                account = ClubAccount.objects.create_account(username=username, password=password,
                    account_type='BOSS', phone=phone, is_phone_verified=True, nickname=display_name, boss_type=boss_type)
                user = user_model.objects.create_user(username=username, password=password, role=user_model.Role.CUSTOMER,
                    phone=phone, is_phone_verified=True, nickname=display_name, boss_type=boss_type)
                LegacyAccountMap.objects.create(legacy_user_id=user.pk, account=account, legacy_role=user_model.Role.CUSTOMER)
                if Wallet.objects.filter(user=user, account__isnull=True).update(account=account) != 1:
                    raise RuntimeError('老板钱包初始化失败')
                row.consumed = True
                row.save(update_fields=['consumed'])
                token, refresh = issue_account_tokens(account, auth_method='password_sms')
                payload = user_payload(account, 'boss')
            return Response({'code': 0, 'data': {'token': token, 'refreshToken': refresh, 'userInfo': payload}}, status=201)
        except IntegrityError:
            return _fail('账号注册发生冲突，请重新核对后重试', 409, 'ACCOUNT_CONFLICT')
