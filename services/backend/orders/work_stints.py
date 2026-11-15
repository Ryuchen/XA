"""Independent provider participation facts; caller holds the order lock."""
from django.utils import timezone
from .models import OrderTransferSeatEvent, OrderWorkStint


def uses_work(order):
    # Work stints are participation evidence for both attribution paths; they
    # never decide price or authorize settlement.
    return not order.is_gift_snapshot


def initial_entry(order, account_id, *, at, image=None):
    if not uses_work(order) or order.is_gift_snapshot or not account_id:
        return
    from .models import OrderOriginalAttribution, OrderParticipationSegment
    attribution = OrderOriginalAttribution.objects.filter(order=order).first()
    if attribution:
        segments = list(OrderParticipationSegment.objects.select_for_update().filter(
            attribution=attribution, provider_account_id=account_id, released_at__isnull=True)[:2])
        if len(segments) != 1 or not segments[0].started_at:
            raise ValueError('本人履约与原价分段待核对')
        at = segments[0].started_at
    existing = OrderWorkStint.objects.select_for_update().filter(order=order, account_id=account_id,
                                                                 released_at__isnull=True).first()
    if existing:
        if attribution and existing.started_at != at:
            raise ValueError('本人履约与原价分段待核对')
        return
    stint = OrderWorkStint.objects.create(order=order, account_id=account_id, started_at=at, entry_image=image or None)
    from .work_original_capture import initial
    initial(order, stint)


def transferred(order, commands, *, at, operator):
    if not uses_work(order):
        return
    for command in commands:
        old, new = command['old_provider_account_id'], command['new_provider_account'].pk
        previous = list(OrderWorkStint.objects.select_for_update().filter(order=order, account_id=old,
                                                                          released_at__isnull=True))
        if len(previous) > 1:
            raise ValueError('履约段记录异常，请先核对')
        if previous:
            previous[0].released_at = at
            previous[0].save(update_fields=['released_at'])
        event = OrderTransferSeatEvent.objects.filter(seat__order=order, old_account_id=old,
            new_account_id=new, seat__current_account_id=new).order_by('-sequence').first()
        if not event:
            raise ValueError('转单席位记录异常，请先核对')
        incoming = OrderWorkStint.objects.create(order=order, account_id=new, incoming=event)
        from .work_original_capture import transfer
        transfer(previous[0] if previous else None, incoming, event, command, operator, at=at)


def personal_completion(order, account_id):
    if not uses_work(order):
        return
    stint = OrderWorkStint.objects.select_for_update().filter(order=order, account_id=account_id,
        released_at__isnull=True, started_at__isnull=False).first()
    if stint and not stint.completed_at:
        stint.completed_at = timezone.now()
        stint.save(update_fields=['completed_at'])
    return stint.completed_at if stint else None
