"""Buyer VIP growth and points for add-ons after the full order settles."""
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderAddonConsumption, OrderAddonPayment


def confirm_addon_consumption(order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购实销确认须在订单事务内执行')
    if not order.settled_at or order.aftersales.filter(status='OPEN').exists():
        return False
    payments = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                    .select_related('intent', 'intent__buyer').order_by('pk'))
    if not payments:
        return False
    from engagement.services import change_points, config, locked_profile, update_vip
    from stores.license_benefits import check_new_benefits
    operation = config()
    created = False
    for payment in payments:
        # Every add-on owns its payment, refund sources and benefit decision;
        # never infer one add-on's net amount from the main order or another
        # add-on when events are interleaved.
        from .addon_payment import verify_receipt
        verify_receipt(payment)
        from .points_service_payment import needs_integrated_processing
        third_points = needs_integrated_processing(order) and order.refund_records.count() >= 3
        if third_points:
            from .points_third_addon_responsibilities import current as current_responsibilities
        else:
            from .addon_handover import current_responsibilities
        _, responsibilities = current_responsibilities(payment, lock=True)
        expected_segments = {row['segment_id'] for row in responsibilities}
        ordinary_rows = list(payment.settlements.select_related(
            'segment__attribution', 'report', 'order_settlement',
            'refund_settlement__posting__points_refund__redemption'))
        segment_rows = list(payment.segment_settlements.select_related('segment__attribution', 'report', 'manifest'))
        settled_segments = {row.segment_id for row in ordinary_rows + segment_rows}
        refund_total = sum(payment.refund_records.values_list('amount', flat=True))
        if third_points and not responsibilities and refund_total == payment.intent.amount and not ordinary_rows and not segment_rows:
            from .models import OrderSegmentSettlementManifest
            from .segment_settlement import verify_finalized
            from .settlement import SettlementError
            manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
            if not manifest:
                raise ValidationError('全额退款加购缺少整单结算清单')
            try:
                verify_finalized(order, manifest)
            except SettlementError as exc:
                raise ValidationError('全额退款加购的整单结算待核对') from exc
            funding = manifest.plan.get('points_funding', {}) if isinstance(manifest.plan, dict) else {}
            history = [item for item in funding.get('addon_history', [])
                       if isinstance(item, dict) and item.get('payment_id') == payment.pk]
            refund_ids = list(payment.refund_records.order_by('created_at', 'pk').values_list('pk', flat=True))
            if (len(history) != 1 or history[0].get('paid_units') != 0
                    or history[0].get('principal_units') != 0 or history[0].get('refund_ids') != refund_ids
                    or any(item.get('payment_id') == payment.pk for item in funding.get('addons', []))):
                raise ValidationError('全额退款加购的实际退款或零收益清单异常')
        if (not responsibilities and refund_total == payment.intent.amount
                and len(ordinary_rows) == 1 and not segment_rows):
            zero = ordinary_rows[0]
            parent_order = (zero.order_settlement.order_id if zero.order_settlement_id else
                zero.refund_settlement.posting.points_refund.redemption.order_id if zero.refund_settlement_id else None)
            handovers = payment.handovers.filter(status='ACCEPTED', accepted_at__lt=zero.settled_at)
            original_owner = (not payment.handovers.exists() and zero.segment_id == payment.intent.segment_id
                              and zero.segment.provider_account_id == payment.intent.provider_id)
            handed_owner = handovers.filter(shares__segment_id=zero.segment_id).exists()
            if (not (original_owner or handed_owner) or zero.segment.attribution.order_id != order.pk
                    or zero.amount or zero.income_transaction_id or parent_order != order.pk
                    or zero.report.order_id != order.pk
                    or zero.report.provider_account_id != zero.segment.provider_account_id
                    or not zero.settled_at or zero.settled_at > order.settled_at):
                raise ValidationError('全额退款加购零收益结算凭证异常')
            expected_segments = {zero.segment_id}
        if settled_segments != expected_segments:
            raise ValidationError('加购收益尚未结算')
        account = payment.intent.buyer
        terms = payment.benefit_terms
        if not isinstance(terms, dict) or not {'vip_enabled', 'points_enabled', 'points_rate',
                                               'birthday_multiplier'} <= set(terms):
            raise ValidationError('加购付款时的会员规则快照不存在')
        principal_refund = (payment.allocations.filter(kind='PREPAID')
                            .aggregate(amount=Sum('refunded_amount'))['amount'] or 0)
        if refund_total > payment.intent.amount or principal_refund > payment.principal:
            raise ValidationError('加购退款超过原付款')
        existing = OrderAddonConsumption.objects.select_for_update().filter(payment=payment).first()
        if existing:
            if (existing.account_id != account.pk or existing.business_cycle != order.business_cycle
                    or existing.paid_units != payment.intent.amount or existing.refunded_units != refund_total
                    or existing.principal_units != payment.principal
                    or existing.refunded_principal_units != principal_refund):
                raise ValidationError('加购实销与当前退款来源不一致')
            continue
        # Point posting locks this member row. Capture License after waiting
        # for it so a request queued across the expiry instant cannot award
        # points using its earlier eligibility observation.
        profile = None
        if terms['vip_enabled'] and operation.vip_enabled:
            profile = locked_profile(account)
            account.refresh_from_db(fields=['business_cycle'])
            operation.refresh_from_db(fields=['vip_enabled', 'points_enabled'])
        same_cycle = account.business_cycle == order.business_cycle
        vip_at_both = bool(same_cycle and terms['vip_enabled'] and operation.vip_enabled)
        vip_outcome, vip_evidence = check_new_benefits('VIP_GROWTH')
        points_outcome, points_evidence = check_new_benefits()
        vip_eligible = vip_at_both and vip_outcome == 'ALLOW'
        points_applicable = bool(vip_at_both and terms['points_enabled'] and operation.points_enabled
                                 and vip_outcome != 'DENY')
        points_at_decision = ((payment.principal - principal_refund) * terms['points_rate']
                              * terms['birthday_multiplier'] // 100) if points_applicable else 0
        points = 0
        ledger = None
        if (vip_eligible and terms['points_enabled'] and operation.points_enabled and points_outcome == 'ALLOW'):
            points = points_at_decision
            if points:
                ledger = change_points(profile, points, f'addon:{payment.pk}:points', 'CONSUMPTION',
                                       '加购本金消费积分（按付款时会员及生日倍率）', order=order)
        consumption = OrderAddonConsumption.objects.create(payment=payment, account=account, business_cycle=order.business_cycle,
            paid_units=payment.intent.amount, refunded_units=refund_total, principal_units=payment.principal,
            refunded_principal_units=principal_refund, vip_eligible=vip_eligible, awarded_points=points,
            points_ledger=ledger, license_decisions={
                'vip': {'outcome': vip_outcome, 'evidence': vip_evidence, 'applicable': vip_at_both},
                'points': {'outcome': points_outcome, 'evidence': points_evidence, 'applicable': points_applicable,
                           'points_at_decision_hundredths': points_at_decision,
                           'refunded_principal_at_decision': principal_refund}})
        from engagement.achievement_metrics import record_achievement_event
        record_achievement_event(order, addon=consumption)
        created = True
    if created:
        update_vip(order.customer_account)
    return created


def reverse_addon_consumption(refund, *, vip_operation=None):
    """Keep VIP and earned-points accounting aligned with the actual refund."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购退款和实销冲回须在同一订单事务内执行')
    consumption = OrderAddonConsumption.objects.select_for_update().filter(payment=refund.payment).first()
    if not consumption:
        return
    payment = refund.payment
    if payment.intent.order_id != refund.order_id or consumption.account_id != payment.intent.buyer_id:
        raise ValidationError('加购退款与原实销归属不一致')
    refunded = sum(payment.refund_records.values_list('amount', flat=True))
    principal = (payment.allocations.filter(kind='PREPAID')
                 .aggregate(amount=Sum('refunded_amount'))['amount'] or 0)
    if (refunded > consumption.paid_units or principal > consumption.principal_units
            or refunded < consumption.refunded_units or principal < consumption.refunded_principal_units):
        raise ValidationError('加购退款超过已确认实销')
    new_reversed = consumption.reversed_points
    if consumption.awarded_points:
        terms = payment.benefit_terms
        remaining = (consumption.principal_units - principal) * terms['points_rate'] * terms['birthday_multiplier'] // 100
        new_reversed = max(0, consumption.awarded_points - remaining)
        difference = new_reversed - consumption.reversed_points
        if difference < 0:
            raise ValidationError('加购积分冲回累计异常')
        if difference > 0:
            from engagement.services import change_points, locked_profile
            profile = locked_profile(consumption.account)
            change_points(profile, -difference, f'addon-refund:{refund.pk}:points', 'REFUND',
                          '加购退款冲回本金消费积分', order=refund.order, allow_debt=True)
    consumption.refunded_units, consumption.refunded_principal_units = refunded, principal
    consumption.reversed_points = new_reversed
    consumption.save(update_fields=['refunded_units', 'refunded_principal_units', 'reversed_points'])
    from engagement.license_addon_review import reverse_locked
    reviewed_vip = reverse_locked(consumption, refund)
    if consumption.vip_eligible or reviewed_vip:
        from engagement.refunds import recalculate_vip_after_refund
        recalculate_vip_after_refund(consumption.account, consumption.business_cycle, vip_operation=vip_operation)
