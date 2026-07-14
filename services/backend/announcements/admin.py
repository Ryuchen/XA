from django.contrib import admin

from .models import Announcement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'is_pinned', 'is_active', 'sort_order', 'created_at')
    list_filter = ('is_pinned', 'is_active')
    search_fields = ('title', 'content')
    list_editable = ('is_pinned', 'is_active', 'sort_order')
