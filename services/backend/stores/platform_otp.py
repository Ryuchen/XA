"""Session-bound enrollment for a platform account's first authenticator."""
import base64
import io
import secrets
from datetime import timedelta
from urllib.parse import quote, urlencode
from uuid import UUID

import qrcode
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError

from club_accounts.otp import decrypt_secret, encrypt_secret, matching_counter, recovery_hash
from .models import PlatformAccount, PlatformAuditEvent, PlatformOtpCredential, PlatformOtpEnrollment
from .platform_auth import PlatformAuthError, account_for_token


def enroll(token, data):
    try:
        session_id = UUID(str(token.get('session_id')))
        account = account_for_token(token)
    except (ValueError, TypeError, TokenError):
        raise PlatformAuthError('登录状态已失效，请重新登录') from None
    error = None
    result = None
    with transaction.atomic(using='default'):
        account = PlatformAccount.objects.select_for_update().filter(pk=account.pk, is_active=True).first()
        if not account:
            raise PlatformAuthError('平台账号不可用')
        try:
            # Check the current account, grant, phone or OTP again under the lock.
            account_for_token(token)
        except TokenError:
            raise PlatformAuthError('平台账号或授权已变化，请重新登录') from None
        if PlatformOtpCredential.objects.select_for_update().filter(account=account).exists():
            raise PlatformAuthError('验证器已绑定，不能重复绑定')
        now = timezone.now()
        attempts = PlatformOtpEnrollment.objects.filter(account=account, created_at__gte=now-timedelta(minutes=5)).aggregate(total=Sum('attempts'))['total'] or 0
        if attempts >= 20 and data.get('cancel') is not True:
            raise PlatformAuthError('验证尝试过多，请稍后重试', 'PLATFORM_RATE_LIMITED', 429)
        raw = data.get('otpChallenge')
        if data.get('cancel') is True and not raw:
            raise PlatformAuthError('绑定请求无效，请重新开始')
        if not raw:
            secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
            PlatformOtpEnrollment.objects.filter(account=account, consumed=False).update(consumed=True)
            challenge = PlatformOtpEnrollment.objects.create(account=account, session_id=session_id,
                security_version=account.security_version, secret_ciphertext=encrypt_secret(secret),
                expires_at=now+timedelta(minutes=10))
            issuer = 'XA Esports'
            label = f'{issuer}:platform:{account.username}'
            uri = 'otpauth://totp/'+quote(label, safe='')+'?'+urlencode({
                'secret': secret, 'issuer': issuer, 'algorithm': 'SHA1', 'digits': 6, 'period': 30})
            output = io.BytesIO()
            qrcode.make(uri).save(output, format='PNG')
            result = {'requiresOTPSetup': True, 'otpChallenge': str(challenge.pk), 'otpSecret': secret,
                'otpUri': uri, 'otpQr': 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode(), 'expiresIn': 600}
        else:
            try:
                challenge = PlatformOtpEnrollment.objects.select_for_update().get(pk=raw, account=account, session_id=session_id)
            except (PlatformOtpEnrollment.DoesNotExist, ValueError, TypeError, ValidationError):
                raise PlatformAuthError('绑定请求无效，请重新开始') from None
            if data.get('cancel') is True:
                challenge.consumed = True
                challenge.save(update_fields=['consumed'])
                result = {'isOtpBound': False}
            else:
                if (challenge.consumed or challenge.expires_at <= now or challenge.attempts >= 5
                        or challenge.security_version != account.security_version):
                    raise PlatformAuthError('绑定请求已失效，请重新开始')
                challenge.attempts += 1
                challenge.save(update_fields=['attempts'])
                try:
                    counter = matching_counter(decrypt_secret(challenge.secret_ciphertext), data.get('otpCode'))
                except ValueError:
                    counter = None
                if counter is None:
                    error = PlatformAuthError('动态验证码错误或已过期')
                else:
                    codes = [secrets.token_hex(8).upper() for _ in range(8)]
                    codes = [code[:8]+'-'+code[8:] for code in codes]
                    PlatformOtpCredential.objects.create(account=account, secret_ciphertext=challenge.secret_ciphertext,
                        last_counter=counter, recovery_hashes=[recovery_hash(code) for code in codes])
                    challenge.consumed = True
                    challenge.save(update_fields=['consumed'])
                    PlatformAuditEvent.objects.create(account=account, action='auth.otp_enrolled', target_type='platform_account', target_id=str(account.pk))
                    result = {'isOtpBound': True, 'recoveryCodes': codes}
    if error:
        raise error
    return result
