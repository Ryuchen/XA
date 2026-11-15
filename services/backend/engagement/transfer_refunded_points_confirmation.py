"""Isolated net membership confirmation after a transferred points refund."""
import hashlib
import json

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import (ClubOperationConfig, ConsumptionConfirmation, OrderFundingAllocation, PointLedger,
                     TransferRefundPointsConsumptionEvidence)


def enabled():
    from orders.points_checkout_test import points_consumption_enabled, require_isolated_checkout
    from orders.points_transfer_refund_access import permits
    if permits(phase='settlement'):
        return points_consumption_enabled()
    if not (getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_CONSUMPTION_TESTING', False)
            and getattr(settings, 'SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING', False)
            and points_consumption_enabled()):
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    return True


def _digest(row):
    payload = [row.manifest_id, row.posting_id, row.benefit_snapshot_id, row.account_id, row.business_cycle,
        row.paid_units, row.refunded_coin_units, row.net_paid_units, row.net_principal_units,
        row.refunded_principal_units, row.points_rate, row.birthday_multiplier,
        row.confirmation_vip_enabled, row.confirmation_points_enabled, row.vip_eligible,
        row.points_eligible, row.awarded_points, row.points_ledger_id, row.confirmation_id,
        row.resolved_at.isoformat()]
    return hashlib.sha256(json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _facts_locked(order):
    """Require final, fully funded service settlement and actual refund proof."""
    from orders.models import OrderSegmentSettlementManifest, PointsNegotiationPosting
    from orders.points_benefit_snapshot import verify_locked as verify_snapshot
    from orders.points_funding_evidence import verify_order_funding
    from orders.points_funding_plan import PointsFundingPlanError
    from orders.points_service_refund import verify_refund_history_locked
    from orders.points_transfer_refunded_funding import plan_refunded_transfer
    from orders.segment_settlement import verify_finalized
    from orders.settlement import SettlementError
    if not transaction.get_connection().in_atomic_block or not order.settled_at:
        raise ValidationError('转单退款订单尚未完成整单收益结算')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    postings = list(PointsNegotiationPosting.objects.select_for_update().select_related(
        'points_refund__refund', 'points_refund__redemption__order',
        'points_refund__redemption__payment').filter(points_refund__redemption__order=order)
        .order_by('points_refund__refund_id'))
    if (not manifest or not postings or order.refund_records.count() != len(postings)
            or manifest.finalized_at != order.settled_at):
        raise ValidationError('转单退款订单缺少最终收益凭证')
    followup = len(postings) >= 2
    third = len(postings) >= 3
    from orders.points_transfer_refund_access import permits
    if followup and not (permits(order, phase='settlement')
                         or (getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_CONSUMPTION_TESTING', False)
                             and getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', False))):
        raise ValidationError('转单追加退款后的会员实销尚未开放')
    posting = postings[-1]
    try:
        verify_finalized(order, manifest)
        if third:
            from types import SimpleNamespace
            if len(postings) == 3:
                from orders.points_third_refund_settlement_plan import prepare_locked
            else:
                from orders.points_round_settlement_plan import prepare_locked
            _, reconstructed = prepare_locked(order.pk, allow_finalized=True)
            if reconstructed != manifest.plan:
                raise ValidationError('第三轮退款的最终清单与资金版本不一致')
            terms = reconstructed.get('points_funding', {})
            posting_ids = ([terms.get('refund_posting_id'), terms.get('followup_posting_id'),
                            terms.get('third_posting_id')] if len(postings) == 3
                           else terms.get('round_posting_ids'))
            if posting_ids != [item.pk for item in postings]:
                raise ValidationError('第三轮退款清单的原始事件不一致')
            proofs = [verify_refund_history_locked(item.points_refund.refund_id) for item in postings]
            if (any(proof.pk != item.points_refund_id or proof.refund.amount != proof.coin_units
                    for proof, item in zip(proofs, postings))
                    or len({proof.redemption_id for proof in proofs}) != 1):
                raise ValidationError('第三轮退款的原支付或退款凭证不一致')
            calculation = terms.get('calculation', {})
            funding = SimpleNamespace(net_coin_units=calculation.get('net_coin_units'),
                                      net_principal_units=calculation.get('net_principal_units'))
            fully_funded = True  # prepare_locked has verified the actual receipt allocations.
            proof = SimpleNamespace(redemption=proofs[0].redemption,
                coin_units=sum(item.coin_units for item in proofs),
                prepaid_units=sum(item.prepaid_units for item in proofs))
        elif followup:
            from types import SimpleNamespace
            from orders.points_transfer_followup_funding import plan_locked
            from orders.models import OrderPointsTransferFollowupFundingRevision
            funding = plan_locked(order.pk, allow_recorded=True, allow_finalized=True)
            verified = verify_order_funding(order.pk, allow_finalized=True, allow_followup_recorded=True)
            fully_funded = verified.fully_funded
            proofs = [verify_refund_history_locked(item.points_refund.refund_id) for item in postings]
            revision = OrderPointsTransferFollowupFundingRevision.objects.select_for_update().filter(posting=posting).first()
            shares = list(posting.shares.all())
            if (not revision or any(proof.pk != item.points_refund_id or proof.refund.amount != proof.coin_units
                    for proof, item in zip(proofs, postings))
                    or proofs[0].redemption_id != proofs[1].redemption_id
                    or posting.proposal.points_terms.prior_refund_id != proofs[0].pk
                    or not shares or len({item.account_id for item in shares}) != len(shares)
                    or {item.account_id for item in shares} != set(posting.proposal.affected_accounts)
                    or (revision.prior_manifest and any(
                        item.get('account_id') in {share.account_id for share in shares}
                        for item in revision.prior_manifest.get('people', [])))
                    or any(item.pending_deduction + item.wallet_deduction
                        + item.deposit_deduction != item.burden for item in shares)
                    or proofs[1].refund.recovered_amount != sum(
                        item.wallet_deduction + item.deposit_deduction for item in shares)):
                raise ValidationError('转单追加退款的原支付或退款凭证不一致')
            proof = SimpleNamespace(redemption=proofs[0].redemption,
                coin_units=sum(item.coin_units for item in proofs),
                prepaid_units=sum(item.prepaid_units for item in proofs))
        else:
            funding = plan_refunded_transfer(order.pk, allow_finalized=True)
            verified = verify_order_funding(order.pk, allow_finalized=True)
            fully_funded = verified.fully_funded
            proof = verify_refund_history_locked(posting.points_refund.refund_id)
    except (SettlementError, PointsFundingPlanError) as exc:
        raise ValidationError('转单退款订单的结算或补资凭证待核对') from exc
    receipt = proof.redemption
    if ((not followup and (proof.pk != posting.points_refund_id or proof.refund.amount != proof.coin_units))
            or receipt.order_id != order.pk or receipt.paid_units != order.amount
            or (len(postings) <= 3 and manifest.plan.get('points_funding', {}).get('refund_posting_id') != postings[0].pk)
            or (followup and not third and manifest.plan.get('points_funding', {}).get('followup_posting_id') != posting.pk)
            or not fully_funded):
        raise ValidationError('转单退款付款、退款或补资归属不一致')
    snapshot = verify_snapshot(receipt)
    principal = OrderFundingAllocation.objects.filter(order=order, kind='PREPAID').aggregate(
        paid=Sum('amount'), returned=Sum('refunded_amount'))
    paid_principal, refunded_principal = principal['paid'] or 0, principal['returned'] or 0
    net_paid, net_principal = receipt.paid_units - proof.coin_units, paid_principal - refunded_principal
    # The verified funding plan is for the whole order, including separately
    # paid add-ons. Their membership decision is recorded independently, so
    # compare this evidence against the main-order component only.
    terms = manifest.plan.get('points_funding', {}) if isinstance(manifest.plan, dict) else {}
    addon = terms.get('addon_totals')
    from orders.models import OrderAddonPayment
    addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                  .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
    if addons:
        if (not isinstance(addon, dict) or any(type(addon.get(key)) is not int or addon[key] < 0
                                               for key in ('paid_units', 'principal_units'))
                or addon['principal_units'] > addon['paid_units']):
            raise ValidationError('加购净付款或本金结算凭证缺失')
        from orders.addon_payment import verify_receipt
        history_by_payment = {}
        if third:
            history = terms.get('addon_history')
            refund_ids = terms.get('addon_refund_ids')
            if (not isinstance(history, list) or not isinstance(refund_ids, list)
                    or any(not isinstance(entry, dict) or type(entry.get('payment_id')) is not int
                           for entry in history)
                    or len({entry['payment_id'] for entry in history}) != len(history)
                    or {entry['payment_id'] for entry in history} != {item.pk for item in addons}
                    or any(type(pk) is not int for pk in refund_ids)
                    or len(set(refund_ids)) != len(refund_ids)):
                raise ValidationError('第三轮加购付款或退款清单不完整')
            history_by_payment = {entry['payment_id']: entry for entry in history}
        actual_paid = actual_principal = 0
        actual_refund_ids = []
        for item in addons:
            verify_receipt(item)
            if item.intent.buyer_id != order.customer_account_id:
                raise ValidationError('加购付款老板与主单不一致')
            refunds = list(item.refund_records.order_by('created_at', 'pk'))
            paid = item.intent.amount - sum(refund.amount for refund in refunds)
            principal = sum(source.amount - source.refunded_amount for source in
                            item.allocations.filter(kind='PREPAID'))
            if third:
                entry = history_by_payment[item.pk]
                if (entry.get('refund_ids') != [refund.pk for refund in refunds]
                        or entry.get('paid_units') != paid or entry.get('principal_units') != principal):
                    raise ValidationError('第三轮加购逐笔净实付或退款来源与清单不一致')
                actual_refund_ids.extend(refund.pk for refund in refunds)
            actual_paid += paid
            actual_principal += principal
        if third and set(actual_refund_ids) != set(refund_ids):
            raise ValidationError('第三轮加购退款清单与实际退款不一致')
        if addon['paid_units'] != actual_paid or addon['principal_units'] != actual_principal:
            raise ValidationError('加购退款后净付款或本金来源不一致')
        addon_paid, addon_principal = addon['paid_units'], addon['principal_units']
    elif addon:
        raise ValidationError('加购结算凭证与付款不一致')
    else:
        addon_paid = addon_principal = 0
    if (proof.prepaid_units != refunded_principal or refunded_principal > paid_principal
            or proof.coin_units > receipt.paid_units or funding.net_coin_units != net_paid + addon_paid
            or funding.net_principal_units != net_principal + addon_principal):
        raise ValidationError('转单退款后的净实付或本金来源不一致')
    return manifest, posting, receipt, proof, snapshot, net_paid, net_principal, refunded_principal


def verify_evidence_locked(row):
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('转单退款会员凭证须在门店事务内核对')
    from orders.models import Order
    row = TransferRefundPointsConsumptionEvidence.objects.select_for_update().select_related(
        'manifest__order', 'posting', 'benefit_snapshot', 'points_ledger', 'confirmation').get(pk=row.pk)
    order = Order.objects.select_for_update().get(pk=row.manifest.order_id)
    manifest, posting, receipt, proof, snapshot, net_paid, principal, returned = _facts_locked(order)
    terms = snapshot.terms
    from .license_refund_vip_review import verify_decision
    decision = verify_decision(order, paid_units=receipt.paid_units, refunded_units=proof.coin_units,
        payment_vip=terms['vip_enabled'], confirmation_vip=row.confirmation_vip_enabled, decided_at=row.resolved_at)
    vip_eligible = terms['vip_enabled'] and row.confirmation_vip_enabled and decision.outcome == 'ALLOW'
    points_eligible = vip_eligible and terms['points_enabled'] and row.confirmation_points_enabled
    if points_eligible:
        from .services import license_allows_recorded_points
        expected = principal * terms['points_rate'] * terms['birthday_multiplier'] // 100
        if expected and not license_allows_recorded_points(order, expected):
            points_eligible = False
    points = principal * terms['points_rate'] * terms['birthday_multiplier'] // 100 if points_eligible else 0
    if (row.manifest_id != manifest.pk or row.posting_id != posting.pk or row.benefit_snapshot_id != snapshot.pk
            or row.account_id != order.customer_account_id or row.business_cycle != order.business_cycle
            or (row.paid_units, row.refunded_coin_units, row.net_paid_units, row.net_principal_units,
                row.refunded_principal_units) != (receipt.paid_units, proof.coin_units, net_paid, principal, returned)
            or row.points_rate != terms['points_rate'] or row.birthday_multiplier != terms['birthday_multiplier']
            or row.vip_eligible != vip_eligible or row.points_eligible != points_eligible or row.awarded_points != points
            or row.resolved_at < manifest.finalized_at or row.resolved_at > timezone.now()
            or order.funding_benefits_resolved_at != row.resolved_at
            or order.funding_award_refunded_principal != returned or row.evidence_hash != _digest(row)):
        raise ValidationError('转单退款后的会员实销或积分凭证不一致')
    ledgers = list(PointLedger.objects.filter(order=order, kind='CONSUMPTION',
                                              event_key=f'order:{order.pk}:points').order_by('pk'))
    if points:
        ledger = row.points_ledger
        if (len(ledgers) != 1 or not ledger or ledgers[0].pk != ledger.pk
                or ledger.event_key != f'order:{order.pk}:points' or ledger.account_id != row.account_id
                or ledger.amount != points):
            raise ValidationError('转单退款后的消费积分流水不一致')
    elif row.points_ledger_id or ledgers:
        raise ValidationError('转单退款后不应存在消费积分流水')
    confirmation = row.confirmation
    if vip_eligible and net_paid:
        if (not confirmation or confirmation.order_id != order.pk or confirmation.account_id != row.account_id
                or confirmation.business_cycle != row.business_cycle or confirmation.amount != receipt.paid_units
                or confirmation.refunded_amount != proof.coin_units or confirmation.reversed_at
                or confirmation.confirmed_at != row.resolved_at or order.consumption_confirmed_at != row.resolved_at):
            raise ValidationError('转单退款后的会员实销记录不一致')
    elif (confirmation or order.consumption_confirmed_at
          or ConsumptionConfirmation.objects.filter(order=order).exists()):
        raise ValidationError('转单退款后不应存在会员实销记录')
    return row


def confirm_refunded_transfer(order_id):
    from orders.models import Order
    from orders.points_transfer_refund_access import permits, verification_scope
    order = Order.objects.filter(pk=order_id).first()
    if order and permits(order, phase='settlement'):
        with verification_scope(order), transaction.atomic():
            return _confirm_locked(order_id)
    if not enabled() or not transaction.get_connection().in_atomic_block:
        raise ValidationError('转单退款后的会员实销尚未开放')
    with transaction.atomic():
        return _confirm_locked(order_id)


def _confirm_locked(order_id):
    from orders.models import Order
    from orders.financial_lock import lock_order_money
    from wallet.services import get_wallet
    from .services import award_order_points, config, locked_profile, update_vip
    from coupons.benefits import try_grant_periodic
    order = Order.objects.select_for_update().get(pk=order_id)
    lock_order_money()
    if order.status != 'COMPLETED' or not order.customer_account_id or not order.settled_at:
        return False
    manifest, posting, receipt, proof, snapshot, net_paid, principal, returned = _facts_locked(order)
    account = order.customer_account
    wallet = get_wallet(account_id=account.pk, user_id=order.customer_id, for_update=True, create=False)
    account.refresh_from_db(fields=['business_cycle', 'is_active'])
    if (not account.is_active or account.business_cycle != order.business_cycle or not wallet
            or not wallet.is_active or wallet.pk != receipt.payment.wallet_id):
        return False
    existing = TransferRefundPointsConsumptionEvidence.objects.select_for_update().filter(manifest=manifest).first()
    if existing:
        verify_evidence_locked(existing)
        return False
    if (order.funding_benefits_resolved_at or order.consumption_confirmed_at
            or ConsumptionConfirmation.objects.filter(order=order).exists()
            or PointLedger.objects.filter(order=order, kind='CONSUMPTION',
                                          event_key=f'order:{order.pk}:points').exists()):
        raise ValidationError('转单退款订单已有未核对的会员或积分记录')
    config()
    operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
    # Keep the config-before-profile lock order and observe License afterward.
    locked_profile(account)
    terms = snapshot.terms
    now = timezone.now()
    from .license_refund_vip_review import record_locked, record_deferred_points
    decision = record_locked(order, paid_units=receipt.paid_units, refunded_units=proof.coin_units,
        payment_vip=terms['vip_enabled'], confirmation_vip=operation.vip_enabled, decided_at=now)
    record_deferred_points(order, decision, principal=principal, terms=terms, points_enabled=operation.points_enabled)
    vip_eligible = terms['vip_enabled'] and operation.vip_enabled and decision.outcome == 'ALLOW'
    points_eligible = vip_eligible and terms['points_enabled'] and operation.points_enabled
    points = principal * terms['points_rate'] * terms['birthday_multiplier'] // 100 if points_eligible else 0
    order.funding_award_refunded_principal, order.funding_benefits_resolved_at = returned, now
    confirmation = ledger = None
    if vip_eligible and net_paid:
        if points:
            award_order_points(order, service_confirmed=True, operation=operation)
            ledger = PointLedger.objects.filter(order=order, event_key=f'order:{order.pk}:points').first()
            from .services import license_allows_recorded_points
            if not ledger and not license_allows_recorded_points(order, points):
                points_eligible, points = False, 0
            elif not ledger or ledger.amount != points:
                raise ValidationError('转单退款后的消费积分发放失败')
        confirmation = ConsumptionConfirmation.objects.create(order=order, account=account,
            business_cycle=order.business_cycle, amount=receipt.paid_units, refunded_amount=proof.coin_units,
            confirmed_at=now)
        order.consumption_confirmed_at = now
    order.save(update_fields=['funding_award_refunded_principal', 'funding_benefits_resolved_at',
                              'consumption_confirmed_at'])
    row = TransferRefundPointsConsumptionEvidence(manifest=manifest, posting=posting, benefit_snapshot=snapshot,
        account=account, business_cycle=order.business_cycle, paid_units=receipt.paid_units,
        refunded_coin_units=proof.coin_units, net_paid_units=net_paid, net_principal_units=principal,
        refunded_principal_units=returned, points_rate=terms['points_rate'], birthday_multiplier=terms['birthday_multiplier'],
        confirmation_vip_enabled=operation.vip_enabled, confirmation_points_enabled=operation.points_enabled,
        vip_eligible=vip_eligible, points_eligible=points_eligible, awarded_points=points,
        points_ledger=ledger, confirmation=confirmation, resolved_at=now)
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
