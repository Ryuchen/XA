"""Use the records and files that the store can actually show to customers."""
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from django.conf import settings
from django.utils.html import strip_tags

from common.catalog_images import readable_catalog_image, readable_image
from common.private_media import public_media_path
from engagement.models import ClubContent
from engagement.public_content import PUBLIC_CONTENT_LIMIT, current_content

from .media import media_root


def store_logo_present(store, store_domains):
    try:
        url = store.logo_url
        parts = urlsplit(url)
        base = urlsplit(getattr(settings, 'MEDIA_BASE_URL', ''))
        admin_host = getattr(settings, 'ADMIN_STORE_HOST', 'admin.esports.club')
        if parts.netloc or parts.scheme:
            managed_base = (parts.scheme, parts.netloc) == (base.scheme, base.netloc)
            if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
                    or (not managed_base and parts.hostname not in (*store_domains, admin_host))):
                return False
        path = unquote(parts.path)
        if not path.startswith(settings.MEDIA_URL) or not public_media_path(url):
            return False
        relative = path[len(settings.MEDIA_URL):]
        if not relative.startswith(('platform/logos/', 'stores/logos/')):
            return False
        platform_logo = relative.startswith('platform/logos/')
        if not platform_logo and parts.hostname == admin_host:
            return False
        if any(key == 'store' and (platform_logo or value != store.slug)
               for key, value in parse_qsl(parts.query, keep_blank_values=True)):
            return False
        if platform_logo:
            root = Path(settings.MEDIA_ROOT).resolve() / 'platform'
            relative = relative.removeprefix('platform/')
        else:
            root = media_root()
        image = (root / relative).resolve()
        return image.is_relative_to(root) and image.is_file() and readable_image(image)
    except (OSError, RuntimeError, ValueError):
        return False


def order_notice_evidence(now):
    rows = current_content(now).only('kind', 'title', 'content', 'image_url', 'ends_at')[:PUBLIC_CONTENT_LIMIT]
    for row in rows:
        if row.kind != ClubContent.Kind.ORDER_RULES or not row.title.strip():
            continue
        text = unescape(strip_tags(row.content)).strip()
        if not text and not readable_catalog_image(row.image_url):
            continue
        # A newly published entry can push the notice out of the public list.
        next_publication = (ClubContent.objects.filter(is_active=True, starts_at__gt=now)
            .exclude(kind=ClubContent.Kind.POPUP).order_by('starts_at').values_list('starts_at', flat=True).first())
        recheck = min((time for time in (row.ends_at, next_publication) if time is not None), default=None)
        return {'content_id': row.pk, 'recheck_at': recheck.isoformat() if recheck else None}
    return None
