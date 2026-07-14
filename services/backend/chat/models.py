from django.conf import settings
from django.db import models


class ChatSession(models.Model):
    """客服会话：每个非客服用户与客服团队之间唯一一个会话（共享会话池）。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_session',
        verbose_name='发起用户',
    )
    last_message = models.CharField(max_length=255, blank=True, default='', verbose_name='最新消息预览')
    last_message_at = models.DateTimeField(blank=True, null=True, db_index=True, verbose_name='最新消息时间')
    unread_user = models.PositiveIntegerField(default=0, verbose_name='用户未读数')
    unread_support = models.PositiveIntegerField(default=0, verbose_name='客服未读数')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_message_at', '-created_at']
        verbose_name = '客服会话'
        verbose_name_plural = '客服会话'

    def __str__(self):
        return f"ChatSession<{self.user_id}>"


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
    is_from_support = models.BooleanField(default=False, db_index=True, verbose_name='是否客服发出')
    content_type = models.CharField(
        max_length=10,
        choices=ContentType.choices,
        default=ContentType.TEXT,
        verbose_name='内容类型',
    )
    content = models.CharField(max_length=1000, blank=True, default='', verbose_name='文本内容')
    image = models.ImageField(upload_to='chat/', blank=True, null=True, verbose_name='图片')
    is_read = models.BooleanField(default=False, db_index=True, verbose_name='是否已读')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['session', 'created_at']),
        ]
        verbose_name = '客服消息'
        verbose_name_plural = '客服消息'

    def __str__(self):
        return f"ChatMessage<{self.session_id} {self.content_type}>"

    @property
    def preview(self):
        if self.content_type == self.ContentType.IMAGE:
            return '[图片]'
        return self.content[:255]
