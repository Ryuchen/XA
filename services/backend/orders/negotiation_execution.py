"""Atomic accounting and evidence checks for an agreed in-service refund."""
from django.conf import settings
from django.db.models import Q
import hashlib
import json
from rest_framework.exceptions import ValidationError

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Transaction, Wallet
from .financial_lock import lock_order_money
from .models import NegotiationExecution, NegotiationExecutionShare, NegotiationProposal, OrderRefund
from .negotiation_financial import preview
from .negotiation_proposals import _decisions, _lock_case, _scope, _verified, enabled
from .refunds import authorized_refund


def _allocation_hash(allocations):
    return hashlib.sha256(json.dumps(allocations, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _lock_reverse_addon_people(order):
    """Lock existing provider funds in account order for the narrow reverse flow."""
    if (order.required_provider_count != 2 or not order.addon_intents.filter(
            payment__refund_records__isnull=False).exists()):
        return
    assignments = list(order.providers.order_by('provider_account_id', 'pk'))
    if (not assignments or any(not row.provider_account_id or not row.provider_id for row in assignments)
            or len({row.provider_account_id for row in assignments}) != len(assignments)
            or len({row.provider_id for row in assignments}) != len(assignments)):
        raise ValidationError('双陪账号归属待核对')
    for row in assignments:
        profiles = list(EscortProfile.objects.select_for_update().filter(
            Q(account_id=row.provider_account_id) | Q(user_id=row.provider_id)).order_by('pk'))
        if len(profiles) != 1 or profiles[0].account_id != row.provider_account_id or profiles[0].user_id != row.provider_id:
            raise ValidationError('双陪押金账号归属待核对')
    for row in assignments:
        wallets = list(Wallet.objects.select_for_update().filter(
            Q(account_id=row.provider_account_id) | Q(user_id=row.provider_id)).order_by('pk'))
        if len(wallets) > 1 or any(wallet.account_id not in (None, row.provider_account_id)
                or wallet.user_id not in (None, row.provider_id) for wallet in wallets):
            raise ValidationError('双陪钱包账号归属待核对')


def prepare_locked(case_id, proposal_id, *, operator):
    """Revalidate agreement and available funds under the eventual write locks."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('协商退款核对必须在门店资金事务内执行')
    if not enabled() or not authorized_refund(operator) or not operator.is_active or not operator.can_login:
        raise ValidationError('仅有退款权限的有效后台账号可处理协商退款')
    if type(proposal_id) is not int or proposal_id <= 0:
        raise ValidationError('协商方案无效')
    order, case = _lock_case(case_id)
    # The shared money mutex must be acquired before any account/profile or
    # wallet locks, matching the other cross-wallet order-money operations.
    lock_order_money()
    _lock_reverse_addon_people(order)
    scope = _scope(order, case)
    proposal = NegotiationProposal.objects.select_for_update().filter(case=case, pk=proposal_id).first()
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    if not proposal or not latest or proposal.pk != latest.pk or proposal.affected_accounts != scope:
        raise ValidationError('协商方案已变化，请重新核对')
    _verified(case, proposal)
    from .ordinary_transfer_addon_agreement import applies as addon_applies, verify_locked as verify_addons
    from .points_service_payment import needs_integrated_processing
    if not needs_integrated_processing(order) and addon_applies(order):
        verify_addons(proposal, compare_current=True)
    _decisions(case, proposal, lock=True)
    if proposal.status != 'ACCEPTED':
        raise ValidationError('协商方案尚未获得全部当事人同意')
    calculation = preview(order, scope, proposal.refund_units, lock=True)
    if calculation['provider_terms'] != proposal.provider_terms:
        raise ValidationError('订单收益或退款分摊已变化，请重新确认方案')
    if not needs_integrated_processing(order) and calculation.get('segment_pending_plan') != proposal.ordinary_segment_pending_plan:
        raise ValidationError('逐段承担计划已变化，请重新确认方案')
    if (not needs_integrated_processing(order)
            and calculation.get('ordinary_paid_settlement_snapshot') != proposal.ordinary_paid_settlement_snapshot):
        raise ValidationError('已结算记录已变化，请重新确认方案')

    # The payment and every funding allocation are examined again while the
    # caller holds the transaction.  Later refund writes must use these same
    # rows and revalidate their lots; this is not a cross-request quote.
    list(order.transactions.select_for_update().order_by('pk'))
    payment = order.transactions.select_for_update().select_related('wallet').get(
        tx_type='PAY', status='SUCCESS', reversal_of__isnull=True)
    if (not payment.wallet.is_active or payment.wallet.account_id != order.customer_account_id
            or payment.wallet.user_id != order.customer_id):
        raise ValidationError('原付款钱包的账号归属不完整，请先核对')
    if order.funding_terms is not None:
        from engagement.funding_wallet import validate_order_funding_sources
        from engagement.models import PointFundingLot
        allocations = list(order.funding_allocations.select_for_update().select_related('lot').order_by('pk'))
        lots = {lot.pk: lot for lot in PointFundingLot.objects.select_for_update().filter(
            pk__in={item.lot_id for item in allocations}).order_by('pk')}
        if (not allocations or len(lots) != len({item.lot_id for item in allocations})
                or sum(item.amount for item in allocations) != order.amount
                or any(item.refunded_amount for item in allocations)):
            raise ValidationError('原付款资金来源或退款记录不完整，请先核对')
        for item in allocations:
            item.lot = lots[item.lot_id]
        validate_order_funding_sources(order, allocations)

    assignments = {item.provider_account_id: item for item in order.providers.all()}
    profiles = {p.account_id: p for p in EscortProfile.objects.select_for_update().filter(
        account_id__in=scope).order_by('pk')}
    user_ids = [assignments[account].provider_id for account in scope]
    wallets = list(Wallet.objects.select_for_update().filter(
        Q(account_id__in=scope) | Q(user_id__in=user_ids)).order_by('pk'))
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    from .points_checkout_test import ordinary_production_enabled
    prior_addon = order.addon_intents.filter(payment__refund_records__isnull=False).exists()
    if ordinary_production_enabled() and prior_addon:
        from .models import OrderOriginalShareTransfer
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        from .ordinary_transfer_capability import permits as transfer_permits
        if transferred and transfer_permits(order):
            from django.utils import timezone
            from .ordinary_transfer_addon_prior import verify_locked
            verify_locked(order, boundary=timezone.now())
        else:
            from .ordinary_zero_followup_capability import eligible as zero_followup_eligible
            zero_supported = proposal.refund_units == 0 and zero_followup_eligible(order)
            if not ordinary_addon_eligible(order, proposal=proposal) and not zero_supported:
                raise ValidationError('当前加购退款后的主单协商暂未开放')
            from .ordinary_addon_prior_refund import verify_locked
            verify_locked(order)
    plans = []
    for term in proposal.provider_terms:
        account_id = term['account_id']
        assignment, profile = assignments[account_id], profiles.get(account_id)
        matches = [wallet for wallet in wallets if wallet.account_id == account_id or wallet.user_id == assignment.provider_id]
        if (not profile or profile.user_id != assignment.provider_id or profile.deposit_paid < 0
                or not LegacyAccountMap.objects.select_for_update().filter(
                    account_id=account_id, legacy_user_id=assignment.provider_id).exists()
                or len(matches) > 1 or any(not wallet.is_active or wallet.balance < 0
                    or wallet.account_id not in (None, account_id)
                    or wallet.user_id not in (None, assignment.provider_id) for wallet in matches)):
            raise ValidationError('陪玩钱包或押金归属不完整，请先核对')
        # Older valid wallets may only carry one of the two identity columns.
        # Resolve and backfill it under the same money transaction, using the
        # shared identity rules; conflicting identities must still fail closed.
        if matches:
            from wallet.services import get_wallet, WalletAddressingError
            try:
                wallet = get_wallet(account_id=account_id, user_id=assignment.provider_id,
                                    for_update=True, create=False)
            except WalletAddressingError as exc:
                raise ValidationError('陪玩钱包或押金归属不完整，请先核对') from exc
        else:
            wallet = None
        pending = min(term['burden_units'], term['original_income_units'])
        remaining = term['burden_units'] - pending
        available = min(remaining, wallet.balance if wallet else 0)
        remaining -= available
        deposit = min(remaining, profile.deposit_paid)
        shortfall = remaining - deposit
        plans.append({'account_id': account_id, 'assignment': assignment, 'profile': profile, 'wallet': wallet,
                      'pending_units': pending, 'wallet_units': available, 'deposit_units': deposit,
                      'shortfall_units': shortfall})
    if any(item['shortfall_units'] for item in plans):
        raise ValidationError('陪玩待结算收益、可用余额及押金不足，本次不能退款')
    return {'order': order, 'case': case, 'proposal': proposal, 'calculation': calculation, 'providers': plans}


def verify_execution(execution):
    """Fail closed when persisted refund evidence no longer matches its agreement."""
    from .negotiation_proposals import _decisions, _verified
    if not execution.refund_id or not execution.customer_transaction_id:
        raise ValidationError('该协商凭证不包含退款')
    proposal, refund = execution.proposal, execution.refund
    case, tx = proposal.case, execution.customer_transaction
    from .refunds import request_fingerprint
    # After this refund, the same AfterSale row can be reopened for a later
    # episode. Verify against the proposal's recorded episode, not mutable
    # current case timestamps or request ID.
    from copy import copy
    episode = copy(case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    _verified(episode, proposal)
    _decisions(episode, proposal, lock=True)
    if (proposal.status != 'ACCEPTED' or execution.terms_hash != proposal.terms_hash
            or execution.accepted_at != proposal.accepted_at or refund.order_id != case.order_id
            or refund.operator_account_id != execution.operator_id or refund.amount != proposal.refund_units
            or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('negotiation_execution_version') != 1
            or refund.reason != proposal.explanation or not refund.client_request_id
            or refund.request_fingerprint != request_fingerprint(refund.amount, refund.reason,
                {'proposal_id':proposal.pk, 'terms_hash':proposal.terms_hash})
            or tx.order_id != case.order_id or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
            or tx.amount != refund.amount or tx.wallet.account_id != case.account_id
            or tx.wallet.user_id != case.order.customer_id or tx.operator_account_id != execution.operator_id
            or tx.operator_id != refund.operator_id or tx.reversal_of_id or tx.reversals.exists()
            or tx.balance_after != tx.balance_before + tx.amount):
        raise ValidationError('协商退款凭证不一致，请联系管理员核对')
    if case.order.funding_terms is not None:
        from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
        from engagement.funding_wallet import validate_order_funding_sources
        from engagement.models import PointFundingLot
        allocations = list(case.order.funding_allocations.select_for_update().order_by('pk'))
        lots = {lot.pk: lot for lot in PointFundingLot.objects.select_for_update().filter(
            pk__in={row.lot_id for row in allocations}).order_by('pk')}
        if (not allocations or len(lots) != len({row.lot_id for row in allocations})
                or sum(row.amount for row in allocations) != case.order.amount
                or sum(row.refunded_amount for row in allocations) != sum(
                    case.order.refund_records.values_list('amount', flat=True))):
            raise ValidationError('协商退款的原资金来源记录不完整')
        for row in allocations:
            row.lot = lots[row.lot_id]
        validate_order_funding_sources(case.order, allocations)
        followup = None
        if refund.benefit_effects.get('ordinary_transfer_followup_version') == 1:
            from .models import OrdinaryTransferFollowupRound
            from .ordinary_transfer_followup import _hash
            followup = OrdinaryTransferFollowupRound.objects.select_for_update().filter(execution=execution).first()
            if (not followup or followup.order_id != case.order_id or followup.proposal_id != proposal.pk
                    or followup.facts_hash != _hash(proposal.pk, followup.facts)):
                raise ValidationError('追加协商逐轮凭证缺失')
        try:
            if followup:
                before = {item['id']: item for item in followup.facts['funding_sources']}
                if set(before) != {row.pk for row in allocations} or any(
                        before[row.pk]['kind'] != row.kind or before[row.pk]['amount'] != row.amount
                        or before[row.pk]['lot_id'] != row.lot_id for row in allocations):
                    raise ValidationError('追加协商原付款来源已变化')
                delta = plan_refund([RefundablePart(row.pk, row.kind, row.amount,
                                    before[row.pk]['refunded_amount']) for row in allocations], refund.amount)
                expected = {row.pk: before[row.pk]['refunded_amount'] + delta[row.pk] for row in allocations}
            else:
                expected = plan_refund([RefundablePart(row.pk, row.kind, row.amount) for row in allocations], refund.amount)
            plan_refund([RefundablePart(row.pk, row.kind, row.amount, row.refunded_amount) for row in allocations], 0)
        except FundingPlanError as exc:
            raise ValidationError('协商退款的原资金来源记录异常') from exc
        if any(row.refunded_amount < expected[row.pk] for row in allocations):
            raise ValidationError('协商退款的原资金来源记录异常')
    rows = list(execution.shares.select_for_update().select_related(
        'assignment', 'wallet_transaction__wallet', 'deposit_deficit').order_by('account_id'))
    transfer_evidence = None
    if refund.benefit_effects.get('ordinary_transfer_negotiation_version') == 1:
        from .negotiation_transfer_evidence import verify_locked
        transfer_evidence = verify_locked(execution)
        if not transfer_evidence:
            raise ValidationError('转单协商凭证缺失')
    if refund.benefit_effects.get('ordinary_transfer_followup_version') == 1:
        from .models import OrdinaryTransferFollowupRound
        from .ordinary_transfer_followup import _hash
        transfer_evidence = OrdinaryTransferFollowupRound.objects.select_for_update().filter(execution=execution).first()
        if (not transfer_evidence or transfer_evidence.order_id != case.order_id
                or transfer_evidence.facts_hash != _hash(proposal.pk, transfer_evidence.facts)
                or transfer_evidence.facts.get('provider_terms') != proposal.provider_terms
                or transfer_evidence.facts.get('segment_pending_plan') != proposal.ordinary_segment_pending_plan
                or sorted(segment for ids in transfer_evidence.facts.get('segments', {}).values() for segment in ids)
                   != (proposal.affected_segment_ids or [])):
            raise ValidationError('追加协商逐轮收益凭证缺失')
        from .ordinary_retained_segment_shares import verify_plan
        try:
            verify_plan(transfer_evidence.facts, rows)
        except ValueError as exc:
            raise ValidationError('追加协商逐段扣款凭证异常') from exc
    from .addon_main_deduction import deductions_at
    prior_addon_deductions = deductions_at(case.order, before=execution.created_at)
    terms = {item['account_id']: item for item in proposal.provider_terms}
    if [row.account_id for row in rows] != sorted(terms):
        raise ValidationError('协商退款陪玩承担记录不完整')
    for row in rows:
        term = terms[row.account_id]
        if (row.assignment.order_id != case.order_id or row.assignment.provider_account_id != row.account_id
                or row.original_income != term['original_income_units']
                or (not transfer_evidence and row.original_income != row.assignment.provider_income
                    - prior_addon_deductions.get(row.account_id, 0))
                or row.burden != term['burden_units']
                or row.retained_income != term['retained_units']
                or row.original_income != row.pending_deduction + row.retained_income
                or row.burden != row.pending_deduction + row.wallet_deduction + row.deposit_deduction):
            raise ValidationError('协商退款陪玩收益或承担记录不一致')
        wallet_tx, deficit = row.wallet_transaction, row.deposit_deficit
        if row.wallet_deduction:
            if (not wallet_tx or wallet_tx.order_id != case.order_id or wallet_tx.tx_type != 'REFUND'
                    or wallet_tx.amount != -row.wallet_deduction or wallet_tx.status != 'SUCCESS'
                    or wallet_tx.wallet.account_id != row.account_id or wallet_tx.wallet.user_id != row.assignment.provider_id
                    or wallet_tx.operator_account_id != execution.operator_id or wallet_tx.operator_id != refund.operator_id
                    or wallet_tx.balance_after != wallet_tx.balance_before + wallet_tx.amount
                    or wallet_tx.reversal_of_id or wallet_tx.reversals.exists()):
                raise ValidationError('陪玩钱包扣款凭证异常')
        elif wallet_tx:
            raise ValidationError('陪玩钱包扣款凭证异常')
        if row.deposit_deduction:
            if (not deficit or deficit.refund_id != refund.pk or deficit.account_id != row.account_id
                    or deficit.amount != row.deposit_deduction or deficit.source_kind != 'DIRECT'):
                raise ValidationError('陪玩押金扣款凭证异常')
            from wallet.models import get_platform_wallet
            from wallet.refund_deposit import _verify_direct_deficit
            _verify_direct_deficit(deficit, get_platform_wallet(for_update=True))
        elif deficit:
            raise ValidationError('陪玩押金扣款凭证异常')
    allocations = refund.negotiated_allocation
    if not isinstance(allocations, list) or execution.allocation_hash != _allocation_hash(allocations):
        raise ValidationError('协商退款分摊记录不完整')
    by_assignment = {row.assignment_id: row for row in rows}
    matched = set()
    for item in allocations:
        if not isinstance(item, dict):
            raise ValidationError('协商退款分摊记录已变化')
        share = by_assignment.get(item.get('share_id'))
        if not share:
            continue  # An unaffected participant may subsequently transfer.
        assignment = share.assignment
        term = terms[share.account_id]
        expected = {'key':f'provider:{assignment.pk}', 'role':'PROVIDER',
            'account_id':assignment.provider_account_id, 'user_id':assignment.provider_id,
            'share_id':assignment.pk, 'entitlement':term['original_income_units'],
            'amount':term['burden_units'] if term else 0,
            'retained':term['retained_units'] if term else assignment.provider_income}
        if not isinstance(item, dict) or type(item.get('settled')) is not bool or {k:v for k,v in item.items() if k != 'settled'} != expected:
            raise ValidationError('协商退款分摊记录已变化')
        if assignment.pk in matched:
            raise ValidationError('协商退款分摊记录重复')
        matched.add(assignment.pk)
    if matched != set(by_assignment):
        raise ValidationError('协商退款分摊记录不完整')
    if (sum(row.burden for row in rows) - refund.amount != execution.rounding_to_store_units
            or refund.recovered_amount != sum(row.wallet_deduction + row.deposit_deduction for row in rows)):
        raise ValidationError('协商退款分摊或追回合计异常')
    return rows


@transaction.atomic
def apply_refund(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Post the refund and external burden together inside the caller's transaction.

    The feature-gated termination flow consumes its receipt in the same outer
    transaction; live use remains disabled until the required validation.
    """
    from .refunds import request_fingerprint
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    if (not enabled() or not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id != request_id.strip()):
        raise ValidationError('退款权限或操作凭证无效')
    if legacy_operator is not None:
        from club_accounts.models import LegacyAccountMap
        if not LegacyAccountMap.objects.filter(account=operator, legacy_user_id=legacy_operator.pk).exists():
            raise ValidationError('退款经办人的业务账号与历史身份不一致')
    order, case = _lock_case(case_id)
    lock_order_money()
    _lock_reverse_addon_people(order)
    existing = NegotiationExecution.objects.select_for_update().filter(proposal_id=proposal_id, proposal__case=case).first()
    if existing:
        if not existing.refund_id or existing.operator_id != operator.pk or existing.refund.client_request_id != request_id:
            raise ValidationError('该方案已处理，请核对原退款记录')
        verify_execution(existing)
        if order.addon_intents.filter(payment__refund_records__created_at__lt=existing.created_at).exists():
            if existing.refund.benefit_effects.get('ordinary_transfer_negotiation_version') == 1:
                from .ordinary_transfer_addon_prior import verify_locked
            else:
                from .ordinary_addon_prior_refund import verify_locked
            verify_locked(order, boundary=existing.created_at)
        return existing
    if (OrderRefund.objects.filter(order=order, client_request_id=request_id).exists()
            or NegotiationExecution.objects.filter(zero_order=order, zero_request_id=request_id).exists()):
        raise ValidationError('操作凭证已用于其他协商处理')
    plan = prepare_locked(case_id, proposal_id, operator=operator)
    order, proposal = plan['order'], plan['proposal']
    from .negotiation_transfer_preview import agreement_testing_available
    transfer_snapshot = None
    if agreement_testing_available(order):
        from .models import OrderPointsFundingAllocation
        from .ordinary_transfer_capability import permits as production_permits
        if (not production_permits(order) and (plan['calculation']['funding_if_recovered']['required_units']
                or OrderPointsFundingAllocation.objects.filter(service_order=order).exists())):
            raise ValidationError('转单退款的门店补资及释放尚待接通')
        from .negotiation_transfer_evidence import make_snapshot_locked
        transfer_snapshot = make_snapshot_locked(order, plan['calculation'])
    from wallet.refund_deposit import enabled as refund_deposit_enabled
    if any(item['deposit_units'] for item in plan['providers']) and not refund_deposit_enabled():
        raise ValidationError('退款押金处理尚未启用，请联系管理员核对')
    if not proposal.refund_units:
        raise ValidationError('零退款的协商终止处理尚未接通')
    if order.consumption_confirmed_at:
        raise ValidationError('订单实销已确认，请先核对')
    from .direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order=order).exists():
        raise ValidationError('微信直付订单的协商退款尚未接通')
    from .models import OrderPlatformSubsidy
    if OrderPlatformSubsidy.objects.filter(order=order).exists():
        raise ValidationError('平台补贴订单的协商退款尚未接通')
    if order.funding_terms is None and order.point_allocations.exists():
        raise ValidationError('历史付款来源须先核对，暂不能自动退款')
    payment = order.transactions.select_for_update().select_related('wallet').get(
        tx_type='PAY', status='SUCCESS', reversal_of__isnull=True)
    customer = Wallet.objects.select_for_update().get(pk=payment.wallet_id)
    if customer.balance + proposal.refund_units > 2147483647:
        raise ValidationError('老板钱包余额超过系统范围')
    from engagement.refunds import lock_refund_vip_operation
    vip_operation = lock_refund_vip_operation()

    affected = {item['account_id']: item for item in proposal.provider_terms}
    from .addon_main_deduction import deductions_at
    prior_addon_deductions = deductions_at(order)
    allocations = []
    for assignment in order.providers.select_for_update().order_by('pk'):
        term = affected.get(assignment.provider_account_id)
        amount = term['burden_units'] if term else 0
        income = assignment.provider_income - prior_addon_deductions.get(assignment.provider_account_id, 0)
        allocations.append({'key':f'provider:{assignment.pk}', 'role':'PROVIDER',
            'account_id':assignment.provider_account_id, 'user_id':assignment.provider_id,
            'share_id':assignment.pk, 'entitlement':term['original_income_units'] if term else income, 'amount':amount,
            'retained':term['retained_units'] if term else income,
            'settled':bool(assignment.settled_at)})
    refund = OrderRefund.objects.create(order=order, amount=proposal.refund_units, reason=proposal.explanation,
        operator=legacy_operator, operator_account=operator, client_request_id=request_id,
        request_fingerprint=request_fingerprint(proposal.refund_units, proposal.explanation,
                                                {'proposal_id':proposal.pk, 'terms_hash':proposal.terms_hash}),
        negotiated_allocation=allocations)
    before = customer.balance; customer.balance += proposal.refund_units; customer.save(update_fields=['balance'])
    customer_tx = Transaction.objects.create(wallet=customer, order=order, tx_type='REFUND', amount=proposal.refund_units,
        balance_before=before, balance_after=customer.balance, operator=legacy_operator, operator_account=operator,
        remark=f'协商退款：{order.order_no}')
    if order.funding_terms is not None:
        from engagement.funding_wallet import refund_locked_order_funding
        refund_locked_order_funding(order, customer, proposal.refund_units)
    from engagement.services import reverse_order_points
    reverse_order_points(order, refund=refund)
    from engagement.refunds import reverse_refund_benefits
    refund.benefit_effects = reverse_refund_benefits(order, refund, operator, vip_operation=vip_operation)
    refund.benefit_effects['negotiation_execution_version'] = 1
    if transfer_snapshot:
        refund.benefit_effects['ordinary_transfer_negotiation_version'] = 1
    if proposal.refund_units == order.amount:
        from coupons.benefits import restore_order_coupons
        from coupons.models import UserCoupon
        coupons = list(UserCoupon.objects.select_for_update().filter(
            order=order, status=UserCoupon.Status.USED).select_related('coupon').order_by('pk'))
        restore_order_coupons(order)
        if coupons:
            refund.benefit_effects['order_coupon_results'] = [
                {'name': row.coupon.name, 'outcome': 'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'}
                for row in coupons]
    execution = NegotiationExecution.objects.create(proposal=proposal, refund=refund, customer_transaction=customer_tx,
        operator=operator, terms_hash=proposal.terms_hash, allocation_hash=_allocation_hash(allocations), accepted_at=proposal.accepted_at,
        rounding_to_store_units=plan['calculation']['rounding_to_store_units'])
    if transfer_snapshot:
        from .negotiation_transfer_evidence import persist_locked
        persist_locked(execution, transfer_snapshot)
    for item in plan['providers']:
        wallet, wallet_tx, deficit = item['wallet'], None, None
        if item['wallet_units']:
            before = wallet.balance; wallet.balance -= item['wallet_units']; wallet.save(update_fields=['balance'])
            wallet_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND', amount=-item['wallet_units'],
                balance_before=before, balance_after=wallet.balance, operator=legacy_operator, operator_account=operator,
                remark=f'协商退款承担：{order.order_no}')
        if item['deposit_units']:
            from wallet.refund_deposit import charge_refund_deposit
            deficit = charge_refund_deposit(refund=refund, profile=item['profile'], amount=item['deposit_units'],
                                            operator=legacy_operator, operator_account=operator)
        term = affected[item['account_id']]
        NegotiationExecutionShare.objects.create(execution=execution, assignment=item['assignment'],
            account_id=item['account_id'], original_income=term['original_income_units'], burden=term['burden_units'],
            pending_deduction=item['pending_units'], wallet_deduction=item['wallet_units'],
            deposit_deduction=item['deposit_units'], retained_income=term['retained_units'],
            wallet_transaction=wallet_tx, deposit_deficit=deficit)
    refund.recovered_amount = sum(item['wallet_units'] + item['deposit_units'] for item in plan['providers'])
    refund.save(update_fields=['recovered_amount', 'benefit_effects'])
    from django.utils import timezone
    order.payment_status = order.PaymentStatus.REFUNDED if proposal.refund_units == order.amount else order.PaymentStatus.PARTIALLY_REFUNDED
    order.refunded_at = timezone.now()
    order.save(update_fields=['payment_status', 'refunded_at', 'updated_at'])
    if not transfer_snapshot:
        from .original_attribution import mark_review
        mark_review(order, '协商退款后原价份额待核对')
    verify_execution(execution)
    return execution
