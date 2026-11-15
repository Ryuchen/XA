"""Verify posted add-on refunds before a transferred main-order refund."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderAddonFundingReleaseRun, OrderAddonPayment


def verify_locked(order, *, boundary):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购退款核对须在订单资金事务内执行')
    from .addon_payment import verify_receipt
    from .models import OrderOriginalShareTransfer
    first_transfer = OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
    from .addon_handover import assert_no_pending_handover
    from .addon_funding_release import verify_run
    from wallet.models import get_platform_wallet
    from wallet.refund_deposit import _verify_direct_deficit
    payments = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                    .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
    for payment in payments:
        verify_receipt(payment)
        assert_no_pending_handover(payment)
        if payment.refund_records.filter(created_at=boundary).exists():
            raise ValidationError('加购退款与主单退款时序待核对')
        for refund in payment.refund_records.select_for_update().filter(created_at__lt=boundary).select_related('proposal').order_by('pk'):
            proposal = refund.proposal
            decisions = list(proposal.decisions.all())
            people = list(proposal.participants.all())
            expected = {(payment.intent.buyer_id, 'BOSS')} | {(person.account_id, 'PROVIDER') for person in people}
            if (refund.created_at >= boundary or not proposal.accepted_at
                    or proposal.accepted_at > refund.created_at or not decisions
                    or not people or len(decisions) != len(expected)
                    or {(row.account_id, row.role) for row in decisions} != expected
                    or sum(person.burden for person in people) != refund.amount + refund.rounding_surplus
                    or any(row.accepted is not True or not row.decided_at
                           or row.decided_at < proposal.created_at or row.decided_at > proposal.accepted_at
                           for row in decisions)
                    or proposal.accepted_at != max(row.decided_at for row in decisions)):
                raise ValidationError('加购退款及双方确认时序待核对')
            if refund.rounding_surplus:
                from .models import OrderPointsFundingAllocation
                if OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__lte=refund.created_at).exists():
                    run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
                    kind = (run.ordinary_prestart_snapshot.get('kind')
                            if run and isinstance(run.ordinary_prestart_snapshot, dict) else None)
                    pretransfer = (kind == 'ORDINARY_PRESTART_ADDON_V1' and first_transfer
                                   and run.created_at < first_transfer.created_at)
                    if not run or not (kind == 'ORDINARY_TRANSFER_PRE_MAIN_ADDON_V1' or pretransfer):
                        raise ValidationError('主单退款前加购取整的已有补资释放尚未接通')
            from django.core.exceptions import ValidationError as DjangoValidationError
            from .addon_refund_share_allocation import verify as verify_share_allocations
            try:
                verify_share_allocations(refund)
            except DjangoValidationError as exc:
                raise ValidationError('转单加购退款逐份额来源异常') from exc
            for deficit in refund.deposit_deficits.select_for_update().select_related('profile'):
                try:
                    _verify_direct_deficit(deficit, get_platform_wallet(for_update=True))
                except ValueError as exc:
                    raise ValidationError('加购退款押金凭证待核对') from exc
            run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
            if run:
                verify_run(run)
    return payments
