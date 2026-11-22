"""订单异步任务。

- auto_cancel_pending_order：下单后定时检查是否超时未接单，超时则自动取消并退款
- check_pending_timeouts：兜底任务（Celery beat 定时调用，扫描所有过期 PENDING 订单）
"""
from celery import shared_task
from stores.db import transaction
from django.conf import settings
from django.utils import timezone

from .models import KookDispatchRecord, Order, OrderStatusLog


@shared_task
def award_continuations():
    from .continuations import award_one, enabled, record_reward_failure
    from .models import OrderContinuation, OrderContinuationReward
    import logging
    if not enabled():
        return 0
    ids = (OrderContinuation.objects.filter(status='VERIFIED', rewards__isnull=True)
        .exclude(reward_state='INELIGIBLE').order_by('pk').values_list('pk', flat=True).iterator(chunk_size=200))
    count = 0
    for pk in ids:
        try:
            count += int(award_one(pk))
        except Exception as exc:
            logging.getLogger(__name__).exception('续单奖励待核对 claim_id=%s', pk)
            record_reward_failure(pk, exc)
    from .continuation_notices import deliver
    pending = OrderContinuationReward.objects.filter(notice_completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    for pk in pending.iterator(chunk_size=200):
        try:
            deliver(pk)
        except Exception:
            logging.getLogger(__name__).exception('续单奖励通知待恢复 reward_id=%s', pk)
    return count


@shared_task
def expire_reservations() -> int:
    from .reservation_expiration import expire_due_reservations
    return expire_due_reservations()


@shared_task
def recover_room_image_uploads():
    from .room_image_recovery import recover_stale_uploads
    return recover_stale_uploads()


@shared_task
def auto_confirm_room_orders():
    from .room_auto_confirmation import process_due
    return process_due()


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def auto_cancel_pending_order(self, order_id: int) -> str:
    """超时自动取消 PENDING 订单。

    幂等：仅当订单仍处于 PENDING 状态时才执行取消。
    """
    from .grab_single_flow import applies, resolve
    if applies(order_id):
        return resolve(order_id).lower()
    from .grab_team_flow import applies as team_applies, resolve as resolve_team
    if team_applies(order_id):
        return resolve_team(order_id).lower()
    from .models import OrderGrabPoolOpeningIntent
    if OrderGrabPoolOpeningIntent.objects.filter(order_id=order_id, order__grab_pool_snapshot__isnull=True).exists():
        return 'pending_grab_opening'
    # 退款实现已收口到领域服务层，异步任务不再需要 import 视图模块
    # （那会把 DRF/序列化器这一整条依赖拖进 worker 进程）。
    from .services import refund_to_customer
    from .state_machine import IllegalTransitionError, transition
    from .settlement import SettlementError
    from .notifier import notify_order_update
    from .direct_payment_models import DirectPaymentIntent
    from .direct_payment import DirectPaymentError
    from .direct_refund import DirectRefundError
    from .timeout import reserve_expired_direct_refund

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

    if DirectPaymentIntent.objects.filter(order_id=order_id).exists():
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(current):
            return 'skip'
        try:
            order = reserve_expired_direct_refund(order_id)
        except (Order.DoesNotExist, DirectPaymentError, DirectRefundError, IllegalTransitionError):
            return 'skip'
        if order is None:
            return 'skip'
        transaction.on_commit(lambda: notify_order_update(order))
        return 'refund_reserved'

    def side_effect(order):
        from .points_service_payment import needs_integrated_processing
        points_paid = needs_integrated_processing(order)
        if points_paid:
            from .points_service_timeout import refund_expired_locked
            refund = refund_expired_locked(order)
        else:
            refund = refund_to_customer(order, reason='超时未接单自动退款')
        order.cancel_reason = '超时未接单自动取消'
        from .timeout import record_timeout_notice
        record_timeout_notice(order, refunded_to_wallet=bool(refund and refund.amount), refunded_points=points_paid)

    def pre_check(order):
        # 扫描结果可能已过时：必须在订单行锁内重新核对接单状态与本轮期限。
        if order.status != Order.Status.PENDING:
            raise IllegalTransitionError('订单已离开待接单池')
        if OrderGrabPoolOpeningIntent.objects.filter(order=order, order__grab_pool_snapshot__isnull=True).exists():
            raise IllegalTransitionError('订单等待抢单池初始化')
        from .grab_round_facts import pool_exists
        if pool_exists(order.pk):
            raise IllegalTransitionError('抢单订单须由本轮仲裁处理')
        if not order.auto_cancel_at or order.auto_cancel_at > timezone.now():
            raise IllegalTransitionError('当前接单期限尚未到达')

    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = transition(
                order_id,
                Order.Status.CANCELLED,
                operator=None,
                action=OrderStatusLog.Action.AUTO_CANCEL,
                reason='超时未接单',
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=['cancel_reason', 'payment_status', 'refunded_at'],
            )
    except (Order.DoesNotExist, IllegalTransitionError, SettlementError):
        return 'skip'

    transaction.on_commit(lambda: notify_order_update(order))
    return 'cancelled'


@shared_task
def check_pending_timeouts() -> int:
    """扫描所有过期 PENDING 订单并触发自动取消。

    用于 Celery beat 定时兜底（例如每分钟）；也可被 management command 调用。

    Returns:
        本轮到期候选数量；不代表任务已执行或退款成功。
    """
    import logging

    def enqueue_candidate(task, order_id):
        try:
            task.delay(order_id)
        except Exception:
            # A broker error can happen before or after acceptance. Keep the
            # persisted business state unchanged and retry in the next scan.
            logging.getLogger(__name__).exception(
                '订单恢复任务入队结果未确认 task=%s order_id=%s', task.name, order_id)

    from .timeout import expired_order_candidates
    if getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False):
        from .models import OrderGrabPoolOpeningIntent
        pending = OrderGrabPoolOpeningIntent.objects.filter(
            state__in=['PENDING', 'OBSERVED'], order__status=Order.Status.PENDING,
            order__grab_pool_snapshot__isnull=True).order_by('pk').values_list('order_id', flat=True)
        for order_id in pending.iterator(chunk_size=200):
            enqueue_candidate(open_committed_grab_order, order_id)
    # Activation acknowledgement and receipt classification are separate
    # commits.  A lost post-commit callback must not strand committed entries.
    from .models import OrderGrabReceiptProbe
    waiting_rounds = (OrderGrabReceiptProbe.objects.filter(
        state='WAIT_OPENING', round__opening_evidence__state='OBSERVED',
        round__pool__order__status=Order.Status.PENDING)
        .order_by().values_list('round_id', flat=True).distinct())
    for round_id in waiting_rounds.iterator(chunk_size=200):
        enqueue_candidate(recover_grab_opening_waiters, round_id)
    from .grab_single_flow import enabled
    from django.db.models import Q
    from .grab_clock_contract import production_enabled
    if production_enabled():
        from .grab_receipt_probe import _db_clock
        clock_now, _, _ = _db_clock()
    else:
        clock_now = timezone.now()
    due_rounds = (Q(status='OPEN', round__deadline__lte=clock_now)
                  | Q(status='PARKED', round__total_deadline__lte=clock_now))
    # A worker may stop after the receipt commits but before its immediate
    # resolver runs. Retry persisted cancellations even before cutoff and
    # completed post-window candidates while the order is still parked.
    committed_cancel = (
        Q(status='OPEN', round__receipt_probes__round_phase__in=['OPEN', 'PARKED'],
          round__receipt_probes__kind='BOSS_CANCEL', round__receipt_probes__state='BEFORE')
        | Q(status='PARKED', round__receipt_probes__round_phase='PARKED',
            round__receipt_probes__kind='BOSS_CANCEL', round__receipt_probes__state='BEFORE'))
    if enabled():
        from .models import OrderGrabSingleRoundState
        parked_candidate = Q(status='PARKED', round__receipt_probes__round_phase='PARKED',
            round__receipt_probes__kind='SINGLE', round__receipt_probes__state='BEFORE',
            round__receipt_probes__single_admission__status='ACTIVE')
        due = OrderGrabSingleRoundState.objects.filter(due_rounds | committed_cancel | parked_candidate,
            round__pool__order__status=Order.Status.PENDING).values_list('round__pool__order_id', flat=True).distinct()
        for order_id in due:
            enqueue_candidate(finalize_single_grab, order_id)
    from .grab_team_flow import enabled as team_enabled
    if team_enabled():
        from .models import OrderGrabTeamRoundState
        parked_candidate = (Q(status='PARKED', round__receipt_probes__round_phase='PARKED',
            round__receipt_probes__kind='CONFIRM', round__receipt_probes__state='BEFORE')
            | Q(status='PARKED', round__receipt_probes__round_phase='PARKED',
                round__receipt_probes__kind='WITHDRAW', round__receipt_probes__state='BEFORE',
                round__receipt_probes__withdrawn_team__isnull=True))
        due = OrderGrabTeamRoundState.objects.filter(due_rounds | committed_cancel | parked_candidate,
            round__pool__order__status=Order.Status.PENDING).values_list('round__pool__order_id', flat=True).distinct()
        for order_id in due:
            enqueue_candidate(finalize_team_grab, order_id)
    expired_ids = list(expired_order_candidates().values_list('id', flat=True))
    for order_id in expired_ids:
        enqueue_candidate(auto_cancel_pending_order, order_id)
    return len(expired_ids)


@shared_task
def recover_grab_opening_waiters(round_id):
    """Resume durable receipts only after the real activation was observed."""
    from .grab_round_opening import _resume_waiting
    _resume_waiting(round_id)


@shared_task
def open_committed_grab_order(order_id):
    from .grab_opening_intent import open_committed_payment
    return open_committed_payment(order_id)


@shared_task
def finalize_single_grab(order_id):
    from .grab_single_flow import resolve
    return resolve(order_id)


@shared_task
def finalize_team_grab(order_id):
    from .grab_team_flow import resolve
    return resolve(order_id)


@shared_task
def send_kook_dispatch(record_id: int) -> str:
    from .dispatching import deliver_dispatch
    return deliver_dispatch('KOOK', record_id)


@shared_task
def send_wecom_dispatch(record_id: int) -> str:
    from .dispatching import deliver_dispatch
    return deliver_dispatch('WECOM', record_id)


@shared_task
def recover_pool_group_dispatch() -> int:
    from .grab_group_recovery import recover_due
    return recover_due()
