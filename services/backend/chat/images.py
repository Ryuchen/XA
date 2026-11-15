"""Shared limits for images sent by customers and support staff."""
from PIL import Image, UnidentifiedImageError


def chat_image_error(image):
    if image.size > 5 * 1024 * 1024:
        return '图片不能超过 5MB'
    try:
        image.seek(0)
        picture = Image.open(image)
        if picture.format not in ('JPEG', 'PNG', 'WEBP'):
            return '请上传 JPG、PNG 或 WebP 图片'
        picture.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return '图片内容无效'
    finally:
        image.seek(0)
    return None
