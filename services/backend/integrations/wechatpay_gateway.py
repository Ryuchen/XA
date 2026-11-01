"""Bounded WeChat Pay v3 HTTP operations; no business state or automatic retries.

The returned JSON has an authenticated provider signature, not a guarantee
that it matches a local order. The domain layer must reconcile every relevant
merchant, payer, amount, currency and transaction/refund identifier before
changing funds or order state. A refund response is not proof of refund success.
"""
from __future__ import annotations

import http.client
import hashlib
import ipaddress
import json
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit

from .wechatpay_v3 import WeChatPayProtocolError, WeChatPayV3


_HOST = 'api.mch.weixin.qq.com'
_MAX_RESPONSE_BYTES = 1024 * 1024
_TRADE_NO = re.compile(r'[A-Za-z0-9_|*\-]{6,32}\Z')
_REFUND_NO = re.compile(r'[A-Za-z0-9_|*@\-]{1,64}\Z')
_PREPAY_ID = re.compile(r'[A-Za-z0-9_\-]{1,64}\Z')
_ERROR_CODE = re.compile(r'[A-Z0-9_]{1,64}\Z')
_TRADE_STATES = frozenset(('SUCCESS', 'REFUND', 'NOTPAY', 'CLOSED', 'REVOKED', 'USERPAYING', 'PAYERROR'))
_REFUND_STATES = frozenset(('SUCCESS', 'CLOSED', 'PROCESSING', 'ABNORMAL'))


class WeChatPayInputError(ValueError):
    """The request was rejected locally before sending anything."""


class WeChatPayOutcomeUnknown(Exception):
    """The operation may have reached WeChat; reconcile using the same ID."""


class WeChatPayResponseError(Exception):
    """An authenticated 4xx response, not a final business/financial state."""

    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(f'WeChat Pay returned HTTP {status} ({code}); reconcile before acting')


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class AuthenticatedResponseEvidence:
    """Signature-verified response body plus transport status for controlled storage.

    The WeChat signature covers timestamp, nonce and exact response bytes; it
    does NOT cover the HTTP status, which is protected by the HTTPS transport.
    A body hash alone cannot independently verify the signature later: retain
    the bounded raw bytes and trusted public key material if re-verification is
    required. This object is not proof of an order or refund outcome.

    Raw bytes can contain personal information; never log or expose them to a
    client. Callers must apply restricted, encrypted storage and retention.
    """
    status: int
    body_sha256: str
    signed_headers: tuple[tuple[str, str], ...]
    raw_body: bytes = field(repr=False)

    @property
    def wechatpay_serial(self):
        return dict(self.signed_headers)['wechatpay-serial']


class OfficialHTTPTransport:
    """One TLS request to the fixed official host. No proxy, retry or redirect."""

    def send(self, method: str, path: str, headers: dict[str, str], body: bytes,
             *, timeout: float, max_response_bytes: int) -> TransportResponse:
        deadline = time.monotonic() + timeout
        connection = http.client.HTTPSConnection(_HOST, timeout=timeout, context=ssl.create_default_context())
        try:
            connection.request(method, path, body=body if method == 'POST' else None, headers=headers)
            socket = connection.sock
            response = connection.getresponse()
            # Authentication headers must be unambiguous. HTTPMessage retains
            # duplicate field lines even when their casing differs.
            signed_headers = {}
            for name in ('Wechatpay-Serial', 'Wechatpay-Timestamp', 'Wechatpay-Nonce', 'Wechatpay-Signature'):
                values = response.headers.get_all(name, [])
                if len(values) != 1:
                    raise WeChatPayOutcomeUnknown('Ambiguous or missing response signature headers')
                signed_headers[name] = values[0]
            declared = response.getheader('Content-Length')
            if declared is not None:
                if not declared.isascii() or not declared.isdigit() or int(declared) > max_response_bytes:
                    raise WeChatPayOutcomeUnknown('Invalid or oversized response')
            chunks = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WeChatPayOutcomeUnknown('Response deadline exceeded')
                if socket is not None:
                    socket.settimeout(remaining)
                chunk = response.read1(min(65536, max_response_bytes + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_response_bytes:
                    raise WeChatPayOutcomeUnknown('Oversized response')
                chunks.append(chunk)
            if declared is not None and size != int(declared):
                raise WeChatPayOutcomeUnknown('Truncated provider response')
            return TransportResponse(response.status, signed_headers, b''.join(chunks))
        finally:
            connection.close()


def _identifier(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise WeChatPayInputError(f'Invalid {label}')
    return value


def _text(value, label, limit, *, optional=False, bytes_limit=False):
    if not isinstance(value, str) or (not value and not optional) or any(ord(c) < 32 for c in value):
        raise WeChatPayInputError(f'Invalid {label}')
    try:
        encoded = value.encode('utf-8')
        size = len(encoded) if bytes_limit else len(value)
    except UnicodeError as exc:
        raise WeChatPayInputError(f'Invalid {label}') from exc
    if size > limit:
        raise WeChatPayInputError(f'Invalid {label}')
    return value


def _amount(value, label):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise WeChatPayInputError(f'Invalid {label}')
    return value


def _notify_url(value):
    _text(value, 'notification URL', 255, bytes_limit=True)
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port  # validates malformed ports
        if (url.scheme != 'https' or not host or not url.path or url.path == '/'
                or url.username is not None or url.password is not None or url.query or url.fragment
                or any(c.isspace() for c in value) or host.lower().endswith(('.local', '.localhost'))
                or host.lower() == 'localhost' or '.' not in host or (port is not None and port < 1)):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        labels = host.split('.')
        if (len(host) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                                   for label in labels)):
            raise ValueError
    except ValueError as exc:
        raise WeChatPayInputError('Invalid notification URL') from exc
    return value


def _strict_object(raw):
    def object_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('duplicate key')
            obj[key] = value
        return obj
    def invalid_constant(_):
        raise ValueError('invalid constant')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=object_pairs, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise WeChatPayOutcomeUnknown('Invalid authenticated provider response') from exc
    if not isinstance(result, dict):
        raise WeChatPayOutcomeUnknown('Invalid authenticated provider response')
    return result


class WeChatPayGateway:
    """API-v3 gateway; the caller owns persistence, reconciliation and retries.

    ``transport`` is injectable for isolated tests and must implement ``send``.
    The default transport has a fixed WeChat Pay hostname and TLS verification.
    Before any call, compare ``app_id`` and ``merchant_id`` with the immutable
    payment-intent snapshot; a credential change must not resend an old intent
    under another merchant or application.
    """

    def __init__(self, protocol: WeChatPayV3, *, transport=None, timeout: float = 10):
        if not isinstance(protocol, WeChatPayV3):
            raise WeChatPayInputError('A configured WeChat Pay protocol is required')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
            raise WeChatPayInputError('Invalid HTTP timeout')
        self._protocol = protocol
        self._transport = transport if transport is not None else OfficialHTTPTransport()
        self._timeout = timeout

    @property
    def app_id(self):
        return self._protocol.app_id

    @property
    def merchant_id(self):
        return self._protocol.merchant_id

    def _authenticated_request(self, method, path, payload=None):
        body = b'' if payload is None else json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        signed = self._protocol.sign_request(method, path, body)
        headers = {'Authorization': signed.authorization, 'Accept': 'application/json',
                   'Content-Type': 'application/json', 'Connection': 'close'}
        try:
            response = self._transport.send(signed.method, signed.path_and_query, headers, signed.body,
                                            timeout=self._timeout, max_response_bytes=_MAX_RESPONSE_BYTES)
            if (not isinstance(response, TransportResponse) or type(response.status) is not int
                    or not 100 <= response.status <= 599 or not isinstance(response.body, bytes)
                    or len(response.body) > _MAX_RESPONSE_BYTES or not isinstance(response.headers, dict)):
                raise WeChatPayOutcomeUnknown('Invalid or oversized transport response')
            # Verify even error and empty responses before exposing status/data.
            self._protocol.verify_signed_message(response.headers, response.body)
        except WeChatPayOutcomeUnknown:
            raise
        except (WeChatPayProtocolError, OSError, TimeoutError, http.client.HTTPException, ValueError, TypeError):
            # Transport exceptions may embed request URLs, headers or bodies.
            # Do not accidentally expose them when an upper layer logs a trace.
            raise WeChatPayOutcomeUnknown('WeChat Pay result is unconfirmed; query before retrying') from None
        normalized = {str(k).lower(): v for k, v in response.headers.items()}
        signature_fields = tuple((name, normalized[name]) for name in (
            'wechatpay-serial', 'wechatpay-timestamp', 'wechatpay-nonce', 'wechatpay-signature'))
        return AuthenticatedResponseEvidence(response.status, hashlib.sha256(response.body).hexdigest(),
                                             signature_fields, response.body)

    def _request(self, method, path, payload=None, *, empty_response=False):
        response = self._authenticated_request(method, path, payload)
        if 400 <= response.status < 500:
            error = _strict_object(response.raw_body)
            code = error.get('code')
            if not isinstance(code, str) or not _ERROR_CODE.fullmatch(code):
                raise WeChatPayOutcomeUnknown('Invalid authenticated provider error')
            raise WeChatPayResponseError(response.status, code)
        if response.status != (204 if empty_response else 200):
            # Redirects are never followed. 5xx and unexpected success codes
            # cannot establish a final payment or refund state.
            raise WeChatPayOutcomeUnknown('Unexpected provider status; reconcile the operation')
        if empty_response:
            if response.raw_body:
                raise WeChatPayOutcomeUnknown('Unexpected close response body')
            return None
        return _strict_object(response.raw_body)

    def create_jsapi(self, out_trade_no: str, description: str, amount_fen: int, openid: str,
                     notify_url: str, time_expire: datetime | None = None) -> dict:
        _identifier(out_trade_no, _TRADE_NO, 'merchant order number')
        _text(description, 'description', 127)
        _amount(amount_fen, 'amount')
        _text(openid, 'openid', 128)
        _notify_url(notify_url)
        payload = {'appid': self._protocol.app_id, 'mchid': self._protocol.merchant_id,
                   'description': description, 'out_trade_no': out_trade_no,
                   'notify_url': notify_url, 'amount': {'total': amount_fen, 'currency': 'CNY'},
                   'payer': {'openid': openid}}
        if time_expire is not None:
            if not isinstance(time_expire, datetime) or time_expire.tzinfo is None or time_expire.utcoffset() is None:
                raise WeChatPayInputError('Expiration must be an aware datetime')
            expire = time_expire.replace(microsecond=0)
            remaining = (expire.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
            # WeChat can silently adjust out-of-range deadlines. Leave enough
            # time for our bounded request, and never infer closure from this
            # local deadline: the caller must still query/close remotely.
            if not 60 + self._timeout <= remaining <= 15 * 24 * 60 * 60:
                raise WeChatPayInputError('Expiration must be within the permitted payment window')
            payload['time_expire'] = expire.isoformat(timespec='seconds')
        result = self._request('POST', '/v3/pay/transactions/jsapi', payload)
        if not isinstance(result.get('prepay_id'), str) or not _PREPAY_ID.fullmatch(result['prepay_id']):
            raise WeChatPayOutcomeUnknown('Invalid prepayment response')
        return result

    def query_order(self, out_trade_no: str) -> dict:
        _identifier(out_trade_no, _TRADE_NO, 'merchant order number')
        path = '/v3/pay/transactions/out-trade-no/' + quote(out_trade_no, safe='')
        path += '?' + urlencode({'mchid': self._protocol.merchant_id})
        result = self._request('GET', path)
        if (result.get('out_trade_no') != out_trade_no or result.get('mchid') != self._protocol.merchant_id
                or result.get('appid') != self._protocol.app_id or not isinstance(result.get('trade_state'), str)
                or result['trade_state'] not in _TRADE_STATES
                or (result.get('trade_type') is not None and result.get('trade_type') != 'JSAPI')):
            raise WeChatPayOutcomeUnknown('Authenticated order response does not match the request')
        return result

    def query_order_authenticated(self, out_trade_no: str) -> AuthenticatedResponseEvidence:
        """Query once and retain the signed bytes, without interpreting the result.

        The caller must keep the response in restricted encrypted storage and
        reconcile its authenticated content against the locked order. The HTTP
        status, request path and transport origin are not covered by WeChat's
        response signature. Neither this method nor an archived response proves
        that a payment may be dispatched or settled.
        """
        _identifier(out_trade_no, _TRADE_NO, 'merchant order number')
        path = '/v3/pay/transactions/out-trade-no/' + quote(out_trade_no, safe='')
        path += '?' + urlencode({'mchid': self._protocol.merchant_id})
        return self._authenticated_request('GET', path)

    def close_order(self, out_trade_no: str) -> None:
        _identifier(out_trade_no, _TRADE_NO, 'merchant order number')
        path = '/v3/pay/transactions/out-trade-no/' + quote(out_trade_no, safe='') + '/close'
        return self._request('POST', path, {'mchid': self._protocol.merchant_id}, empty_response=True)

    @staticmethod
    def _refund_payload(out_refund_no, amount_fen, total_fen, *, out_trade_no, transaction_id, reason, notify_url):
        _identifier(out_refund_no, _REFUND_NO, 'refund number')
        _amount(amount_fen, 'refund amount')
        _amount(total_fen, 'order amount')
        if amount_fen > total_fen or bool(out_trade_no) == bool(transaction_id):
            raise WeChatPayInputError('Invalid refund amounts or transaction identity')
        payload = {'out_refund_no': out_refund_no,
                   'amount': {'refund': amount_fen, 'total': total_fen, 'currency': 'CNY'}}
        if out_trade_no:
            payload['out_trade_no'] = _identifier(out_trade_no, _TRADE_NO, 'merchant order number')
        else:
            payload['transaction_id'] = _text(transaction_id, 'WeChat transaction ID', 32)
        if reason:
            payload['reason'] = _text(reason, 'refund reason', 80, bytes_limit=True)
        elif not isinstance(reason, str):
            raise WeChatPayInputError('Invalid refund reason')
        if notify_url is not None:
            payload['notify_url'] = _notify_url(notify_url)
        return payload

    def create_refund(self, out_refund_no: str, amount_fen: int, total_fen: int, *,
                      out_trade_no: str | None = None, transaction_id: str | None = None,
                      reason: str = '', notify_url: str | None = None) -> dict:
        payload = self._refund_payload(out_refund_no, amount_fen, total_fen, out_trade_no=out_trade_no,
                                       transaction_id=transaction_id, reason=reason, notify_url=notify_url)
        result = self._request('POST', '/v3/refund/domestic/refunds', payload)
        self._check_refund(result, out_refund_no)
        actual = result.get('amount')
        if (not isinstance(actual, dict) or type(actual.get('refund')) is not int or actual['refund'] != amount_fen
                or type(actual.get('total')) is not int or actual['total'] != total_fen or actual.get('currency') != 'CNY'
                or (out_trade_no and result.get('out_trade_no') != out_trade_no)
                or (transaction_id and result.get('transaction_id') != transaction_id)):
            raise WeChatPayOutcomeUnknown('Authenticated refund response does not match the request')
        return result

    def create_refund_authenticated(self, out_refund_no: str, amount_fen: int, total_fen: int, *,
                                    out_trade_no: str | None = None, transaction_id: str | None = None,
                                    reason: str = '', notify_url: str | None = None) -> AuthenticatedResponseEvidence:
        """Submit once and return verified bytes; no outcome or refund is inferred.

        A timeout or unsigned response is unknown. Never change the refund
        number or automatically repeat a submission because of this result.
        """
        payload = self._refund_payload(out_refund_no, amount_fen, total_fen, out_trade_no=out_trade_no,
                                       transaction_id=transaction_id, reason=reason, notify_url=notify_url)
        return self._authenticated_request('POST', '/v3/refund/domestic/refunds', payload)

    def query_refund(self, out_refund_no: str) -> dict:
        _identifier(out_refund_no, _REFUND_NO, 'refund number')
        result = self._request('GET', '/v3/refund/domestic/refunds/' + quote(out_refund_no, safe=''))
        self._check_refund(result, out_refund_no)
        return result

    def query_refund_authenticated(self, out_refund_no: str) -> AuthenticatedResponseEvidence:
        """Query once and retain verified bytes, including authenticated errors.

        This low-level evidence API does not classify HTTP status or validate
        refund identity, JSON or financial state; the caller must do so before
        any business transition. A signed 4xx or 5xx is not final refund state.
        Network or signature failure raises OutcomeUnknown with no evidence.
        """
        _identifier(out_refund_no, _REFUND_NO, 'refund number')
        return self._authenticated_request('GET', '/v3/refund/domestic/refunds/' + quote(out_refund_no, safe=''))

    @staticmethod
    def _check_refund(result, out_refund_no):
        if (result.get('out_refund_no') != out_refund_no or not isinstance(result.get('status'), str)
                or result['status'] not in _REFUND_STATES
                or not isinstance(result.get('refund_id'), str) or not result['refund_id']):
            raise WeChatPayOutcomeUnknown('Authenticated refund response does not match the request')
