"""Resolve banner targets in batches for storefront and staff views."""
from django.db.models import Q
from django.utils import timezone

from .models import Banner


def prepare_banner_availability(rows, *, account=None):
    rows = list(rows)
    def target_ids(kind):
        return {int(row.link_value) for row in rows if row.link_type == kind
                and row.link_value.isascii() and row.link_value.isdigit() and len(row.link_value) <= 18}
    product_ids = target_ids(Banner.LinkType.PRODUCT)
    announcement_ids = target_ids(Banner.LinkType.ANNOUNCEMENT)
    gameplay_ids = target_ids(Banner.LinkType.GAMEPLAY)
    content_ids = target_ids(Banner.LinkType.CONTENT)
    product_reasons = {}
    if product_ids:
        from orders.catalog import visible_service_q
        from orders.models import ServiceItem
        qs = ServiceItem.objects.filter(visible_service_q(), pk__in=product_ids)
        if account is not None:
            from orders.models import ExperienceUse
            from orders.purchase_benefits import account_service_q, activity_scope
            visible = list(qs.only('id', 'experience_kind', 'platform'))
            available_ids = set(qs.filter(account_service_q(account)).values_list('pk', flat=True))
            restricted = [service for service in visible if service.pk not in available_ids]
            scopes = {service.pk: activity_scope(account, service.experience_kind, service.platform)
                      for service in restricted}
            scope_query = Q(pk__in=[])
            for scope in scopes.values():
                scope_query |= Q(**scope)
            used = set(ExperienceUse.objects.filter(scope_query).values_list('kind', 'platform', 'period')) if scopes else set()
            product_reasons = {service.pk: ('QUALIFICATION_USED' if
                (scopes[service.pk]['kind'], scopes[service.pk]['platform'], scopes[service.pk]['period']) in used
                else 'QUALIFICATION_REQUIRED') for service in restricted}
            product_ids = available_ids
        else:
            product_ids = set(qs.values_list('pk', flat=True))
    if announcement_ids:
        from announcements.models import Announcement
        announcement_ids = set(Announcement.objects.filter(is_active=True, pk__in=announcement_ids).values_list('pk', flat=True))
    if gameplay_ids:
        from orders.models import Gameplay
        gameplay_ids = set(Gameplay.objects.filter(is_active=True, pk__in=gameplay_ids).values_list('pk', flat=True))
    if content_ids:
        from engagement.models import ClubContent
        now = timezone.now()
        content_ids = set(ClubContent.objects.filter(is_active=True, pk__in=content_ids).exclude(kind='POPUP')
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            .filter(Q(kind='HOT') | Q(ends_at__isnull=True) | Q(ends_at__gt=now)).values_list('pk', flat=True))
    available = {Banner.LinkType.PRODUCT: product_ids, Banner.LinkType.ANNOUNCEMENT: announcement_ids,
                 Banner.LinkType.GAMEPLAY: gameplay_ids, Banner.LinkType.CONTENT: content_ids}
    for row in rows:
        row.link_unavailable_reason = ''
        if row.link_type in available:
            row.link_available = (row.link_value.isascii() and row.link_value.isdigit() and len(row.link_value) <= 18
                                  and int(row.link_value) in available[row.link_type])
            if row.link_type == Banner.LinkType.PRODUCT and row.link_value.isascii() and row.link_value.isdigit() and len(row.link_value) <= 18:
                row.link_unavailable_reason = product_reasons.get(int(row.link_value), '')
        else:
            row.link_available = True
    return rows
