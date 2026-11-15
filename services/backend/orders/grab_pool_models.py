"""Immutable initial configuration for the new automatic grab pool."""
from django.db import models
from django.db.models import F, Q


class OrderGrabReviewCancellation(models.Model):
    """The customer's request for staff review; it is not a timed receipt."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='grab_review_cancellation')
    customer = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=12, choices=[('PENDING', '待处理'), ('CANCELLED', '已退款取消')], default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class OrderGrabPoolOpeningIntent(models.Model):
    """Paid-order evidence for future pool activation; never an open window."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='grab_opening_intent')
    payment = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    paid_amount = models.PositiveIntegerField()
    state = models.CharField(max_length=12, default='PENDING', choices=[
        ('PENDING', '待确认付款提交'), ('OBSERVED', '已观察到付款提交'), ('REVIEW', '待核对')])
    commit_observed_at = models.DateTimeField(null=True, blank=True)
    clock_precision_us = models.PositiveIntegerField(null=True, blank=True)
    database_identity = models.CharField(max_length=100, blank=True, default='')
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(
                Q(state='PENDING', commit_observed_at__isnull=True, clock_precision_us__isnull=True, database_identity='', reason='')
                | (Q(state='OBSERVED', commit_observed_at__isnull=False, clock_precision_us__gte=1, reason='') & ~Q(database_identity=''))
                | (Q(state='REVIEW') & ~Q(reason=''))
            ), name='grab_opening_intent_evidence_valid'),
        ]


class OrderGrabPoolSnapshot(models.Model):
    """An order's first-round rules; this record does not activate dispatch."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='grab_pool_snapshot')
    opened_at = models.DateTimeField()
    original_total_deadline = models.DateTimeField()
    first_deadline = models.DateTimeField()
    total_deadline = models.DateTimeField()
    window_seconds = models.PositiveIntegerField()
    config_version = models.PositiveIntegerField()
    visibility_delays = models.JSONField()
    required_provider_count = models.PositiveSmallIntegerField()
    pass_snapshot_complete = models.BooleanField(default=False, editable=False)
    pass_snapshot_count = models.PositiveIntegerField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(window_seconds__gte=1) & Q(window_seconds__lte=86400), name='grab_pool_window_range'),
            models.CheckConstraint(condition=Q(config_version__gte=1), name='grab_pool_config_version'),
            models.CheckConstraint(condition=Q(required_provider_count__in=[1, 2]), name='grab_pool_team_size'),
            models.CheckConstraint(condition=Q(original_total_deadline__gte=F('opened_at')), name='grab_pool_original_deadline'),
            models.CheckConstraint(condition=Q(first_deadline__gt=F('opened_at')), name='grab_pool_first_deadline'),
            models.CheckConstraint(condition=Q(total_deadline__gte=F('first_deadline')) & Q(total_deadline__gte=F('original_total_deadline')), name='grab_pool_total_deadline'),
        ]


class OrderGrabPoolPassSnapshot(models.Model):
    """Holders at pool entry, including offline and legacy profiles."""
    pool = models.ForeignKey(OrderGrabPoolSnapshot, on_delete=models.PROTECT, related_name='entry_passes')
    profile_id_snapshot = models.PositiveBigIntegerField()
    user_id_snapshot = models.PositiveBigIntegerField()
    account_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    tier = models.CharField(max_length=10)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['pool', 'profile_id_snapshot'], name='grab_pool_one_profile'),
            models.CheckConstraint(condition=Q(tier__in=['BLACK', 'GOLD', 'SILVER', 'BRONZE']), name='grab_pool_pass_tier_valid'),
        ]


class OrderGrabRound(models.Model):
    """Prepared round facts; activation and deadline receipt are separate."""
    pool = models.ForeignKey(OrderGrabPoolSnapshot, on_delete=models.PROTECT, related_name='rounds')
    number = models.PositiveIntegerField()
    opened_at = models.DateTimeField()
    deadline = models.DateTimeField()
    total_deadline = models.DateTimeField()
    simultaneous_visibility = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['pool', 'number'], name='grab_round_one_number'),
            models.CheckConstraint(condition=Q(number__gte=1), name='grab_round_number_positive'),
            models.CheckConstraint(condition=Q(deadline__gt=F('opened_at')), name='grab_round_deadline_valid'),
            models.CheckConstraint(condition=Q(total_deadline__gte=F('deadline')), name='grab_round_total_valid'),
        ]


class OrderGrabRoundOpeningEvidence(models.Model):
    """Commit-time bounds for a round; neither bound is the commit instant."""
    round = models.OneToOneField(OrderGrabRound, on_delete=models.PROTECT, related_name='opening_evidence')
    state = models.CharField(max_length=12, default='PENDING', choices=[
        ('PENDING', '待观察提交'), ('PREPARED', '待观察激活'),
        ('OBSERVED', '已观察提交'), ('REVIEW', '待核对')])
    lower_at = models.DateTimeField()
    upper_at = models.DateTimeField(null=True, blank=True)
    clock_evidence = models.JSONField()
    activation_lower_at = models.DateTimeField(null=True, blank=True)
    activation_upper_at = models.DateTimeField(null=True, blank=True)
    activation_clock_evidence = models.JSONField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class OrderGrabCutoffWatermark(models.Model):
    """Test-only proof of the frozen OPEN receipt set, never clock authorization."""
    round = models.OneToOneField(OrderGrabRound, on_delete=models.PROTECT, related_name='cutoff_watermark')
    cutoff_at = models.DateTimeField()
    observed_at = models.DateTimeField()
    precision_us = models.PositiveIntegerField()
    clock_vendor = models.CharField(max_length=12)
    server_id = models.CharField(max_length=64)
    receipt_count = models.PositiveIntegerField()
    receipt_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderGrabTeamDraft(models.Model):
    """Team preparation, never evidence of an on-time ranking candidate."""
    round = models.ForeignKey(OrderGrabRound, on_delete=models.PROTECT, related_name='team_drafts')
    captain = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    partner = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_key = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default='INVITING', choices=[
        ('INVITING', '等待队友'), ('UNVERIFIED', '等待登记核验'), ('WITHDRAWN', '已退出'),
        ('DISQUALIFIED', '资格失效')])
    withdrawn_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT,
                                      null=True, blank=True, related_name='+')
    withdrawal_receipt = models.OneToOneField('orders.OrderGrabReceiptProbe', on_delete=models.PROTECT,
                                               null=True, blank=True, related_name='withdrawn_team')
    provisional_withdrawal_at = models.DateTimeField(null=True, blank=True)
    disqualification_reason = models.CharField(max_length=255, blank=True, default='')
    disqualification_notice_intents = models.JSONField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['round', 'captain', 'request_key'], name='grab_team_request_unique'),
            models.CheckConstraint(condition=~Q(captain=F('partner')), name='grab_team_distinct_people'),
            models.CheckConstraint(condition=Q(status__in=['INVITING', 'UNVERIFIED', 'WITHDRAWN', 'DISQUALIFIED']), name='grab_team_status_valid'),
            models.CheckConstraint(condition=(Q(status='WITHDRAWN', withdrawn_by__isnull=False, provisional_withdrawal_at__isnull=False)
                | (Q(status__in=['INVITING', 'UNVERIFIED', 'DISQUALIFIED']) & Q(withdrawn_by__isnull=True, provisional_withdrawal_at__isnull=True))),
                name='grab_team_withdrawal_actor'),
        ]


class OrderGrabTeamDraftMember(models.Model):
    """Provisional member facts; tier is not a verified submission snapshot."""
    team = models.ForeignKey(OrderGrabTeamDraft, on_delete=models.PROTECT, related_name='members')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    joined = models.BooleanField(default=False)
    provisional_pass_tier = models.CharField(max_length=10, null=True, blank=True)
    provisional_pass_expires_at = models.DateTimeField(null=True, blank=True)
    receipt_pass_evidence = models.OneToOneField('orders.OrderGrabReceiptPassEvidence', on_delete=models.PROTECT,
                                                null=True, blank=True, related_name='team_member')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['team', 'account'], name='grab_team_member_once'),
            models.CheckConstraint(condition=(Q(joined=False, provisional_pass_tier__isnull=True,
                                                provisional_pass_expires_at__isnull=True)
                | Q(joined=True, provisional_pass_tier='', provisional_pass_expires_at__isnull=True)
                | Q(joined=True, provisional_pass_tier__in=['BLACK', 'GOLD', 'SILVER', 'BRONZE'],
                    provisional_pass_expires_at__isnull=False)), name='grab_team_member_pass_valid'),
        ]


class OrderGrabRoundSeat(models.Model):
    """Only joined members occupy a seat; deleted on withdrawal."""
    round = models.ForeignKey(OrderGrabRound, on_delete=models.PROTECT, related_name='occupied_seats')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    team = models.ForeignKey(OrderGrabTeamDraft, on_delete=models.PROTECT, related_name='occupied_seats')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['round', 'account'], name='grab_round_one_team_per_user')]


class OrderGrabReceiptProbe(models.Model):
    """Provisional command plus conservative bounds around its DB commit."""
    round = models.ForeignKey(OrderGrabRound, on_delete=models.PROTECT, related_name='receipt_probes')
    actor = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    actor_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    invitee_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    actor_cycle_guarded = models.BooleanField(default=False, editable=False)
    request_key = models.CharField(max_length=64)
    kind = models.CharField(max_length=20, choices=[('SINGLE', '单陪抢单'), ('INVITE', '组队邀请'), ('CONFIRM', '组队确认'),
        ('WITHDRAW', '退出组队'), ('BOSS_CANCEL', '老板取消')])
    payload = models.JSONField()
    fingerprint = models.CharField(max_length=64)
    lower_at = models.DateTimeField()
    cutoff_at = models.DateTimeField(null=True, blank=True)
    round_phase = models.CharField(max_length=8, default='OPEN')
    upper_observed_at = models.DateTimeField(null=True, blank=True)
    precision_us = models.PositiveIntegerField()
    clock_evidence = models.JSONField(null=True, blank=True, editable=False)
    opening_prepared_observed = models.BooleanField(default=False, editable=False)
    state = models.CharField(max_length=16, default='PENDING', choices=[('PENDING', '待确认'), ('WAIT_OPENING', '等待开池证据'),
        ('BEFORE', '证明截止前'), ('AFTER', '证明截止后'), ('REVIEW', '待核对')])
    review_reason = models.CharField(max_length=48, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['round', 'actor', 'request_key'], name='grab_receipt_request_unique'),
            models.CheckConstraint(condition=Q(kind__in=['SINGLE', 'INVITE', 'CONFIRM', 'WITHDRAW', 'BOSS_CANCEL']), name='grab_receipt_kind_valid'),
            models.CheckConstraint(condition=Q(state__in=['PENDING', 'WAIT_OPENING', 'BEFORE', 'AFTER', 'REVIEW']), name='grab_receipt_state_valid'),
            models.CheckConstraint(condition=Q(precision_us__gte=1), name='grab_receipt_precision_valid'),
            models.CheckConstraint(condition=(Q(state='PENDING', upper_observed_at__isnull=True, review_reason='')
                | Q(state__in=['WAIT_OPENING', 'BEFORE', 'AFTER'], upper_observed_at__isnull=False, review_reason='')
                | (Q(state='REVIEW') & ~Q(review_reason=''))), name='grab_receipt_evidence_valid'),
        ]


class OrderGrabReceiptPassEvidence(models.Model):
    """Pass state locked with an intent, resolved only from commit bounds."""
    receipt = models.OneToOneField(OrderGrabReceiptProbe, on_delete=models.PROTECT, related_name='pass_evidence')
    profile_id_snapshot = models.PositiveBigIntegerField()
    user_id_snapshot = models.PositiveBigIntegerField()
    raw_tier = models.CharField(max_length=10, blank=True, default='')
    raw_expires_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=12, default='PENDING', choices=[('PENDING', '待确认'), ('KNOWN', '已核验'),
        ('REVIEW', '待核对'), ('LATE', '超时无需排序')])
    submitted_tier = models.CharField(max_length=10, null=True, blank=True)
    review_reason = models.CharField(max_length=48, blank=True, default='')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(raw_tier__in=['', 'BLACK', 'GOLD', 'SILVER', 'BRONZE']), name='grab_receipt_raw_tier_valid'),
            models.CheckConstraint(condition=(Q(state='KNOWN', submitted_tier__in=['', 'BLACK', 'GOLD', 'SILVER', 'BRONZE'], review_reason='')
                | Q(state__in=['PENDING', 'LATE'], submitted_tier__isnull=True, review_reason='')
                | (Q(state='REVIEW', submitted_tier__isnull=True) & ~Q(review_reason=''))), name='grab_receipt_pass_state_valid'),
        ]


class OrderGrabSingleRoundState(models.Model):
    """Operational state of one single-player round."""
    round = models.OneToOneField(OrderGrabRound, on_delete=models.PROTECT, related_name='single_state')
    status = models.CharField(max_length=12, default='OPEN', choices=[
        ('OPEN', '等待窗口结束'), ('PARKED', '留池等待'), ('ASSIGNED', '已分配'),
        ('CANCELLED', '已取消'), ('REOPENED', '已重开'), ('REVIEW', '待核对')])
    winner = models.ForeignKey(OrderGrabReceiptProbe, on_delete=models.PROTECT,
                               related_name='+', null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    ranking_snapshot = models.JSONField(default=dict, blank=True)
    notification_intents = models.JSONField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)


class OrderGrabTeamRoundState(models.Model):
    """Operational state for one double-player grab round."""
    round = models.OneToOneField(OrderGrabRound, on_delete=models.PROTECT, related_name='team_state')
    status = models.CharField(max_length=12, default='OPEN', choices=[
        ('OPEN', '等待窗口结束'), ('PARKED', '留池等待'), ('ASSIGNED', '已分配'),
        ('CANCELLED', '已取消'), ('REOPENED', '已重开'), ('REVIEW', '待核对')])
    winner = models.ForeignKey(OrderGrabTeamDraft, on_delete=models.PROTECT,
                               related_name='+', null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    ranking_snapshot = models.JSONField(default=dict, blank=True)
    notification_intents = models.JSONField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)


class OrderGrabSingleAdmission(models.Model):
    """A candidate whose eligibility was checked while submitting the receipt."""
    receipt = models.OneToOneField(OrderGrabReceiptProbe, on_delete=models.PROTECT, related_name='single_admission')
    round = models.ForeignKey(OrderGrabRound, on_delete=models.PROTECT, related_name='single_admissions')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    phase = models.CharField(max_length=8, choices=[('OPEN', '窗口内'), ('PARKED', '留池后')])
    attempt = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='ACTIVE', choices=[('ACTIVE', '等待分配'),
                                                                        ('DISQUALIFIED', '资格失效')])
    disqualification_reason = models.CharField(max_length=255, blank=True, default='')
    disqualification_notice_intents = models.JSONField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['round', 'account', 'phase', 'attempt'],
                                               name='grab_single_phase_attempt_unique')]
