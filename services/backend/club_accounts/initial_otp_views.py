"""Anonymous first-admin OTP enrollment bound to a platform-issued capability."""
import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from urllib.parse import quote, urlencode
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.context import resolve_store, store_context, StoreNotFound, StoreUnavailable
from stores.db import transaction
from stores.hosts import is_admin_host, STORE_SLUG
from stores.models import Store
from stores.platform_initial_otp import SALT, eligible, fingerprint
from console.models import AdminAuditLog
from .models import ClubAccount, InitialAdminOtpGrant, OtpCredential
from .otp import encrypt_secret, decrypt_secret, matching_counter, recovery_hash

def failure(message='引导凭据或账号信息无效，请联系平台重新签发', status=400):
    response = Response({'code': status, 'msg': message}, status=status)
    response['Cache-Control'] = 'no-store'
    return response

def audit(account, event, status):
    AdminAuditLog.objects.create(operator_account=account, operator_name=account.username, method='POST',
        path='/api/admin/auth/initial-otp', resource='initial_admin_otp', object_id=str(account.pk),
        request_body={'event': event}, status_code=status)

class InitialAdminOtpView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = 'login'

    def post(self, request):
        if not is_admin_host(request.get_host()):
            return failure(status=403)
        data = request.data
        if not isinstance(data, Mapping):
            return failure()
        token, username, password = data.get('token'), data.get('username'), data.get('password')
        if not all(isinstance(x, str) for x in (token, username, password)) or len(token) > 2048 or len(password) > 1024:
            return failure()
        try:
            claim = signing.loads(token, salt=SALT, max_age=600)
            slug = claim['store_slug']
            if (claim.get('purpose') != 'initial-admin-otp' or not isinstance(slug, str)
                    or not STORE_SLUG.fullmatch(slug) or type(claim.get('account_id')) is not int):
                return failure()
            store = resolve_store(slug, require_active=False, allow_cached=False)
            if store.initialized_at is None or store.status != Store.Status.CONFIGURING:
                return failure()
            with store_context(store), transaction.atomic():
                account = ClubAccount.objects.select_for_update().filter(pk=claim['account_id']).first()
                if not account:
                    return failure()
                grant = InitialAdminOtpGrant.objects.select_for_update().filter(pk=claim['grant_id'], account=account).first()
                if (not grant or not hmac.compare_digest(grant.token_hash, hashlib.sha256(token.encode()).hexdigest())
                        or grant.revoked_at or grant.consumed_at or grant.expires_at <= timezone.now()
                        or grant.attempts >= 5 or grant.password_fingerprint != fingerprint(account)
                        or not eligible(account) or OtpCredential.objects.filter(account=account).exists()):
                    return failure()
                if account.username != username or not account.check_password(password):
                    grant.attempts += 1
                    grant.save(update_fields=['attempts'])
                    audit(account, 'credentials_failed', 400)
                    return failure()
                code = data.get('otpCode')
                if code is None:
                    if grant.prepared_at:
                        return failure('绑定资料已显示，请输入动态验证码；如已丢失请联系平台重新签发')
                    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
                    grant.secret_ciphertext = encrypt_secret(secret)
                    grant.prepared_at = timezone.now()
                    grant.save(update_fields=['secret_ciphertext', 'prepared_at'])
                    issuer = 'XA Esports'
                    uri = 'otpauth://totp/' + quote(f'{issuer}:console:{account.username}', safe='') + '?' + urlencode(
                        {'secret': secret, 'issuer': issuer, 'algorithm': 'SHA1', 'digits': 6, 'period': 30})
                    audit(account, 'prepared', 200)
                    response = Response({'code': 0, 'data': {'otpSecret': secret, 'otpUri': uri}, 'msg': '请使用验证器绑定并提交动态验证码'})
                else:
                    if not grant.prepared_at or not grant.secret_ciphertext:
                        return failure()
                    grant.attempts += 1
                    grant.save(update_fields=['attempts'])
                    counter = matching_counter(decrypt_secret(grant.secret_ciphertext), code)
                    if counter is None:
                        audit(account, 'otp_failed', 400)
                        return failure('动态验证码错误或已过期')
                    codes = [secrets.token_hex(8).upper() for _ in range(8)]
                    codes = [value[:8] + '-' + value[8:] for value in codes]
                    OtpCredential.objects.create(account=account, secret_ciphertext=grant.secret_ciphertext,
                        last_counter=counter, recovery_hashes=[recovery_hash(value) for value in codes])
                    grant.consumed_at = timezone.now()
                    grant.secret_ciphertext = ''
                    grant.save(update_fields=['consumed_at', 'secret_ciphertext'])
                    audit(account, 'completed', 200)
                    response = Response({'code': 0, 'data': {'recoveryCodes': codes}, 'msg': '验证器已绑定，请使用账号密码和动态验证码登录'})
                response['Cache-Control'] = 'no-store'
                return response
        except (signing.BadSignature, KeyError, TypeError, ValueError, ValidationError, StoreNotFound, StoreUnavailable):
            return failure()
        except DatabaseError:
            return failure('引导服务暂不可用，请稍后重试', 503)
