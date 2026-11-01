"""Test-only durable inbox for authenticated WeChat payment notifications.

Callers must verify/decrypt the channel message before ingestion; there is no
public endpoint here. Only a controlled subset of the plaintext is retained.
"""
import hashlib
import json
import logging
import re

from django.db import transaction

from integrations.wechatpay_gateway import WeChatPayOutcomeUnknown
from integrations.wechatpay_payment_evidence import parse_payment_notification
from integrations.wechatpay_v3 import VerifiedNotification

from .direct_payment import DirectPaymentError, _require_test_domain, confirm_verified_payment
from .direct_payment_evidence_compare import contradicts_saved_payment, flag_payment_evidence_risk
from .direct_payment_delivery_models import DirectPaymentInbox, DirectPaymentInboxConflict, DirectPaymentInboxObservation
from .direct_payment_models import DirectPaymentIntent
from .direct_payment_delivery_models import DirectPaymentOutbox
from .models import Order

MAX_PAYLOAD_BYTES = 256 * 1024
_HEX = re.compile(r'[0-9a-f]{64}\Z')
logger = logging.getLogger(__name__)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _controlled(payload):
    """Discard descriptions, bank data, arbitrary attach and goods remarks."""
    def scalar(value):
        # An authenticated but malformed nested object must not smuggle
        # arbitrary sensitive keys into the persisted allowlist.
        if (value is None or type(value) is bool
                or (type(value) is int and -(2**63) <= value <= 2**63 - 1)
                or (isinstance(value, str) and len(value) <= 256)):
            return value
        return {'invalid_type': True}

    keys = ('appid', 'mchid', 'out_trade_no', 'transaction_id', 'trade_type', 'trade_state', 'success_time')
    result = {key: scalar(payload[key]) for key in keys if key in payload}
    for field, allowed in (('payer', ('openid',)), ('amount', ('total', 'payer_total', 'currency', 'payer_currency'))):
        if field in payload:
            value = payload[field]
            result[field] = {key: scalar(value[key]) for key in allowed if key in value} if isinstance(value, dict) else scalar(value)
    if 'promotion_detail' in payload:
        promotions = payload['promotion_detail']
        allowed = ('coupon_id', 'scope', 'type', 'amount', 'stock_id', 'wechatpay_contribute', 'merchant_contribute', 'other_contribute', 'currency')
        if isinstance(promotions, list):
            controlled = []
            for item in promotions:
                if not isinstance(item, dict):
                    controlled.append(scalar(item))
                    continue
                row = {key: scalar(item[key]) for key in allowed if key in item}
                if 'goods_detail' in item:
                    goods = item['goods_detail']
                    fields = ('goods_id', 'quantity', 'unit_price', 'discount_amount')
                    row['goods_detail'] = [{key: scalar(entry[key]) for key in fields if key in entry} if isinstance(entry, dict) else scalar(entry)
                                           for entry in goods] if isinstance(goods, list) else scalar(goods)
                controlled.append(row)
            result['promotion_detail'] = controlled
        else:
            result['promotion_detail'] = scalar(promotions)
    return result


def _freeze_conflicting_receipts(row):
    """Called with inbox locked; attribute each retained authenticated payload separately."""
    if (row.event_type != 'TRANSACTION.SUCCESS' or row.resource_type != 'encrypt-resource'
            or row.original_type != 'transaction'):
        return
    receipts = [(row.controlled_payload, row.controlled_hash)]
    receipts.extend(row.conflicts.values_list('controlled_payload', 'controlled_hash'))
    identities = set()
    for payload, digest in receipts:
        if not isinstance(payload, dict) or hashlib.sha256(_canonical(payload)).hexdigest() != digest:
            continue
        payer = payload.get('payer')
        if not isinstance(payer, dict):
            continue
        identity = (payload.get('appid'), payload.get('mchid'), payload.get('out_trade_no'), payer.get('openid'))
        if (all(isinstance(value, str) and value and value == value.strip() for value in identity)
                and identity[1] == row.merchant_id):
            identities.add(identity)
    if not identities:
        return
    heads = list(DirectPaymentIntent.objects.filter(merchant_order_no__in=[identity[2] for identity in identities])
                 .values('pk', 'order_id'))
    orders = {order.pk: order for order in Order.objects.select_for_update().filter(
        pk__in=sorted({head['order_id'] for head in heads})).order_by('pk')}
    intents = DirectPaymentIntent.objects.select_for_update().filter(
        pk__in=[head['pk'] for head in heads]).order_by('order_id')
    for intent in intents:
        order = orders.get(intent.order_id)
        identity = (intent.appid, intent.mchid, intent.merchant_order_no, intent.openid)
        if order and intent.account_id == order.customer_account_id and identity in identities:
            flag_payment_evidence_risk(intent)


def ingest_verified_payment_notice(merchant_id, notice):
    """Persist authenticated evidence before acknowledging an event.

    ``merchant_id`` is trusted server configuration, not a request parameter.
    Duplicate semantic data is idempotent even if the envelope bytes differ;
    differing signed data for the same event ID is retained as a conflict.
    """
    _require_test_domain()
    if (not isinstance(merchant_id, str) or not merchant_id or len(merchant_id) > 32
            or not isinstance(notice, VerifiedNotification)
            or not isinstance(notice.notification_id, str) or not notice.notification_id
            or len(notice.notification_id) > 36 or notice.event_type != 'TRANSACTION.SUCCESS'
            or notice.resource_type != 'encrypt-resource' or notice.original_type != 'transaction'
            or not isinstance(notice.payload, dict) or not isinstance(notice.signed_body_sha256, str)
            or not _HEX.fullmatch(notice.signed_body_sha256)):
        raise DirectPaymentError('支付通知身份或认证证据无效')
    try:
        full = _canonical({'event_type': notice.event_type, 'resource_type': notice.resource_type,
                           'original_type': notice.original_type, 'payload': notice.payload})
        if len(full) > MAX_PAYLOAD_BYTES:
            raise DirectPaymentError('支付通知超过保存上限')
        controlled = _controlled(notice.payload)
        clean = _canonical(controlled)
        controlled = json.loads(clean)
    except (TypeError, ValueError) as exc:
        raise DirectPaymentError('支付通知内容无法保存') from exc
    payload_hash = hashlib.sha256(full).hexdigest()
    controlled_hash = hashlib.sha256(clean).hexdigest()
    with transaction.atomic():
        row, _ = DirectPaymentInbox.objects.get_or_create(
            merchant_id=merchant_id, notification_id=notice.notification_id,
            defaults={'event_type': notice.event_type, 'resource_type': notice.resource_type,
                      'original_type': notice.original_type, 'payload_hash': payload_hash,
                      'controlled_hash': controlled_hash, 'signed_body_hash': notice.signed_body_sha256,
                      'controlled_payload': controlled},
        )
        row = DirectPaymentInbox.objects.select_for_update().get(pk=row.pk)
        DirectPaymentInboxObservation.objects.get_or_create(
            inbox=row, signed_body_hash=notice.signed_body_sha256, defaults={'payload_hash': payload_hash},
        )
        if row.payload_hash != payload_hash:
            DirectPaymentInboxConflict.objects.get_or_create(
                inbox=row, payload_hash=payload_hash,
                defaults={'controlled_hash': controlled_hash, 'signed_body_hash': notice.signed_body_sha256,
                          'controlled_payload': controlled},
            )
            if not row.has_conflict:
                row.has_conflict = True
            row.status, row.last_error = DirectPaymentInbox.Status.REVIEW, 'EVENT_CONTENT_CONFLICT'
            row.save(update_fields=['has_conflict', 'status', 'last_error', 'updated_at'])
            _freeze_conflicting_receipts(row)
        return row


def process_payment_inbox(inbox_id):
    """Apply one saved event atomically; no network or HTTP inside the lock."""
    _require_test_domain()
    with transaction.atomic():
        row = DirectPaymentInbox.objects.select_for_update().get(pk=inbox_id)
        if row.status == DirectPaymentInbox.Status.DONE and not row.has_conflict:
            return row
        row.attempts += 1
        if row.has_conflict:
            _freeze_conflicting_receipts(row)
            row.status, row.last_error = DirectPaymentInbox.Status.REVIEW, 'EVENT_CONTENT_CONFLICT'
            row.save(update_fields=['attempts', 'status', 'last_error', 'updated_at'])
            return row
        try:
            if hashlib.sha256(_canonical(row.controlled_payload)).hexdigest() != row.controlled_hash:
                raise DirectPaymentError('stored notification payload changed')
            payload = row.controlled_payload
            payer = payload.get('payer')
            if (row.event_type != 'TRANSACTION.SUCCESS' or row.resource_type != 'encrypt-resource'
                    or row.original_type != 'transaction' or not isinstance(payer, dict)
                    or any(not isinstance(value, str) or not value or value != value.strip()
                           for value in (payload.get('appid'), payload.get('mchid'),
                                         payload.get('out_trade_no'), payer.get('openid')))
                    or payload.get('mchid') != row.merchant_id):
                raise DirectPaymentError('payment identity mismatch')
            head = DirectPaymentIntent.objects.filter(merchant_order_no=payload['out_trade_no']).first()
            if not head:
                raise DirectPaymentError('payment intent not found')
            order = Order.objects.select_for_update().get(pk=head.order_id)
            intent = DirectPaymentIntent.objects.select_for_update().get(pk=head.pk)
            if (intent.mchid != payload['mchid'] or intent.appid != payload['appid']
                    or intent.merchant_order_no != payload['out_trade_no']
                    or intent.openid != payer['openid'] or intent.account_id != order.customer_account_id):
                raise DirectPaymentError('payment identity mismatch')
            row.intent = intent
            notice = VerifiedNotification(row.notification_id, row.event_type, row.resource_type,
                                          row.original_type, row.controlled_payload, row.signed_body_hash)
            try:
                evidence = parse_payment_notification(notice).verified_payment
            except WeChatPayOutcomeUnknown:
                flag_payment_evidence_risk(intent)
                raise
            # A different event ID may still concern a transaction already
            # accepted. Preserve both receipts and block later dispatch when
            # authenticated facts contradict the saved transaction. Missing
            # optional values alone are not proof of a contradiction.
            if contradicts_saved_payment(intent, evidence):
                if intent.status == DirectPaymentIntent.Status.PAID:
                    intent.status, intent.review_reason = DirectPaymentIntent.Status.REVIEW, 'PAYMENT_EVIDENCE_CONFLICT'
                    intent.save(update_fields=['status', 'review_reason', 'updated_at'])
                DirectPaymentOutbox.objects.filter(intent_id=intent.pk).update(
                    status=DirectPaymentOutbox.Status.REVIEW, last_error='PAYMENT_EVIDENCE_REVIEW',
                    lease_token='', lease_until=None,
                )
                row.status, row.last_error = DirectPaymentInbox.Status.REVIEW, 'PAYMENT_EVIDENCE_CONFLICT'
                row.save(update_fields=['attempts', 'intent', 'status', 'last_error', 'updated_at'])
                return row
            if evidence.amount_fen != intent.amount_fen or evidence.currency != intent.currency:
                flag_payment_evidence_risk(intent)
                row.status, row.last_error = DirectPaymentInbox.Status.REVIEW, 'PAYMENT_EVIDENCE_CONFLICT'
                row.save(update_fields=['attempts', 'intent', 'status', 'last_error', 'updated_at'])
                return row
            intent = confirm_verified_payment(intent.order_id, evidence, received_at=row.created_at)
            if intent.status != DirectPaymentIntent.Status.PAID:
                row.status, row.last_error = DirectPaymentInbox.Status.REVIEW, 'PAYMENT_REQUIRES_REVIEW'
            else:
                row.status, row.last_error = DirectPaymentInbox.Status.DONE, ''
        except (DirectPaymentError, WeChatPayOutcomeUnknown):
            # Preserve only a generic code: evidence can contain OpenIDs.
            row.status, row.last_error = DirectPaymentInbox.Status.REVIEW, 'EVIDENCE_REQUIRES_REVIEW'
        row.save(update_fields=['attempts', 'intent', 'status', 'last_error', 'updated_at'])
        return row


def scan_payment_inbox(limit=100):
    """Explicit recovery hook for unprocessed receipts; no scheduler is wired.

    REVIEW is deliberately excluded: ambiguous evidence requires an operator.
    An unexpected failure rolls back processing and leaves the row PENDING.
    """
    _require_test_domain()
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('invalid scan limit')
    ids = list(DirectPaymentInbox.objects.filter(status=DirectPaymentInbox.Status.PENDING)
               .order_by('id').values_list('id', flat=True)[:limit])
    results = []
    for pk in ids:
        try:
            results.append((pk, process_payment_inbox(pk).status))
        except Exception:
            # Do not put arbitrary exception text (which can include channel
            # data) into logs, and do not prevent unrelated rows progressing.
            logger.warning('支付收件箱处理失败: inbox_id=%s', pk)
            results.append((pk, 'retry'))
    return results
