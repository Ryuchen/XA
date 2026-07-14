from django.contrib import admin

from .models import Promotion


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'title', 'scope', 'discount_rate', 'commission_rate',
        'priority', 'is_active', 'start_at', 'end_at',
    )
    list_filter = ('scope', 'is_active')
    search_fields = ('title', 'remark')
    filter_horizontal = ('items',)
