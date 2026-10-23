"""媒体文件 URL 统一构建工具。

约定：物理文件落在 MEDIA_ROOT，数据库仅存相对路径；对外统一输出绝对 URL。
公开素材使用 MEDIA_BASE_URL 或当前 Host；业务凭证始终由 API 鉴权签发，
不经过公开媒体域名。未知目录默认不输出文件地址。
"""
from django.conf import settings
from django.db.models import ImageField
from .private_media import PRIVATE_FIELDS, private_media_url, public_media_path


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
    base = getattr(settings, 'MEDIA_BASE_URL', '')
    if base:
        return f"{base.rstrip('/')}{url}"

    return request.build_absolute_uri(url) if request else url


def build_media_url_from_path(request, url):
    """已持有 .url 字符串（而非 field 对象）时的等价构建。"""
    if not url:
        return ''
    if not public_media_path(url):
        return ''

    from .catalog_images import bounded_public_image_url
    url = bounded_public_image_url(url)
    if not url:
        return ''

    base = getattr(settings, 'MEDIA_BASE_URL', '')
    if base:
        return f"{base.rstrip('/')}{url}"

    return request.build_absolute_uri(url) if request else url
