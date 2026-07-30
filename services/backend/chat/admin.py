from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import ChatMessage, ChatSession


@admin.register(ChatSession)
class ChatSessionAdmin(ModelAdmin):
    list_display = ('id', 'user', 'last_message', 'last_message_at', 'unread_user', 'unread_support')
    search_fields = ('user__username', 'user__nickname')


@admin.register(ChatMessage)
class ChatMessageAdmin(ModelAdmin):
    list_display = ('id', 'session', 'sender', 'is_from_support', 'content_type', 'is_read', 'created_at')
    list_filter = ('content_type', 'is_from_support', 'is_read')
    search_fields = ('content',)
