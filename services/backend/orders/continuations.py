"""Verified continuation claims and one-time store-local rewards."""
from functools import wraps
from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from .models import (Order, OrderContinuation, OrderContinuationReward, OrderOriginalShareTransfer,
                     OrderPointsRedemption, OrderPrestartHandoff)


def enabled():
    return getattr(settings, 'ORDER_CONTINUATION_REWARD_ENABLED', False) is True


def _text(value, maximum):
    return isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip()


def _protect_participants(view):
    """Serialize claim and verification with each current provider's exit."""
    @wraps(view)
    def wrapped(self, request, *args, **kwargs):
        if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            return view(self, request, *args, **kwargs)
        from club_accounts.models import ClubAccount
        from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                       lock_provider_operations)
        order_id = kwargs.get('order_id')
        if order_id is None:
            order_id = OrderContinuation.objects.filter(pk=kwargs.get('claim_id')).values_list(
                'continuation_order_id', flat=True).first()
        from .models import OrderProvider
        members = list(OrderProvider.objects.filter(order_id=order_id, released_at__isnull=True)
                       .order_by('provider_account_id').values_list('provider_account_id', 'provider_business_cycle'))
        if any(not account_id or not cycle for account_id, cycle in members):
            return Response({'code': 409, 'msg': '续单陪玩所属周期待核对'}, status=409)
        ids = {account_id for account_id, _ in members}
        accounts = list(ClubAccount.objects.filter(pk__in=ids, account_type='PROVIDER').order_by('pk'))
        if len(accounts) != len(ids):
            return Response({'code': 409, 'msg': '续单陪玩账号待核对'}, status=409)
        try:
            for account in accounts:
                ensure_provider_lifecycle(account)
            with transaction.atomic():
                lock_provider_operations(accounts)
                request._continuation_participants = (order_id, members)
                return view(self, request, *args, **kwargs)
        except ProviderLifecycleError:
            return Response({'code': 503, 'msg': '续单陪玩账号状态暂不可核验'}, status=503)
    return wrapped


def _participants_current(request, order):
    captured = getattr(request, '_continuation_participants', None)
    if captured is None:
        return True
    from club_accounts.models import ClubAccount
    members = list(order.providers.filter(released_at__isnull=True).order_by('provider_account_id')
                   .values_list('provider_account_id', 'provider_business_cycle'))
    if captured != (order.pk, members):
        return False
    for account_id, cycle in members:
        if not ClubAccount.objects.filter(pk=account_id, account_type='PROVIDER',
                business_cycle=cycle, license_membership_active=True).exists():
            return False
    return True


def _serialize(row):
    rewards = list(row.rewards.select_related('transaction__wallet').order_by('pk'))
    reward_data = []
    for reward in rewards:
        tx = reward.transaction
        people = [item for item in (row.participants if isinstance(row.participants, list) else [])
                  if isinstance(item, dict) and item.get('provider_account_id') == reward.provider_id]
        valid = bool(tx.order_id == row.continuation_order_id and tx.wallet.account_id == reward.provider_id
            and len(people) == 1 and people[0].get('provider_business_cycle') == reward.provider_business_cycle
            and tx.wallet.user_id == people[0].get('provider_id')
            and tx.tx_type == 'REWARD' and tx.status == 'SUCCESS' and tx.amount == reward.amount == 500
            and not tx.reversal_of_id and not tx.reversals.exists()
            and tx.balance_after == tx.balance_before + tx.amount)
        reward_data.append({'provider_id': reward.provider_id, 'amount': reward.amount if valid else None,
            'transaction_id': tx.pk if valid else None, 'created_at': reward.created_at,
            'evidence_status': 'VERIFIED' if valid else 'REVIEW',
            'notice_status': ('SENT' if reward.notice_message_id else
                              'SKIPPED' if reward.notice_completed_at else
                              'REVIEW' if reward.notice_error else 'PENDING'),
            'notice_error': reward.notice_error})
    state = row.reward_state
    if state == 'PAID' and (not isinstance(row.participants, list) or len(reward_data) != len(row.participants)
                            or not reward_data or any(item['evidence_status'] != 'VERIFIED' for item in reward_data)):
        state = 'REVIEW'
    descriptions = {'PENDING': '等待续单结算和奖励核验', 'PAID': '已核对奖励钱包流水',
                    'INELIGIBLE': row.reward_review_reason or '续单不满足奖励条件',
                    'REVIEW': '奖励或收款凭证待客服核对'}
    return {'id': row.pk, 'original_order_id': row.original_order_id,
            'continuation_order_id': row.continuation_order_id, 'status': row.status,
            'submitted_at': row.submitted_at, 'verified_at': row.verified_at,
            'verified_by': row.verified_by_id, 'verification_note': row.verification_note,
            'reward_state': state, 'reward_state_reason': descriptions.get(state, '奖励状态待核对'),
            'reward_review_reason': row.reward_review_reason,
            'participants': row.participants,
            'rewards': reward_data}


def _orders_valid(original, current, customer):
    return bool(original and current and original.pk != current.pk and not original.is_gift_snapshot
        and not current.is_gift_snapshot and not original.source_order_id and not current.source_order_id
        and original.customer_account_id == current.customer_account_id == customer.pk
        and original.business_cycle == current.business_cycle == customer.business_cycle
        and original.created_at < current.created_at)


def _public_result(row, account):
    result = _serialize(row)
    result.pop('reward_review_reason', None)
    for reward in result['rewards']:
        reward.pop('notice_error', None)
    if account.account_type == 'PROVIDER':
        result['participants'] = None
        result['rewards'] = [item for item in result['rewards'] if item['provider_id'] == account.pk]
    return result


class ContinuationClaimView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @_protect_participants
    @transaction.atomic
    def post(self, request, order_id):
        if not enabled():
            return Response({'code': 503, 'msg': '续单核实暂未开放'}, status=503)
        account = request.account
        if account.account_type != 'BOSS':
            return Response({'code': 403, 'msg': '仅老板可提交续单关联'}, status=403)
        data = request.data
        if (not isinstance(data, dict) or set(data) != {'original_order_id', 'client_request_id'}
                or type(data['original_order_id']) is not int or data['original_order_id'] <= 0
                or not _text(data['client_request_id'], 64)):
            return Response({'code': 400, 'msg': '请选择原订单并填写有效请求编号'}, status=400)
        from club_accounts.models import ClubAccount
        account = ClubAccount.objects.select_for_update().get(pk=account.pk)
        orders = {o.pk: o for o in Order.objects.select_for_update().filter(
            pk__in=[order_id, data['original_order_id']]).order_by('pk')}
        original, current = orders.get(data['original_order_id']), orders.get(order_id)
        if not _orders_valid(original, current, account):
            return Response({'code': 404, 'msg': '原订单或续单不属于当前账号周期'}, status=404)
        if not _participants_current(request, current):
            return Response({'code': 409, 'msg': '续单陪玩账号或参与人已变化，请刷新后重试'}, status=409)
        key = data['client_request_id']
        prior = OrderContinuation.objects.filter(customer=account, business_cycle=account.business_cycle,
                                                  request_id=key).first()
        if prior:
            if prior.original_order_id != original.pk or prior.continuation_order_id != current.pk:
                return Response({'code': 409, 'msg': '请求编号已用于其他续单'}, status=409)
            return Response({'code': 0, 'data': _public_result(prior, account)})
        if OrderContinuation.objects.filter(continuation_order=current).exists():
            return Response({'code': 409, 'msg': '该续单已有核实申请'}, status=409)
        row = OrderContinuation.objects.create(original_order=original, continuation_order=current,
            customer=account, business_cycle=account.business_cycle, request_id=key)
        return Response({'code': 0, 'data': _public_result(row, account), 'msg': '已提交客服核实'})

    def get(self, request, order_id):
        account = request.account
        rows = OrderContinuation.objects.filter(continuation_order_id=order_id)
        if account.account_type == 'BOSS':
            rows = rows.filter(customer=account, business_cycle=account.business_cycle)
        elif account.account_type == 'PROVIDER':
            rows = rows.filter(continuation_order__providers__provider_account=account,
                               continuation_order__providers__provider_business_cycle=account.business_cycle).distinct()
        else:
            return Response({'code': 403, 'msg': '无权查看'}, status=403)
        row = rows.first()
        result = _public_result(row, account) if row else None
        return Response({'code': 0, 'data': result})


class ContinuationReviewView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, claim_id=None):
        from .room_objections import _authorized
        if not enabled():
            return Response({'code': 503, 'msg': '续单核实暂未开放'}, status=503)
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权查看续单'}, status=403)
        rows = OrderContinuation.objects.all().order_by('-pk')
        if claim_id is not None:
            row = rows.filter(pk=claim_id).first()
            return Response({'code': 0, 'data': _serialize(row)}) if row else Response({'code': 404, 'msg': '续单申请不存在'}, status=404)
        status = request.query_params.get('status')
        if status is not None:
            if status not in ('PENDING', 'VERIFIED', 'REJECTED'):
                return Response({'code': 400, 'msg': '续单申请状态筛选无效'}, status=400)
            rows = rows.filter(status=status)
        from console.pagination import ConsolePagination
        paginator = ConsolePagination()
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response([_serialize(row) for row in page])

    @_protect_participants
    @transaction.atomic
    def post(self, request, claim_id):
        from .room_objections import _authorized
        if not enabled():
            return Response({'code': 503, 'msg': '续单核实暂未开放'}, status=503)
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权核实续单'}, status=403)
        data = request.data
        if (not isinstance(data, dict) or set(data) != {'decision', 'note', 'client_request_id'}
                or data['decision'] not in ('VERIFIED', 'REJECTED') or not _text(data['note'], 1000)
                or not _text(data['client_request_id'], 64)):
            return Response({'code': 400, 'msg': '请填写核实结果和说明'}, status=400)
        hint = OrderContinuation.objects.filter(pk=claim_id).values('original_order_id', 'continuation_order_id').first()
        if not hint:
            return Response({'code': 404, 'msg': '续单申请不存在'}, status=404)
        orders = {o.pk: o for o in Order.objects.select_for_update().filter(pk__in=hint.values()).order_by('pk')}
        row = OrderContinuation.objects.select_for_update().select_related('customer').get(pk=claim_id)
        if row.status != 'PENDING':
            if (row.status == data['decision'] and row.verified_by_id == request.account.pk
                    and row.verification_note == data['note'] and row.verification_request_id == data['client_request_id']):
                return Response({'code': 0, 'data': _serialize(row)})
            return Response({'code': 409, 'msg': '该申请已核实'}, status=409)
        original, current = orders.get(row.original_order_id), orders.get(row.continuation_order_id)
        if not _orders_valid(original, current, row.customer):
            return Response({'code': 409, 'msg': '续单账号或订单事实已变化'}, status=409)
        if not _participants_current(request, current):
            return Response({'code': 409, 'msg': '续单陪玩账号或参与人已变化，请刷新后重试'}, status=409)
        people = list(current.providers.filter(released_at__isnull=True).order_by('provider_account_id')
                      .values('provider_account_id', 'provider_id', 'provider_business_cycle'))
        if data['decision'] == 'VERIFIED' and (len(people) != current.required_provider_count
                or any(not p['provider_account_id'] or not p['provider_id'] or not p['provider_business_cycle'] for p in people)
                or len({p['provider_account_id'] for p in people}) != len(people)):
            return Response({'code': 409, 'msg': '续单陪玩或所属周期待核对'}, status=409)
        row.status, row.verified_by, row.verified_at = data['decision'], request.account, timezone.now()
        row.verification_note, row.verification_request_id = data['note'], data['client_request_id']
        row.participants = people if row.status == 'VERIFIED' else None
        row.save(update_fields=['status', 'verified_by', 'verified_at', 'verification_note',
                                'verification_request_id', 'participants'])
        return Response({'code': 0, 'data': _serialize(row)})


def award_one(claim_id):
    """Pay only an evidenced, settled and unrefunded continuation in this store."""
    from club_accounts.models import ClubAccount
    from wallet.models import Transaction
    from wallet.services import get_wallet
    from .financial_lock import lock_order_money
    if not enabled():
        return False
    hint = OrderContinuation.objects.filter(pk=claim_id, status='VERIFIED').values('continuation_order_id', 'participants').first()
    if not hint:
        return False
    if not isinstance(hint['participants'], list) or not hint['participants']:
        raise ValueError('续单已核实参与人快照缺失')
    try:
        ids = sorted({item['provider_account_id'] for item in hint['participants']})
    except (KeyError, TypeError) as exc:
        raise ValueError('续单已核实参与人快照异常') from exc
    if any(type(account_id) is not int or account_id <= 0 for account_id in ids):
        raise ValueError('续单已核实陪玩账号异常')
    accounts = list(ClubAccount.objects.filter(pk__in=ids, account_type='PROVIDER').order_by('pk'))
    if len(accounts) != len(ids):
        raise ValueError('续单奖励原陪玩账号缺失，请客服核对')
    lifecycle = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    if lifecycle:
        from club_accounts.provider_lifecycle import ensure_provider_lifecycle
        for account in accounts:
            ensure_provider_lifecycle(account)
    with transaction.atomic():
        if lifecycle:
            from club_accounts.provider_lifecycle import lock_provider_operations
            lock_provider_operations(accounts)
        order = Order.objects.select_for_update().get(pk=hint['continuation_order_id'])
        row = OrderContinuation.objects.select_for_update().get(pk=claim_id, continuation_order=order)
        if row.status != 'VERIFIED' or row.participants != hint['participants']:
            return False
        if row.rewards.exists():
            # A committed payout writes all participants in the same transaction.
            if row.rewards.count() != len(ids):
                raise ValueError('续单奖励记录不完整，请核对')
            return False
        # Add-on refunds and rewards both serialize on the same order row.
        # With no reward receipt under that lock, an independently verified
        # refund is necessarily earlier than any future reward payment.
        if order.addon_refunds.exists():
            lock_order_money()
            from .addon_payment import verify_receipt
            from .models import OrderAddonPayment
            payments = list(OrderAddonPayment.objects.select_for_update().filter(
                refund_records__order=order).select_related('intent', 'buyer_transaction__wallet').distinct())
            if not payments or order.addon_refunds.exclude(payment_id__in=[payment.pk for payment in payments]).exists():
                raise ValueError('续单加购退款来源待核对')
            for payment in payments:
                verify_receipt(payment)
            row.reward_state, row.reward_review_reason = 'INELIGIBLE', '发奖前加购已退款'
            row.save(update_fields=['reward_state', 'reward_review_reason'])
            return False
        if (order.status == 'CANCELLED' or order.refund_records.exists()
                or order.status_logs.filter(action__in=['TRANSFER', 'REFUND', 'CANCEL']).exists()
                or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
                or OrderPrestartHandoff.objects.filter(order=order).exists()):
            row.reward_state, row.reward_review_reason = 'INELIGIBLE', '发奖前本次续单已退款、取消或转单'
            row.save(update_fields=['reward_state', 'reward_review_reason'])
            return False
        if order.status != 'COMPLETED' or order.payment_status != 'PAID' or not order.settled_at:
            return False
        # An unpurchased add-on is not part of the continuation's settlement.
        # It may still exist as an intent, so retain that fact separately for
        # the points service's capability context below.
        from .addon_settlement import has_paid_addons
        has_addons = has_paid_addons(order)
        current = list(order.providers.order_by('provider_account_id')
                       .values('provider_account_id', 'provider_id', 'provider_business_cycle'))
        if current != row.participants or len(current) != order.required_provider_count:
            raise ValueError('续单参与人已变化，请核对')
        lock_order_money()
        from .points_service_payment import needs_integrated_processing
        receipt = OrderPointsRedemption.objects.filter(order=order).first()
        if not receipt and needs_integrated_processing(order):
            raise ValueError('续单积分付款凭据缺失')
        if receipt:
            if not needs_integrated_processing(order):
                raise ValueError('续单积分付款凭据待核对')
            from .points_business_access import service_order
            from .points_funding_plan import plan_verified_order
            from .points_funding_evidence import verify_order_funding
            with service_order(order.pk, phase='settlement', addon=has_addons):
                from .points_service_refund import _plan
                _plan(order, None, None, history_only=True)
                plan_verified_order(order.pk, allow_finalized=True)
                funding = verify_order_funding(order.pk, allow_finalized=True)
                if not funding.fully_funded:
                    raise ValueError('续单补资待核对')
                if has_addons:
                    from .points_settlement_test import verified_order_settlement_display
                    if verified_order_settlement_display(order) != 'SETTLED':
                        raise ValueError('续单加购结算凭证待核对')
            deal = receipt.deal_units
        else:
            from .models import OrderAddonPayment
            addon_tx_ids = OrderAddonPayment.objects.filter(intent__order=order).values('buyer_transaction_id')
            payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', reversal_of__isnull=True)
                            .exclude(pk__in=addon_tx_ids))
            if (len(payments) != 1 or payments[0].amount != -order.amount
                    or payments[0].wallet.account_id != order.customer_account_id
                    or payments[0].balance_after != payments[0].balance_before + payments[0].amount
                    or payments[0].reversals.exists()):
                raise ValueError('续单付款凭据待核对')
            deal = order.amount
            if has_addons:
                from .points_settlement_test import verified_order_settlement_display
                if verified_order_settlement_display(order) != 'SETTLED':
                    raise ValueError('续单加购结算凭证待核对')
        if deal <= 18800:
            row.reward_state, row.reward_review_reason = 'INELIGIBLE', '续单成交额未超过 188 元'
            row.save(update_fields=['reward_state', 'reward_review_reason'])
            return False
        locked_accounts = {a.pk: a for a in ClubAccount.objects.select_for_update().filter(pk__in=ids).order_by('pk')}
        from .models import OrderPointsSettlement
        settlement = OrderPointsSettlement.objects.filter(order=order).first()
        if settlement and settlement.finalized_at != order.settled_at:
            raise ValueError('续单整单结算事实待核对')
        for item in current:
            account = locked_accounts.get(item['provider_account_id'])
            if (not account or account.business_cycle != item['provider_business_cycle']
                    or not account.license_membership_active):
                raise ValueError('续单奖励陪玩业务周期待核对')
            assignment = order.providers.get(provider_account_id=account.pk)
            if settlement:
                # The current service settlement records each person in its
                # own receipt and does not populate assignment.settled_at.
                # Verify that actual receipt and its income transaction,
                # rather than waiting indefinitely on the old assignment.
                from console.deposit_points_clearance import verified_initial_person
                applies, person = verified_initial_person(order, assignment)
                if (not applies or not person or person.settlement_id != settlement.pk
                        or person.settled_at > order.settled_at):
                    raise ValueError('续单陪玩结算事实待核对')
                continue
            if not assignment.settled_at:
                raise ValueError('续单陪玩结算事实待核对')
            credits = list(order.transactions.filter(wallet__account_id=account.pk, tx_type='INCOME',
                status='SUCCESS', reversal_of__isnull=True, remark=f'订单收入：{order.order_no}'))
            if assignment.provider_income:
                if (len(credits) != 1 or credits[0].amount != assignment.provider_income
                        or credits[0].balance_after != credits[0].balance_before + credits[0].amount
                        or credits[0].reversals.exists()):
                    raise ValueError('续单陪玩收益流水待核对')
            elif credits:
                raise ValueError('续单零收益流水待核对')
        for item in current:
            account = locked_accounts[item['provider_account_id']]
            wallet = get_wallet(account_id=account.pk, user_id=item['provider_id'], for_update=True)
            if not wallet.is_active or wallet.balance > 2147483147:
                raise ValueError('续单奖励钱包不可用')
            before = wallet.balance
            wallet.balance += 500
            wallet.save(update_fields=['balance'])
            payment = Transaction.objects.create(wallet=wallet, order=order, tx_type='REWARD', amount=500,
                balance_before=before, balance_after=wallet.balance, remark=f'续单奖励：{order.order_no}')
            reward = OrderContinuationReward.objects.create(continuation=row, provider=account,
                provider_business_cycle=account.business_cycle, transaction=payment, amount=500)
            from .continuation_notices import deliver
            transaction.on_commit(lambda reward_id=reward.pk: deliver(reward_id), robust=True)
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
        row.reward_state, row.reward_review_reason = 'PAID', ''
        row.save(update_fields=['reward_state', 'reward_review_reason'])
        return True


def record_reward_failure(claim_id, error):
    """Record a retry failure without overwriting a concurrent final result."""
    with transaction.atomic():
        row = OrderContinuation.objects.select_for_update().filter(pk=claim_id, status='VERIFIED').first()
        if not row or row.reward_state in ('PAID', 'INELIGIBLE') or row.rewards.exists():
            return
        row.reward_state = 'REVIEW'
        row.reward_review_reason = str(error)[:255] or '奖励凭证待核对'
        row.save(update_fields=['reward_state', 'reward_review_reason'])


class ContinuationRetryView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, claim_id):
        from .room_objections import _authorized
        if not enabled():
            return Response({'code': 503, 'msg': '续单核实暂未开放'}, status=503)
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权核对续单奖励'}, status=403)
        data = request.data
        if (not isinstance(data, dict) or set(data) != {'reason'} or not _text(data['reason'], 1000)):
            return Response({'code': 400, 'msg': '请填写重新核对原因'}, status=400)
        row = OrderContinuation.objects.filter(pk=claim_id, status='VERIFIED').first()
        if not row:
            return Response({'code': 404, 'msg': '已核实续单不存在'}, status=404)
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=f'/api/admin/continuations/{claim_id}/retry/', resource='continuation-reward-review',
            object_id=str(claim_id), request_body={'reason': data['reason']}, status_code=200)
        try:
            award_one(claim_id)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception('客服重核续单奖励失败 claim_id=%s', claim_id)
            record_reward_failure(claim_id, exc)
        row.refresh_from_db()
        return Response({'code': 0, 'data': _serialize(row),
                         'msg': '奖励已发放' if row.reward_state == 'PAID' else '已重新核对，请查看当前状态'})
