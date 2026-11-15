import uuid
from django.db import models


class ProviderWechatUnionOwner(models.Model):
    """Store-local, transactional owner of a WeChat UnionID for providers."""
    unionid = models.CharField(max_length=100, unique=True)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT,
        related_name='provider_wechat_union_owners')
    created_at = models.DateTimeField(auto_now_add=True)


class WebLoginSession(models.Model):
    """Short-lived handoff from an explicitly confirmed WeChat login to its browser."""
    ticket = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poll_token_hash = models.CharField(max_length=64)
    bridge_token_hash = models.CharField(max_length=64, blank=True)
    audience = models.CharField(max_length=12, default='boss', db_index=True)
    purpose = models.CharField(max_length=20, default='login')
    expected_account = models.ForeignKey('club_accounts.ClubAccount', null=True, blank=True,
        on_delete=models.CASCADE, related_name='web_login_requests')
    expected_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    confirmed_account = models.ForeignKey('club_accounts.ClubAccount', null=True, blank=True,
        on_delete=models.CASCADE, related_name='web_login_confirmations')
    wechat_openid = models.CharField(max_length=100, blank=True)
    wechat_unionid = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    sms_hash = models.CharField(max_length=64, blank=True)
    sms_sent_at = models.DateTimeField(null=True, blank=True)
    sms_expires_at = models.DateTimeField(null=True, blank=True)
    sms_attempts = models.PositiveSmallIntegerField(default=0)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = '网页微信登录会话'


class PasswordLoginChallenge(models.Model):
    """A password-bound, single-use SMS challenge; never stores the plaintext code."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.CASCADE,
        related_name='password_login_challenges')
    scope = models.CharField(max_length=12)
    password_fingerprint = models.CharField(max_length=64)
    phone = models.CharField(max_length=20)
    sms_hash = models.CharField(max_length=64)
    sms_sent_at = models.DateTimeField()
    sms_expires_at = models.DateTimeField()
    sms_attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
    consumed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '账号密码短信登录挑战'
        indexes = [models.Index(fields=['account', 'created_at'], name='password_sms_account_idx')]
