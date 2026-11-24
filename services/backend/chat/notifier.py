"""聊天消息 WS 广播 helper：复用现有 /ws/orders/ 单连接通道。"""
import logging
import uuid
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from stores.db import transaction

logger = logging.getLogger(__name__)


def schedule_chat_message(session, message, *, owner_snapshot=None):
    """Call inside the message transaction; delivery happens only after commit."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('咨询消息通知意图须与消息同事务保存')
    from .models import ChatNotificationOutbox
    owner_id, cycle = owner_snapshot or (None, None)
    message.notification_intent = True
    message.notification_recipient_id = owner_id
    message.notification_recipient_cycle = cycle
    message.save(update_fields=['notification_intent', 'notification_recipient_id', 'notification_recipient_cycle'])
    try:
        with transaction.atomic():
            row = ChatNotificationOutbox.objects.create(message=message, next_attempt_at=timezone.now(),
                recipient_account_id_snapshot=owner_id, recipient_business_cycle=cycle)
            transaction.on_commit(lambda: _deliver_safely(row.pk))
    except Exception:
        logger.exception('Chat message outbox pending recovery for message %s', message.pk)


def _deliver_safely(outbox_id):
    try:
        deliver_chat_notification(outbox_id)
    except Exception:
        # The message and outbox have already committed; the periodic scan
        # will retry without turning a successful send into a failed response.
        logger.exception('Chat notification %s awaits retry', outbox_id)


def stage_chat_session_update(session, *, owner_snapshot=None):
    """Append under the session lock before the business state is saved."""
    if not getattr(settings, 'CHAT_NOTIFICATION_OUTBOX_ENABLED', False):
        return None
    if not isinstance(session.refresh_notification_intents, list):
        logger.error('Chat session refresh intents invalid for session %s', session.pk)
        return None
    account_id, cycle = owner_snapshot or (None, None)
    key = uuid.uuid4().hex
    session.refresh_notification_intents = [*session.refresh_notification_intents,
        {'key': key, 'account_id': account_id, 'business_cycle': cycle}]
    return key


def materialize_session_intent(session_id, key):
    """Move a committed intent to outbox without losing concurrent entries."""
    from .models import ChatSession, ChatSessionNotificationOutbox
    try:
        with transaction.atomic():
            session = ChatSession.objects.select_for_update().filter(pk=session_id).first()
            if not session or not isinstance(session.refresh_notification_intents, list):
                return
            matches = [entry for entry in session.refresh_notification_intents
                       if isinstance(entry, dict) and entry.get('key') == key]
            if len(matches) != 1:
                return
            entry = matches[0]
            row, created = ChatSessionNotificationOutbox.objects.get_or_create(intent_key=key, defaults={
                'session': session, 'recipient_account_id_snapshot': entry.get('account_id'),
                'recipient_business_cycle': entry.get('business_cycle'), 'next_attempt_at': timezone.now()})
            if (not created and (row.session_id != session.pk
                    or row.recipient_account_id_snapshot != entry.get('account_id')
                    or row.recipient_business_cycle != entry.get('business_cycle'))):
                raise ValueError('会话通知意图与 outbox 不匹配')
            session.refresh_notification_intents = [item for item in session.refresh_notification_intents if item != entry]
            session.save(update_fields=['refresh_notification_intents'])
            transaction.on_commit(lambda: _deliver_session_safely(row.pk), robust=True)
    except Exception:
        logger.exception('Chat session refresh intent awaits recovery session=%s key=%s', session_id, key)


def schedule_chat_session_update(session, *, owner_snapshot=None, intent_key=None):
    """Persist the invalidation in the same transaction as reception/read state."""
    if getattr(settings, 'CHAT_NOTIFICATION_OUTBOX_ENABLED', False):
        if intent_key:
            transaction.on_commit(lambda: materialize_session_intent(session.pk, intent_key), robust=True)
        else:
            logger.error('Chat session refresh has no persisted intent for session %s', session.pk)
    else:
        transaction.on_commit(lambda: notify_chat_session_update(session, owner_snapshot=owner_snapshot))


def _account_allowed(account_id, snapshot):
    if not snapshot or snapshot[0] != account_id or type(snapshot[1]) is not int or snapshot[1] <= 0:
        return False
    from club_accounts.models import ClubAccount
    return ClubAccount.objects.filter(pk=account_id, license_membership_active=True,
        business_cycle=snapshot[1], account_type__in=('BOSS', 'PROVIDER')).exists()


def _deliver_session_safely(outbox_id):
    try:
        deliver_chat_session_notification(outbox_id)
    except Exception:
        logger.exception('Chat session notification %s awaits retry', outbox_id)


def deliver_chat_session_notification(outbox_id):
    from .models import ChatSessionNotificationOutbox
    with transaction.atomic():
        row = ChatSessionNotificationOutbox.objects.select_for_update().filter(pk=outbox_id).first()
        if row is None or row.completed_at:
            return False
        row.attempts += 1
        layer = get_channel_layer()
        account_id = row.session.account_id
        event = {'type': 'chat_session_update', 'data': {'session_id': row.session_id}}
        destinations = [('operators_sent_at', 'operators')]
        snapshot = (row.recipient_account_id_snapshot, row.recipient_business_cycle)
        if account_id and _account_allowed(account_id, snapshot):
            destinations.append(('account_sent_at', f'account_{account_id}'))
        else:
            if not row.account_sent_at:
                row.account_skipped_reason = '原收件账号或周期不可核验'
        failed = False
        for field, group in destinations:
            if getattr(row, field):
                continue
            try:
                if layer is None:
                    raise RuntimeError('Channel layer unavailable')
                outgoing = event
                if field == 'account_sent_at':
                    outgoing = {'type': 'chat_session_update', 'data': {'session_id': row.session_id,
                                'recipient_business_cycle': row.recipient_business_cycle}}
                async_to_sync(layer.group_send)(group, outgoing)
                setattr(row, field, timezone.now())
            except Exception as exc:
                failed = True
                if isinstance(exc, (RedisConnectionError, RedisTimeoutError)):
                    break
        if failed:
            row.last_error = 'CHANNEL_UNAVAILABLE'
            row.next_attempt_at = timezone.now() + timedelta(seconds=min(300, 2 ** min(row.attempts, 8)))
        else:
            row.last_error = ''
            row.completed_at = timezone.now()
        row.save(update_fields=['operators_sent_at', 'account_sent_at', 'account_skipped_reason', 'attempts', 'last_error',
                                'next_attempt_at', 'completed_at'])
        return row.completed_at is not None


def deliver_chat_notification(outbox_id):
    """Retry only unfinished channel sends. Acceptance is not client delivery."""
    from .models import ChatMessage, ChatNotificationOutbox
    with transaction.atomic():
        # Lock only the outbox row; a joined FOR UPDATE would also lock the
        # chat session and invert the sender's session -> outbox lock order.
        row = ChatNotificationOutbox.objects.select_for_update().filter(pk=outbox_id).first()
        if row is None or row.completed_at:
            return False
        row.attempts += 1
        message = ChatMessage.objects.select_related('session').get(pk=row.message_id)
        session = message.session
        layer = get_channel_layer()
        failed = False
        payload = {'session_id': session.pk, 'message': {'id': message.pk}}
        snapshot = (row.recipient_account_id_snapshot, row.recipient_business_cycle)
        allowed = _account_allowed(session.account_id, snapshot)
        if not allowed and not row.account_sent_at:
            row.account_skipped_reason = '原收件账号或周期不可核验'
        account_payload = dict(payload)
        account_payload['recipient_business_cycle'] = row.recipient_business_cycle
        destinations = [
            ('operators_sent_at', 'operators', {'type': 'chat_message', 'data': payload}),
            ('account_sent_at', f'account_{session.account_id}' if session.account_id and allowed else None,
             {'type': 'chat_message', 'data': account_payload}),
            ('session_sent_at', 'operators', {'type': 'chat_session_update', 'data': {'session_id': session.pk}}),
        ]
        for field, group, event in destinations:
            if getattr(row, field) or (field == 'account_sent_at' and row.account_skipped_reason):
                continue
            if layer is None or group is None:
                failed = True
                continue
            try:
                async_to_sync(layer.group_send)(group, event)
                setattr(row, field, timezone.now())
            except Exception as exc:
                failed = True
                logger.warning('Chat notification channel unavailable for message %s', message.pk)
                # A disconnected Redis affects all groups on this layer.
                # Avoid waiting for the same connection timeout three times;
                # untouched destinations remain pending for the next retry.
                if isinstance(exc, (RedisConnectionError, RedisTimeoutError)):
                    break
        if failed:
            row.last_error = 'CHANNEL_UNAVAILABLE'
            row.next_attempt_at = timezone.now() + timedelta(seconds=min(300, 2 ** min(row.attempts, 8)))
        else:
            row.last_error = ''
            row.completed_at = timezone.now()
        row.save(update_fields=['operators_sent_at', 'account_sent_at', 'account_skipped_reason', 'session_sent_at', 'completed_at',
                                'next_attempt_at', 'attempts', 'last_error'])
        return not failed


def notify_chat_message(session, message, *, owner_snapshot=None):
    """广播一条新消息。

    - 用户发出 → 推送给所有客服(operators) + 回推用户多端(account_{id})
    - 客服发出 → 推送给会话所属用户(account_{id}) + 同步其他客服(operators)
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    # Consumers reload the message after checking the current receiver's
    # account, permissions and session claim. Do not put private content on
    # the shared operator group.
    payload = {'session_id': session.id, 'message': {'id': message.id}}

    async_to_sync(channel_layer.group_send)(
        'operators',
        {'type': 'chat_message', 'data': payload},
    )
    if _account_allowed(session.account_id, owner_snapshot):
        account_payload = dict(payload)
        account_payload['recipient_business_cycle'] = owner_snapshot[1]
        async_to_sync(channel_layer.group_send)(f'account_{session.account_id}',
            {'type': 'chat_message', 'data': account_payload})
    elif session.account_id:
        logger.warning('咨询消息实时接收周期待核对 session=%s message=%s', session.pk, message.pk)
    notify_chat_session_update(session, owner_snapshot=owner_snapshot)


def notify_chat_session_update(session, *, owner_snapshot=None):
    """Tell staff and the session owner to refresh committed state."""
    try:
        channel_layer = get_channel_layer()
    except Exception:
        logger.exception('Chat session channel unavailable for session %s', session.pk)
        return
    if channel_layer is None:
        return
    groups = ['operators']
    if session.account_id and _account_allowed(session.account_id, owner_snapshot):
        groups.append(f'account_{session.account_id}')
    elif session.account_id:
        logger.warning('咨询会话实时接收周期待核对 session=%s', session.pk)
    event = {'type': 'chat_session_update', 'data': {'session_id': session.id}}
    for group in groups:
        try:
            outgoing = event
            if group != 'operators':
                outgoing = {'type': 'chat_session_update', 'data': {'session_id': session.id,
                            'recipient_business_cycle': owner_snapshot[1]}}
            async_to_sync(channel_layer.group_send)(group, outgoing)
        except Exception:
            # A committed read/assignment must not appear to have failed.
            logger.exception('Chat session update unavailable for session %s', session.pk)
