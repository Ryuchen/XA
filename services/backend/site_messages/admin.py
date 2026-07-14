from django.contrib import admin

from .models import Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'recipient', 'type', 'title', 'is_read', 'created_at')
    list_filter = ('type', 'is_read')
    search_fields = ('title', 'preview', 'recipient__username')
    autocomplete_fields = ('recipient',)
    date_hierarchy = 'created_at'
