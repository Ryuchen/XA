from django.contrib import admin

from .models import AuditionLink


@admin.register(AuditionLink)
class AuditionLinkAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'operator', 'is_active', 'expire_at', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('title', 'remark')
    readonly_fields = ('boss_token', 'provider_token', 'created_at', 'updated_at')
