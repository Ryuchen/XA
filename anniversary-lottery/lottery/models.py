"""Standalone event records. Awards are fulfilled by an administrator."""
import uuid

from django.conf import settings
from django.db import models

from .catalog import ENDS_AT, STARTS_AT


class Pool(models.Model):
    slug = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    version = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ['id']
        verbose_name = '奖池'
        verbose_name_plural = '奖池'

    def __str__(self):
        return self.name


class Cell(models.Model):
    pool = models.ForeignKey(Pool, on_delete=models.PROTECT, related_name='cells')
    row = models.PositiveSmallIntegerField()
    column = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=100)
    kind = models.CharField(max_length=8, choices=[('THANKS', '感谢参与'), ('RETRY', '再来一次'), ('REWARD', '奖品')])
    probability = models.PositiveIntegerField(help_text='旧版联合权重，仅供历史兼容；现行概率见每次抽奖快照。')
    reward_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['row', 'column']
        constraints = [
            models.UniqueConstraint(fields=['pool', 'row', 'column'], name='unique_pool_dice_cell'),
            models.CheckConstraint(condition=models.Q(row__gte=1, row__lte=6, column__gte=1, column__lte=6), name='dice_points_valid'),
            models.CheckConstraint(condition=models.Q(probability__gte=1, probability__lte=1000000), name='cell_probability_valid'),
        ]

    def __str__(self):
        return f'{self.pool} {self.row}×{self.column} {self.label}'


class Room(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    starts_at = models.DateTimeField(default=STARTS_AT)
    ends_at = models.DateTimeField(default=ENDS_AT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='lottery_rooms')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '抽奖房间'
        verbose_name_plural = '抽奖房间'
        constraints = [models.CheckConstraint(condition=models.Q(ends_at__gt=models.F('starts_at')), name='room_dates_valid')]

    def __str__(self):
        return f'{self.name} ({self.code})'


class Participant(models.Model):
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name='participants')
    nickname = models.CharField(max_length=50)
    code_digest = models.CharField(max_length=64, unique=True)
    creation_key = models.CharField(max_length=96, unique=True, null=True, blank=True, editable=False)
    last_reset_key = models.CharField(max_length=64, blank=True, default='', editable=False)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        verbose_name = '参与老板'
        verbose_name_plural = '参与老板'

    def __str__(self):
        return f'{self.nickname} · {self.room.name}'


class Entry(models.Model):
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name='entries')
    pool = models.ForeignKey(Pool, on_delete=models.PROTECT)
    remaining = models.PositiveIntegerField(default=0)
    granted = models.PositiveIntegerField(default=0)
    consumed = models.PositiveIntegerField(default=0)
    total_rolls = models.PositiveIntegerField(default=0)
    ordinary_dry_streak = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['participant', 'pool'], name='unique_participant_pool_entry')]


class EntryGrant(models.Model):
    entry = models.ForeignKey(Entry, on_delete=models.PROTECT, related_name='grants')
    event_key = models.CharField(max_length=160, unique=True)
    count = models.PositiveIntegerField()
    reason = models.CharField(max_length=255)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='lottery_entry_grants')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        verbose_name = '次数发放记录'
        verbose_name_plural = '次数发放记录'


class DrawBatch(models.Model):
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name='draw_batches')
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name='draw_batches')
    pool = models.ForeignKey(Pool, on_delete=models.PROTECT, related_name='draw_batches')
    event_key = models.CharField(max_length=160, unique=True)
    request_fingerprint = models.CharField(max_length=64)
    campaign_version = models.PositiveIntegerField()
    count = models.PositiveSmallIntegerField(default=10)
    initial_entries = models.PositiveIntegerField()
    entries_remaining = models.PositiveIntegerField()
    entries_consumed = models.PositiveSmallIntegerField(default=0)
    total_rolls = models.PositiveIntegerField(default=0)
    pity_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        verbose_name = '十连抽记录'
        verbose_name_plural = '十连抽记录'
        indexes = [models.Index(fields=['participant', 'room', '-id'], name='batch_owner_room_idx')]
        constraints = [
            models.CheckConstraint(condition=models.Q(count=10, entries_consumed__lte=10), name='batch_size_valid'),
            models.CheckConstraint(condition=models.Q(initial_entries=models.F('entries_remaining') + models.F('entries_consumed')),
                                   name='batch_entries_conserved'),
        ]


class Draw(models.Model):
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name='draws')
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name='draws')
    pool = models.ForeignKey(Pool, on_delete=models.PROTECT, related_name='draws')
    cell = models.ForeignKey(Cell, on_delete=models.PROTECT)
    batch = models.ForeignKey(DrawBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='draws')
    batch_index = models.PositiveSmallIntegerField(null=True, blank=True)
    event_key = models.CharField(max_length=160, unique=True)
    request_fingerprint = models.CharField(max_length=64)
    campaign_version = models.PositiveIntegerField()
    random_ticket = models.PositiveIntegerField()
    second_random_ticket = models.PositiveIntegerField(null=True, blank=True)
    roll_number = models.PositiveIntegerField(default=0)
    result_snapshot = models.JSONField()
    probability_snapshot = models.JSONField()
    entries_remaining = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        indexes = [models.Index(fields=['participant', '-id'], name='participant_draw_idx'),
                   models.Index(fields=['participant', 'room', '-id'], name='draw_owner_room_idx')]
        constraints = [
            models.UniqueConstraint(fields=['batch', 'batch_index'], name='unique_batch_draw_index'),
            models.CheckConstraint(condition=(models.Q(batch__isnull=True, batch_index__isnull=True)
                | models.Q(batch__isnull=False, batch_index__isnull=False, batch_index__gte=1, batch_index__lte=10)),
                name='batch_draw_index_valid'),
        ]
        verbose_name = '抽奖记录'
        verbose_name_plural = '抽奖记录'


class Award(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '待发放'
        FULFILLED = 'FULFILLED', '已发放'

    draw = models.OneToOneField(Draw, on_delete=models.PROTECT, related_name='award')
    label = models.CharField(max_length=100)
    kind = models.CharField(max_length=16)
    data = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    delivery_note = models.CharField(max_length=500, blank=True, default='')
    fulfilled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='fulfilled_lottery_awards')
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        verbose_name = '奖品发放'
        verbose_name_plural = '奖品发放'


class BonusAward(models.Model):
    draw = models.ForeignKey(Draw, on_delete=models.PROTECT, related_name='bonuses')
    threshold = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, default='BALANCE')
    data = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Award.Status.choices, default=Award.Status.PENDING)
    delivery_note = models.CharField(max_length=500, blank=True, default='')
    fulfilled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='fulfilled_lottery_bonuses')
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        verbose_name = '累计保底奖品'
        verbose_name_plural = '累计保底奖品'
        constraints = [
            models.UniqueConstraint(fields=['draw', 'threshold'], name='unique_draw_bonus_threshold'),
            models.CheckConstraint(condition=models.Q(threshold__in=[30, 50]), name='bonus_threshold_valid'),
        ]
