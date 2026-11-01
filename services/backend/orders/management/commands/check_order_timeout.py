"""兜底命令：扫描超时 PENDING 订单并取消。

用法：
    python manage.py check_order_timeout

可加入 cron：
    * * * * * cd /path/to/backend && python manage.py check_order_timeout
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
import logging

from orders.models import Order, OrderStatusLog
from orders.notifier import notify_order_update
from orders.services import refund_to_customer
from orders.state_machine import IllegalTransitionError, transition

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '扫描超时未接单的 PENDING 订单并自动取消退款'

    def handle(self, *args, **options):
        from orders.timeout import expired_order_candidates
        expired = expired_order_candidates()
        total = expired.count()
        cancelled = 0

        for order in expired:
            from orders.direct_payment_models import DirectPaymentIntent
            if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
                from orders.timeout import reserve_expired_direct_refund
                try:
                    updated = reserve_expired_direct_refund(order.pk)
                except Exception:
                    logger.exception('超时直付订单退款预留失败，需核对 order_id=%s', order.pk)
                    continue
                if updated is not None:
                    cancelled += 1
                    try:
                        notify_order_update(updated)
                    except Exception:
                        logger.exception('超时直付订单通知失败 order_id=%s', order.pk)
                continue
            def pre_check(o):
                # The scan is only a hint: a grab/rejection may have started a
                # new pending round before transition acquires the order lock.
                if o.status != Order.Status.PENDING:
                    raise IllegalTransitionError('订单已离开待接单池')
                if not o.auto_cancel_at or o.auto_cancel_at > timezone.now():
                    raise IllegalTransitionError('当前接单期限尚未到达')

            def side_effect(o):
                refund = refund_to_customer(o, reason='超时未接单自动退款')
                o.cancel_reason = '超时未接单自动取消'
                from orders.timeout import record_timeout_notice
                record_timeout_notice(o, refunded_to_wallet=bool(refund and refund.amount))

            try:
                updated = transition(
                    order.id,
                    Order.Status.CANCELLED,
                    operator=None,
                    action=OrderStatusLog.Action.AUTO_CANCEL,
                    reason='超时未接单',
                    pre_check=pre_check,
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
