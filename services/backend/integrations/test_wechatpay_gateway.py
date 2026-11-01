import base64
import hashlib
import json
import ssl
import time
import traceback
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from integrations.wechatpay_gateway import (
    OfficialHTTPTransport, TransportResponse, WeChatPayGateway, WeChatPayInputError,
    WeChatPayOutcomeUnknown, WeChatPayResponseError,
)
from integrations.wechatpay_v3 import WeChatPayProtocolError, WeChatPayV3


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.response = None
        self.error = None

    def send(self, method, path, headers, body, *, timeout, max_response_bytes):
        self.calls.append((method, path, headers, body, timeout, max_response_bytes))
        if self.error:
            raise self.error
        return self.response


class WeChatPayGatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = cls.merchant.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public = cls.platform.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.protocol = WeChatPayV3(app_id='wx-test', merchant_id='123456', merchant_serial='merchant-serial',
                                   merchant_private_key=private, api_v3_key=b'x' * 32,
                                   platform_keys={'PUB_KEY_ID_test': public})

    def setUp(self):
        self.transport = FakeTransport()
        self.gateway = WeChatPayGateway(self.protocol, transport=self.transport, timeout=4)

    def signed_response(self, status, payload=None, *, raw=None):
        body = raw if raw is not None else (b'' if payload is None else json.dumps(payload, ensure_ascii=False).encode())
        timestamp = str(int(time.time()))
        signature = self.platform.sign((timestamp + '\nnonce\n').encode() + body + b'\n',
                                       padding.PKCS1v15(), hashes.SHA256())
        headers = {'Wechatpay-Serial': 'PUB_KEY_ID_test', 'Wechatpay-Timestamp': timestamp,
                   'Wechatpay-Nonce': 'nonce', 'Wechatpay-Signature': base64.b64encode(signature).decode()}
        return TransportResponse(status, headers, body)

    def order(self, trade_no='XA|order*123', state='NOTPAY'):
        return {'appid': 'wx-test', 'mchid': '123456', 'out_trade_no': trade_no,
                'trade_state': state, 'trade_type': 'JSAPI'}

    def refund(self, refund_no='refund|123', state='PROCESSING'):
        return {'refund_id': 'wx-refund', 'out_refund_no': refund_no,
                'out_trade_no': 'XA|order*123', 'transaction_id': 'wx-transaction',
                'status': state, 'amount': {'refund': 123, 'total': 1000, 'currency': 'CNY'}}

    def assert_signed_request(self, call):
        method, path, headers, body, timeout, limit = call
        values = dict(part.split('=', 1) for part in headers['Authorization'].split(' ', 1)[1].split(','))
        values = {key: value.strip('"') for key, value in values.items()}
        message = f'{method}\n{path}\n{values["timestamp"]}\n{values["nonce_str"]}\n'.encode() + body + b'\n'
        self.merchant.public_key().verify(base64.b64decode(values['signature']), message,
                                          padding.PKCS1v15(), hashes.SHA256())
        self.assertEqual((timeout, limit), (4, 1024 * 1024))

    def test_jsapi_request_signs_exact_json_and_preserves_minor_units(self):
        self.transport.response = self.signed_response(200, {'prepay_id': 'wx-prepay_123'})
        expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0)
        result = self.gateway.create_jsapi('XA-order123', '双陪服务', 10001, 'openid_test',
                                          'https://pay.example.test/wx/notify',
                                          expires)
        self.assertEqual(result['prepay_id'], 'wx-prepay_123')
        self.assertEqual(len(self.transport.calls), 1)
        call = self.transport.calls[0]
        self.assert_signed_request(call)
        self.assertEqual((call[0], call[1]), ('POST', '/v3/pay/transactions/jsapi'))
        data = json.loads(call[3])
        self.assertEqual(data['amount'], {'total': 10001, 'currency': 'CNY'})
        self.assertEqual(data['payer'], {'openid': 'openid_test'})
        self.assertEqual(data['time_expire'], expires.isoformat(timespec='seconds'))

    def test_query_escapes_order_number_and_requires_matching_provider_identity(self):
        self.transport.response = self.signed_response(200, self.order())
        result = self.gateway.query_order('XA|order*123')
        self.assertEqual(result['trade_state'], 'NOTPAY')
        self.assert_signed_request(self.transport.calls[0])
        self.assertEqual(self.transport.calls[0][1], '/v3/pay/transactions/out-trade-no/XA%7Corder%2A123?mchid=123456')
        for changes in ({'mchid': 'other'}, {'appid': 'other'}, {'out_trade_no': 'other'},
                        {'trade_type': 'NATIVE'}, {'trade_state': []}):
            self.transport.response = self.signed_response(200, {**self.order(), **changes})
            with self.subTest(changes=changes), self.assertRaises(WeChatPayOutcomeUnknown):
                self.gateway.query_order('XA|order*123')

    def test_authenticated_order_query_retains_signed_bytes_without_interpreting_state(self):
        raw = b'{"code":"ORDER_NOT_EXIST","message":"opaque"}'
        self.transport.response = self.signed_response(404, raw=raw)
        evidence = self.gateway.query_order_authenticated('XA|order*123')
        self.assertEqual((evidence.status, evidence.raw_body), (404, raw))
        self.assertEqual(evidence.body_sha256, hashlib.sha256(raw).hexdigest())
        self.assertNotIn('opaque', repr(evidence))
        from integrations.wechatpay_response_archive import open_response, seal_response
        archive = seal_response(evidence, protocol=self.protocol, key_id='local-test', encryption_key=b'k' * 32)
        restored = open_response(archive, encryption_key=b'k' * 32, platform_serial='PUB_KEY_ID_test',
            trusted_public_key_pem=self.platform.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        self.assertEqual(restored.evidence, evidence)
        self.assertEqual(self.transport.calls[0][1], '/v3/pay/transactions/out-trade-no/XA%7Corder%2A123?mchid=123456')
        self.assert_signed_request(self.transport.calls[0])
        self.transport.response = TransportResponse(200, {}, b'{}')
        with self.assertRaises(WeChatPayOutcomeUnknown):
            self.gateway.query_order_authenticated('XA|order*123')
        before = len(self.transport.calls)
        with self.assertRaises(WeChatPayInputError):
            self.gateway.query_order_authenticated('../unsafe')
        self.assertEqual(len(self.transport.calls), before)

    def test_close_requires_authenticated_empty_204(self):
        self.transport.response = self.signed_response(204)
        self.assertIsNone(self.gateway.close_order('XA-order123'))
        self.assertEqual(self.transport.calls[0][1], '/v3/pay/transactions/out-trade-no/XA-order123/close')
        self.assertEqual(json.loads(self.transport.calls[0][3]), {'mchid': '123456'})
        self.assert_signed_request(self.transport.calls[0])
        for response in (TransportResponse(204, {}, b''), self.signed_response(204, raw=b'{}'),
                         self.signed_response(200, {})):
            self.transport.response = response
            with self.subTest(response=response), self.assertRaises(WeChatPayOutcomeUnknown):
                self.gateway.close_order('XA-order123')

    def test_refund_acceptance_is_not_treated_as_final_and_responses_are_bound_to_request(self):
        self.transport.response = self.signed_response(200, self.refund())
        result = self.gateway.create_refund('refund|123', 123, 1000, out_trade_no='XA|order*123', reason='客户取消')
        self.assertEqual(result['status'], 'PROCESSING')
        self.assert_signed_request(self.transport.calls[0])
        self.assertEqual(json.loads(self.transport.calls[0][3])['amount'], {'refund': 123, 'total': 1000, 'currency': 'CNY'})
        self.assertNotIn('transaction_id', json.loads(self.transport.calls[0][3]))
        self.transport.response = self.signed_response(200, self.refund(state='SUCCESS'))
        self.assertEqual(self.gateway.query_refund('refund|123')['status'], 'SUCCESS')
        self.assertEqual(self.transport.calls[-1][1], '/v3/refund/domestic/refunds/refund%7C123')
        self.transport.response = self.signed_response(200, self.refund())
        self.gateway.create_refund('refund|123', 123, 1000, transaction_id='wx-transaction')
        self.assertEqual(json.loads(self.transport.calls[-1][3])['transaction_id'], 'wx-transaction')
        self.assertNotIn('out_trade_no', json.loads(self.transport.calls[-1][3]))
        for change in ({'out_refund_no': 'other'}, {'out_trade_no': 'other'},
                       {'amount': {'refund': 124, 'total': 1000, 'currency': 'CNY'}}, {'status': []}):
            self.transport.response = self.signed_response(200, {**self.refund(), **change})
            with self.subTest(change=change), self.assertRaises(WeChatPayOutcomeUnknown):
                self.gateway.create_refund('refund|123', 123, 1000, out_trade_no='XA|order*123')

    def test_authenticated_provider_error_is_only_a_response_and_unknowns_never_retry(self):
        self.transport.response = self.signed_response(400, {'code': 'ORDERPAID', 'message': 'untrusted details'})
        with self.assertRaises(WeChatPayResponseError) as caught:
            self.gateway.close_order('XA-order123')
        self.assertEqual((caught.exception.status, caught.exception.code), (400, 'ORDERPAID'))
        self.assertNotIn('untrusted details', str(caught.exception))
        self.assertEqual(len(self.transport.calls), 1)
        for response in (TransportResponse(400, {}, b'{"code":"ORDERPAID"}'),
                         self.signed_response(500, {'code': 'SYSTEM_ERROR'}),
                         self.signed_response(302, {}),
                         self.signed_response(200, raw=b'{"prepay_id":"one","prepay_id":"two"}'),
                         self.signed_response(200, raw=b'NaN'),
                         self.signed_response(200, raw=b'x' * (1024 * 1024 + 1))):
            self.transport.response = response
            before = len(self.transport.calls)
            with self.subTest(status=response.status), self.assertRaises(WeChatPayOutcomeUnknown):
                self.gateway.create_jsapi('XA-order123', '服务', 100, 'openid', 'https://pay.example.test/wx/notify')
            self.assertEqual(len(self.transport.calls), before + 1)
        self.transport.error = TimeoutError('timed out')
        before = len(self.transport.calls)
        with self.assertRaises(WeChatPayOutcomeUnknown):
            self.gateway.close_order('XA-order123')
        self.assertEqual(len(self.transport.calls), before + 1)

    def test_invalid_requests_never_reach_transport(self):
        prepay = lambda **kw: self.gateway.create_jsapi(**{
            'out_trade_no': 'XA-order123', 'description': '服务', 'amount_fen': 100,
            'openid': 'openid', 'notify_url': 'https://pay.example.test/wx/notify', **kw})
        for change in ({'out_trade_no': '../other'}, {'amount_fen': True}, {'amount_fen': -1},
                       {'openid': ''}, {'description': 'a\nheader'}, {'description': '\ud800'},
                       {'time_expire': datetime(2027, 1, 1)},
                       {'time_expire': datetime.now(timezone.utc) + timedelta(seconds=30)},
                       {'time_expire': datetime.now(timezone.utc) + timedelta(days=16)},
                       {'notify_url': 'https://127.0.0.1/wx'}, {'notify_url': 'https://localhost/wx'},
                       {'notify_url': 'https://pay.example.test/wx?x=1'},
                       {'notify_url': 'https://pay.example.test'}, {'notify_url': 'http://pay.example.test/wx'},
                       {'notify_url': 'https://user@pay.example.test/wx'}):
            with self.subTest(change=change), self.assertRaises(WeChatPayInputError):
                prepay(**change)
        for call in (
            lambda: self.gateway.create_refund('refund01', True, 100, out_trade_no='XA-order123'),
            lambda: self.gateway.create_refund('refund01', 101, 100, out_trade_no='XA-order123'),
            lambda: self.gateway.create_refund('refund01', 1, 100),
            lambda: self.gateway.create_refund('refund01', 1, 100, out_trade_no='XA-order123', transaction_id='wx123'),
            lambda: self.gateway.create_refund('refund01', 1, 100, out_trade_no='XA-order123', reason='退' * 27),
            lambda: self.gateway.query_refund('../refund'),
        ):
            with self.assertRaises(WeChatPayInputError):
                call()
        self.assertEqual(self.transport.calls, [])

    def test_refund_verified_bytes_can_be_reverified_without_logging_sensitive_content(self):
        original = self.signed_response(200, {**self.refund(), 'user_received_account': 'sensitive-bank-account'})
        headers = {**original.headers, 'Authorization': 'must-not-escape', 'X-Private': 'other-secret'}
        self.transport.response = TransportResponse(original.status, headers, original.body)
        evidence = self.gateway.query_refund_authenticated('refund|123')
        self.assertEqual((evidence.status, evidence.body_sha256, evidence.wechatpay_serial),
                         (200, hashlib.sha256(original.body).hexdigest(), 'PUB_KEY_ID_test'))
        self.assertEqual(evidence.raw_body, original.body)
        self.assertEqual(set(dict(evidence.signed_headers)),
                         {'wechatpay-serial', 'wechatpay-timestamp', 'wechatpay-nonce', 'wechatpay-signature'})
        self.assertNotIn('sensitive-bank-account', repr(evidence))
        self.assertNotIn('must-not-escape', repr(evidence))
        self.assertNotIn('other-secret', repr(evidence))
        self.protocol.verify_signed_message(dict(evidence.signed_headers), evidence.raw_body)
        with self.assertRaises(WeChatPayProtocolError):
            self.protocol.verify_signed_message(dict(evidence.signed_headers), evidence.raw_body + b' ')
        self.assertEqual(len(self.transport.calls), 1)
        self.assert_signed_request(self.transport.calls[0])

    def test_signed_refund_errors_are_evidence_not_financial_outcomes(self):
        for status in (400, 500, 302):
            self.transport.response = self.signed_response(status, {'code': 'CHANNEL_ERROR', 'message': 'private-message'})
            with self.subTest(status=status):
                evidence = self.gateway.query_refund_authenticated('refund|123')
                self.assertEqual(evidence.status, status)
                self.assertEqual(evidence.raw_body, self.transport.response.body)
                self.assertNotIn('private-message', repr(evidence))
                if status == 400:
                    with self.assertRaises(WeChatPayResponseError):
                        self.gateway.query_refund('refund|123')
                else:
                    with self.assertRaises(WeChatPayOutcomeUnknown):
                        self.gateway.query_refund('refund|123')

    def test_refund_submission_evidence_exposes_unparsed_signed_bytes_once(self):
        # Even a malformed signed response is retained for reconciliation.
        self.transport.response = self.signed_response(200, raw=b'not-json-private')
        result = self.gateway.create_refund_authenticated('refund|123', 123, 1000, out_trade_no='XA|order*123')
        self.assertEqual(result.raw_body, b'not-json-private')
        self.assertNotIn('not-json-private', repr(result))
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.transport.calls[0][0], 'POST')
        self.assert_signed_request(self.transport.calls[0])

    def test_unsigned_oversized_or_timed_out_refund_never_returns_evidence(self):
        signed = self.signed_response(200, raw=b'original')
        for response in (TransportResponse(200, {}, b'private'), TransportResponse(200, signed.headers, b'tampered'),
                         self.signed_response(200, raw=b'x' * (1024 * 1024 + 1))):
            self.transport.response = response
            with self.subTest(status=response.status), self.assertRaises(WeChatPayOutcomeUnknown):
                self.gateway.query_refund_authenticated('refund|123')
        self.transport.error = TimeoutError('sensitive-error')
        before = len(self.transport.calls)
        with self.assertRaises(WeChatPayOutcomeUnknown) as caught:
            self.gateway.create_refund_authenticated('refund|123', 123, 1000, out_trade_no='XA|order*123')
        self.assertNotIn('sensitive-error', str(caught.exception))
        self.assertNotIn('sensitive-error', ''.join(traceback.format_exception(caught.exception)))
        self.assertEqual(len(self.transport.calls), before + 1)

    def test_exact_response_size_limit_preserves_all_bytes(self):
        body = b'x' * (1024 * 1024)
        self.transport.response = self.signed_response(200, raw=body)
        evidence = self.gateway.query_refund_authenticated('refund|123')
        self.assertEqual((len(evidence.raw_body), evidence.body_sha256), (len(body), hashlib.sha256(body).hexdigest()))


class TransportTest(unittest.TestCase):
    def test_fixed_tls_host_never_follows_redirect_and_checks_response_bound(self):
        class Headers:
            def get_all(self, name, default):
                return ['test']
        class Socket:
            def settimeout(self, timeout):
                self.timeout = timeout
        class Response:
            headers = Headers()
            status = 302
            def getheader(self, name):
                return '4' if name == 'Content-Length' else None
            def read1(self, count):
                value, self.body = self.body, b''
                return value
            body = b'test'
        class Connection:
            def __init__(self, host, timeout, context):
                self.host, self.timeout, self.context = host, timeout, context
                self.sock = Socket()
                instances.append(self)
            def request(self, method, path, body, headers):
                self.requested = (method, path, body, headers)
            def getresponse(self):
                return Response()
            def close(self):
                self.closed = True
        instances = []
        with patch('integrations.wechatpay_gateway.http.client.HTTPSConnection', Connection):
            result = OfficialHTTPTransport().send('GET', '/v3/test', {}, b'', timeout=3, max_response_bytes=4)
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].host, 'api.mch.weixin.qq.com')
        self.assertTrue(instances[0].context.check_hostname)
        self.assertEqual(instances[0].context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(result.status, 302)
        self.assertTrue(instances[0].closed)
        with patch('integrations.wechatpay_gateway.http.client.HTTPSConnection', Connection):
            with self.assertRaises(WeChatPayOutcomeUnknown):
                OfficialHTTPTransport().send('GET', '/v3/test', {}, b'', timeout=3, max_response_bytes=3)

    def test_transport_rejects_duplicate_signature_header_and_truncated_response(self):
        class Headers:
            def get_all(self, name, default):
                return ['one', 'two'] if name == 'Wechatpay-Serial' and duplicate else ['one']
        class Response:
            headers = Headers()
            status = 200
            body = b'ab'
            def getheader(self, name): return '4'
            def read1(self, count):
                value, self.body = self.body, b''
                return value
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch('integrations.wechatpay_gateway.http.client.HTTPSConnection', Connection):
            for duplicate in (True, False):
                with self.subTest(duplicate=duplicate), self.assertRaises(WeChatPayOutcomeUnknown):
                    OfficialHTTPTransport().send('GET', '/v3/test', {}, b'', timeout=3, max_response_bytes=10)
