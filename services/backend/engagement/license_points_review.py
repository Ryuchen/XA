"""Resolve an unavailable historical License decision without rewriting it."""
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import (LicenseConsumptionPointsDecision, LicenseConsumptionPointsReview,
                     OrderFundingAllocation, PointLedger)


def _verify_review(review):
    decision = review.decision
    ledger = review.points_ledger
    if (decision.outcome != 'UNKNOWN' or review.outcome not in ('ALLOW', 'DENY')
            or (review.outcome == 'DENY' and review.awarded_points)
            or (review.awarded_points and (not ledger or ledger.event_key != f'license-review:{decision.pk}:award'
                or ledger.kind != 'LICENSE_REVIEW_AWARD' or ledger.amount != review.awarded_points
                or ledger.account_id != decision.account_id or ledger.order_id != decision.order_id))
            or (not review.awarded_points and ledger is not None)):
        raise ValidationError('积分核对和补发流水不一致')
    return review


def _principal(order, decision):
    """Return paid, refunded and payment-time points factor from order evidence."""
    if order.funding_terms is not None:
        terms = order.funding_terms
        totals = OrderFundingAllocation.objects.filter(order=order, kind='PREPAID').aggregate(
            paid=Sum('amount'), refunded=Sum('refunded_amount'))
        paid, refunded = totals['paid'] or 0, totals['refunded'] or 0
        baseline = order.funding_award_refunded_principal
        if not order.funding_benefits_resolved_at or baseline is None:
            raise ValidationError('原订单实销快照不完整')
    elif not order.is_gift_snapshot:
        from .models import DefaultPaymentPointsSnapshot
        from .default_funding_refunds import verified_sources
        snapshot = DefaultPaymentPointsSnapshot.objects.filter(order=order).first()
        if not snapshot or not snapshot.vip_enabled or not snapshot.points_enabled or not order.funding_benefits_resolved_at:
            raise ValidationError('原订单实销或付款快照不完整')
        sources = [row for row in verified_sources(order, snapshot) if row.kind == 'PREPAID']
        paid = sum(row.amount for row in sources)
        refunded = sum(row.refunded_amount for row in sources)
        baseline = snapshot.awarded_refunded_principal
        terms = {'points_rate': snapshot.points_rate, 'birthday_multiplier': snapshot.birthday_multiplier}
        if baseline is None:
            raise ValidationError('原订单积分本金快照不完整')
    elif order.is_gift_snapshot and isinstance(order.gift_points_snapshot, dict):
        from orders.refunds import refunded_amount
        terms = order.gift_points_snapshot
        paid, refunded, baseline = order.amount, refunded_amount(order), 0
    else:
        raise ValidationError('订单本金和积分倍率快照不完整')
    rate, multiplier = terms.get('points_rate'), terms.get('birthday_multiplier')
    if (type(rate) is not int or rate < 0 or type(multiplier) is not int or multiplier < 1
            or not 0 <= baseline <= refunded <= paid
            or (paid - baseline) * rate * multiplier // 100 != decision.points_amount):
        raise ValidationError('原消费积分和本金快照不一致')
    return paid, refunded, rate * multiplier


def _verify_award_sources(order):
    """Prove payment and refund facts before issuing any reviewed points."""
    from wallet.models import Transaction
    if (not order.settled_at or not order.completed_at or order.amount <= 0
            or order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED')):
        raise ValidationError('订单付款或实销状态待核对')
    tx_type = Transaction.TxType.GIFT if order.is_gift_snapshot else Transaction.TxType.PAY
    payments = list(Transaction.objects.select_for_update().filter(order=order, tx_type=tx_type,
        status=Transaction.Status.SUCCESS, amount__lt=0).select_related('wallet').order_by('pk')[:2])
    if (len(payments) != 1 or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].amount != -order.amount or payments[0].reversal_of_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversals.exists()):
        raise ValidationError('订单原付款钱包流水待核对')
    if order.funding_terms is not None:
        from .funding_wallet import validate_order_funding_sources
        sources = list(OrderFundingAllocation.objects.select_for_update().filter(order=order).select_related('lot'))
        validate_order_funding_sources(order, sources)
    elif order.is_gift_snapshot:
        sources = list(order.point_allocations.select_for_update().select_related('lot'))
        if any(row.lot.account_id != order.customer_account_id or row.lot.kind not in ('PREPAID', 'RECHARGE')
               or row.source_gift_amount not in (None, 0)
               or (row.lot.kind == 'RECHARGE' and row.source_gift_amount is None)
               or not 0 <= row.refunded_amount <= row.amount
               or row.released != (row.refunded_amount == row.amount) for row in sources):
            raise ValidationError('礼物本金来源待核对')
    else:
        from .models import DefaultPaymentPointsSnapshot
        from .default_funding_refunds import verified_sources
        snapshot = DefaultPaymentPointsSnapshot.objects.filter(order=order).first()
        if not snapshot:
            raise ValidationError('订单付款来源快照缺失')
        sources = verified_sources(order, snapshot)
    refunds = order.refund_records.aggregate(value=Sum('amount'))['value'] or 0
    return_rows = list(Transaction.objects.select_for_update().filter(order=order,
        wallet__account_id=order.customer_account_id, tx_type=Transaction.TxType.REFUND,
        status=Transaction.Status.SUCCESS, amount__gt=0))
    if any(row.balance_after != row.balance_before + row.amount for row in return_rows):
        raise ValidationError('订单退款钱包流水待核对')
    returned = sum(row.amount for row in return_rows)
    if order.is_gift_snapshot:
        from collections import Counter
        refund_rows = order.refund_records.filter(amount__gt=0).values_list('amount', 'operator_id', 'operator_account_id')
        if Counter(refund_rows) != Counter((row.amount, row.operator_id, row.operator_account_id) for row in return_rows):
            raise ValidationError('礼物逐笔退款与钱包流水不一致')
    if (not sources or sum(row.amount for row in sources) != order.amount
            or sum(row.refunded_amount for row in sources) != refunds or returned != refunds
            or not 0 <= refunds <= order.amount):
        raise ValidationError('订单付款来源或退款流水不一致，请先核对')


def _original_decision_baseline(order, decision):
    """Validate the original decision without manufacturing current refunds."""
    from wallet.models import Transaction
    evidence = decision.license_evidence
    if (not isinstance(evidence, dict)
            or (evidence.get('reason') != 'VIP_GROWTH_UNKNOWN'
                and evidence.get('status') not in ('CONTROL_UNAVAILABLE', 'LICENSE_CONFIGURATION_INVALID',
                                                    'STORE_CONTEXT_UNAVAILABLE'))):
        raise ValidationError('原积分授权未知依据待核对')
    if (not order.completed_at or not order.settled_at or order.amount <= 0
            or type(order.business_cycle) is not int or order.business_cycle < 1
            or order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED')
            or decision.decided_at < order.completed_at):
        raise ValidationError('原订单付款或实销时点待核对')
    tx_type = Transaction.TxType.GIFT if order.is_gift_snapshot else Transaction.TxType.PAY
    if not Transaction.objects.filter(order=order, wallet__account_id=decision.account_id,
            tx_type=tx_type, status=Transaction.Status.SUCCESS, amount=-order.amount,
            created_at__lte=decision.decided_at).exists():
        raise ValidationError('原订单付款流水待核对')
    if order.is_gift_snapshot:
        if not isinstance(order.gift_points_snapshot, dict):
            raise ValidationError('礼物付款积分快照待核对')
        baseline = 0  # Gift points are decided in the original payment transaction.
    elif order.funding_terms is not None:
        if not order.funding_benefits_resolved_at or decision.decided_at < order.funding_benefits_resolved_at:
            raise ValidationError('原订单实销时点待核对')
        baseline = order.funding_award_refunded_principal
    else:
        from .models import DefaultPaymentPointsSnapshot
        snapshot = DefaultPaymentPointsSnapshot.objects.filter(order=order).first()
        if not snapshot or not order.funding_benefits_resolved_at or decision.decided_at < order.funding_benefits_resolved_at:
            raise ValidationError('原订单付款或实销快照待核对')
        baseline = snapshot.awarded_refunded_principal
    if type(baseline) is not int or not 0 <= baseline <= order.amount:
        raise ValidationError('原积分决定的退款基线待核对')
    return baseline


def resolve_locked(decision_id, *, request_id, actor_id, outcome, reason):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('积分核对须在门店事务内执行')
    if outcome not in ('ALLOW', 'DENY') or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise ValidationError('请填写有效的核对结论和依据')
    reason = reason.strip()
    from orders.models import Order
    decision = LicenseConsumptionPointsDecision.objects.filter(pk=decision_id).first()
    if not decision:
        raise ValidationError('待核对记录不存在')
    order = Order.objects.select_for_update().get(pk=decision.order_id)
    decision = LicenseConsumptionPointsDecision.objects.select_for_update().get(pk=decision_id)
    vip_review = None
    if (outcome == 'ALLOW' and isinstance(decision.license_evidence, dict)
            and decision.license_evidence.get('reason') == 'VIP_GROWTH_UNKNOWN'):
        from .models import LicenseRefundVipReview
        vip_review = LicenseRefundVipReview.objects.filter(
                decision_id=decision.license_evidence.get('vip_decision_id'), decision__order=order,
                outcome='ALLOW').first()
        if not vip_review:
            raise ValidationError('请先核对该笔消费的会员成长授权')
    existing = LicenseConsumptionPointsReview.objects.select_for_update().filter(decision=decision).first()
    if existing:
        if (existing.request_id != request_id or existing.outcome != outcome or existing.reason != reason):
            raise ValidationError('该记录已核对，请查看原处理结果')
        return _verify_review(existing)
    if (decision.outcome != 'UNKNOWN' or decision.account_id != order.customer_account_id
            or decision.event_key != f'order:{order.pk}:points' or decision.points_amount <= 0
            or PointLedger.objects.filter(event_key=decision.event_key).exists()):
        raise ValidationError('原授权决定或积分流水待核对')
    if LicenseConsumptionPointsReview.objects.filter(request_id=request_id).exists():
        raise ValidationError('请求编号已用于其他积分核对')
    if outcome == 'DENY':
        baseline = _original_decision_baseline(order, decision)
        return LicenseConsumptionPointsReview.objects.create(decision=decision, request_id=request_id,
            platform_actor_id=actor_id, outcome=outcome, reason=reason, awarded_points=0,
            refunded_principal_at_award=baseline, points_ledger=None, skip_reason='DENIED')
    from club_accounts.models import ClubAccount
    observed_cycle = ClubAccount.objects.filter(pk=decision.account_id).values_list('business_cycle', flat=True).first()
    if observed_cycle is not None and observed_cycle > order.business_cycle:
        # A stale read is only a hint. Retirement and award both take wallet
        # before account; repeat the decision under those same locks.
        from wallet.services import get_wallet
        if not get_wallet(account_id=decision.account_id, for_update=True, create=False):
            raise ValidationError('原老板钱包不存在，业务周期待核对')
        locked_account = ClubAccount.objects.select_for_update().get(pk=decision.account_id)
        if locked_account.business_cycle > order.business_cycle:
            from .models import RetirementAudit
            if (locked_account.account_type != 'BOSS' or not RetirementAudit.objects.filter(
                    account_id=locked_account.pk, business_cycle=order.business_cycle).exists()):
                raise ValidationError('原业务周期退店事实缺失，不能零额结案')
            baseline = _original_decision_baseline(order, decision)
            return LicenseConsumptionPointsReview.objects.create(decision=decision, request_id=request_id,
                platform_actor_id=actor_id, outcome=outcome, reason=reason, awarded_points=0,
                refunded_principal_at_award=baseline, points_ledger=None, skip_reason='CYCLE_CLOSED')
    if outcome == 'ALLOW':
        _verify_award_sources(order)
    paid, refunded, factor = _principal(order, decision)
    account = ClubAccount.objects.select_for_update().get(pk=decision.account_id)
    if account.business_cycle < order.business_cycle:
        raise ValidationError('账号业务周期早于原订单，不能推断退店状态')
    cycle_closed = account.business_cycle > order.business_cycle
    if vip_review and not vip_review.vip_applied and not cycle_closed and paid != refunded:
        raise ValidationError('会员成长尚未恢复，不能补发消费积分')
    awarded = (paid - refunded) * factor // 100 if outcome == 'ALLOW' and not cycle_closed else 0
    ledger = None
    if awarded:
        from .services import change_points, locked_profile
        ledger = change_points(locked_profile(account), awarded, f'license-review:{decision.pk}:award',
            'LICENSE_REVIEW_AWARD', '授权故障后的消费积分人工核对补发', order=order)
    return LicenseConsumptionPointsReview.objects.create(decision=decision, request_id=request_id,
        platform_actor_id=actor_id, outcome=outcome, reason=reason, awarded_points=awarded,
        refunded_principal_at_award=refunded, points_ledger=ledger,
        skip_reason=('CYCLE_CLOSED' if outcome == 'ALLOW' and cycle_closed else
                     'NO_PRINCIPAL' if outcome == 'ALLOW' and paid == refunded else ''))


def reverse_reviewed_points(order, *, refund=None):
    """Reverse only post-review returned principal, leaving original evidence intact."""
    review = LicenseConsumptionPointsReview.objects.select_for_update().filter(decision__order=order).select_related(
        'decision', 'points_ledger').first()
    if review:
        _verify_review(review)
    if not review or not review.awarded_points or order.customer_account.business_cycle != order.business_cycle:
        return
    paid, refunded, factor = _principal(order, review.decision)
    if refunded < review.refunded_principal_at_award:
        raise ValidationError('核对后的本金退款记录异常')
    remaining = (paid - refunded) * factor // 100
    target = review.awarded_points if refund is None else max(0, review.awarded_points - remaining)
    previous = -(PointLedger.objects.filter(order=order, kind='LICENSE_REVIEW_REFUND')
                 .aggregate(total=Sum('amount'))['total'] or 0)
    delta = target - previous
    if delta > 0:
        from .services import change_points, locked_profile
        suffix = str(refund.pk) if refund is not None else 'full'
        change_points(locked_profile(order.customer_account), -delta, f'license-review:{review.pk}:refund:{suffix}',
            'LICENSE_REVIEW_REFUND', '核对补发积分按退款本金冲回', order=order, allow_debt=True)
