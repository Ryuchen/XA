from django.contrib import admin

from .models import Evaluation, KookDispatchRecord, Order, OrderStatusLog, ServiceItem


@admin.register(ServiceItem)
class ServiceItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'game_category', 'service_category', 'price', 'cover_url', 'is_active', 'sort_order']
    list_filter = ['game_category', 'service_category', 'is_active']
    search_fields = ['name']


class OrderStatusLogInline(admin.TabularInline):
    model = OrderStatusLog
    extra = 0
    readonly_fields = ['action', 'from_status', 'to_status', 'operator', 'reason', 'created_at']
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'order_no', 'status', 'payment_status',
        'customer', 'provider', 'amount', 'created_at',
    ]
    list_filter = ['status', 'payment_status', 'created_at']
    search_fields = ['order_no', 'customer__username', 'provider__username']
    readonly_fields = [
        'order_no', 'created_at', 'updated_at',
        'grabbed_at', 'in_service_at', 'completed_at',
        'cancelled_at', 'refunded_at',
    ]
    inlines = [OrderStatusLogInline]


@admin.register(OrderStatusLog)
class OrderStatusLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'action', 'from_status', 'to_status', 'operator', 'created_at']
    list_filter = ['action', 'created_at']
    search_fields = ['order__order_no']
    readonly_fields = ['order', 'action', 'from_status', 'to_status', 'operator', 'reason', 'created_at']


@admin.register(KookDispatchRecord)
class KookDispatchRecordAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'trigger', 'sequence', 'channel_id', 'status', 'attempts', 'sent_at']
    list_filter = ['trigger', 'status', 'created_at']
    search_fields = ['order__order_no', 'message_id', 'channel_id']
    readonly_fields = [
        'order', 'sequence', 'trigger', 'channel_id', 'status', 'message_id',
        'attempts', 'last_error', 'created_at', 'sent_at',
    ]


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'score', 'is_anonymous', 'created_at']
    list_filter = ['score', 'is_anonymous']
