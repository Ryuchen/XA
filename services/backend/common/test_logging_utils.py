"""结构化日志 Formatter 的单元测试（纯函数，无需数据库）。

资金日志是审计证据：字段名和 JSON 结构一旦漂移，运维侧的采集规则和对账
脚本会静默失配 —— 日志还在打，但没人再解析得出来。这里把契约钉死。
"""

import json
import logging

from django.test import SimpleTestCase

from common.logging_utils import (
    BUSINESS_FIELDS,
    MONEY_LOGGER_NAME,
    JsonLogFormatter,
    log_money_event,
    new_trace_id,
)


def _make_record(msg='hello', **extra):
    record = logging.LogRecord(
        name='xa.money', level=logging.INFO, pathname=__file__,
        lineno=42, msg=msg, args=(), exc_info=None, func='test_func',
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class JsonLogFormatterTest(SimpleTestCase):
    def setUp(self):
        self.formatter = JsonLogFormatter()

    def test_output_is_single_line_json(self):
        output = self.formatter.format(_make_record())
        self.assertNotIn('\n', output)
        json.loads(output)

    def test_common_fields_always_present(self):
        payload = json.loads(self.formatter.format(_make_record('boom')))
        for key in ('ts', 'level', 'logger', 'module', 'message'):
            self.assertIn(key, payload)
        self.assertEqual(payload['level'], 'INFO')
        self.assertEqual(payload['logger'], 'xa.money')
        self.assertEqual(payload['message'], 'boom')

    def test_business_fields_promoted_to_top_level(self):
        payload = json.loads(self.formatter.format(_make_record(
            event='withdraw.submit',
            account_id=7,
            order_no='XA202601010001',
            tx_type='WITHDRAW',
            amount=-5000,
            balance_before=9000,
            balance_after=4000,
            trace_id='abc123',
        )))
        self.assertEqual(payload['event'], 'withdraw.submit')
        self.assertEqual(payload['account_id'], 7)
        self.assertEqual(payload['order_no'], 'XA202601010001')
        self.assertEqual(payload['amount'], -5000)
        self.assertEqual(payload['balance_before'], 9000)
        self.assertEqual(payload['balance_after'], 4000)
        self.assertEqual(payload['trace_id'], 'abc123')

    def test_absent_business_fields_are_omitted(self):
        """普通日志不该被塞满一堆 null 业务字段。"""
        payload = json.loads(self.formatter.format(_make_record()))
        for field in BUSINESS_FIELDS:
            self.assertNotIn(field, payload)

    def test_non_ascii_is_not_escaped(self):
        payload = json.loads(self.formatter.format(_make_record('提现申请')))
        self.assertEqual(payload['message'], '提现申请')

    def test_exception_info_is_captured(self):
        try:
            raise ValueError('exploded')
        except ValueError:
            import sys
            record = _make_record()
            record.exc_info = sys.exc_info()
        payload = json.loads(self.formatter.format(record))
        self.assertIn('ValueError', payload['exc_info'])


class LogMoneyEventTest(SimpleTestCase):
    def test_amount_is_not_divided(self):
        """金额恒为内部账务单位整数，日志层绝不做 ÷10 换算。"""
        with self.assertLogs(MONEY_LOGGER_NAME, level='INFO') as captured:
            log_money_event(
                'order.create', account_id=1, amount=-12345, balance_after=0,
            )
        record = captured.records[0]
        self.assertEqual(record.amount, -12345)
        self.assertEqual(record.event, 'order.create')

    def test_blank_optional_fields_are_dropped(self):
        with self.assertLogs(MONEY_LOGGER_NAME, level='INFO') as captured:
            log_money_event('checkin.reward', account_id=3, amount=10)
        record = captured.records[0]
        self.assertFalse(hasattr(record, 'order_no'))
        self.assertFalse(hasattr(record, 'request_id'))

    def test_zero_amount_is_kept(self):
        """0 是有效金额，不能因为 falsy 被丢掉。"""
        with self.assertLogs(MONEY_LOGGER_NAME, level='INFO') as captured:
            log_money_event('order.settle', account_id=3, amount=0)
        self.assertEqual(captured.records[0].amount, 0)

    def test_trace_ids_are_unique(self):
        self.assertNotEqual(new_trace_id(), new_trace_id())
