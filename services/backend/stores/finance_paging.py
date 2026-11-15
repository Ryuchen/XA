"""Stable bounded database reads for complete platform finance lists."""
from django.db.models import Q


def descending_created(query):
    cursor = None
    while True:
        current = query
        if cursor is not None:
            current = current.filter(Q(created_at__lt=cursor[0])
                                     | Q(created_at=cursor[0], pk__lt=cursor[1]))
        page = list(current.order_by('-created_at', '-pk')[:256])
        if not page:
            return
        yield from page
        cursor = page[-1].created_at, page[-1].pk
