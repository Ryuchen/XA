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
        if not self.account_no:
            self.account_no = generate_account_no()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nickname or self.username


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
