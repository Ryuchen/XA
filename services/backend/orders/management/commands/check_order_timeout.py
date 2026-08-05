"""兜底命令：扫描超时 PENDING 订单并取消。

用法：
    python manage.py check_order_timeout

可加入 cron：
    * * * * * cd /path/to/backend && python manage.py check_order_timeout
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.models import Order, OrderStatusLog
from orders.notifier import notify_order_update
from orders.services import refund_to_customer
from orders.state_machine import IllegalTransitionError, transition


class Command(BaseCommand):
    help = '扫描超时未接单的 PENDING 订单并自动取消退款'

    def handle(self, *args, **options):
        expired = Order.objects.filter(
            status=Order.Status.PENDING,
            auto_cancel_at__lte=timezone.now(),
        )
        total = expired.count()
        cancelled = 0

        for order in expired:
            def side_effect(o):
                refund_to_customer(o, reason='超时未接单自动退款')
                o.cancel_reason = '超时未接单自动取消'

            try:
                updated = transition(
                    order.id,
                    Order.Status.CANCELLED,
                    operator=None,
                    action=OrderStatusLog.Action.AUTO_CANCEL,
                    reason='超时未接单',
                    side_effect=side_effect,
                    update_fields=['cancel_reason', 'payment_status', 'refunded_at'],
                )
            except (Order.DoesNotExist, IllegalTransitionError):
                continue

            notify_order_update(updated)
            cancelled += 1

        self.stdout.write(
            self.style.SUCCESS(f'扫描超时订单完成：共 {total} 笔，已取消 {cancelled} 笔')
        )
