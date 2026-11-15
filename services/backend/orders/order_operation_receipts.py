"""Replay a provider's committed order operation without repeating it."""
import hashlib
import json
from functools import wraps

from stores.db import transaction
from rest_framework.response import Response

from wallet.models import IdempotencyKey
from .models import Order


def order_receipt(scope, action, fingerprint_for, *, prepare=None):
    def decorate(view):
        @wraps(view)
        def wrapped(self, request, order_id):
            if scope == 'order.grab':
                from .grab_single_flow import applies
                if applies(order_id):
                    return view(self, request, order_id)
            request_id = request.data.get('client_request_id', '')
            if not request_id:
                return view(self, request, order_id)
            if not isinstance(request_id, str) or len(request_id) > 64:
                return Response({'code': 400, 'msg': '订单操作请求编号无效'})
            fingerprint = fingerprint_for(request, order_id)
            dispatch_ids = prepare(request) if prepare is not None else ()
            class _GrabReceiptReroute(Exception):
                pass

            try:
                with transaction.atomic():
                    order = Order.objects.select_for_update().filter(pk=order_id).first()
                    if not order:
                        return Response({'code': 404, 'msg': '订单不存在'})
                    if scope == 'order.grab':
                        # Opening and this route check share the order row lock.
                        # Its durable receipt must own a fresh outer transaction.
                        from .grab_single_flow import applies
                        if applies(order_id):
                            raise _GrabReceiptReroute()
                    if scope == 'order.fulfillment' and request.account.account_type == 'PROVIDER':
                        from wallet.report_lifecycle import report_order_cycle_matches
                        if not report_order_cycle_matches(order, request.account):
                            return Response({'code': 409, 'msg': '订单陪玩所属周期待核对，请联系客服'}, status=409)
                    if dispatch_ids:
                        # A new receipt also takes an FK shared lock on its
                        # provider account. Take the capacity mutex first, or a
                        # concurrent ban (mutex -> account) can deadlock here.
                        from .services import lock_provider_dispatch
                        lock_provider_dispatch(dispatch_ids)
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
            except _GrabReceiptReroute:
                # Leave the legacy receipt transaction before recording the
                # real database-timed grab intent.
                return view(self, request, order_id)
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
    def prepare(request):
        if action != 'start' or request.account.account_type != 'PROVIDER':
            return ()
        from .services import ensure_provider_dispatch_locks
        ensure_provider_dispatch_locks([request.account.pk])
        return [request.account.pk]
    def decorate(view):
        receipt_view = order_receipt('order.fulfillment', action, fingerprint, prepare=prepare)(view)
        from wallet.report_lifecycle import provider_report_operation
        protected_view = provider_report_operation(receipt_view)

        @wraps(view)
        def wrapped(self, request, order_id):
            from django.conf import settings
            # Initializing capacity rows may insert an account FK. Do this in
            # autocommit before the lifecycle/receipt transaction and locks.
            if (getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                    and action == 'start' and request.account.account_type == 'PROVIDER'):
                from .services import ensure_provider_dispatch_locks
                ensure_provider_dispatch_locks([request.account.pk])
            return protected_view(self, request, order_id)
        return wrapped
    return decorate


def grab_receipt(view):
    def prepare(request):
        # The receipt takes the order lock before calling the view. Initialize
        # capacity rows here, outside that transaction, including a team mate
        # for public orders that delegate to ApplyOrderView.
        if request.account.account_type != 'PROVIDER':
            return
        from club_accounts.models import ClubAccount
        from .services import ensure_provider_dispatch_locks
        ids = [request.account.pk]
        partner_id = request.data.get('partner_id')
        if (request.data.get('team_size', 1) == 2 and type(partner_id) is int
                and ClubAccount.objects.filter(pk=partner_id, account_type='PROVIDER').exists()):
            ids.append(partner_id)
        ensure_provider_dispatch_locks(ids)
        return ids

    def fingerprint(request, order_id):
        payload = {'order_id': order_id, 'team_size': request.data.get('team_size', 1),
                   'partner_id': request.data.get('partner_id')}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return order_receipt('order.grab', 'grab', fingerprint, prepare=prepare)(view)
