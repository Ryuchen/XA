import base64
import hashlib
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from integrations.wechatpay_gateway import AuthenticatedResponseEvidence
from integrations.wechatpay_response_archive import (EncryptedResponseArchive, ResponseArchiveError,
    open_response, seal_response)
from integrations.wechatpay_v3 import WeChatPayV3


class ResponseArchiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public = cls.platform.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.wrong_public = cls.other.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.protocol = WeChatPayV3(app_id='wx-test', merchant_id='merchant-test', merchant_serial='merchant-serial',
            merchant_private_key=cls.merchant.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), api_v3_key=b'a' * 32,
            platform_keys={'PUB_KEY_ID_test': cls.public})

    def setUp(self):
        self.key = b'k' * 32
        self.body = b'{"openid":"private-data","trade_state":"SUCCESS"}'
        timestamp = str(int(time.time()))
        message = (timestamp + '\nnonce\n').encode() + self.body + b'\n'
        signature = base64.b64encode(self.platform.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()
        self.headers = (('wechatpay-serial', 'PUB_KEY_ID_test'), ('wechatpay-timestamp', timestamp),
                        ('wechatpay-nonce', 'nonce'), ('wechatpay-signature', signature))
        self.response = AuthenticatedResponseEvidence(200, hashlib.sha256(self.body).hexdigest(), self.headers, self.body)

    def seal(self, response=None):
        return seal_response(response or self.response, protocol=self.protocol, key_id='archive-key-1', encryption_key=self.key)

    def open(self, archive, *, key=None, serial='PUB_KEY_ID_test', public=None):
        return open_response(archive, encryption_key=key or self.key, platform_serial=serial,
                             trusted_public_key_pem=public or self.public)

    def test_encrypted_exact_bytes_reverify_after_original_time_window(self):
        archive = self.seal()
        self.assertNotIn(self.body, archive.ciphertext)
        self.assertNotIn('private-data', repr(archive))
        with patch('integrations.wechatpay_response_archive.time.time', return_value=time.time() + 86400):
            restored = self.open(archive)
        self.assertEqual(restored.evidence, self.response)
        self.assertLessEqual(abs(restored.observed_at - int(dict(self.headers)['wechatpay-timestamp'])), 300)
        self.assertEqual(len(restored.public_key_sha256), 64)
        self.assertNotIn('private-data', repr(restored))

    def test_tampering_wrong_keys_serial_and_truncated_ciphertext_fail_closed(self):
        archive = self.seal()
        altered = replace(archive, ciphertext=archive.ciphertext[:-1] + bytes([archive.ciphertext[-1] ^ 1]))
        for candidate, args in ((altered, {}), (replace(archive, nonce=b'x' * 12), {}),
                (replace(archive, key_id='other'), {}), (replace(archive, ciphertext=archive.ciphertext[:10]), {}),
                (archive, {'key': b'z' * 32}), (archive, {'serial': 'other'}),
                (archive, {'public': self.wrong_public})):
            with self.subTest(candidate=candidate, args=args), self.assertRaises(ResponseArchiveError):
                self.open(candidate, **args)

    def test_untrusted_modified_or_ambiguous_evidence_cannot_be_sealed(self):
        bad_body = replace(self.response, raw_body=self.body + b' ')
        bad_hash = replace(bad_body, body_sha256=hashlib.sha256(bad_body.raw_body).hexdigest())
        duplicate = replace(self.response, signed_headers=self.headers[:-1] + (self.headers[0],))
        stale = replace(self.response, signed_headers=tuple((name, '1' if name == 'wechatpay-timestamp' else value)
                                                            for name, value in self.headers))
        for response in (bad_body, bad_hash, duplicate, stale, replace(self.response, status=True)):
            with self.subTest(response=response), self.assertRaises(ResponseArchiveError):
                self.seal(response)

    def test_archive_configuration_and_size_are_bounded(self):
        for key_id, key in (('', self.key), ('bad\nkey', self.key), ('ok', b'x')):
            with self.subTest(key_id=key_id), self.assertRaises(ResponseArchiveError):
                seal_response(self.response, protocol=self.protocol, key_id=key_id, encryption_key=key)
        for archive in (EncryptedResponseArchive('ok', b'bad', b'x' * 16),
                        EncryptedResponseArchive('ok', b'x' * 12, b'x' * (2 * 1024 * 1024 + 1))):
            with self.assertRaises(ResponseArchiveError):
                self.open(archive)


if __name__ == '__main__':
    unittest.main()
