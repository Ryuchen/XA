from django.conf import settings
from django.db import models
from django.utils import timezone


class Message(models.Model):
    class Type(models.TextChoices):
        SYSTEM = 'SYSTEM', '系统通知'
        ORDER = 'ORDER', '订单消息'
        SUPPORT = 'SUPPORT', '客服消息'
        PROMOTION = 'PROMOTION', '活动通知'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='messages',
        null=True,
        blank=True,
    )
    recipient_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='messages',
    )
    recipient_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.SYSTEM, db_index=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255, blank=True, default='')
    detail = models.TextField(blank=True, default='')
    action_url = models.CharField(max_length=255, blank=True, default='')
    is_read = models.BooleanField(default=False, db_index=True)
    assignment_alert = models.BooleanField(default=False)
    related_order_id = models.PositiveIntegerField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
            models.Index(
                fields=['recipient_account', 'is_read', '-created_at'],
                name='site_msg_account_unread_idx',
            ),
            models.Index(
                fields=['recipient_account', 'type', '-created_at'],
                name='site_msg_account_type_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(type__in=['SYSTEM', 'ORDER', 'SUPPORT', 'PROMOTION']),
                name='site_message_type_valid',
            ),
        ]
        verbose_name = '站内消息'
        verbose_name_plural = '站内消息'

    def __str__(self):
        return f"Message<{self.recipient_id} {self.title}>"


class LicenseNoticeDelivery(models.Model):
    """Tenant-side idempotency record, committed together with its message."""
    notice_id = models.UUIDField()
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    message = models.OneToOneField(Message, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['notice_id', 'account'], name='license_notice_account_unique')]


class WithdrawalNoticeDelivery(models.Model):
    """One local withdrawal fact projected to a message, or explicitly skipped."""
    withdrawal = models.ForeignKey('wallet.WithdrawRequest', on_delete=models.PROTECT, related_name='+')
    event_key = models.CharField(max_length=100, unique=True)
    platform_event = models.OneToOneField('wallet.WithdrawalPlatformEvent', on_delete=models.PROTECT,
                                          null=True, blank=True, related_name='+')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class GrabInviteNoticeDelivery(models.Model):
    """One message or explicit skip for a committed grab invitation receipt."""
    receipt = models.OneToOneField('orders.OrderGrabReceiptProbe', on_delete=models.PROTECT, related_name='+')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class CheckinNoticeDelivery(models.Model):
    """One durable result for a committed full-attendance notice intent."""
    progress = models.OneToOneField('users.CheckinMonthProgress', on_delete=models.PROTECT,
                                    related_name='notice_delivery')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class AuditionNoticeDelivery(models.Model):
    signup = models.OneToOneField('audition.AuditionSignup', on_delete=models.PROTECT,
                                  related_name='notice_delivery')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class ReservationInviteNoticeDelivery(models.Model):
    reservation = models.OneToOneField('orders.Reservation', on_delete=models.PROTECT,
        null=True, blank=True, related_name='invitation_notice_delivery')
    seat_event = models.OneToOneField('orders.ReservationSeatEvent', on_delete=models.PROTECT,
        null=True, blank=True, related_name='invitation_notice_delivery')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(reservation__isnull=False, seat_event__isnull=True)
            | models.Q(reservation__isnull=True, seat_event__isnull=False)), name='reservation_notice_one_source')]


class ReservationStatusNoticeDelivery(models.Model):
    status_log = models.OneToOneField('orders.ReservationStatusLog', on_delete=models.PROTECT,
        null=True, blank=True, related_name='status_notice_delivery')
    seat_event = models.OneToOneField('orders.ReservationSeatEvent', on_delete=models.PROTECT,
        null=True, blank=True, related_name='status_notice_delivery')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(status_log__isnull=False, seat_event__isnull=True)
            | models.Q(status_log__isnull=True, seat_event__isnull=False)), name='reservation_status_one_source')]


class ReservationEventNoticeBatch(models.Model):
    status_log = models.OneToOneField('orders.ReservationStatusLog', on_delete=models.PROTECT,
                                     related_name='event_notice_batch')
    completed_at = models.DateTimeField(null=True, blank=True)


class ReservationEventNoticeDelivery(models.Model):
    status_log = models.ForeignKey('orders.ReservationStatusLog', on_delete=models.PROTECT, related_name='event_notice_deliveries')
    position = models.PositiveIntegerField()
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['status_log', 'position'], name='reservation_event_notice_once')]


class GrabResultNoticeBatch(models.Model):
    state = models.OneToOneField('orders.OrderGrabSingleRoundState', on_delete=models.PROTECT, related_name='notice_batch')
    completed_at = models.DateTimeField(null=True, blank=True)


class GrabResultNoticeDelivery(models.Model):
    state = models.ForeignKey('orders.OrderGrabSingleRoundState', on_delete=models.PROTECT, related_name='notice_deliveries')
    position = models.PositiveIntegerField()
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['state', 'position'], name='grab_result_notice_once')]


class GrabTeamResultNoticeBatch(models.Model):
    state = models.OneToOneField('orders.OrderGrabTeamRoundState', on_delete=models.PROTECT, related_name='notice_batch')
    completed_at = models.DateTimeField(null=True, blank=True)


class GrabTeamResultNoticeDelivery(models.Model):
    state = models.ForeignKey('orders.OrderGrabTeamRoundState', on_delete=models.PROTECT, related_name='notice_deliveries')
    position = models.PositiveIntegerField()
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['state', 'position'], name='grab_team_result_notice_once')]


class GrabDisqualificationNoticeBatch(models.Model):
    admission = models.OneToOneField('orders.OrderGrabSingleAdmission', on_delete=models.PROTECT,
                                     related_name='disqualification_notice_batch', null=True, blank=True)
    team = models.OneToOneField('orders.OrderGrabTeamDraft', on_delete=models.PROTECT,
                                related_name='disqualification_notice_batch', null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(admission__isnull=False, team__isnull=True) |
                       models.Q(admission__isnull=True, team__isnull=False)),
            name='grab_disqual_one_source')]


class GrabDisqualificationNoticeDelivery(models.Model):
    batch = models.ForeignKey(GrabDisqualificationNoticeBatch, on_delete=models.PROTECT, related_name='deliveries')
    position = models.PositiveIntegerField()
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch', 'position'], name='grab_disqual_notice_once')]


class OrderApplicationNotice(models.Model):
    application = models.ForeignKey('orders.OrderApplication', on_delete=models.PROTECT, related_name='notice_intents')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    kind = models.CharField(max_length=16)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    action_url = models.CharField(max_length=255, blank=True, default='')
    assignment_alert = models.BooleanField(default=False)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['application', 'account', 'kind'], name='app_notice_event_once')]


class AfterSaleClosureNotice(models.Model):
    case = models.ForeignKey('orders.AfterSale', on_delete=models.PROTECT, related_name='closure_notice_intents')
    history_index = models.PositiveIntegerField()
    history_fingerprint = models.CharField(max_length=64)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    account_type = models.CharField(max_length=12)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'history_index', 'account'],
                                               name='aftersale_closure_notice_once')]


class AfterSaleOpenNotice(models.Model):
    case = models.ForeignKey('orders.AfterSale', on_delete=models.PROTECT, related_name='open_notice_intents')
    history_index = models.PositiveIntegerField()
    history_fingerprint = models.CharField(max_length=64)
    case_opened_at = models.DateTimeField()
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    account_type = models.CharField(max_length=12)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'history_index', 'account'],
                                               name='aftersale_open_notice_once')]


class LowRatingNotice(models.Model):
    evaluation = models.OneToOneField('orders.Evaluation', on_delete=models.PROTECT, related_name='low_rating_notice')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class EvaluationNotice(models.Model):
    evaluation = models.OneToOneField('orders.Evaluation', on_delete=models.PROTECT, related_name='normal_notice')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class EvaluationReplyNotice(models.Model):
    evaluation = models.ForeignKey('orders.Evaluation', on_delete=models.PROTECT, related_name='reply_notice_intents')
    reply_version = models.PositiveBigIntegerField()
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    account_type = models.CharField(max_length=12)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['evaluation', 'reply_version', 'account_type'],
                                                name='evaluation_reply_notice_once')]


class RoomConfirmationNotice(models.Model):
    confirmation = models.ForeignKey('orders.OrderRoomConfirmation', on_delete=models.PROTECT, related_name='notice_intents')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    account_type = models.CharField(max_length=12)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['confirmation', 'account'], name='room_confirm_notice_once')]


class RoomReviewNotice(models.Model):
    request = models.ForeignKey('orders.OrderRoomCompletionRequest', on_delete=models.PROTECT, related_name='review_notice_intents')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    account_type = models.CharField(max_length=12)
    decision = models.CharField(max_length=12)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['request', 'account'], name='room_review_notice_once')]


class RoomObjectionNotice(models.Model):
    objection = models.ForeignKey('orders.OrderRoomObjection', on_delete=models.PROTECT, related_name='notice_intents')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=12)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['objection', 'account', 'kind'], name='room_objection_notice_once')]


class OrderCompletionNotice(models.Model):
    status_log = models.OneToOneField('orders.OrderStatusLog', on_delete=models.PROTECT, related_name='completion_notice')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderTimeoutNotice(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='timeout_notice_intent')
    refund = models.ForeignKey('orders.OrderRefund', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    refunded_to_wallet = models.BooleanField(default=False)
    refunded_points = models.BooleanField(default=False)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class CustomerCancellationNotice(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='customer_cancel_notice_intent')
    refund = models.ForeignKey('orders.OrderRefund', on_delete=models.PROTECT, related_name='+')
    receipt = models.ForeignKey('orders.OrderGrabReceiptProbe', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    review_audit = models.ForeignKey('console.AdminAuditLog', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    wallet_transaction = models.ForeignKey('wallet.Transaction', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    refunded_to_wallet = models.BooleanField(default=False)
    refunded_points = models.BooleanField(default=False)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(receipt__isnull=True) | models.Q(review_audit__isnull=True),
            name='customer_cancel_notice_one_source',
        )]


class OrderRefundNotice(models.Model):
    refund = models.ForeignKey('orders.OrderRefund', on_delete=models.PROTECT, related_name='notice_intents')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    wallet_transaction = models.ForeignKey('wallet.Transaction', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    amount = models.PositiveIntegerField()
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    can_open_order = models.BooleanField(default=True)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['refund', 'account'], name='order_refund_notice_once')]


class PointsAftersaleRefundNotice(models.Model):
    posting = models.OneToOneField('orders.PointsNegotiationPosting', on_delete=models.PROTECT, related_name='refund_notice')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    coin_units = models.PositiveIntegerField()
    points_hundredths = models.PositiveBigIntegerField()
    case_history_index = models.PositiveIntegerField()
    case_history_fingerprint = models.CharField(max_length=64)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AddonRefundNotice(models.Model):
    refund = models.ForeignKey('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='notice_intents')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    amount = models.PositiveIntegerField()
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['refund', 'account'], name='addon_refund_notice_once')]


class ZeroRefundClosureNotice(models.Model):
    case = models.ForeignKey('orders.AfterSale', on_delete=models.PROTECT, related_name='zero_notice_intents')
    proposal = models.ForeignKey('orders.NegotiationProposal', on_delete=models.PROTECT, related_name='+')
    source_kind = models.CharField(max_length=16)
    source_id = models.PositiveBigIntegerField()
    history_index = models.PositiveIntegerField()
    history_fingerprint = models.CharField(max_length=64)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'history_index', 'account'], name='zero_closure_notice_once')]


class AddonHandoverResultNotice(models.Model):
    handover = models.ForeignKey('orders.OrderAddonHandover', on_delete=models.PROTECT, related_name='result_notice_intents')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    role = models.CharField(max_length=8)
    result = models.CharField(max_length=12)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['handover', 'account'], name='addon_handover_notice_once')]


class ServiceTransferNotice(models.Model):
    transfer = models.ForeignKey('orders.OrderOriginalShareTransfer', on_delete=models.PROTECT, related_name='notice_intents')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    role = models.CharField(max_length=8)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField()
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['transfer', 'account'], name='service_transfer_notice_once')]


class ServiceTransferAssignmentNotice(models.Model):
    """A staff transfer submission and its store-local recipient."""

    status_log = models.ForeignKey('orders.OrderStatusLog', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    assignment = models.ForeignKey('orders.OrderProvider', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    seat_event_ids = models.JSONField(default=list)
    transferred_at = models.DateTimeField()
    role = models.CharField(max_length=8)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['status_log', 'account'], name='service_transfer_assignment_notice_once')]


class ReservationSeatTimeoutNotice(models.Model):
    event = models.OneToOneField('orders.ReservationSeatEvent', on_delete=models.PROTECT, related_name='timeout_provider_notice')
    invitation_event = models.ForeignKey('orders.ReservationSeatEvent', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ManualMessageCampaign(models.Model):
    """One administrator request and its fixed store-local audience."""
    creator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    request_fingerprint = models.CharField(max_length=64)
    selector = models.JSONField(default=dict)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255, blank=True, default='')
    detail = models.TextField(blank=True, default='')
    message_type = models.CharField(max_length=20, choices=Message.Type.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['creator', 'request_id'], name='manual_msg_creator_request_unique')]


class ManualMessageRecipient(models.Model):
    """A single durable message-creation attempt, not an external delivery receipt."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', '待创建'
        CREATED = 'CREATED', '已创建'
        SKIPPED = 'SKIPPED', '账号已变化'

    campaign = models.ForeignKey(ManualMessageCampaign, on_delete=models.PROTECT, related_name='recipients')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField()
    account_type = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    message = models.OneToOneField(Message, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=100, blank=True, default='')
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['campaign', 'account'], name='manual_msg_campaign_account_unique')]
        indexes = [models.Index(fields=['status', 'next_attempt_at', 'id'], name='manual_msg_pending_idx')]


class PrestartHandoffNotice(models.Model):
    status_log = models.ForeignKey('orders.OrderStatusLog', on_delete=models.PROTECT, related_name='+')
    handoff = models.ForeignKey('orders.OrderPrestartHandoff', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    handoff_ids = models.JSONField(default=list)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['status_log', 'account'], name='prestart_handoff_notice_once')]


class ReservationTeamDeclinedNotice(models.Model):
    event = models.OneToOneField('orders.ReservationSeatEvent', on_delete=models.PROTECT, related_name='+')
    invitation_event = models.ForeignKey('orders.ReservationSeatEvent', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    recipient_seat_number = models.PositiveSmallIntegerField()
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ReservationTeamReplacedNotice(models.Model):
    event = models.OneToOneField('orders.ReservationSeatEvent', on_delete=models.PROTECT, related_name='+')
    vacancy_event = models.ForeignKey('orders.ReservationSeatEvent', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    recipient_seat_number = models.PositiveSmallIntegerField()
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    skipped_reason = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class NegotiationProposalNotice(models.Model):
    proposal = models.ForeignKey('orders.NegotiationProposal', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    outcome = models.CharField(max_length=10)
    role = models.CharField(max_length=8)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['proposal', 'account', 'outcome'], name='neg_proposal_notice_once')]


class GiftReceiptNotice(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    payment = models.ForeignKey('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    income = models.ForeignKey('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    preview = models.CharField(max_length=255)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class StaffStartNotice(models.Model):
    status_log = models.ForeignKey('orders.OrderStatusLog', on_delete=models.PROTECT, related_name='+')
    assignment = models.ForeignKey('orders.OrderProvider', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    preview = models.CharField(max_length=255)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['status_log', 'assignment'], name='staff_start_notice_once')]


class OrderAssignmentNotice(models.Model):
    status_log = models.ForeignKey('orders.OrderStatusLog', on_delete=models.PROTECT, related_name='+')
    payment = models.ForeignKey('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    assignment = models.ForeignKey('orders.OrderProvider', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    slot = models.PositiveBigIntegerField()
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    account_type = models.CharField(max_length=12)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    legacy_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['status_log', 'slot'], name='order_assignment_notice_once')]


class AddonPaymentNotice(models.Model):
    payment = models.OneToOneField('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AddonCompletionNotice(models.Model):
    completion = models.ForeignKey('orders.OrderAddonCompletion', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['completion', 'role'], name='addon_completion_notice_role_once')]


class AddonRefundProposalNotice(models.Model):
    proposal = models.ForeignKey('orders.OrderAddonRefundProposal', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8)
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    segment_ids = models.JSONField(default=list)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['proposal', 'account'], name='addon_refund_proposal_notice_once')]


class AddonHandoverProposalNotice(models.Model):
    handover = models.ForeignKey('orders.OrderAddonHandover', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8)
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT,
                                null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['handover', 'account'], name='addon_handover_proposal_notice_once')]


class DepositEventNotice(models.Model):
    kind = models.CharField(max_length=8)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    topup = models.OneToOneField('wallet.CashDepositTopup', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    refund = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    preview = models.CharField(max_length=255)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(kind='TOPUP', topup__isnull=False, refund__isnull=True)
            | models.Q(kind='REFUND', topup__isnull=True, refund__isnull=False)), name='deposit_notice_one_source')]


class DepositApplicationNotice(models.Model):
    application = models.ForeignKey('wallet.DepositRefundApplication', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['application', 'account'], name='deposit_application_notice_once')]


class ReportApprovalNotice(models.Model):
    report = models.ForeignKey('wallet.ProviderReport', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    audited_at = models.DateTimeField()
    kind = models.CharField(max_length=10, default='APPROVED')
    preview = models.CharField(max_length=255)
    detail = models.CharField(max_length=300, blank=True, default='')
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['report', 'audited_at'], name='report_approval_notice_once')]


class RechargeReceiptNotice(models.Model):
    recharge = models.OneToOneField('wallet.RechargeRecord', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    recharge_tx = models.ForeignKey('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    gift_tx = models.ForeignKey('wallet.Transaction', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    gift_units = models.PositiveIntegerField(default=0)
    gift_pending = models.BooleanField(default=False)
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RechargeGiftDecisionNotice(models.Model):
    approval = models.OneToOneField('wallet.RechargeGiftApproval', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField(null=True, blank=True)
    decision = models.CharField(max_length=12)
    decided_at = models.DateTimeField()
    amount = models.PositiveIntegerField(default=0)
    gift_tx = models.ForeignKey('wallet.Transaction', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    status = models.CharField(max_length=10, default='PENDING')
    note = models.CharField(max_length=100, blank=True, default='')
    message = models.OneToOneField(Message, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
