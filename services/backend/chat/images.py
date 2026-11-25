"""Shared limits for images sent by customers and support staff."""
import warnings

from PIL import Image, UnidentifiedImageError


def chat_image_error(image):
    if image.size > 5 * 1024 * 1024:
        return '图片不能超过 5MB'
    try:
        image.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(image) as picture:
                if picture.format not in ('JPEG', 'PNG', 'WEBP'):
                    return '请上传 JPG、PNG 或 WebP 图片'
                picture.verify()
            # JPEG verification checks markers but can accept truncated pixel
            # data. Decode once so persistence never stores a broken picture.
            image.seek(0)
            with Image.open(image) as picture:
                picture.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        return '图片内容无效'
    finally:
        image.seek(0)
    return None
