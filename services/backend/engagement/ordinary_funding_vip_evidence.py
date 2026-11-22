"""Prove an ordinary funded service was settled before License review."""
from contextlib import nullcontext
from datetime import timedelta

from rest_framework.exceptions import ValidationError
from stores.db import transaction
from wallet.models import Transaction


def verify_locked(order, decision):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('会员实销核对须在订单事务中执行')
    from .license_refund_vip_review import _verify_original_denial
    from .license_points_review import _verify_award_sources
    from orders.models import OrderPointsSettlement
    from orders.ordinary_funding_continuation import finalized_read_scope
    from orders.points_checkout_test import ordinary_settlement_enabled
    from orders.points_settlement_test import verified_order_settlement_display

    _verify_original_denial(order, decision)
    if (order.refund_records.exists() or decision.refunded_units
            or order.status != 'COMPLETED' or order.payment_status != 'PAID'
            or order.service_clock_version is None or order.service_aftersale_hours is None
            or order.service_freeze_hours is None or not order.completed_at or not order.settled_at
            or order.settled_at > decision.decided_at
            or order.completed_at + timedelta(hours=order.service_aftersale_hours) > order.settled_at
            or not order.consumption_ready_at or order.consumption_ready_at > decision.decided_at):
        raise ValidationError('普通服务结算时间或退款事实待核对')
    _verify_award_sources(order)
    # Structured settlement stores payment evidence separately from the original provider shares.
    settlement = OrderPointsSettlement.objects.select_for_update().filter(
        order=order, redemption__isnull=True, finalized_at=order.settled_at).first()
    if (not settlement or settlement.funding_units != order.settlement_store_funding
            or order.settlement_addon_contribution):
        raise ValidationError('普通服务人员结算及补资凭证待核对')
    with (nullcontext() if ordinary_settlement_enabled() else finalized_read_scope(order)):
        if verified_order_settlement_display(order) != 'SETTLED':
            raise ValidationError('普通服务人员结算及补资凭证待核对')

    people = list(settlement.persons.select_for_update().values_list('settled_at', 'income_transaction_id'))
    if (len(people) != order.required_provider_count
            or any(settled_at > settlement.finalized_at for settled_at, _ in people)):
        raise ValidationError('普通服务逐人结算时点待核对')
    expected = [(tx_id, settled_at) for settled_at, tx_id in people if tx_id]
    expected.extend((tx_id, settlement.finalized_at)
                    for tx_id in (settlement.inviter_transaction_id, settlement.store_transaction_id) if tx_id)
    credits = dict(order.transactions.filter(
        status=Transaction.Status.SUCCESS, tx_type__in=(Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME),
    ).values_list('pk', 'created_at'))
    if (sorted(tx_id for tx_id, _ in expected) != sorted(credits)
            or any(not posted_at <= credits[tx_id] <= decision.decided_at for tx_id, posted_at in expected)):
        raise ValidationError('普通服务原时点收益流水待核对')
