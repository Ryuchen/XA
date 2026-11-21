"""Verified, read-only preview of a first points-order refund.

This is not an authorization, agreement, release of allocated funds or refund.
The caller must hold the tenant transaction until any eventual posting, and
must revalidate this preview after the parties have accepted the proposal.
"""
from dataclasses import dataclass
import hashlib
import json

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Transaction

from .models import (AfterSale, Order, OrderPointsFundingAllocation, OrderPointsSettlement, OrderProvider,
                     OrderSegmentFinancialSnapshot, PointsNegotiationTerms)
from .points_checkout_test import require_isolated_checkout, settlement_enabled
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_evidence import verify_order_funding
from .points_funding_plan import PointsFundingPlanError, plan_verified_order
from .points_service_refund import _plan as payment_refund_plan
from .refund_math import RefundCalculationError, provider_refund_burden


@dataclass(frozen=True)
class RefundedFunding:
    net_coin_units: int
    net_principal_units: int
    net_gift_units: int
    net_compensation_units: int
    provider_income_units: int
    planned_provider_external_units: int
    inviter_base_units: int
    inviter_income_units: int
    store_income_units: int
    required_units: int
    allocated_units: int
    release_units: int
    remaining_units: int
    rounding_reserved_units: int = 0

    @property
    def additional_store_funding_units(self):
        return self.required_units


def agreement_enabled():
    from .points_transfer_refund_access import permits
    from .points_business_access import permits as service_permits
    if permits(phase='refund') or service_permits(phase='refund', kind='service') or service_permits(phase='refund', kind='addon'):
        return True
    return bool(getattr(settings, 'SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING', False)
                and getattr(settings, 'SERVICE_POINTS_NEGOTIATION_TESTING', False) and settlement_enabled())


def asset_terms(calculated):
    values = {'redemption_id': calculated['redemption_id'], 'coin_units': calculated['coin_units'],
            'points_hundredths': calculated['points_hundredths'],
            'prepaid_units': calculated['returned_prepaid_units'], 'gift_units': calculated['returned_gift_units'],
            'compensation_units': calculated['returned_compensation_units'], 'is_final': calculated['is_final']}
    if calculated.get('prior_refund_id'):
        values['prior_refund_id'] = calculated['prior_refund_id']
    if calculated.get('segment_pending_plan') is not None:
        values['segment_pending_plan'] = calculated['segment_pending_plan']
    if calculated.get('addon_pending_plan') is not None:
        values['addon_pending_plan'] = calculated['addon_pending_plan']
    if calculated.get('prior_round_id') is not None:
        keys = ('prior_round_id', 'prior_round_hash', 'previous_addon_id', 'previous_addon_hash',
                'previous_transfer_id', 'previous_transfer_hash', 'prior_funding', 'allocations')
        if any(key not in calculated for key in keys):
            raise ValidationError('多轮退款方案缺少资金前版')
        values['round_context'] = {key: calculated[key] for key in keys}
        if calculated.get('provider_recoveries') is not None:
            values['round_context']['provider_recoveries'] = calculated['provider_recoveries']
        if calculated.get('paid_snapshot') is not None:
            values['round_context']['paid_snapshot'] = calculated['paid_snapshot']
        if calculated.get('pending_handovers'):
            values['round_context']['pending_handovers'] = calculated['pending_handovers']
    return values


def asset_digest(proposal, values):
    payload = {'proposal_id': proposal.pk, 'case_id': proposal.case_id, 'terms_hash': proposal.terms_hash,
               'refund_equivalent_units': proposal.refund_units, 'assets': values}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verified_asset_terms(proposal):
    try:
        row = PointsNegotiationTerms.objects.select_related('redemption', 'prior_refund__refund').filter(proposal=proposal).first()
    except DatabaseError as exc:
        raise ValidationError('积分协商退款凭证暂不可用，请联系管理员核对') from exc
    if not row:
        raise ValidationError('积分协商退款构成凭证缺失')
    values = {'redemption_id': row.redemption_id, 'coin_units': row.coin_units,
              'points_hundredths': row.points_hundredths, 'prepaid_units': row.prepaid_units,
              'gift_units': row.gift_units, 'compensation_units': row.compensation_units, 'is_final': row.is_final}
    if row.segment_pending_plan is not None:
        values['segment_pending_plan'] = row.segment_pending_plan
    if row.addon_pending_plan is not None:
        values['addon_pending_plan'] = row.addon_pending_plan
    if row.round_context is not None:
        values['round_context'] = row.round_context
    prior_equivalent = 0
    if row.prior_refund_id:
        prior = row.prior_refund
        if (prior.redemption_id != row.redemption_id or prior.refund.order_id != proposal.case.order_id
                or prior.created_at > row.created_at or not 0 < prior.equivalent_units < row.redemption.deal_units):
            raise ValidationError('积分后续协商历史退款凭证不一致')
        from .models import OrderPointsRefund
        from .points_service_refund import verify_refund_history_locked
        from .refund_math import payment_refund, wallet_source_refund
        from engagement.models import OrderFundingAllocation
        with transaction.atomic():
            verified = verify_refund_history_locked(prior.refund_id)
            history = list(OrderPointsRefund.objects.filter(redemption_id=row.redemption_id,
                created_at__lt=row.created_at).order_by('refund_id'))
            if (verified.pk != prior.pk or not history or history[-1].pk != prior.pk
                    or any(item.is_final for item in history)
                    or OrderPointsRefund.objects.filter(redemption_id=row.redemption_id,
                        created_at=row.created_at).exists()):
                raise ValidationError('积分后续协商历史退款顺序不一致')
            totals = {field: sum(getattr(item, field) for item in history) for field in (
                'equivalent_units', 'coin_units', 'points_hundredths', 'prepaid_units',
                'gift_units', 'compensation_units')}
            paid = {kind: sum(item.amount for item in OrderFundingAllocation.objects.filter(
                order_id=proposal.case.order_id, kind=kind)) for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
            try:
                expected = payment_refund(paid_coin_units=row.redemption.paid_units,
                    paid_point_hundredths=row.redemption.points_hundredths,
                    point_value_units=row.redemption.discount_units, refund_equivalent_units=proposal.refund_units,
                    previously_refunded_equivalent_units=totals['equivalent_units'],
                    previously_refunded_coin_units=totals['coin_units'],
                    previously_refunded_point_hundredths=totals['points_hundredths'])
                sources = wallet_source_refund(coin_units=expected.coin_units,
                    paid_prepaid_units=paid['PREPAID'], paid_gift_units=paid['GIFT'],
                    paid_compensation_units=paid['COMPENSATION'], refunded_prepaid_units=totals['prepaid_units'],
                    refunded_gift_units=totals['gift_units'], refunded_compensation_units=totals['compensation_units'])
            except RefundCalculationError as exc:
                raise ValidationError('积分后续协商退款构成待核对') from exc
            if (row.coin_units, row.points_hundredths, row.is_final, row.prepaid_units, row.gift_units,
                    row.compensation_units) != (expected.coin_units, expected.point_hundredths, expected.is_final,
                                                sources.prepaid_units, sources.gift_units, sources.compensation_units):
                raise ValidationError('积分后续协商退款构成不一致')
        prior_equivalent = totals['equivalent_units']
        values['prior_refund_id'] = prior.pk
    if (row.redemption.order_id != proposal.case.order_id or row.coin_units > row.redemption.paid_units
            or row.points_hundredths > row.redemption.points_hundredths
            or row.coin_units != row.prepaid_units + row.gift_units + row.compensation_units
            or any(value % 10 for value in (row.coin_units, row.prepaid_units, row.gift_units, row.compensation_units))
            or not 0 < proposal.refund_units <= row.redemption.deal_units - prior_equivalent
            or row.is_final != (prior_equivalent + proposal.refund_units == row.redemption.deal_units)
            or row.evidence_hash != asset_digest(proposal, values)):
        raise ValidationError('积分协商退款构成凭证不一致')
    return values


def funding_after_refund(*, principal_units, gift_units, compensation_units,
                         returned_prepaid_units, returned_gift_units, returned_compensation_units,
                         provider_income_units, pending_deduction_units, provider_external_units,
                         inviter_rate, allocated_units, rounding_reserved_units=0):
    """Compute cash needs assuming the separately planned recovery succeeds.

    The external provider amount is not evidence of a balance, recovery or
    receipt. A later operation must verify and collect it transactionally.
    Confirmed rounding reserved for the store is an obligation before any
    excess is released; it is included in store income only once.
    """
    values = (principal_units, gift_units, compensation_units, returned_prepaid_units,
              returned_gift_units, returned_compensation_units, provider_income_units,
              pending_deduction_units, provider_external_units, inviter_rate, allocated_units,
              rounding_reserved_units)
    if any(type(value) is not int or value < 0 for value in values) or inviter_rate > 100:
        raise ValidationError('退款后资金依据无效')
    if any(value % 10 for value in (principal_units, gift_units, compensation_units,
                                    returned_prepaid_units, returned_gift_units, returned_compensation_units)):
        raise ValidationError('老板支付和退款须为整数店币')
    if (returned_prepaid_units > principal_units or returned_gift_units > gift_units
            or returned_compensation_units > compensation_units
            or pending_deduction_units > provider_income_units):
        raise ValidationError('退款或收益扣减超过原始金额')
    principal = principal_units - returned_prepaid_units
    gift = gift_units - returned_gift_units
    compensation = compensation_units - returned_compensation_units
    net_coin = principal + gift + compensation
    income = provider_income_units - pending_deduction_units
    inviter_base = net_coin
    inviter = inviter_base * inviter_rate // 100
    cash_margin = principal + provider_external_units - income - inviter - rounding_reserved_units
    required = max(-cash_margin, 0)
    store = max(cash_margin, 0) + rounding_reserved_units
    release, remaining = max(allocated_units - required, 0), max(required - allocated_units, 0)
    if principal + provider_external_units + required != income + inviter + store:
        raise ValidationError('退款后资金不守恒')
    return RefundedFunding(net_coin, principal, gift, compensation, income, provider_external_units,
                           inviter_base, inviter, store, required, allocated_units, release, remaining,
                           rounding_reserved_units)


def combined_funding_sufficient(calculation):
    combined = calculation.get('conditional_combined_funding')
    return combined is not None and combined.remaining_units == 0


def preview_locked(case_id, *, refund_equivalent_units):
    """Reconcile a first refund with actual payment, terms and reserved funds.

    Untransferred in-service or timely after-sale orders with no payouts are
    handled here; later refund effects require their own transactional path.
    """
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('协商资金核对须在门店事务内执行')
    if type(case_id) is not int or case_id <= 0:
        raise ValidationError('售后记录无效')
    case_row = AfterSale.objects.filter(pk=case_id).values('order_id').first()
    if not case_row:
        raise ValidationError('售后记录不存在')
    try:
        require_isolated_checkout()
    except ValidationError:
        from .points_negotiation_production import require_order_locked
        require_order_locked(case_row['order_id'], phase='before', refund_equivalent_units=refund_equivalent_units)
    else:
        if not getattr(settings, 'SERVICE_POINTS_NEGOTIATION_TESTING', False) or not settlement_enabled():
            raise ValidationError('积分订单协商预览尚未开放')
    order = Order.objects.select_for_update().get(pk=case_row['order_id'])
    case = AfterSale.objects.select_for_update().get(pk=case_id, order=order)
    scope = case.affected_provider_account_ids
    if (case.status != 'OPEN' or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or case.account_id != order.customer_account_id or not order.in_service_at or not case.created_at
            or case.created_at < order.in_service_at or case.created_at > timezone.now()
            or not isinstance(scope, list) or not scope or any(type(pk) is not int or pk <= 0 for pk in scope)
            or len(set(scope)) != len(scope) or order.customer_account_id in scope
            or order.is_gift_snapshot or order.source_order_id
            or Order.objects.filter(source_order_id=order.pk).exists()):
        raise ValidationError('订单或售后范围尚待核对')
    if order.status == Order.Status.COMPLETED:
        from engagement.confirmation import aftersale_deadline
        deadline = aftersale_deadline(order)
        if not deadline or case.created_at >= deadline:
            raise ValidationError('售后申请时间超出订单售后期')
    if (order.customer_id is not None and not LegacyAccountMap.objects.filter(
            account_id=order.customer_account_id, legacy_user_id=order.customer_id).exists()):
        raise ValidationError('老板账号归属待核对')
    from .direct_payment_models import DirectPaymentIntent
    from .models import OrderPlatformSubsidy
    settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
    paid_people = (list(settlement.persons.select_for_update().select_related(
        'segment__attribution', 'report', 'income_transaction__wallet').order_by('pk')) if settlement else [])
    from .models import OrderOriginalShareTransfer
    if paid_people and (len(paid_people) != 1 or order.required_provider_count != 2
            or order.status != Order.Status.COMPLETED or order.settled_at
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or paid_people[0].segment.provider_account_id in scope
            or not paid_people[0].settled_at or case.created_at >= paid_people[0].settled_at):
        raise ValidationError('已付人员与本次售后范围待核对')
    paid_ids = {person.income_transaction_id for person in paid_people if person.income_transaction_id}
    if paid_people:
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .points_first_paid_source import verify_original_addons
        try:
            _, addon_rows = verify_original_addons(order, settlement, paid_people[0], timezone.now())
        except DjangoValidationError as exc:
            raise ValidationError('首退前已付加购或收益流水待核对') from exc
        paid_ids.update(row.income_transaction_id for row in addon_rows if row.income_transaction_id)
    if (DirectPaymentIntent.objects.filter(order=order).exists() or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or order.transactions.filter(status=Transaction.Status.SUCCESS,
                tx_type__in=[Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME, Transaction.TxType.GIFT])
                .exclude(pk__in=paid_ids).exists()):
        raise ValidationError('订单已有补贴、收益或结算凭证，请先核对')
    try:
        original = plan_verified_order(order.pk)
        from .points_receipt_usage import eligible as receipt_usage_eligible
        funding = verify_order_funding(order.pk, _main_refund_usage=receipt_usage_eligible(order))
        receipt, assets, sources = payment_refund_plan(order, None, refund_equivalent_units)
    except (PointsFundingPlanError, RefundCalculationError) as exc:
        raise ValidationError(str(exc)) from exc
    allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(
        Q(redemption__order=order) | Q(service_order=order)).order_by('pk'))
    from .addon_funding_release import addon_release_totals
    prior_addon = addon_release_totals(allocations)
    if sum(row.amount - prior_addon[row.pk] for row in allocations) != funding.allocated:
        raise ValidationError('订单补资分配已变化，请重新核对')
    if settlement:
        from .addon_settlement import has_paid_addons, settlement_facts
        from .addon_funding import include_paid_addons
        try:
            combined = include_paid_addons(order, original) if has_paid_addons(order) else None
        except PointsFundingPlanError as exc:
            raise ValidationError('首退前加购资金计划待核对') from exc
        facts = settlement_facts(original, combined)
        recorded = (settlement.paid_units, settlement.principal_units, settlement.recovery_units, settlement.gift_units,
                    settlement.compensation_units, settlement.funding_units, settlement.provider_units,
                    settlement.inviter_units, settlement.store_units)
        uses = list(settlement.funding_uses.select_for_update().order_by('allocation_id')
                    .values_list('allocation_id', 'amount'))
        if (not funding.fully_funded or not settlement.redemption_id or settlement.redemption.order_id != order.pk
                or settlement.finalized_at or settlement.inviter_transaction_id or settlement.store_transaction_id
                or recorded != facts
                or uses != [(row.pk, row.amount - prior_addon[row.pk]) for row in allocations
                            if row.amount > prior_addon[row.pk]]):
            raise ValidationError('已预留补资或收益结算凭证待核对')
    from .points_service_participation import service_snapshots
    snapshots = service_snapshots(order)
    assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update().filter(
        order=order, released_at__isnull=True)}
    by_account = {row.segment.provider_account_id: row for row in snapshots}
    if (len(by_account) != len(snapshots) or set(by_account) != set(assignments)
            or not set(scope).issubset(by_account) or any(not by_account[pk].segment.started_at for pk in scope)):
        raise ValidationError('受影响陪玩或实际履约份额待核对')
    if paid_people:
        from .points_settlement_test import _verify_person
        from .settlement import SettlementError
        paid = paid_people[0]
        account_id = paid.segment.provider_account_id
        snapshot, assignment = by_account.get(account_id), assignments.get(account_id)
        if (not snapshot or not assignment or snapshot.segment_id != paid.segment_id
                or any(assignments[pk].settled_at for pk in scope)):
            raise ValidationError('已付人员或本次待结算陪玩待核对')
        try:
            from .addon_main_deduction import deductions_at
            deduction = deductions_at(order, before=paid.settled_at).get(account_id, 0)
            _verify_person(order, paid, snapshot, assignment, deduction=deduction)
        except SettlementError as exc:
            raise ValidationError('首退前已付人员原始流水待核对') from exc
    identities = {(row.user_id, row.account_id) for row in EscortProfile.objects.select_for_update()
                  .filter(account_id__in=scope)}
    if identities != {(assignments[pk].provider_id, pk) for pk in scope} or any(
            not LegacyAccountMap.objects.filter(account_id=pk, legacy_user_id=assignments[pk].provider_id).exists()
            for pk in scope):
        raise ValidationError('陪玩账号归属待核对')
    attribution = snapshots[0].segment.attribution
    if (attribution.original_amount != order.original_amount or any(not row.segment.original_share for row in snapshots)
            or sum(row.segment.original_share for row in snapshots) != attribution.original_amount + attribution.rounding_surplus):
        raise ValidationError('陪玩原价份额待核对')
    weights = {pk: by_account[pk].segment.original_share for pk in scope}
    try:
        burden = provider_refund_burden(refund_equivalent_units=refund_equivalent_units, original_price_weights=weights)
    except RefundCalculationError as exc:
        raise ValidationError(str(exc)) from exc
    from .addon_main_deduction import deductions_at
    addon_deductions = deductions_at(order)
    if set(addon_deductions) - set(by_account) or any(
            amount > by_account[account].income_at_entry for account, amount in addon_deductions.items()):
        raise ValidationError('加购退款的主单收益扣减待核对')
    terms = []
    for pk in sorted(scope):
        income = by_account[pk].income_at_entry - addon_deductions.get(pk, 0)
        amount = burden.amounts[pk]
        terms.append({'account_id': pk, 'original_income_units': income, 'burden_units': amount,
                      'retained_units': max(income - amount, 0), 'additional_units': max(amount - income, 0)})
    pending = sum(min(row['original_income_units'], row['burden_units']) for row in terms)
    external = sum(row['additional_units'] for row in terms)
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    post = funding_after_refund(principal_units=original.principal_units, gift_units=original.gift_units,
        compensation_units=original.compensation_units, returned_prepaid_units=sources.prepaid_units,
        returned_gift_units=sources.gift_units, returned_compensation_units=sources.compensation_units,
        provider_income_units=original.provider_income_units, pending_deduction_units=pending,
        provider_external_units=external, inviter_rate=rate, allocated_units=funding.allocated,
        rounding_reserved_units=burden.rounding_to_store_units)
    combined_post = None
    from .addon_settlement import has_paid_addons
    if has_paid_addons(order):
        from .addon_funding import include_paid_addons
        from .refunded_addon_funding import combine_refunded_funding
        try:
            pending_by_account = {row['account_id']: min(row['original_income_units'], row['burden_units'])
                                  for row in terms}
            retained_before_addon = {pk: row.income_at_entry - pending_by_account.get(pk, 0)
                                     for pk, row in by_account.items()}
            combined_post = combine_refunded_funding(post, include_paid_addons(order, post),
                                                      main_retained_by_account=retained_before_addon)
        except PointsFundingPlanError as exc:
            raise ValidationError(str(exc)) from exc
    try:
        releases = plan_release(allocations=[(row.pk, row.amount, prior_addon[row.pk]) for row in allocations],
                                amount=(combined_post or post).release_units)
    except PointsFundingAllocationError as exc:
        raise ValidationError('退款后补资释放记录待核对') from exc
    return {'order_id': order.pk, 'case_id': case.pk, 'redemption_id': receipt.pk,
            'refund_equivalent_units': refund_equivalent_units,
            'coin_units': assets.coin_units, 'points_hundredths': assets.point_hundredths,
            'returned_prepaid_units': sources.prepaid_units, 'returned_gift_units': sources.gift_units,
            'returned_compensation_units': sources.compensation_units, 'is_final': assets.is_final,
            'provider_terms': terms, 'rounding_to_store_units': burden.rounding_to_store_units,
            'original_required_units': funding.required, 'conditional_funding_if_recovered': post,
            'conditional_release_parts': releases, 'conditional_combined_funding': combined_post}
