"""Store guarantee terms and append-only offline CNY receipt/refund evidence."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone as datetime_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    PlatformAuditEvent, Store, StoreGuaranteeContract, StoreGuaranteeEvent,
    StoreLicenseContract,
)
from .platform_permissions import platform_may

MAX_FEN = 2 ** 63 - 1
_REQUEST_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,79}')
_EVIDENCE_REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_BANK_ACCOUNT = re.compile(r'(?<![A-Za-z0-9])(?:\d[ -]?){12,19}(?![A-Za-z0-9])')

PUBLIC_LABELS = {
    'PENDING_REVIEW': '保障信息待核',
    'NOT_COVERED': '未形成保证金保障',
    'PARTIAL': '保证金部分登记',
    'COVERED': '保证金已按合同登记足额',
}


class GuaranteeError(ValueError):
    def __init__(self, message, status=400, code='GUARANTEE_VALIDATION_ERROR'):
        super().__init__(message)
        self.status = status
        self.code = code


def _identifier(value, label, maximum):
    if not isinstance(value, str):
        raise GuaranteeError(f'{label}无效')
    value = value.strip()
    if len(value) > maximum or not _REQUEST_KEY.fullmatch(value):
        raise GuaranteeError(f'{label}无效')
    return value


def _amount(value, *, allow_zero=False):
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= MAX_FEN:
        raise GuaranteeError('金额必须为人民币分的有效整数')
    return value


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise GuaranteeError('请填写 200 字以内的变更原因')
    return value.strip()


def _contains_bank_account(value):
    return bool(_BANK_ACCOUNT.search(value))


def _valid_evidence_ref(value):
    return bool(
        isinstance(value, str)
        and _EVIDENCE_REF.fullmatch(value)
        and not _contains_bank_account(value)
    )


def _evidence(values):
    if not isinstance(values, tuple) or len(values) != 2:
        return None
    digest, reference = values
    if not isinstance(digest, str) or not isinstance(reference, str):
        raise GuaranteeError('线下凭证摘要和安全引用不能为空')
    digest = digest.strip().lower()
    reference = reference.strip()
    if not _SHA256.fullmatch(digest) or not _valid_evidence_ref(reference):
        raise GuaranteeError('请提供有效的线下凭证 SHA256 和不包含银行账号的安全引用')
    return digest, reference


def _locked_store(store_id):
    store = Store.objects.select_for_update().filter(pk=store_id).first()
    if store is None:
        raise GuaranteeError('门店不存在', 404, 'STORE_NOT_FOUND')
    return store


def _authorize(actor, action, store):
    if not platform_may(actor, action, store_slug=store.slug):
        raise GuaranteeError('缺少该门店的保证金平台权限', 403, 'PLATFORM_FORBIDDEN')


def _totals(contract, *, lock=False):
    rows = StoreGuaranteeEvent.objects.filter(contract=contract)
    if lock:
        # See any rows committed while waiting for the contract lock, including
        # under MySQL REPEATABLE READ. All writers take the same store/contract locks.
        values = rows.select_for_update().order_by('id').values_list('direction', 'amount_fen')
    else:
        values = rows.values_list('direction', 'amount_fen')
    received = refunded = 0
    for direction, amount in values:
        if direction == StoreGuaranteeEvent.Direction.RECEIPT:
            received += amount
        elif direction == StoreGuaranteeEvent.Direction.REFUND:
            refunded += amount
        else:
            raise GuaranteeError('保证金历史方向无效', 409, 'GUARANTEE_HISTORY_INVALID')
    return received, refunded, received - refunded


def _actor_data(actor):
    return {'id': str(actor.pk), 'username': actor.username, 'nickname': actor.nickname}


def contract_summary(contract, *, lock=False):
    received, refunded, net = _totals(contract, lock=lock)
    if contract.amount_due_fen == 0 and net == 0:
        status = 'NO_REQUIREMENT'
    elif net == 0:
        status = 'UNPAID'
    elif net == contract.amount_due_fen:
        status = 'COVERED'
    else:
        status = 'PARTIAL'
    return {
        'id': str(contract.pk),
        'store_slug': contract.store.slug,
        'source_license_contract_id': str(contract.source_license_contract_id) if contract.source_license_contract_id else None,
        'amount_due_fen': contract.amount_due_fen,
        'version': contract.version,
        'amount_received_fen': received,
        'amount_refunded_fen': refunded,
        'net_received_fen': net,
        'amount_remaining_fen': contract.amount_due_fen - net,
        'status': status,
        'updated_at': contract.updated_at.isoformat(),
        'updated_by': _actor_data(contract.updated_by),
    }


def event_summary(event):
    return {
        'id': str(event.pk),
        'business_key': event.business_key,
        'client_request_id': event.client_request_id,
        'contract_version': event.contract_version,
        'direction': event.direction,
        'amount_fen': event.amount_fen,
        'occurred_at': event.occurred_at.isoformat(),
        'evidence_sha256': event.evidence_sha256,
        'evidence_ref': event.evidence_ref,
        'compensates_event_id': str(event.compensates_id) if event.compensates_id else None,
        'created_by': _actor_data(event.created_by),
        'created_at': event.created_at.isoformat(),
        'channel_status': 'NOT_REQUESTED',
    }


def read_platform_summary(store):
    contract = StoreGuaranteeContract.objects.filter(store=store).select_related(
        'store', 'updated_by',
    ).first()
    if contract is None:
        return {'contract': None, 'events': [], 'public_status': public_status(store)}
    events = StoreGuaranteeEvent.objects.filter(contract=contract).select_related(
        'created_by',
    ).order_by('created_at', 'id')
    return {
        'contract': contract_summary(contract),
        'events': [event_summary(row) for row in events],
        'public_status': public_status(store),
    }


def configure_contract(actor, *, store_id, amount_due_fen, expected_version, reason,
                       source_license_contract_id=None):
    amount_due_fen = _amount(amount_due_fen, allow_zero=True)
    if type(expected_version) is not int or expected_version < 0:
        raise GuaranteeError('expected_version 必须是非负整数')
    reason = _reason(reason)
    if source_license_contract_id is not None and not isinstance(source_license_contract_id, uuid.UUID):
        raise GuaranteeError('来源 License 合同无效')
    with transaction.atomic(using='default'):
        store = _locked_store(store_id)
        _authorize(actor, 'guarantee:configure', store)
        contract = StoreGuaranteeContract.objects.select_for_update().filter(store=store).select_related(
            'store', 'updated_by',
        ).first()
        current_version = contract.version if contract else 0
        if current_version != expected_version:
            raise GuaranteeError('保证金约定已变化，请刷新后重试', 409, 'GUARANTEE_REVISION_CONFLICT')
        if source_license_contract_id is None:
            source = None
        else:
            source = StoreLicenseContract.objects.filter(pk=source_license_contract_id).first()
            if source is None or source.store_id != store.pk:
                raise GuaranteeError('来源 License 合同不属于目标门店')
        if contract is not None and amount_due_fen < _totals(contract, lock=True)[2]:
            raise GuaranteeError('保证金应收不能低于已收净额', 409, 'GUARANTEE_LIMIT_EXCEEDED')
        before = contract.amount_due_fen if contract else None
        if contract is None:
            contract = StoreGuaranteeContract.objects.create(
                store=store, source_license_contract=source, amount_due_fen=amount_due_fen,
                version=1, created_by=actor, updated_by=actor,
            )
        else:
            contract.source_license_contract = source
            contract.amount_due_fen = amount_due_fen
            contract.version += 1
            contract.updated_by = actor
            contract.save(update_fields=[
                'source_license_contract', 'amount_due_fen', 'version', 'updated_by', 'updated_at',
            ])
        PlatformAuditEvent.objects.create(
            account=actor, action='guarantee.contract_configured', target_store_slug=store.slug,
            target_type='guarantee_contract', target_id=str(contract.pk),
            detail={
                'before_fen': before, 'after_fen': amount_due_fen, 'version': contract.version,
                'source_license_contract_id': str(source.pk) if source else None,
                'reason': reason,
            },
        )
        return contract_summary(contract, lock=True)


def _fingerprint(store_id, values):
    raw = json.dumps(
        {'store_id': store_id, **values}, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def record_event(actor, *, store_id, business_key, client_request_id, direction, amount_fen,
                 occurred_at, evidence_sha256, evidence_ref, compensates_event_id=None):
    business_key = _identifier(business_key, '本店业务键', 80)
    client_request_id = _identifier(client_request_id, 'client_request_id', 64)
    amount_fen = _amount(amount_fen)
    if direction not in StoreGuaranteeEvent.Direction.values:
        raise GuaranteeError('收退方向无效')
    if not isinstance(occurred_at, datetime) or timezone.is_naive(occurred_at):
        raise GuaranteeError('线下收退发生时间必须包含时区')
    occurred_at = occurred_at.astimezone(datetime_timezone.utc)
    evidence_sha256, evidence_ref = _evidence((evidence_sha256, evidence_ref))
    if compensates_event_id is not None and not isinstance(compensates_event_id, uuid.UUID):
        raise GuaranteeError('补偿原事件无效')
    values = {
        'business_key': business_key,
        'client_request_id': client_request_id,
        'direction': direction,
        'amount_fen': amount_fen,
        'occurred_at': occurred_at.isoformat(),
        'evidence_sha256': evidence_sha256,
        'evidence_ref': evidence_ref,
        'compensates_event_id': str(compensates_event_id) if compensates_event_id else None,
    }
    fingerprint = _fingerprint(store_id, values)
    try:
        with transaction.atomic(using='default'):
            store = _locked_store(store_id)
            _authorize(actor, 'guarantee:record', store)
            contract = StoreGuaranteeContract.objects.select_for_update().filter(store=store).select_related(
                'store', 'updated_by',
            ).first()
            if contract is None:
                raise GuaranteeError('请先配置保证金合同应收', 409, 'GUARANTEE_CONTRACT_REQUIRED')
            previous = StoreGuaranteeEvent.objects.select_for_update().filter(
                store=store, client_request_id=client_request_id,
            ).first()
            if previous is not None:
                if not hmac.compare_digest(previous.request_fingerprint, fingerprint):
                    raise GuaranteeError('请求编号已用于其他内容', 409, 'REQUEST_ID_CONFLICT')
                return previous, False
            if StoreGuaranteeEvent.objects.filter(store=store, business_key=business_key).exists():
                raise GuaranteeError('本店业务键已登记', 409, 'GUARANTEE_BUSINESS_KEY_CONFLICT')
            compensates = None
            if compensates_event_id is not None:
                compensates = StoreGuaranteeEvent.objects.select_for_update().filter(
                    pk=compensates_event_id, store=store,
                ).first()
                if compensates is None:
                    raise GuaranteeError('补偿原事件不存在或不属于本店', 404, 'GUARANTEE_EVENT_NOT_FOUND')
                if compensates.direction == direction or compensates.amount_fen != amount_fen:
                    raise GuaranteeError('补偿事件须与原事件方向相反且金额相同')
                if StoreGuaranteeEvent.objects.filter(compensates=compensates).exists():
                    raise GuaranteeError('原事件已有补偿记录', 409, 'GUARANTEE_ALREADY_COMPENSATED')
            _received, _refunded, net = _totals(contract, lock=True)
            if net < 0 or net > contract.amount_due_fen:
                raise GuaranteeError('保证金历史净额异常，请先核对原始凭证', 409, 'GUARANTEE_HISTORY_INVALID')
            if direction == StoreGuaranteeEvent.Direction.RECEIPT and net + amount_fen > contract.amount_due_fen:
                raise GuaranteeError('已收净额不能超过合同应收', 409, 'GUARANTEE_LIMIT_EXCEEDED')
            if direction == StoreGuaranteeEvent.Direction.REFUND and amount_fen > net:
                raise GuaranteeError('退还金额不能超过已收净额', 409, 'GUARANTEE_LIMIT_EXCEEDED')
            event = StoreGuaranteeEvent.objects.create(
                store=store, contract=contract, contract_version=contract.version,
                business_key=business_key, client_request_id=client_request_id,
                request_fingerprint=fingerprint, direction=direction, amount_fen=amount_fen,
                occurred_at=occurred_at, evidence_sha256=evidence_sha256,
                evidence_ref=evidence_ref, compensates=compensates, created_by=actor,
            )
            PlatformAuditEvent.objects.create(
                account=actor, action='guarantee.offline_event_recorded', target_store_slug=store.slug,
                target_type='guarantee_event', target_id=str(event.pk),
                detail={
                    'contract_version': contract.version, 'business_key': business_key,
                    'client_request_id': client_request_id, 'direction': direction,
                    'amount_fen': amount_fen, 'occurred_at': occurred_at.isoformat(),
                    'evidence_sha256': evidence_sha256,
                    'compensates_event_id': str(compensates.pk) if compensates else None,
                },
            )
            return event, True
    except IntegrityError as exc:
        raise GuaranteeError('业务键或请求编号已登记，请核对原记录', 409, 'GUARANTEE_EVENT_CONFLICT') from exc


def _pending_public():
    return {
        'status': 'PENDING_REVIEW',
        'label': PUBLIC_LABELS['PENDING_REVIEW'],
        'currency': 'CNY',
        'amount_due_fen': None,
        'net_received_fen': None,
        'amount_remaining_fen': None,
        'record_basis': 'OFFLINE_EVIDENCE',
    }


def public_status(store):
    """Return only a fail-closed public projection; never internal evidence fields."""
    contract = StoreGuaranteeContract.objects.filter(store=store).first()
    if contract is None:
        return _pending_public()
    events = list(StoreGuaranteeEvent.objects.filter(store=store).only(
        'direction', 'amount_fen', 'evidence_sha256', 'evidence_ref',
    ))
    if any(not _SHA256.fullmatch(row.evidence_sha256 or '')
           or not _valid_evidence_ref(row.evidence_ref or '') for row in events):
        return _pending_public()
    received = sum(row.amount_fen for row in events if row.direction == StoreGuaranteeEvent.Direction.RECEIPT)
    refunded = sum(row.amount_fen for row in events if row.direction == StoreGuaranteeEvent.Direction.REFUND)
    known_directions = set(StoreGuaranteeEvent.Direction.values)
    if any(row.direction not in known_directions for row in events):
        return _pending_public()
    net = received - refunded
    due = contract.amount_due_fen
    if net < 0 or net > due:
        return _pending_public()
    if net == 0:
        status = 'NOT_COVERED'
    elif net < due:
        status = 'PARTIAL'
    else:
        status = 'COVERED'
    label = '无保证金缴纳要求' if due == 0 and net == 0 else PUBLIC_LABELS[status]
    return {
        'status': status,
        'label': label,
        'currency': 'CNY',
        'amount_due_fen': due,
        'net_received_fen': net,
        'amount_remaining_fen': due - net,
        'record_basis': 'OFFLINE_EVIDENCE',
    }
