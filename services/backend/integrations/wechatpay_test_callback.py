"""Isolated ASGI callback adapter used only by an explicitly enabled test URL.

There is no production URL, scheduler, payment action or external network call.
This adapter requires raw ASGI headers so it can reject duplicate signed
headers. An upstream proxy can still merge them before ASGI: production must
enforce and test that boundary independently.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from integrations.wechatpay_v3 import WeChatPayProtocolError, WeChatPayV3, _strict_json
from orders.direct_callback_models import (DirectCallbackEnvelope, DirectCallbackEnvelopeBody,
    DirectCallbackQuarantine, DirectCallbackQuarantineObservation)
from orders.direct_payment import DirectPaymentError, _require_test_domain
from orders.direct_payment_delivery_models import DirectPaymentInbox
from orders.direct_payment_evidence_compare import flag_payment_evidence_risk
from orders.direct_payment_inbox import ingest_verified_payment_notice
from orders.direct_payment_models import DirectPaymentIntent
from orders.direct_refund_delivery_models import DirectRefundInbox
from orders.direct_refund_inbox import RefundInboxError, _stop, ingest_verified_refund_notice
from orders.direct_refund_models import DirectPaymentRefund
from orders.models import Order

MAX_BODY_BYTES = 2 * 1024 * 1024
_KEY_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_SERIAL = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_NONCE = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_SIGNATURE = re.compile(r'[A-Za-z0-9+/=]{1,1024}\Z')
_FIELDS = (b'wechatpay-serial', b'wechatpay-timestamp', b'wechatpay-nonce', b'wechatpay-signature')


@dataclass(frozen=True, repr=False)
class TestCallbackConfig:
    """All credentials and the merchant identity are server-owned test config."""
    protocol: WeChatPayV3
    encryption_key_id: str
    encryption_key: bytes = field(repr=False)


def _config():
    if (not getattr(settings, 'DEBUG', False)
            or not getattr(settings, 'WECHAT_DIRECT_DOMAIN_TESTING', False)
            or not getattr(settings, 'WECHAT_DIRECT_CALLBACK_TESTING', False)):
        return None
    try:
        _require_test_domain()
    except DirectPaymentError:
        return None
    config = getattr(settings, 'WECHAT_DIRECT_CALLBACK_TEST_CONFIG', None)
    if (type(config) is not TestCallbackConfig or type(config.protocol) is not WeChatPayV3
            or not isinstance(config.encryption_key_id, str) or not _KEY_ID.fullmatch(config.encryption_key_id)
            or not isinstance(config.encryption_key, bytes) or len(config.encryption_key) != 32
            or len(config.protocol.merchant_id) > 32):
        return None
    return config


def _raw_headers(request):
    # WSGI loses duplicate-header information. Never infer uniqueness from it.
    scope = getattr(request, 'scope', None)
    if not isinstance(scope, dict) or not isinstance(scope.get('headers'), list):
        raise ValueError('raw ASGI headers required')
    found = {}
    lengths = []
    for item in scope['headers']:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError('invalid header')
        name, value = item
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            raise ValueError('invalid header')
        name = name.lower()
        if name == b'content-length':
            lengths.append(value)
        if name in _FIELDS:
            if name in found:
                raise ValueError('duplicate signed header')
            found[name] = value.decode('ascii')
    if len(lengths) > 1:
        raise ValueError('duplicate content length')
    if lengths:
        length = lengths[0]
        if not length.isdigit() or len(length) > 9 or int(length) > MAX_BODY_BYTES:
            raise ValueError('invalid content length')
    if set(found) != set(_FIELDS):
        raise ValueError('missing signed header')
    serial = found[b'wechatpay-serial']
    timestamp = found[b'wechatpay-timestamp']
    nonce = found[b'wechatpay-nonce']
    signature = found[b'wechatpay-signature']
    if (not _SERIAL.fullmatch(serial) or not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12
            or not _NONCE.fullmatch(nonce) or not _SIGNATURE.fullmatch(signature)):
        raise ValueError('invalid signed header')
    return {key.decode('ascii'): value for key, value in found.items()}, (int(lengths[0]) if lengths else None)


def _key_fingerprint(protocol, serial):
    """Record the public key actually used for the verified incoming message."""
    key = protocol._platform_keys.get(serial)
    if key is None:
        raise ValueError('unknown platform key')
    encoded = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(encoded).hexdigest()


def _quarantine(config, headers, body, *, reason):
    """Preserve authenticated input encrypted; no plaintext or secret in DB.

    The signed body is stored once per merchant/public key; each distinct
    signed header set is an encrypted observation, including WeChat retries.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    fingerprint = _key_fingerprint(config.protocol, headers['wechatpay-serial'])
    body_aad = f'body\n{config.protocol.merchant_id}\n{body_hash}\n{fingerprint}\n{config.encryption_key_id}'.encode('utf-8')
    nonce = secrets.token_bytes(12)
    encrypted_body = AESGCM(config.encryption_key).encrypt(nonce, body, body_aad)
    row, _ = DirectCallbackQuarantine.objects.get_or_create(
        merchant_id=config.protocol.merchant_id, body_hash=body_hash, platform_key_hash=fingerprint,
        defaults={'platform_serial': headers['wechatpay-serial'], 'encryption_key_id': config.encryption_key_id,
                  'body_nonce': nonce, 'encrypted_body': encrypted_body},
    )
    header_bytes = json.dumps(headers, sort_keys=True, separators=(',', ':')).encode('ascii')
    receipt_hash = hashlib.sha256(body_hash.encode('ascii') + b'\n' + fingerprint.encode('ascii') + b'\n' + header_bytes).hexdigest()
    header_aad = f'headers\n{row.pk}\n{receipt_hash}\n{config.encryption_key_id}'.encode('utf-8')
    header_nonce = secrets.token_bytes(12)
    encrypted_headers = AESGCM(config.encryption_key).encrypt(header_nonce, header_bytes, header_aad)
    DirectCallbackQuarantineObservation.objects.get_or_create(
        quarantine=row, receipt_hash=receipt_hash,
        defaults={'encryption_key_id': config.encryption_key_id, 'headers_nonce': header_nonce,
                  'encrypted_headers': encrypted_headers, 'reason': reason},
    )


def _notification_id(body):
    """An ID is usable only if the entire signed JSON is unambiguous."""
    try:
        envelope = _strict_json(body)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None
    value = envelope.get('id') if isinstance(envelope, dict) else None
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > 36
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    return value


def _envelope(merchant_id, notification_id):
    marker, _ = DirectCallbackEnvelope.objects.get_or_create(
        merchant_id=merchant_id, notification_id=notification_id)
    return DirectCallbackEnvelope.objects.select_for_update().get(pk=marker.pk)


def _lock_known_inboxes(merchant_id, notification_id):
    # Uniform payment -> refund order avoids cross-type inbox lock inversion.
    payment = DirectPaymentInbox.objects.select_for_update().filter(
        merchant_id=merchant_id, notification_id=notification_id).first()
    refund = DirectRefundInbox.objects.select_for_update().filter(
        merchant_id=merchant_id, notification_id=notification_id).first()
    return payment, refund


def _content_valid(payload, expected_hash):
    try:
        value = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False).encode('utf-8')
        return hashlib.sha256(value).hexdigest() == expected_hash
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return False


def _hold_known_inboxes(merchant_id, payment, refund, *, incoming=None):
    """Stop only business facts attributed to authenticated inbox payloads.

    Callers hold payment then refund inbox locks. Identify all involved orders
    first and lock them in ascending order before intent/refund rows. No order
    is inferred from an undecodable quarantined message.
    """
    identities = []
    refund_contexts = []
    intent_ids = set()

    def payment_identity(payload):
        payer = payload.get('payer') if isinstance(payload, dict) else None
        if isinstance(payer, dict):
            identity = (payload.get('appid'), payload.get('mchid'), payload.get('out_trade_no'), payer.get('openid'))
            if (all(isinstance(value, str) and value and value == value.strip() for value in identity)
                    and identity[1] == merchant_id and len(identity[2]) <= 32):
                head = DirectPaymentIntent.objects.filter(merchant_order_no=identity[2]).values('pk', 'order_id').first()
                if head:
                    identities.append((head['pk'], identity))
                    intent_ids.add(head['pk'])

    def refund_identity(payload, refund_id=None):
        if not isinstance(payload, dict):
            return
        number = payload.get('out_refund_no')
        if not isinstance(number, str) or not number or len(number) > 64 or payload.get('mchid') != merchant_id:
            return
        query = DirectPaymentRefund.objects.filter(merchant_refund_no=number)
        if refund_id is not None:
            query = query.filter(pk=refund_id)
        head = query.values('pk', 'intent_id').first()
        if head:
            refund_contexts.append((head['pk'], head['intent_id'], payload))
            intent_ids.add(head['intent_id'])

    if payment:
        if _content_valid(payment.controlled_payload, payment.controlled_hash):
            payment_identity(payment.controlled_payload)
        for payload, digest in payment.conflicts.values_list('controlled_payload', 'controlled_hash'):
            if _content_valid(payload, digest):
                payment_identity(payload)
    if refund:
        if refund.refund_id and _content_valid(refund.controlled_payload, refund.controlled_hash):
            refund_identity(refund.controlled_payload, refund.refund_id)
        for payload, digest in refund.conflicts.values_list('controlled_payload', 'controlled_hash'):
            if _content_valid(payload, digest):
                refund_identity(payload)
    if incoming is not None:
        if incoming.event_type == 'TRANSACTION.SUCCESS':
            payment_identity(incoming.payload)
        elif incoming.event_type in ('REFUND.SUCCESS', 'REFUND.CLOSED', 'REFUND.ABNORMAL'):
            refund_identity(incoming.payload)
    heads = list(DirectPaymentIntent.objects.filter(pk__in=intent_ids).values('pk', 'order_id'))
    orders = {order.pk: order for order in Order.objects.select_for_update().filter(
        pk__in=sorted({head['order_id'] for head in heads})).order_by('pk')}
    intents = {intent.pk: intent for intent in DirectPaymentIntent.objects.select_for_update().filter(
        pk__in=intent_ids).order_by('order_id')}
    for intent_id, identity in identities:
        intent = intents.get(intent_id)
        order = orders.get(intent.order_id) if intent else None
        if (order and intent.account_id == order.customer_account_id
                and (intent.appid, intent.mchid, intent.merchant_order_no, intent.openid) == identity):
            flag_payment_evidence_risk(intent)
    refunds = {item.pk: item for item in DirectPaymentRefund.objects.select_for_update().filter(
        pk__in=sorted({context[0] for context in refund_contexts})).order_by('pk')}
    for refund_id, intent_id, payload in refund_contexts:
        original = refunds.get(refund_id)
        intent = intents.get(intent_id)
        order = orders.get(intent.order_id) if intent else None
        if (original and intent and order and intent.account_id == order.customer_account_id
                and isinstance(payload, dict) and intent.mchid == merchant_id == payload.get('mchid')
                and payload.get('out_trade_no') == intent.merchant_order_no
                and intent.transaction_id and payload.get('transaction_id') == intent.transaction_id
                and payload.get('out_refund_no') == original.merchant_refund_no):
            _stop(intent, original)
    if payment:
        payment.status, payment.last_error, payment.has_conflict = 'REVIEW', 'QUARANTINED_ID_CONFLICT', True
        payment.save(update_fields=['status', 'last_error', 'has_conflict', 'updated_at'])
    if refund:
        refund.status, refund.last_error, refund.has_conflict = 'REVIEW', 'QUARANTINED_ID_CONFLICT', True
        refund.save(update_fields=['status', 'last_error', 'has_conflict', 'updated_at'])


def _record_unusable_body(marker, observation, body_hash, merchant_id, payment, refund):
    """Quarantine creates a conflict only when another body is distinguishable."""
    prior_hashes = {row.signed_body_hash for row in (payment, refund) if row is not None}
    if body_hash in prior_hashes:
        observation.accepted = True
        if payment and payment.signed_body_hash == body_hash:
            observation.event_kind = 'PAYMENT'
        elif refund and refund.signed_body_hash == body_hash:
            observation.event_kind = 'REFUND'
    if (not observation.accepted and (marker.bodies.exclude(pk=observation.pk).exists()
                                       or any(value != body_hash for value in prior_hashes))):
        marker.has_conflict = True
        marker.save(update_fields=['has_conflict', 'updated_at'])
    observation.quarantined = True
    observation.save(update_fields=['quarantined', 'accepted', 'event_kind', 'updated_at'])
    if marker.has_conflict:
        _hold_known_inboxes(merchant_id, payment, refund)


@csrf_exempt
@transaction.non_atomic_requests
def isolated_callback(request):
    """Only an independently configured test URL may dispatch to this handler.

    A 204 means a fully verified/decrypted event was committed to the inbox,
    not that payment, dispatch, or refund was completed. Authenticated but
    undecodable inputs are encrypted into quarantine and receive non-ACK. No
    historical replay or background work is performed here.
    """
    config = _config()
    if config is None:
        return HttpResponse(status=404)
    if request.method != 'POST':
        return HttpResponse(status=405)
    try:
        headers, declared_length = _raw_headers(request)
        body = request.body
        if not body or len(body) > MAX_BODY_BYTES or (declared_length is not None and declared_length != len(body)):
            raise ValueError('invalid body')
        config.protocol.verify_signed_message(headers, body)
    except (ValueError, UnicodeError, RequestDataTooBig, WeChatPayProtocolError):
        return HttpResponse(status=400)
    # A nested transaction cannot prove durability before an HTTP response.
    if transaction.get_connection().in_atomic_block:
        return HttpResponse(status=503)
    try:
        notification_id = _notification_id(body)
        try:
            notice = config.protocol.decrypt_notification(headers, body)
        except WeChatPayProtocolError:
            notice = None
        with transaction.atomic(durable=True):
            if notification_id is None:
                # Never guess an ID from duplicate-key or malformed JSON.
                _quarantine(config, headers, body, reason='AUTHENTICATED_INVALID')
                return HttpResponse(status=503)
            marker = _envelope(config.protocol.merchant_id, notification_id)
            body_hash = hashlib.sha256(body).hexdigest()
            observation, _ = DirectCallbackEnvelopeBody.objects.get_or_create(envelope=marker, body_hash=body_hash)
            payment, refund = _lock_known_inboxes(config.protocol.merchant_id, notification_id)
            if notice is None:
                _quarantine(config, headers, body, reason='AUTHENTICATED_INVALID')
                # A body already accepted before a temporary key failure does
                # not conflict with its own later retransmission.
                _record_unusable_body(marker, observation, body_hash, config.protocol.merchant_id, payment, refund)
                return HttpResponse(status=503)
            if notice.notification_id != notification_id:
                raise DirectPaymentError('authenticated event ID mismatch')
            kind = 'PAYMENT' if notice.event_type == 'TRANSACTION.SUCCESS' else 'REFUND'
            conflicting = (marker.has_conflict
                or marker.bodies.filter(quarantined=True, accepted=False).exclude(pk=observation.pk).exists()
                or marker.bodies.filter(accepted=True).exclude(event_kind=kind).exists()
                or (kind == 'PAYMENT' and refund is not None)
                or (kind == 'REFUND' and payment is not None))
            if conflicting:
                marker.has_conflict = True
                marker.save(update_fields=['has_conflict', 'updated_at'])
                _quarantine(config, headers, body, reason='AUTHENTICATED_ID_CONFLICT')
                observation.quarantined = True
                observation.save(update_fields=['quarantined', 'updated_at'])
                # Never invoke an inbox that could lock a subset of orders
                # before a cross-type conflict has acquired the full set.
                _hold_known_inboxes(config.protocol.merchant_id, payment, refund, incoming=notice)
                return HttpResponse(status=503)
            try:
                if notice.event_type == 'TRANSACTION.SUCCESS':
                    ingest_verified_payment_notice(config.protocol.merchant_id, notice)
                elif notice.event_type in ('REFUND.SUCCESS', 'REFUND.CLOSED', 'REFUND.ABNORMAL'):
                    ingest_verified_refund_notice(config.protocol.merchant_id, notice)
                else:
                    raise DirectPaymentError('unsupported event')
            except (DirectPaymentError, RefundInboxError):
                # The inbox transaction has rolled back; keep this signed
                # body, but do not acknowledge or mark it accepted.
                _quarantine(config, headers, body, reason='AUTHENTICATED_INBOX_REJECTED')
                _record_unusable_body(marker, observation, body_hash, config.protocol.merchant_id, payment, refund)
                return HttpResponse(status=503)
            observation.accepted = True
            observation.event_kind = kind
            observation.save(update_fields=['accepted', 'event_kind', 'updated_at'])
    except Exception:
        # Never log or return channel bytes, signature data or exception text.
        return HttpResponse(status=503)
    return HttpResponse(status=204)
