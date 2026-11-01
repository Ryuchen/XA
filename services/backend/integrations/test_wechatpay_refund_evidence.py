import base64
import json
import time
import unittest
from datetime import datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from integrations.wechatpay_gateway import (
    TransportResponse, WeChatPayGateway, WeChatPayInputError, WeChatPayOutcomeUnknown,
)
from integrations.wechatpay_refund_evidence import (
    parse_refund_notification, query_refund_evidence, submit_refund_evidence,
)
from integrations.wechatpay_v3 import WeChatPayProtocolError, WeChatPayV3
from orders.direct_refund import VerifiedRefund


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.response = None

    def send(self, method, path, headers, body, *, timeout, max_response_bytes):
        self.calls.append((method, path, body))
        return self.response


class WeChatPayRefundEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.key = b'x' * 32
        merchant_pem = cls.merchant.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        platform_pem = cls.platform.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.protocol = WeChatPayV3(app_id='wx-test', merchant_id='123456', merchant_serial='merchant-serial',
            merchant_private_key=merchant_pem, api_v3_key=cls.key, platform_keys={'PUB_KEY_ID_test': platform_pem})

    def setUp(self):
        self.transport = FakeTransport()
        self.gateway = WeChatPayGateway(self.protocol, transport=self.transport)

    def signed(self, body):
        timestamp = str(int(time.time()))
        signature = self.platform.sign((timestamp + '\nnonce\n').encode() + body + b'\n',
                                       padding.PKCS1v15(), hashes.SHA256())
        return {'Wechatpay-Serial': 'PUB_KEY_ID_test', 'Wechatpay-Timestamp': timestamp,
                'Wechatpay-Nonce': 'nonce', 'Wechatpay-Signature': base64.b64encode(signature).decode()}

    def payload(self, status='SUCCESS'):
        data = {'mchid': '123456', 'out_trade_no': 'XA-order123', 'transaction_id': 'wx-transaction',
                'out_refund_no': 'XR-refund123', 'refund_id': 'wx-refund', 'refund_status': status,
                'amount': {'total': 10000, 'refund': 10000, 'payer_total': 9000, 'payer_refund': 9000}}
        if status == 'SUCCESS':
            data['success_time'] = '2026-10-27T18:30:45+08:00'
        return data

    def notice(self, data, event='REFUND.SUCCESS'):
        plain = json.dumps(data).encode()
        encrypted = AESGCM(self.key).encrypt(b'123456789012', plain, b'refund')
        body = json.dumps({'id': 'notice-123', 'event_type': event, 'resource_type': 'encrypt-resource',
            'resource': {'original_type': 'refund', 'algorithm': 'AEAD_AES_256_GCM',
                'nonce': '123456789012', 'associated_data': 'refund',
                'ciphertext': base64.b64encode(encrypted).decode()}}).encode()
        return self.protocol.decrypt_notification(self.signed(body), body)

    def query_response(self, status='SUCCESS'):
        data = self.payload(status)
        del data['mchid']  # Direct refund API response has no merchant field.
        data['status'] = data.pop('refund_status')
        data['amount']['currency'] = 'CNY'
        data['amount'].update(settlement_total=9500, settlement_refund=9500, discount_refund=1000)
        body = json.dumps(data).encode()
        return TransportResponse(200, self.signed(body), body)

    def test_notification_has_no_currency_and_query_supplies_authenticated_evidence(self):
        notice = parse_refund_notification(self.notice(self.payload()))
        self.assertEqual((notice.notification_id, notice.amount_fen, notice.payer_refund_fen), ('notice-123', 10000, 9000))
        self.assertFalse(hasattr(notice, 'currency'))
        self.transport.response = self.query_response()
        bundle = query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123', notification=notice)
        evidence = bundle.verified_refund
        self.assertIsInstance(evidence, VerifiedRefund)
        self.assertEqual((evidence.mchid, evidence.currency, evidence.status), ('123456', 'CNY', 'SUCCESS'))
        self.assertEqual((bundle.payer_total_fen, bundle.payer_refund_fen, bundle.notification_id), (9000, 9000, 'notice-123'))
        self.assertEqual((evidence.payer_total_fen, evidence.payer_refund_fen), (9000, 9000))
        self.assertEqual((evidence.merchant_order_no, evidence.transaction_id, evidence.amount_fen, evidence.total_fen),
                         ('XA-order123', 'wx-transaction', 10000, 10000))
        self.assertEqual(evidence.success_at.isoformat(), '2026-10-27T18:30:45+08:00')
        self.assertEqual(len(self.transport.calls), 1)

    def test_processing_acceptance_stays_processing_and_query_confirms_terminal_state(self):
        self.transport.response = self.query_response('PROCESSING')
        bundle = submit_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123',
            amount_fen=10000, total_fen=10000, out_trade_no='XA-order123')
        evidence = bundle.verified_refund
        self.assertEqual((evidence.status, evidence.success_at), ('PROCESSING', None))
        self.assertEqual((bundle.payer_total_fen, bundle.payer_refund_fen, bundle.notification_id), (9000, 9000, None))
        self.assertEqual((evidence.payer_total_fen, evidence.payer_refund_fen), (9000, 9000))
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.transport.calls[0][0], 'POST')
        self.transport.response = self.query_response('SUCCESS')
        self.assertEqual(query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123').verified_refund.status, 'SUCCESS')

    def test_wrong_merchant_is_refused_before_network(self):
        with self.assertRaises(WeChatPayInputError):
            query_refund_evidence(self.gateway, intent_mchid='other-merchant', intent_appid='wx-test', out_refund_no='XR-refund123')
        with self.assertRaises(WeChatPayInputError):
            submit_refund_evidence(self.gateway, intent_mchid='other-merchant', intent_appid='wx-test', out_refund_no='XR-refund123',
                amount_fen=10000, total_fen=10000, out_trade_no='XA-order123')
        with self.assertRaises(WeChatPayInputError):
            query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='other-app', out_refund_no='XR-refund123')
        self.assertEqual(self.transport.calls, [])

    def test_rejects_mismatched_event_state_and_invalid_amounts(self):
        for status in ('CLOSED', 'ABNORMAL'):
            event = 'REFUND.' + status
            notice = parse_refund_notification(self.notice(self.payload(status), event=event))
            self.assertEqual((notice.status, notice.success_at), (status, None))
        changes = [
            {'refund_status': 'CLOSED'},
            {'amount': {'total': 10000, 'refund': True, 'payer_total': 9000, 'payer_refund': 9000}},
            {'amount': {'total': 10000, 'refund': 10000, 'payer_total': 9000, 'payer_refund': 9001}},
            {'amount': {'total': 10000, 'refund': 10001, 'payer_total': 9000, 'payer_refund': 9000}},
            {'success_time': '2026-10-27T18:30:45'},
            {'success_time': 'not-a-time'},
            {'transaction_id': ''},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(WeChatPayOutcomeUnknown):
                parse_refund_notification(self.notice({**self.payload(), **change}))

    def test_submicrosecond_timestamps_are_never_silently_truncated(self):
        payload = self.payload()
        payload['success_time'] = '2026-10-27T18:30:45.123456+08:00'
        notice = parse_refund_notification(self.notice(payload))
        self.assertEqual(notice.success_at.microsecond, 123456)
        original = self.query_response()
        data = json.loads(original.body)
        data['success_time'] = payload['success_time']
        body = json.dumps(data).encode()
        self.transport.response = TransportResponse(200, self.signed(body), body)
        result = query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test',
                                       out_refund_no='XR-refund123', notification=notice)
        self.assertEqual(result.verified_refund.success_at.microsecond, 123456)
        for fractional in ('1234561', '1234560'):
            bad = {**payload, 'success_time': f'2026-10-27T18:30:45.{fractional}+08:00'}
            with self.subTest(fractional=fractional, source='notification'), self.assertRaises(WeChatPayOutcomeUnknown):
                parse_refund_notification(self.notice(bad))
            query_data = {**data, 'success_time': bad['success_time']}
            body = json.dumps(query_data).encode()
            self.transport.response = TransportResponse(200, self.signed(body), body)
            with self.subTest(fractional=fractional, source='query'), self.assertRaises(WeChatPayOutcomeUnknown):
                query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test',
                                      out_refund_no='XR-refund123', notification=notice)

    def test_conflicting_or_unsigned_channel_evidence_never_becomes_a_refund(self):
        notice = parse_refund_notification(self.notice(self.payload()))
        original = self.query_response()
        data = json.loads(original.body)
        for change in ({'transaction_id': 'wrong'}, {'amount': {'total': 10000, 'refund': 9999, 'currency': 'CNY'}},
                       {'amount': {'total': 10000, 'refund': 10000, 'payer_total': 9000, 'payer_refund': 8000, 'currency': 'CNY'}},
                       {'amount': {'total': 10000, 'refund': 10000, 'payer_total': 9500, 'payer_refund': 9000, 'currency': 'CNY'}},
                       {'amount': {'total': 10000, 'refund': 10000}}, {'status': 'PROCESSING', 'success_time': None},
                       {'mchid': 'other'}):
            body = json.dumps({**data, **change}).encode()
            self.transport.response = TransportResponse(200, self.signed(body), body)
            with self.subTest(change=change), self.assertRaises(WeChatPayOutcomeUnknown):
                query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123', notification=notice)
        self.transport.response = TransportResponse(200, {}, original.body)
        with self.assertRaises(WeChatPayOutcomeUnknown):
            query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123')

    def test_cash_amounts_are_required_from_both_signed_response_paths(self):
        response = self.query_response('PROCESSING')
        data = json.loads(response.body)
        for field, invalid in (('payer_total', None), ('payer_refund', None),
                               ('payer_total', True), ('payer_refund', False)):
            amount = {**data['amount'], field: invalid}
            body = json.dumps({**data, 'amount': amount}).encode()
            self.transport.response = TransportResponse(200, self.signed(body), body)
            with self.subTest(field=field, invalid=invalid, operation='query'), self.assertRaises(WeChatPayOutcomeUnknown):
                query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123')
            with self.subTest(field=field, invalid=invalid, operation='submit'), self.assertRaises(WeChatPayOutcomeUnknown):
                submit_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123',
                                       amount_fen=10000, total_fen=10000, out_trade_no='XA-order123')

    def test_signed_response_must_match_requested_refund(self):
        response = self.query_response('PROCESSING')
        data = json.loads(response.body)
        for change in ({'out_refund_no': 'XR-wrong'}, {'out_trade_no': 'wrong-order'},
                       {'amount': {**data['amount'], 'total': 11000}}):
            body = json.dumps({**data, **change}).encode()
            self.transport.response = TransportResponse(200, self.signed(body), body)
            with self.subTest(change=change, operation='submit'), self.assertRaises(WeChatPayOutcomeUnknown):
                submit_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123',
                                       amount_fen=10000, total_fen=10000, out_trade_no='XA-order123')
            if 'out_refund_no' in change:
                with self.subTest(change=change, operation='query'), self.assertRaises(WeChatPayOutcomeUnknown):
                    query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123')

    def test_settlement_fields_preserved_from_signed_query_without_inference(self):
        notice = parse_refund_notification(self.notice(self.payload()))
        response = self.query_response()
        data = json.loads(response.body)
        data['amount'].update(settlement_total=9500, settlement_refund=9500, discount_refund=1000)
        body = json.dumps(data).encode()
        self.transport.response = TransportResponse(200, self.signed(body), body)
        bundle = query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test',
                                       out_refund_no='XR-refund123', notification=notice)
        self.assertEqual((bundle.settlement_total_fen, bundle.settlement_refund_fen, bundle.discount_refund_fen),
                         (9500, 9500, 1000))
        self.assertEqual((bundle.verified_refund.settlement_total_fen, bundle.verified_refund.settlement_refund_fen,
                          bundle.verified_refund.discount_refund_fen), (9500, 9500, 1000))
        for name, bad in (('settlement_total', None), ('settlement_refund', True), ('discount_refund', -1),
                          ('settlement_total', 10001), ('settlement_refund', 10001), ('discount_refund', 10001)):
            changed = json.loads(response.body)
            changed['amount'][name] = bad
            body = json.dumps(changed).encode()
            self.transport.response = TransportResponse(200, self.signed(body), body)
            with self.subTest(name=name, bad=bad), self.assertRaises(WeChatPayOutcomeUnknown):
                query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123')
        for name in ('settlement_total', 'settlement_refund', 'discount_refund'):
            changed = json.loads(response.body)
            del changed['amount'][name]
            body = json.dumps(changed).encode()
            self.transport.response = TransportResponse(200, self.signed(body), body)
            with self.subTest(missing=name), self.assertRaises(WeChatPayOutcomeUnknown):
                query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123')

    def test_abnormal_can_be_followed_by_success_but_closed_is_terminal(self):
        self.transport.response = self.query_response('SUCCESS')
        abnormal = parse_refund_notification(self.notice(self.payload('ABNORMAL'), event='REFUND.ABNORMAL'))
        self.assertEqual(query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123', notification=abnormal).verified_refund.status, 'SUCCESS')
        closed = parse_refund_notification(self.notice(self.payload('CLOSED'), event='REFUND.CLOSED'))
        with self.assertRaises(WeChatPayOutcomeUnknown):
            query_refund_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test', out_refund_no='XR-refund123', notification=closed)

    def test_unsigned_or_tampered_notification_never_decrypts(self):
        data = self.payload()
        body = json.dumps({'id': 'notice-123', 'event_type': 'REFUND.SUCCESS', 'resource_type': 'encrypt-resource',
            'resource': {'original_type': 'refund', 'algorithm': 'AEAD_AES_256_GCM', 'nonce': '123456789012',
                         'associated_data': 'refund', 'ciphertext': base64.b64encode(AESGCM(self.key).encrypt(b'123456789012', json.dumps(data).encode(), b'refund')).decode()}}).encode()
        with self.assertRaises(WeChatPayProtocolError):
            self.protocol.decrypt_notification(self.signed(body), body + b' ')
