"""Store-local media roots and compact, routing-only WeChat QR scenes."""
import base64
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.files.storage import FileSystemStorage

from .context import current_store

BACKGROUND_PREFIX = 'club-catalog/store-backgrounds/'


def store_slug():
    store = current_store()
    if store is None:
        raise ValueError('门店媒体需要明确的门店上下文')
    return store.slug


def media_root():
    root = Path(settings.MEDIA_ROOT).resolve()
    slug = store_slug()
    if slug == 'shenglang':
        return root
    # Store slugs are validated by the registry model; check again here because
    # storage must not allow a malformed fixture to escape its local directory.
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', slug):
        raise ValueError('Invalid store media namespace')
    return root / 'stores' / slug


def with_store_query(url):
    if store_slug() == 'shenglang':
        return url
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'store']
    query.append(('store', store_slug()))
    return urlunsplit(parts._replace(query=urlencode(query)))


class StoreFileSystemStorage(FileSystemStorage):
    @property
    def location(self):
        return str(media_root())

    def url(self, name):
        return with_store_query(super().url(name))


def _base36(value):
    if type(value) is not int or not 0 < value < 36 ** 9:
        raise ValueError('Store ID exceeds the WeChat scene limit')
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    result = ''
    while value:
        value, remainder = divmod(value, 36)
        result = digits[remainder] + result
    return result


def encode_login_scene(ticket, *, platform=False):
    store = current_store()
    if platform:
        if store is not None:
            raise ValueError('平台登录码不能绑定门店上下文')
        prefix = 'P'
    else:
        if store is None:
            raise ValueError('门店登录码需要明确的门店上下文')
        prefix = _base36(store.pk)
    encoded = base64.urlsafe_b64encode(uuid.UUID(str(ticket)).bytes).decode().rstrip('=')
    return f'{prefix}_{encoded}'


def decode_login_scene(value):
    if isinstance(value, uuid.UUID):
        return None, value  # Internal database identifiers do not route requests.
    value = str(value)
    match = re.fullmatch(r'([0-9a-z]{1,9}|P)_([A-Za-z0-9_-]{22})', value)
    if not match:
        raise ValueError('Invalid store login scene')
    prefix = match[1]
    store_id = None if prefix == 'P' else int(prefix, 36)
    if store_id is not None and store_id <= 0:
        raise ValueError('Invalid store login scene')
    raw = base64.b64decode(match[2] + '==', altchars=b'-_', validate=True)
    ticket = uuid.UUID(bytes=raw)
    if base64.urlsafe_b64encode(ticket.bytes).decode().rstrip('=') != match[2]:
        raise ValueError('Invalid store login scene encoding')
    return store_id, ticket


def save_store_logo_upload(upload):
    """Save a size-bounded PNG without discarding the source alpha channel."""
    import uuid
    from io import BytesIO
    from django.core.files.base import ContentFile
    from django.core.files.storage import FileSystemStorage
    from PIL import Image, ImageOps, UnidentifiedImageError

    extension = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(upload.image.format)
    if not extension:
        raise ValueError('仅支持 PNG、JPEG、WebP 图片')
    upload.seek(0)
    try:
        with Image.open(upload) as opened:
            image = ImageOps.exif_transpose(opened).convert('RGBA')
            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, 'PNG', optimize=True)
            display = output.getvalue()
            width, height = image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('无法读取门店 Logo，请更换有效图片') from exc
    upload.seek(0)
    key = uuid.uuid4().hex
    storage = FileSystemStorage(location=Path(settings.MEDIA_ROOT).resolve() / 'platform',
                                base_url=settings.MEDIA_URL.rstrip('/') + '/platform/')
    original = storage.save(f'logos/{key}{extension}', upload)
    path = storage.save(f'logos/{key}.png', ContentFile(display))
    return {
        'url': storage.url(path),
        'original_url': storage.url(original),
        'width': width,
        'height': height,
        'bytes': len(display),
    }


def save_store_background_upload(upload):
    from common.catalog_images import compress_catalog_image
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    if upload.image.format not in ('PNG', 'JPEG', 'WEBP'):
        raise ValueError('仅支持 PNG、JPEG、WebP 图片')
    upload.seek(0)
    display, (width, height) = compress_catalog_image(upload)
    name = default_storage.save(f'{BACKGROUND_PREFIX}{uuid.uuid4().hex}.jpg', ContentFile(display))
    return {'url': default_storage.url(name), 'width': width, 'height': height, 'bytes': len(display)}


def _background_name(value, slug):
    try:
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith(settings.MEDIA_URL):
            return ''
        if parts.query and parse_qs(parts.query, keep_blank_values=True) != {'store': [slug]}:
            return ''
        name = parts.path[len(settings.MEDIA_URL):]
        if not name.startswith(BACKGROUND_PREFIX) or not re.fullmatch(
                r'[a-f0-9]{32}(?:_[A-Za-z0-9]{7})?\.jpg', name.removeprefix(BACKGROUND_PREFIX)):
            return ''
        return name
    except ValueError:
        return ''


def public_background_url(store):
    if not _background_name(store.background_url, store.slug):
        return ''
    base = getattr(settings, 'MEDIA_BASE_URL', '').rstrip('/')
    return base + store.background_url


def verified_background_path(value):
    """Resolve only a readable background generated by this store's upload."""
    from common.catalog_images import readable_image

    name = _background_name(value, store_slug())
    if not name:
        return ''
    try:
        root = media_root()
        image = (root / name).resolve()
        if image.is_relative_to(root) and image.is_file() and readable_image(image):
            return name
    except (OSError, RuntimeError, ValueError):
        pass
    return ''
