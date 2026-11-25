import csv
import io
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils.crypto import salted_hmac


EXPORT_COLUMNS = (
    ('order_no', '订单号'),
    ('store_slug', '门店'),
    ('created_at', '创建时间'),
    ('status_display', '订单状态'),
    ('payment_status_display', '支付状态'),
    ('settlement_status', '结算状态'),
    ('dispatch_route', '派单路径'),
    ('service_name_snapshot', '服务名称'),
    ('gameplay_snapshot', '玩法'),
    ('escort_mode_display', '陪玩人数'),
    ('customer_entity_no', '老板编号'),
    ('customer_nickname', '老板昵称'),
    ('customer_phone', '老板手机号（脱敏）'),
    ('provider_names', '陪玩'),
    ('original_amount', '原价（店币）'),
    ('amount', '成交金额（店币）'),
    ('refund_amount', '已退金额（店币）'),
    ('remaining_amount', '剩余可退（店币）'),
    ('refund_count', '退款笔数'),
    ('remark', '订单备注'),
    ('cancel_reason', '取消原因'),
)

_FILTER_DIGEST_SALT = 'xa.order-export.filters.v1'

_AUDIT_FILTER_KEYS = (
    'order_kind', 'status', 'payment_status', 'dispatch_route',
    'exclude_direct_test', 'overdue_pending', 'legacy_team',
    'aftersale_open', 'actionable_staff', 'created_day', 'refund_day',
)


def mask_phone(value):
    text = str(value or '').strip()
    if not text:
        return ''
    digits = re.sub(r'\D', '', text)
    if len(digits) == 13 and digits.startswith('86'):
        digits = digits[2:]
    if len(digits) == 11:
        return f'{digits[:3]}****{digits[-4:]}'
    if len(digits) >= 7:
        return f'{digits[:3]}****{digits[-3:]}'
    return '***'


_SENSITIVE_FILTER_KEYS = (
    'keyword', 'order_no', 'order_no_exact', 'customer_phone',
    'customer_account_id', 'customer_username', 'customer_nickname',
)


def export_filter_facts(query_params):
    result = {
        key: query_params.get(key)
        for key in _AUDIT_FILTER_KEYS
        if query_params.get(key) not in (None, '')
    }
    for key in _SENSITIVE_FILTER_KEYS:
        if query_params.get(key) not in (None, ''):
            result[key] = query_params.get(key)
    return result


def audit_filters(query_params):
    result = export_filter_facts(query_params)
    for key in _SENSITIVE_FILTER_KEYS:
        if key in result:
            result[key] = '***'
    return result


def filters_digest(filters):
    payload = json.dumps(filters, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return salted_hmac(_FILTER_DIGEST_SALT, payload, algorithm='sha256').hexdigest()


def store_coin_text(value):
    if value is None or value == '':
        return ''
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ''
    if not amount.is_finite() or amount != amount.to_integral_value():
        return ''
    return format(amount / Decimal('10'), 'f')


def _csv_text(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return '是' if value else '否'
    text = str(value)
    if text.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + text
    return text


def _provider_names(row):
    providers = row.get('providers') or []
    names = []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        name = provider.get('name') or provider.get('display_name') or provider.get('provider_name')
        if name and name not in names:
            names.append(str(name))
    if not names and row.get('provider_name'):
        names.append(str(row['provider_name']))
    return '、'.join(names)


def _row_values(row, store_slug):
    refund = row.get('refund_summary') if isinstance(row.get('refund_summary'), dict) else {}
    values = {
        'order_no': row.get('order_no'),
        'store_slug': store_slug,
        'created_at': row.get('created_at'),
        'status_display': row.get('status_display') or row.get('status'),
        'payment_status_display': row.get('payment_status_display') or row.get('payment_status'),
        'settlement_status': row.get('settlement_status'),
        'dispatch_route': row.get('dispatch_route'),
        'service_name_snapshot': row.get('service_name_snapshot'),
        'gameplay_snapshot': row.get('gameplay_snapshot'),
        'escort_mode_display': row.get('escort_mode_display') or row.get('escort_mode'),
        'customer_entity_no': row.get('customer_entity_no'),
        'customer_nickname': row.get('customer_nickname') or row.get('customer_name'),
        'customer_phone': mask_phone(row.get('customer_phone') or row.get('customer_phone_snapshot')),
        'provider_names': _provider_names(row),
        'original_amount': store_coin_text(row.get('original_amount')),
        'amount': store_coin_text(row.get('amount')),
        'refund_amount': store_coin_text(refund.get('amount')),
        'remaining_amount': store_coin_text(refund.get('remaining_amount')),
        'refund_count': refund.get('refund_count') if refund else '',
        'remark': row.get('remark'),
        'cancel_reason': row.get('cancel_reason'),
    }
    return [values[key] for key, _ in EXPORT_COLUMNS]


def build_order_csv(rows, *, store_slug, store_name, operator_name, operator_id,
                    export_id, exported_at, filters):
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    watermark = (
        ('文件', 'XA 订单明细导出'),
        ('门店', f'{store_name}（{store_slug}）'),
        ('操作人', f'{operator_name}（账户 {operator_id}）'),
        ('导出时间', exported_at.isoformat() if isinstance(exported_at, datetime) else str(exported_at)),
        ('导出凭证', export_id),
        ('筛选摘要', filters_digest(filters)),
        ('记录数', len(rows)),
        ('保密声明', '本文件含脱敏业务信息，仅限授权人员在批准范围内使用。'),
    )
    for label, value in watermark:
        writer.writerow([_csv_text(label), _csv_text(value)])
    writer.writerow([])
    writer.writerow([label for _, label in EXPORT_COLUMNS])
    for row in rows:
        writer.writerow([_csv_text(value) for value in _row_values(row, store_slug)])
    return b'\xef\xbb\xbf' + output.getvalue().encode('utf-8')
