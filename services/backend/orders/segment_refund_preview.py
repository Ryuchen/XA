"""Read-only allocation of a first refund for a captured transferred order.

The result is informational and intentionally contains no execution token.
Pending income, available wallet and deposits must be locked and reconciled
again together with the eventual refund and deposit-deficit ledgers.
"""
from collections import defaultdict

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from club_accounts.models import LegacyAccountMap
from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import get_account_permissions, is_console_account
from users.models import EscortProfile
from wallet.models import Transaction, Wallet
from .models import Order, OrderSegmentSettlementManifest
from .refund_math import RefundCalculationError, provider_refund_burden
from .refunds import RefundError, _customer_wallet
from .segment_settlement import _hash, _terms, _validate_records, verify_finalized
from .segment_settlement_plan import build_segment_settlement_plan
from .settlement import SettlementError


def _scope(value):
    if value is None:
        return None
    if (not isinstance(value, list) or not value or any(type(x) is not int or x <= 0 for x in value)
            or len(set(value)) != len(value)):
        raise RefundError('请选择本单受影响的陪玩', code='SEGMENT_REFUND_SCOPE_INVALID')
    return sorted(value)


def preview_segment_refund(order, *, amount, affected=None, now=None):
    """Allocate using each actual participant's original share, not each segment."""
    from engagement.confirmation import refund_within_aftersale_window
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    from .models import OrderOriginalShareTransfer

    if order.status != Order.Status.COMPLETED or order.payment_status != Order.PaymentStatus.PAID:
        raise RefundError('仅可核对已完成且尚未退款的服务订单', code='SEGMENT_REFUND_UNAVAILABLE')
    now = now or timezone.now()
    if not refund_within_aftersale_window(order, now=now):
        raise RefundError('订单售后期限已结束且没有期限内未结售后', code='SEGMENT_REFUND_EXPIRED')
    if type(amount) is not int or amount <= 0 or amount > order.amount or amount % 10:
        raise RefundError('退款金额须为不超过实付的整数店币', code='SEGMENT_REFUND_AMOUNT_INVALID')
    plan = build_segment_settlement_plan(order, now=now, allow_recorded=True)
    attribution = order.original_attribution
    if not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution).exists():
        raise RefundError('订单没有可核对的服务中转单', code='SEGMENT_REFUND_UNAVAILABLE')
    manifest = OrderSegmentSettlementManifest.objects.filter(order=order).first()
    if manifest:
        if manifest.plan != _terms(order, plan) or manifest.plan_hash != _hash(manifest.plan):
            raise RefundError('逐段结算快照已变化，请人工核对', code='SEGMENT_REFUND_LEDGER_INVALID')
        if manifest.finalized_at:
            verify_finalized(order, manifest)
            records = _validate_records(order, manifest, plan, finalized=True)
        else:
            records = _validate_records(order, manifest, plan)
    else:
        if (order.settled_at or order.providers.filter(settled_at__isnull=False).exists()
                or order.transactions.filter(tx_type__in=['INCOME', 'SHOP_INCOME'], status='SUCCESS',
                                             reversal_of__isnull=True).exists()):
            raise RefundError('订单存在未归属的历史收益，请先核对', code='SEGMENT_REFUND_LEDGER_INVALID')
        records = {}
    customer = _customer_wallet(order)
    payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', reversal_of__isnull=True))
    if (len(payments) != 1 or payments[0].wallet_id != customer.pk or payments[0].amount != -order.amount
            or payments[0].reversals.exists() or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise RefundError('订单付款流水不完整，请先核对', code='SEGMENT_REFUND_LEDGER_INVALID')
    weights = defaultdict(int)
    for segment in attribution.segments.filter(started_at__isnull=False, original_share__gt=0):
        weights[segment.provider_account_id] += segment.original_share
    if not weights or sum(weights.values()) != attribution.original_amount + attribution.rounding_surplus:
        raise RefundError('本人原价份额不完整，请先核对', code='SEGMENT_REFUND_SHARE_INVALID')
    requested = _scope(affected)
    case = order.aftersales.filter(status='OPEN').first()
    case_scope = _scope(case.affected_provider_account_ids) if case and case.affected_provider_account_ids is not None else None
    if case_scope and requested and requested != case_scope:
        raise RefundError('受影响人员须与当前售后范围一致', code='SEGMENT_REFUND_SCOPE_INVALID')
    selected = requested or case_scope or sorted(weights)
    if not set(selected).issubset(weights):
        raise RefundError('受影响陪玩不属于有原价份额的履约人员', code='SEGMENT_REFUND_SCOPE_INVALID')
    try:
        burden = provider_refund_burden(refund_equivalent_units=amount,
                                        original_price_weights={pk:weights[pk] for pk in selected})
    except RefundCalculationError as exc:
        raise RefundError(str(exc), code='SEGMENT_REFUND_SHARE_INVALID') from exc
    people = {p.account_id:p for p in plan.persons}
    rows = []
    for account_id in selected:
        person = people.get(account_id)
        mapping = LegacyAccountMap.objects.filter(account_id=account_id).first()
        profile = EscortProfile.objects.filter(account_id=account_id).first()
        if not person or not mapping or not profile or profile.user_id != mapping.legacy_user_id:
            raise RefundError('陪玩身份或本人收益份额待核对', code='SEGMENT_REFUND_LEDGER_INVALID')
        wallets = list(Wallet.objects.filter(Q(account_id=account_id)
                         | Q(account__isnull=True, user_id=mapping.legacy_user_id)))
        if len(wallets) > 1 or (wallets and (wallets[0].balance < 0 or not wallets[0].is_active)):
            raise RefundError('陪玩钱包状态或归属待核对', code='SEGMENT_REFUND_LEDGER_INVALID')
        available = wallets[0].balance if wallets else 0
        needed = burden.amounts[account_id]
        pending = 0 if account_id in records else min(needed, person.income)
        from_wallet = min(needed - pending, available)
        from_deposit = min(needed - pending - from_wallet, profile.deposit_paid)
        rows.append({'account_id':account_id, 'name':profile.display_name, 'original_share':weights[account_id],
                     'burden':needed, 'pending_income':pending, 'wallet_amount':from_wallet,
                     'deposit_amount':from_deposit, 'shortfall':needed-pending-from_wallet-from_deposit,
                     'already_settled':account_id in records})
    funding = []
    if order.funding_terms is not None:
        allocations = list(order.funding_allocations.select_related('lot').order_by('pk'))
        if (not allocations or sum(x.amount for x in allocations) != order.amount
                or any(x.lot.account_id != order.customer_account_id for x in allocations)):
            raise RefundError('老板付款来源不完整，请先核对', code='SEGMENT_REFUND_LEDGER_INVALID')
        try:
            deltas = plan_refund([RefundablePart(x.pk, x.kind, x.amount, x.refunded_amount) for x in allocations], amount)
        except FundingPlanError as exc:
            raise RefundError(str(exc), code='SEGMENT_REFUND_LEDGER_INVALID') from exc
        totals = defaultdict(int)
        for item in allocations:
            totals[item.kind] += deltas[item.pk]
        funding = [{'kind':kind, 'amount':totals[kind]} for kind in ('PREPAID','GIFT','COMPENSATION') if totals[kind]]
    names = dict(EscortProfile.objects.filter(account_id__in=weights).values_list('account_id', 'display_name'))
    return {'order_id':order.pk, 'refund_amount':amount, 'original_amount':order.amount,
        'refunded_amount':0, 'remaining_amount':order.amount, 'remaining_after_refund':order.amount-amount,
        'candidates':[{'account_id':pk, 'name':names.get(pk) or f'陪玩 #{pk}', 'original_share':weights[pk]}
                      for pk in sorted(weights)], 'scope_locked':bool(case_scope), 'payment_sources':funding,
        'payment_source_note':'' if funding else '付款来源尚需核对；无法确定的历史部分按本金处理',
        'scope':selected, 'case_id':case.pk if case else None, 'burdens':rows,
        'rounding_to_store':burden.rounding_to_store_units,
        'estimated_sufficient':all(not row['shortfall'] for row in rows),
        'executable':False, 'reason':'仅供核对；逐段退款、押金缺口和后续结算尚未接通',
        'calculated_at':now}


class SegmentRefundPreviewView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        from .segment_settlement import protection_enabled
        if not getattr(settings, 'SEGMENT_REFUND_PREVIEW_ENABLED', False) or not protection_enabled():
            return Response({'code':404, 'msg':'逐段退款核对暂未开放'}, status=404)
        if not is_console_account(request.account) or 'order:refund' not in get_account_permissions(request.account):
            return Response({'code':403, 'msg':'无退款核对权限'}, status=403)
        try:
            amount = serializers.IntegerField(min_value=1).run_validation(request.data.get('refund_amount'))
            with transaction.atomic():
                order = Order.objects.select_for_update().filter(pk=order_id).first()
                if not order:
                    return Response({'code':404, 'msg':'订单不存在'}, status=404)
                from .points_service_payment import needs_integrated_processing
                from .models import OrderOriginalShareTransfer
                if needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).exists():
                    cases = list(order.aftersales.filter(status='OPEN').order_by('pk')[:2])
                    if len(cases) != 1:
                        raise RefundError('请先核对当前售后范围', code='SEGMENT_REFUND_SCOPE_INVALID')
                    requested = _scope(request.data.get('affected_provider_account_ids'))
                    actual = _scope(cases[0].affected_provider_account_ids)
                    if requested is not None and requested != actual:
                        raise RefundError('受影响人员须与当前售后范围一致', code='SEGMENT_REFUND_SCOPE_INVALID')
                    from .points_transfer_refund_preview import preview_locked
                    data = preview_locked(cases[0].pk, refund_equivalent_units=amount)
                else:
                    data = preview_segment_refund(order, amount=amount, affected=request.data.get('affected_provider_account_ids'))
        except (serializers.ValidationError, RefundError, SettlementError) as exc:
            return Response({'code':409, 'msg':str(exc), 'errorCode':getattr(exc, 'code', 'SEGMENT_REFUND_REVIEW')}, status=409)
        return Response({'code':0, 'data':data})
