"""Mark only messages the reader has actually loaded or received."""


def mark_read_through(session, *, from_support, through_id=None):
    unread = session.messages.filter(is_from_support=from_support, is_read=False)
    selected = unread.filter(pk__lte=through_id) if through_id is not None else unread
    selected.update(is_read=True)
    # Call while holding the session row lock used by both senders.
    return unread.count()
