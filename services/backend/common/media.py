"""媒体文件 URL 统一构建工具。

约定：物理文件落在 MEDIA_ROOT，数据库仅存相对路径；对外统一输出绝对 URL。
优先使用可配置的 MEDIA_BASE_URL（对象存储 / 独立媒体域名的过渡入口），
未配置时回退到当前请求的 Host（本地开发零配置）。
"""
from django.conf import settings


def build_media_url(request, field):
    """将 FileField / ImageField 转换为对外可访问的绝对 URL。

    field 为空（未上传）时返回空字符串。
    """
    if not field:
        return ''

    base = getattr(settings, 'MEDIA_BASE_URL', '')
    if base:
        return f"{base.rstrip('/')}{field.url}"

    return request.build_absolute_uri(field.url) if request else field.url


def build_media_url_from_path(request, url):
    """已持有 .url 字符串（而非 field 对象）时的等价构建。"""
    if not url:
        return ''

    base = getattr(settings, 'MEDIA_BASE_URL', '')
    if base:
        return f"{base.rstrip('/')}{url}"

    return request.build_absolute_uri(url) if request else url
