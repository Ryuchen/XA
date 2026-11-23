"""Bind a staff-created aftersale to a real, authorized consultation card."""
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from .authorization import can_read_session
from .models import ChatMessage, ChatSession


def lookup(message_id):
    if type(message_id) is not int or not 0 < message_id <= 9223372036854775807:
        raise ValidationError('请选择有效的咨询订单消息')
    hint = ChatMessage.objects.filter(pk=message_id).values('session_id', 'session__account_id').first()
    if not hint or not hint['session__account_id']:
        raise ValidationError('咨询消息或所属账号待核对')
    return hint


def lock_context(hint, message_id, actor):
    # Linked actions lock the order first. The conversation itself still
    # follows the chat owner -> session -> message order.
    owner = ClubAccount.objects.select_for_update().filter(pk=hint['session__account_id']).first()
    session = ChatSession.objects.select_for_update().filter(pk=hint['session_id'], account=owner).first()
    message = ChatMessage.objects.select_for_update().filter(pk=message_id, session=session).first() if session else None
    if not owner or not session or not message or not can_read_session(actor, session):
        raise ValidationError('咨询不存在、归属已变化或无权处理')
    if (owner.account_type not in ('BOSS', 'PROVIDER') or not owner.license_membership_active
            or message.is_from_support or message.sender_account_id != owner.pk
            or message.notification_recipient_id != owner.pk
            or type(message.notification_recipient_cycle) is not int
            or message.notification_recipient_cycle != owner.business_cycle):
        raise ValidationError('咨询原账号或业务周期待核对')
    return owner, session, message


def verify_order(context, order, scope):
    owner, session, message = context
    if message.related_order_id != order.pk:
        raise ValidationError('咨询消息未正式关联该订单')
    if owner.account_type == 'BOSS':
        if order.customer_account_id != owner.pk or order.business_cycle != owner.business_cycle:
            raise ValidationError('咨询老板与订单原业务周期不一致')
    else:
        from .order_context import owned_order_queryset, participated_order_queryset
        from site_messages.order_cycle import recipient_cycle
        participant = (owned_order_queryset(owner).filter(pk=order.pk).exists()
                       or participated_order_queryset(owner).filter(pk=order.pk).exists())
        if (not participant or recipient_cycle(order, owner.pk) != owner.business_cycle
                or not isinstance(scope, list) or owner.pk not in scope):
            raise ValidationError('咨询陪玩与本次受影响人员或原业务周期不一致')
    return {'message_id': message.pk, 'session_id': session.pk,
            'account_id': owner.pk, 'business_cycle': owner.business_cycle}
