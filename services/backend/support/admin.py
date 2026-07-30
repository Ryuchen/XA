from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import SupportContactCard


@admin.register(SupportContactCard)
class SupportContactCardAdmin(ModelAdmin):
    list_display = ('id', 'name', 'company', 'wechat_id', 'is_active', 'sort_order', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'company', 'wechat_id')
    list_editable = ('is_active', 'sort_order')
