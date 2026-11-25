"""Cash provenance for closure returns; funding classifications are not receipts."""
from __future__ import annotations

import hashlib
import re

from engagement.models import PointFundingLot

from .models import RechargeGiftApproval, RechargeRecord, Transaction


RECHARGE_KEY = re.compile(r'recharge:([1-9][0-9]*)')


def _reason(code):
    return None, code


def _record_for_lot(account, lot):
    match = RECHARGE_KEY.fullmatch(lot.source_key or '')
    if match is None:
        return _reason('CASH_SOURCE_UNVERIFIED')
    record = RechargeRecord.objects.select_related(
        'recharge_tx__wallet', 'gift_tx__wallet', 'gift_approval',
    ).filter(pk=int(match.group(1)), account=account).first()
    if record is None:
        return _reason('CASH_SOURCE_UNVERIFIED')
    if record.account_business_cycle != account.business_cycle:
        return _reason('CASH_CYCLE_MISMATCH')
    trade_no = (record.trade_no or '').strip()
    expected_receipt = hashlib.sha256(trade_no.casefold().encode('utf-8')).hexdigest() if trade_no else ''
    if not trade_no or not record.proof_image:
        return _reason('CASH_RECEIPT_MISSING')
    if not record.receipt_key or record.receipt_key != expected_receipt:
        return _reason('CASH_RECEIPT_MISMATCH')
    tx = record.recharge_tx
    if (tx is None or tx.wallet.account_id != account.pk or tx.tx_type != Transaction.TxType.TOPUP
            or tx.status != Transaction.Status.SUCCESS or record.amount <= 0 or tx.amount != record.amount
            or tx.order_id is not None or tx.reversal_of_id is not None or tx.reversals.exists()
            or tx.balance_after != tx.balance_before + tx.amount or tx.created_at < record.created_at
            or RechargeRecord.objects.filter(recharge_tx_id=tx.pk).count() != 1):
        return _reason('CASH_TRANSACTION_INVALID')
    snapshot = record.bonus_snapshot
    if (not isinstance(snapshot, dict) or snapshot.get('amount') != record.amount
            or snapshot.get('gift_amount') != (record.gift_approval.standard_amount
                                               if hasattr(record, 'gift_approval') else record.gift_amount)):
        return _reason('CASH_TERMS_MISMATCH')
    try:
        approval = record.gift_approval
    except RechargeGiftApproval.DoesNotExist:
        approval = None
    if approval is not None:
        from .recharge_approval import RechargeApprovalError, _verify_completed, _verify_original
        if lot.kind != PointFundingLot.Kind.PREPAID or approval.status not in (
                RechargeGiftApproval.Status.PENDING, RechargeGiftApproval.Status.APPROVED,
                RechargeGiftApproval.Status.REJECTED):
            return _reason('CASH_GIFT_EVIDENCE_INVALID')
        try:
            _verify_original(record, approval, tx.wallet)
            if approval.status == RechargeGiftApproval.Status.PENDING:
                if record.gift_amount or record.gift_tx_id:
                    return _reason('CASH_GIFT_EVIDENCE_INVALID')
            else:
                _verify_completed(record, approval, tx.wallet)
        except RechargeApprovalError:
            return _reason('CASH_GIFT_EVIDENCE_INVALID')
        if lot.remaining > record.amount:
            return _reason('CASH_LOT_AMOUNT_INVALID')
    else:
        if lot.kind != PointFundingLot.Kind.RECHARGE:
            return _reason('CASH_SOURCE_UNVERIFIED')
        gift = record.gift_tx
        if record.gift_amount:
            if (gift is None or gift.wallet_id != tx.wallet_id or gift.tx_type != Transaction.TxType.GIFT
                    or gift.status != Transaction.Status.SUCCESS or gift.amount != record.gift_amount
                    or gift.order_id is not None or gift.reversal_of_id is not None or gift.reversals.exists()
                    or gift.balance_after != gift.balance_before + gift.amount):
                return _reason('CASH_GIFT_EVIDENCE_INVALID')
        elif gift is not None:
            return _reason('CASH_GIFT_EVIDENCE_INVALID')
        if lot.remaining > record.amount + record.gift_amount:
            return _reason('CASH_LOT_AMOUNT_INVALID')
    return (record, approval), None


def verified_source_balances(account, lots):
    """Return known current source amounts and independent blocking reasons.

    Any unproved positive source makes all cash-source totals unknown. Dedicated
    gift/compensation lots remain non-cash facts, never inferred cash principal.
    """
    values = {'PREPAID': 0, 'GIFT': 0, 'COMPENSATION': 0}
    reasons = []
    unknown = False
    for lot in lots:
        if lot.kind == PointFundingLot.Kind.GIFT:
            values['GIFT'] += lot.remaining
            continue
        if lot.kind == PointFundingLot.Kind.COMPENSATION:
            values['COMPENSATION'] += lot.remaining
            continue
        if lot.kind not in (PointFundingLot.Kind.PREPAID, PointFundingLot.Kind.RECHARGE):
            unknown = True
            reasons.append('UNRECOGNIZED_FUNDING_SOURCE')
            continue
        evidence, error = _record_for_lot(account, lot)
        if error:
            unknown = True
            reasons.append(error)
            continue
        _record, approval = evidence
        if approval is not None and approval.status == RechargeGiftApproval.Status.PENDING:
            values['PREPAID'] += lot.remaining
            reasons.append('PENDING_GIFT_APPROVAL')
            continue
        if lot.kind == PointFundingLot.Kind.RECHARGE:
            if type(lot.gift_remaining) is not int or not 0 <= lot.gift_remaining <= lot.remaining:
                unknown = True
                reasons.append('UNKNOWN_RECHARGE_SPLIT')
                continue
            values['GIFT'] += lot.gift_remaining
            values['PREPAID'] += lot.remaining - lot.gift_remaining
        else:
            if lot.gift_remaining not in (None, 0):
                unknown = True
                reasons.append('CASH_LOT_KIND_MISMATCH')
                continue
            values['PREPAID'] += lot.remaining
    if unknown:
        return {'PREPAID': None, 'GIFT': None, 'COMPENSATION': None}, False, list(dict.fromkeys(reasons))
    return values, True, list(dict.fromkeys(reasons))
