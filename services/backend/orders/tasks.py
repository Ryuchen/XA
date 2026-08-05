"""订单异步任务。

- auto_cancel_pending_order：下单后定时检查是否超时未接单，超时则自动取消并退款
- check_pending_timeouts：兜底任务（Celery beat 定时调用，扫描所有过期 PENDING 订单）
"""
from celery import shared_task
from django.utils import timezone

from .models import KookDispatchRecord, Order, OrderStatusLog


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def auto_cancel_pending_order(self, order_id: int) -> str:
    """超时自动取消 PENDING 订单。

    幂等：仅当订单仍处于 PENDING 状态时才执行取消。
    """
    # 退款实现已收口到领域服务层，异步任务不再需要 import 视图模块
    # （那会把 DRF/序列化器这一整条依赖拖进 worker 进程）。
    from .services import refund_to_customer
    from .state_machine import IllegalTransitionError, transition
    from .notifier import notify_order_update

    try:
        current = Order.objects.only('id', 'status', 'auto_cancel_at').get(id=order_id)
    except Order.DoesNotExist:
        return 'order_not_found'

    if current.status != Order.Status.PENDING:
        return f'skip:status={current.status}'

    # 双重保险：检查超时时间是否真的到了
    if current.auto_cancel_at and current.auto_cancel_at > timezone.now():
        # 任务被提前触发，重新调度
        delta = (current.auto_cancel_at - timezone.now()).total_seconds()
        raise self.retry(countdown=max(1, int(delta)))

    def side_effect(order):
        refund_to_customer(order, reason='超时未接单自动退款')
        order.cancel_reason = '超时未接单自动取消'

    try:
        order = transition(
            order_id,
            Order.Status.CANCELLED,
            operator=None,
            action=OrderStatusLog.Action.AUTO_CANCEL,
            reason='超时未接单',
            side_effect=side_effect,
            update_fields=['cancel_reason', 'payment_status', 'refunded_at'],
        )
    except (Order.DoesNotExist, IllegalTransitionError):
        return 'skip'

    notify_order_update(order)
    return 'cancelled'


@shared_task
def check_pending_timeouts() -> int:
    """扫描所有过期 PENDING 订单并触发自动取消。

    用于 Celery beat 定时兜底（例如每分钟）；也可被 management command 调用。

    Returns:
        触发的订单数量
    """
    expired_ids = list(
        Order.objects.filter(
            status=Order.Status.PENDING,
            auto_cancel_at__lte=timezone.now(),
        ).values_list('id', flat=True)
    )
    for order_id in expired_ids:
        auto_cancel_pending_order.delay(order_id)
    return len(expired_ids)


@shared_task(bind=True, max_retries=3)
def send_kook_dispatch(self, record_id: int) -> str:
    """发送 KOOK 派单记录；网络/API 错误按 1/2/4 分钟退避重试。"""
    from integrations.kook import KookClient, KookConfigurationError
    from .kook_dispatch import build_order_card

    try:
        record = KookDispatchRecord.objects.select_related('order__service').get(id=record_id)
    except KookDispatchRecord.DoesNotExist:
        return 'record_not_found'
    if record.status == KookDispatchRecord.Status.SENT:
        return f'already_sent:{record.message_id}'
    if record.order.status != Order.Status.PENDING:
        record.status = KookDispatchRecord.Status.SKIPPED
        record.last_error = f'订单状态已变更为 {record.order.status}'
        record.save(update_fields=['status', 'last_error'])
        return f'skip:status={record.order.status}'

    record.attempts += 1
    try:
        result = KookClient().send_channel_message(
            channel_id=record.channel_id,
            content=build_order_card(record.order, record.trigger),
            message_type=10,
            nonce=f'xa-order-{record.order_id}-{record.sequence}',
        )
    except KookConfigurationError as exc:
        record.status = KookDispatchRecord.Status.FAILED
        record.last_error = str(exc)[:500]
        record.save(update_fields=['status', 'attempts', 'last_error'])
        return 'failed:configuration'
    except Exception as exc:
        record.last_error = str(exc)[:500]
        if self.request.retries >= self.max_retries:
            record.status = KookDispatchRecord.Status.FAILED
            record.save(update_fields=['status', 'attempts', 'last_error'])
            return 'failed:max_retries'
        record.save(update_fields=['attempts', 'last_error'])
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

    record.status = KookDispatchRecord.Status.SENT
    record.message_id = result.message_id
    record.last_error = ''
    record.sent_at = timezone.now()
    record.save(update_fields=['status', 'message_id', 'attempts', 'last_error', 'sent_at'])
    return f'sent:{result.message_id}'
