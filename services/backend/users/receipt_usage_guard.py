"""Fail closed for writers not yet integrated with receipt usage accounting."""
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.conf import settings
from stores.context import current_store
from stores.db import transaction


def reject_unintegrated_locked_receipt(receipt):
    """Caller must already hold this receipt row, in its existing lock order."""
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('到账用途核对必须在门店事务内执行')
    if receipt.usage_version is not None:
        raise ValidationError('该到账已建用途账本，当前操作尚未接入，请先核对')


def isolated_allocation_state_locked(receipt):
    """Return verified state for the controlled allocation ledger."""
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('到账用途核对必须在门店事务内执行')
    if receipt.usage_version is None:
        return None
    if (getattr(settings, 'RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING', False) is not True
            and getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is not True):
        reject_unintegrated_locked_receipt(receipt)
    from .receipt_usage_sources import verify_allocations_locked
    return verify_allocations_locked(receipt.pk)


def _usage_scope():
    from orders.points_business_access import _scope as points_scope
    connection = transaction.get_connection()
    return connection, tuple(connection.atomic_blocks), current_store(), points_scope.get()


def _receipt_details(receipt):
    return receipt._state.db, receipt.pk, receipt.usage_version, receipt.amount, receipt.verified_at, receipt.verified_by


def _used_units(ledger):
    return (ledger.pass_units, ledger.order_units + ledger.referral_units
            - ledger.referral_released_units - ledger.released_units)


@dataclass(frozen=True)
class _VerifiedAllocationUsage:
    receipt: object
    details: tuple
    scope: tuple
    used_units: tuple[int, int]


def capture_allocation_usage_locked(receipt):
    """Verify the full ledger for a continuous, read-only block under the existing receipt lock."""
    scope = _usage_scope()
    if receipt._state.db != scope[0].alias or receipt.usage_version is None:
        raise ValidationError('到账用途须在本店到账记录上核对')
    ledger = isolated_allocation_state_locked(receipt)
    if ledger.receipt_id != receipt.pk or ledger.version != receipt.usage_version:
        raise ValidationError('到账用途版本已变化，请重新核对')
    return _VerifiedAllocationUsage(receipt, _receipt_details(receipt), scope, _used_units(ledger))


def used_allocation_units_locked(receipt, *, verified_usage=None):
    if verified_usage is None:
        ledger = isolated_allocation_state_locked(receipt)
        if ledger is None:
            raise ValidationError('到账用途账本未建立，请先核对')
        return _used_units(ledger)
    if (not isinstance(verified_usage, _VerifiedAllocationUsage) or verified_usage.receipt is not receipt
            or verified_usage.details != _receipt_details(receipt) or verified_usage.scope != _usage_scope()):
        raise ValidationError('到账用途核对结果已失效，请重新核对')
    return verified_usage.used_units
