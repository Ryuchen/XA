"""Operating payroll is a costing ledger, independent of permissions and wallets."""
from django.db import models


class PayrollConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    night_start = models.TimeField(null=True, blank=True)
    night_end = models.TimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='payroll_config_singleton')]


class PayrollLock(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)


class PayrollEventBarrier(models.Model):
    """Preseeded per-store row serializing payroll with payment/refund events."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='payroll_event_barrier_singleton')]


class SalaryPlan(models.Model):
    position = models.ForeignKey('console.BusinessDictionaryItem', on_delete=models.PROTECT,
                                 limit_choices_to={'type': 'SALARY_POSITION'}, related_name='salary_plans')
    name = models.CharField(max_length=100)
    base_fen = models.PositiveBigIntegerField(default=0)
    performance_fen = models.PositiveBigIntegerField(default=0)
    target_fen = models.PositiveBigIntegerField(default=0)
    target_scope = models.CharField(max_length=10, default='PERSON', choices=[('PERSON', '个人业绩'), ('STORE', '门店业绩')])
    dispatch_day_bps = models.PositiveSmallIntegerField(default=0)
    dispatch_night_bps = models.PositiveSmallIntegerField(default=0)
    recharge_bps = models.PositiveSmallIntegerField(default=0)
    aftersale_fee_fen = models.PositiveBigIntegerField(default=0)
    exam_fee_fen = models.PositiveBigIntegerField(default=0)
    exam_commission_bps = models.PositiveSmallIntegerField(default=0)
    remark = models.CharField(max_length=1000, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT,
                                   related_name='+', null=True, blank=True)
    created_by_platform_id = models.UUIDField(null=True, blank=True)
    created_by_system = models.BooleanField(default=True)
    platform_request_id = models.UUIDField(null=True, blank=True, unique=True)
    platform_request_hash = models.CharField(max_length=64, blank=True, default='')
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position__sort_order', 'id']
        constraints = [
            models.CheckConstraint(condition=(
                models.Q(created_by__isnull=False, created_by_platform_id__isnull=True, created_by_system=False,
                         platform_request_id__isnull=True, platform_request_hash='')
                | models.Q(created_by__isnull=True, created_by_platform_id__isnull=False, created_by_system=False,
                           platform_request_id__isnull=False) & ~models.Q(platform_request_hash='')
                | models.Q(created_by__isnull=True, created_by_platform_id__isnull=True, created_by_system=True,
                           platform_request_id__isnull=True, platform_request_hash='')),
                name='salary_plan_creator_one_source'),
            models.CheckConstraint(condition=models.Q(target_scope__in=['PERSON', 'STORE']), name='salary_target_scope_valid'),
            models.CheckConstraint(condition=models.Q(dispatch_day_bps__lte=10000, dispatch_night_bps__lte=10000, recharge_bps__lte=10000, exam_commission_bps__lte=10000), name='salary_rates_valid'),
        ]


class SalaryAssignment(models.Model):
    month = models.DateField(db_index=True)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='salary_assignments')
    plan = models.ForeignKey(SalaryPlan, on_delete=models.PROTECT, related_name='assignments')
    plan_snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    created_by_platform_id = models.UUIDField(null=True, blank=True)
    platform_request_id = models.UUIDField(null=True, blank=True, unique=True)
    platform_request_hash = models.CharField(max_length=64, blank=True, default='')
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-month', 'account_id']
        constraints = [models.UniqueConstraint(fields=['month', 'account'], name='salary_one_plan_per_person_month'),
            models.CheckConstraint(condition=(
                models.Q(created_by__isnull=False, created_by_platform_id__isnull=True,
                         platform_request_id__isnull=True, platform_request_hash='')
                | models.Q(created_by__isnull=True, created_by_platform_id__isnull=False,
                           platform_request_id__isnull=False) & ~models.Q(platform_request_hash='')),
                name='salary_assignment_creator_one_source')]


class SalaryActivity(models.Model):
    class Kind(models.TextChoices):
        DISPATCH = 'DISPATCH', '派单归属'
        RECHARGE = 'RECHARGE', '充值归属'
        AFTERSALE = 'AFTERSALE', '售后完成核实'
        EXAM = 'EXAM', '考试完成核实'

    kind = models.CharField(max_length=12, choices=Kind.choices, db_index=True)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='salary_activities')
    source_key = models.CharField(max_length=180, unique=True)
    client_request_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default='')
    is_void = models.BooleanField(default=False)
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, null=True, blank=True, related_name='salary_activities')
    recharge = models.ForeignKey('wallet.RechargeRecord', on_delete=models.PROTECT, null=True, blank=True, related_name='salary_activities')
    aftersale = models.ForeignKey('orders.AfterSale', on_delete=models.PROTECT, null=True, blank=True, related_name='salary_activities')
    source_snapshot = models.JSONField(default=dict)
    performed_at = models.DateTimeField(db_index=True)
    recognition_month = models.DateField(null=True, blank=True, db_index=True)
    reason = models.CharField(max_length=1000)
    created_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-performed_at', '-id']
        constraints = [models.CheckConstraint(condition=models.Q(kind__in=['DISPATCH', 'RECHARGE', 'AFTERSALE', 'EXAM']), name='salary_activity_kind_valid')]


class PayrollRun(models.Model):
    month = models.DateField(unique=True)
    fingerprint = models.CharField(max_length=64)
    snapshot = models.JSONField(default=dict)
    confirmed_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    confirmed_by_platform_id = models.UUIDField(null=True, blank=True)
    platform_request_id = models.UUIDField(null=True, blank=True, unique=True)
    platform_request_hash = models.CharField(max_length=64, blank=True, default='')
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-month']
        constraints = [models.CheckConstraint(condition=(
            models.Q(confirmed_by__isnull=False, confirmed_by_platform_id__isnull=True,
                     platform_request_id__isnull=True, platform_request_hash='')
            | models.Q(confirmed_by__isnull=True, confirmed_by_platform_id__isnull=False,
                       platform_request_id__isnull=False) & ~models.Q(platform_request_hash='')),
            name='payroll_run_confirmer_one_source')]


class PayrollRefundDeduction(models.Model):
    """A confirmed dispatch commission reversed by one subsequent refund."""
    refund = models.ForeignKey('orders.OrderRefund', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    addon_refund = models.ForeignKey('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    activity = models.ForeignKey(SalaryActivity, on_delete=models.PROTECT, related_name='+')
    original_run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    bucket = models.CharField(max_length=8, choices=[('DAY', '日班'), ('NIGHT', '夜班')])
    rate_bps = models.PositiveSmallIntegerField()
    refund_fen = models.PositiveBigIntegerField()
    amount_fen = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['refund', 'activity'], name='payroll_refund_activity_unique'),
            models.UniqueConstraint(fields=['addon_refund', 'activity'], name='payroll_addon_refund_activity_unique'),
            models.CheckConstraint(condition=(models.Q(refund__isnull=False, addon_refund__isnull=True)
                | models.Q(refund__isnull=True, addon_refund__isnull=False)), name='payroll_refund_one_source')]


class PayrollRefundApplication(models.Model):
    deduction = models.ForeignKey(PayrollRefundDeduction, on_delete=models.PROTECT, related_name='applications')
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='refund_applications')
    amount_fen = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['deduction', 'run'], name='payroll_refund_application_unique'),
            models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='payroll_refund_application_positive'),
        ]


class PayrollRefundPerformanceAdjustment(models.Model):
    """A refund's incremental effect on a previously confirmed period's performance."""
    refund = models.ForeignKey('orders.OrderRefund', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    addon_refund = models.ForeignKey('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    original_run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    before_fen = models.PositiveBigIntegerField()
    after_fen = models.PositiveBigIntegerField()
    amount_fen = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['refund', 'original_run', 'account'], name='payroll_refund_performance_unique'),
            models.UniqueConstraint(fields=['addon_refund', 'original_run', 'account'], name='payroll_addon_performance_unique'),
            models.CheckConstraint(condition=(models.Q(refund__isnull=False, addon_refund__isnull=True)
                | models.Q(refund__isnull=True, addon_refund__isnull=False)), name='payroll_perf_one_source')]


class PayrollRefundPerformanceApplication(models.Model):
    adjustment = models.ForeignKey(PayrollRefundPerformanceAdjustment, on_delete=models.PROTECT,
                                   related_name='applications')
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='performance_refund_applications')
    amount_fen = models.PositiveBigIntegerField()
    waived_fen = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['adjustment', 'run'], name='payroll_performance_application_unique'),
        ]


class PayrollRefundPerformanceRevision(models.Model):
    """An append-only correction to a previously recorded refund effect."""
    adjustment = models.ForeignKey(PayrollRefundPerformanceAdjustment, on_delete=models.PROTECT, related_name='revisions')
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='performance_refund_revisions')
    delta_fen = models.BigIntegerField()
    corrected_amount_fen = models.PositiveBigIntegerField()
    applied_fen = models.BigIntegerField(default=0)
    waived_fen = models.PositiveBigIntegerField(default=0)
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['adjustment', 'run'], name='payroll_perf_revision_unique'),
            models.CheckConstraint(condition=~models.Q(delta_fen=0), name='payroll_perf_revision_nonzero'),
        ]


class PayrollSeparationSettlement(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    final_run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='+')
    settled_fen = models.PositiveBigIntegerField()
    reference = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=500)
    confirmed_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'final_run'], name='payroll_separation_run_unique')]


class PayrollRefundWriteOff(models.Model):
    deduction = models.ForeignKey(PayrollRefundDeduction, on_delete=models.PROTECT, related_name='writeoffs')
    settlement = models.ForeignKey(PayrollSeparationSettlement, on_delete=models.PROTECT, related_name='writeoffs')
    amount_fen = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['deduction', 'settlement'], name='payroll_refund_writeoff_unique'),
            models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='payroll_refund_writeoff_positive'),
        ]


class PayrollPerformanceSeparationWaiver(models.Model):
    """Refund performance effect forgiven after a completed departure settlement."""
    adjustment = models.OneToOneField(PayrollRefundPerformanceAdjustment, on_delete=models.PROTECT,
                                      related_name='separation_waiver')
    settlement = models.ForeignKey(PayrollSeparationSettlement, on_delete=models.PROTECT,
                                   related_name='performance_waivers')
    amount_fen = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=100, default='离职工资结清后发生退款，绩效不再追扣')
    created_at = models.DateTimeField(auto_now_add=True)


class PayrollReemployment(models.Model):
    separation = models.OneToOneField(PayrollSeparationSettlement, on_delete=models.PROTECT,
                                      related_name='reemployment')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    registered_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class PayrollReemploymentPlan(models.Model):
    reemployment = models.ForeignKey(PayrollReemployment, on_delete=models.PROTECT, related_name='plans')
    plan = models.ForeignKey(SalaryPlan, on_delete=models.PROTECT, related_name='+')
    plan_revision = models.PositiveIntegerField()
    plan_snapshot = models.JSONField()
    night_config_snapshot = models.JSONField(default=dict)
    registered_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    effective_at = models.DateTimeField(auto_now_add=True)


class PayrollReemploymentRefundWaiver(models.Model):
    """Real later refund linked to a confirmed reemployment performance source."""
    plan = models.ForeignKey(PayrollReemploymentPlan, on_delete=models.PROTECT, related_name='refund_waivers')
    settlement = models.ForeignKey(PayrollSeparationSettlement, on_delete=models.PROTECT, related_name='+')
    source_run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='+')
    refund = models.ForeignKey('orders.OrderRefund', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    addon_refund = models.ForeignKey('orders.OrderAddonRefund', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    source_payment_id = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=100, default='原周期工资已结清，原周期不追扣')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['plan', 'refund'], name='payroll_reentry_refund_uniq'),
            models.UniqueConstraint(fields=['plan', 'addon_refund'], name='payroll_reentry_addon_uniq'),
            models.CheckConstraint(condition=(models.Q(refund__isnull=False, addon_refund__isnull=True)
                | models.Q(refund__isnull=True, addon_refund__isnull=False)), name='payroll_reentry_refund_source'),
        ]


class OperatingCost(models.Model):
    day = models.DateField(db_index=True)
    category = models.ForeignKey('console.BusinessDictionaryItem', on_delete=models.PROTECT,
                                 limit_choices_to={'type': 'COST_CATEGORY'}, related_name='operating_costs')
    channel = models.ForeignKey('console.BusinessDictionaryItem', on_delete=models.PROTECT, null=True, blank=True,
                                limit_choices_to={'type': 'AD_CHANNEL'}, related_name='advertising_costs')
    amount_fen = models.PositiveBigIntegerField()
    note = models.CharField(max_length=1000, blank=True, default='')
    client_request_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default='')
    is_void = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-day', '-id']
        constraints = [models.CheckConstraint(condition=models.Q(amount_fen__gte=0), name='operating_cost_nonnegative')]
