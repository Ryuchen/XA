import uuid
import random

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .managers import ClubAccountManager


def generate_account_no():
    while True:
        account_no = f'XA{random.randint(0, 999999):06d}'
        if not ClubAccount.objects.filter(account_no=account_no).exists():
            return account_no


class ClubAccount(AbstractBaseUser):
    """俱乐部业务账户，与 Django auth.User 完全独立。"""

    class AccountType(models.TextChoices):
        STAFF = 'STAFF', '后台人员'
        BOSS = 'BOSS', '老板'
        PROVIDER = 'PROVIDER', '陪玩'

    username = models.CharField(max_length=150, unique=True, db_index=True)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        db_index=True,
    )
    openid = models.CharField(max_length=100, blank=True, null=True, unique=True)
    wechat_appid = models.CharField(max_length=32, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    avatar = models.ImageField(upload_to='accounts/avatars/', blank=True, null=True)
    avatar_url = models.URLField(blank=True, null=True)
    nickname = models.CharField(max_length=50, blank=True, default='')
    real_name = models.CharField(max_length=50, blank=True, default='')
    is_phone_verified = models.BooleanField(default=False)
    is_openid_bound = models.BooleanField(default=False)
    game_region = models.CharField(max_length=50, blank=True, default='')
    game_nickname = models.CharField(max_length=50, blank=True, default='')
    game_uid = models.CharField(max_length=50, blank=True, default='')
    inviter = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invitees',
    )
    inviter_commission_rate = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    boss_type = models.ForeignKey(
        'users.BossType',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='club_accounts',
    )
    business_cycle = models.PositiveIntegerField(default=1, editable=False)
    license_membership_active = models.BooleanField(default=True, editable=False)
    account_no = models.CharField(max_length=32, unique=True, db_index=True)
    can_login = models.BooleanField(default=True, db_index=True)
    can_view = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    last_active_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ClubAccountManager()

    USERNAME_FIELD = 'username'

    class Meta:
        verbose_name = '俱乐部业务账户'
        verbose_name_plural = '俱乐部业务账户'
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(account_type__in=['STAFF', 'BOSS', 'PROVIDER']),
                name='club_account_type_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(inviter_commission_rate__range=(0, 100)),
                name='club_account_inviter_rate_valid',
            ),
        ]
        indexes = [
            models.Index(
                fields=['account_type', 'is_active', '-created_at'],
                name='club_account_type_active_idx',
            ),
            models.Index(
                fields=['account_type', 'can_login', 'is_active'],
                name='club_account_login_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and self.account_type in ('BOSS', 'PROVIDER'):
            from .license_quota import create_account_with_quota
            return create_account_with_quota(self, lambda: self._save_account(*args, **kwargs),
                                             using=kwargs.get('using'))
        if not self._state.adding:
            fields = kwargs.get('update_fields')
            protected = {'account_type', 'license_membership_active', 'business_cycle'}
            if fields is not None and protected.intersection(fields):
                raise ValueError('账号类型和业务周期须通过账号生命周期服务更新')
            if fields is None:
                # Do not write stale membership or cycle values from a form
                # loaded before retirement; preserve Django's deferred fields.
                kwargs['update_fields'] = [field.name for field in self._meta.concrete_fields
                    if not field.primary_key and not getattr(field, 'generated', False)
                    and field.name not in protected and field.attname in self.__dict__]
        return self._save_account(*args, **kwargs)

    def _save_account(self, *args, **kwargs):
        if not self.account_no:
            self.account_no = generate_account_no()
        if self._state.adding and self.account_type == self.AccountType.PROVIDER:
            # The account and its lifecycle barrier must commit together.  An
            # operation must never need to lazily insert a lock while holding
            # any order, profile or wallet lock.
            from django.db import transaction
            from stores.context import current_database
            alias = kwargs.get('using') or current_database()
            with transaction.atomic(using=alias):
                result = super().save(*args, **kwargs)
                ProviderLifecycleLock.objects.using(alias).create(account_id=self.pk)
                return result
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.nickname or self.username


class LicenseQuotaLock(models.Model):
    """One lock per account category, stored in each store's business database."""
    account_type = models.CharField(max_length=20, primary_key=True)
    active_count = models.PositiveIntegerField(default=0)


class ProviderLifecycleLock(models.Model):
    """Per-store provider lifecycle barrier, acquired before business locks."""
    account = models.OneToOneField(ClubAccount, on_delete=models.PROTECT, primary_key=True,
                                   related_name='provider_lifecycle_lock')


class LicenseStatusSnapshot(models.Model):
    """Authenticated last-known control-plane terms, stored per business DB."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    store_slug = models.SlugField(max_length=48, blank=True, default='')
    payload = models.JSONField(default=dict)
    signature = models.CharField(max_length=64, blank=True, default='')
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='license_snapshot_singleton')]


class StoreRoutingSnapshot(models.Model):
    """Signed routing identity retained in this store's own business database."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    payload = models.JSONField(default=dict)
    signature = models.CharField(blank=True, default='', max_length=64)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='store_routing_snapshot_singleton')]


class LegacyAccountMap(models.Model):
    """永久保存旧 CustomUser ID 与新业务账户 ID 的映射。"""

    legacy_user_id = models.PositiveBigIntegerField(unique=True, db_index=True)
    account = models.OneToOneField(
        ClubAccount,
        on_delete=models.PROTECT,
        related_name='legacy_mapping',
    )
    legacy_role = models.CharField(max_length=20, blank=True, default='')
    migrated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '旧账户映射'
        verbose_name_plural = '旧账户映射'
        ordering = ['legacy_user_id']

    def __str__(self):
        return f'{self.legacy_user_id} -> {self.account_id}'

class WechatBindingChange(models.Model):
    account = models.ForeignKey(ClubAccount, on_delete=models.PROTECT, related_name='wechat_binding_changes')
    from_appid = models.CharField(max_length=32)
    to_appid = models.CharField(max_length=32)
    old_openid_hash = models.CharField(max_length=64)
    new_openid_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

class WechatIdentity(models.Model):
    """Channel-specific WeChat identity belonging to one unified business account."""
    account = models.ForeignKey(ClubAccount, on_delete=models.CASCADE, related_name='wechat_identities')
    channel = models.CharField(max_length=20)
    appid = models.CharField(max_length=64)
    openid = models.CharField(max_length=100)
    unionid = models.CharField(max_length=100, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '微信渠道身份'
        constraints = [models.UniqueConstraint(fields=['channel', 'appid', 'openid'], name='uniq_wechat_channel_openid')]
        indexes = [models.Index(fields=['account', 'channel'], name='wechat_account_channel_idx')]


class OtpCredential(models.Model):
    account = models.OneToOneField(ClubAccount, on_delete=models.CASCADE, related_name='otp_credential')
    secret_ciphertext = models.TextField()
    version = models.UUIDField(default=uuid.uuid4, editable=False)
    last_counter = models.BigIntegerField(default=-1)
    recovery_hashes = models.JSONField(default=list)
    enabled_at = models.DateTimeField(auto_now_add=True)

class OtpLoginChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(ClubAccount, on_delete=models.CASCADE, related_name='otp_login_challenges')
    scope = models.CharField(max_length=12)
    password_fingerprint = models.CharField(max_length=64)
    secret_ciphertext = models.TextField(blank=True, default='')
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes=[models.Index(fields=['account','created_at'],name='otp_account_created_idx')]


class InitialAdminOtpGrant(models.Model):
    """One-use platform-issued enrollment grant, stored in its own store."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(ClubAccount, on_delete=models.PROTECT, related_name='initial_otp_grants')
    token_hash = models.CharField(max_length=64, unique=True)
    password_fingerprint = models.CharField(max_length=64)
    secret_ciphertext = models.TextField(blank=True, default='')
    platform_account_id = models.UUIDField()
    operation_id = models.CharField(max_length=32)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    prepared_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
