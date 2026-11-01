import base64
import json
import time
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from integrations.wechatpay_gateway import (
    TransportResponse, WeChatPayGateway, WeChatPayInputError, WeChatPayOutcomeUnknown,
)
from integrations.wechatpay_payment_evidence import parse_payment_notification, query_payment_evidence
from integrations.wechatpay_v3 import WeChatPayProtocolError, WeChatPayV3


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.response = None

    def send(self, method, path, headers, body, *, timeout, max_response_bytes):
        self.calls.append((method, path, body))
        return self.response


class PaymentEvidenceTest(unittest.TestCase):
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

    def sign(self, body):
        timestamp = str(int(time.time()))
        signature = self.platform.sign((timestamp + '\nnonce\n').encode() + body + b'\n', padding.PKCS1v15(), hashes.SHA256())
        return {'Wechatpay-Serial': 'PUB_KEY_ID_test', 'Wechatpay-Timestamp': timestamp,
                'Wechatpay-Nonce': 'nonce', 'Wechatpay-Signature': base64.b64encode(signature).decode()}

    def payload(self):
        return {'appid': 'wx-test', 'mchid': '123456', 'out_trade_no': 'XA-order123',
                'transaction_id': 'wx-transaction', 'trade_type': 'JSAPI', 'trade_state': 'SUCCESS',
                'success_time': '2026-10-27T18:30:45+08:00', 'payer': {'openid': 'test-openid'},
                'amount': {'total': 10000, 'payer_total': 9000, 'currency': 'CNY', 'payer_currency': 'CNY'},
                'promotion_detail': [{'coupon_id': 'voucher', 'name': 'coupon', 'amount': 1000}]}

    def notice(self, payload):
        plain = json.dumps(payload).encode()
        ciphertext = AESGCM(self.key).encrypt(b'123456789012', plain, b'payment')
        body = json.dumps({'id': 'notice-123', 'event_type': 'TRANSACTION.SUCCESS', 'resource_type': 'encrypt-resource',
            'resource': {'original_type': 'transaction', 'algorithm': 'AEAD_AES_256_GCM',
                         'nonce': '123456789012', 'associated_data': 'payment',
                         'ciphertext': base64.b64encode(ciphertext).decode()}}).encode()
        return self.protocol.decrypt_notification(self.sign(body), body)

    def set_response(self, payload, signed=True):
        body = json.dumps(payload).encode()
        self.transport.response = TransportResponse(200, self.sign(body) if signed else {}, body)

    def query(self, notification=None):
        return query_payment_evidence(self.gateway, intent_mchid='123456', intent_appid='wx-test',
                                      out_trade_no='XA-order123', notification=notification)

    def test_notice_and_signed_query_preserve_actual_payer_amount_and_event_id(self):
        notice = parse_payment_notification(self.notice(self.payload()))
        self.set_response(self.payload())
        result = self.query(notice)
        self.assertEqual(result.notification_id, 'notice-123')
        p = result.verified_payment
        self.assertEqual((p.appid, p.mchid, p.merchant_order_no, p.transaction_id, p.openid),
                         ('wx-test', '123456', 'XA-order123', 'wx-transaction', 'test-openid'))
        self.assertEqual((p.amount_fen, p.payer_total_fen, p.currency), (10000, 9000, 'CNY'))
        self.assertEqual((p.payer_currency, p.promotion_detail), ('CNY', [{'coupon_id': 'voucher', 'name': 'coupon', 'amount': 1000}]))
        self.assertEqual(p.paid_at.isoformat(), '2026-10-27T18:30:45+08:00')
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.transport.calls[0][0], 'GET')

    def test_query_alone_and_fully_discounted_payment(self):
        payload = self.payload()
        payload['amount']['payer_total'] = 0
        self.set_response(payload)
        result = self.query()
        self.assertIsNone(result.notification_id)
        self.assertEqual(result.verified_payment.payer_total_fen, 0)

    def test_each_business_identity_or_amount_mismatch_is_rejected(self):
        notice = parse_payment_notification(self.notice(self.payload()))
        changes = [ {'appid': 'other'}, {'mchid': 'other'}, {'out_trade_no': 'XA-other'},
                    {'transaction_id': 'other'}, {'payer': {'openid': 'other'}},
                    {'amount': {'total': 10001, 'payer_total': 9000, 'currency': 'CNY', 'payer_currency': 'CNY'}},
                    {'amount': {'total': 10000, 'payer_total': 8999, 'currency': 'CNY', 'payer_currency': 'CNY'}},
                    {'amount': {'total': 10000, 'payer_total': 9000, 'currency': 'CNY', 'payer_currency': 'USD'}},
                    {'promotion_detail': []},
                    {'success_time': '2026-10-27T18:30:46+08:00'}, {'trade_type': 'NATIVE'},
                    {'trade_state': 'REFUND'} ]
        for change in changes:
            self.set_response({**self.payload(), **change})
            with self.subTest(change=change), self.assertRaises(WeChatPayOutcomeUnknown):
                self.query(notice)

    def test_missing_or_invalid_fields_never_create_success_evidence(self):
        changes = [{'transaction_id': ''}, {'payer': {}}, {'trade_type': None}, {'trade_state': 'NOTPAY'},
                   {'success_time': '2026-10-27T18:30:45'}, {'amount': {'total': 10000, 'payer_total': None, 'currency': 'CNY'}},
                   {'amount': {'total': True, 'payer_total': 1, 'currency': 'CNY'}},
                   {'amount': {'total': 10000, 'payer_total': False, 'currency': 'CNY'}},
                   {'amount': {'total': 10000, 'payer_total': 10001, 'currency': 'CNY'}},
                   {'amount': {'total': 10000, 'payer_total': 9000}},
                   {'amount': {'total': 10000, 'payer_total': 9000, 'currency': 'USD'}},
                   {'promotion_detail': None}, {'promotion_detail': ['bad']}]
        for change in changes:
            payload = {**self.payload(), **change}
            with self.subTest(change=change, operation='notice'), self.assertRaises(WeChatPayOutcomeUnknown):
                parse_payment_notification(self.notice(payload))
            self.set_response(payload)
            with self.subTest(change=change, operation='query'), self.assertRaises(WeChatPayOutcomeUnknown):
                self.query()

    def test_missing_optional_facts_are_preserved_for_domain_review(self):
        payload = self.payload()
        del payload['amount']['payer_total']
        del payload['amount']['payer_currency']
        del payload['promotion_detail']
        notice = parse_payment_notification(self.notice(payload))
        self.set_response(payload)
        payment = self.query(notice).verified_payment
        self.assertIsNone(payment.payer_total_fen)
        self.assertIsNone(payment.payer_currency)
        self.assertIsNone(payment.promotion_detail)

    def test_wrong_gateway_snapshot_is_refused_before_network(self):
        for appid, mchid in (('other', '123456'), ('wx-test', 'other')):
            with self.assertRaises(WeChatPayInputError):
                query_payment_evidence(self.gateway, intent_mchid=mchid, intent_appid=appid, out_trade_no='XA-order123')
        self.assertEqual(self.transport.calls, [])

    def test_unsigned_query_and_tampered_notification_rejected(self):
        self.set_response(self.payload(), signed=False)
        with self.assertRaises(WeChatPayOutcomeUnknown):
            self.query()
        body = b'{}'
        with self.assertRaises(WeChatPayProtocolError):
            self.protocol.decrypt_notification(self.sign(body), body + b' ')
