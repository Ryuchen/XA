"""Atomic multi-gift checkout using the same ledger path as a single gift."""
import hashlib
import json
from django.db import transaction
from rest_framework.response import Response
from club_accounts.models import ClubAccount
from wallet.receipts import fund_success
from wallet.services import claim_fund_write, get_wallet
from .models import Order, ServiceItem
from .gifts import MAX_GIFT_QUANTITY, MAX_LEDGER_AMOUNT, gift_available


class LineRequest:
    def __init__(self, request, data):
        self.original, self.data = request, data
    def __getattr__(self, key):
        return getattr(self.original, key)


class BasketRejected(Exception):
    def __init__(self, response):
        self.response = response


def send_basket(view, request, *, order_id=None):
    if request.account.account_type != ClubAccount.AccountType.BOSS:
        return Response({'code': 403, 'msg': '仅老板可以赠送礼物'})
    items = request.data.get('items')
    request_id = request.data.get('client_request_id')
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
        return Response({'code': 400, 'msg': '请重新确认赠礼操作'})
    if not isinstance(items, list) or not 1 <= len(items) <= 30:
        return Response({'code': 400, 'msg': '请选择 1 至 30 款礼物'})
    quantities = {}
    for item in items:
        if not isinstance(item, dict) or type(item.get('gift_service_id')) is not int or not 1 <= item['gift_service_id'] < 2**63 or type(item.get('quantity')) is not int or not 1 <= item['quantity'] <= MAX_GIFT_QUANTITY:
            return Response({'code': 400, 'msg': '礼物或赠送数量无效'})
        pk = item['gift_service_id']
        quantities[pk] = quantities.get(pk, 0) + item['quantity']
        if quantities[pk] > MAX_GIFT_QUANTITY:
            return Response({'code': 400, 'msg': '单款礼物最多赠送 999 件'})
    try:
        with transaction.atomic():
            if order_id is not None:
                # Match single gifts and source-order refunds before wallets.
                list(Order.objects.select_for_update().filter(pk=order_id, customer_account=request.account))
            from .financial_lock import lock_order_money
            lock_order_money()
            wallet = get_wallet(account=request.account, user=request.legacy_user, for_update=True)
            services = list(ServiceItem.objects.select_for_update().select_related('service_category').filter(
                pk__in=quantities, is_active=True, service_category__is_active=True, service_category__is_gift=True,
                purchase_mode='DIRECT', is_addon=False, gameplay__isnull=True).order_by('pk'))
            if len(services) != len(quantities) or any(not gift_available(item) for item in services):
                return Response({'code': 400, 'msg': '部分礼物已下架或不在活动时间内，请重新选择'})
            total = sum(item.price * quantities[item.pk] for item in services)
            expected = request.data.get('expected_amount')
            if type(expected) is not int or expected != total:
                return Response({'code': 409, 'errorCode': 'PRICE_CHANGED', 'msg': '礼物价格已变化，请刷新后重新确认'})
            if not 0 < total <= MAX_LEDGER_AMOUNT or not wallet.is_active or wallet.balance < total:
                return Response({'code': 400, 'msg': '钱包不可用或余额不足，请核对赠礼金额'})
            allowed, reason = claim_fund_write(account=request.account, request_id=request_id, scope='order.tip',
                fingerprint=json.dumps({'items': quantities, 'provider': request.data.get('provider_id'), 'source': order_id, 'total': total}, sort_keys=True))
            if not allowed:
                return Response({'code': 409, 'msg': reason})
            orders = []
            for item in services:
                payload = {**request.data, 'gift_service_id': item.pk, 'quantity': quantities[item.pk],
                    'expected_amount': item.price * quantities[item.pk],
                    'client_request_id': 'giftline:' + hashlib.sha256(f'{request_id}:{item.pk}'.encode()).hexdigest()}
                payload.pop('items', None)
                response = view.create_one(LineRequest(request, payload), order_id)
                if response.data.get('code') != 0:
                    raise BasketRejected(response)
                orders.append(response.data['data'])
            return fund_success(request, 'order.tip', msg='礼物已全部送达', data={
                'orders': orders, 'amount': total, 'quantity': sum(quantities.values()),
                'naming_days': sum(item.gift_naming_days * quantities[item.pk] for item in services),
            })
    except BasketRejected as error:
        return error.response
