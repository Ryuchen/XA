"""Reconcile only persisted, stale image upload intents in the current store."""
import logging
import re
from datetime import timedelta
from pathlib import Path

from django.core.files.storage import default_storage
from django.core.exceptions import SuspiciousFileOperation
from django.utils import timezone
from stores.db import transaction
from stores.media import media_root
from .models import OrderRoomImageUploadIntent, OrderRoomMessageImage
from .room_image_integrity import MAX_IMAGE_BYTES, image_matches

_SAFE_PATH = re.compile(r'order_rooms/messages/[0-9a-f]{32}\.(?:jpg|png|webp)\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_MIN_AGE = timedelta(hours=1)


def recover_stale_uploads(*, now=None, limit=100):
    """Never enumerate the filesystem or delete a file with uncertain ownership."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('清理批次大小无效')
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValueError('清理时间必须包含时区')
    cutoff = now - _MIN_AGE
    ids = list(OrderRoomImageUploadIntent.objects.filter(status='PENDING', created_at__lte=cutoff)
               .order_by('created_at', 'pk').values_list('pk', flat=True)[:limit])
    result = {'cleaned': 0, 'review': 0, 'failed': 0}
    for intent_id in ids:
        try:
            with transaction.atomic():
                intent = OrderRoomImageUploadIntent.objects.select_for_update().filter(
                    pk=intent_id, status='PENDING', created_at__lte=cutoff).first()
                if not intent:
                    continue
                review = (not _SAFE_PATH.fullmatch(intent.path) or not _HASH.fullmatch(intent.content_sha256)
                          or OrderRoomMessageImage.objects.filter(image=intent.path).exists())
                if not review:
                    try:
                        review = not Path(default_storage.path(intent.path)).resolve().is_relative_to(media_root().resolve())
                    except (NotImplementedError, OSError, ValueError, SuspiciousFileOperation):
                        review = True
                if not review and default_storage.exists(intent.path):
                    if not 0 < default_storage.size(intent.path) <= MAX_IMAGE_BYTES:
                        review = True
                    else:
                        with default_storage.open(intent.path, 'rb') as handle:
                            review = not image_matches(handle, intent.content_sha256)
                    if not review:
                        default_storage.delete(intent.path)
                        if default_storage.exists(intent.path):
                            raise OSError('图片删除未完成')
                intent.status = 'REVIEW' if review else 'CLEANED'
                intent.resolved_at = timezone.now()
                intent.save(update_fields=['status', 'resolved_at'])
                result['review' if review else 'cleaned'] += 1
        except (OSError, ValueError):
            result['failed'] += 1
            logging.getLogger(__name__).warning('房间图片上传记录恢复失败: intent_id=%s', intent_id, exc_info=True)
    return result
