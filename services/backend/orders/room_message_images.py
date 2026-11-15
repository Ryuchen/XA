"""Private image messages; payment and game evidence are separate records."""
import hashlib
import logging
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from django.core.files.storage import default_storage
from django.db import DatabaseError
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from stores.db import transaction
from .models import Order, OrderRoomMessage, OrderRoomMessageImage, OrderRoomImageUploadIntent
from .reservation_permissions import staff_can
from .room_message_notifier import schedule_room_message_update
from .room_messages import OrderRoomMessageView, _message
from .room_image_integrity import MAX_IMAGE_BYTES, image_matches

EXTENSIONS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}


def _existing_image_matches(attachment):
    try:
        with attachment.image.open('rb') as handle:
            return image_matches(handle, attachment.content_sha256)
    except (OSError, ValueError):
        return False


class OrderRoomMessageImageView(OrderRoomMessageView):
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ['post', 'options']

    def post(self, request, order_id):
        if (set(request.data) != {'image', 'client_request_id'} or len(request.FILES.getlist('image')) != 1
                or len(request.data.getlist('client_request_id')) != 1):
            return Response({'code': 400, 'msg': '请选择图片并填写有效请求编号'}, status=400)
        key = request.data.get('client_request_id')
        upload = request.FILES['image']
        if (not isinstance(key, str) or not 0 < len(key) <= 64 or key != key.strip()
                or not 0 < upload.size <= MAX_IMAGE_BYTES):
            return Response({'code': 400, 'msg': '请求编号无效或图片超过 5MB'}, status=400)
        try:
            picture = Image.open(upload)
            extension = EXTENSIONS.get(picture.format)
            if not extension:
                raise ValueError('unsupported image')
            picture.verify()
            upload.seek(0)
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            return Response({'code': 400, 'msg': '请上传有效的 JPG、PNG 或 WebP 图片'}, status=400)
        digest = hashlib.sha256()
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)
        fingerprint = digest.hexdigest()
        # The recovery intent must be committed independently of the later
        # order/file transaction. An enclosing request transaction cannot
        # provide that guarantee.
        if transaction.get_connection().in_atomic_block or not transaction.get_connection().get_autocommit():
            return Response({'code': 503, 'msg': '图片上传事务环境不可用，请稍后重试'}, status=503)
        with transaction.atomic():
            order, room, role = self._access(request.account, order_id, lock=True)
            if not room or not role:
                return Response({'code': 404, 'msg': '房间不存在'}, status=404)
            existing = OrderRoomMessage.objects.filter(room=room, author=request.account, request_id=key).first()
            if existing:
                image = getattr(existing, 'image_attachment', None)
                if not image or image.content_sha256 != fingerprint:
                    return Response({'code': 409, 'errorCode': 'REQUEST_CONFLICT', 'msg': '请求编号已用于其他消息'}, status=409)
                if not _existing_image_matches(image):
                    return Response({'code': 409, 'errorCode': 'IMAGE_REVIEW', 'msg': '原图片异常，请联系客服核对'}, status=409)
                return Response({'code': 0, 'data': _message(existing, request)})
            if role == 'FORMER_PROVIDER' or (role == 'STAFF' and not staff_can(request.account, 'support:edit')):
                return Response({'code': 403, 'msg': '当前身份不能发送房间图片'}, status=403)
            if order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE):
                return Response({'code': 400, 'msg': '订单已结束，不能发送房间图片'}, status=400)
        filename = f'{uuid4().hex}.{extension}'
        intended_path = f'order_rooms/messages/{filename}'
        with transaction.atomic(durable=True):
            intent = OrderRoomImageUploadIntent.objects.create(path=intended_path, content_sha256=fingerprint,
                                                              author=request.account, room=room, request_id=key)
        attachment, saved_name = None, None
        try:
            with transaction.atomic():
                order, room, role = self._access(request.account, order_id, lock=True)
                intent = OrderRoomImageUploadIntent.objects.select_for_update().get(pk=intent.pk)
                if intent.status != 'PENDING':
                    return Response({'code': 409, 'msg': '上传记录已过期，请重新发送'}, status=409)
                def unused():
                    intent.status, intent.resolved_at = 'CLEANED', timezone.now()
                    intent.save(update_fields=['status', 'resolved_at'])
                if not room or not role:
                    unused()
                    return Response({'code': 404, 'msg': '房间不存在'}, status=404)
                existing = OrderRoomMessage.objects.filter(room=room, author=request.account, request_id=key).first()
                if existing:
                    image = getattr(existing, 'image_attachment', None)
                    unused()
                    if not image or image.content_sha256 != fingerprint:
                        return Response({'code': 409, 'errorCode': 'REQUEST_CONFLICT', 'msg': '请求编号已用于其他消息'}, status=409)
                    if not _existing_image_matches(image):
                        return Response({'code': 409, 'errorCode': 'IMAGE_REVIEW', 'msg': '原图片异常，请联系客服核对'}, status=409)
                    return Response({'code': 0, 'data': _message(existing, request)})
                if role == 'FORMER_PROVIDER' or (role == 'STAFF' and not staff_can(request.account, 'support:edit')):
                    unused()
                    return Response({'code': 403, 'msg': '当前身份不能发送房间图片'}, status=403)
                if order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE):
                    unused()
                    return Response({'code': 400, 'msg': '订单已结束，不能发送房间图片'}, status=400)
                if default_storage.exists(intended_path):
                    intent.status, intent.resolved_at = 'REVIEW', timezone.now()
                    intent.save(update_fields=['status', 'resolved_at'])
                    return Response({'code': 409, 'msg': '上传文件名异常，请重新发送'}, status=409)
                message = OrderRoomMessage.objects.create(room=room, author=request.account, author_role=role,
                                                          content='[图片]', request_id=key)
                attachment = OrderRoomMessageImage(message=message, content_sha256=fingerprint)
                attachment.image.save(filename, upload, save=False)
                saved_name = attachment.image.name
                if saved_name != intended_path:
                    raise RuntimeError('上传文件名与恢复记录不一致')
                attachment.save()
                intent.status, intent.attachment, intent.resolved_at = 'ATTACHED', attachment, timezone.now()
                intent.save(update_fields=['status', 'attachment', 'resolved_at'])
                schedule_room_message_update(order)
                return Response({'code': 0, 'data': _message(message, request), 'msg': '图片已发送'})
        except Exception as exc:
            # A commit can succeed even if its response is lost. Never delete
            # the intended file here: the persisted intent decides recovery.
            if saved_name and saved_name != intended_path:
                try:
                    attachment.image.storage.delete(saved_name)
                except Exception:
                    logging.getLogger(__name__).warning('房间图片回滚清理失败', exc_info=True)
                OrderRoomImageUploadIntent.objects.filter(pk=intent.pk, status='PENDING').update(
                    status='REVIEW', resolved_at=timezone.now())
            if isinstance(exc, (DatabaseError, OSError)):
                logging.getLogger(__name__).warning('房间图片上传结果待核对: intent_id=%s', intent.pk, exc_info=True)
                return Response({'code': 503, 'errorCode': 'IMAGE_RESULT_UNCERTAIN',
                    'msg': '图片结果待核对，请使用原请求编号重试'}, status=503)
            raise
