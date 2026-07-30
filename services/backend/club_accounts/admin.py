from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import ClubAccount, LegacyAccountMap


@admin.register(ClubAccount)
class ClubAccountAdmin(ModelAdmin):
    list_display = (
        'id', 'account_no', 'username', 'nickname', 'account_type',
        'phone', 'is_active', 'can_login', 'created_at',
    )
    list_filter = ('account_type', 'is_active', 'can_login', 'can_view')
    search_fields = ('account_no', 'username', 'nickname', 'phone', 'openid')
    readonly_fields = (
        'password', 'last_login', 'created_at', 'updated_at',
    )


@admin.register(LegacyAccountMap)
class LegacyAccountMapAdmin(ModelAdmin):
    list_display = ('legacy_user_id', 'account', 'legacy_role', 'migrated_at')
    search_fields = ('legacy_user_id', 'account__username', 'account__account_no')
    readonly_fields = ('legacy_user_id', 'account', 'legacy_role', 'migrated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
