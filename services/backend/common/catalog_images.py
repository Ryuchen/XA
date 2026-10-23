"""Size-bounded catalog thumbnails shared by all customer-facing catalog APIs."""
from hashlib import sha256
from io import BytesIO
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps, UnidentifiedImageError

DISPLAY_MAX_WIDTH = 1080
# Use decimal KB as the stricter interpretation of the product requirement.
DISPLAY_MAX_BYTES = 200_000
DISPLAY_PREFIX = 'club-catalog/thumbnails/v4/'
CATALOG_PREFIXES = ('club-catalog/', 'banners/', 'game_categories/',
                    'accounts/avatars/', 'customers/avatars/', 'escorts/avatars/')


def compress_catalog_image(source):
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
            rgba = image.convert('RGBA')
            image = Image.new('RGB', rgba.size, '#fffdf5')
            image.paste(rgba, mask=rgba.getchannel('A'))
        else:
            image = image.convert('RGB')
        width = min(image.width, DISPLAY_MAX_WIDTH,
                    max(1, round(image.width * 16000 / image.height)))
        while True:
            size = (width, max(1, round(image.height * width / image.width)))
            display = image.resize(size, Image.Resampling.LANCZOS)
            for quality in (82, 76, 70, 64, 58, 50):
                output = BytesIO()
                display.save(output, 'JPEG', quality=quality, subsampling=0)
                result = output.getvalue()
                if len(result) <= DISPLAY_MAX_BYTES:
                    return result, display.size
            if width == 1:
                raise ValueError('无法生成200KB以内的展示图片')
            width = max(1, min(width - 1, round(width * .85)))


def _catalog_path(value):
    value = str(value or '').strip()
    if not value or '\\' in value or any(ord(c) < 32 for c in value):
        return ''
    try:
        parts = urlsplit(value)
    except ValueError:
        return ''
    if parts.query or parts.fragment or parts.username or parts.password:
        return ''
    if parts.netloc:
        # Never fetch arbitrary URLs or resolve another host against local storage.
        hosts = {'api.xaesports.club', 'admin.xaesports.club', 'provider.xaesports.club'}
        base_host = urlsplit(getattr(settings, 'MEDIA_BASE_URL', '')).hostname
        if base_host:
            hosts.add(base_host)
        if parts.scheme != 'https' or parts.hostname not in hosts:
            return ''
    elif parts.scheme:
        return ''
    path = unquote(parts.path)
    prefix = settings.MEDIA_URL
    if not path.startswith(prefix):
        return ''
    path = path[len(prefix):]
    if any(p in ('', '.', '..') for p in path.split('/')) or '\\' in path:
        return ''
    return path if path.startswith(CATALOG_PREFIXES) else ''


def catalog_display_url(value):
    """Return only verified bounded assets; keep source files and DB values intact.

    Derivatives are shared on storage across workers. The source signature changes
    when a same-path upload is replaced. Missing, private, foreign and corrupt
    files fail closed instead of falling back to an unbounded original.
    """
    path = _catalog_path(value)
    if not path:
        return ''
    try:
        size = default_storage.size(path)
        if path.startswith(DISPLAY_PREFIX) and size <= DISPLAY_MAX_BYTES:
            return default_storage.url(path)
        modified = default_storage.get_modified_time(path).isoformat()
        key = sha256(f'{path}|{size}|{modified}'.encode()).hexdigest()
        target = f'{DISPLAY_PREFIX}{key}.jpg'
        if not default_storage.exists(target) or default_storage.size(target) > DISPLAY_MAX_BYTES:
            with default_storage.open(path, 'rb') as source:
                display, _ = compress_catalog_image(source)
            # Storage.save is atomic on FileSystemStorage; concurrent requests can
            # safely receive either of the identical bounded derivatives.
            target = default_storage.save(target, ContentFile(display))
        return default_storage.url(target)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return ''


def save_catalog_upload(upload, prefix):
    """Archive a validated upload and return its bounded display version."""
    import uuid
    ext = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(upload.image.format)
    if not ext:
        raise ValueError('仅支持 PNG、JPEG、WebP 图片')
    upload.seek(0)
    display, (width, height) = compress_catalog_image(upload)
    upload.seek(0)
    key = uuid.uuid4().hex
    original = default_storage.save(f'{prefix}/{key}{ext}', upload)
    path = default_storage.save(f'{DISPLAY_PREFIX}{key}.jpg', ContentFile(display))
    return {'url': default_storage.url(path), 'original_url': default_storage.url(original),
            'width': width, 'height': height, 'bytes': len(display)}


def bounded_public_image_url(value):
    """Keep small public files stable; replace oversized local images only."""
    base = getattr(settings, 'MEDIA_BASE_URL', '').rstrip('/')
    local_value = value[len(base):] if base and str(value).startswith(base + '/media/') else value
    path = _catalog_path(local_value)
    if path:
        try:
            if default_storage.size(path) > DISPLAY_MAX_BYTES:
                return catalog_display_url(local_value)
        except OSError:
            pass
    return value
