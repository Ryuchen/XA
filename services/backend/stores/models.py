import uuid

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


class DirectoryQuerySet(models.QuerySet):
    def using(self, alias):
        if alias not in (None, 'default'):
            raise ValueError('门店目录仅允许访问平台数据库')
        return super().using('default')


class DirectoryManager(models.Manager.from_queryset(DirectoryQuerySet)):
    """The directory is never routed to a store business database."""

    def get_queryset(self):
        return super().get_queryset().using('default')


class DirectoryModel(models.Model):
    objects = DirectoryManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        kwargs['using'] = 'default'
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        kwargs['using'] = 'default'
        return super().delete(*args, **kwargs)


class Store(DirectoryModel):
    class Status(models.TextChoices):
        PROVISIONING = 'PROVISIONING', '待初始化'
        CONFIGURING = 'CONFIGURING', '待配置'
        ACTIVE = 'ACTIVE', '营业中'
        DISABLED = 'DISABLED', '已停用'

    slug = models.SlugField(max_length=48, unique=True, validators=[
        RegexValidator(r'^[a-z][a-z0-9-]{0,47}$', '门店标识须以小写字母开头，仅含小写字母、数字和短横线'),
    ])
    name = models.CharField(max_length=80)
    entity_prefix = models.CharField(max_length=4, unique=True, null=True, blank=True, default=None, validators=[
        RegexValidator(r'^[A-Z0-9]{2,4}$', '编号前缀须为2至4位大写字母或数字'),
    ])
    provider_name_prefix = models.CharField(max_length=8, blank=True, default='', validators=[
        RegexValidator(r'^[A-Z]{2,8}$', '陪玩展示简称须为2至8位大写英文字母'),
    ])
    currency_name = models.CharField(max_length=16, default='浪花')
    logo_url = models.CharField(max_length=500, blank=True, default='')
    background_url = models.CharField(max_length=500, blank=True, default='')
    introduction = models.TextField(max_length=2000, blank=True, default='')
    content_compliance_enabled = models.BooleanField(default=True)
    database_alias = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROVISIONING, db_index=True)
    initialized_at = models.DateTimeField(null=True, blank=True)
    boss_url = models.URLField(max_length=500, blank=True, default='')
    provider_url = models.URLField(max_length=500, blank=True, default='')
    admin_url = models.URLField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        verbose_name = '门店'
        verbose_name_plural = '门店'
        constraints = [models.CheckConstraint(
            condition=models.Q(status__in=['PROVISIONING', 'CONFIGURING', 'ACTIVE', 'DISABLED']),
            name='store_status_valid',
        )]

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    def __str__(self):
        return self.name


class StorePlatformGrant(DirectoryModel):
    """An explicit grant to an account in the original/default business database.

    Deliberately independent from tenant administrator roles. IDs in another
    store may overlap and never confer this grant.
    """

    account_id = models.PositiveBigIntegerField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '平台门店管理授权'
        verbose_name_plural = '平台门店管理授权'


class StoreRegistryLock(DirectoryModel):
    """Platform-owned mutex for allocating store database identities."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='store_registry_lock_singleton')]


class StoreRoutingRevision(DirectoryModel):
    """Monotonic directory version for safely publishing local route snapshots."""
    store = models.OneToOneField(Store, on_delete=models.PROTECT, primary_key=True, related_name='routing_revision')
    version = models.PositiveBigIntegerField(default=0)


class StoreProvisioningClaim(DirectoryModel):
    """Private platform record of a store's database ownership and initial admin."""

    store = models.OneToOneField(Store, on_delete=models.PROTECT, related_name='provisioning_claim')
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    database_fingerprint = models.CharField(max_length=64)
    admin_username = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PlatformAccount(DirectoryModel):
    """Control-plane identity, independent of every store's business accounts."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True)
    password = models.CharField(max_length=128)
    nickname = models.CharField(max_length=80, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    is_phone_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    security_version = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_password(self, raw_password):
        from django.contrib.auth.hashers import make_password
        self.password = make_password(raw_password)
        self.security_version = uuid.uuid4()

    def check_password(self, raw_password):
        from django.contrib.auth.hashers import check_password
        return check_password(raw_password, self.password)

    def __str__(self):
        return self.nickname or self.username


class PlatformRoleGrant(DirectoryModel):
    """A role and explicit store scope; '*' grants the role for all stores."""

    class Role(models.TextChoices):
        SUPER_ADMIN = 'SUPER_ADMIN', '平台超级管理员'
        FINANCE = 'FINANCE', '平台财务'
        ORDER_VIEWER = 'ORDER_VIEWER', '平台订单查看'
        ORDER_OPERATOR = 'ORDER_OPERATOR', '平台订单操作'

    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='grants')
    role = models.CharField(max_length=16, choices=Role.choices)
    scope_slug = models.CharField(max_length=48, default='*')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account', 'role', 'scope_slug'], name='platform_role_scope_unique'),
            models.CheckConstraint(condition=models.Q(role__in=[
                'SUPER_ADMIN', 'FINANCE', 'ORDER_VIEWER', 'ORDER_OPERATOR',
            ]), name='platform_role_valid'),
            models.CheckConstraint(condition=~models.Q(scope_slug=''), name='platform_scope_not_empty'),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.scope_slug != '*' and not Store.objects.filter(slug=self.scope_slug).exists():
            raise ValidationError({'scope_slug': '授权门店不存在'})


class PlatformAuditEvent(DirectoryModel):
    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='audit_events')
    action = models.CharField(max_length=80, db_index=True)
    target_store_slug = models.CharField(max_length=48, blank=True, default='')
    target_type = models.CharField(max_length=80, blank=True, default='')
    target_id = models.CharField(max_length=100, blank=True, default='')
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class StoreBusinessCertificate(DirectoryModel):
    """Original evidence confirmed by the platform and retained on replacement."""

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='business_certificates')
    confirmed_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT)
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    client_request_id = models.UUIDField()
    expires_on = models.DateField(null=True, blank=True)
    document = models.BinaryField(editable=False)
    content_type = models.CharField(max_length=20)
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        constraints = [models.UniqueConstraint(fields=['store', 'client_request_id'],
                                               name='store_business_certificate_request_unique')]


class PlatformWithdrawalAuditReceipt(DirectoryModel):
    """Exactly one control-plane audit for an already committed tenant event."""
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='withdrawal_audit_receipts')
    tenant_event_id = models.PositiveBigIntegerField()
    audit = models.OneToOneField(PlatformAuditEvent, on_delete=models.PROTECT, related_name='withdrawal_receipt')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store', 'tenant_event_id'], name='platform_withdraw_audit_event_unique')]


class PlatformWithdrawalAuditScanCursor(DirectoryModel):
    store = models.OneToOneField(Store, on_delete=models.PROTECT, related_name='withdrawal_audit_scan_cursor')
    last_event_id = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class PlatformWithdrawalAuditDispatchCursor(DirectoryModel):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_store_id = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='withdraw_audit_dispatch_singleton')]


class PlatformPassRepaymentClaim(DirectoryModel):
    """Global reservation of one external receipt, without copying tenant funds."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    external_reference_digest = models.CharField(max_length=64, unique=True, null=True)
    payload_digest = models.CharField(max_length=64)
    store = models.ForeignKey(Store, on_delete=models.PROTECT)
    actor = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT)
    tenant_receipt_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    recorded_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    void_reason = models.CharField(max_length=200, blank=True, default='')
    voided_reference_digest = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(tenant_receipt_id__isnull=True, recorded_at__isnull=True)
                       | models.Q(tenant_receipt_id__isnull=False, recorded_at__isnull=False)),
            name='pass_repay_claim_result_pair'),
            models.CheckConstraint(condition=(
                models.Q(voided_at__isnull=True, voided_by__isnull=True, void_reason='',
                         voided_reference_digest='', external_reference_digest__isnull=False)
                | (models.Q(voided_at__isnull=False, voided_by__isnull=False, external_reference_digest__isnull=True,
                            tenant_receipt_id__isnull=True, recorded_at__isnull=True)
                   & ~models.Q(void_reason='') & ~models.Q(voided_reference_digest=''))),
                name='pass_repay_claim_void_state')]


class PlatformPassRepaymentOperation(DirectoryModel):
    """Directory-side intent for a tenant action with recoverable outcome."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    claim = models.ForeignKey(PlatformPassRepaymentClaim, on_delete=models.PROTECT)
    actor = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12)
    payload_digest = models.CharField(max_length=64)
    tenant_action_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_message = models.CharField(max_length=200, blank=True, default='')
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    void_reason = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(kind__in=['VERIFY', 'ALLOCATE']), name='pass_repay_op_kind_valid'),
            models.CheckConstraint(condition=(
                models.Q(tenant_action_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=True, rejection_message='')
                | models.Q(tenant_action_id__isnull=False, completed_at__isnull=False, rejected_at__isnull=True, rejection_message='')
                | (models.Q(tenant_action_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=False)
                   & ~models.Q(rejection_message=''))), name='pass_repay_op_result_pair'),
            models.CheckConstraint(condition=(
                models.Q(voided_at__isnull=True, voided_by__isnull=True, void_reason='')
                | (models.Q(voided_at__isnull=False, voided_by__isnull=False, tenant_action_id__isnull=True,
                            completed_at__isnull=True, rejected_at__isnull=True, rejection_message='')
                   & ~models.Q(void_reason=''))), name='pass_repay_op_void_state'),
        ]


class PlatformPointsFundingOperation(DirectoryModel):
    """Platform intent whose tenant allocation may need retry/reconciliation."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    claim = models.ForeignKey(PlatformPassRepaymentClaim, on_delete=models.PROTECT)
    actor = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT)
    payload_digest = models.CharField(max_length=64)
    tenant_action_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_message = models.CharField(max_length=200, blank=True, default='')
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    void_reason = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(tenant_action_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=True, rejection_message='')
            | models.Q(tenant_action_id__isnull=False, completed_at__isnull=False, rejected_at__isnull=True, rejection_message='')
            | (models.Q(tenant_action_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=False)
               & ~models.Q(rejection_message=''))), name='points_funding_op_result_pair'),
            models.CheckConstraint(condition=(
                models.Q(voided_at__isnull=True, voided_by__isnull=True, void_reason='')
                | (models.Q(voided_at__isnull=False, voided_by__isnull=False, tenant_action_id__isnull=True,
                            completed_at__isnull=True, rejected_at__isnull=True, rejection_message='')
                   & ~models.Q(void_reason=''))), name='points_funding_op_void_state')]


class PlatformOtpCredential(DirectoryModel):
    account = models.OneToOneField(PlatformAccount, on_delete=models.PROTECT, related_name='otp_credential')
    secret_ciphertext = models.TextField()
    version = models.UUIDField(default=uuid.uuid4, editable=False)
    last_counter = models.BigIntegerField(default=-1)
    recovery_hashes = models.JSONField(default=list)
    enabled_at = models.DateTimeField(auto_now_add=True)


class PlatformLoginChallenge(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='login_challenges')
    method = models.CharField(max_length=8, choices=[('SMS', '短信'), ('OTP', '验证器')])
    security_version = models.UUIDField()
    otp_version = models.UUIDField(null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True, default='')
    code_hash = models.CharField(max_length=128, blank=True, default='')
    expires_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(method__in=['SMS', 'OTP']), name='platform_challenge_method_valid')]


class PlatformOtpEnrollment(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='otp_enrollments')
    session_id = models.UUIDField()
    security_version = models.UUIDField()
    secret_ciphertext = models.TextField()
    expires_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class AdminLoginHandoff(DirectoryModel):
    """Control-plane WeChat handoff that resolves a console account's tenant safely."""
    ticket = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poll_token_hash = models.CharField(max_length=64)
    bridge_token_hash = models.CharField(max_length=64, blank=True)
    purpose = models.CharField(max_length=20, default='login')
    store_slug = models.SlugField(max_length=48, blank=True, db_index=True)
    expected_account_id = models.PositiveBigIntegerField(null=True, blank=True)
    confirmed_account_id = models.PositiveBigIntegerField(null=True, blank=True)
    platform_account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    wechat_openid = models.CharField(max_length=100, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = '管理后台微信登录会话'


class PlatformWechatIdentity(DirectoryModel):
    """WeChat binding belongs to an independent platform account."""
    account = models.OneToOneField(PlatformAccount, on_delete=models.PROTECT, related_name='wechat_identity')
    appid = models.CharField(max_length=64)
    openid = models.CharField(max_length=100)
    version = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['appid', 'openid'], name='platform_wechat_identity_unique')]


class PlatformWechatBindingIntent(DirectoryModel):
    handoff = models.OneToOneField(AdminLoginHandoff, on_delete=models.PROTECT, related_name='platform_binding')
    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    security_version = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)


class StoreLicenseCode(DirectoryModel):
    """Control-plane authorization; the secret itself is never persisted."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='license_codes')
    contract = models.ForeignKey('StoreLicenseContract', on_delete=models.PROTECT, related_name='codes', null=True)
    reissued_from = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='replacement')
    code_digest = models.CharField(max_length=64, unique=True)
    tier = models.CharField(max_length=8, choices=[('BASIC', 'Basic'), ('PRO', 'Pro'), ('ULTRA', 'Ultra')])
    kind = models.CharField(max_length=8, choices=[('INITIAL', '首次'), ('RENEW', '续期'), ('CHANGE', '调档')])
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    backdate_reason = models.CharField(max_length=200, blank=True, default='')
    created_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    void_reason = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(tier__in=['BASIC', 'PRO', 'ULTRA']), name='license_code_tier_valid'),
            models.CheckConstraint(condition=models.Q(kind__in=['INITIAL', 'RENEW', 'CHANGE']), name='license_code_kind_valid'),
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F('starts_at')), name='license_code_dates_valid'),
            models.CheckConstraint(condition=models.Q(contract__isnull=False), name='license_code_contract_required'),
            models.CheckConstraint(condition=(models.Q(activated_at__isnull=True, activated_by_id__isnull=True)
                | models.Q(activated_at__isnull=False, activated_by_id__isnull=False)), name='license_code_activation_pair'),
            models.CheckConstraint(condition=(models.Q(voided_at__isnull=True, voided_by__isnull=True, void_reason='')
                | (models.Q(voided_at__isnull=False, voided_by__isnull=False) & ~models.Q(void_reason=''))),
                name='license_code_void_pair'),
        ]


class StoreLicenseActivationAudit(DirectoryModel):
    """Store identity is recorded without a cross-database foreign key."""
    license_code = models.ForeignKey(StoreLicenseCode, on_delete=models.PROTECT, related_name='activation_events')
    store_slug = models.SlugField(max_length=48)
    actor_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class StoreLicenseActivatorGrant(DirectoryModel):
    """Explicit platform delegation to an identified store staff account."""
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='license_activators')
    account_id = models.PositiveBigIntegerField()
    account_no = models.CharField(max_length=32)
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store', 'account_id'], name='license_activator_store_account_unique')]


class PlatformLicenseSettings(DirectoryModel):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    default_grace_days = models.PositiveIntegerField(default=7)
    updated_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, null=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='license_settings_singleton')]


class StoreLicenseSettings(DirectoryModel):
    store = models.OneToOneField(Store, on_delete=models.PROTECT, related_name='license_settings')
    grace_days_override = models.PositiveIntegerField(null=True, blank=True)
    updated_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)


class LicenseTierFeatureOverride(DirectoryModel):
    tier = models.CharField(max_length=8)
    feature = models.CharField(max_length=48)
    mode = models.CharField(max_length=12)
    updated_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tier', 'feature'], name='license_tier_feature_unique'),
            models.CheckConstraint(condition=models.Q(tier__in=['BASIC', 'PRO', 'ULTRA']), name='license_tier_feature_tier_valid'),
            models.CheckConstraint(condition=models.Q(mode__in=['OFF', 'READ_ONLY', 'FULL', 'BASIC', 'RECENT']),
                                   name='license_tier_feature_mode_valid'),
        ]


class StoreLicenseContract(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='license_contracts')
    amount_due_fen = models.PositiveBigIntegerField()
    created_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    updated_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class StoreLicenseReceipt(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    contract = models.ForeignKey(StoreLicenseContract, on_delete=models.PROTECT, related_name='receipts')
    amount_fen = models.PositiveBigIntegerField()
    received_on = models.DateField()
    note = models.CharField(max_length=200, blank=True, default='')
    created_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    void_reason = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='license_receipt_amount_positive'),
            models.CheckConstraint(condition=(models.Q(voided_at__isnull=True, voided_by__isnull=True, void_reason='')
                | (models.Q(voided_at__isnull=False, voided_by__isnull=False) & ~models.Q(void_reason=''))),
                name='license_receipt_void_state'),
        ]


class LicenseNotice(DirectoryModel):
    """Durable control-plane reminder with a single entitlement or certificate source."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_key = models.CharField(max_length=120, unique=True)
    store = models.ForeignKey(Store, on_delete=models.PROTECT)
    license_code = models.ForeignKey(StoreLicenseCode, on_delete=models.PROTECT, null=True, blank=True)
    business_certificate = models.ForeignKey(StoreBusinessCertificate, on_delete=models.PROTECT, null=True, blank=True)
    kind = models.CharField(max_length=16)
    deadline = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    platform_delivered_at = models.DateTimeField(null=True, blank=True)
    store_delivered_at = models.DateTimeField(null=True, blank=True)
    suppressed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(kind__in=['CANCEL', 'D30', 'D7', 'D1', 'EXPIRY', 'GRACE_END']),
                                   name='license_notice_kind_valid'),
            models.CheckConstraint(condition=(
                models.Q(license_code__isnull=False, business_certificate__isnull=True)
                | models.Q(license_code__isnull=True, business_certificate__isnull=False, kind__in=['D30', 'D7', 'D1'])
            ), name='license_notice_single_source'),
        ]


class PlatformLicenseNotice(DirectoryModel):
    notice = models.ForeignKey(LicenseNotice, on_delete=models.PROTECT, related_name='platform_deliveries')
    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='license_notices')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['notice', 'account'], name='license_platform_notice_unique')]


class PlatformSalaryPlan(DirectoryModel):
    """One platform employee's configured pay for one natural month, in CNY fen."""
    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='salary_plans')
    month = models.DateField()
    base_fen = models.PositiveBigIntegerField()
    performance_fen = models.PositiveBigIntegerField()
    created_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    updated_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'month'], name='platform_salary_plan_month_unique')]


class PlatformSalaryConfirmation(DirectoryModel):
    """Immutable confirmed platform pay; excluded from every store's books."""
    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='salary_confirmations')
    month = models.DateField()
    base_fen = models.PositiveBigIntegerField()
    performance_fen = models.PositiveBigIntegerField()
    adjustment_increase_fen = models.PositiveBigIntegerField(default=0)
    adjustment_deduction_fen = models.PositiveBigIntegerField(default=0)
    net_fen = models.PositiveBigIntegerField(default=0)
    confirmed_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'month'], name='platform_salary_confirm_month_unique')]


class PlatformSalaryPeriod(DirectoryModel):
    """Explicitly attested monthly population; closed periods never change."""
    month = models.DateField(unique=True)
    roster = models.JSONField(default=list)
    roster_recorded = models.BooleanField(default=False)
    closed_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)


class PlatformSalaryAdjustment(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    account = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='salary_adjustments')
    original = models.ForeignKey(PlatformSalaryConfirmation, on_delete=models.PROTECT, related_name='adjustments')
    kind = models.CharField(max_length=8, choices=[('CREDIT', '补发'), ('DEBIT', '扣减')])
    amount_fen = models.PositiveBigIntegerField()
    due_month = models.DateField(db_index=True)
    reason = models.CharField(max_length=500)
    deduction_source = models.CharField(max_length=24, blank=True, default='')
    created_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(kind__in=['CREDIT', 'DEBIT']), name='platform_salary_adjustment_kind'),
                       models.CheckConstraint(condition=(models.Q(kind='CREDIT', deduction_source='') |
                           models.Q(kind='DEBIT', deduction_source__in=['REFUND_COMMISSION', 'OTHER'])),
                           name='platform_salary_deduction_source'),
                       models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='platform_salary_adjustment_positive')]


class PlatformSalaryAdjustmentApplication(DirectoryModel):
    adjustment = models.ForeignKey(PlatformSalaryAdjustment, on_delete=models.PROTECT, related_name='applications')
    confirmation = models.ForeignKey(PlatformSalaryConfirmation, on_delete=models.PROTECT, related_name='adjustment_applications')
    amount_fen = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['adjustment', 'confirmation'], name='platform_salary_adjustment_once'),
                       models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='platform_salary_application_positive')]


class PlatformSalaryPayment(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    confirmation = models.ForeignKey(PlatformSalaryConfirmation, on_delete=models.PROTECT, related_name='payments')
    amount_fen = models.PositiveBigIntegerField()
    reference = models.CharField(max_length=200, unique=True)
    paid_on = models.DateField()
    recorded_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='platform_salary_payment_positive')]


class PlatformSalarySeparation(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    account = models.OneToOneField(PlatformAccount, on_delete=models.PROTECT, related_name='salary_separation')
    ended_on = models.DateField()
    reason = models.CharField(max_length=500)
    confirmed_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)


class PlatformSalaryAdjustmentWriteOff(DirectoryModel):
    adjustment = models.OneToOneField(PlatformSalaryAdjustment, on_delete=models.PROTECT, related_name='writeoff')
    separation = models.ForeignKey(PlatformSalarySeparation, on_delete=models.PROTECT, related_name='writeoffs')
    amount_fen = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='platform_salary_writeoff_positive')]


class PlatformSalarySupplement(DirectoryModel):
    """Post-separation salary expense; original settlement remains immutable."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    separation = models.ForeignKey(PlatformSalarySeparation, on_delete=models.PROTECT, related_name='supplements')
    original_confirmation = models.ForeignKey(PlatformSalaryConfirmation, on_delete=models.PROTECT,
                                               null=True, blank=True, related_name='separation_supplements')
    basis_reference = models.CharField(max_length=200, unique=True)
    reason = models.CharField(max_length=500)
    amount_fen = models.PositiveBigIntegerField()
    recognition_month = models.DateField(db_index=True)
    created_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='platform_salary_supplement_positive')]


class PlatformSalarySupplementPayment(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    supplement = models.ForeignKey(PlatformSalarySupplement, on_delete=models.PROTECT, related_name='payments')
    amount_fen = models.PositiveBigIntegerField()
    reference = models.CharField(max_length=200, unique=True)
    paid_on = models.DateField()
    recorded_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='platform_supplement_payment_positive')]


from .referral_funding_models import PlatformReferralFundingOperation, PlatformReferralFundingReleaseOperation  # noqa: E402,F401


class StoreDomain(DirectoryModel):
    """A hostname is permanently reserved to one store, including after revocation."""
    hostname = models.CharField(max_length=253, unique=True)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='domains')
    active = models.BooleanField(default=True)
    revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ComplianceRuleVersion(DirectoryModel):
    """Immutable published snapshot of the platform's literal text rules."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', '草稿'
        PUBLISHED = 'PUBLISHED', '已发布'

    version = models.PositiveIntegerField(unique=True, validators=[MinValueValidator(1)])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    rules = models.JSONField(default=list)
    rules_sha256 = models.CharField(max_length=64)
    created_by = models.ForeignKey(PlatformAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    published_by = models.ForeignKey(PlatformAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-version']
        verbose_name = '内容合规规则版本'
        verbose_name_plural = '内容合规规则版本'
        constraints = [models.CheckConstraint(condition=models.Q(version__gt=0), name='compliance_rule_version_positive')]

    def save(self, *args, **kwargs):
        if not self._state.adding and self.pk:
            from django.core.exceptions import ValidationError
            current = type(self).objects.only(
                'version', 'status', 'rules', 'rules_sha256', 'created_by_id',
                'published_by_id', 'created_at', 'published_at',
            ).get(pk=self.pk)
            if current.status == self.Status.PUBLISHED:
                tracked = ('version', 'status', 'rules', 'rules_sha256', 'created_by_id',
                           'published_by_id', 'created_at', 'published_at')
                if any(getattr(current, field) != getattr(self, field) for field in tracked):
                    raise ValidationError('已发布的合规规则版本不可修改')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.status == self.Status.PUBLISHED:
            raise ValidationError('已发布的合规规则版本不可删除')
        return super().delete(*args, **kwargs)


class ComplianceRuleState(DirectoryModel):
    """Singleton pointer switched while holding its row lock."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    current_version = models.ForeignKey(
        ComplianceRuleVersion, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='compliance_rule_state_singleton')]


class StoreContentComplianceReceipt(DirectoryModel):
    """Control-plane evidence for store-owned text; never stores the content."""

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='content_compliance_receipts')
    field_name = models.CharField(max_length=40)
    content_sha256 = models.CharField(max_length=64)
    rule_version = models.PositiveIntegerField()
    rule_set_sha256 = models.CharField(max_length=64)
    matched_rule_codes = models.JSONField(default=list)
    exempted_rule_codes = models.JSONField(default=list)
    actor_type = models.CharField(max_length=32)
    actor_id = models.CharField(max_length=100, blank=True, default='')
    actor_label = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['store', '-created_at'])]


class StoreGuaranteeContract(DirectoryModel):
    """Current platform-attested guarantee terms for one store, in CNY fen."""

    store = models.OneToOneField(Store, on_delete=models.PROTECT, related_name='guarantee_contract')
    source_license_contract = models.ForeignKey(
        StoreLicenseContract, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    amount_due_fen = models.PositiveBigIntegerField()
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    updated_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(version__gt=0), name='guarantee_contract_version_positive'),
        ]


class StoreGuaranteeEventQuerySet(DirectoryQuerySet):
    """Committed offline evidence is append-only, including through bulk ORM calls."""

    def update(self, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('保证金收退事件不可修改，请登记补偿事件')

    def delete(self):
        from django.core.exceptions import ValidationError
        raise ValidationError('保证金收退事件不可删除，请保留原始凭证')


class StoreGuaranteeEventManager(DirectoryManager.from_queryset(StoreGuaranteeEventQuerySet)):
    pass


class StoreGuaranteeEvent(DirectoryModel):
    """One immutable offline collection/refund fact; never a payment-provider result."""

    class Direction(models.TextChoices):
        RECEIPT = 'RECEIPT', '线下缴纳登记'
        REFUND = 'REFUND', '线下退还登记'

    objects = StoreGuaranteeEventManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='guarantee_events')
    contract = models.ForeignKey(StoreGuaranteeContract, on_delete=models.PROTECT, related_name='events')
    contract_version = models.PositiveIntegerField()
    business_key = models.CharField(max_length=80)
    client_request_id = models.CharField(max_length=64)
    request_fingerprint = models.CharField(max_length=64)
    direction = models.CharField(max_length=8, choices=Direction.choices)
    amount_fen = models.PositiveBigIntegerField()
    occurred_at = models.DateTimeField()
    evidence_sha256 = models.CharField(max_length=64)
    evidence_ref = models.CharField(max_length=200)
    compensates = models.OneToOneField(
        'self', null=True, blank=True, on_delete=models.PROTECT, related_name='compensation',
    )
    created_by = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['store', 'created_at', 'id']
        indexes = [models.Index(fields=['store', 'created_at'], name='guarantee_store_time_idx')]
        constraints = [
            models.UniqueConstraint(fields=['store', 'business_key'], name='guarantee_store_business_key_unique'),
            models.UniqueConstraint(fields=['store', 'client_request_id'], name='guarantee_store_request_unique'),
            models.CheckConstraint(condition=models.Q(direction__in=['RECEIPT', 'REFUND']),
                                   name='guarantee_event_direction_valid'),
            models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='guarantee_event_amount_positive'),
            models.CheckConstraint(condition=models.Q(contract_version__gt=0),
                                   name='guarantee_event_contract_version_positive'),
            models.CheckConstraint(condition=(
                models.Q(evidence_sha256='', evidence_ref='')
                | (~models.Q(evidence_sha256='') & ~models.Q(evidence_ref=''))
            ), name='guarantee_event_evidence_pair'),
        ]

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if not self._state.adding:
            raise ValidationError('保证金收退事件不可修改，请登记补偿事件')
        if self.contract.store_id != self.store_id or self.contract.version != self.contract_version:
            raise ValidationError('保证金事件与门店合同版本不一致')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('保证金收退事件不可删除，请保留原始凭证')


class StoreClosureImmutableQuerySet(DirectoryQuerySet):
    def update(self, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('闭店公示与到期事件不可修改，请追加后续事实')

    def delete(self):
        from django.core.exceptions import ValidationError
        raise ValidationError('闭店公示与到期事件不可删除')


class StoreClosureNoticeManager(DirectoryManager.from_queryset(StoreClosureImmutableQuerySet)):
    pass


class StoreClosureEventManager(DirectoryManager.from_queryset(StoreClosureImmutableQuerySet)):
    pass


class StoreClosureNotice(DirectoryModel):
    """Immutable SA02 application and original 30-day notice terms."""

    class ReasonCategory(models.TextChoices):
        OPERATOR_REQUEST = 'OPERATOR_REQUEST', '经营主体申请'
        LICENSE_TERMINATION = 'LICENSE_TERMINATION', '授权终止'
        PLATFORM_DIRECTED = 'PLATFORM_DIRECTED', '平台要求'
        OTHER = 'OTHER', '其他'

    objects = StoreClosureNoticeManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.OneToOneField(Store, on_delete=models.PROTECT, related_name='closure_notice')
    reason_category = models.CharField(max_length=32, choices=ReasonCategory.choices)
    reason = models.CharField(max_length=500)
    applicant = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    started_at = models.DateTimeField()
    notice_policy_version = models.CharField(max_length=32)
    notice_days = models.PositiveSmallIntegerField()
    earliest_settlement_at = models.DateTimeField()
    client_request_id = models.CharField(max_length=64)
    request_fingerprint = models.CharField(max_length=64)
    clock_source = models.CharField(max_length=64)
    clock_identity = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['store', 'client_request_id'], name='closure_store_request_unique'),
            models.CheckConstraint(condition=models.Q(notice_days__gte=30), name='closure_notice_at_least_30_days'),
            models.CheckConstraint(condition=models.Q(earliest_settlement_at__gt=models.F('started_at')),
                                   name='closure_deadline_after_start'),
            models.CheckConstraint(condition=models.Q(reason_category__in=[
                'OPERATOR_REQUEST', 'LICENSE_TERMINATION', 'PLATFORM_DIRECTED', 'OTHER',
            ]), name='closure_reason_category_valid'),
        ]

    def save(self, *args, **kwargs):
        from datetime import timedelta
        from django.core.exceptions import ValidationError
        if not self._state.adding:
            raise ValidationError('闭店公示申请不可修改或重置倒计时')
        if self.notice_days < 30 or self.earliest_settlement_at != self.started_at + timedelta(days=self.notice_days):
            raise ValidationError('闭店公示时长或最早结清时点无效')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('闭店公示申请不可删除')


class StoreClosureEvent(DirectoryModel):
    """Immutable authorized observation that the notice deadline has elapsed."""

    class Kind(models.TextChoices):
        NOTICE_EXPIRED = 'NOTICE_EXPIRED', '公示到期确认'

    objects = StoreClosureEventManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='closure_events')
    notice = models.OneToOneField(StoreClosureNotice, on_delete=models.PROTECT, related_name='expiry_event')
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.NOTICE_EXPIRED)
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey(PlatformAccount, on_delete=models.PROTECT, related_name='+')
    occurred_at = models.DateTimeField()
    client_request_id = models.CharField(max_length=64)
    request_fingerprint = models.CharField(max_length=64)
    clock_source = models.CharField(max_length=64)
    clock_identity = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['store', 'occurred_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['store', 'client_request_id'], name='closure_event_store_request_unique'),
            models.CheckConstraint(condition=models.Q(kind='NOTICE_EXPIRED'), name='closure_event_kind_valid'),
        ]

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if not self._state.adding:
            raise ValidationError('闭店到期事件不可修改')
        if self.notice.store_id != self.store_id or self.occurred_at < self.notice.earliest_settlement_at:
            raise ValidationError('闭店到期事件门店或时点无效')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('闭店到期事件不可删除')
