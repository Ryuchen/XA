"""Receipt-local accounting fact for an accepted pre-service reassignment."""
from django.core.exceptions import ValidationError


def facts(row):
    from rest_framework.exceptions import ValidationError as RestValidationError
    from users.receipt_usage_sources import _digest, _require_current_unlocked_reads
    from .addon_funding_release import verify_run

    _require_current_unlocked_reads()
    run, allocation = row.run, row.allocation
    if (not run.prestart_handoff_id or run.refund_id or run.handover_id or run.payment_id
            or run.negotiation_id or run.ordinary_transfer_revision_id
            or not allocation.redemption_id or allocation.service_order_id is not None
            or allocation.redemption.order_id != run.prestart_handoff.order_id
            or type(row.amount) is not int or row.amount <= 0):
        raise ValidationError('积分改派到账释放来源异常')
    try:
        verify_run(run, _skip_usage=True)
    except RestValidationError as exc:
        raise ValidationError('积分改派到账释放依据异常') from exc
    return ('RELEASE', f'order:{allocation.pk}', f'points-prestart-release:{row.pk}', row.amount,
            _digest(['points-prestart-release', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                     run.pk, run.prestart_handoff_id, run.prestart_hash, run.required_amount,
                     run.allocated_before, run.released_amount, run.allocation_snapshot]))
