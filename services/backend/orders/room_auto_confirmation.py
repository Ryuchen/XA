"""Store-scoped, idempotent room deadline processing."""
import logging

from django.conf import settings
from django.utils import timezone

from stores.db import transaction
from .models import Order, OrderRoom, OrderRoomCompletionRequest, OrderRoomConfirmation
from .room_boss_confirmation import complete_confirmation_locked, preflight_confirmation
from .settlement import SettlementError
from .state_machine import IllegalTransitionError
from .room_completion_lifecycle import automatic_confirmation

logger = logging.getLogger(__name__)


def _flag_review(wait, reason):
    from .room_objections import _notify_store_support
    first = wait.review_needed_at is None
    if first:
        wait.review_needed_at = timezone.now()
    wait.review_reason = reason[:200]
    wait.save(update_fields=['review_needed_at', 'review_reason'])
    if first:
        _notify_store_support('房间自动结单待核对', '本店有房间自动结单待核对，请在后台查看。')


def enabled():
    return (getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False)
            and getattr(settings, 'ROOM_BOSS_CONFIRMATION_ENABLED', False)
            and getattr(settings, 'ROOM_AUTO_CONFIRMATION_ENABLED', False))


@transaction.atomic
def mark_lifecycle_review(wait_id):
    """Surface a due, still-running confirmation whose original cycle cannot be verified."""
    if not enabled():
        return 'disabled'
    initial = OrderRoomConfirmation.objects.filter(pk=wait_id).values('request__room__order_id').first()
    if not initial:
        return 'missing'
    order = Order.objects.select_for_update().filter(pk=initial['request__room__order_id']).first()
    room = OrderRoom.objects.select_for_update().filter(order=order).first() if order else None
    row = (OrderRoomCompletionRequest.objects.select_for_update().filter(
        confirmation__pk=wait_id, room=room).first() if room else None)
    wait = OrderRoomConfirmation.objects.select_for_update().filter(pk=wait_id, request=row).first() if row else None
    if not wait or wait.status != 'RUNNING':
        return 'skip'
    if not wait.deadline or wait.deadline > timezone.now():
        return 'not_due'
    _flag_review(wait, '结单陪玩或原业务周期待核对，请核实后重试')
    return 'review'


@automatic_confirmation
@transaction.atomic
def process_one(wait_id):
    if not enabled():
        return 'disabled'
    initial = OrderRoomConfirmation.objects.filter(pk=wait_id).values('request__room__order_id').first()
    if not initial:
        return 'missing'
    order = Order.objects.select_for_update().get(pk=initial['request__room__order_id'])
    room = OrderRoom.objects.select_for_update().get(order=order)
    row = OrderRoomCompletionRequest.objects.select_for_update().filter(confirmation__pk=wait_id, room=room).first()
    wait = OrderRoomConfirmation.objects.select_for_update().filter(pk=wait_id, request=row).first() if row else None
    if not wait or wait.status != 'RUNNING':
        return 'skip'
    now = timezone.now()
    if wait.deadline > now:
        return 'not_due'
    participants, error = preflight_confirmation(order, room, row, wait)
    if error:
        logger.warning('Room auto confirmation needs review: order=%s wait=%s reason=%s', order.pk, wait.pk, error)
        _flag_review(wait, error)
        return 'review'
    try:
        with transaction.atomic():
            complete_confirmation_locked(order, row, wait, participants, actor=None, legacy_actor=None,
                key=f'auto:{wait.pk}', at=now, automatic=True)
    except (IllegalTransitionError, SettlementError):
        logger.exception('Room auto confirmation failed validation: order=%s wait=%s', order.pk, wait.pk)
        wait.refresh_from_db()
        _flag_review(wait, '房间自动结单校验失败，请核对订单、报单和结算信息')
        return 'review'
    return 'confirmed'


def process_due(*, batch_size=100):
    """Visit each due id at most once per pass; bad rows cannot starve later rows."""
    result = {'confirmed': 0, 'review': 0, 'skipped': 0, 'failed': 0}
    if not enabled():
        return result
    cursor, cutoff = 0, timezone.now()
    while True:
        ids = list(OrderRoomConfirmation.objects.filter(status='RUNNING', deadline__lte=cutoff, pk__gt=cursor)
                   .order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not ids:
            return result
        for pk in ids:
            try:
                outcome = process_one(pk)
            except Exception:
                # A single corrupt order or a transient storage error cannot
                # block other orders; the next scan can retry the same row.
                logger.exception('Room auto confirmation unexpected failure: wait=%s', pk)
                result['failed'] += 1
            else:
                result[outcome if outcome in ('confirmed', 'review') else 'skipped'] += 1
        cursor = ids[-1]
