"""兜底命令：扫描超时 PENDING 订单并取消。

用法：
    python manage.py check_order_timeout

可加入 cron：
    * * * * * cd /path/to/backend && python manage.py check_order_timeout
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from stores.db import transaction
from django.utils import timezone
import logging

from orders.models import Order, OrderStatusLog
from orders.notifier import notify_order_update
from orders.services import refund_to_customer
from orders.state_machine import IllegalTransitionError, transition
from orders.settlement import SettlementError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '扫描超时未接单的 PENDING 订单并自动取消退款'

    def handle(self, *args, **options):
        from orders.grab_round_facts import pool_exists
        from orders.models import OrderGrabPoolOpeningIntent
        from orders.timeout import expired_order_candidates
        expired = expired_order_candidates()
        total = expired.count()
        cancelled = 0

        for order in expired:
            # Respect committed grab/cancel receipts before considering the
            # legacy timeout refund, just like the Celery timeout worker.
            from orders.grab_single_flow import applies as single_applies, resolve as resolve_single
            from orders.grab_team_flow import applies as team_applies, resolve as resolve_team
            if single_applies(order.pk):
                cancelled += int(resolve_single(order.pk) == 'CANCELLED')
                continue
            if team_applies(order.pk):
                cancelled += int(resolve_team(order.pk) == 'CANCELLED')
                continue
            from orders.direct_payment_models import DirectPaymentIntent
            if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
                from orders.points_service_payment import needs_integrated_processing
                if needs_integrated_processing(order):
                    logger.error('超时订单同时存在直付与积分支付证据，需人工核对 order_id=%s', order.pk)
                    continue
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
                if pool_exists(o.pk) or OrderGrabPoolOpeningIntent.objects.filter(order=o).exists():
                    raise IllegalTransitionError('订单须由当前抢单轮次处理')
                if not o.auto_cancel_at or o.auto_cancel_at > timezone.now():
                    raise IllegalTransitionError('当前接单期限尚未到达')

            def side_effect(o):
                from orders.points_service_payment import needs_integrated_processing
                points_paid = needs_integrated_processing(o)
                if points_paid:
                    from orders.points_service_timeout import refund_expired_locked
                    refund = refund_expired_locked(o)
                else:
                    refund = refund_to_customer(o, reason='超时未接单自动退款')
                o.cancel_reason = '超时未接单自动取消'
                from orders.timeout import record_timeout_notice
                record_timeout_notice(o, refunded_to_wallet=bool(refund and refund.amount), refunded_points=points_paid)

            try:
                with transaction.atomic():
                    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                        from console.payroll_event_barrier import lock_payment_refund_events
                        lock_payment_refund_events()
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
            except (Order.DoesNotExist, IllegalTransitionError, SettlementError):
                continue

            notify_order_update(updated)
            cancelled += 1

        self.stdout.write(
            self.style.SUCCESS(f'扫描超时订单完成：共 {total} 笔，已取消 {cancelled} 笔')
        )
