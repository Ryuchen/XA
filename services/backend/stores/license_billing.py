"""Offline RMB contract and receipt records, independent of license activation."""
import uuid
from datetime import date

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import PlatformAuditEvent, StoreLicenseContract, StoreLicenseReceipt
from .platform_accounts import _locked_actor


class LicenseBillingError(ValueError):
    pass


def _total(contract, *, lock=False):
    rows = StoreLicenseReceipt.objects.filter(contract=contract, voided_at__isnull=True)
    if lock:
        # A locking read sees receipts committed while this transaction was
        # waiting for the contract lock, including under MySQL REPEATABLE READ.
        return sum(rows.select_for_update().order_by('pk').values_list('amount_fen', flat=True))
    return rows.aggregate(total=Sum('amount_fen'))['total'] or 0


def contract_summary(contract, *, lock=False):
    received = _total(contract, lock=lock)
    status = 'PAID' if received == contract.amount_due_fen else ('UNPAID' if received == 0 else 'PARTIAL')
    return {'id': str(contract.pk), 'store_slug': contract.store.slug, 'amount_due_fen': contract.amount_due_fen,
            'amount_received_fen': received, 'amount_remaining_fen': contract.amount_due_fen - received, 'status': status}


def receipt_summary(receipt):
    return {'id': str(receipt.pk), 'request_id': str(receipt.request_id), 'amount_fen': receipt.amount_fen,
            'received_on': receipt.received_on.isoformat(), 'note': receipt.note,
            'voided_at': receipt.voided_at.isoformat() if receipt.voided_at else None, 'void_reason': receipt.void_reason}


def update_due(token, *, contract_id, amount_fen, reason):
    if (type(amount_fen) is not int or not 0 <= amount_fen <= 2 ** 63 - 1
            or not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200):
        raise LicenseBillingError('请填写有效的合同金额和变更原因')
    with transaction.atomic(using='default'):
        actor = _locked_actor(token)
        contract = StoreLicenseContract.objects.select_for_update().filter(pk=contract_id).first()
        if not contract:
            raise LicenseBillingError('合同不存在')
        if amount_fen < _total(contract, lock=True):
            raise LicenseBillingError('合同应收不能低于累计实收')
        before = contract.amount_due_fen
        contract.amount_due_fen, contract.updated_by = amount_fen, actor
        contract.save(update_fields=['amount_due_fen', 'updated_by', 'updated_at'])
        PlatformAuditEvent.objects.create(account=actor, action='license.contract_amount_changed',
            target_store_slug=contract.store.slug, target_type='license_contract', target_id=str(contract.pk),
            detail={'before_fen': before, 'after_fen': amount_fen, 'reason': reason.strip()})
        return contract_summary(contract, lock=True)


def record_receipt(token, *, contract_id, request_id, amount_fen, received_on, note):
    if (not isinstance(request_id, uuid.UUID) or type(amount_fen) is not int or not 0 < amount_fen <= 2 ** 63 - 1
            or type(received_on) is not date or not isinstance(note, str) or len(note.strip()) > 200):
        raise LicenseBillingError('收款信息无效')
    note = note.strip()
    with transaction.atomic(using='default'):
        actor = _locked_actor(token)
        contract = StoreLicenseContract.objects.select_for_update().filter(pk=contract_id).first()
        if not contract:
            raise LicenseBillingError('合同不存在')
        previous = StoreLicenseReceipt.objects.select_for_update().filter(request_id=request_id).first()
        if previous:
            if (previous.contract_id != contract.pk or previous.amount_fen != amount_fen
                    or previous.received_on != received_on or previous.note != note):
                raise LicenseBillingError('收款请求编号已被其他记录使用')
            return previous, False
        if _total(contract, lock=True) + amount_fen > contract.amount_due_fen:
            raise LicenseBillingError('累计实收不能超过合同应收，请核对合同金额')
        receipt = StoreLicenseReceipt.objects.create(request_id=request_id, contract=contract, amount_fen=amount_fen,
            received_on=received_on, note=note, created_by=actor)
        PlatformAuditEvent.objects.create(account=actor, action='license.receipt_recorded',
            target_store_slug=contract.store.slug, target_type='license_receipt', target_id=str(receipt.pk),
            detail={'contract_id': str(contract.pk), 'amount_fen': amount_fen, 'received_on': received_on.isoformat(), 'note': note})
        return receipt, True


def void_receipt(token, *, receipt_id, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
        raise LicenseBillingError('请填写作废原因')
    with transaction.atomic(using='default'):
        actor = _locked_actor(token)
        contract_id = StoreLicenseReceipt.objects.filter(pk=receipt_id).values_list('contract_id', flat=True).first()
        if not contract_id:
            raise LicenseBillingError('收款记录不存在')
        StoreLicenseContract.objects.select_for_update().get(pk=contract_id)
        receipt = StoreLicenseReceipt.objects.select_for_update().filter(pk=receipt_id, contract_id=contract_id).first()
        if not receipt:
            raise LicenseBillingError('收款记录不存在')
        if receipt.voided_at:
            return receipt
        receipt.voided_at, receipt.voided_by, receipt.void_reason = timezone.now(), actor, reason.strip()
        receipt.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
        PlatformAuditEvent.objects.create(account=actor, action='license.receipt_voided',
            target_store_slug=receipt.contract.store.slug, target_type='license_receipt', target_id=str(receipt.pk),
            detail={'contract_id': str(receipt.contract_id), 'reason': reason.strip()})
        return receipt
