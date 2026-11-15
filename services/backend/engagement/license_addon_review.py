"""Review add-on VIP and points decisions without changing original evidence."""
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import LicenseAddonBenefitsReview, PointLedger


def _verify(review):
    consumption = review.consumption
    ledger = review.points_ledger
    if ((review.vip_outcome not in ('', 'ALLOW', 'DENY'))
            or (review.points_outcome not in ('', 'ALLOW', 'DENY'))
            or (review.vip_applied and (consumption.vip_eligible or review.vip_outcome != 'ALLOW'))
            or (review.awarded_points and (not ledger or ledger.kind != 'ADDON_LICENSE_AWARD'
                or ledger.event_key != f'addon-license-review:{consumption.pk}:award'
                or ledger.amount != review.awarded_points or ledger.account_id != consumption.account_id
                or ledger.order_id != consumption.payment.intent.order_id))
            or (not review.awarded_points and ledger is not None)):
        raise ValidationError('加购权益核对或补发流水不一致')
    return review


def _facts(consumption):
    payment, terms = consumption.payment, consumption.payment.benefit_terms
    decisions = consumption.license_decisions
    if not isinstance(terms, dict) or not isinstance(decisions, dict):
        raise ValidationError('加购会员和授权快照缺失')
    vip, points = decisions.get('vip'), decisions.get('points')
    if not isinstance(vip, dict) or not isinstance(points, dict):
        raise ValidationError('加购授权决定缺失')
    for decision in (vip, points):
        if decision.get('outcome') not in ('ALLOW', 'DENY', 'UNKNOWN') or type(decision.get('applicable')) is not bool:
            raise ValidationError('加购授权决定异常')
    rate, multiplier = terms.get('points_rate'), terms.get('birthday_multiplier')
    baseline = points.get('refunded_principal_at_decision')
    expected = points.get('points_at_decision_hundredths')
    principal = (payment.allocations.filter(kind='PREPAID').aggregate(value=Sum('refunded_amount'))['value'] or 0)
    refunded = sum(payment.refund_records.values_list('amount', flat=True))
    if (type(rate) is not int or rate < 0 or type(multiplier) is not int or multiplier < 1
            or type(baseline) is not int or type(expected) is not int
            or consumption.account_id != payment.intent.buyer_id
            or consumption.business_cycle != payment.intent.order.business_cycle
            or consumption.paid_units != payment.intent.amount or consumption.principal_units != payment.principal
            or refunded != consumption.refunded_units or principal != consumption.refunded_principal_units
            or not 0 <= baseline <= principal <= consumption.principal_units
            or refunded > consumption.paid_units
            or (points['applicable'] and expected != (consumption.principal_units - baseline) * rate * multiplier // 100)):
        raise ValidationError('加购实销、退款或积分倍率快照异常')
    return vip, points, principal, rate * multiplier


def _verify_payment_and_refund_history(consumption):
    """Prove both the current net and the source baseline at confirmation."""
    from orders.addon_payment import verify_receipt
    payment = consumption.payment
    verify_receipt(payment)
    if consumption.confirmed_at < payment.paid_at:
        raise ValidationError('加购实销早于付款，请先核对')
    allocations = {row.pk: row for row in payment.allocations.select_for_update().order_by('pk')}
    totals = {pk: 0 for pk in allocations}
    original_principal = 0
    refunds = list(payment.refund_records.select_for_update().select_related('proposal').order_by('created_at', 'pk'))
    for refund in refunds:
        plan = refund.proposal.source_plan
        if not isinstance(plan, list) or not plan:
            raise ValidationError('加购退款来源快照缺失')
        seen, refund_total = set(), 0
        for part in plan:
            if not isinstance(part, dict):
                raise ValidationError('加购退款来源快照无效')
            pk, amount, kind = part.get('allocation_id'), part.get('amount_raw'), part.get('kind')
            if (type(pk) is not int or pk not in allocations or pk in seen
                    or type(amount) is not int or amount <= 0 or kind != allocations[pk].kind):
                raise ValidationError('加购退款来源与原付款不一致')
            seen.add(pk)
            totals[pk] += amount
            refund_total += amount
            if refund.created_at <= consumption.confirmed_at and kind == 'PREPAID':
                original_principal += amount
        if refund_total != refund.amount:
            raise ValidationError('加购退款来源合计不一致')
    if any(totals[pk] != row.refunded_amount or totals[pk] > row.amount for pk, row in allocations.items()):
        raise ValidationError('加购累计退款与原付款来源不一致')
    decisions = consumption.license_decisions
    points = decisions.get('points') if isinstance(decisions, dict) else None
    if not isinstance(points, dict) or points.get('refunded_principal_at_decision') != original_principal:
        raise ValidationError('加购实销时本金退款与授权快照不一致')


def _denial_baseline(consumption):
    """Validate original facts without depending on later refund state."""
    payment, terms, decisions = consumption.payment, consumption.payment.benefit_terms, consumption.license_decisions
    if not isinstance(terms, dict) or not isinstance(decisions, dict):
        raise ValidationError('加购原会员或授权快照缺失')
    vip, points = decisions.get('vip'), decisions.get('points')
    if not isinstance(vip, dict) or not isinstance(points, dict):
        raise ValidationError('加购原授权决定缺失')
    for item in (vip, points):
        if item.get('outcome') not in ('ALLOW', 'DENY', 'UNKNOWN') or type(item.get('applicable')) is not bool:
            raise ValidationError('加购原授权决定异常')
        if item['outcome'] == 'UNKNOWN':
            evidence = item.get('evidence')
            if not isinstance(evidence, dict) or evidence.get('status') not in (
                    'CONTROL_UNAVAILABLE', 'LICENSE_CONFIGURATION_INVALID', 'STORE_CONTEXT_UNAVAILABLE'):
                raise ValidationError('加购原授权未知依据待核对')
    intent, debit = payment.intent, payment.buyer_transaction
    if (consumption.account_id != intent.buyer_id or type(consumption.business_cycle) is not int
            or consumption.business_cycle < 1 or consumption.business_cycle != intent.order.business_cycle
            or consumption.paid_units != intent.amount or consumption.principal_units != payment.principal
            or payment.principal != intent.principal or intent.amount != intent.principal + intent.gift + intent.compensation
            or consumption.confirmed_at < payment.paid_at or debit.wallet.account_id != intent.buyer_id
            or debit.order_id is not None or debit.tx_type != 'PAY' or debit.status != 'SUCCESS'
            or debit.amount != -intent.amount or debit.reversal_of_id
            or debit.operator_account_id != payment.operator_id
            or debit.balance_after != debit.balance_before + debit.amount
            or type(terms.get('vip_enabled')) is not bool or type(terms.get('points_enabled')) is not bool
            or (vip['applicable'] and not terms['vip_enabled'])
            or (points['applicable'] and (not terms['points_enabled'] or not vip['applicable']))
            or consumption.vip_eligible != (vip['applicable'] and vip['outcome'] == 'ALLOW')
            or consumption.awarded_points or consumption.points_ledger_id):
        raise ValidationError('加购原付款或实销事实待核对')
    allocations = {row.pk: row for row in payment.allocations.select_related('lot')}
    if (not allocations or sum(row.amount for row in allocations.values()) != intent.amount
            or any(row.lot.account_id != intent.buyer_id or row.kind not in ('PREPAID', 'GIFT', 'COMPENSATION')
                   or row.amount <= 0 for row in allocations.values())
            or sum(row.amount for row in allocations.values() if row.kind == 'PREPAID') != intent.principal
            or sum(row.amount for row in allocations.values() if row.kind == 'GIFT') != intent.gift
            or sum(row.amount for row in allocations.values() if row.kind == 'COMPENSATION') != intent.compensation):
        raise ValidationError('加购原付款来源待核对')
    totals = {pk: 0 for pk in allocations}
    baseline = 0
    for refund in payment.refund_records.filter(created_at__lte=consumption.confirmed_at).select_related(
            'proposal', 'buyer_transaction').order_by('created_at', 'pk'):
        returned, plan = refund.buyer_transaction, refund.proposal.source_plan
        if (refund.order_id != intent.order_id or refund.proposal.payment_id != payment.pk
                or refund.proposal.status != 'ACCEPTED' or returned.wallet_id != debit.wallet_id
                or returned.tx_type != 'REFUND' or returned.status != 'SUCCESS' or returned.amount != refund.amount
                or returned.order_id is not None
                or returned.balance_after != returned.balance_before + returned.amount
                or not isinstance(plan, list) or not plan):
            raise ValidationError('加购实销前退款凭证待核对')
        seen, amount = set(), 0
        for part in plan:
            if not isinstance(part, dict):
                raise ValidationError('加购实销前退款来源待核对')
            pk, units, kind = part.get('allocation_id'), part.get('amount_raw'), part.get('kind')
            if (type(pk) is not int or pk not in allocations or pk in seen or type(units) is not int or units <= 0
                    or kind != allocations[pk].kind):
                raise ValidationError('加购实销前退款来源与原付款不一致')
            seen.add(pk)
            totals[pk] += units
            amount += units
            if kind == 'PREPAID':
                baseline += units
        if amount != refund.amount:
            raise ValidationError('加购实销前退款金额不一致')
    points_baseline, expected = points.get('refunded_principal_at_decision'), points.get('points_at_decision_hundredths')
    rate, multiplier = terms.get('points_rate'), terms.get('birthday_multiplier')
    if (any(totals[pk] > row.amount for pk, row in allocations.items()) or points_baseline != baseline
            or type(rate) is not int or not 0 <= rate <= 1000 or type(multiplier) is not int or not 1 <= multiplier <= 10
            or type(expected) is not int
            or (not points['applicable'] and expected != 0)
            or (points['applicable'] and expected != (payment.principal - baseline) * rate * multiplier // 100)):
        raise ValidationError('加购原积分或退款基线不一致')
    return vip, points, baseline


def resolve_locked(consumption_id, *, request_id, actor_id, vip_outcome, points_outcome, reason,
                   _batch_verify=False):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购权益核对须在门店事务内执行')
    from orders.models import Order, OrderAddonConsumption
    initial = OrderAddonConsumption.objects.select_related('payment__intent').filter(pk=consumption_id).first()
    if not initial:
        raise ValidationError('待核对加购不存在')
    order = Order.objects.select_for_update().get(pk=initial.payment.intent.order_id)
    if 'ALLOW' in (vip_outcome, points_outcome):
        # Lock the order and money before the individual review.  Third-round
        # transferred orders must prove their real final settlement, including
        # every add-on, before a same-cycle grant or recalculation.
        from club_accounts.models import ClubAccount
        from orders.models import OrderOriginalShareTransfer, OrderSegmentSettlementManifest
        from orders.points_service_payment import needs_integrated_processing
        observed_cycle = ClubAccount.objects.filter(pk=initial.account_id).values_list('business_cycle', flat=True).first()
        if (observed_cycle is not None and observed_cycle <= initial.business_cycle
                and needs_integrated_processing(order) and order.refund_records.count() >= 3
                and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
            if not order.settled_at:
                raise ValidationError('第三轮退款订单尚未最终结算')
            from orders.points_transfer_refund_access import after_transfer, verification_scope
            from orders.segment_settlement import verify_finalized
            from orders.settlement import SettlementError
            with after_transfer(order.pk, phase='settlement') as locked:
                with verification_scope(locked):
                    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(
                        order=locked, finalized_at=locked.settled_at).first()
                    if not manifest:
                        raise ValidationError('第三轮退款最终结算清单缺失')
                    try:
                        verify_finalized(locked, manifest)
                    except SettlementError as exc:
                        raise ValidationError('第三轮退款最终结算凭证待核对') from exc
    consumption = OrderAddonConsumption.objects.select_for_update().select_related('payment__intent__order').get(pk=consumption_id)
    review = LicenseAddonBenefitsReview.objects.select_for_update().filter(consumption=consumption).first()
    vip_outcome, points_outcome = vip_outcome or '', points_outcome or ''
    if review:
        if (review.request_id != request_id or review.vip_outcome != vip_outcome
                or review.points_outcome != points_outcome or review.reason != reason):
            raise ValidationError('该加购已核对，请查看原处理结果')
        _verify(review)
        if review.vip_recalculation_pending:
            if review.vip_outcome != 'ALLOW' or not review.vip_applied:
                raise ValidationError('加购会员审核与重算状态不一致')
            # Recheck the current net, including refunds completed since the
            # original review, before recalculation can issue any benefit.
            _verify_payment_and_refund_history(consumption)
            vip, points, _, _ = _facts(consumption)
            if (not vip['applicable'] or vip['outcome'] != 'UNKNOWN'
                    or review.vip_outcome != 'ALLOW'):
                raise ValidationError('原加购会员授权决定待核对')
            from wallet.services import get_wallet
            if not get_wallet(account_id=consumption.account_id, for_update=True, create=False):
                raise ValidationError('加购老板钱包不存在')
            from club_accounts.models import ClubAccount
            account = ClubAccount.objects.select_for_update().get(pk=consumption.account_id)
            if account.account_type != 'BOSS' or account.business_cycle != consumption.business_cycle:
                raise ValidationError('原老板业务周期已变化，请先核对')
            if _batch_verify:
                return review
            from .license_vip_pending import is_only_pending
            if not is_only_pending(account.pk, account.business_cycle, kind='addon', review_id=review.pk):
                raise ValidationError('同周期存在多笔待核对会员成长，不能单笔重算')
            from .services import update_vip
            update_vip(account)
            review.refresh_from_db()
        return review
    # A pure denial writes no benefit. Later refund evidence or a closed
    # account cycle must not prevent recording a proven historical refusal.
    decisions = consumption.license_decisions
    vip = decisions.get('vip') if isinstance(decisions, dict) else None
    points = decisions.get('points') if isinstance(decisions, dict) else None
    if isinstance(vip, dict) and isinstance(points, dict):
        needs_vip = vip.get('applicable') is True and vip.get('outcome') == 'UNKNOWN'
        needs_points = points.get('applicable') is True and points.get('outcome') == 'UNKNOWN'
        pure_denial = ((needs_vip or needs_points)
            and (vip_outcome == 'DENY' if needs_vip else not vip_outcome)
            and (points_outcome == 'DENY' if needs_points else not points_outcome))
        if pure_denial:
            _, _, baseline = _denial_baseline(consumption)
            if LicenseAddonBenefitsReview.objects.filter(request_id=request_id).exists():
                raise ValidationError('请求编号已用于其他加购权益核对')
            return LicenseAddonBenefitsReview.objects.create(consumption=consumption, request_id=request_id,
                platform_actor_id=actor_id, vip_outcome=vip_outcome, points_outcome=points_outcome, reason=reason,
                vip_applied=False, vip_recalculation_pending=False, awarded_points=0,
                refunded_principal_at_award=baseline, skip_reason='DENIED')
        valid_outcomes = ((needs_vip or needs_points)
            and (vip_outcome in ('ALLOW', 'DENY') if needs_vip else not vip_outcome)
            and (points_outcome in ('ALLOW', 'DENY') if needs_points else not points_outcome))
        if valid_outcomes:
            from club_accounts.models import ClubAccount
            observed_cycle = ClubAccount.objects.filter(pk=consumption.account_id).values_list('business_cycle', flat=True).first()
            if observed_cycle is not None and observed_cycle > consumption.business_cycle:
                # Recheck under the same wallet/account lock order used by
                # retirement and the normal benefit-award path. The first
                # read is only a hint; it never authorizes the conclusion.
                from wallet.services import get_wallet
                if not get_wallet(account_id=consumption.account_id, for_update=True, create=False):
                    raise ValidationError('加购老板钱包不存在')
                account = ClubAccount.objects.select_for_update().get(pk=consumption.account_id)
                if account.business_cycle > consumption.business_cycle:
                    from .models import RetirementAudit
                    if (account.account_type != 'BOSS' or not RetirementAudit.objects.filter(
                            account_id=account.pk, business_cycle=consumption.business_cycle).exists()):
                        raise ValidationError('原业务周期退店事实缺失，不能零额结案')
                    _, _, baseline = _denial_baseline(consumption)
                    if LicenseAddonBenefitsReview.objects.filter(request_id=request_id).exists():
                        raise ValidationError('请求编号已用于其他加购权益核对')
                    return LicenseAddonBenefitsReview.objects.create(consumption=consumption, request_id=request_id,
                        platform_actor_id=actor_id, vip_outcome=vip_outcome, points_outcome=points_outcome, reason=reason,
                        vip_applied=False, vip_recalculation_pending=False, awarded_points=0,
                        refunded_principal_at_award=baseline, skip_reason='CYCLE_CLOSED')
    _verify_payment_and_refund_history(consumption)
    vip, points, principal, factor = _facts(consumption)
    needs_vip = vip['applicable'] and vip['outcome'] == 'UNKNOWN'
    needs_points = points['applicable'] and points['outcome'] == 'UNKNOWN'
    if (not (needs_vip or needs_points) or (vip_outcome not in ('ALLOW', 'DENY') if needs_vip else bool(vip_outcome))
            or (points_outcome not in ('ALLOW', 'DENY') if needs_points else bool(points_outcome))):
        raise ValidationError('请分别核对所有未知的加购授权决定')
    if LicenseAddonBenefitsReview.objects.filter(request_id=request_id).exists():
        raise ValidationError('请求编号已用于其他加购权益核对')
    from wallet.services import get_wallet
    if not get_wallet(account_id=consumption.account_id, for_update=True, create=False):
        raise ValidationError('加购老板钱包不存在')
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.select_for_update().get(pk=consumption.account_id)
    if account.business_cycle < consumption.business_cycle:
        raise ValidationError('账号业务周期早于原加购，不能推断退店状态')
    cycle_closed = account.business_cycle > consumption.business_cycle
    vip_allowed = vip['applicable'] and (vip_outcome if needs_vip else vip['outcome']) == 'ALLOW'
    points_allowed = points['applicable'] and (points_outcome if needs_points else points['outcome']) == 'ALLOW'
    fully_refunded = consumption.refunded_units == consumption.paid_units
    vip_applied = bool(not cycle_closed and not fully_refunded and needs_vip and vip_allowed and not consumption.vip_eligible)
    if consumption.awarded_points or consumption.points_ledger_id:
        raise ValidationError('加购已有消费积分流水，请先核对')
    amount = ((consumption.principal_units - principal) * factor // 100
              if not cycle_closed and vip_allowed and points_allowed else 0)
    ledger = None
    if amount:
        from .services import change_points, locked_profile
        ledger = change_points(locked_profile(account), amount, f'addon-license-review:{consumption.pk}:award',
            'ADDON_LICENSE_AWARD', '加购授权故障后的消费积分人工核对补发', order=order)
    review = LicenseAddonBenefitsReview.objects.create(consumption=consumption, request_id=request_id,
        platform_actor_id=actor_id, vip_outcome=vip_outcome, points_outcome=points_outcome, reason=reason,
        vip_applied=vip_applied, vip_recalculation_pending=vip_applied, awarded_points=amount,
        refunded_principal_at_award=principal, points_ledger=ledger,
        skip_reason='CYCLE_CLOSED' if cycle_closed else 'FULLY_REFUNDED' if fully_refunded else '')
    if vip_applied:
        from .license_vip_pending import is_only_pending
        if is_only_pending(account.pk, account.business_cycle, kind='addon', review_id=review.pk):
            from .services import update_vip
            update_vip(account)
        review.refresh_from_db()
    return review


def reverse_locked(consumption, refund):
    review = LicenseAddonBenefitsReview.objects.select_for_update().filter(consumption=consumption).select_related(
        'consumption__payment__intent', 'points_ledger').first()
    if not review:
        return False
    _verify(review)
    if not review.awarded_points or consumption.account.business_cycle != consumption.business_cycle:
        return review.vip_applied
    _, _, principal, factor = _facts(consumption)
    if principal < review.refunded_principal_at_award:
        raise ValidationError('加购补发后的本金退款记录异常')
    remaining = (consumption.principal_units - principal) * factor // 100
    target = max(0, review.awarded_points - remaining)
    previous = -(PointLedger.objects.filter(event_key__startswith=f'addon-license-review:{consumption.pk}:refund:',
        kind='ADDON_LICENSE_REFUND').aggregate(total=Sum('amount'))['total'] or 0)
    delta = target - previous
    if delta > 0:
        from .services import change_points, locked_profile
        change_points(locked_profile(consumption.account), -delta, f'addon-license-review:{consumption.pk}:refund:{refund.pk}',
            'ADDON_LICENSE_REFUND', '加购补发积分按退款本金冲回', order=refund.order, allow_debt=True)
    return review.vip_applied
