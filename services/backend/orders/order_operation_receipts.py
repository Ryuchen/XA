"""Replay a provider's committed order operation without repeating it."""
import hashlib
import json
from functools import wraps

from django.db import transaction
from rest_framework.response import Response

from wallet.models import IdempotencyKey
from .models import Order


def order_receipt(scope, action, fingerprint_for):
    def decorate(view):
        @wraps(view)
        def wrapped(self, request, order_id):
            request_id = request.data.get('client_request_id', '')
            if not request_id:
                return view(self, request, order_id)
            if not isinstance(request_id, str) or len(request_id) > 64:
                return Response({'code': 400, 'msg': '订单操作请求编号无效'})
            fingerprint = fingerprint_for(request, order_id)
            with transaction.atomic():
                order = Order.objects.select_for_update().filter(pk=order_id).first()
                if not order:
                    return Response({'code': 404, 'msg': '订单不存在'})
                receipt, created = IdempotencyKey.objects.get_or_create(
                    account=request.account, request_id=request_id,
                    defaults={'scope': scope, 'request_fingerprint': fingerprint})
                if not created:
                    if receipt.scope != scope or receipt.request_fingerprint != fingerprint:
                        return Response({'code': 409, 'msg': '订单操作请求编号已用于其他操作，请核对订单'}, status=409)
                    if not receipt.response:
                        return Response({'code': 409, 'msg': '上次提交结果待核对，请查看订单'}, status=409)
                    from .serializers import OrderSerializer
                    data = dict(OrderSerializer(order, context={'request': request}).data)
                    data['request_replayed'] = True
                    return Response({'code': 0, 'msg': '本次操作已提交，请核对订单状态', 'data': data})
                result = view(self, request, order_id)
                if not isinstance(result.data, dict) or result.data.get('code') != 0:
                    transaction.set_rollback(True)
                    return result
                receipt.response = {'order_id': order.pk, 'action': action}
                receipt.save(update_fields=['response'])
                return result
        return wrapped
    return decorate


def fulfillment_receipt(action, image_field):
    def fingerprint(request, order_id):
        image = request.FILES.get(image_field)
        digest = hashlib.sha256()
        if image:
            for chunk in image.chunks():
                digest.update(chunk)
            image.seek(0)
        else:
            digest.update(b'no-image')
        return hashlib.sha256(f'{order_id}:{action}:{digest.hexdigest()}'.encode()).hexdigest()
    return order_receipt('order.fulfillment', action, fingerprint)


def grab_receipt(view):
    def fingerprint(request, order_id):
        payload = {'order_id': order_id, 'team_size': request.data.get('team_size', 1),
                   'partner_id': request.data.get('partner_id')}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return order_receipt('order.grab', 'grab', fingerprint)(view)
