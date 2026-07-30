from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from unfold.admin import ModelAdmin

from .models import Achievement, CheckinRecord, CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin, ModelAdmin):
    list_display = ('username', 'email', 'role', 'phone', 'is_staff')
    list_filter = ('role', 'is_staff', 'is_superuser', 'is_active')
    search_fields = ('username', 'email', 'phone', 'openid')
    fieldsets = UserAdmin.fieldsets + (
        (
            'Additional info',
            {
                'fields': ('role', 'openid', 'phone', 'avatar_url'),
            },
        ),
    )


@admin.register(CheckinRecord)
class CheckinRecordAdmin(ModelAdmin):
    list_display = ('user', 'checkin_date', 'seq_in_month', 'reward_amount', 'created_at')
    list_filter = ('checkin_date',)
    search_fields = ('user__username',)


@admin.register(Achievement)
class AchievementAdmin(ModelAdmin):
    list_display = ('sort_order', 'code', 'title', 'metric', 'target', 'is_active')
    list_filter = ('metric', 'is_active')
    search_fields = ('code', 'title')
