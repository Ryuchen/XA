"""Atomic, isolated posting for a second ordinary negotiation with add-ons.

The caller must finish the case and rebalance funding in the same coordinator
before the transaction commits. The remaining person is settled separately
when their own conditions are satisfied. The backend route is restricted to
the isolated test configuration until production validation is complete.
"""
from dataclasses import asdict

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .settlement import SettlementError


def post_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('追加退款必须由完整结案事务调用')
    # A failed sub-operation rolls back even when the caller handles the error.
    with transaction.atomic():
        return _post_locked(case_id, proposal_id, operator=operator,
                            legacy_operator=legacy_operator, request_id=request_id)


def _post_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .ordinary_followup_capability import require_access, stage_enabled
    require_access()
    if (not transaction.get_connection().in_atomic_block
            or not stage_enabled('ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING')):
        raise ValidationError('带加购的追加退款记账尚未开放')
    from .negotiation_proposals import _decisions, _lock_case, _verified
    from .refunds import authorized_refund, request_fingerprint
    from club_accounts.models import LegacyAccountMap
    from .models import (NegotiationExecution, NegotiationExecutionShare, NegotiationFollowupLink,
                         NegotiationProposal, OrderRefund)
    if (not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or not isinstance(request_id, str) or request_id != request_id.strip() or not 1 <= len(request_id) <= 64
            or (legacy_operator is not None and not LegacyAccountMap.objects.filter(
                account=operator, legacy_user_id=legacy_operator.pk).exists())):
        raise ValidationError('退款权限或操作凭证无效')
    order, case = _lock_case(case_id)
    from .financial_lock import lock_order_money
    lock_order_money()
    existing = NegotiationFollowupLink.objects.select_for_update().filter(execution__proposal_id=proposal_id,
                                                                          order=order).first()
    if existing:
        key = (existing.execution.refund.client_request_id if existing.execution.refund_id
               else existing.execution.zero_request_id)
        if existing.execution.operator_id != operator.pk or key != request_id:
            raise ValidationError('该方案已处理，请核对原退款记录')
        verify_posting_locked(existing)
        return existing
    if (OrderRefund.objects.filter(order=order, client_request_id=request_id).exists()
            or NegotiationExecution.objects.filter(zero_order=order, zero_request_id=request_id).exists()):
        raise ValidationError('操作凭证已用于其他退款')
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    if not proposal or not latest or latest.pk != proposal.pk:
        raise ValidationError('协商方案已变化，请重新核对')
    _verified(case, proposal)
    _decisions(case, proposal, lock=True)
    if proposal.status != 'ACCEPTED':
        raise ValidationError('追加协商须经双方确认')
    from .models import OrderPointsSettlement
    if not OrderPointsSettlement.objects.filter(order=order).exists():
        from .ordinary_refunded_addon_settlement import reserve_locked
        try:
            if not reserve_locked(order, allow_unfunded_followup=stage_enabled(
                    'ORDINARY_ADDON_FOLLOWUP_UNFUNDED_INTERNAL_TESTING')):
                raise ValidationError('前次退款的整单补资尚未足额核对')
        except SettlementError as exc:
            raise ValidationError(str(exc)) from exc
    if not proposal.refund_units:
        from .ordinary_addon_followup_termination import zero_enabled
        if not zero_enabled():
            raise ValidationError('带加购的零退款追加协商尚未开放')
        return _post_zero_locked(order, case, proposal, operator=operator, request_id=request_id)
    from .ordinary_addon_followup_preview import preview_case_locked, provider_terms
    try:
        estimate = preview_case_locked(case.pk, refund_units=proposal.refund_units)
    except SettlementError as exc:
        raise ValidationError(str(exc)) from exc
    if proposal.provider_terms != provider_terms(estimate) or proposal.affected_accounts != [estimate.account_id]:
        raise ValidationError('陪玩承担或保留收益已变化，请重新确认')
    if estimate.insufficient:
        raise ValidationError('陪玩待结算收益、钱包及押金不足，暂不能退款')
    from wallet.refund_deposit import enabled as deposit_enabled
    if estimate.deposit_deduction and not deposit_enabled():
        raise ValidationError('退款押金处理尚未启用')

    settlement = order.points_settlement
    evidence = settlement.ordinary_refund_addon_evidence
    previous = evidence.execution
    assignment = order.providers.select_for_update().get(provider_account_id=estimate.account_id)
    from wallet.models import Transaction, Wallet
    from wallet.services import get_wallet
    customer_payment = order.transactions.select_for_update().select_related('wallet').get(
        tx_type='PAY', status='SUCCESS', reversal_of__isnull=True)
    customer = Wallet.objects.select_for_update().get(pk=customer_payment.wallet_id)
    if not customer.is_active or customer.balance + proposal.refund_units > 2147483647:
        raise ValidationError('老板钱包不可用或余额超出范围')
    provider = get_wallet(account_id=estimate.account_id, user_id=assignment.provider_id,
                          for_update=True, create=bool(estimate.wallet_deduction))
    from engagement.refunds import lock_refund_vip_operation
    vip_operation = lock_refund_vip_operation()
    allocations = []
    facts = {row['account_id']: row for row in evidence.facts['people']}
    for row in order.providers.select_for_update().order_by('pk'):
        own = row.pk == assignment.pk
        fact = facts[row.provider_account_id]
        allocations.append({'key': f'provider:{row.pk}', 'role': 'PROVIDER',
            'account_id': row.provider_account_id, 'user_id': row.provider_id, 'share_id': row.pk,
            'entitlement': (estimate.retained_main_income + estimate.pending_deduction) if own else fact['main_income'],
            'amount': proposal.refund_units if own else 0,
            'retained': estimate.retained_main_income if own else fact['main_income'], 'settled': bool(row.settled_at)})
    refund = OrderRefund.objects.create(order=order, amount=proposal.refund_units, reason=proposal.explanation,
        operator=legacy_operator, operator_account=operator, client_request_id=request_id,
        request_fingerprint=request_fingerprint(proposal.refund_units, proposal.explanation,
                                                {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash}),
        negotiated_allocation=allocations)
    before = customer.balance
    customer.balance += refund.amount
    customer.save(update_fields=['balance'])
    customer_tx = Transaction.objects.create(wallet=customer, order=order, tx_type='REFUND', amount=refund.amount,
        balance_before=before, balance_after=customer.balance, operator=legacy_operator,
        operator_account=operator, remark=f'协商退款：{order.order_no}')
    from engagement.funding_wallet import refund_locked_order_funding
    refund_locked_order_funding(order, customer, refund.amount)
    from engagement.services import reverse_order_points
    from engagement.refunds import reverse_refund_benefits
    reverse_order_points(order, refund=refund)
    refund.benefit_effects = reverse_refund_benefits(order, refund, operator, vip_operation=vip_operation)
    refund.benefit_effects['negotiation_execution_version'] = 1
    wallet_tx = deficit = None
    if estimate.wallet_deduction:
        if not provider or provider.balance < estimate.wallet_deduction:
            raise ValidationError('陪玩钱包余额已变化，请重新核对')
        before = provider.balance
        provider.balance -= estimate.wallet_deduction
        provider.save(update_fields=['balance'])
        wallet_tx = Transaction.objects.create(wallet=provider, order=order, tx_type='REFUND',
            amount=-estimate.wallet_deduction, balance_before=before, balance_after=provider.balance,
            operator=legacy_operator, operator_account=operator, remark=f'协商退款承担：{order.order_no}')
    if estimate.deposit_deduction:
        from users.models import EscortProfile
        from wallet.refund_deposit import charge_refund_deposit
        profile = EscortProfile.objects.select_for_update().get(account_id=estimate.account_id,
                                                                user_id=assignment.provider_id)
        deficit = charge_refund_deposit(refund=refund, profile=profile, amount=estimate.deposit_deduction,
                                        operator=legacy_operator, operator_account=operator)
    refund.recovered_amount = estimate.wallet_deduction + estimate.deposit_deduction
    refund.save(update_fields=['recovered_amount', 'benefit_effects'])
    from .negotiation_execution import _allocation_hash, verify_execution
    current = NegotiationExecution.objects.create(proposal=proposal, refund=refund, customer_transaction=customer_tx,
        operator=operator, terms_hash=proposal.terms_hash, allocation_hash=_allocation_hash(allocations),
        accepted_at=proposal.accepted_at, rounding_to_store_units=0)
    NegotiationExecutionShare.objects.create(execution=current, assignment=assignment, account_id=estimate.account_id,
        original_income=estimate.retained_main_income + estimate.pending_deduction, burden=refund.amount,
        pending_deduction=estimate.pending_deduction, wallet_deduction=estimate.wallet_deduction,
        deposit_deduction=estimate.deposit_deduction, retained_income=estimate.retained_main_income,
        wallet_transaction=wallet_tx, deposit_deficit=deficit)
    from django.utils import timezone
    cumulative = (previous.refund.amount if previous.refund_id else 0) + refund.amount
    order.payment_status = order.PaymentStatus.REFUNDED if cumulative == order.amount else order.PaymentStatus.PARTIALLY_REFUNDED
    order.refunded_at = timezone.now()
    order.save(update_fields=['payment_status', 'refunded_at', 'updated_at'])
    if cumulative == order.amount:
        from coupons.benefits import restore_order_coupons
        from coupons.models import UserCoupon
        coupons = list(UserCoupon.objects.select_for_update().filter(order=order, status=UserCoupon.Status.USED)
                       .select_related('coupon').order_by('pk'))
        restore_order_coupons(order)
        if coupons:
            refund.benefit_effects['order_coupon_results'] = [{'name': row.coupon.name,
                'outcome': 'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'} for row in coupons]
            refund.save(update_fields=['benefit_effects'])
    from .models import OrderStatusLog
    completion = list(order.status_logs.filter(action=OrderStatusLog.Action.COMPLETE,
        from_status=order.Status.IN_SERVICE, to_status=order.Status.COMPLETED))
    if (len(completion) != 1 or not order.completed_at or order.completed_at > proposal.case_opened_at
            or not order.completed_at <= completion[0].created_at <= proposal.accepted_at):
        raise ValidationError('原订单完成时间或记录待核对')
    projected = {'ordinary_addon_followup_version': 1, 'prior_facts_hash': evidence.facts_hash,
                 'estimate': asdict(estimate), 'completion_evidence': {'completed_at': order.completed_at.isoformat(),
                                                                       'completion_log_id': completion[0].pk}}
    link = NegotiationFollowupLink.objects.create(order=order, previous=previous, execution=current,
        prior_refund_units=previous.refund.amount if previous.refund_id else 0, original_amount_units=order.amount,
        previous_terms_hash=previous.terms_hash, projected_ledger=projected, ledger_hash=_allocation_hash(projected))
    verify_execution(current)
    verify_posting_locked(link)
    return link


def _post_zero_locked(order, case, proposal, *, operator, request_id):
    """Record both-party agreement without creating any refund or money row."""
    from .ordinary_addon_followup_preview import preview_case_locked, provider_terms
    from .negotiation_execution import _allocation_hash
    from .models import NegotiationExecution, NegotiationExecutionShare, NegotiationFollowupLink, OrderStatusLog
    try:
        estimate = preview_case_locked(case.pk, refund_units=0)
    except SettlementError as exc:
        raise ValidationError(str(exc)) from exc
    if proposal.provider_terms != provider_terms(estimate) or proposal.affected_accounts != [estimate.account_id]:
        raise ValidationError('陪玩保留收益已变化，请重新确认')
    evidence = order.points_settlement.ordinary_refund_addon_evidence
    previous = evidence.execution
    assignments = list(order.providers.select_for_update().order_by('pk'))
    facts = {row['account_id']: row for row in evidence.facts['people']}
    allocations = [{'key': f'provider:{row.pk}', 'role': 'PROVIDER', 'account_id': row.provider_account_id,
                    'user_id': row.provider_id, 'share_id': row.pk, 'entitlement': facts[row.provider_account_id]['main_income'],
                    'amount': 0, 'retained': facts[row.provider_account_id]['main_income'], 'settled': bool(row.settled_at)}
                   for row in assignments]
    assignment = next((row for row in assignments if row.provider_account_id == estimate.account_id), None)
    logs = list(order.status_logs.filter(action=OrderStatusLog.Action.COMPLETE,
        from_status=order.Status.IN_SERVICE, to_status=order.Status.COMPLETED))
    if (not assignment or len(logs) != 1 or not order.completed_at or order.completed_at > proposal.case_opened_at
            or not order.completed_at <= logs[0].created_at <= proposal.accepted_at):
        raise ValidationError('零退款协商的订单完成或陪玩凭证待核对')
    current = NegotiationExecution.objects.create(proposal=proposal, zero_order=order, zero_request_id=request_id,
        zero_allocation=allocations, operator=operator, terms_hash=proposal.terms_hash,
        allocation_hash=_allocation_hash(allocations), accepted_at=proposal.accepted_at)
    NegotiationExecutionShare.objects.create(execution=current, assignment=assignment, account_id=estimate.account_id,
        original_income=estimate.retained_main_income, burden=0, pending_deduction=0, wallet_deduction=0,
        deposit_deduction=0, retained_income=estimate.retained_main_income)
    projected = {'ordinary_addon_followup_version': 1, 'prior_facts_hash': evidence.facts_hash,
                 'estimate': asdict(estimate), 'completion_evidence': {'completed_at': order.completed_at.isoformat(),
                                                                       'completion_log_id': logs[0].pk}}
    link = NegotiationFollowupLink.objects.create(order=order, previous=previous, execution=current,
        prior_refund_units=previous.refund.amount if previous.refund_id else 0, original_amount_units=order.amount,
        previous_terms_hash=previous.terms_hash, projected_ledger=projected, ledger_hash=_allocation_hash(projected))
    verify_posting_locked(link)
    return link


def _verify_zero_current(order, current, economics, prior_paid):
    from copy import copy
    from .negotiation_execution import _allocation_hash
    from .negotiation_proposals import _decisions, _verified
    proposal, episode = current.proposal, copy(current.proposal.case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    _verified(episode, proposal)
    _decisions(episode, proposal, lock=True)
    if (current.zero_order_id != order.pk or current.refund_id or current.customer_transaction_id
            or proposal.case.order_id != order.pk
            or proposal.refund_units or proposal.status != 'ACCEPTED' or current.terms_hash != proposal.terms_hash
            or current.accepted_at != proposal.accepted_at or not isinstance(current.zero_request_id, str)
            or not 1 <= len(current.zero_request_id) <= 64 or current.zero_request_id != current.zero_request_id.strip()
            or current.allocation_hash != _allocation_hash(current.zero_allocation)):
        raise ValidationError('零退款追加协商凭证不一致')
    assignments = list(order.providers.select_for_update().order_by('pk'))
    facts = {row['account_id']: row for row in economics['people']}
    expected = [{'key': f'provider:{row.pk}', 'role': 'PROVIDER', 'account_id': row.provider_account_id,
                 'user_id': row.provider_id, 'share_id': row.pk, 'entitlement': facts[row.provider_account_id]['main_income'],
                 'amount': 0, 'retained': facts[row.provider_account_id]['main_income'],
                 'settled': bool(prior_paid and facts[row.provider_account_id]['affected'])} for row in assignments]
    if current.zero_allocation != expected:
        raise ValidationError('零退款追加协商收益归属不一致')
    shares = list(current.shares.select_for_update().select_related('assignment'))
    if (len(shares) != 1 or shares[0].account_id not in facts
            or shares[0].assignment.provider_account_id != shares[0].account_id
            or shares[0].assignment.order_id != order.pk
            or shares[0].original_income != facts[shares[0].account_id]['main_income']
            or shares[0].retained_income != shares[0].original_income
            or any((shares[0].burden, shares[0].pending_deduction, shares[0].wallet_deduction,
                    shares[0].deposit_deduction, shares[0].wallet_transaction_id, shares[0].deposit_deficit_id))):
        raise ValidationError('零退款追加协商陪玩收益不一致')
    if proposal.provider_terms != [{'account_id': shares[0].account_id,
            'original_income_units': shares[0].original_income, 'burden_units': 0,
            'retained_units': shares[0].retained_income, 'additional_units': 0}]:
        raise ValidationError('零退款追加协商方案与收益不一致')
    return shares


def verify_posting_locked(link):
    """Verify both money receipts and retain the already-paid person's evidence."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('追加退款凭证必须在门店事务内核对')
    from .models import NegotiationFollowupLink
    from .negotiation_execution import _allocation_hash, verify_execution
    from .ordinary_refunded_addon_settlement import _digest, _verify_paid_people
    from .ordinary_addon_followup_preview import FollowupFundingPreview
    from dataclasses import fields
    link = NegotiationFollowupLink.objects.select_for_update().select_related(
        'order', 'previous__refund', 'execution__refund').get(pk=link.pk)
    order, previous, current = link.order, link.previous, link.execution
    prior_amount = previous.refund.amount if previous.refund_id else 0
    evidence = order.points_settlement.ordinary_refund_addon_evidence
    projection = link.projected_ledger
    if (not isinstance(projection, dict) or set(projection) != {'ordinary_addon_followup_version', 'prior_facts_hash', 'estimate', 'completion_evidence'}
            or projection['ordinary_addon_followup_version'] != 1 or link.ledger_hash != _allocation_hash(projection)
            or evidence.execution_id != previous.pk or projection['prior_facts_hash'] != evidence.facts_hash
            or evidence.facts_hash != _digest(evidence.facts) or link.prior_refund_units != prior_amount
            or link.original_amount_units != order.amount or link.previous_terms_hash != previous.terms_hash
            or (current.refund_id and current.refund.order_id != order.pk)
            or (not current.refund_id and current.zero_order_id != order.pk)
            or (previous.refund.order_id != order.pk if previous.refund_id else previous.zero_order_id != order.pk)):
        raise ValidationError('追加退款及原结算凭证不一致')
    from .models import OrderStatusLog
    completion = projection['completion_evidence']
    logs = list(order.status_logs.filter(action=OrderStatusLog.Action.COMPLETE,
        from_status=order.Status.IN_SERVICE, to_status=order.Status.COMPLETED))
    if (not isinstance(completion, dict) or set(completion) != {'completed_at', 'completion_log_id'}
            or len(logs) != 1 or completion['completion_log_id'] != logs[0].pk or not order.completed_at
            or completion['completed_at'] != order.completed_at.isoformat()
            or not order.completed_at <= logs[0].created_at <= current.accepted_at):
        raise ValidationError('追加退款的订单完成凭证不一致')
    if previous.refund_id:
        before = verify_execution(previous)
    else:
        from .negotiation_zero import verify_zero_execution, verify_zero_termination
        before = verify_zero_execution(previous, _allow_linked_refunds=True)
        verify_zero_termination(previous)
        from .ordinary_zero_followup_evidence import verified_first
        verified_first(previous, boundary=current.accepted_at)
    expected = projection['estimate']
    if not isinstance(expected, dict) or expected.get('prior_paid') not in (0, 1):
        raise ValidationError('追加退款前的结算状态待核对')
    after = verify_execution(current) if current.refund_id else _verify_zero_current(
        order, current, evidence.facts, expected['prior_paid'])
    current_amount = current.refund.amount if current.refund_id else 0
    current_recovered = current.refund.recovered_amount if current.refund_id else 0
    previous_segments = [item['segment_id'] for item in evidence.facts['people']
                         if len(before) == 1 and item['account_id'] == before[0].account_id]
    paid = _verify_paid_people(order, order.points_settlement, previous, evidence.facts, segment_ids=previous_segments)
    if (not isinstance(expected, dict) or set(expected) != {field.name for field in fields(FollowupFundingPreview)}
            or any(type(value) is not int or value < 0 for value in expected.values())
            or len(before) != 1 or len(after) != 1 or len(paid) < expected['prior_paid'] or len(paid) > 1
            or expected.get('prior_settled_account_id') != before[0].account_id
            or expected.get('account_id') != after[0].account_id
            or expected.get('refund_units') != current_amount
            or expected.get('pending_deduction') != after[0].pending_deduction
            or expected.get('wallet_deduction') != after[0].wallet_deduction
            or expected.get('deposit_deduction') != after[0].deposit_deduction
            or expected.get('retained_main_income') != after[0].retained_income
            or expected.get('insufficient') != 0):
        raise ValidationError('追加退款承担或已结算陪玩凭证不一致')
    recorded_allocations = current.refund.negotiated_allocation if current.refund_id else current.zero_allocation
    prior_allocations = [row for row in recorded_allocations if row.get('account_id') == before[0].account_id]
    target_allocations = [row for row in recorded_allocations if row.get('account_id') == after[0].account_id]
    if (len(prior_allocations) != 1 or len(target_allocations) != 1
            or prior_allocations[0].get('settled') is not bool(expected['prior_paid'])
            or target_allocations[0].get('settled') is not False):
        raise ValidationError('追加退款时的陪玩结算快照不一致')
    refund_ids = {item for item in (previous.refund_id, current.refund_id) if item}
    cumulative_amount = prior_amount + current_amount
    expected_status = (order.PaymentStatus.PAID if not cumulative_amount else order.PaymentStatus.REFUNDED
                       if cumulative_amount == order.amount else order.PaymentStatus.PARTIALLY_REFUNDED)
    if (order.payment_status != expected_status or bool(order.refunded_at) != bool(cumulative_amount)
            or set(order.refund_records.values_list('pk', flat=True)) != refund_ids
            or sum(order.funding_allocations.values_list('refunded_amount', flat=True)) !=
               cumulative_amount):
        raise ValidationError('追加退款资金来源不一致')
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    allocations = list(order.funding_allocations.select_for_update().order_by('pk'))
    parts = [RefundablePart(row.pk, row.kind, row.amount) for row in allocations]
    try:
        prior = plan_refund(parts, prior_amount)
        cumulative = plan_refund(parts, prior_amount + current_amount)
    except FundingPlanError as exc:
        raise ValidationError('追加退款资金来源待核对') from exc
    if any(row.refunded_amount != cumulative[row.pk] for row in allocations):
        raise ValidationError('追加退款资金来源与原付款不一致')
    for kind, key in (('PREPAID', 'returned_principal'), ('GIFT', 'returned_gift'),
                      ('COMPENSATION', 'returned_compensation')):
        if expected[key] != sum(cumulative[row.pk] - prior[row.pk] for row in allocations if row.kind == kind):
            raise ValidationError('追加退款的原支付来源分配不一致')
    combined = evidence.facts['combined']
    provider_income = combined['provider_income_units'] - after[0].pending_deduction
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    inviter = (order.amount - prior_amount - current_amount) * rate // 100
    margin = (combined['net_principal_units'] - expected['returned_principal']
              + combined['planned_provider_external_units'] + current_recovered
              - provider_income - inviter)
    rounding = previous.rounding_to_store_units + current.rounding_to_store_units
    required = max(0, rounding - margin)
    store = margin + required
    allocated = sum(amount for _, amount in evidence.facts['funding_uses'])
    if (expected['provider_income'] != provider_income or expected['inviter_income'] != inviter
            or expected['store_income'] != store or expected['required_funding'] != required
            or expected['allocated_funding'] != allocated
            or expected['funding_to_release'] != max(allocated - required, 0)
            or expected['funding_still_needed'] != max(required - allocated, 0)):
        raise ValidationError('追加退款后的整单资金计划不一致')
    return link
