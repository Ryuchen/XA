from django.conf import settings
from django.db import models


class ChatSession(models.Model):
    """客服会话：每个非客服用户与客服团队之间唯一一个会话（共享会话池）。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_session',
        null=True,
        blank=True,
        verbose_name='发起用户',
    )
    account = models.OneToOneField(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chat_session',
        verbose_name='发起业务账户',
    )
    claimed_by = models.ForeignKey(
        'club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True,
        related_name='claimed_chat_sessions', verbose_name='接待客服',
    )
    claim_version = models.PositiveBigIntegerField(default=0, verbose_name='接待版本')
    claimed_at = models.DateTimeField(null=True, blank=True, verbose_name='接待时间')
    last_message = models.CharField(max_length=255, blank=True, default='', verbose_name='最新消息预览')
    last_message_at = models.DateTimeField(blank=True, null=True, db_index=True, verbose_name='最新消息时间')
    unread_user = models.PositiveIntegerField(default=0, verbose_name='用户未读数')
    unread_support = models.PositiveIntegerField(default=0, verbose_name='客服未读数')
    refresh_notification_intents = models.JSONField(default=list, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_message_at', '-created_at']
        verbose_name = '客服会话'
        verbose_name_plural = '客服会话'
        indexes = [
            models.Index(
                fields=['account', '-last_message_at'],
                name='chat_session_account_idx',
            ),
        ]

    def __str__(self):
        return f"ChatSession<{self.user_id}>"


class ChatAssignmentEvent(models.Model):
    session = models.ForeignKey(ChatSession, on_delete=models.PROTECT, related_name='assignment_events')
    actor = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    previous = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, related_name='+')
    current = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, related_name='+')
    action = models.CharField(max_length=16)
    version = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=500, blank=True, default='')
    acting_as_supervisor = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    transfer_notice_pending = models.BooleanField(default=False, editable=False)
    transfer_notice_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    transfer_notice_message = models.OneToOneField('site_messages.Message', on_delete=models.PROTECT,
                                                   null=True, blank=True, related_name='+', editable=False)
    transfer_notice_completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    transfer_notice_error = models.CharField(max_length=255, blank=True, default='', editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['session', 'version'], name='chat_assignment_version_uniq')]


class ChatMessage(models.Model):
    """客服会话内的单条消息。"""

    class ContentType(models.TextChoices):
        TEXT = 'TEXT', '文本'
        IMAGE = 'IMAGE', '图片'

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name='所属会话',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_chat_messages',
        verbose_name='发送者',
    )
    sender_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_chat_messages',
        verbose_name='发送业务账户',
    )
    related_order = models.ForeignKey(
        'orders.Order', on_delete=models.PROTECT, null=True, blank=True,
        related_name='consultation_messages', verbose_name='关联订单',
    )
    order_snapshot = models.JSONField(default=dict, blank=True, verbose_name='咨询订单快照')
    is_from_support = models.BooleanField(default=False, db_index=True, verbose_name='是否客服发出')
    content_type = models.CharField(
        max_length=10,
        choices=ContentType.choices,
        default=ContentType.TEXT,
        verbose_name='内容类型',
    )
    content = models.CharField(max_length=1000, blank=True, default='', verbose_name='文本内容')
    image = models.ImageField(upload_to='chat/', blank=True, null=True, verbose_name='图片')
    client_message_id = models.CharField(max_length=64, null=True, blank=True, editable=False)
    message_fingerprint = models.CharField(max_length=64, blank=True, default='', editable=False)
    notification_intent = models.BooleanField(default=False, editable=False)
    notification_recipient_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    notification_recipient_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    is_read = models.BooleanField(default=False, db_index=True, verbose_name='是否已读')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['session', 'created_at']),
            models.Index(
                fields=['session', 'is_from_support', 'is_read'],
                name='chat_message_unread_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(fields=['session', 'sender_account', 'client_message_id'], name='chat_message_client_id_uniq'),
            models.CheckConstraint(
                condition=models.Q(content_type__in=['TEXT', 'IMAGE']),
                name='chat_message_content_type_valid',
            ),
        ]
        verbose_name = '客服消息'
        verbose_name_plural = '客服消息'

    def __str__(self):
        return f"ChatMessage<{self.session_id} {self.content_type}>"

    @property
    def preview(self):
        if self.content_type == self.ContentType.IMAGE:
            return '[图片]'
        if not self.content and self.related_order_id:
            return '[订单]'
        return self.content[:255]


class ChatNotificationOutbox(models.Model):
    """A committed message's channel-layer delivery attempts, scoped to its store."""
    message = models.OneToOneField(ChatMessage, on_delete=models.CASCADE, related_name='notification_outbox')
    recipient_account_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    recipient_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    account_skipped_reason = models.CharField(max_length=100, blank=True, default='')
    operators_sent_at = models.DateTimeField(null=True, blank=True)
    account_sent_at = models.DateTimeField(null=True, blank=True)
    session_sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['completed_at', 'next_attempt_at', 'id'], name='chat_outbox_due_idx')]


class ChatSessionNotificationOutbox(models.Model):
    """Committed reception/read-state changes awaiting a refresh notification."""
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='state_notifications')
    intent_key = models.CharField(max_length=32, null=True, blank=True, unique=True, editable=False)
    recipient_account_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    recipient_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    account_skipped_reason = models.CharField(max_length=100, blank=True, default='')
    operators_sent_at = models.DateTimeField(null=True, blank=True)
    account_sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['completed_at', 'next_attempt_at', 'id'], name='chat_state_outbox_due_idx')]
