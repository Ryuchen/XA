"""Test-only durable intake of authenticated refund notifications.

Only a server-trusted merchant and a notification produced by the signature-
verifying protocol may be passed here. No HTTP endpoint, channel query, refund
submission or scheduler is provided. A notification is never refund success;
it must later be reconciled against authenticated query evidence.

The previous-event read occurs after the refund row lock. MySQL 8 MUST use
READ COMMITTED so a transaction that began before a competing receipt commits
can see it; locking other inbox rows here would invert inbox -> order locks.
"""
import hashlib
import json
import re

from django.db import connection, transaction

from integrations.wechatpay_gateway import WeChatPayOutcomeUnknown
from integrations.wechatpay_refund_evidence import parse_refund_notification
from integrations.wechatpay_v3 import VerifiedNotification

from .direct_payment import _require_test_domain
from .direct_payment_delivery_models import DirectPaymentOutbox
from .direct_payment_models import DirectPaymentIntent
from .direct_refund_delivery_models import DirectRefundInbox, DirectRefundInboxConflict, DirectRefundInboxObservation
from .direct_refund_models import DirectPaymentRefund
from .models import Order

MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_EVENTS = frozenset(('REFUND.SUCCESS', 'REFUND.CLOSED', 'REFUND.ABNORMAL'))


class RefundInboxError(ValueError):
    pass


def _require_supported_isolation():
    if connection.vendor == 'mysql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT @@transaction_isolation')
            value = cursor.fetchone()[0]
        if str(value).upper().replace('_', '-').replace(' ', '-') != 'READ-COMMITTED':
            raise RefundInboxError('退款通知处理要求 MySQL READ COMMITTED 隔离级别')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _controlled(payload):
    def scalar(value):
        if (value is None or type(value) is bool or (type(value) is int and -(2**63) <= value < 2**63)
                or (isinstance(value, str) and len(value) <= 256)):
            return value
        return {'invalid_type': True}
    keys = ('mchid', 'out_trade_no', 'transaction_id', 'out_refund_no', 'refund_id', 'refund_status', 'success_time')
    result = {key: scalar(payload[key]) for key in keys if key in payload}
    if 'amount' in payload:
        amount = payload['amount']
        allowed = ('total', 'refund', 'payer_total', 'payer_refund')
        result['amount'] = ({key: scalar(amount[key]) for key in allowed if key in amount}
                            if isinstance(amount, dict) else scalar(amount))
    return result


def _locked_match(merchant_id, payload):
    """Lock order -> intent -> refund only for a fully matching identity."""
    number = payload.get('out_refund_no')
    if not isinstance(number, str) or not number or len(number) > 64:
        return None
    head = DirectPaymentRefund.objects.filter(merchant_refund_no=number).values('pk', 'intent_id').first()
    if not head:
        return None
    intent_head = DirectPaymentIntent.objects.filter(pk=head['intent_id']).values('order_id').first()
    if not intent_head:
        return None
    order = Order.objects.select_for_update().get(pk=intent_head['order_id'])
    intent = DirectPaymentIntent.objects.select_for_update().get(pk=head['intent_id'])
    refund = DirectPaymentRefund.objects.select_for_update().get(pk=head['pk'], intent=intent)
    if (merchant_id != intent.mchid or payload.get('mchid') != intent.mchid
            or payload.get('out_trade_no') != intent.merchant_order_no
            or not intent.transaction_id or payload.get('transaction_id') != intent.transaction_id
            or payload.get('out_refund_no') != refund.merchant_refund_no
            or intent.account_id != order.customer_account_id):
        return None
    return order, intent, refund


def _stop(intent, refund):
    # Preserve completed channel and order facts; all other refund states are
    # blocked from issuing another refund number until reconciled.
    if refund.status not in (DirectPaymentRefund.Status.SUCCESS, DirectPaymentRefund.Status.REVIEW):
        refund.status = DirectPaymentRefund.Status.REVIEW
        refund.save(update_fields=['status', 'updated_at'])
    if intent.status != DirectPaymentIntent.Status.REVIEW:
        intent.status = DirectPaymentIntent.Status.REVIEW
        intent.review_reason = 'REFUND_NOTIFICATION_CONFLICT'
        intent.save(update_fields=['status', 'review_reason', 'updated_at'])
    DirectPaymentOutbox.objects.filter(intent=intent).update(
        status=DirectPaymentOutbox.Status.REVIEW, last_error='REFUND_NOTIFICATION_REVIEW',
        lease_token='', lease_until=None)


def _conflicts(candidate, previous):
    if (candidate.channel_refund_id != previous.channel_refund_id or candidate.amount_fen != previous.amount_fen
            or candidate.total_fen != previous.total_fen or candidate.payer_total_fen != previous.payer_total_fen
            or candidate.payer_refund_fen != previous.payer_refund_fen):
        return True
    if {candidate.status, previous.status} in ({'CLOSED', 'SUCCESS'}, {'CLOSED', 'ABNORMAL'}):
        return True
    return candidate.status == previous.status == 'SUCCESS' and candidate.success_at != previous.success_at


def ingest_verified_refund_notice(merchant_id, notice):
    """Durably observe a decrypted notification; never mark a refund successful."""
    _require_test_domain()
    if (not isinstance(merchant_id, str) or not merchant_id or len(merchant_id) > 32
            or not isinstance(notice, VerifiedNotification)
            or not isinstance(notice.notification_id, str) or not notice.notification_id
            or len(notice.notification_id) > 36 or not isinstance(notice.event_type, str) or notice.event_type not in _EVENTS
            or notice.resource_type != 'encrypt-resource' or notice.original_type != 'refund'
            or not isinstance(notice.payload, dict) or not isinstance(notice.signed_body_sha256, str)
            or not _HEX.fullmatch(notice.signed_body_sha256)):
        raise RefundInboxError('退款通知身份或认证证据无效')
    try:
        full = _canonical({'event_type': notice.event_type, 'resource_type': notice.resource_type,
                           'original_type': notice.original_type, 'payload': notice.payload})
        if len(full) > MAX_PAYLOAD_BYTES:
            raise RefundInboxError('退款通知超过保存上限')
        clean = _canonical(_controlled(notice.payload))
        controlled = json.loads(clean)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RefundInboxError('退款通知内容无法保存') from exc
    payload_hash, controlled_hash = hashlib.sha256(full).hexdigest(), hashlib.sha256(clean).hexdigest()
    try:
        candidate = parse_refund_notification(notice)
    except WeChatPayOutcomeUnknown:
        candidate = None

    with transaction.atomic():
        _require_supported_isolation()
        row, _ = DirectRefundInbox.objects.get_or_create(
            merchant_id=merchant_id, notification_id=notice.notification_id,
            defaults={'event_type': notice.event_type, 'resource_type': notice.resource_type,
                      'original_type': notice.original_type, 'payload_hash': payload_hash,
                      'controlled_hash': controlled_hash, 'signed_body_hash': notice.signed_body_sha256,
                      'controlled_payload': controlled})
        row = DirectRefundInbox.objects.select_for_update().get(pk=row.pk)
        DirectRefundInboxObservation.objects.get_or_create(
            inbox=row, signed_body_hash=notice.signed_body_sha256, defaults={'payload_hash': payload_hash})
        changed = row.payload_hash != payload_hash
        if changed:
            DirectRefundInboxConflict.objects.get_or_create(
                inbox=row, payload_hash=payload_hash, defaults={'event_type': notice.event_type,
                    'controlled_hash': controlled_hash, 'signed_body_hash': notice.signed_body_sha256,
                    'controlled_payload': controlled})
            row.has_conflict = True
            row.status, row.last_error = DirectRefundInbox.Status.REVIEW, 'EVENT_CONTENT_CONFLICT'
            # The original event was already bound to this exact refund. A
            # conflicting signed payload under the same ID compromises that
            # event even if its new identity names a different refund. Never
            # bind or lock the unrelated refund named by the changed payload.
            if row.refund_id:
                original = _locked_match(merchant_id, row.controlled_payload)
                if original and original[2].pk == row.refund_id:
                    _stop(original[1], original[2])
            row.save(update_fields=['has_conflict', 'status', 'last_error', 'updated_at'])
            return row
        match = _locked_match(merchant_id, controlled)
        if match:
            _, intent, refund = match
            row.refund = refund
            conflict = candidate is None
            if candidate:
                conflict |= (candidate.amount_fen != refund.amount_fen or candidate.total_fen != refund.total_fen
                             or (intent.payer_total_fen is not None and candidate.payer_total_fen != intent.payer_total_fen)
                             or (refund.channel_refund_id is not None and candidate.channel_refund_id != refund.channel_refund_id))
                if refund.status in (DirectPaymentRefund.Status.SUCCESS, DirectPaymentRefund.Status.CLOSED):
                    conflict |= (refund.payer_refund_fen is not None and candidate.payer_refund_fen != refund.payer_refund_fen)
                    if refund.status == DirectPaymentRefund.Status.SUCCESS:
                        conflict |= (candidate.status == 'CLOSED' or
                                     (candidate.status == 'SUCCESS' and candidate.success_at != refund.success_at))
                    else:
                        conflict |= candidate.status in ('SUCCESS', 'ABNORMAL')
                previous_rows = DirectRefundInbox.objects.filter(refund=refund).exclude(pk=row.pk).order_by('pk')
                for previous in previous_rows:
                    try:
                        previous_notice = VerifiedNotification(previous.notification_id, previous.event_type,
                            previous.resource_type, previous.original_type, previous.controlled_payload, previous.signed_body_hash)
                        prior = parse_refund_notification(previous_notice)
                    except WeChatPayOutcomeUnknown:
                        conflict = True
                        break
                    if _conflicts(candidate, prior):
                        conflict = True
                        break
            if conflict:
                _stop(intent, refund)
                row.has_conflict = True
                row.status, row.last_error = DirectRefundInbox.Status.REVIEW, 'REFUND_EVIDENCE_CONFLICT'
            elif refund.status == DirectPaymentRefund.Status.REVIEW:
                row.status, row.last_error = DirectRefundInbox.Status.REVIEW, 'REFUND_ALREADY_REVIEW'
        else:
            row.status, row.last_error = DirectRefundInbox.Status.REVIEW, 'REFUND_IDENTITY_UNMATCHED'
        row.save(update_fields=['refund', 'has_conflict', 'status', 'last_error', 'updated_at'])
        return row
