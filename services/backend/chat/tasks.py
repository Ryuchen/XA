"""Store-scoped retries for committed chat notification attempts."""
from celery import shared_task
from django.conf import settings
from django.utils import timezone


@shared_task
def drain_chat_notifications():
    message_count = _drain_message_notices()
    if not getattr(settings, 'CHAT_NOTIFICATION_OUTBOX_ENABLED', False):
        return message_count + _drain_transfer_notices()
    from .models import ChatSession, ChatSessionNotificationOutbox
    from .notifier import _deliver_session_safely, materialize_session_intent
    sessions = ChatSession.objects.exclude(refresh_notification_intents=[]).values_list(
        'pk', 'refresh_notification_intents').order_by('pk')
    for session_id, intents in sessions.iterator(chunk_size=200):
        if not isinstance(intents, list):
            continue
        for entry in intents:
            if isinstance(entry, dict) and isinstance(entry.get('key'), str) and len(entry['key']) == 32:
                materialize_session_intent(session_id, entry['key'])
    state_ids = list(ChatSessionNotificationOutbox.objects.filter(
        completed_at__isnull=True, next_attempt_at__lte=timezone.now()
    ).order_by('next_attempt_at', 'pk').values_list('pk', flat=True)[:100])
    for pk in state_ids:
        _deliver_session_safely(pk)
    return message_count + len(state_ids) + _drain_transfer_notices()


def _drain_message_notices():
    from .models import ChatMessage, ChatNotificationOutbox
    from .notifier import _deliver_safely
    for message in ChatMessage.objects.filter(notification_intent=True,
            notification_outbox__isnull=True).order_by('pk').iterator(chunk_size=200):
        try:
            ChatNotificationOutbox.objects.get_or_create(message=message, defaults={
                'next_attempt_at': timezone.now(), 'recipient_account_id_snapshot': message.notification_recipient_id,
                'recipient_business_cycle': message.notification_recipient_cycle})
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Chat outbox restore failed for message %s', message.pk)
    ids = list(ChatNotificationOutbox.objects.filter(completed_at__isnull=True, next_attempt_at__lte=timezone.now())
               .order_by('next_attempt_at', 'pk').values_list('pk', flat=True)[:100])
    for pk in ids:
        _deliver_safely(pk)
    return len(ids)


def _drain_transfer_notices():
    from .models import ChatAssignmentEvent
    from .transfer_notices import deliver_safely
    transfer_ids = ChatAssignmentEvent.objects.filter(transfer_notice_pending=True,
        transfer_notice_completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    transfer_count = 0
    for pk in transfer_ids.iterator(chunk_size=200):
        deliver_safely(pk)
        transfer_count += 1
    return transfer_count
