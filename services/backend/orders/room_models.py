from django.db import models


class OrderRoom(models.Model):
    """One store-local room per service order; game-specific state is separate."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='room')
    created_at = models.DateTimeField(auto_now_add=True)


class OrderRoomObjection(models.Model):
    class Status(models.TextChoices):
        OPEN = 'OPEN', '待处理'
        RESOLVED = 'RESOLVED', '已处理'

    room = models.ForeignKey(OrderRoom, on_delete=models.PROTECT, related_name='objections')
    customer = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    reason = models.CharField(max_length=1000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    resolution = models.CharField(max_length=1000, blank=True, default='')
    resolution_request_id = models.CharField(max_length=64, blank=True, default='')
    resolution_proposal = models.ForeignKey('orders.NegotiationProposal', on_delete=models.PROTECT,
                                            null=True, blank=True, related_name='resolved_room_objections')
    confirmation = models.ForeignKey('orders.OrderRoomConfirmation', on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='objections')
    remaining_microseconds = models.PositiveBigIntegerField(null=True, blank=True)
    confirmation_ready = models.BooleanField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['room', 'request_id'], name='room_objection_request_unique'),
            models.CheckConstraint(condition=(models.Q(resolution_proposal__isnull=True) | models.Q(status='RESOLVED')),
                                   name='room_objection_proposal_resolved'),
            models.CheckConstraint(condition=models.Q(status__in=['OPEN', 'RESOLVED']), name='room_objection_status_valid'),
            models.CheckConstraint(condition=(models.Q(status='OPEN', resolved_at__isnull=True, resolved_by__isnull=True,
                resolution='', resolution_request_id='') | (models.Q(status='RESOLVED', resolved_at__isnull=False,
                resolved_by__isnull=False) & ~models.Q(resolution='') & ~models.Q(resolution_request_id=''))),
                name='room_objection_resolution_valid'),
            models.CheckConstraint(condition=(
                models.Q(confirmation__isnull=True, remaining_microseconds__isnull=True, confirmation_ready__isnull=True)
                | (models.Q(confirmation__isnull=False, remaining_microseconds__isnull=False, remaining_microseconds__gte=0)
                   & (models.Q(status='OPEN', confirmation_ready__isnull=True)
                      | models.Q(status='RESOLVED', confirmation_ready__isnull=False)))
            ), name='room_objection_wait_valid'),
        ]


class OrderRoomCompletionRequest(models.Model):
    """Evidence of a provider's request and the store's independent review."""
    room = models.ForeignKey(OrderRoom, on_delete=models.PROTECT, related_name='completion_requests')
    submitted_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    description = models.CharField(max_length=1000)
    participants = models.JSONField()
    status = models.CharField(max_length=12, choices=[('PENDING', '待核实'), ('VERIFIED', '已核实'),
                                                     ('REJECTED', '未通过')], default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=1000, blank=True, default='')
    review_request_id = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['room', 'submitted_by', 'request_id'], name='room_completion_request_unique'),
            models.CheckConstraint(condition=(
                models.Q(status='PENDING', reviewed_by__isnull=True, reviewed_at__isnull=True,
                         review_note='', review_request_id='')
                | (models.Q(status__in=['VERIFIED', 'REJECTED'], reviewed_by__isnull=False,
                            reviewed_at__isnull=False) & ~models.Q(review_note='') & ~models.Q(review_request_id=''))
            ), name='room_completion_review_valid'),
        ]


class OrderRoomConfirmation(models.Model):
    """Immutable wait configuration and a restartable, per-application clock."""
    request = models.OneToOneField(OrderRoomCompletionRequest, on_delete=models.PROTECT, related_name='confirmation')
    config_minutes = models.PositiveIntegerField()
    config_version = models.PositiveIntegerField()
    started_at = models.DateTimeField()
    deadline = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    remaining_microseconds = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=[('RUNNING', '待确认'), ('PAUSED', '异议暂停'),
        ('INVALIDATED', '条件不再满足'), ('CONFIRMED', '老板已确认'), ('AUTO', '超时自动确认')], default='RUNNING')
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidated_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT,
                                       null=True, blank=True, related_name='+')
    invalidation_reason = models.CharField(max_length=32, blank=True, default='')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='+')
    confirmation_request_id = models.CharField(max_length=64, blank=True, default='')
    review_needed_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        indexes = [models.Index(fields=['status', 'deadline', 'id'], name='room_wait_due_idx')]
        constraints = [
            models.CheckConstraint(condition=models.Q(config_minutes__gt=0, config_version__gt=0),
                                   name='room_wait_config_positive'),
            models.CheckConstraint(condition=(models.Q(review_needed_at__isnull=True, review_reason='')
                | (models.Q(review_needed_at__isnull=False) & ~models.Q(review_reason=''))),
                name='room_wait_review_valid'),
            models.CheckConstraint(condition=(
                models.Q(status='RUNNING', deadline__isnull=False, paused_at__isnull=True, remaining_microseconds__isnull=True,
                         invalidated_at__isnull=True, invalidated_by__isnull=True, invalidation_reason='',
                         confirmed_at__isnull=True, confirmed_by__isnull=True, confirmation_request_id='')
                | models.Q(status='PAUSED', deadline__isnull=True, paused_at__isnull=False,
                           remaining_microseconds__isnull=False, remaining_microseconds__gte=0,
                           invalidated_at__isnull=True, invalidated_by__isnull=True, invalidation_reason='',
                           confirmed_at__isnull=True, confirmed_by__isnull=True, confirmation_request_id='')
                | models.Q(status='INVALIDATED', deadline__isnull=True, paused_at__isnull=True, remaining_microseconds__isnull=True,
                           invalidated_at__isnull=False, invalidated_by__isnull=False,
                           invalidation_reason__in=['TRANSFER', 'CONDITION_CHANGED', 'NEGOTIATED_TERMINATION'],
                           confirmed_at__isnull=True, confirmed_by__isnull=True, confirmation_request_id='')
                | (models.Q(status='CONFIRMED', deadline__isnull=False, paused_at__isnull=True,
                            remaining_microseconds__isnull=True, invalidated_at__isnull=True,
                            invalidated_by__isnull=True, invalidation_reason='', confirmed_at__isnull=False,
                            confirmed_by__isnull=False) & ~models.Q(confirmation_request_id=''))
                | (models.Q(status='AUTO', deadline__isnull=False, paused_at__isnull=True,
                            remaining_microseconds__isnull=True, invalidated_at__isnull=True,
                            invalidated_by__isnull=True, invalidation_reason='', confirmed_at__isnull=False,
                            confirmed_by__isnull=True) & ~models.Q(confirmation_request_id=''))
            ), name='room_wait_state_valid'),
        ]


class OrderRoomMessage(models.Model):
    room = models.ForeignKey(OrderRoom, on_delete=models.PROTECT, related_name='messages')
    author = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    author_role = models.CharField(max_length=12, choices=[('BOSS', '老板'), ('PROVIDER', '陪玩'), ('STAFF', '后台')])
    content = models.CharField(max_length=1000)
    request_id = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['room', 'author', 'request_id'], name='room_message_request_unique'),
            models.CheckConstraint(condition=models.Q(author_role__in=['BOSS', 'PROVIDER', 'STAFF']),
                                   name='room_message_role_valid'),
            models.CheckConstraint(condition=~models.Q(content='') & ~models.Q(request_id=''),
                                   name='room_message_content_valid'),
        ]


class OrderRoomMessageImage(models.Model):
    message = models.OneToOneField(OrderRoomMessage, on_delete=models.PROTECT, related_name='image_attachment')
    image = models.ImageField(upload_to='order_rooms/messages/')
    content_sha256 = models.CharField(max_length=64)


class OrderRoomImageUploadIntent(models.Model):
    """Committed before file I/O so interrupted uploads remain recoverable."""
    path = models.CharField(max_length=160, unique=True)
    content_sha256 = models.CharField(max_length=64)
    author = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    room = models.ForeignKey(OrderRoom, on_delete=models.PROTECT, null=True, blank=True, related_name='upload_intents')
    request_id = models.CharField(max_length=64, blank=True, default='')
    status = models.CharField(max_length=12, choices=[('PENDING', '待完成'), ('ATTACHED', '已关联'),
        ('CLEANED', '已清理'), ('REVIEW', '待核对')], default='PENDING')
    attachment = models.OneToOneField(OrderRoomMessageImage, on_delete=models.PROTECT,
                                      null=True, blank=True, related_name='upload_intent')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(models.Q(status='PENDING', attachment__isnull=True, resolved_at__isnull=True)
                | models.Q(status='ATTACHED', attachment__isnull=False, resolved_at__isnull=False)
                | models.Q(status__in=['CLEANED', 'REVIEW'], attachment__isnull=True, resolved_at__isnull=False)),
                name='room_upload_intent_state_valid'),
        ]
