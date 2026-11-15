"""Receipt-local ledger primitives for controlled business writers.

All callers must write their underlying allocation/release and this ledger in
one tenant transaction. Until every writer does so this module must not be
used to authorize spending or replace existing reconciliation.
"""
import hashlib
import json
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from stores.context import current_store
from stores.db import transaction
from .models import PassReceiptUsageEvent, PassReceiptUsageState, PassRefundRepaymentReceipt

ZERO_HASH = '0' * 64
KINDS = {'PASS', 'ORDER', 'RELEASE', 'REFERRAL', 'REFERRAL_RELEASE'}


def _locked_receipt(receipt_id):
    testing = getattr(settings, 'RECEIPT_USAGE_LEDGER_INTERNAL_TESTING', False) is True
    formal = getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
    if not (testing or formal):
        raise ValidationError('到账用途账本尚未开放')
    store = current_store()
    connection = transaction.get_connection()
    if (not connection.in_atomic_block or store is None or store.database_alias != connection.alias
            or type(receipt_id) is not int or receipt_id <= 0):
        raise ValidationError('到账账本必须在对应门店事务内操作')
    if testing:
        from orders.points_checkout_test import require_isolated_checkout
        from rest_framework.exceptions import ValidationError as RestValidationError
        try:
            require_isolated_checkout()
        except RestValidationError as exc:
            raise ValidationError('到账用途测试账本仅限隔离环境') from exc
    elif connection.vendor != 'mysql':
        raise ValidationError('正式到账用途账本仅支持 MySQL')
    receipt = PassRefundRepaymentReceipt.objects.select_for_update().filter(pk=receipt_id).first()
    if not receipt or not receipt.verified_at or not receipt.verified_by:
        raise ValidationError('到账不存在或尚未核实')
    if not testing:
        from .receipt_usage_sources import _require_current_unlocked_reads
        _require_current_unlocked_reads()
    return receipt


def _digest(receipt, version, key, kind, allocation_key, amount, source_digest, previous):
    data = [current_store().slug, receipt.pk, receipt.request_key, receipt.amount,
            version, key, kind, allocation_key, amount, source_digest, previous]
    return hashlib.sha256(json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _validate_locked(receipt, state):
    totals = {'PASS': 0, 'ORDER': 0, 'RELEASE': 0, 'REFERRAL': 0, 'REFERRAL_RELEASE': 0}
    allocated, released, pass_keys = {}, {}, set()
    referral_allocated, referral_released = {}, set()
    previous = ZERO_HASH
    def event_pages():
        last = 0
        while True:
            page = list(state.events.select_for_update().filter(version__gt=last).order_by('version')[:256])
            if not page:
                break
            yield from page
            last = page[-1].version
    count = 0
    for version, row in enumerate(event_pages(), 1):
        count = version
        expected = _digest(receipt, version, row.request_key, row.kind, row.allocation_key,
                           row.amount, row.source_digest, previous)
        if (row.version != version or row.kind not in KINDS or row.amount <= 0
                or not row.allocation_key or row.allocation_key != row.allocation_key.strip()
                or not re.fullmatch(r'[0-9a-f]{64}', row.source_digest)
                or row.previous_hash != previous or row.event_hash != expected):
            raise ValidationError('到账用途事件凭据不一致，请先核对')
        totals[row.kind] += row.amount
        if row.kind == 'ORDER':
            if row.allocation_key in allocated:
                raise ValidationError('到账订单分配凭据重复')
            allocated[row.allocation_key] = row.amount
        elif row.kind == 'REFERRAL':
            if row.allocation_key in referral_allocated:
                raise ValidationError('推荐佣金退款分配凭据重复')
            referral_allocated[row.allocation_key] = row.amount
        elif row.kind == 'REFERRAL_RELEASE':
            if (row.allocation_key in referral_released
                    or referral_allocated.get(row.allocation_key) != row.amount):
                raise ValidationError('推荐佣金退款释放与原分配不一致')
            referral_released.add(row.allocation_key)
        elif row.kind == 'PASS':
            if row.allocation_key in pass_keys:
                raise ValidationError('到账通行证分配凭据重复')
            pass_keys.add(row.allocation_key)
        else:
            released[row.allocation_key] = released.get(row.allocation_key, 0) + row.amount
            if released[row.allocation_key] > allocated.get(row.allocation_key, 0):
                raise ValidationError('释放金额超过对应订单的到账分配')
        if (totals['RELEASE'] > totals['ORDER'] or totals['PASS'] + totals['ORDER']
                + totals['REFERRAL'] - totals['REFERRAL_RELEASE'] - totals['RELEASE'] > receipt.amount):
            raise ValidationError('到账用途事件金额不守恒')
        previous = row.event_hash
    if (receipt.usage_version != state.version or
            (state.version, state.pass_units, state.order_units, state.referral_units, state.referral_released_units,
             state.released_units, state.chain_hash)
            != (count, totals['PASS'], totals['ORDER'], totals['REFERRAL'], totals['REFERRAL_RELEASE'],
                totals['RELEASE'], previous)):
        raise ValidationError('到账用途汇总与事件不一致，请先核对')
    return state


def initialize_empty_locked(receipt_id):
    """Initialize only a verified receipt with no pre-existing allocations."""
    receipt = _locked_receipt(receipt_id)
    state = PassReceiptUsageState.objects.select_for_update().filter(receipt=receipt).first()
    if state:
        return _validate_locked(receipt, state)
    if receipt.usage_version is not None:
        raise ValidationError('到账用途版本存在但账本缺失，请先核对')
    from .pass_repayment import points_funding_table_exists
    if not points_funding_table_exists():
        raise ValidationError('订单补资表尚未就绪，无法初始化到账用途')
    from orders.models import OrderPointsFundingAllocation, ReferralRefundFunding
    if (receipt.allocations.select_for_update().exists()
            or OrderPointsFundingAllocation.objects.select_for_update().filter(receipt=receipt).exists()
            or ReferralRefundFunding.objects.select_for_update().filter(receipt=receipt).exists()):
        raise ValidationError('到账已有分配记录，须先人工核对，不能按空账初始化')
    with transaction.atomic():
        state = PassReceiptUsageState.objects.create(receipt=receipt)
        receipt.usage_version = 0
        receipt.save(update_fields=['usage_version'])
    return state


def append_locked(receipt_id, *, request_key, kind, allocation_key, amount, source_digest):
    """Append one idempotent event under the receipt lock in the caller transaction."""
    receipt = _locked_receipt(receipt_id)
    if (not isinstance(request_key, str) or not request_key or request_key != request_key.strip()
            or len(request_key) > 160 or not isinstance(kind, str) or kind not in KINDS or not isinstance(allocation_key, str)
            or not allocation_key or allocation_key != allocation_key.strip() or len(allocation_key) > 160
            or type(amount) is not int or amount <= 0
            or amount > 2147483647 or not isinstance(source_digest, str)
            or not re.fullmatch(r'[0-9a-f]{64}', source_digest)):
        raise ValidationError('到账用途事件参数无效')
    state = PassReceiptUsageState.objects.select_for_update().filter(receipt=receipt).first()
    if not state:
        raise ValidationError('到账用途账本尚未初始化')
    _validate_locked(receipt, state)
    existing = state.events.select_for_update().filter(request_key=request_key).first()
    if existing:
        if (existing.kind, existing.allocation_key, existing.amount, existing.source_digest) != (
                kind, allocation_key, amount, source_digest):
            raise ValidationError('到账用途请求编号已用于其他操作')
        return existing
    allocation = list(state.events.select_for_update().filter(
        kind='ORDER', allocation_key=allocation_key).values_list('amount', flat=True))
    if ((kind == 'ORDER' and allocation)
            or (kind in ('PASS', 'REFERRAL') and state.events.select_for_update().filter(
                kind=kind, allocation_key=allocation_key).exists())):
        raise ValidationError('到账分配凭据已登记')
    if kind == 'RELEASE':
        released = sum(state.events.select_for_update().filter(
            kind='RELEASE', allocation_key=allocation_key).values_list('amount', flat=True))
        if len(allocation) != 1 or released + amount > allocation[0]:
            raise ValidationError('释放金额超过对应订单的到账分配')
    if kind == 'REFERRAL_RELEASE':
        original = list(state.events.select_for_update().filter(
            kind='REFERRAL', allocation_key=allocation_key).values_list('amount', flat=True))
        if (original != [amount] or state.events.select_for_update().filter(
                kind='REFERRAL_RELEASE', allocation_key=allocation_key).exists()):
            raise ValidationError('推荐佣金退款分配只能完整释放一次')
    values = {'PASS': state.pass_units, 'ORDER': state.order_units, 'REFERRAL': state.referral_units,
              'REFERRAL_RELEASE': state.referral_released_units, 'RELEASE': state.released_units}
    values[kind] += amount
    if (values['RELEASE'] > values['ORDER'] or values['PASS'] + values['ORDER']
            + values['REFERRAL'] - values['REFERRAL_RELEASE'] - values['RELEASE'] > receipt.amount):
        raise ValidationError('到账用途超出已分配或到账金额')
    version = state.version + 1
    digest = _digest(receipt, version, request_key, kind, allocation_key, amount, source_digest, state.chain_hash)
    # A caller might catch an error inside its wider transaction. Keep the
    # event and materialized counters indivisible even in that case.
    with transaction.atomic():
        event = PassReceiptUsageEvent.objects.create(state=state, version=version, request_key=request_key,
            kind=kind, allocation_key=allocation_key, amount=amount, source_digest=source_digest,
            previous_hash=state.chain_hash, event_hash=digest)
        (state.version, state.pass_units, state.order_units, state.referral_units,
         state.referral_released_units, state.released_units, state.chain_hash) = (
            version, values['PASS'], values['ORDER'], values['REFERRAL'], values['REFERRAL_RELEASE'], values['RELEASE'], digest)
        state.save(update_fields=['version', 'pass_units', 'order_units', 'referral_units', 'referral_released_units', 'released_units', 'chain_hash'])
        receipt.usage_version = version
        receipt.save(update_fields=['usage_version'])
    return event


def verify_locked(receipt_id):
    """Use current reads without locking or authorizing any order."""
    receipt = _locked_receipt(receipt_id)
    state = PassReceiptUsageState.objects.select_for_update().filter(receipt=receipt).first()
    if not state:
        raise ValidationError('到账用途账本尚未初始化')
    return _validate_locked(receipt, state)
