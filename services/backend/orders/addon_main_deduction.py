"""Verified add-on refund deductions from each person's unpaid main income."""
from collections import defaultdict

from rest_framework.exceptions import ValidationError

from .models import OrderAddonRefund


def deductions_at(order, *, before=None):
    refunds = OrderAddonRefund.objects.filter(order=order, main_pending_deduction__gt=0).select_related('payment__intent')
    if before is not None:
        if refunds.filter(created_at=before).exists():
            raise ValidationError('加购退款与主单退款时序无法区分')
        refunds = refunds.filter(created_at__lt=before)
    refunds = list(refunds.order_by('pk'))
    from .addon_payment import verify_receipt
    for payment in {row.payment_id: row.payment for row in refunds}.values():
        verify_receipt(payment)
    amounts = defaultdict(int)
    for refund in refunds:
        people = list(refund.proposal.participants.filter(main_pending_deduction__gt=0))
        if sum(person.main_pending_deduction for person in people) != refund.main_pending_deduction:
            raise ValidationError('加购退款的主单收益扣减凭证不一致')
        for person in people:
            amounts[person.account_id] += person.main_pending_deduction
    return dict(amounts)
