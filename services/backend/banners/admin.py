from django.contrib import admin

from .models import Banner


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'link_type', 'sort_order', 'is_active', 'created_at')
    list_filter = ('link_type', 'is_active')
    search_fields = ('title',)
    list_editable = ('sort_order', 'is_active')
