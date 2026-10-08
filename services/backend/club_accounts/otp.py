import base64
import hashlib
import hmac
import secrets
import struct
from datetime import timedelta
from urllib.parse import quote, urlencode
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.response import Response
from .models import ClubAccount, OtpCredential, OtpLoginChallenge

def otp_required(account):
    return settings.LOGIN_OTP_REQUIRED and account.account_type in ('STAFF','PROVIDER')

def _cipher(key):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('xa:login-otp:v1:'+key).encode()).digest()))

def encrypt_secret(secret):
    return _cipher(settings.SECRET_KEY).encrypt(secret.encode()).decode()

def decrypt_secret(encrypted):
    for key in [settings.SECRET_KEY,*settings.SECRET_KEY_FALLBACKS]:
        try:return _cipher(key).decrypt(encrypted.encode()).decode()
        except (InvalidToken,UnicodeError):pass
    raise ValueError('动态验证码密钥无法读取，请联系管理员')

def hotp(secret,counter,digits=6):
    key=base64.b32decode(secret+'='*((8-len(secret)%8)%8))
    digest=hmac.new(key,struct.pack('>Q',counter),hashlib.sha1).digest()
    offset=digest[-1]&15
    value=struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff
    return str(value%(10**digits)).zfill(digits)

def matching_counter(secret,code,last_counter=-1):
    if not isinstance(code,str) or len(code)!=6 or not code.isascii() or not code.isdigit():return None
    current=int(timezone.now().timestamp())//30
    for counter in (current,current-1,current+1):
        if counter>last_counter and hmac.compare_digest(hotp(secret,counter),code):return counter
    return None

def recovery_hash(code,key=None):
    return salted_hmac('xa:otp:recovery:v1',code.replace('-','').replace(' ','').upper(),secret=key or settings.SECRET_KEY,algorithm='sha256').hexdigest()

def _failure(message):
    return Response({'code':400,'msg':message,'errorCode':'OTP_INVALID'})

def otp_login_gate(account,data,scope):
    if not otp_required(account):return None
    with transaction.atomic():
        account=ClubAccount.objects.select_for_update().get(pk=account.pk)
        credential=OtpCredential.objects.select_for_update().filter(account=account).first()
        attempts=OtpLoginChallenge.objects.filter(account=account,created_at__gte=timezone.now()-timedelta(minutes=5)).aggregate(total=Sum('attempts'))['total'] or 0
        if attempts>=20:return _failure('动态验证码尝试过多，请5分钟后再试')
        raw=data.get('otpChallenge')
        if not raw:
            secret='' if credential else base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
            challenge=OtpLoginChallenge.objects.create(account=account,scope=scope,
                password_fingerprint=hashlib.sha256(account.password.encode()).hexdigest(),
                secret_ciphertext=encrypt_secret(secret) if secret else '',
                expires_at=timezone.now()+timedelta(minutes=10))
            payload={'requiresOTP':True,'requiresOTPSetup':credential is None,'otpChallenge':str(challenge.pk),'expiresIn':600}
            if secret:
                issuer='XA Esports'
                label=f'{issuer}:{scope}:{account.username}'
                payload['otpSecret']=secret
                payload['otpUri']='otpauth://totp/'+quote(label,safe='')+'?'+urlencode({'secret':secret,'issuer':issuer,'algorithm':'SHA1','digits':6,'period':30})
                import qrcode,io
                image=qrcode.make(payload['otpUri'])
                output=io.BytesIO();image.save(output,format='PNG')
                payload['otpQr']='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
            return Response({'code':0,'msg':'请绑定验证器并输入动态验证码' if secret else '请输入验证器动态验证码','data':payload})
        if not isinstance(raw,str) or len(raw)!=36:return _failure('验证请求无效，请重新输入账号密码')
        try:challenge=OtpLoginChallenge.objects.select_for_update().get(pk=raw,account=account,scope=scope)
        except (OtpLoginChallenge.DoesNotExist,ValueError,ValidationError):
            return _failure('验证请求无效，请重新输入账号密码')
        if challenge.consumed or challenge.expires_at<=timezone.now() or challenge.attempts>=5 or challenge.password_fingerprint!=hashlib.sha256(account.password.encode()).hexdigest():
            return _failure('验证请求已失效，请重新输入账号密码')
        challenge.attempts+=1
        challenge.save(update_fields=['attempts'])
        code=data.get('otpCode','')
        if not isinstance(code,str) or len(code)>32:return _failure('请输入动态验证码或备用恢复码')
        recovery_used=False
        backup_codes=[]
        try:
            secret=decrypt_secret(credential.secret_ciphertext if credential else challenge.secret_ciphertext)
        except ValueError as exc:return _failure(str(exc))
        if not secret:return _failure('绑定状态已变化，请重新登录')
        counter=matching_counter(secret,code,credential.last_counter if credential else -1)
        if counter is None and credential and len(code.replace('-','').replace(' ',''))==16:
            hashes=[recovery_hash(code,key) for key in [settings.SECRET_KEY,*settings.SECRET_KEY_FALLBACKS]]
            found=next((saved for saved in credential.recovery_hashes if any(hmac.compare_digest(saved,item) for item in hashes)),None)
            if found:
                credential.recovery_hashes.remove(found);recovery_used=True
        if counter is None and not recovery_used:return _failure('动态验证码错误、已使用或已过期')
        if credential is None:
            # A challenge created before another enrollment cannot replace its new secret.
            if not challenge.secret_ciphertext:return _failure('请重新开始绑定')
            backup_codes=[secrets.token_hex(8).upper() for _ in range(8)]
            backup_codes=[code[:8]+'-'+code[8:] for code in backup_codes]
            credential=OtpCredential.objects.create(account=account,secret_ciphertext=challenge.secret_ciphertext,
                last_counter=counter,recovery_hashes=[recovery_hash(code) for code in backup_codes])
        else:
            if challenge.secret_ciphertext:return _failure('账号已完成绑定，请重新登录')
            if counter is not None:credential.last_counter=counter
            credential.save(update_fields=['last_counter','recovery_hashes'])
        challenge.consumed=True;challenge.save(update_fields=['consumed'])
        return {'otp_verified':True,'recoveryCodes':backup_codes,'recoveryCodeUsed':recovery_used}
