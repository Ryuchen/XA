"""媒体文件 URL 统一构建工具。

约定：物理文件落在 MEDIA_ROOT，数据库仅存相对路径；对外统一输出绝对 URL。
门店公开素材由本店域名读取。平台公共素材可使用 MEDIA_BASE_URL。
业务凭证始终由 API 鉴权签发。未知目录默认不输出文件地址。
"""
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from django.conf import settings
from django.db.models import ImageField
from stores.context import current_store
from stores.hosts import store_media_origin
from .private_media import PRIVATE_FIELDS, private_media_url, public_media_path


def render_public_media_url(request, url, *, store=None):
    if not url:
        return ''
    parts = urlsplit(url)
    if parts.scheme not in ('', 'http', 'https') or parts.username or parts.password:
        return ''
    platform_logo = unquote(parts.path).startswith(settings.MEDIA_URL + 'platform/logos/')
    store = store or (current_store() if request else None)
    if store and not platform_logo:
        hints = parse_qs(parts.query, keep_blank_values=True).get('store', [])
        if hints and hints != [store.slug]:
            return ''
        origin = store_media_origin(store, request)
        path = urlunsplit(('', '', parts.path, parts.query, parts.fragment))
        return origin + path if origin else ''
    if parts.netloc:
        return url
    base = getattr(settings, 'MEDIA_BASE_URL', '')
    if base:
        return base.rstrip('/') + url
    return request.build_absolute_uri(url) if request else url


def build_media_url(request, field):
    """将 FileField / ImageField 转换为对外可访问的绝对 URL。

    field 为空（未上传）时返回空字符串。
    """
    if not field:
        return ''

    if field.field.name in PRIVATE_FIELDS.get(field.instance._meta.label_lower, ()):
        return private_media_url(request, field)
    if not public_media_path(field.url):
        return ''

    url = field.url
    if isinstance(field.field, ImageField):
        from .catalog_images import bounded_public_image_url
        url = bounded_public_image_url(url)
        if not url:
            return ''
    return render_public_media_url(request, url)


def build_media_url_from_path(request, url, *, store=None):
    """已持有 .url 字符串（而非 field 对象）时的等价构建。"""
    if not url:
        return ''
    if not public_media_path(url):
        return ''

    if store is None or getattr(current_store(), 'slug', None) == store.slug:
        from .catalog_images import bounded_public_image_url
        url = bounded_public_image_url(url)
    return render_public_media_url(request, url, store=store)
