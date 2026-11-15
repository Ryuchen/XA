"""Internal atomic accounting for a second scoped negotiation.

This is not a public operation. A caller must hold an enclosing transaction and
complete service termination, audit and chain-aware settlement integration
before the transaction can be committed. The integration gate is test-only.
"""
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from club_accounts.models import LegacyAccountMap
from wallet.models import Transaction, Wallet
from .financial_lock import lock_order_money
from .models import (NegotiationExecution, NegotiationExecutionShare, NegotiationFollowupLink,
                     NegotiationProposal, OrderRefund)
from .negotiation_execution import _allocation_hash, verify_execution
from .negotiation_followup import _preview_followup_locked, followup_enabled
from .negotiation_proposals import _decisions, _lock_case, _require_followup_zero_enabled, _verified, enabled
from .refunds import authorized_refund, request_fingerprint

FOLLOWUP_MARKER = 'negotiation_followup_v1'


def marked_followup_id(order):
    """Read the persisted chain marker, not the caller's possibly stale order."""
    from .models import Order
    terms = Order.objects.filter(pk=order.pk).values_list('funding_terms', flat=True).first()
    if not isinstance(terms, dict) or FOLLOWUP_MARKER not in terms:
        return None
    marker = terms[FOLLOWUP_MARKER]
    if (not isinstance(marker, dict) or set(marker) != {'link_id'}
            or type(marker['link_id']) is not int or marker['link_id'] <= 0):
        raise ValidationError('追加协商标记待核对')
    return marker['link_id']


def verify_followup_accounting(link):
    """Reconcile both accepted receipts, original funding and all actual debits."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('追加协商凭证核对必须在门店事务内执行')
    link = NegotiationFollowupLink.objects.select_for_update().select_related(
        'order', 'previous__proposal__case', 'previous__refund', 'execution__proposal__case',
        'execution__refund').get(pk=link.pk)
    previous, current, order = link.previous, link.execution, link.order
    def receipt_order(receipt):
        return receipt.zero_order_id if receipt.zero_order_id else receipt.refund.order_id
    if (previous.pk == current.pk or receipt_order(previous) != order.pk or receipt_order(current) != order.pk
            or previous.proposal.case_id != current.proposal.case_id
            or previous.proposal.case.order_id != order.pk or current.proposal.case.order_id != order.pk
            or not previous.termination_processed_at
            or not previous.termination_processed_at <= current.proposal.case_opened_at <= current.accepted_at
            or link.original_amount_units != order.amount or link.previous_terms_hash != previous.terms_hash
            or link.ledger_hash != _allocation_hash(link.projected_ledger)):
        raise ValidationError('追加协商的前后凭证不一致')
    if previous.zero_order_id:
        from .negotiation_zero import verify_zero_execution, verify_zero_termination
        before = verify_zero_execution(previous, _allow_linked_refunds=True)
        verify_zero_termination(previous)
        prior_refund, prior_recovered = 0, 0
    else:
        from .negotiation_termination import verify_termination_evidence
        before = verify_execution(previous)
        verify_termination_evidence(previous)
        prior_refund, prior_recovered = previous.refund.amount, previous.refund.recovered_amount
    if current.zero_order_id:
        from .negotiation_zero import verify_zero_execution
        after = verify_zero_execution(current, _allow_linked_refunds=True)
        current_refund, current_recovered = 0, 0
    else:
        after = verify_execution(current)
        current_refund, current_recovered = current.refund.amount, current.refund.recovered_amount
    if (len(before) != 1 or len(after) != 1 or before[0].account_id == after[0].account_id
            or link.prior_refund_units != prior_refund or prior_refund + current_refund > order.amount):
        raise ValidationError('追加协商的陪玩或退款金额不一致')
    actual_refunds = list(order.refund_records.select_for_update().order_by('pk'))
    expected_ids = {receipt.refund_id for receipt in (previous, current) if receipt.refund_id}
    if {row.pk for row in actual_refunds} != expected_ids or sum(row.amount for row in actual_refunds) != prior_refund + current_refund:
        raise ValidationError('追加协商退款记录不完整')
    payment = list(order.transactions.select_for_update().filter(
        tx_type='PAY', status='SUCCESS', reversal_of__isnull=True).select_related('wallet'))
    if (len(payment) != 1 or payment[0].amount != -order.amount
            or payment[0].wallet.account_id != order.customer_account_id or payment[0].wallet.user_id != order.customer_id
            or payment[0].balance_after != payment[0].balance_before + payment[0].amount
            or payment[0].reversals.exists()):
        raise ValidationError('追加协商原付款凭证不一致')
    customer_refunds = set(order.transactions.select_for_update().filter(
        wallet_id=payment[0].wallet_id, tx_type='REFUND', amount__gt=0,
        status='SUCCESS', reversal_of__isnull=True).values_list('pk', flat=True))
    expected_refunds = {receipt.customer_transaction_id for receipt in (previous, current)
                        if receipt.customer_transaction_id}
    total_refund = prior_refund + current_refund
    status = ('PAID' if total_refund == 0 else 'REFUNDED' if total_refund == order.amount else 'PARTIALLY_REFUNDED')
    if (customer_refunds != expected_refunds or order.payment_status != status
            or (total_refund == 0) != (order.refunded_at is None)):
        raise ValidationError('追加协商付款状态或退款流水不一致')
    referral_rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    if order.inviter_commission != order.amount * referral_rate // 100:
        raise ValidationError('主单推荐佣金快照待核对')
    inviter_income = (order.amount - prior_refund - current_refund) * referral_rate // 100
    store_income = order.shop_income + order.inviter_commission - inviter_income
    expected = {'previous_refund_units':prior_refund, 'previous_recovered_units':prior_recovered,
                'previous_retained_units':before[0].retained_income, 'current_retained_units':after[0].retained_income,
                'current_additional_units':current_recovered, 'store_income_units':store_income,
                'inviter_income_units':inviter_income,
                'rounding_units':previous.rounding_to_store_units + current.rounding_to_store_units,
                'customer_net_units':order.amount - prior_refund - current_refund}
    if 'completion_evidence' in link.projected_ledger:
        expected['completion_evidence'] = link.projected_ledger['completion_evidence']
    if link.projected_ledger != expected or (
            expected['previous_retained_units'] + expected['current_retained_units'] + expected['store_income_units']
            + expected['inviter_income_units'] + expected['rounding_units']
            != expected['customer_net_units'] + prior_recovered + current_recovered):
        raise ValidationError('追加协商账务合计不一致')
    return before[0], after[0]


def _apply_followup(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Write a second receipt inside the caller's full termination transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('追加协商记账必须在完整终止事务内执行')
    if (not followup_enabled() or not enabled() or not authorized_refund(operator)
            or not operator.is_active or not operator.can_login or not isinstance(request_id, str)
            or not 1 <= len(request_id) <= 64 or request_id != request_id.strip()):
        raise ValidationError('追加协商记账权限或操作凭证无效')
    if legacy_operator is not None and not LegacyAccountMap.objects.filter(
            account=operator, legacy_user_id=legacy_operator.pk).exists():
        raise ValidationError('经办人的业务账号与历史身份不一致')
    order, case = _lock_case(case_id)
    lock_order_money()
    existing = NegotiationFollowupLink.objects.select_for_update().filter(execution__proposal_id=proposal_id,
                                                                            order=order).first()
    if existing:
        current = existing.execution
        existing_key = current.zero_request_id if current.zero_order_id else current.refund.client_request_id
        if current.operator_id != operator.pk or existing_key != request_id:
            raise ValidationError('该方案已处理，请核对原操作')
        verify_followup_accounting(existing)
        return existing
    if (OrderRefund.objects.filter(order=order, client_request_id=request_id).exists()
            or NegotiationExecution.objects.filter(zero_order=order, zero_request_id=request_id).exists()):
        raise ValidationError('操作凭证已用于其他协商处理')
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    if not proposal or not latest or proposal.pk != latest.pk:
        raise ValidationError('协商方案已变化，请重新核对')
    _verified(case, proposal)
    _require_followup_zero_enabled(order, proposal.refund_units)
    _decisions(case, proposal, lock=True)
    if proposal.status != 'ACCEPTED':
        raise ValidationError('追加协商方案尚未获得双方同意')
    plan = _preview_followup_locked(case.pk, refund_units=proposal.refund_units)
    if plan['provider_terms'] != proposal.provider_terms or proposal.affected_accounts != [plan['provider_terms'][0]['account_id']]:
        raise ValidationError('陪玩承担或保留收益已变化，请重新确认')
    previous = NegotiationExecution.objects.select_for_update().get(pk=plan['prior_execution_id'])
    term = plan['provider_terms'][0]
    assignment = order.providers.select_for_update().get(provider_account_id=term['account_id'])
    from users.models import EscortProfile
    profile = EscortProfile.objects.select_for_update().get(account_id=term['account_id'], user_id=assignment.provider_id)
    wallets = list(Wallet.objects.select_for_update().filter(
        Q(account_id=term['account_id']) | Q(user_id=assignment.provider_id)).order_by('pk'))
    if (len(wallets) > 1 or any(not item.is_active or item.balance < 0
            or item.account_id not in (None, term['account_id'])
            or item.user_id not in (None, assignment.provider_id) for item in wallets) or profile.deposit_paid < 0):
        raise ValidationError('陪玩钱包或押金归属待核对')
    wallet = None
    if wallets:
        from wallet.services import get_wallet, WalletAddressingError
        try:
            wallet = get_wallet(account_id=term['account_id'], user_id=assignment.provider_id,
                                for_update=True, create=False)
        except WalletAddressingError as exc:
            raise ValidationError('陪玩钱包身份待核对') from exc
    pending = min(term['burden_units'], term['original_income_units'])
    excess = term['burden_units'] - pending
    wallet_part = min(excess, wallet.balance if wallet else 0)
    deposit_part = min(excess - wallet_part, profile.deposit_paid)
    if excess != wallet_part + deposit_part:
        raise ValidationError('陪玩待结算收益、可用余额及押金不足，本次不能退款')
    if deposit_part:
        from wallet.refund_deposit import enabled as deposit_enabled
        if not deposit_enabled():
            raise ValidationError('退款押金处理尚未启用')
    allocations = []
    for row in order.providers.select_for_update().order_by('pk'):
        own = row.pk == assignment.pk
        allocations.append({'key':f'provider:{row.pk}', 'role':'PROVIDER', 'account_id':row.provider_account_id,
            'user_id':row.provider_id, 'share_id':row.pk, 'entitlement':row.provider_income,
            'amount':term['burden_units'] if own else 0,
            'retained':term['retained_units'] if own else row.provider_income, 'settled':bool(row.settled_at)})
    refund = customer_tx = wallet_tx = deficit = None
    if proposal.refund_units:
        payment = order.transactions.select_for_update().select_related('wallet').get(
            tx_type='PAY', status='SUCCESS', reversal_of__isnull=True)
        customer = Wallet.objects.select_for_update().get(pk=payment.wallet_id)
        if not customer.is_active or customer.balance + proposal.refund_units > 2147483647:
            raise ValidationError('老板钱包不可用或余额超出范围')
        from engagement.refunds import lock_refund_vip_operation
        vip_operation = lock_refund_vip_operation()
        refund = OrderRefund.objects.create(order=order, amount=proposal.refund_units, reason=proposal.explanation,
            operator=legacy_operator, operator_account=operator, client_request_id=request_id,
            request_fingerprint=request_fingerprint(proposal.refund_units, proposal.explanation,
                                                    {'proposal_id':proposal.pk, 'terms_hash':proposal.terms_hash}),
            negotiated_allocation=allocations)
        before = customer.balance; customer.balance += proposal.refund_units; customer.save(update_fields=['balance'])
        customer_tx = Transaction.objects.create(wallet=customer, order=order, tx_type='REFUND',
            amount=proposal.refund_units, balance_before=before, balance_after=customer.balance,
            operator=legacy_operator, operator_account=operator, remark=f'协商退款：{order.order_no}')
        from engagement.funding_wallet import refund_locked_order_funding
        refund_locked_order_funding(order, customer, proposal.refund_units)
        from engagement.services import reverse_order_points
        from engagement.refunds import reverse_refund_benefits
        reverse_order_points(order, refund=refund)
        refund.benefit_effects = reverse_refund_benefits(order, refund, operator, vip_operation=vip_operation)
        refund.benefit_effects['negotiation_execution_version'] = 1
        if wallet_part:
            before = wallet.balance; wallet.balance -= wallet_part; wallet.save(update_fields=['balance'])
            wallet_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND', amount=-wallet_part,
                balance_before=before, balance_after=wallet.balance, operator=legacy_operator, operator_account=operator,
                remark=f'协商退款承担：{order.order_no}')
        if deposit_part:
            from wallet.refund_deposit import charge_refund_deposit
            deficit = charge_refund_deposit(refund=refund, profile=profile, amount=deposit_part,
                                            operator=legacy_operator, operator_account=operator)
        refund.recovered_amount = wallet_part + deposit_part
        refund.save(update_fields=['recovered_amount', 'benefit_effects'])
    current = NegotiationExecution.objects.create(proposal=proposal, refund=refund, customer_transaction=customer_tx,
        zero_order=None if refund else order, zero_request_id=None if refund else request_id,
        zero_allocation=[] if refund else allocations, operator=operator, terms_hash=proposal.terms_hash,
        allocation_hash=_allocation_hash(allocations), accepted_at=proposal.accepted_at,
        rounding_to_store_units=plan['rounding_to_store_units'])
    NegotiationExecutionShare.objects.create(execution=current, assignment=assignment, account_id=term['account_id'],
        original_income=term['original_income_units'], burden=term['burden_units'], pending_deduction=pending,
        wallet_deduction=wallet_part, deposit_deduction=deposit_part, retained_income=term['retained_units'],
        wallet_transaction=wallet_tx, deposit_deficit=deficit)
    if refund:
        from django.utils import timezone
        cumulative = plan['projected_ledger']['previous_refund_units'] + refund.amount
        order.payment_status = order.PaymentStatus.REFUNDED if cumulative == order.amount else order.PaymentStatus.PARTIALLY_REFUNDED
        order.refunded_at = timezone.now(); order.save(update_fields=['payment_status', 'refunded_at', 'updated_at'])
        if cumulative == order.amount:
            from coupons.benefits import restore_order_coupons
            from coupons.models import UserCoupon
            coupons = list(UserCoupon.objects.select_for_update().filter(order=order, status=UserCoupon.Status.USED)
                           .select_related('coupon').order_by('pk'))
            restore_order_coupons(order)
            if coupons:
                refund.benefit_effects['order_coupon_results'] = [{'name':row.coupon.name,
                    'outcome':'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'} for row in coupons]
                refund.save(update_fields=['benefit_effects'])
    projected = dict(plan['projected_ledger'])
    if 'completion_evidence' in plan:
        projected['completion_evidence'] = plan['completion_evidence']
    link = NegotiationFollowupLink.objects.create(order=order, previous=previous, execution=current,
        prior_refund_units=plan['projected_ledger']['previous_refund_units'], original_amount_units=order.amount,
        previous_terms_hash=previous.terms_hash, projected_ledger=projected,
        ledger_hash=_allocation_hash(projected))
    verify_followup_accounting(link)
    return link


def verify_followup_termination(link):
    """Check the scoped completion and transactional audit of the second episode."""
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime
    from console.models import AdminAuditLog
    from .models import AfterSale, Order, OrderStatusLog
    link = NegotiationFollowupLink.objects.select_related('order', 'execution__proposal', 'execution__refund').get(pk=link.pk)
    order, current = link.order, link.execution
    if not isinstance(link.projected_ledger, dict):
        raise ValidationError('追加协商账务快照待核对')
    completion = link.projected_ledger.get('completion_evidence')
    if completion is not None:
        if (not isinstance(completion, dict) or set(completion) != {'completed_at', 'completion_log_id'}
                or type(completion['completion_log_id']) is not int or not order.completed_at
                or completion['completed_at'] != order.completed_at.isoformat()
                or order.completed_at > current.accepted_at or current.completed_by_negotiation):
            raise ValidationError('原完成时间或协商快照待核对')
        logs = list(order.status_logs.filter(action=OrderStatusLog.Action.COMPLETE,
            from_status=Order.Status.IN_SERVICE, to_status=Order.Status.COMPLETED))
        if (len(logs) != 1 or logs[0].pk != completion['completion_log_id']
                or not order.completed_at <= logs[0].created_at <= current.accepted_at):
            raise ValidationError('原订单完成记录待核对')
        shares = list(current.shares.select_related('assignment'))
        if len(shares) != 1 or shares[0].assignment.released_at != order.completed_at:
            raise ValidationError('完成后的陪玩履约记录待核对')
    elif not current.completed_by_negotiation or order.completed_at != current.accepted_at:
        raise ValidationError('追加协商终止凭证待核对')
    if (marked_followup_id(order) != link.pk or not current.termination_processed_at
            or current.termination_processed_at < current.accepted_at or order.status != Order.Status.COMPLETED):
        raise ValidationError('追加协商终止凭证待核对')
    amount = current.refund.amount if current.refund_id else 0
    status = ('RESOLVED' if not amount else 'REFUNDED'
              if link.prior_refund_units + amount == order.amount else 'PARTIALLY_REFUNDED')
    case = AfterSale.objects.get(pk=current.proposal.case_id)
    if not isinstance(case.history, list):
        raise ValidationError('追加协商售后记录待核对')
    entries = [row for row in case.history if isinstance(row, dict) and row.get('status') == status
               and row.get('created_at') == current.proposal.case_opened_at.isoformat()
               and row.get('client_request_id') == current.proposal.case_request_id]
    try:
        resolved_at = parse_datetime(entries[0].get('resolved_at') or '') if len(entries) == 1 else None
    except (ValueError, TypeError):
        resolved_at = None
    if (len(entries) != 1 or not resolved_at or not timezone.is_aware(resolved_at)
            or not current.accepted_at <= resolved_at <= current.termination_processed_at
            or entries[0].get('operator_id') != current.operator_id
            or entries[0].get('resolution') != current.proposal.explanation
            or entries[0].get('affected_provider_account_ids') != current.proposal.affected_accounts):
        raise ValidationError('追加协商售后记录待核对')
    request_id = current.refund.client_request_id if current.refund_id else current.zero_request_id
    expected = {'proposal_id':current.proposal_id, 'execution_id':current.pk, 'link_id':link.pk,
                'previous_execution_id':link.previous_id, 'refund_units':amount, 'client_request_id':request_id}
    path = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
    audit = [row for row in AdminAuditLog.objects.filter(path=path, resource='aftersales', object_id=str(case.pk))
             if isinstance(row.request_body, dict) and row.request_body.get('link_id') == link.pk]
    if (len(audit) != 1 or audit[0].operator_account_id != current.operator_id or audit[0].method != 'POST'
            or audit[0].status_code != 200 or audit[0].request_body != expected
            or (current.refund_id and audit[0].operator_id != current.refund.operator_id)
            or (audit[0].operator_id and not LegacyAccountMap.objects.filter(
                account_id=current.operator_id, legacy_user_id=audit[0].operator_id).exists())):
        raise ValidationError('追加协商审计凭证待核对')


@transaction.atomic
def terminate_followup_internal(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Post and terminate together through the test-gated staff execution flow."""
    from django.utils import timezone
    from .models import Order, OrderParticipationSegment
    order, case = _lock_case(case_id)
    if order.status != Order.Status.IN_SERVICE and marked_followup_id(order) is None:
        from .negotiation_followup import completed_followup_execution_enabled
        if order.status != Order.Status.COMPLETED or not completed_followup_execution_enabled():
            raise ValidationError('已完成订单的追加退款执行尚未接通，请联系客服核对')
    link = _apply_followup(case_id, proposal_id, operator=operator,
                           legacy_operator=legacy_operator, request_id=request_id)
    current = NegotiationExecution.objects.select_for_update().get(pk=link.execution_id)
    if current.termination_processed_at:
        verify_followup_termination(link)
        return link
    completed_before = link.projected_ledger.get('completion_evidence') is not None
    if (marked_followup_id(order) is not None or case.status != 'OPEN'
            or order.status != (Order.Status.COMPLETED if completed_before else Order.Status.IN_SERVICE)
            or current.accepted_at > timezone.now() or not order.in_service_at
            or current.accepted_at < order.in_service_at or order.service_clock_version is None
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100):
        raise ValidationError('追加协商终止时间或订单状态待核对')
    shares = list(current.shares.select_related('assignment'))
    active = list(order.providers.select_for_update().filter(released_at__isnull=True))
    if (len(shares) != 1 or len(active) != 1 or active[0].pk != shares[0].assignment_id
            or active[0].settled_at):
        raise ValidationError('当前陪玩履约状态已变化')
    assignment = active[0]
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, provider_account_id=assignment.provider_account_id,
        released_at__isnull=True).order_by('pk'))
    if len(segments) > 1 or any(not row.started_at for row in segments):
        raise ValidationError('当前陪玩履约片段待核对')
    ended_at = order.completed_at if completed_before else current.accepted_at
    if not ended_at or any(segment.started_at > ended_at for segment in segments):
        raise ValidationError('陪玩履约结束时间待核对')
    for segment in segments:
        segment.released_at = ended_at; segment.save(update_fields=['released_at'])
    assignment.released_at = ended_at; assignment.save(update_fields=['released_at'])
    order.funding_terms = {**order.funding_terms, FOLLOWUP_MARKER:{'link_id':link.pk}}
    order.save(update_fields=['funding_terms'])
    from .services import complete_fulfillment, ensure_fulfillment_reports, refresh_escort_status
    if not completed_before:
        from .room_completion_review import invalidate_for_transfer_locked
        invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
        from .state_machine import transition
        ensure_fulfillment_reports(order)
        def finish(item):
            item.completed_at = current.accepted_at
            complete_fulfillment(item, operator=legacy_operator, operator_account=operator)
        order = transition(order.pk, Order.Status.COMPLETED, operator=legacy_operator, operator_account=operator,
            action='COMPLETE', reason='双方确认追加协商终止', side_effect=finish, update_fields=['completed_at'],
            room_negotiation_followup_id=link.pk)
    refresh_escort_status(provider_ids=[assignment.provider_id], account_ids=[assignment.provider_account_id],
                          exclude_order_ids=[order.pk])
    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    amount = current.refund.amount if current.refund_id else 0
    case.status = ('RESOLVED' if not amount else 'REFUNDED'
                   if link.prior_refund_units + amount == order.amount else 'PARTIALLY_REFUNDED')
    case.operator = operator; case.resolution = current.proposal.explanation; case.resolved_at = timezone.now()
    append_history(case)
    from wallet.income_priority_evidence import attach_closure
    attach_closure(case)
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    current.termination_processed_at = timezone.now(); current.completed_by_negotiation = not completed_before
    current.save(update_fields=['termination_processed_at', 'completed_by_negotiation'])
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales', object_id=str(case.pk),
        request_body={'proposal_id':proposal_id, 'execution_id':current.pk, 'link_id':link.pk,
            'previous_execution_id':link.previous_id, 'refund_units':amount, 'client_request_id':request_id}, status_code=200)
    verify_followup_accounting(link)
    verify_followup_termination(link)
    from .notifier import notify_order_update
    from wallet.notifier import notify_wallet_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    wallet_ids = {row for row in (current.customer_transaction.wallet_id if current.customer_transaction_id else None,
                                  shares[0].wallet_transaction.wallet_id if shares[0].wallet_transaction_id else None) if row}
    for wallet in Wallet.objects.filter(pk__in=wallet_ids):
        transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
    return link
