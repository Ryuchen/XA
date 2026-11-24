from django.db.models import Q

from .models import ClubContent

PUBLIC_CONTENT_LIMIT = 50


def published_content(now):
    return (ClubContent.objects.filter(is_active=True).exclude(kind=ClubContent.Kind.POPUP)
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)))


def current_content(now):
    return published_content(now).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
