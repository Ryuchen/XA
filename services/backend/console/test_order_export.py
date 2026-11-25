import csv
import hashlib
import io
import json
from datetime import datetime, timezone

from django.test import TestCase, override_settings

from .order_export import (
    audit_filters,
    build_order_csv,
    export_filter_facts,
    filters_digest,
    mask_phone,
    store_coin_text,
)


class OrderExportTest(TestCase):
    def row(self):
        return {
            'order_no': '=HYPERLINK("https://example.invalid")',
            'created_at': '2026-11-25T10:00:00+08:00',
            'status_display': '服务中',
            'payment_status_display': '已支付',
            'settlement_status': 'WAITING',
            'dispatch_route': 'STAFF',
            'service_name_snapshot': '英雄联盟陪玩',
            'gameplay_snapshot': '排位',
            'escort_mode_display': '双陪',
            'customer_entity_no': 'B0001',
            'customer_nickname': '测试老板',
            'customer_phone': '13800138000',
            'providers': [{'name': '陪玩甲'}, {'name': '陪玩乙'}, {'name': '陪玩甲'}],
            'original_amount': 1200,
            'amount': 1000,
            'refund_summary': {'amount': 510, 'remaining_amount': 490, 'refund_count': 2},
            'remark': '@SUM(1,2)',
            'cancel_reason': '',
        }

    def parse(self, content):
        text = content.decode('utf-8-sig')
        return list(csv.reader(io.StringIO(text)))

    def test_phone_masking_never_returns_a_full_identifier(self):
        self.assertEqual(mask_phone('13800138000'), '138****8000')
        self.assertEqual(mask_phone('+86 138 0013 8000'), '138****8000')
        self.assertEqual(mask_phone('12345'), '***')
        self.assertEqual(mask_phone(None), '')

    def test_store_coin_values_use_integer_units_and_keep_unknown_blank(self):
        self.assertEqual(store_coin_text(1250), '125')
        self.assertEqual(store_coin_text(51), '5.1')
        self.assertEqual(store_coin_text(0), '0')
        self.assertEqual(store_coin_text(None), '')
        for value in ('not-a-number', '1.5', 'NaN', 'Infinity', True, {}, []):
            self.assertEqual(store_coin_text(value), '')

    def test_export_masks_phone_and_neutralizes_spreadsheet_formulas(self):
        content = build_order_csv(
            [self.row()], store_slug='xinan', store_name='兴安电竞',
            operator_name='测试运营', operator_id=7, export_id='ORDER-EXPORT-9',
            exported_at=datetime(2026, 11, 25, 10, 30, tzinfo=timezone.utc),
            filters={'status': 'IN_SERVICE'},
        )
        rows = self.parse(content)
        self.assertEqual(rows[0], ['文件', 'XA 订单明细导出'])
        self.assertIn(['导出凭证', 'ORDER-EXPORT-9'], rows)
        self.assertIn(['记录数', '1'], rows)
        header = rows[9]
        data = dict(zip(header, rows[10]))
        self.assertEqual(data['老板手机号（脱敏）'], '138****8000')
        self.assertEqual(data['原价（店币）'], '120')
        self.assertEqual(data['成交金额（店币）'], '100')
        self.assertEqual(data['已退金额（店币）'], '51')
        self.assertEqual(data['剩余可退（店币）'], '49')
        self.assertEqual(data['陪玩'], '陪玩甲、陪玩乙')
        self.assertTrue(data['订单号'].startswith("'="))
        self.assertTrue(data['订单备注'].startswith("'@"))
        self.assertNotIn('13800138000', content.decode('utf-8-sig'))

    def test_unknown_financial_values_are_not_rendered_as_zero(self):
        row = self.row()
        row['refund_summary'] = None
        row['original_amount'] = None
        row['amount'] = None
        rows = self.parse(build_order_csv(
            [row], store_slug='xinan', store_name='兴安电竞',
            operator_name='测试运营', operator_id=7, export_id='ORDER-EXPORT-10',
            exported_at=datetime(2026, 11, 25, tzinfo=timezone.utc), filters={},
        ))
        data = dict(zip(rows[9], rows[10]))
        self.assertEqual(data['原价（店币）'], '')
        self.assertEqual(data['成交金额（店币）'], '')
        self.assertEqual(data['已退金额（店币）'], '')
        self.assertEqual(data['剩余可退（店币）'], '')
        self.assertEqual(data['退款笔数'], '')

    def test_audit_filters_redact_search_identifiers_and_digest_is_stable(self):
        filters = audit_filters({
            'status': 'IN_SERVICE', 'keyword': '13800138000',
            'customer_phone': '13800138000', 'order_no_exact': 'XA123',
            'customer_account_id': '20', 'customer_username': 'boss-secret',
            'customer_nickname': '老板昵称',
            'created_day': '2026-11-25',
        })
        self.assertEqual(filters['keyword'], '***')
        self.assertEqual(filters['customer_phone'], '***')
        self.assertEqual(filters['order_no_exact'], '***')
        self.assertEqual(filters['customer_account_id'], '***')
        self.assertEqual(filters['customer_username'], '***')
        self.assertEqual(filters['customer_nickname'], '***')
        self.assertEqual(filters['status'], 'IN_SERVICE')
        self.assertNotIn('13800138000', str(filters))
        self.assertNotIn('boss-secret', str(filters))
        self.assertNotIn('老板昵称', str(filters))
        self.assertEqual(filters_digest(filters), filters_digest(dict(reversed(list(filters.items())))))

    def test_digest_distinguishes_redacted_identifiers_without_persisting_them(self):
        first = export_filter_facts({'customer_phone': '13800138000', 'order_no_exact': 'XA123'})
        second = export_filter_facts({'customer_phone': '13900139000', 'order_no_exact': 'XA124'})
        self.assertNotEqual(filters_digest(first), filters_digest(second))
        self.assertEqual(audit_filters({'customer_phone': '13800138000'}),
                         audit_filters({'customer_phone': '13900139000'}))
        self.assertNotIn('13800138000', str(audit_filters({'customer_phone': '13800138000'})))

    def test_digest_is_keyed_and_cannot_be_recomputed_with_plain_sha256(self):
        filters = export_filter_facts({'customer_phone': '13800138000'})
        payload = json.dumps(filters, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        plain_digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        digest = filters_digest(filters)
        self.assertNotEqual(digest, plain_digest)
        with override_settings(SECRET_KEY='another-server-secret-for-order-export-digest'):
            self.assertNotEqual(filters_digest(filters), digest)
