"""WeChat Pay v3 signing primitives.

This module does not make network requests or expose an HTTP endpoint. Callers
must verify the provider response before using its contents and reconcile the
transaction with the expected order before changing any business state.
"""
import base64
import binascii
import hashlib
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from typing import Mapping

from cryptography import x509
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class WeChatPayProtocolError(ValueError):
    """The provider message or configuration cannot be authenticated."""


def _line(value, name):
    if not isinstance(value, str) or not value or '\n' in value or '\r' in value:
        raise WeChatPayProtocolError(f'Invalid {name}')
    return value


def _rsa_private_key(pem):
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (TypeError, ValueError) as exc:
        raise WeChatPayProtocolError('Invalid merchant private key') from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise WeChatPayProtocolError('Merchant key must be RSA 2048 or stronger')
    return key


def _rsa_public_key(pem):
    try:
        if b'BEGIN CERTIFICATE' in pem:
            key = x509.load_pem_x509_certificate(pem).public_key()
        else:
            key = serialization.load_pem_public_key(pem)
    except (TypeError, ValueError) as exc:
        raise WeChatPayProtocolError('Invalid WeChat Pay public key') from exc
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise WeChatPayProtocolError('WeChat Pay key must be RSA 2048 or stronger')
    return key


def _sign(key, message):
    return base64.b64encode(key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode('ascii')


def _strict_json(raw):
    """Reject ambiguous JSON and JavaScript constants in authenticated data."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('invalid JSON constant')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite JSON number')
        return number

    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant,
                      parse_float=finite_float)


@dataclass(frozen=True)
class SignedRequest:
    method: str
    path_and_query: str
    body: bytes
    authorization: str


@dataclass(frozen=True)
class VerifiedNotification:
    notification_id: str
    event_type: str
    resource_type: str
    original_type: str
    payload: dict
    signed_body_sha256: str


class WeChatPayV3:
    """Explicitly configured merchant key and trusted platform keys.

    ``platform_keys`` maps Wechatpay-Serial values to PEM public keys or
    certificates. Multiple keys permit a controlled certificate rotation.
    Credentials are supplied by the server and are never returned to clients.
    """

    def __init__(self, *, app_id: str, merchant_id: str, merchant_serial: str,
                 merchant_private_key: bytes, api_v3_key: bytes,
                 platform_keys: Mapping[str, bytes]):
        self.app_id = _line(app_id, 'app ID')
        self.merchant_id = _line(merchant_id, 'merchant ID')
        self.merchant_serial = _line(merchant_serial, 'merchant serial')
        if any('"' in value or '\\' in value for value in (merchant_id, merchant_serial)):
            raise WeChatPayProtocolError('Invalid merchant identity')
        self._merchant_key = _rsa_private_key(merchant_private_key)
        if not isinstance(api_v3_key, bytes) or len(api_v3_key) != 32:
            raise WeChatPayProtocolError('API v3 key must be 32 bytes')
        self._api_key = api_v3_key
        if not platform_keys:
            raise WeChatPayProtocolError('At least one trusted platform key is required')
        self._platform_keys = {_line(serial, 'platform serial'): _rsa_public_key(pem)
                               for serial, pem in platform_keys.items()}

    def sign_request(self, method: str, path_and_query: str, body: bytes = b'', *,
                     timestamp: str | None = None, nonce: str | None = None) -> SignedRequest:
        if method not in ('GET', 'POST') or not isinstance(body, bytes) or (method == 'GET' and body):
            raise WeChatPayProtocolError('Invalid request method or body')
        if not isinstance(path_and_query, str) or not path_and_query.startswith('/v3/') or '\n' in path_and_query or '\r' in path_and_query:
            raise WeChatPayProtocolError('Invalid request path')
        timestamp = str(int(time.time())) if timestamp is None else timestamp
        nonce = secrets.token_hex(16) if nonce is None else nonce
        if not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdigit():
            raise WeChatPayProtocolError('Invalid timestamp')
        if not isinstance(nonce, str) or not re.fullmatch(r'[A-Za-z0-9]{1,32}', nonce):
            raise WeChatPayProtocolError('Invalid nonce')
        try:
            message = f'{method}\n{path_and_query}\n{timestamp}\n{nonce}\n'.encode('utf-8') + body + b'\n'
            signature = _sign(self._merchant_key, message)
        except (UnicodeError, ValueError) as exc:
            raise WeChatPayProtocolError('Unable to sign request') from exc
        authorization = (f'WECHATPAY2-SHA256-RSA2048 mchid="{self.merchant_id}",'
                         f'nonce_str="{nonce}",timestamp="{timestamp}",'
                         f'serial_no="{self.merchant_serial}",signature="{signature}"')
        return SignedRequest(method, path_and_query, body, authorization)

    def miniprogram_parameters(self, prepay_id: str, *, timestamp: str | None = None,
                               nonce: str | None = None) -> dict:
        if not isinstance(prepay_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,118}', prepay_id):
            raise WeChatPayProtocolError('Invalid prepay ID')
        timestamp = str(int(time.time())) if timestamp is None else timestamp
        nonce = secrets.token_hex(16) if nonce is None else nonce
        if not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdigit():
            raise WeChatPayProtocolError('Invalid timestamp')
        if not isinstance(nonce, str) or not re.fullmatch(r'[A-Za-z0-9]{1,32}', nonce):
            raise WeChatPayProtocolError('Invalid nonce')
        package = 'prepay_id=' + prepay_id
        message = f'{self.app_id}\n{timestamp}\n{nonce}\n{package}\n'.encode('utf-8')
        return {'timeStamp': timestamp, 'nonceStr': nonce, 'package': package,
                'signType': 'RSA', 'paySign': _sign(self._merchant_key, message)}

    def verify_signed_message(self, headers: Mapping[str, str], body: bytes) -> None:
        """Authenticate exact response/callback bytes using the named trusted key."""
        if not isinstance(body, bytes):
            raise WeChatPayProtocolError('Message body must be bytes')
        normalized = {str(k).lower(): v for k, v in headers.items()}
        serial = normalized.get('wechatpay-serial')
        timestamp = normalized.get('wechatpay-timestamp')
        nonce = normalized.get('wechatpay-nonce')
        signature = normalized.get('wechatpay-signature')
        key = self._platform_keys.get(serial)
        if (not key or not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdigit()
                or len(timestamp) > 12 or abs(int(timestamp) - int(time.time())) > 300
                or not isinstance(nonce, str) or not nonce or '\n' in nonce or '\r' in nonce
                or not isinstance(signature, str) or signature.startswith('WECHATPAY/SIGNTEST/')):
            raise WeChatPayProtocolError('Untrusted or incomplete provider signature')
        try:
            decoded = base64.b64decode(signature, validate=True)
            message = f'{timestamp}\n{nonce}\n'.encode('utf-8') + body + b'\n'
            key.verify(decoded, message, padding.PKCS1v15(), hashes.SHA256())
        except (ValueError, binascii.Error, InvalidSignature) as exc:
            raise WeChatPayProtocolError('Invalid provider signature') from exc

    def decrypt_notification(self, headers: Mapping[str, str], body: bytes) -> VerifiedNotification:
        """Verify and preserve event identity; caller must check business identity."""
        if not isinstance(body, bytes) or len(body) > 2 * 1024 * 1024:
            raise WeChatPayProtocolError('Invalid notification size')
        self.verify_signed_message(headers, body)
        try:
            envelope = _strict_json(body)
            if not isinstance(envelope, dict):
                raise WeChatPayProtocolError('Invalid notification envelope')
            notification_id = _line(envelope['id'], 'notification ID')
            if len(notification_id) > 36:
                raise WeChatPayProtocolError('Invalid notification ID')
            event_type = envelope['event_type']
            resource_type = envelope['resource_type']
            resource = envelope['resource']
            types = {'TRANSACTION.SUCCESS': 'transaction', 'REFUND.SUCCESS': 'refund',
                     'REFUND.CLOSED': 'refund', 'REFUND.ABNORMAL': 'refund'}
            if (not isinstance(event_type, str) or event_type not in types
                    or resource_type != 'encrypt-resource' or not isinstance(resource, dict)
                    or resource.get('original_type') != types[event_type]):
                raise WeChatPayProtocolError('Unsupported notification type')
            if resource.get('algorithm') != 'AEAD_AES_256_GCM':
                raise WeChatPayProtocolError('Unsupported notification encryption')
            nonce = resource['nonce'].encode('utf-8')
            associated = resource.get('associated_data', '').encode('utf-8')
            ciphertext = base64.b64decode(resource['ciphertext'], validate=True)
            plain = AESGCM(self._api_key).decrypt(nonce, ciphertext, associated)
            payload = _strict_json(plain)
            if not isinstance(payload, dict):
                raise WeChatPayProtocolError('Invalid notification payload')
            return VerifiedNotification(notification_id, event_type, resource_type,
                                        resource['original_type'], payload, hashlib.sha256(body).hexdigest())
        except WeChatPayProtocolError:
            raise
        except (KeyError, TypeError, AttributeError, ValueError, UnicodeError, binascii.Error, InvalidTag, RecursionError) as exc:
            raise WeChatPayProtocolError('Invalid encrypted notification') from exc
