"""Locked financial preview for the remaining person after a teammate was paid.

The backend exposes this calculation only in isolated tests. It does not post
a refund; agreement and execution re-read it under the same order lock.
"""
from dataclasses import dataclass

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .points_checkout_test import require_isolated_checkout
from .settlement import SettlementError


@dataclass(frozen=True)
class FollowupFundingPreview:
    account_id: int
    prior_settled_account_id: int
    prior_paid: int
    refund_units: int
    returned_principal: int
    returned_gift: int
    returned_compensation: int
    pending_deduction: int
    wallet_deduction: int
    deposit_deduction: int
    insufficient: int
    retained_main_income: int
    provider_income: int
    inviter_income: int
    store_income: int
    required_funding: int
    allocated_funding: int
    funding_to_release: int
    funding_still_needed: int


def applies(order):
    """The recorded mixed settlement requires its own follow-up coordinator."""
    from .models import (NegotiationExecution, OrderAddonHandover, OrderAddonPayment,
                         OrderOrdinaryRefundAddonSettlementEvidence, OrderOriginalShareTransfer,
                         OrderPlatformSubsidy, OrderPrestartHandoff)
    if OrderOrdinaryRefundAddonSettlementEvidence.objects.filter(settlement__order=order).exists():
        return True
    from .points_service_payment import needs_integrated_processing
    from .ordinary_followup_capability import stage_enabled, production_enabled
    from .ordinary_zero_followup_capability import eligible as zero_followup_eligible
    zero_first = bool(zero_followup_eligible(order) and not order.refund_records.exists()
        and NegotiationExecution.objects.filter(zero_order=order, termination_processed_at__isnull=False).exists())
    return bool(stage_enabled('ORDINARY_ADDON_FOLLOWUP_PREVIEW_INTERNAL_TESTING')
        and order.completed_at and not order.settled_at and order.required_provider_count == 2
        and not order.is_gift_snapshot and not needs_integrated_processing(order)
        and (order.refund_records.count() == 1 or zero_first)
        and (production_enabled() or OrderAddonPayment.objects.filter(intent__order=order).exists())
        and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        and not OrderPrestartHandoff.objects.filter(order=order).exists()
        and not OrderPlatformSubsidy.objects.filter(order=order).exists()
        and not OrderAddonHandover.objects.filter(payment__intent__order=order,
            status__in=('PENDING',) if zero_first else ('PENDING', 'ACCEPTED')).exists()
        and (zero_first or NegotiationExecution.objects.filter(refund__order=order,
             termination_processed_at__isnull=False).exists()))


def agreement_testing_available(order):
    """Agreement wiring is available only to isolated end-to-end tests."""
    from .ordinary_followup_capability import require_access, stage_enabled
    if not stage_enabled('ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING') or not applies(order):
        return False
    try:
        require_access()
    except ValidationError:
        return False
    return True


def provider_terms(estimate):
    burden = (estimate.pending_deduction + estimate.wallet_deduction +
              estimate.deposit_deduction + estimate.insufficient)
    return [{'account_id': estimate.account_id,
             'original_income_units': estimate.retained_main_income + estimate.pending_deduction,
             'burden_units': burden, 'retained_units': estimate.retained_main_income,
             'additional_units': burden - estimate.pending_deduction}]


def preview_case_locked(case_id, *, refund_units):
    """Only a timely open case for the continuing person may use this estimate."""
    from datetime import timedelta
    from django.utils import timezone
    from .negotiation_proposals import _lock_case
    if not transaction.get_connection().in_atomic_block:
        raise SettlementError('后续售后预览必须在门店事务内执行')
    try:
        order, case = _lock_case(case_id)
    except ValidationError as exc:
        raise SettlementError('售后记录不存在') from exc
    scope = case.affected_provider_account_ids
    if (case.status != 'OPEN' or case.account_id != order.customer_account_id or not case.client_request_id
            or not isinstance(scope, list) or len(scope) != 1 or not order.completed_at
            or order.service_aftersale_hours is None or not case.created_at
            or not order.completed_at <= case.created_at <= timezone.now()
            or case.created_at >= order.completed_at + timedelta(hours=order.service_aftersale_hours)):
        raise SettlementError('后续售后范围或创建时间待核对')
    estimate = preview_locked(order.pk, account_id=scope[0], refund_units=refund_units)
    from .models import NegotiationExecution
    previous = NegotiationExecution.objects.filter(refund__order=order, termination_processed_at__isnull=False).first()
    if not previous:
        from .negotiation_zero import marked_execution_id
        zero_id = marked_execution_id(order)
        previous = NegotiationExecution.objects.filter(pk=zero_id, zero_order=order,
                                                       termination_processed_at__isnull=False).first() if zero_id else None
    if not previous or not previous.termination_processed_at <= case.created_at:
        raise SettlementError('后续售后早于前次协商处理')
    return estimate


def preview_locked(order_id, *, account_id, refund_units):
    """Reconcile a proposed B refund without changing paid A or moving funds."""
    from .ordinary_followup_capability import require_access, stage_enabled
    require_access()
    if (not transaction.get_connection().in_atomic_block
            or not stage_enabled('ORDINARY_ADDON_FOLLOWUP_PREVIEW_INTERNAL_TESTING')):
        raise SettlementError('加购订单后续退款资金预览尚未开放')
    if (type(account_id) is not int or account_id <= 0 or type(refund_units) is not int
            or refund_units < 0 or refund_units % 10):
        raise SettlementError('陪玩或退款金额无效')
    from .models import Order, OrderPointsSettlement
    from .financial_lock import lock_order_money
    from .ordinary_refunded_addon_funding import plan_locked
    from .ordinary_refunded_addon_settlement import _economics_locked, verify_recorded_locked
    from .points_funding_plan import PointsFundingPlanError
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise SettlementError('订单不存在')
    lock_order_money()
    if order.settled_at or order.consumption_confirmed_at:
        raise SettlementError('订单已有最终结算或实销')
    try:
        plan = plan_locked(order.pk, _allow_settled=True)
        if OrderPointsSettlement.objects.filter(order=order).exists():
            settlement, paid = verify_recorded_locked(order, allow_unfunded_followup=True)
            facts = settlement.ordinary_refund_addon_evidence.facts
        else:
            from .points_funding_evidence import verified_order_allocations_locked
            from .addon_funding_release import addon_release_totals
            if plan.releasable or (plan.remaining and not stage_enabled(
                    'ORDINARY_ADDON_FOLLOWUP_UNFUNDED_INTERNAL_TESTING')):
                raise SettlementError('前次退款的整单补资尚未足额核对')
            allocations = verified_order_allocations_locked(order)
            released = addon_release_totals(allocations)
            uses = [(row.pk, row.amount - released[row.pk]) for row in allocations if row.amount > released[row.pk]]
            _, facts = _economics_locked(order, plan, uses)
            paid = []
    except (PointsFundingPlanError, ValidationError) as exc:
        raise SettlementError('原退款及加购资金凭证待核对') from exc
    people = facts['people']
    target = next((item for item in people if item['account_id'] == account_id), None)
    if (len(people) != 2 or len(paid) > 1 or not target or target['affected']
            or (paid and paid[0].segment_id == target['segment_id'])):
        raise SettlementError('后续售后须针对尚未结算的另一位陪玩')
    previous = next((item for item in people if item['affected']), None)
    if not previous or (paid and previous['segment_id'] != paid[0].segment_id):
        raise SettlementError('前次售后与已结算陪玩不一致')

    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    allocations = list(order.funding_allocations.select_for_update().order_by('pk'))
    try:
        deltas = plan_refund([RefundablePart(row.pk, row.kind, row.amount, row.refunded_amount)
                              for row in allocations], refund_units)
    except FundingPlanError as exc:
        raise SettlementError('后续退款的原支付来源待核对') from exc
    returned = {kind: sum(deltas[row.pk] for row in allocations if row.kind == kind)
                for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    if sum(returned.values()) != refund_units or refund_units > plan.main.net_coin_units:
        raise SettlementError('后续退款超过主单剩余可退金额')

    from .refund_math import provider_refund_burden
    burden = provider_refund_burden(refund_equivalent_units=refund_units,
                                    original_price_weights={account_id: 1}).amounts[account_id] if refund_units else 0
    pending = min(burden, target['main_income'])
    external = burden - pending
    from users.models import EscortProfile
    from django.db.models import Q
    from wallet.models import Wallet
    profile = EscortProfile.objects.select_for_update().filter(account_id=account_id, user_id=target['user_id']).first()
    if not profile or profile.deposit_paid < 0:
        raise SettlementError('后续退款陪玩押金身份待核对')
    wallets = list(Wallet.objects.select_for_update().filter(
        Q(account_id=account_id) | Q(user_id=target['user_id'])).order_by('pk'))
    if (len(wallets) > 1 or any(item.account_id not in (None, account_id)
                                or item.user_id not in (None, target['user_id']) for item in wallets)):
        raise SettlementError('后续退款陪玩钱包身份待核对')
    wallet = wallets[0] if wallets else None
    if wallet and (not wallet.is_active or wallet.balance < 0):
        raise SettlementError('后续退款陪玩钱包不可用')
    wallet_part = min(external, wallet.balance if wallet else 0)
    deposit_part = min(external - wallet_part, profile.deposit_paid)
    insufficient = external - wallet_part - deposit_part

    combined = plan.combined
    principal = combined.net_principal_units - returned['PREPAID']
    provider_income = combined.provider_income_units - pending
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    inviter_income = (plan.main.net_coin_units - refund_units) * rate // 100
    # Insufficient amounts are not actual funds; the estimate shows the
    # funding state only if the provider supplies the missing recovery.
    margin = principal + combined.planned_provider_external_units + external - provider_income - inviter_income
    from .models import NegotiationExecution
    previous_execution = NegotiationExecution.objects.select_for_update().get(pk=facts['execution_id'])
    rounding = previous_execution.rounding_to_store_units + sum(
        order.addon_refunds.values_list('rounding_surplus', flat=True))
    # Preserve the prior refund's separately payable rounding surplus even
    # when promotional balances make the order require additional funding.
    required = max(0, rounding - margin)
    store = margin + required
    return FollowupFundingPreview(account_id, previous['account_id'], len(paid), refund_units,
        returned['PREPAID'], returned['GIFT'], returned['COMPENSATION'], pending,
        wallet_part, deposit_part, insufficient, target['main_income'] - pending,
        provider_income, inviter_income, store, required, plan.allocated,
        max(plan.allocated - required, 0), max(required - plan.allocated, 0))
