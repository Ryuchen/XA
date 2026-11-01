"""Encrypt and later reverify a signed WeChat response without moving money.

This is a storage-independent primitive, not an evidence repository. It does
not establish that a public key was trusted, the HTTP status/path was signed,
or the response came over the official transport. The caller must manage keys,
retain historical trusted public keys, restrict access, and implement retention.
No endpoint, network request, order mutation or production enablement is added.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .wechatpay_gateway import AuthenticatedResponseEvidence
from .wechatpay_v3 import WeChatPayProtocolError, WeChatPayV3, _rsa_public_key, _strict_json

_KEY_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_HEADERS = ('wechatpay-serial', 'wechatpay-timestamp', 'wechatpay-nonce', 'wechatpay-signature')
_MAX_BODY = 1024 * 1024
_MAX_CIPHERTEXT = 2 * 1024 * 1024


class ResponseArchiveError(ValueError):
    """Archive cannot be safely encrypted, opened or independently reverified."""


@dataclass(frozen=True)
class EncryptedResponseArchive:
    key_id: str
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)


@dataclass(frozen=True)
class ReverifiedResponseArchive:
    evidence: AuthenticatedResponseEvidence = field(repr=False)
    observed_at: int
    public_key_sha256: str


def _config(key_id, key):
    if (not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id)
            or not isinstance(key, bytes) or len(key) != 32):
        raise ResponseArchiveError('Invalid evidence archive key configuration')


def _aad(key_id):
    return b'wechatpay-response-archive-v1\n' + key_id.encode('ascii')


def _headers(values):
    if (not isinstance(values, (tuple, list)) or len(values) != len(_HEADERS)
            or any(not isinstance(item, (tuple, list)) or len(item) != 2 for item in values)):
        raise ResponseArchiveError('Invalid signed response headers')
    headers = {}
    for name, value in values:
        if (not isinstance(name, str) or name not in _HEADERS or name in headers
                or not isinstance(value, str) or not value or len(value) > 1024
                or any(ord(c) < 32 or ord(c) > 126 for c in value)):
            raise ResponseArchiveError('Invalid signed response headers')
        headers[name] = value
    if set(headers) != set(_HEADERS):
        raise ResponseArchiveError('Invalid signed response headers')
    stamp = headers['wechatpay-timestamp']
    if (not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12
            or len(headers['wechatpay-serial']) > 128 or len(headers['wechatpay-nonce']) > 128):
        raise ResponseArchiveError('Invalid signed response timestamp')
    return headers


def _fingerprint(key):
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def seal_response(response: AuthenticatedResponseEvidence, *, protocol: WeChatPayV3,
                  key_id: str, encryption_key: bytes) -> EncryptedResponseArchive:
    """Verify a fresh response again and encrypt its exact bytes and signature.

    The fresh timestamp check uses this process's current clock, which must be
    synchronized. The capture time is retained inside the authenticated blob;
    it is a server observation, not a WeChat-signed timestamp.
    """
    _config(key_id, encryption_key)
    if (type(response) is not AuthenticatedResponseEvidence or type(protocol) is not WeChatPayV3
            or type(response.status) is not int or not 100 <= response.status <= 599
            or not isinstance(response.raw_body, bytes) or len(response.raw_body) > _MAX_BODY
            or not isinstance(response.body_sha256, str)
            or hashlib.sha256(response.raw_body).hexdigest() != response.body_sha256):
        raise ResponseArchiveError('Invalid signed response evidence')
    headers = _headers(response.signed_headers)
    observed_at = int(time.time())
    try:
        protocol.verify_signed_message(headers, response.raw_body)
        platform_key = protocol._platform_keys[headers['wechatpay-serial']]
    except (WeChatPayProtocolError, KeyError, TypeError):
        raise ResponseArchiveError('Response signature verification failed') from None
    if abs(observed_at - int(headers['wechatpay-timestamp'])) > 300:
        raise ResponseArchiveError('Response timestamp outside capture window')
    payload = {'version': 1, 'status': response.status, 'observed_at': observed_at,
               'key_sha256': _fingerprint(platform_key), 'body_sha256': response.body_sha256,
               'body': base64.b64encode(response.raw_body).decode('ascii'), 'headers': headers}
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    nonce = secrets.token_bytes(12)
    return EncryptedResponseArchive(key_id, nonce, AESGCM(encryption_key).encrypt(nonce, raw, _aad(key_id)))


def open_response(archive: EncryptedResponseArchive, *, encryption_key: bytes,
                  platform_serial: str, trusted_public_key_pem: bytes) -> ReverifiedResponseArchive:
    """Decrypt and verify historical signature using an explicitly trusted key.

    This permits an old signature's age only because its capture time was
    protected by the archive at the original fresh verification. The supplied
    public key and its trust history must be independently managed. The result
    is still not an order match or proof of actual funds.
    """
    if type(archive) is not EncryptedResponseArchive:
        raise ResponseArchiveError('Invalid response archive')
    _config(archive.key_id, encryption_key)
    if (not isinstance(archive.nonce, bytes) or len(archive.nonce) != 12
            or not isinstance(archive.ciphertext, bytes) or not 16 <= len(archive.ciphertext) <= _MAX_CIPHERTEXT
            or not isinstance(platform_serial, str) or not platform_serial or len(platform_serial) > 128
            or not isinstance(trusted_public_key_pem, bytes) or len(trusted_public_key_pem) > 128 * 1024):
        raise ResponseArchiveError('Invalid response archive')
    try:
        payload = _strict_json(AESGCM(encryption_key).decrypt(archive.nonce, archive.ciphertext, _aad(archive.key_id)))
        expected_keys = {'version', 'status', 'observed_at', 'key_sha256', 'body_sha256', 'body', 'headers'}
        if (not isinstance(payload, dict) or set(payload) != expected_keys or type(payload['version']) is not int or payload['version'] != 1
                or type(payload['status']) is not int or not 100 <= payload['status'] <= 599
                or type(payload['observed_at']) is not int or payload['observed_at'] < 0
                or not isinstance(payload['headers'], dict) or not isinstance(payload['body'], str)):
            raise ValueError
        headers = _headers(tuple(payload['headers'].items()))
        body = base64.b64decode(payload['body'], validate=True)
        key = _rsa_public_key(trusted_public_key_pem)
        if (len(body) > _MAX_BODY or headers['wechatpay-serial'] != platform_serial
                or payload['key_sha256'] != _fingerprint(key)
                or payload['body_sha256'] != hashlib.sha256(body).hexdigest()
                or abs(payload['observed_at'] - int(headers['wechatpay-timestamp'])) > 300):
            raise ValueError
        signature = base64.b64decode(headers['wechatpay-signature'], validate=True)
        message = (headers['wechatpay-timestamp'] + '\n' + headers['wechatpay-nonce'] + '\n').encode('utf-8') + body + b'\n'
        key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
        evidence = AuthenticatedResponseEvidence(payload['status'], payload['body_sha256'],
            tuple((name, headers[name]) for name in _HEADERS), body)
        return ReverifiedResponseArchive(evidence, payload['observed_at'], payload['key_sha256'])
    except (InvalidTag, InvalidSignature, WeChatPayProtocolError, ResponseArchiveError,
            ValueError, TypeError, KeyError, UnicodeError, binascii.Error, RecursionError):
        raise ResponseArchiveError('Response archive could not be verified') from None
