"""Recover an actual admin recharge without posting its funds twice."""
import hashlib
import json

from stores.db import transaction
from .models import IdempotencyKey, RechargeRecord, Transaction
from .services import DERIVED_KEY_PREFIX, normalize_request_id


class RechargeReplayError(ValueError):
    pass


def fingerprint(data, proof):
    fields = ('account_id', 'user_id', 'amount', 'gift_amount', 'trade_no', 'remark', 'bonus_version')
    payload = {field: '' if data.get(field) is None else str(data.get(field)).strip() for field in fields}
    payload['trade_no'] = payload['trade_no'].casefold()
    if proof:
        digest = hashlib.sha256()
        try:
            for chunk in proof.chunks():
                digest.update(chunk)
        finally:
            proof.seek(0)
        payload['proof_sha256'] = digest.hexdigest()
    else:
        payload['proof_sha256'] = ''
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def find(actor, key, signature, *, lock=False):
    normalized = normalize_request_id(key)
    if not normalized or normalized.startswith(DERIVED_KEY_PREFIX):
        return None
    query = IdempotencyKey.objects.filter(account=actor, request_id=normalized)
    if lock:
        query = query.select_for_update()
    receipt = query.first()
    if not receipt:
        return None
    if receipt.scope != 'admin.recharge' or receipt.request_fingerprint != signature:
        raise RechargeReplayError('请求编号已用于其他操作或充值内容不同')
    saved = receipt.response
    if not isinstance(saved, dict) or saved.get('kind') != 'ADMIN_RECHARGE_V1' or type(saved.get('record_id')) is not int:
        raise RechargeReplayError('原充值结果缺少可核对的回执')
    record = RechargeRecord.objects.select_related('recharge_tx__wallet', 'gift_tx', 'gift_approval').filter(
        pk=saved['record_id'], operator_account=actor).first()
    tx = record.recharge_tx if record else None
    if (not record or not tx or tx.wallet.account_id != record.account_id
            or (record.user_id is not None and tx.wallet.user_id != record.user_id) or tx.tx_type != Transaction.TxType.TOPUP
            or tx.operator_account_id != actor.pk or tx.amount != record.amount
            or tx.status != Transaction.Status.SUCCESS or tx.reversal_of_id
            or tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount):
        raise RechargeReplayError('原充值本金流水待核对')
    from .models import RechargeGiftApproval
    approval = RechargeGiftApproval.objects.filter(recharge=record).first()
    if approval:
        from .recharge_approval import RechargeApprovalError, _verify_original, _verify_completed
        try:
            _verify_original(record, approval, tx.wallet)
            if approval.status == RechargeGiftApproval.Status.PENDING:
                if record.gift_amount or record.gift_tx_id:
                    raise RechargeApprovalError('待审批赠送不应提前到账')
            else:
                _verify_completed(record, approval, tx.wallet)
        except RechargeApprovalError as exc:
            raise RechargeReplayError(str(exc)) from exc
    else:
        gift = record.gift_tx
        snapshot = record.bonus_snapshot
        if (not isinstance(snapshot, dict) or snapshot.get('gift_amount') != record.gift_amount
                or (not record.gift_amount and gift)
                or (record.gift_amount and (not gift or gift.wallet_id != tx.wallet_id
                    or gift.tx_type != Transaction.TxType.GIFT or gift.status != Transaction.Status.SUCCESS
                    or gift.amount != record.gift_amount or gift.operator_account_id != actor.pk
                    or gift.reversal_of_id or gift.reversals.exists()
                    or gift.balance_after != gift.balance_before + gift.amount))):
            raise RechargeReplayError('原充值赠送流水待核对')
    return record


def save(actor, key, signature, record):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('充值回执必须在原资金事务内保存')
    changed = IdempotencyKey.objects.filter(account=actor, request_id=normalize_request_id(key),
        scope='admin.recharge', response__isnull=True).update(
            request_fingerprint=signature, response={'kind': 'ADMIN_RECHARGE_V1', 'record_id': record.pk})
    if changed != 1:
        raise RechargeReplayError('充值请求与原资金回执不一致')
