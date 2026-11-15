from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Coupon, UserCoupon


@admin.register(Coupon)
class CouponAdmin(ModelAdmin):
    list_display = (
        'id', 'name', 'discount_type', 'threshold', 'amount',
        'valid_to', 'total_qty', 'claimed_qty', 'is_active', 'sort_order',
    )
    list_filter = ('discount_type', 'is_active')
    search_fields = ('name',)
    list_editable = ('is_active', 'sort_order')


@admin.register(UserCoupon)
class UserCouponAdmin(ModelAdmin):
    list_display = ('id', 'user', 'coupon', 'status', 'claimed_at', 'used_at')
    list_filter = ('status',)
    search_fields = ('user__username', 'coupon__name')
    # Ownership, cycle, stock and redemption state are business facts.  The
    # generic model form and bulk-delete action cannot establish the required
    # lifecycle locks, funding evidence or audit trail.
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
