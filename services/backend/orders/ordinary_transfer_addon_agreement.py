"""Capture add-on responsibility with a transferred-order agreement."""
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderAddonPayment, OrderOriginalShareTransfer, OrdinaryTransferAddonAgreement


def applies(order):
    return (OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            and OrderAddonPayment.objects.filter(intent__order=order).exists())


def snapshot_locked(order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购方案须在订单事务内核对')
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    from .addon_payment import verify_receipt
    rows = []
    payments = (OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                .select_related('intent').order_by('pk'))
    for payment in payments:
        verify_receipt(payment)
        assert_no_pending_handover(payment)
        handover, responsibilities = current_responsibilities(payment, lock=True)
        rows.append({'payment_id': payment.pk, 'intent_id': payment.intent_id,
                     'handover_id': handover.pk if handover else None,
                     'refund_ids': list(payment.refund_records.order_by('pk').values_list('pk', flat=True)),
                     'responsibilities': responsibilities})
    return rows


def _digest(proposal, snapshot):
    payload = {'proposal': proposal.pk, 'terms_hash': proposal.terms_hash, 'addons': snapshot}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def bind_locked(proposal):
    snapshot = snapshot_locked(proposal.case.order)
    if not snapshot:
        raise ValidationError('已付款加购凭证缺失')
    return OrdinaryTransferAddonAgreement.objects.create(
        proposal=proposal, snapshot=snapshot, snapshot_hash=_digest(proposal, snapshot))


def verify_locked(proposal, *, compare_current=False):
    item = OrdinaryTransferAddonAgreement.objects.select_for_update().filter(proposal=proposal).first()
    if not item or item.snapshot_hash != _digest(proposal, item.snapshot):
        raise ValidationError('加购责任方案快照缺失或异常')
    if compare_current and item.snapshot != snapshot_locked(proposal.case.order):
        raise ValidationError('加购责任或退款已变化，请重新确认方案')
    return item.snapshot


def display(proposal, *, account_id=None, staff=False):
    item = OrdinaryTransferAddonAgreement.objects.filter(proposal=proposal).first()
    if not item:
        return None
    if item.snapshot_hash != _digest(proposal, item.snapshot):
        raise ValidationError('加购责任方案快照异常')
    if staff:
        return item.snapshot
    result = []
    for row in item.snapshot:
        parts = [part for part in row['responsibilities'] if account_id is None or part['provider_id'] == account_id]
        if parts:
            result.append({'payment_id': row['payment_id'], 'intent_id': row['intent_id'],
                           'responsibilities': [{key: part[key] for key in
                               ('segment_id', 'provider_id', 'basis', 'is_current')} for part in parts]})
    return result
