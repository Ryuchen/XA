"""Read-only, isolated preflight for the other provider's later refund.

This is deliberately not an authorization or a posting operation. The first
provider may already have received income; a later implementation must keep
that transaction and reconcile the new funding reservation atomically.
"""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from engagement.models import OrderFundingAllocation
from stores.db import transaction
from .models import (AfterSale, Order, OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsRefundSettlement,
                     OrderProvider, OrderSegmentFinancialSnapshot, PointsNegotiationPosting)
from .points_checkout_test import require_isolated_checkout
from .points_funding_evidence import verify_order_funding
from .points_refunded_funding import plan_refunded_order
from .points_negotiation_preview import funding_after_refund
from .points_service_refund import _plan as payment_refund_plan
from .refund_math import RefundCalculationError, provider_refund_burden


def preview_followup(case_id, *, refund_equivalent_units):
    from .points_business_access import permits
    formal = any(permits(phase='refund', kind=kind) for kind in ('service', 'addon'))
    if not formal:
        require_isolated_checkout()
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING', False))
            or not transaction.get_connection().in_atomic_block):
        raise ValidationError('后续协商退款核对尚未开放')
    if type(case_id) is not int or case_id <= 0 or type(refund_equivalent_units) is not int or refund_equivalent_units <= 0:
        raise ValidationError('售后或退款金额无效')
    order_id = AfterSale.objects.filter(pk=case_id).values_list('order_id', flat=True).first()
    if not order_id:
        raise ValidationError('售后不存在')
    order = Order.objects.select_for_update().get(pk=order_id)
    if formal and not any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon')):
        raise ValidationError('后续退款与当前订单资金上下文不一致')
    case = AfterSale.objects.select_for_update().get(pk=case_id, order=order)
    from .financial_lock import lock_order_money
    lock_order_money()
    from .points_receipt_usage import prelock_followup_receipts, followup_eligible
    prelock_followup_receipts(order)
    in_service = order.status == Order.Status.IN_SERVICE
    if order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED) or case.status != 'OPEN' or case.account_id != order.customer_account_id:
        raise ValidationError('订单或售后状态不适合后续核对')
    from .addon_settlement import has_paid_addons
    has_addons = has_paid_addons(order)
    scope = case.affected_provider_account_ids
    if not isinstance(scope, list) or len(scope) != 1 or type(scope[0]) is not int or scope[0] <= 0:
        raise ValidationError('本次后续售后须明确一位陪玩')
    if in_service:
        from django.utils import timezone
        from .negotiation_termination import continuing_team
        prior_continuation = continuing_team(order, verify=True)
        if (not prior_continuation or not order.in_service_at or not case.created_at
                or not prior_continuation.termination_processed_at <= case.created_at <= timezone.now()):
            raise ValidationError('继续履约的售后时间或原协商凭证待核对')
    else:
        from engagement.confirmation import aftersale_deadline
        deadline = aftersale_deadline(order)
        if not deadline or not case.created_at or case.created_at >= deadline:
            raise ValidationError('售后申请超出期限')
    from .negotiation_aftersale_scope import closed_provider_accounts
    if scope[0] in closed_provider_accounts(order):
        raise ValidationError('已确认的服务范围不能重开')
    try:
        current = plan_refunded_order(order.pk)
        funding = verify_order_funding(order.pk, _main_refund_usage=followup_eligible(order))
        receipt, assets, sources = payment_refund_plan(order, None, refund_equivalent_units)
    except (ValueError, RefundCalculationError) as exc:
        raise ValidationError('原退款及资金依据待核对') from exc
    posting = PointsNegotiationPosting.objects.get(points_refund__redemption=receipt)
    settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=posting).first()
    if settlement and settlement.finalized_at:
        raise ValidationError('原订单已完成整单结算')
    people = list(settlement.persons.select_related('report')) if settlement else []
    if len(people) > 1 or (people and (not funding.fully_funded
            or people[0].segment.provider_account_id == scope[0])):
        raise ValidationError('原陪玩结算或本次受影响范围待核对')
    if in_service and (people or settlement):
        raise ValidationError('服务中的后续协商已有收益结算，须先核对')
    from .points_refunded_settlement import verified_person_display
    def person_display(report):
        if formal:
            from .points_business_access import service_order
            with service_order(order.pk, phase='settlement', addon=order.addon_intents.filter(payment__isnull=False).exists()):
                return verified_person_display(report)
        return verified_person_display(report)
    if people and person_display(people[0].report)[0] != 'SETTLED':
        raise ValidationError('原陪玩结算凭证待核对')
    from .points_service_participation import service_snapshots
    snapshots = service_snapshots(order)
    if len(snapshots) != 2 or len({row.segment.provider_account_id for row in snapshots}) != 2:
        raise ValidationError('双陪履约份额待核对')
    target = next((row for row in snapshots if row.segment.provider_account_id == scope[0]), None)
    assignment = OrderProvider.objects.filter(order=order, provider_account_id=scope[0]).first()
    if not target or not assignment or assignment.settled_at or not target.segment.started_at:
        raise ValidationError('本次陪玩履约或结算状态待核对')
    if in_service:
        selected = case.affected_segment_ids
        if (assignment.released_at or target.segment.released_at
                or (selected is not None and selected != [target.segment_id])):
            raise ValidationError('继续履约陪玩或受影响履约段待核对')
    from wallet.models import ProviderReport
    report = ProviderReport.objects.filter(order=order, provider_account_id=scope[0],
                                           provider_id=assignment.provider_id).first()
    if not in_service and (not report or person_display(report)[0] != 'WAITING'):
        raise ValidationError('本次陪玩收益状态待核对')
    if not people:
        from wallet.models import Transaction
        assignments = list(OrderProvider.objects.select_for_update().filter(order=order,
            provider_account_id__in=[row.segment.provider_account_id for row in snapshots]))
        if (order.settled_at or len(assignments) != 2 or any(item.settled_at for item in assignments)
                or order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                    remark=f'订单收入：{order.order_no}').exists()
                or order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                    remark=f'推荐分佣：{order.order_no}').exists()
                or order.transactions.filter(tx_type=Transaction.TxType.SHOP_INCOME,
                                             status=Transaction.Status.SUCCESS).exists()):
            raise ValidationError('未结算订单存在待核对的收益流水')
    try:
        burden = provider_refund_burden(refund_equivalent_units=refund_equivalent_units,
                                        original_price_weights={scope[0]: target.segment.original_share})
    except RefundCalculationError as exc:
        raise ValidationError(str(exc)) from exc
    amount = burden.amounts[scope[0]]
    from .addon_main_deduction import deductions_at
    addon_deductions = deductions_at(order) if has_addons else {}
    available_income = target.income_at_entry - addon_deductions.get(scope[0], 0)
    if available_income < 0:
        raise ValidationError('加购退款承担超过陪玩主单收益')
    pending = min(amount, available_income)
    previous = posting.points_refund
    paid = {kind: sum(row.amount for row in OrderFundingAllocation.objects.filter(order=order, kind=kind))
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    shares = list(posting.shares.all())
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    after = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=previous.prepaid_units + sources.prepaid_units,
        returned_gift_units=previous.gift_units + sources.gift_units,
        returned_compensation_units=previous.compensation_units + sources.compensation_units,
        provider_income_units=sum(row.income_at_entry for row in snapshots),
        pending_deduction_units=sum(row.pending_deduction for row in shares) + pending,
        provider_external_units=sum(row.wallet_deduction + row.deposit_deduction for row in shares) + amount - pending,
        inviter_rate=rate, allocated_units=funding.allocated,
        rounding_reserved_units=posting.rounding_to_store_units + burden.rounding_to_store_units)
    addon_snapshot = None
    if has_addons:
        from django.utils import timezone
        from .addon_funding_history import combined_at
        from .points_funding_plan import PointsFundingPlanError
        from .refunded_addon_funding import combine_refunded_history
        pending_by_account = {row.account_id: row.pending_deduction for row in shares}
        pending_by_account[scope[0]] = pending_by_account.get(scope[0], 0) + pending
        retained = {row.segment.provider_account_id: row.income_at_entry
                    - pending_by_account.get(row.segment.provider_account_id, 0) for row in snapshots}
        segment_shares = [(row.segment_id, row.segment.provider_account_id, row.incoming_basis, 0,
                           retained[row.segment.provider_account_id]) for row in snapshots]
        try:
            history = combined_at(order, after, timezone.now(), segment_shares=segment_shares)
            after = combine_refunded_history(after, history, main_retained_by_account=retained)
            addon_snapshot = {'addon_history': history['addon_history'], 'main_deductions': history['main_deductions']}
        except PointsFundingPlanError as exc:
            raise ValidationError('后续退款与加购资金待核对') from exc
    from .points_funding_allocation import PointsFundingAllocationError
    from .points_funding_revision import plan_funding_revision, plan_unpaid_funding_revision
    from django.db.models import Q
    from .models import OrderAddonFundingReleaseRun
    team = OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
        points_handover_snapshot__kind='SAME_TEAM_V1').exists()
    owner = (Q(redemption__order=order) | Q(service_order=order)) if team else Q(redemption__order=order)
    allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(owner).order_by('pk'))
    from .addon_funding_release import addon_release_totals
    prior_addon = addon_release_totals(allocations)
    revision_rows = [(row.pk, row.receipt_id, row.amount,
                      sum(OrderPointsFundingRelease.objects.filter(allocation=row).values_list('amount', flat=True))
                      + prior_addon[row.pk])
                     for row in allocations]
    reservation = (list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
                   if settlement else None)
    try:
        if people:
            paid_addons = sum(settlement.addon_settlements.filter(segment_id=people[0].segment_id)
                              .values_list('amount', flat=True)) if has_addons else 0
            revision = plan_funding_revision(allocations=revision_rows, original_reservation=reservation,
                previous_required=current.required_units, paid_provider_units=people[0].amount + paid_addons, projected=after)
        else:
            revision = plan_unpaid_funding_revision(allocations=revision_rows, original_reservation=reservation,
                previous_required=current.required_units, projected=after)
    except PointsFundingAllocationError as exc:
        raise ValidationError('后续退款的补资预留版本待核对') from exc
    term = {'account_id': scope[0], 'original_income_units': available_income,
            'burden_units': amount, 'retained_units': available_income - pending,
            'additional_units': amount - pending}
    return {'order_id': order.pk, 'case_id': case.pk, 'account_id': scope[0],
            'redemption_id': receipt.pk, 'prior_refund_id': previous.pk,
            'refund_equivalent_units': refund_equivalent_units, 'refund_coin_units': assets.coin_units,
            'coin_units': assets.coin_units, 'points_hundredths': assets.point_hundredths,
            'returned_prepaid_units': sources.prepaid_units, 'returned_gift_units': sources.gift_units,
            'returned_compensation_units': sources.compensation_units, 'is_final': assets.is_final,
            'provider_terms': [term],
            'refund_points_hundredths': assets.point_hundredths, 'original_income_units': available_income,
            'burden_units': amount, 'rounding_to_store_units': burden.rounding_to_store_units,
            'retained_units': available_income - pending,
            'additional_provider_units': amount - pending, 'settled_other_provider_units': people[0].amount if people else 0,
            'prior_paid': bool(people),
            'has_addons': has_addons, 'addon_snapshot': addon_snapshot,
            'current_required_units': current.required_units, 'conditional_funding': after,
            'conditional_funding_revision': revision, 'execution_available': False}
