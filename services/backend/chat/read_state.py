"""Mark only messages the reader has actually loaded or received."""
from rest_framework.exceptions import ValidationError


def validate_read_through(session, through_id):
    """Require a cursor from this conversation; zero means no messages."""
    if type(through_id) is not int or through_id < 0:
        raise ValidationError('聊天已读位置无效，请刷新后重试')
    if through_id and not session.messages.filter(pk=through_id).exists():
        raise ValidationError('聊天已读位置不属于当前会话，请刷新后重试')


def mark_read_through(session, *, from_support, through_id):
    validate_read_through(session, through_id)
    unread = session.messages.filter(is_from_support=from_support, is_read=False)
    unread.filter(pk__lte=through_id).update(is_read=True)
    # Call while holding the session row lock used by both senders.
    return unread.count()
