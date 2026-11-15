"""Fail closed for writers not yet integrated with receipt usage accounting."""
from django.core.exceptions import ValidationError
from django.conf import settings
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
