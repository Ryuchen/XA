"""Post one agreed transferred-order refund within its caller's transaction.

The enclosing coordinator must also terminate the case, rebalance receipts and
revise the settlement manifest in the same transaction. This module is not an
API endpoint and cannot commit independently.
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import NegotiationExecution, NegotiationExecutionShare, OrderRefund


def post_locked(order, case, proposal, *, operator, legacy_operator, request_id):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('追加退款必须在完整结案事务内执行')
    from .refunds import authorized_refund, request_fingerprint
    if not authorized_refund(operator) or not operator.is_active or not operator.can_login:
        raise ValidationError('仅有退款权限的有效后台账号可以执行')
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id != request_id.strip():
        raise ValidationError('退款操作凭证无效')
    from .financial_lock import lock_order_money
    lock_order_money()
    from .ordinary_transfer_followup import calculation_locked, verify_draft_locked
    from .negotiation_proposals import _decisions, _verified
    _verified(case, proposal)
    _decisions(case, proposal, lock=True)
    if proposal.affected_segment_ids != case.affected_segment_ids:
        raise ValidationError('受影响履约段已变化，请重新确认')
    from .ordinary_transfer_addon_agreement import applies as addon_applies, verify_locked as verify_addons
    if addon_applies(order):
        verify_addons(proposal, compare_current=True)
    if proposal.status != 'ACCEPTED':
        raise ValidationError('协商方案尚未获得双方同意')
    facts = calculation_locked(order, case, proposal.refund_units)
    round_record = verify_draft_locked(proposal, facts)
    if round_record.execution_id:
        existing = round_record.execution
        key = existing.refund.client_request_id if existing.refund_id else existing.zero_request_id
        if existing.operator_id != operator.pk or key != request_id:
            raise ValidationError('该方案已处理，请核对原退款记录')
        return existing
    if (OrderRefund.objects.filter(order=order, client_request_id=request_id).exists()
            or NegotiationExecution.objects.filter(zero_order=order, zero_request_id=request_id).exists()):
        raise ValidationError('操作凭证已用于其他退款')
    if proposal.provider_terms != facts['provider_terms']:
        raise ValidationError('陪玩收益或退款分摊已变化，请重新确认')
    assignments = {row.provider_account_id: row for row in order.providers.select_for_update().order_by('pk')}
    accounts = [term['account_id'] for term in facts['provider_terms']]
    if not set(accounts).issubset(assignments):
        raise ValidationError('陪玩账号归属已变化')
    if not proposal.refund_units:
        from django.conf import settings
        if not getattr(settings, 'NEGOTIATION_ZERO_EXECUTION_ENABLED', False):
            raise ValidationError('零退款追加协商尚未开放')
        from .negotiation_execution import _allocation_hash
        allocation = []
        for assignment in assignments.values():
            account = assignment.provider_account_id
            amount = facts['outstanding_income'].get(str(account), 0)
            allocation.append({'key': f'provider:{assignment.pk}', 'role': 'PROVIDER', 'account_id': account,
                'user_id': assignment.provider_id, 'share_id': assignment.pk, 'entitlement': amount,
                'amount': 0, 'retained': amount, 'settled': bool(assignment.settled_at)})
        execution = NegotiationExecution.objects.create(proposal=proposal, zero_order=order,
            zero_request_id=request_id, zero_allocation=allocation, operator=operator,
            terms_hash=proposal.terms_hash, allocation_hash=_allocation_hash(allocation),
            accepted_at=proposal.accepted_at, rounding_to_store_units=0)
        for term in facts['provider_terms']:
            NegotiationExecutionShare.objects.create(execution=execution, assignment=assignments[term['account_id']],
                account_id=term['account_id'], original_income=term['original_income_units'], burden=0,
                pending_deduction=0, wallet_deduction=0, deposit_deduction=0,
                retained_income=term['retained_units'])
        round_record.execution = execution
        round_record.save(update_fields=['execution'])
        return execution
    from users.models import EscortProfile
    from wallet.models import Transaction, Wallet
    profiles = {row.account_id: row for row in EscortProfile.objects.select_for_update().filter(
        account_id__in=accounts).order_by('account_id')}
    if len(profiles) != len(accounts) or any(profiles[account].user_id != assignments[account].provider_id
                                             for account in accounts):
        raise ValidationError('陪玩资料待核对')
    payment = order.transactions.select_for_update().select_related('wallet').get(
        tx_type=Transaction.TxType.PAY, status=Transaction.Status.SUCCESS, reversal_of__isnull=True)
    customer = Wallet.objects.select_for_update().get(pk=payment.wallet_id)
    if (not customer.is_active or customer.account_id != order.customer_account_id
            or payment.amount != -order.amount or customer.balance + proposal.refund_units > 2147483647):
        raise ValidationError('老板原付款钱包待核对')
    existing_wallets = list(Wallet.objects.select_for_update().filter(
        Q(account_id__in=accounts) | Q(user_id__in=[assignments[a].provider_id for a in accounts])).order_by('pk'))
    wallets = {}
    for account in accounts:
        candidates = [wallet for wallet in existing_wallets
                      if wallet.account_id == account or wallet.user_id == assignments[account].provider_id]
        if len(candidates) > 1 or any(wallet.account_id != account or wallet.user_id != assignments[account].provider_id
                                      or not wallet.is_active for wallet in candidates):
            raise ValidationError('陪玩钱包身份待核对')
        wallets[account] = candidates[0] if candidates else None
    from engagement.refunds import lock_refund_vip_operation
    vip_operation = lock_refund_vip_operation()
    # The quote was made before wallet locks; recalculate the debit split
    # under the actual profile and wallet locks immediately before posting.
    facts = calculation_locked(order, case, proposal.refund_units)
    verify_draft_locked(proposal, facts)
    funding = {row['account_id']: row for row in facts['provider_funding_preview']['providers']}
    if any(row['shortfall_units'] for row in funding.values()):
        raise ValidationError('陪玩待结算收益、钱包和押金不足，暂不能退款')
    segment_plan = facts['segment_pending_plan']
    if (set(funding) != set(accounts) or any(item['account_id'] not in funding for item in segment_plan)
            or any(sum(item['amount'] for item in segment_plan if item['account_id'] == account)
                   != funding[account]['pending_income_units'] for account in accounts)):
        raise ValidationError('逐段扣减与本人实际待结收益不一致，请重新确认')
    amounts = {row['account_id']: row for row in facts['provider_terms']}
    allocation = []
    for assignment in assignments.values():
        account = assignment.provider_account_id
        term = amounts.get(account)
        prior = facts['outstanding_income'].get(str(account), 0)
        allocation.append({'key': f'provider:{assignment.pk}', 'role': 'PROVIDER', 'account_id': account,
            'user_id': assignment.provider_id, 'share_id': assignment.pk,
            'entitlement': term['original_income_units'] if term else prior,
            'amount': term['burden_units'] if term else 0,
            'retained': term['retained_units'] if term else prior, 'settled': bool(assignment.settled_at)})
    refund = OrderRefund.objects.create(order=order, amount=proposal.refund_units, reason=proposal.explanation,
        operator=legacy_operator, operator_account=operator, client_request_id=request_id,
        request_fingerprint=request_fingerprint(proposal.refund_units, proposal.explanation,
                                                {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash}),
        negotiated_allocation=allocation)
    before = customer.balance
    customer.balance += refund.amount
    customer.save(update_fields=['balance'])
    customer_tx = Transaction.objects.create(wallet=customer, order=order, tx_type=Transaction.TxType.REFUND,
        amount=refund.amount, balance_before=before, balance_after=customer.balance, operator=legacy_operator,
        operator_account=operator, remark=f'协商退款：{order.order_no}')
    from engagement.funding_wallet import refund_locked_order_funding
    refund_locked_order_funding(order, customer, refund.amount)
    from engagement.services import reverse_order_points
    from engagement.refunds import reverse_refund_benefits
    reverse_order_points(order, refund=refund)
    refund.benefit_effects = reverse_refund_benefits(order, refund, operator, vip_operation=vip_operation)
    refund.benefit_effects['negotiation_execution_version'] = 1
    refund.benefit_effects['ordinary_transfer_followup_version'] = 1
    from .negotiation_execution import _allocation_hash
    execution = NegotiationExecution.objects.create(proposal=proposal, refund=refund, customer_transaction=customer_tx,
        operator=operator, terms_hash=proposal.terms_hash, allocation_hash=_allocation_hash(allocation),
        accepted_at=proposal.accepted_at, rounding_to_store_units=facts['rounding_to_store_units'])
    recovered = 0
    for account in accounts:
        term, split = amounts[account], funding[account]
        wallet, wallet_tx, deficit = wallets[account], None, None
        if split['wallet_units']:
            if not wallet or wallet.balance < split['wallet_units']:
                raise ValidationError('陪玩钱包余额已变化')
            before = wallet.balance
            wallet.balance -= split['wallet_units']
            wallet.save(update_fields=['balance'])
            wallet_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type=Transaction.TxType.REFUND,
                amount=-split['wallet_units'], balance_before=before, balance_after=wallet.balance,
                operator=legacy_operator, operator_account=operator, remark=f'协商退款承担：{order.order_no}')
        if split['deposit_units']:
            from wallet.refund_deposit import charge_refund_deposit, enabled as deficit_enabled
            if not deficit_enabled():
                raise ValidationError('退款押金处理尚未启用')
            deficit = charge_refund_deposit(refund=refund, profile=profiles[account], amount=split['deposit_units'],
                                            operator=legacy_operator, operator_account=operator)
        recovered += split['wallet_units'] + split['deposit_units']
        NegotiationExecutionShare.objects.create(execution=execution, assignment=assignments[account], account_id=account,
            original_income=term['original_income_units'], burden=term['burden_units'],
            pending_deduction=split['pending_income_units'], wallet_deduction=split['wallet_units'],
            deposit_deduction=split['deposit_units'], retained_income=term['retained_units'],
            wallet_transaction=wallet_tx, deposit_deficit=deficit)
    refund.recovered_amount = recovered
    refund.save(update_fields=['recovered_amount', 'benefit_effects'])
    cumulative = facts['prior_refund_units'] + refund.amount
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
    round_record.execution = execution
    round_record.save(update_fields=['execution'])
    return execution
