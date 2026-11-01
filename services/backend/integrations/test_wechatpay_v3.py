import base64
import json
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from integrations.wechatpay_v3 import WeChatPayProtocolError, WeChatPayV3


class WeChatPayProtocolTest(unittest.TestCase):
    def setUp(self):
        clock = patch('integrations.wechatpay_v3.time.time', return_value=1700000000)
        clock.start()
        self.addCleanup(clock.stop)

    @classmethod
    def setUpClass(cls):
        cls.merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.api_key = b'x' * 32
        merchant_pem = cls.merchant.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        platform_pem = cls.platform.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cls.client = WeChatPayV3(app_id='wx-test', merchant_id='123456', merchant_serial='merchant-serial',
                                merchant_private_key=merchant_pem, api_v3_key=cls.api_key,
                                platform_keys={'PUB_KEY_ID_test': platform_pem})

    def signed_headers(self, body):
        signature = self.platform.sign(b'1700000000\nnonce\n' + body + b'\n',
                                       padding.PKCS1v15(), hashes.SHA256())
        return {'Wechatpay-Serial': 'PUB_KEY_ID_test', 'Wechatpay-Timestamp': '1700000000',
                'Wechatpay-Nonce': 'nonce', 'Wechatpay-Signature': base64.b64encode(signature).decode()}

    def envelope(self, resource, **overrides):
        return json.dumps({'id': 'event-123', 'event_type': 'TRANSACTION.SUCCESS',
                           'resource_type': 'encrypt-resource', 'resource': {'original_type': 'transaction', **resource},
                           **overrides}).encode()

    def test_request_signature_covers_method_query_and_exact_body(self):
        body = b'{"amount":100}'
        result = self.client.sign_request('POST', '/v3/pay/transactions/jsapi?x=1', body,
                                          timestamp='1700000000', nonce='testnonce')
        signature = result.authorization.split('signature="', 1)[1][:-1]
        expected = b'POST\n/v3/pay/transactions/jsapi?x=1\n1700000000\ntestnonce\n' + body + b'\n'
        self.merchant.public_key().verify(base64.b64decode(signature), expected,
                                          padding.PKCS1v15(), hashes.SHA256())
        self.assertEqual(result.body, body)
        for path in ('https://example.org/v3/test', '/v3/test\nmore', '/other'):
            with self.subTest(path=path), self.assertRaises(WeChatPayProtocolError):
                self.client.sign_request('GET', path)
        with self.assertRaises(WeChatPayProtocolError):
            self.client.sign_request('GET', '/v3/test', b'unexpected body')

    def test_miniprogram_signature_uses_configured_app_and_exact_package(self):
        result = self.client.miniprogram_parameters('prepay-123', timestamp='1700000000', nonce='testnonce')
        self.assertEqual(result['signType'], 'RSA')
        self.assertEqual(result['package'], 'prepay_id=prepay-123')
        self.merchant.public_key().verify(base64.b64decode(result['paySign']),
            b'wx-test\n1700000000\ntestnonce\nprepay_id=prepay-123\n',
            padding.PKCS1v15(), hashes.SHA256())
        with self.assertRaises(WeChatPayProtocolError):
            self.client.miniprogram_parameters('id\ninjected')

    def test_signed_notification_is_verified_before_decryption(self):
        payload = {'appid': 'wx-test', 'amount': {'total': 100}, 'trade_state': 'SUCCESS'}
        ciphertext = AESGCM(self.api_key).encrypt(b'123456789012', json.dumps(payload).encode(), b'data')
        body = self.envelope({'algorithm': 'AEAD_AES_256_GCM', 'nonce': '123456789012',
            'associated_data': 'data', 'ciphertext': base64.b64encode(ciphertext).decode()})
        headers = self.signed_headers(body)
        event = self.client.decrypt_notification(headers, body)
        self.assertEqual(event.payload, payload)
        self.assertEqual((event.notification_id, event.event_type), ('event-123', 'TRANSACTION.SUCCESS'))
        with self.assertRaises(WeChatPayProtocolError):
            self.client.decrypt_notification(headers, body + b' ')
        for changes in ({'Wechatpay-Serial': 'unknown'}, {'Wechatpay-Signature': 'WECHATPAY/SIGNTEST/probe'},
                        {'Wechatpay-Signature': 'bad%%'}, {'Wechatpay-Nonce': 'other'}):
            with self.subTest(changes=changes), self.assertRaises(WeChatPayProtocolError):
                self.client.decrypt_notification({**headers, **changes}, body)

    def test_invalid_ciphertext_and_algorithm_never_return_payload(self):
        for resource in (
            {'algorithm': 'AEAD_AES_256_GCM', 'nonce': '123456789012', 'ciphertext': base64.b64encode(b'bad').decode()},
            {'algorithm': 'wrong', 'nonce': '123456789012', 'ciphertext': ''},
            {'algorithm': 'AEAD_AES_256_GCM', 'nonce': 'short', 'ciphertext': '%%%'}):
            body = self.envelope(resource)
            with self.subTest(resource=resource), self.assertRaises(WeChatPayProtocolError):
                self.client.decrypt_notification(self.signed_headers(body), body)
        with self.assertRaises(WeChatPayProtocolError):
            self.client.decrypt_notification({}, b'x' * (2 * 1024 * 1024 + 1))

    def test_authenticated_but_wrong_notification_type_is_rejected(self):
        resource = {'algorithm': 'AEAD_AES_256_GCM', 'nonce': '123456789012', 'ciphertext': ''}
        for overrides in ({'id': ''}, {'event_type': 'REFUND.SUCCESS'}, {'resource_type': 'other'},
                          {'event_type': 'TRANSACTION.CLOSED'}):
            body = self.envelope(resource, **overrides)
            with self.subTest(overrides=overrides), self.assertRaises(WeChatPayProtocolError):
                self.client.decrypt_notification(self.signed_headers(body), body)

    def test_timestamp_skew_over_five_minutes_is_rejected(self):
        body = b'{}'
        headers = self.signed_headers(body)
        for timestamp, accepted in ((1700000300, True), (1699999700, True),
                                    (1700000301, False), (1699999699, False)):
            with self.subTest(timestamp=timestamp), patch('integrations.wechatpay_v3.time.time', return_value=timestamp):
                if accepted:
                    self.client.verify_signed_message(headers, body)
                else:
                    with self.assertRaises(WeChatPayProtocolError):
                        self.client.verify_signed_message(headers, body)

    def test_bad_credentials_are_rejected(self):
        with self.assertRaises(WeChatPayProtocolError):
            WeChatPayV3(app_id='wx-test', merchant_id='1', merchant_serial='1',
                        merchant_private_key=b'invalid', api_v3_key=b'x' * 32, platform_keys={'a': b'bad'})
