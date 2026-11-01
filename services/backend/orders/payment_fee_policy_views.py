"""Operator-owned fee assumptions, separate from live WeChat settlement."""
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from console.pagination import ConsolePagination
from console.permissions import IsConsoleUser, get_account_permissions
from .payment_fee_policy_models import PaymentFeePolicy, PaymentFeePolicyHistory
from .payment_fee_quote import PaymentFeeQuoteError, quote_payment_fee

RATE_DENOMINATOR = 1000000


def _data(row):
    if row is None or row.revision == 0:
        return {'revision': 0, 'configured': False}
    return {'revision': row.revision, 'configured': True, 'merchant_id': row.merchant_id,
            'appid': row.appid, 'rate_percent': f'{Decimal(row.rate_millionths) / Decimal(10000):.4f}',
            'fee_basis': row.fee_basis, 'rounding': row.rounding,
            'updated_at': getattr(row, 'updated_at', getattr(row, 'created_at', None))}


def _parse(data):
    if not isinstance(data, dict) or set(data) != {'revision', 'merchant_id', 'appid', 'rate_percent', 'fee_basis', 'rounding'}:
        raise ValueError('请完整填写商户、费率与计算方式')
    revision = data['revision']
    merchant_id, appid, rate = data['merchant_id'], data['appid'], data['rate_percent']
    if type(revision) is not int or revision < 0 or revision >= 2147483647:
        raise ValueError('配置版本无效')
    if not isinstance(merchant_id, str) or not re.fullmatch(r'[0-9]{8,32}', merchant_id):
        raise ValueError('商户号须为 8–32 位数字')
    if not isinstance(appid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', appid):
        raise ValueError('小程序 AppID 格式无效')
    if not isinstance(rate, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?', rate):
        raise ValueError('费率须为百分数，最多四位小数')
    try:
        rate_value = Decimal(rate)
        if rate_value > 100:
            raise ValueError('费率不得超过 100%')
        numerator = int(rate_value * 10000)
    except InvalidOperation as exc:
        raise ValueError('费率无效') from exc
    if data['fee_basis'] not in ('SERVICE_AMOUNT', 'PAYMENT_TOTAL') or data['rounding'] not in ('DOWN', 'UP', 'HALF_UP'):
        raise ValueError('请选择计算基数和取整方式')
    if data['fee_basis'] == 'PAYMENT_TOTAL' and numerator == RATE_DENOMINATOR:
        raise ValueError('以支付总额为基数时费率须小于 100%')
    return revision, merchant_id, appid, numerator, data['fee_basis'], data['rounding']


class PaymentFeePolicyView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if 'payment_fee:view' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权查看支付费率配置'}, status=403)
        return Response({'code': 0, 'data': _data(PaymentFeePolicy.objects.filter(pk=1).first())})

    def put(self, request):
        if not {'payment_fee:view', 'payment_fee:edit'}.issubset(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权修改支付费率配置'}, status=403)
        try:
            revision, merchant_id, appid, rate, basis, rounding = _parse(request.data)
        except ValueError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        with transaction.atomic():
            PaymentFeePolicy.objects.get_or_create(pk=1)
            row = PaymentFeePolicy.objects.select_for_update().get(pk=1)
            if row.revision != revision:
                return Response({'code': 409, 'errorCode': 'CONFIG_CHANGED', 'msg': '配置已被修改，请刷新后核对'}, status=409)
            row.revision += 1
            row.merchant_id, row.appid, row.rate_millionths = merchant_id, appid, rate
            row.fee_basis, row.rounding = basis, rounding
            row.save()
            PaymentFeePolicyHistory.objects.create(revision=row.revision, merchant_id=merchant_id,
                appid=appid, rate_millionths=rate, fee_basis=basis, rounding=rounding,
                operator_account=request.account, operator_name=(request.account.nickname or request.account.username)[:150])
        return Response({'code': 0, 'data': _data(row)})


class PaymentFeePolicyHistoryView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if 'payment_fee:view' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权查看支付费率配置'}, status=403)
        paginator = ConsolePagination()
        rows = paginator.paginate_queryset(PaymentFeePolicyHistory.objects.order_by('-revision'), request, view=self)
        return paginator.get_paginated_response([{**_data(row), 'operator_name': row.operator_name,
                                                   'updated_at': row.created_at} for row in rows])


class PaymentFeePolicyPreviewView(APIView):
    permission_classes = [IsConsoleUser]

    def post(self, request):
        if 'payment_fee:view' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权查看支付费率配置'}, status=403)
        amount = request.data.get('service_fen')
        if type(amount) is not int or not 0 < amount <= 2147483647:
            return Response({'code': 400, 'msg': '服务金额须为有效的人民币分'}, status=400)
        row = PaymentFeePolicy.objects.filter(pk=1).first()
        if not row or not row.revision:
            return Response({'code': 409, 'msg': '请先配置费率'}, status=409)
        try:
            quote = quote_payment_fee(service_fen=amount, rate_numerator=row.rate_millionths,
                rate_denominator=RATE_DENOMINATOR, fee_basis=row.fee_basis, rounding=row.rounding)
        except PaymentFeeQuoteError:
            return Response({'code': 400, 'msg': '金额超出支持范围'}, status=400)
        return Response({'code': 0, 'data': {'revision': row.revision, 'service_fen': quote.service_fen,
            'fee_fen': quote.estimated_fee_fen, 'total_fen': quote.payment_total_fen}})
