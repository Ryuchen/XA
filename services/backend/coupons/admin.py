from django.contrib import admin

from .models import Coupon, UserCoupon


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'discount_type', 'threshold', 'amount',
        'valid_to', 'total_qty', 'claimed_qty', 'is_active', 'sort_order',
    )
    list_filter = ('discount_type', 'is_active')
    search_fields = ('name',)
    list_editable = ('is_active', 'sort_order')


@admin.register(UserCoupon)
class UserCouponAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'coupon', 'status', 'claimed_at', 'used_at')
    list_filter = ('status',)
    search_fields = ('user__username', 'coupon__name')
