"""Isolated membership confirmation after a points-order refund settles."""
import hashlib
import json
from types import SimpleNamespace

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import (ClubOperationConfig, ConsumptionConfirmation, OrderFundingAllocation, PointLedger,
                     RefundedPointsConsumptionEvidence)


def enabled():
    from orders.points_checkout_test import points_consumption_enabled
    from orders.points_refunded_settlement import enabled as refunded_settlement_enabled
    from orders.points_business_access import permits
    return bool((permits(phase='settlement', kind='service') or permits(phase='settlement', kind='addon')
                 or getattr(settings, 'SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING', False))
                and points_consumption_enabled() and refunded_settlement_enabled())


def _digest(row):
    payload = [row.settlement_id, row.benefit_snapshot_id, row.account_id, row.business_cycle,
        row.paid_units, row.refunded_coin_units, row.net_paid_units, row.net_principal_units,
        row.refunded_principal_units, row.points_rate, row.birthday_multiplier,
        row.confirmation_vip_enabled, row.confirmation_points_enabled, row.vip_eligible,
        row.points_eligible, row.awarded_points, row.points_ledger_id, row.confirmation_id,
        row.resolved_at.isoformat()]
    return hashlib.sha256(json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _facts_locked(order):
    """Reconcile the paid receipt, refund and every final settlement credit."""
    from orders.models import OrderPointsRedemption, OrderPointsRefundSettlement
    from orders.points_benefit_snapshot import verify_locked as verify_snapshot
    from orders.points_settlement_test import verified_order_settlement_display
    if not transaction.get_connection().in_atomic_block or not order.settled_at:
        raise ValidationError('退款订单尚未完成收益结算')
    if verified_order_settlement_display(order) != 'SETTLED':
        raise ValidationError('退款订单补资或收益凭证待核对')
    receipt = OrderPointsRedemption.objects.select_for_update().select_related('order').filter(order=order).first()
    if not receipt:
        raise ValidationError('退款订单付款或结算凭证缺失')
    proofs = list(receipt.refunds.select_related('refund').order_by('refund_id'))
    if len(proofs) != order.refund_records.count():
        raise ValidationError('退款订单的实际退款来源不完整')
    from orders.points_service_refund import verify_refund_history_locked
    for item in proofs:
        verified = verify_refund_history_locked(item.refund_id)
        if verified.pk != item.pk:
            raise ValidationError('退款订单的退款凭证不一致')
    if len(proofs) == 1:
        settlement = OrderPointsRefundSettlement.objects.select_for_update().select_related(
            'posting__points_refund').filter(posting__points_refund=proofs[0]).first()
    elif len(proofs) == 2:
        from orders.points_followup_settlement import enabled as followup_enabled, verify_followup_settlement_locked
        if not followup_enabled():
            raise ValidationError('多笔退款后的会员实销尚未开放')
        settlement = verify_followup_settlement_locked(order)
    else:
        raise ValidationError('退款订单退款记录待核对')
    if not settlement or settlement.finalized_at != order.settled_at:
        raise ValidationError('退款订单付款或结算凭证缺失')
    snapshot = verify_snapshot(receipt)
    if any(item.coin_units != item.refund.amount for item in proofs):
        raise ValidationError('退款订单实际退回店币不一致')
    proof = SimpleNamespace(coin_units=sum(item.coin_units for item in proofs),
                            prepaid_units=sum(item.prepaid_units for item in proofs))
    principal = OrderFundingAllocation.objects.filter(order=order, kind='PREPAID').aggregate(
        paid=Sum('amount'), returned=Sum('refunded_amount'))
    paid_principal, refunded_principal = principal['paid'] or 0, principal['returned'] or 0
    if (receipt.paid_units != order.amount or proof.prepaid_units != refunded_principal or refunded_principal > paid_principal
            or settlement.paid_units != receipt.paid_units - proof.coin_units
            or settlement.principal_units != paid_principal - refunded_principal):
        raise ValidationError('退款订单净实付或本金来源不一致')
    return receipt, settlement, snapshot, proof, refunded_principal


def verify_evidence_locked(row):
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('退款会员实销凭证须在门店事务内核对')
    from orders.models import Order
    row = RefundedPointsConsumptionEvidence.objects.select_for_update().select_related(
        'settlement__posting__points_refund__redemption__order', 'benefit_snapshot',
        'points_ledger', 'confirmation').get(pk=row.pk)
    order_id = row.settlement.posting.points_refund.redemption.order_id
    order = Order.objects.select_for_update().get(pk=order_id)
    receipt, settlement, snapshot, proof, refunded_principal = _facts_locked(order)
    terms = snapshot.terms
    from .license_refund_vip_review import verify_decision
    decision = verify_decision(order, paid_units=receipt.paid_units, refunded_units=proof.coin_units,
        payment_vip=terms['vip_enabled'], confirmation_vip=row.confirmation_vip_enabled, decided_at=row.resolved_at)
    vip_eligible = terms['vip_enabled'] and row.confirmation_vip_enabled and decision.outcome == 'ALLOW'
    points_eligible = vip_eligible and terms['points_enabled'] and row.confirmation_points_enabled
    if points_eligible:
        from .services import license_allows_recorded_points
        expected = settlement.principal_units * terms['points_rate'] * terms['birthday_multiplier'] // 100
        if expected and not license_allows_recorded_points(order, expected):
            points_eligible = False
    points = (settlement.principal_units * terms['points_rate'] * terms['birthday_multiplier'] // 100
              if points_eligible else 0)
    if (row.settlement_id != settlement.pk or row.benefit_snapshot_id != snapshot.pk
            or row.account_id != order.customer_account_id or row.business_cycle != order.business_cycle
            or row.paid_units != receipt.paid_units or row.refunded_coin_units != proof.coin_units
            or row.net_paid_units != settlement.paid_units or row.net_principal_units != settlement.principal_units
            or row.refunded_principal_units != refunded_principal or row.points_rate != terms['points_rate']
            or row.birthday_multiplier != terms['birthday_multiplier'] or row.vip_eligible != vip_eligible
            or row.points_eligible != points_eligible or row.awarded_points != points
            or row.resolved_at < settlement.finalized_at or row.resolved_at > timezone.now()
            or order.funding_benefits_resolved_at != row.resolved_at
            or order.funding_award_refunded_principal != refunded_principal or row.evidence_hash != _digest(row)):
        raise ValidationError('退款会员实销或积分凭证不一致')
    # Add-ons have independent consumption ledgers on the same order.
    ledgers = list(PointLedger.objects.filter(order=order, kind='CONSUMPTION',
                                             event_key=f'order:{order.pk}:points').order_by('pk'))
    if points:
        ledger = row.points_ledger
        if (len(ledgers) != 1 or not ledger or ledgers[0].pk != ledger.pk
                or ledger.event_key != f'order:{order.pk}:points' or ledger.account_id != row.account_id
                or ledger.amount != points):
            raise ValidationError('退款后的消费积分流水不一致')
    elif row.points_ledger_id or ledgers:
        raise ValidationError('退款后不应存在消费积分流水')
    confirmation = row.confirmation
    expected_confirmation = vip_eligible and settlement.paid_units > 0
    if expected_confirmation:
        if (not confirmation or confirmation.order_id != order.pk or confirmation.account_id != row.account_id
                or confirmation.business_cycle != row.business_cycle or confirmation.amount != receipt.paid_units
                or confirmation.refunded_amount != proof.coin_units or confirmation.reversed_at
                or confirmation.confirmed_at != row.resolved_at or order.consumption_confirmed_at != row.resolved_at):
            raise ValidationError('退款后的会员实销记录不一致')
    elif (confirmation or order.consumption_confirmed_at
          or ConsumptionConfirmation.objects.filter(order=order).exists()):
        raise ValidationError('退款后不应存在会员实销记录')
    return row


def confirm_refunded_order(order_id):
    from orders.points_business_access import configured, permits
    if configured() and not (permits(phase='settlement', kind='service') or permits(phase='settlement', kind='addon')):
        raise ValidationError('积分退款实销上下文无效')
    if not enabled() or not transaction.get_connection().in_atomic_block:
        raise ValidationError('退款后的会员实销尚未开放')
    # A caller may catch the error; keep points, confirmation and VIP grants
    # together in their own savepoint even in that case.
    with transaction.atomic():
        return _confirm_refunded_order(order_id)


def _confirm_refunded_order(order_id):
    from orders.models import Order
    from orders.financial_lock import lock_order_money
    from wallet.services import get_wallet
    from .services import award_order_points, config, locked_profile, update_vip
    from coupons.benefits import try_grant_periodic
    order = Order.objects.select_for_update().get(pk=order_id)
    from orders.points_business_access import configured, permits
    if configured() and not (permits(order, phase='settlement', kind='service') or permits(order, phase='settlement', kind='addon')):
        raise ValidationError('积分退款实销上下文与订单不一致')
    lock_order_money()
    if order.status != 'COMPLETED' or not order.customer_account_id or not order.settled_at:
        return False
    receipt, settlement, snapshot, proof, refunded_principal = _facts_locked(order)
    account = order.customer_account
    wallet = get_wallet(account_id=account.pk, user_id=order.customer_id, for_update=True, create=False)
    account.refresh_from_db(fields=['business_cycle', 'is_active'])
    if (not account.is_active or account.business_cycle != order.business_cycle or not wallet
            or not wallet.is_active or wallet.pk != receipt.payment.wallet_id):
        return False
    existing = RefundedPointsConsumptionEvidence.objects.select_for_update().filter(settlement=settlement).first()
    if existing:
        verify_evidence_locked(existing)
        return False
    if (order.funding_benefits_resolved_at or order.consumption_confirmed_at
            or ConsumptionConfirmation.objects.filter(order=order).exists()
            or PointLedger.objects.filter(order=order, kind='CONSUMPTION',
                                          event_key=f'order:{order.pk}:points').exists()):
        raise ValidationError('退款订单已有未核对的会员或积分记录')
    config()
    operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
    # Keep the config-before-profile lock order used by funding operations,
    # then observe the License after both waits have completed.
    locked_profile(account)
    terms = snapshot.terms
    now = timezone.now()
    from .license_refund_vip_review import record_locked, record_deferred_points
    decision = record_locked(order, paid_units=receipt.paid_units, refunded_units=proof.coin_units,
        payment_vip=terms['vip_enabled'], confirmation_vip=operation.vip_enabled, decided_at=now)
    record_deferred_points(order, decision, principal=settlement.principal_units,
                           terms=terms, points_enabled=operation.points_enabled)
    vip_eligible = terms['vip_enabled'] and operation.vip_enabled and decision.outcome == 'ALLOW'
    points_eligible = vip_eligible and terms['points_enabled'] and operation.points_enabled
    points = settlement.principal_units * terms['points_rate'] * terms['birthday_multiplier'] // 100 if points_eligible else 0
    order.funding_award_refunded_principal = refunded_principal
    order.funding_benefits_resolved_at = now
    confirmation = ledger = None
    if vip_eligible and settlement.paid_units:
        if points:
            award_order_points(order, service_confirmed=True, operation=operation)
            ledger = PointLedger.objects.filter(order=order, event_key=f'order:{order.pk}:points').first()
            from .services import license_allows_recorded_points
            if not ledger and not license_allows_recorded_points(order, points):
                points_eligible, points = False, 0
            elif not ledger or ledger.amount != points:
                raise ValidationError('退款后的消费积分发放失败')
        confirmation = ConsumptionConfirmation.objects.create(order=order, account=account,
            business_cycle=order.business_cycle, amount=receipt.paid_units, refunded_amount=proof.coin_units,
            confirmed_at=now)
        order.consumption_confirmed_at = now
    order.save(update_fields=['funding_award_refunded_principal', 'funding_benefits_resolved_at',
                              'consumption_confirmed_at'])
    row = RefundedPointsConsumptionEvidence(settlement=settlement, benefit_snapshot=snapshot, account=account,
        business_cycle=order.business_cycle, paid_units=receipt.paid_units, refunded_coin_units=proof.coin_units,
        net_paid_units=settlement.paid_units, net_principal_units=settlement.principal_units,
        refunded_principal_units=refunded_principal, points_rate=terms['points_rate'],
        birthday_multiplier=terms['birthday_multiplier'], confirmation_vip_enabled=operation.vip_enabled,
        confirmation_points_enabled=operation.points_enabled, vip_eligible=vip_eligible,
        points_eligible=points_eligible, awarded_points=points, points_ledger=ledger,
        confirmation=confirmation, resolved_at=now)
    row.evidence_hash = _digest(row)
    row.save()
    from .achievement_metrics import record_achievement_event
    record_achievement_event(order)
    if confirmation:
        update_vip(account, operation=operation)
        try_grant_periodic(account)
    from .confirmation import notify_committed_order
    notify_committed_order(order.pk)
    verify_evidence_locked(row)
    return bool(confirmation)
