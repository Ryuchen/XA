"""Reconcile a refund notice through a signed query in the isolated test domain.

This is not a public endpoint or a production worker. The injected transport
must be a test transport: a test database must never call the merchant API.
"""
import hashlib
import logging

from django.db import transaction

from integrations.wechatpay_gateway import OfficialHTTPTransport, WeChatPayGateway, WeChatPayOutcomeUnknown, WeChatPayResponseError
from integrations.wechatpay_refund_evidence import parse_refund_notification, query_refund_evidence
from integrations.wechatpay_v3 import VerifiedNotification
from .direct_payment import _require_test_domain
from .direct_refund import DirectRefundError, apply_verified_refund
from .direct_refund_delivery_models import DirectRefundInbox
from .direct_refund_inbox import _canonical
from .notifier import notify_order_update

logger = logging.getLogger(__name__)


def reconcile_test_refund_notice(inbox_id, *, gateway):
    """Query outside locks, then apply matching authenticated facts atomically.

    Unknown network results remain pending for a later query under the same
    merchant refund number. This function never submits a refund request.
    """
    _require_test_domain()
    if not isinstance(gateway, WeChatPayGateway) or isinstance(gateway._transport, OfficialHTTPTransport):
        raise ValueError('隔离验收仅接受测试渠道传输')
    if transaction.get_connection().in_atomic_block:
        raise ValueError('渠道查询不能在事务内执行')
    row = DirectRefundInbox.objects.select_related('refund__intent').get(pk=inbox_id)
    if row.status != DirectRefundInbox.Status.PENDING:
        return row
    if row.has_conflict or not row.refund_id:
        return row
    snapshot = (row.controlled_hash, row.refund_id, row.merchant_id)
    try:
        if hashlib.sha256(_canonical(row.controlled_payload)).hexdigest() != row.controlled_hash:
            raise WeChatPayOutcomeUnknown('saved notification changed')
        notice = VerifiedNotification(row.notification_id, row.event_type, row.resource_type,
                                      row.original_type, row.controlled_payload, row.signed_body_hash)
        candidate = parse_refund_notification(notice)
        intent = row.refund.intent
        bundle = query_refund_evidence(gateway, intent_mchid=intent.mchid, intent_appid=intent.appid,
                                       out_refund_no=row.refund.merchant_refund_no, notification=candidate)
    except (WeChatPayOutcomeUnknown, WeChatPayResponseError):
        # A transport timeout or unverified/mismatched query cannot establish
        # the result. Keep the same refund identity and allow later re-query.
        DirectRefundInbox.objects.filter(pk=inbox_id, status='PENDING', controlled_hash=snapshot[0],
                                         refund_id=snapshot[1], has_conflict=False).update(last_error='QUERY_UNCONFIRMED')
        return DirectRefundInbox.objects.get(pk=inbox_id)
    with transaction.atomic():
        row = DirectRefundInbox.objects.select_for_update().select_related('refund__intent').get(pk=inbox_id)
        if (row.status != DirectRefundInbox.Status.PENDING or row.has_conflict
                or (row.controlled_hash, row.refund_id, row.merchant_id) != snapshot):
            return row
        try:
            previous_status = row.refund.status
            refund = apply_verified_refund(row.refund.intent.order_id, row.refund_id, bundle.verified_refund)
        except DirectRefundError:
            row.status, row.last_error = DirectRefundInbox.Status.REVIEW, 'QUERY_IDENTITY_MISMATCH'
        else:
            if refund.status == 'REVIEW':
                row.status, row.last_error = DirectRefundInbox.Status.REVIEW, 'REFUND_REQUIRES_REVIEW'
            else:
                row.status, row.last_error = DirectRefundInbox.Status.DONE, ''
                if refund.status != previous_status:
                    order = refund.intent.order
                    transaction.on_commit(lambda order=order: notify_order_update(order))
        row.save(update_fields=['status', 'last_error', 'updated_at'])
        return row


def scan_test_refund_inbox(*, gateway, limit=100):
    """Process the saved notices for one configured test merchant."""
    _require_test_domain()
    if not isinstance(gateway, WeChatPayGateway) or isinstance(gateway._transport, OfficialHTTPTransport):
        raise ValueError('隔离验收仅接受测试渠道传输')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('处理数量须为 1 至 1000')
    ids = list(DirectRefundInbox.objects.filter(status='PENDING', merchant_id=gateway.merchant_id)
               .order_by('id').values_list('id', flat=True)[:limit])
    results = []
    for inbox_id in ids:
        try:
            results.append((inbox_id, reconcile_test_refund_notice(inbox_id, gateway=gateway).status))
        except Exception:
            # Leave the row pending and continue with unrelated notifications.
            logger.warning('隔离退款通知处理失败: inbox_id=%s', inbox_id)
            results.append((inbox_id, 'retry'))
    return results
