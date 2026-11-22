from django.utils import timezone
from django.conf import settings
from stores.db import transaction
from stores.context import current_currency_name
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import BigIntegerField, Count, Exists, F, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from orders.models import Order, OrderProvider
from orders.points_business_access import scoped_report_action
from users.models import EscortProfile

from .models import (
    DepositPaymentSnapshot,
    DisposeRecord,
    IdempotencyKey,
    IncomeHold,
    ProviderReport,
    ProviderReportImage,
    RechargeRecord,
    Transaction,
    WithdrawRequest,
    WithdrawalAccount,
    compute_withdraw_tax,
    get_min_withdraw_amount,
    get_platform_wallet,
    get_withdraw_tax_rate,
)
from .serializers import ReportSerializer, WithdrawRequestSerializer
from .services import claim_fund_write, get_wallet, peek_fund_write
from .receipts import fund_success, recover_fund_result, recover_withdrawal_result
from .payees import validate_payee
from .report_lifecycle import provider_report_operation
from common.logging_utils import log_money_event, new_trace_id


def _get_wallet(request, *, for_update=False):
    """当前登录主体的钱包。

    统一走 ``wallet.services.get_wallet``：它会同时按 account / user 两个维度
    寻址并自愈缺失的绑定，避免历史上「user 维度已建号、account 维度查不到
    又去创建」撞唯一约束 500 的死循环。

    返回二元组是为了兼容既有调用方的 ``wallet, _ = _get_wallet(...)`` 写法。
    """
    return get_wallet(
        account=request.account,
        user=request.legacy_user,
        for_update=for_update,
    ), False


def _wallet_overview(wallet):
    from engagement.services import wallet_funding_parts
    return {
        'balance': wallet.balance,
        'frozen_amount': wallet.frozen_amount,
        **wallet_funding_parts(wallet),
    }


class WalletInfoView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        wallet, _ = _get_wallet(request)
        return Response({'code': 0, 'data': _wallet_overview(wallet)})


class TopupView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        # 客户端不再允许直接修改余额。企微客服确认收款后，
        # 再由管理端按 1:10 兑换规则确认浪花入账。
        return Response({
            'code': 403,
            'msg': f'请联系企业微信客服充值，确认收款后按 1:10 兑换规则入账{current_currency_name()}',
        })


class IncomeRecordsView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        wallet, _ = _get_wallet(request)
        transactions = wallet.transactions.filter(
            tx_type__in=[Transaction.TxType.INCOME, Transaction.TxType.WITHDRAW]
        ).order_by('-created_at')[:50]

        data = [{
            'id': t.id,
            'tx_no': t.tx_no,
            'amount': t.amount,
            'tx_type': t.tx_type,
            'reversal_of':t.reversal_of_id, 'is_reversal':bool(t.reversal_of_id),
            'balance_before': t.balance_before,
            'balance_after': t.balance_after,
            'status': t.status,
            'remark': t.remark,
            'created_at': t.created_at.isoformat(),
        } for t in transactions]

        total_income = wallet.transactions.filter(
            tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS
        ).aggregate(total=Sum('amount'))['total'] or 0

        return Response({
            'code': 0,
            'data': {
                'transactions': data,
                'total_income': total_income,
            }
        })


class ProviderBenefitRecordsView(APIView):
    """陪玩本人可见的奖惩与礼物打赏记录。"""

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看此记录'})

        try:
            page_size = _withdraw_integer(request.query_params.get('page_size', 20), maximum=100)
            dispose_page = _withdraw_integer(request.query_params.get('dispose_page', 1), maximum=1000000)
            gift_page = _withdraw_integer(request.query_params.get('gift_page', 1), maximum=1000000)
        except ValueError:
            return Response({'code': 400, 'msg': '分页参数非法'})

        dispose_owner = Q(account=request.account) | Q(account__isnull=True, user__wallet__account=request.account)
        if request.legacy_user is not None:
            # Older records may predate account linkage. An explicitly bound
            # wallet remains authoritative if a legacy mapping later changes.
            dispose_owner |= Q(account__isnull=True, user=request.legacy_user, user__wallet__account__isnull=True)
        dispose_qs = DisposeRecord.objects.filter(dispose_owner).order_by('-created_at', '-id')
        dispose_totals = dispose_qs.aggregate(
            total=Count('id'),
            reward=Sum('amount', filter=Q(dispose_type=DisposeRecord.DisposeType.REWARD)),
            penalty=Sum('amount', filter=Q(dispose_type=DisposeRecord.DisposeType.PENALTY)),
        )
        dispose_start = (dispose_page - 1) * page_size
        dispose_records = [{
            'id': item.id,
            'type': item.dispose_type,
            'type_display': item.get_dispose_type_display(),
            'amount': item.amount,
            'signed_amount': item.amount if item.dispose_type == DisposeRecord.DisposeType.REWARD else -item.amount,
            'reason': item.reason,
            'created_at': item.created_at.isoformat(),
        } for item in dispose_qs[dispose_start:dispose_start + page_size]]

        ownership = Q(provider_account=request.account) | Q(provider_account__isnull=True, provider__wallet__account=request.account)
        if request.legacy_user is not None:
            ownership |= Q(provider_account__isnull=True, provider=request.legacy_user, provider__wallet__account__isnull=True)
        assignments = OrderProvider.objects.filter(order_id=OuterRef('pk'))
        meaningful = Q(released_at__isnull=True) | Q(settlement_base__gt=0) | Q(provider_income__gt=0)
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            meaningful |= Q(platform_subsidy_share__amount__gt=0)
        own_assignments = assignments.filter(ownership).filter(meaningful)
        credit_owner = Q(wallet__account=request.account)
        if request.legacy_user is not None:
            credit_owner |= Q(wallet__account__isnull=True, wallet__user=request.legacy_user)
        credits = Transaction.objects.filter(credit_owner).filter(
            order_id=OuterRef('pk'), tx_type__in=[Transaction.TxType.INCOME,Transaction.TxType.GIFT],
            status=Transaction.Status.SUCCESS,
        ).exclude(
            Q(remark__startswith='推荐分佣：') | Q(remark__startswith='礼物推荐分佣：') |
            Q(reversal_of__remark__startswith='推荐分佣：') | Q(reversal_of__remark__startswith='礼物推荐分佣：')
        ).order_by()
        # 有成员分账时以成员为准；旧订单才回退订单头。实得只来自成功入账流水。
        gift_orders = Order.objects.filter(
            status__in=[Order.Status.COMPLETED,Order.Status.CANCELLED], payment_status__in=[Order.PaymentStatus.PAID,Order.PaymentStatus.PARTIALLY_REFUNDED,Order.PaymentStatus.REFUNDED],
            is_gift_snapshot=True,
        ).annotate(
            has_assignments=Exists(assignments), has_own_assignment=Exists(own_assignments),
        ).filter(
            Q(has_own_assignment=True) | (Q(has_assignments=False) & ownership)
        ).annotate(
            expected_income=Coalesce(
                Subquery(own_assignments.values('provider_income')[:1]), F('provider_income'),
                output_field=BigIntegerField(),
            ),
            received_income=Coalesce(
                Subquery(credits.values('order_id').annotate(total=Sum('amount')).values('total')[:1]),
                Value(0), output_field=BigIntegerField(),
            ),
            has_credit=Exists(credits),
            assignment_settled_at=Subquery(own_assignments.values('settled_at')[:1]),
        ).select_related('customer', 'customer_account', 'service', 'source_order').order_by('-completed_at', '-created_at', '-id')
        gift_totals = gift_orders.aggregate(
            total=Count('id'), amount=Sum('amount'), income=Sum('received_income'),
        )

        gift_records = []
        gift_start = (gift_page - 1) * page_size
        gift_page_orders = list(gift_orders[gift_start:gift_start + page_size])
        held_rows = IncomeHold.objects.filter(credit_owner,
            income_transaction__order_id__in=[item.pk for item in gift_page_orders],
            released_at__isnull=True, cancelled_at__isnull=True).values('income_transaction__order_id').annotate(
                amount=Sum(F('amount') - F('recovered_amount')), release_at=Max('release_at'))
        holds = {item['income_transaction__order_id']: item for item in held_rows}
        for order in gift_page_orders:
            customer = order.customer_account if order.customer_account_id else order.customer
            settled_at = order.assignment_settled_at or order.settled_at
            hold = holds.get(order.pk)
            if order.payment_status == 'REFUNDED':
                settlement_status = 'REFUNDED'
            elif order.payment_status == 'PARTIALLY_REFUNDED':
                settlement_status = 'PARTIALLY_REFUNDED_FROZEN' if hold else 'PARTIALLY_REFUNDED'
            elif hold:
                settlement_status = 'FROZEN'
            elif (order.has_credit or settled_at) and order.received_income == order.expected_income:
                settlement_status = 'SETTLED'
            elif settled_at or order.received_income > order.expected_income:
                settlement_status = 'CHECK_REQUIRED'
            else:
                settlement_status = 'PENDING'
            gift_records.append({
                'id': order.id,
                'order_no': order.order_no,
                'source_order_no': order.source_order.order_no if order.source_order_id else '',
                'service_name': order.service_name_snapshot or order.service.name,
                'customer_name': customer.nickname or customer.username or '老板',
                'amount': order.amount,
                'provider_income': order.expected_income,
                'received_income': order.received_income,
                'settlement_status': settlement_status,
                'frozen_income': hold['amount'] if hold else 0,
                'release_at': hold['release_at'].isoformat() if hold and hold['release_at'] else None,
                'completed_at': (order.completed_at or order.updated_at).isoformat(),
            })

        return Response({'code': 0, 'data': {
            'summary': {
                'total_reward': dispose_totals['reward'] or 0,
                'total_penalty': dispose_totals['penalty'] or 0,
                'total_gift_amount': gift_totals['amount'] or 0,
                'total_gift_income': gift_totals['income'] or 0,
                'gift_count': gift_totals['total'],
            },
            'dispose_records': dispose_records,
            'gift_records': gift_records,
            'pagination': {
                'page_size': page_size,
                'dispose': {'page': dispose_page, 'total': dispose_totals['total']},
                'gift': {'page': gift_page, 'total': gift_totals['total']},
            },
        }})


def _withdraw_integer(value, *, minimum=1, maximum=2147483647):
    """金额以账务单位传输；拒绝截断小数、布尔值及超出字段范围的数字。"""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('金额非法')
    raw = str(value).strip()
    if not raw.isascii() or not raw.isdigit() or len(raw) > 10:
        raise ValueError('金额非法')
    parsed = int(raw)
    if not minimum <= parsed <= maximum:
        raise ValueError('金额非法')
    return parsed


class WithdrawView(APIView):
    # 出账接口：限流只作用于 POST（见 common.throttling.WriteScopedRateThrottle）。
    throttle_scope = 'wallet_write'
    """提现：GET 返回提现配置与我的申请记录，POST 提交新的提现申请。"""

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        """返回提现配置与我的申请记录；带 ``?amount=`` 时附带一次服务端试算。

        试算存在的理由：前端此前自己算了一遍税再展示「预计到账」，于是同一套税
        规则在前后端各存一份。两份实现对齐舍入只能让它们**暂时**一致 —— 下次谁
        改了税率口径或舍入方式，展示值就会和落库值悄悄分叉，用户看到「扣完剩
        12.3 币」而记录里是 12.2 币。病根是前端不该持有第二份算法，所以这里把
        试算搬到服务端，与 POST 落库**调用同一个** ``compute_withdraw_tax``。
        """
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看提现信息'})
        wallet, _ = _get_wallet(request)
        requests_qs = WithdrawRequest.objects.filter(account=request.account).order_by('-created_at', '-id')
        page_info = None
        if 'requests_page' in request.query_params:
            try:
                page = _withdraw_integer(request.query_params['requests_page'], maximum=1000000)
                page_size = _withdraw_integer(request.query_params.get('requests_page_size', 20), maximum=100)
            except ValueError:
                return Response({'code': 400, 'msg': '分页参数非法'})
            total = requests_qs.count()
            start = (page - 1) * page_size
            requests_qs = requests_qs[start:start + page_size]
            page_info = {'page': page, 'page_size': page_size, 'total': total}
        serializer = WithdrawRequestSerializer(requests_qs, many=True)
        from .withdraw_rules import withdraw_rule_snapshot
        rules = withdraw_rule_snapshot()
        tax_rate = rules['tax_rate']
        data = {
            'balance': wallet.balance,
            'frozen_amount': wallet.frozen_amount,
            'min_amount': rules['min_amount'],
            'tax_rate': tax_rate,
            'config_version': rules['version'],
            'requests': serializer.data,
        }
        if 'request_id' in request.query_params:
            try:
                request_id = _withdraw_integer(request.query_params['request_id'])
            except ValueError:
                return Response({'code': 400, 'msg': '提现申请编号无效'})
            selected = WithdrawRequest.objects.filter(account=request.account, pk=request_id).first()
            data['requested_withdrawal'] = WithdrawRequestSerializer(selected).data if selected else None
        if page_info is not None:
            data['requests_pagination'] = page_info
        threshold = rules['balance_threshold']
        if threshold is not None:
            data['balance_threshold'] = threshold
            data['can_withdraw'] = wallet.balance > threshold

        raw_amount = request.query_params.get('amount')
        if raw_amount is not None and str(raw_amount).strip() != '':
            try:
                amount = _withdraw_integer(raw_amount)
            except (TypeError, ValueError):
                # 与订单状态白名单同口径：参数不合法就明确拒绝，不做静默兜底。
                # 静默返回一个「不带试算的正常响应」会让前端以为拿到了估值，
                # 实际展示的是上一次的旧数字。
                return Response({'code': 400, 'msg': '金额非法'})
            # 关键：必须复用 POST 落库那一个实现，不许在此另写一遍算法，
            # 否则只是把「前后端两份」换成了「同后端两份」，问题原样保留。
            tax_amount = compute_withdraw_tax(amount, tax_rate)
            data['amount'] = amount
            data['tax_amount'] = tax_amount
            data['actual_amount'] = amount - tax_amount
            data['payout_eligible'] = data['actual_amount'] > 0

        return Response({'code': 0, 'data': data})

    @recover_withdrawal_result('withdraw')
    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可申请提现'})

        try:
            amount = _withdraw_integer(request.data.get('amount'))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '金额非法'})
        if amount <= 0:
            return Response({'code': 400, 'msg': '金额非法'})

        saved_id = request.data.get('withdrawal_account_id')
        saved_version = request.data.get('expected_payee_version')
        if 'withdrawal_account_id' in request.data:
            if type(saved_id) is not int or saved_id <= 0 or type(saved_version) is not int or saved_version <= 0:
                return Response({'code': 400, 'msg': '请选择已绑定的收款账户'})
            if any(field in request.data for field in ('payee_method', 'payee_account', 'payee_name')):
                return Response({'code': 400, 'msg': '绑定账户与手填收款资料不能同时提交'})
        else:
            try:
                payee_method, payee_account, payee_name = validate_payee(request.data)
            except ValueError as error:
                return Response({'code': 400, 'msg': str(error)})
        remark = request.data.get('remark', '')
        if not isinstance(remark, str) or len(remark.strip()) > 255:
            return Response({'code': 400, 'msg': '备注须为255字以内的文本'})
        remark = remark.strip()

        quote_fields = ('expected_tax_rate', 'expected_tax_amount', 'expected_actual_amount')
        expected_quote = None
        if any(field in request.data for field in quote_fields):
            try:
                expected_quote = tuple(_withdraw_integer(request.data.get(field), minimum=0) for field in quote_fields)
            except (TypeError, ValueError):
                return Response({'code': 400, 'msg': '提现试算参数不完整或无效，请重新试算'})

        trace_id = new_trace_id()
        # 先只读探测重放：上一笔已成功时，余额已被扣走，重放会先撞上
        # 「余额不足」返回 400，掩盖掉「其实已经提交过」的真实原因。
        replayed, replay_reason = peek_fund_write(
            account=request.account,
            request_id=request.data.get('client_request_id'),
        )
        if replayed:
            log_money_event(
                'withdraw.duplicate',
                account_id=request.account.pk,
                amount=amount,
                trace_id=trace_id,
                idempotent_hit=True,
                reason=replay_reason,
            )
            return Response({'code': 409, 'msg': replay_reason})

        expected_version = request.data.get('expected_config_version')
        if expected_version is not None and (not isinstance(expected_version, str) or len(expected_version) != 64):
            return Response({'code': 400, 'msg': '提现规则版本无效，请重新试算'})
        with transaction.atomic():
            if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_active_provider_operation
                try:
                    active = lock_active_provider_operation(request.account)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系客服'}, status=503)
                if not active:
                    return Response({'code': 409, 'msg': '陪玩账号状态已变化，请重新登录'}, status=409)
            from .withdraw_rules import lock_withdraw_rules, withdraw_rule_snapshot
            lock_withdraw_rules()
            rules = withdraw_rule_snapshot(lock=True)
            if expected_version is not None and expected_version != rules['version']:
                return Response({'code': 400, 'errorCode': 'WITHDRAW_QUOTE_CHANGED', 'msg': '提现规则已更新，请重新确认试算金额'})
            wallet, _ = _get_wallet(request, for_update=True)
            threshold = rules['balance_threshold']
            if threshold is not None and wallet.balance <= threshold:
                return Response({'code': 400, 'msg': f'可用余额须大于人民币 {threshold / 100:g} 元才可提现'})
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': f'{current_currency_name()}余额不足'})

            if saved_id is not None:
                saved = WithdrawalAccount.objects.select_for_update().filter(pk=saved_id, account=request.account,
                    business_cycle=request.account.business_cycle).first()
                if saved is None or saved.version != saved_version:
                    return Response({'code': 400, 'errorCode': 'PAYEE_CHANGED',
                                     'msg': '收款账户已修改或解绑，请重新选择并核对'})
                payee_method, payee_account, payee_name = saved.payee_method, saved.payee_account, saved.payee_name

            # 与用户看到的试算不一致时不占幂等键、不冻结余额。
            min_amount = rules['min_amount']
            if amount < min_amount:
                return Response({'code': 400, 'errorCode': 'WITHDRAW_QUOTE_CHANGED', 'msg': f'最低提现为 {min_amount / 10:g} {current_currency_name()}，请重新确认'})
            tax_rate = rules['tax_rate']
            tax_amount = compute_withdraw_tax(amount, tax_rate)
            actual_amount = amount - tax_amount
            if expected_quote is not None and expected_quote != (tax_rate, tax_amount, actual_amount):
                return Response({'code': 400, 'errorCode': 'WITHDRAW_QUOTE_CHANGED', 'msg': '提现规则已更新，请重新确认试算金额'})
            if actual_amount <= 0:
                return Response({'code': 400, 'errorCode': 'WITHDRAW_ZERO_PAYOUT',
                                 'msg': '预计实际打款为零，请调整金额或核对费率'})

            # 幂等闸门放在「全部校验通过之后、第一次动钱之前」。
            #
            # 不能更早：视图里的 `return Response(400)` 是正常返回，不是异常，
            # with transaction.atomic() 会照常提交 —— 校验失败也会把键占掉，
            # 用户充完钱拿同一个 client_request_id 重试就会被永久判为「重复提交」。
            # 也不能更晚：必须与扣款同处一个事务，才能靠唯一约束串行化并发重试。
            allowed, reason = claim_fund_write(
                account=request.account,
                request_id=request.data.get('client_request_id'),
                scope='withdraw',
                fingerprint=f'{amount}|{payee_method}|{payee_account}',
            )
            if not allowed:
                log_money_event(
                    'withdraw.duplicate',
                    account_id=request.account.pk,
                    amount=amount,
                    trace_id=trace_id,
                    idempotent_hit=True,
                    reason=reason,
                )
                return Response({'code': 409, 'msg': reason})

            # 快照当前税率并按提现金额扣税：冻结/扣款仍是全额 amount，
            # 税额只影响实际打款给陪玩的 actual_amount，账务可追溯。
            balance_before = wallet.balance
            wallet.balance -= amount
            wallet.frozen_amount += amount
            wallet.save(update_fields=['balance', 'frozen_amount'])

            tx = Transaction.objects.create(
                wallet=wallet,
                amount=-amount,
                tx_type=Transaction.TxType.WITHDRAW,
                balance_before=balance_before,
                balance_after=wallet.balance,
                status=Transaction.Status.PENDING,
                remark=(
                    f'提现申请（税前 {amount / 10:g}，代扣税 {tax_amount / 10:g}，'
                    f'预计到账 {actual_amount / 10:g} {current_currency_name()}）'
                    if tax_amount else '提现申请'
                ),
            )
            withdraw = WithdrawRequest.objects.create(
                user=request.legacy_user,
                account=request.account,
                account_business_cycle=request.account.business_cycle,
                workflow_version=2,
                amount=amount,
                tax_rate=tax_rate,
                tax_amount=tax_amount,
                actual_amount=actual_amount,
                payee_method=payee_method,
                payee_account=payee_account,
                payee_name=payee_name,
                remark=remark,
                transaction=tx,
            )
            log_money_event(
                'withdraw.submit',
                account_id=request.account.pk,
                user_id=getattr(request.legacy_user, 'pk', None),
                tx_type=Transaction.TxType.WITHDRAW,
                amount=-amount,
                balance_before=balance_before,
                balance_after=wallet.balance,
                trace_id=trace_id,
                reason=f'tax_rate={tax_rate};tax_amount={tax_amount}',
            )

            submit_msg = '提现申请已提交，等待审核'
            if tax_amount:
                submit_msg += (
                    f'；代扣税 {tax_rate}% 计 {tax_amount / 10:g} {current_currency_name()}，'
                    f'预计打款人民币 ¥{actual_amount / 100:g}'
                )
            result = fund_success(request, 'withdraw', msg=submit_msg, data={
                'balance': wallet.balance, 'frozen_amount': wallet.frozen_amount,
                'request': WithdrawRequestSerializer(withdraw).data,
            })
            from .withdraw_notifications import withdrawal_message
            withdrawal_message(withdraw, 'submitted')
            from .notifier import notify_wallet_update, notify_withdraw_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
            transaction.on_commit(lambda: notify_withdraw_update(withdraw))
        return result


class TransactionsView(APIView):
    """统一钱包流水：支持按类型筛选 + 分页。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        wallet, _ = _get_wallet(request)
        qs = wallet.transactions.all()

        tx_type = request.query_params.get('tx_type')
        if tx_type:
            qs = qs.filter(tx_type=tx_type)

        try:
            page = max(1, int(request.query_params.get('page', 1)))
            page_size = max(1, min(100, int(request.query_params.get('page_size', 20))))
        except (TypeError, ValueError):
            page, page_size = 1, 20

        total = qs.count()
        start = (page - 1) * page_size
        items = list(qs.select_related('reversal_of', 'order').order_by('-created_at', '-id')[start:start + page_size])
        from .addon_transactions import addon_links
        addon_by_transaction = addon_links(t.pk for t in items)
        # A GIFT can be an order gift, a recharge bonus, or an activity award.
        # Use persisted relations rather than the editable transaction remark.
        original_gift_ids = [t.reversal_of_id or t.id for t in items if t.tx_type == Transaction.TxType.GIFT]
        recharge_gift_ids = set(RechargeRecord.objects.filter(gift_tx_id__in=original_gift_ids)
                                .values_list('gift_tx_id', flat=True))

        def gift_source(t):
            if t.tx_type != Transaction.TxType.GIFT:
                return None
            original = t.reversal_of if t.reversal_of_id else t
            if original.order_id:
                return 'ORDER_GIFT'
            if original.id in recharge_gift_ids:
                return 'RECHARGE_GIFT'
            return None

        data = [{
            'id': t.id,
            'tx_no': t.tx_no,
            'amount': t.amount,
            'tx_type': t.tx_type,
            'gift_source': gift_source(t),
            'reversal_of':t.reversal_of_id, 'is_reversal':bool(t.reversal_of_id),
            # Refund entries carry their own reason; the original entry is the
            # source of the referral classification used by settlement.
            'is_referral_income': t.tx_type == Transaction.TxType.INCOME and
                (t.reversal_of.remark if t.reversal_of_id else t.remark).startswith(('推荐分佣：', '礼物推荐分佣：')),
            'balance_before': t.balance_before,
            'balance_after': t.balance_after,
            'status': t.status,
            'remark': t.remark,
            'created_at': t.created_at.isoformat(),
            'order_id': addon_by_transaction[t.pk]['order_id'] if t.pk in addon_by_transaction else t.order_id,
            'order_no': addon_by_transaction[t.pk]['order_no'] if t.pk in addon_by_transaction else (t.order.order_no if t.order_id else None),
            'addon': addon_by_transaction.get(t.pk),
        } for t in items]

        return Response({
            'code': 0,
            'data': {
                'transactions': data,
                'total_income': wallet.transactions.filter(
                    tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                ).aggregate(total=Sum('amount'))['total'] or 0,
                'total': total,
                'page': page,
                'page_size': page_size,
                **_wallet_overview(wallet),
            }
        })


def _report_owner_q(request):
    """Use the business account first; only unmapped historical rows fall back."""
    owner = Q(provider_account=request.account)
    if request.legacy_user is not None:
        owner |= Q(provider_account__isnull=True, provider=request.legacy_user)
    return owner


class ReportOrderChoicesView(APIView):
    """可创建报单的订单，按当前陪玩和现有报单资格过滤。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可提交报单'})
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            size = max(1, min(100, int(request.query_params.get('page_size', 20))))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '分页参数无效'})
        from orders.models import Order, OrderProvider
        owner = _report_owner_q(request)
        shares = OrderProvider.objects.filter(owner, order_id=OuterRef('pk')).order_by('id')
        reports = ProviderReport.objects.filter(owner, order_id=OuterRef('pk'))
        direct = Q(provider_account=request.account)
        if request.legacy_user is not None:
            direct |= Q(provider_account__isnull=True, provider=request.legacy_user)
        orders = (Order.objects.filter(status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED],
                    payment_status=Order.PaymentStatus.PAID,
                    source_order__isnull=True, is_gift_snapshot=False)
                  .annotate(_has_share=Exists(shares), _has_report=Exists(reports),
                            _released=Subquery(shares.values('released_at')[:1]),
                            _share_base=Subquery(shares.values('settlement_base')[:1]))
                  .filter(direct | Q(_has_share=True), _has_report=False))
        # Points-paid reports are not integrated yet; exclude independently
        # recorded evidence before pagination, including damaged markers.
        from engagement.models import PointLedger
        from orders.models import OrderPointsRedemption
        from django.db import DatabaseError
        from orders.points_service_payment import MARKER, points_receipt_table_exists
        orders = orders.annotate(_has_points_ledger=Exists(PointLedger.objects.filter(
            order_id=OuterRef('pk'), kind='ORDER_POINTS_REDEEM')))
        points_filter = Q(funding_terms__has_key=MARKER) | Q(_has_points_ledger=True)
        try:
            has_points_receipts = points_receipt_table_exists()
        except DatabaseError:
            orders = orders.none()
            has_points_receipts = False
        if has_points_receipts:
            orders = orders.annotate(_has_points_receipt=Exists(OrderPointsRedemption.objects.filter(order_id=OuterRef('pk'))))
            points_filter |= Q(_has_points_receipt=True)
        from orders.points_checkout_test import report_enabled
        verify_points = report_enabled()
        if verify_points:
            from django.db.models import BooleanField, Case, Value, When
            orders = orders.annotate(_verify_points=Case(
                When(points_filter, then=Value(True)), default=Value(False), output_field=BooleanField()))
        else:
            orders = orders.exclude(points_filter)
        from orders.segment_financial_capture import enforcement_enabled
        segment_enforced = enforcement_enabled()
        if segment_enforced:
            from orders.models import OrderOriginalShareTransfer
            retained = OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order_id=OuterRef('pk'),
                previous_segment__attribution__financial_coverage__isnull=False,
                previous_segment__provider_account=request.account,
                previous_segment__started_at__isnull=False, retained_share__gt=0)
            orders = orders.annotate(_has_retained_segment=Exists(retained))
        retained_filter = {'_has_retained_segment': False} if segment_enforced else {}
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            funded = shares.filter(platform_subsidy_share__amount__gt=0)
            orders = orders.annotate(_has_funded_share=Exists(funded)).exclude(
                _released__isnull=False, _share_base__lte=0, _has_funded_share=False, **retained_filter)
        else:
            orders = orders.exclude(_released__isnull=False, _share_base__lte=0, **retained_filter)
        query = request.query_params.get('q', '').strip()
        if query:
            from identifiers.services import entity_ids_for_code
            orders = orders.filter(Q(order_no__icontains=query) | Q(service_name_snapshot__icontains=query)
                                   | Q(id__in=entity_ids_for_code(query, 'orders.order'))
                                   | Q(service_name_snapshot='', service__name__icontains=query))
        orders = orders.order_by('-created_at', '-id').select_related('service')
        start = (page - 1) * size
        if verify_points:
            # Verify every real candidate before counting and paging, without
            # truncating history or building a database-sized IN clause.
            from orders.points_funding_plan import PointsFundingPlanError
            from orders.points_report_test import verified_report_snapshot
            total, page_orders, cursor = 0, [], None
            while True:
                batch_query = orders
                if cursor is not None:
                    batch_query = batch_query.filter(Q(created_at__lt=cursor[0])
                        | Q(created_at=cursor[0], pk__lt=cursor[1]))
                batch = list(batch_query[:256])
                if not batch:
                    break
                for candidate in batch:
                    if candidate._verify_points:
                        try:
                            verified_report_snapshot(candidate, request.account.pk, getattr(request.legacy_user, 'pk', None))
                        except PointsFundingPlanError:
                            continue
                    if start <= total < start + size:
                        page_orders.append(candidate)
                    total += 1
                cursor = (batch[-1].created_at, batch[-1].pk)
        else:
            total = orders.count()
            page_orders = list(orders[start:start + size])
        from identifiers.services import get_entity_number, prime_entity_numbers
        prime_entity_numbers(page_orders)
        rows = [{'id': order.id, 'order_no': order.order_no, 'entity_no': get_entity_number(order),
                 'service_name': order.service_name_snapshot or order.service.name}
                for order in page_orders]
        return Response({'code': 0, 'data': {'list': rows, 'total': total, 'page': page, 'page_size': size}})


class ReportListCreateView(APIView):
    """陪玩报单：GET 查询记录，POST 按已完成订单创建上传草稿。"""

    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        reports = ProviderReport.objects.filter(_report_owner_q(request))
        if request.query_params.get('only_order_ids') == '1':
            ids = list(reports.filter(order__isnull=False).order_by('order_id').values_list('order_id', flat=True).distinct())
            return Response({'code': 0, 'data': ids})
        order_id = request.query_params.get('order_id')
        if order_id:
            if not order_id.isascii() or not order_id.isdigit() or int(order_id) < 1:
                return Response({'code':400, 'msg':'订单编号无效'})
            reports = reports.filter(order_id=int(order_id))
        reports = reports.select_related('order').prefetch_related('result_images').order_by('-created_at', '-id')
        if 'page' not in request.query_params:
            return Response({'code': 0, 'data': ReportSerializer(reports, many=True, context={'request': request}).data})
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            size = max(1, min(100, int(request.query_params.get('page_size', 20))))
        except (TypeError, ValueError):
            return Response({'code':400, 'msg':'分页参数无效'})
        total = reports.count()
        start = (page - 1) * size
        rows = ReportSerializer(reports[start:start + size], many=True, context={'request': request}).data
        return Response({'code':0, 'data':{'list':rows, 'total':total, 'page':page, 'page_size':size}})

    @provider_report_operation
    @transaction.atomic
    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可提交报单'})
        from orders.models import Order
        try:
            order_id = int(request.data.get('order_id', 0))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请选择已完成订单'})
        # 客服切换双陪流程也先锁订单、再检查草稿；创建草稿必须遵循同一顺序，
        # 避免切换时漏掉正在创建的草稿，并以锁内的最新状态判断归属和资格。
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            return Response({'code': 400, 'msg': '订单不存在、未完成或不属于当前陪玩'})
        from .report_lifecycle import report_order_cycle_matches
        if not report_order_cycle_matches(order, request.account):
            return Response({'code': 409, 'msg': '订单陪玩来源周期待核对'}, status=409)
        # 与开始/完成服务入口统一用 provider + order 唯一键，兼容历史未回填账户的草稿。
        share_owner = Q(provider_account=request.account)
        if request.legacy_user is not None:
            share_owner |= Q(provider_account__isnull=True, provider=request.legacy_user)
        share = order.providers.filter(share_owner).first()
        from orders.report_eligibility import report_entry_error
        error = report_entry_error(order, request.account, share, legacy_user=request.legacy_user)
        if error:
            return Response({'code': 400, 'msg': error})
        from orders.points_service_payment import needs_integrated_processing
        snapshot = None
        if needs_integrated_processing(order):
            from orders.points_funding_plan import PointsFundingPlanError
            from orders.points_report_test import verified_report_snapshot
            try:
                snapshot = verified_report_snapshot(order, request.account.pk, getattr(request.legacy_user, 'pk', None))
            except PointsFundingPlanError:
                return Response({'code': 400, 'msg': '积分订单报单或收益快照待核对'})
        report, created = ProviderReport.objects.get_or_create(
            provider=request.legacy_user,
            order=order,
            defaults={
                'provider_account': request.account,
                'game_name': order.service_name_snapshot or order.service.name,
                'description': order.remark, 'amount': snapshot.incoming_basis if snapshot else share.settlement_base if share else order.amount,
                'commission_rate': snapshot.commission_rate if snapshot else share.commission_rate if share else order.commission_rate,
                'status': ProviderReport.Status.DRAFT,
            },
        )
        if report.provider_account_id and report.provider_account_id != request.account.pk:
            return Response({'code': 409, 'msg': '报单归属需客服核对'}, status=409)
        if snapshot and (report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate):
            return Response({'code': 409, 'msg': '积分订单报单金额或抽成快照待核对'}, status=409)
        if not report.provider_account_id:
            report.provider_account = request.account
            report.save(update_fields=['provider_account'])
            created = True
        if created:
            from orders.notifier import notify_report_update
            transaction.on_commit(lambda: notify_report_update(report))
        serializer = ReportSerializer(report, context={'request': request})
        return Response({'code': 0, 'msg': '报单草稿已创建', 'data': serializer.data})


def _locked_report(request, pk):
    """与结单、退款、审核保持订单→报单的锁顺序。调用方已开启事务。"""
    from orders.models import Order
    initial = ProviderReport.objects.filter(_report_owner_q(request), pk=pk).only('order_id').first()
    if initial is None:
        return None
    if initial.order_id:
        order = Order.objects.select_for_update().get(pk=initial.order_id)
        from .report_lifecycle import report_order_cycle_matches
        if not report_order_cycle_matches(order, request.account):
            return None
    return ProviderReport.objects.select_for_update().get(_report_owner_q(request), pk=pk)


class ReportImageUploadView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @provider_report_operation
    @transaction.atomic
    @scoped_report_action
    def post(self, request, pk):
        import hashlib
        from orders.services import report_edit_error
        report = _locked_report(request, pk)
        if not report:
            return Response({'code': 400, 'msg': '当前报单不可上传凭证'})
        image = request.FILES.get('image')
        kind = (request.data.get('kind') or '').upper()
        if not image or kind not in ('ENTRY', 'COMPLETION', 'RESULT'):
            return Response({'code': 400, 'msg': '截图类型或文件无效'})
        request_id = request.data.get('client_request_id', '') if kind == 'RESULT' else ''
        if request_id and (not isinstance(request_id, str) or len(request_id) > 64):
            return Response({'code': 400, 'msg': '截图请求编号无效'})
        fingerprint = ''
        if request_id:
            digest = hashlib.sha256()
            for chunk in image.chunks():
                digest.update(chunk)
            image.seek(0)
            fingerprint = hashlib.sha256(f'{report.pk}:RESULT:{digest.hexdigest()}'.encode()).hexdigest()

        def replay(receipt):
            if receipt.scope != 'report.image' or receipt.request_fingerprint != fingerprint:
                return Response({'code': 409, 'msg': '截图请求编号已用于其他操作，请重新选择图片'}, status=409)
            image_id = (receipt.response or {}).get('image_id')
            if not image_id or not report.result_images.filter(pk=image_id).exists():
                return Response({'code': 409, 'msg': '此前上传的截图已删除，请重新选择图片'}, status=409)
            return Response({'code': 0, 'data': ReportSerializer(report, context={'request': request}).data})

        if request_id:
            receipt = IdempotencyKey.objects.select_for_update().filter(account=request.account, request_id=request_id).first()
            if receipt:
                return replay(receipt)
        error = report_edit_error(report)
        if error:
            return Response({'code': 400, 'msg': error})
        if kind == 'COMPLETION' and report.order_id and report.order.room_required_snapshot is True:
            from orders.models import OrderRoomCompletionRequest
            if OrderRoomCompletionRequest.objects.filter(room__order_id=report.order_id, status='VERIFIED',
                    confirmation__status__in=['RUNNING', 'PAUSED']).exists():
                return Response({'code': 409, 'msg': '结单申请已核实，凭证不能再修改'}, status=409)
        # 双陪的首次入队/结单截图同时是履约动作，必须由订单入口提交，
        # 否则报单草稿会显示已提交，但订单不会触发两人齐备后的状态流转。
        from orders.team_fulfillment import team_report_evidence_error
        evidence_error = team_report_evidence_error(report, kind)
        if evidence_error:
            return Response({'code': 400, 'msg': evidence_error})
        from rest_framework import serializers
        try:
            serializers.ImageField().run_validation(image)
        except (serializers.ValidationError, DjangoValidationError):
            return Response({'code': 400, 'msg': '请上传有效的截图文件'})
        if kind == 'ENTRY':
            report.entry_image = image
            report.save(update_fields=['entry_image', 'updated_at'])
        elif kind == 'COMPLETION':
            report.completion_image = image
            report.save(update_fields=['completion_image', 'updated_at'])
        else:
            if report.result_images.count() >= 9:
                return Response({'code': 400, 'msg': '战绩截图最多上传9张'})
            if request_id:
                receipt, created = IdempotencyKey.objects.get_or_create(account=request.account, request_id=request_id,
                    defaults={'scope': 'report.image', 'request_fingerprint': fingerprint})
                if not created:
                    return replay(receipt)
            result_image = ProviderReportImage.objects.create(report=report, image=image)
            if request_id:
                receipt.response = {'image_id': result_image.pk}
                receipt.save(update_fields=['response'])
        report.refresh_from_db()
        from orders.notifier import notify_report_update
        transaction.on_commit(lambda: notify_report_update(report))
        return Response({'code': 0, 'data': ReportSerializer(report, context={'request': request}).data})


class ReportImageDeleteView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @provider_report_operation
    @transaction.atomic
    @scoped_report_action
    def delete(self, request, pk, image_id):
        from orders.services import report_edit_error
        report = _locked_report(request, pk)
        error = report_edit_error(report) if report else '当前报单不可修改凭证'
        if error:
            return Response({'code': 400, 'msg': error})
        picture = report.result_images.filter(pk=image_id).first()
        if not picture:
            return Response({'code': 404, 'msg': '截图不存在'})
        # 只移除草稿引用，原文件留给媒体保留策略统一清理。
        picture.delete()
        from orders.notifier import notify_report_update
        transaction.on_commit(lambda: notify_report_update(report))
        return Response({'code': 0, 'data': ReportSerializer(report, context={'request': request}).data})


class ReportSubmitView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @provider_report_operation
    @transaction.atomic
    @scoped_report_action
    def post(self, request, pk):
        from orders.services import report_edit_error
        report = _locked_report(request, pk)
        error = report_edit_error(report) if report else '当前报单不可提交'
        if error:
            return Response({'code': 400, 'msg': error})
        from orders.models import Order
        # report_edit_error already verifies the special negotiated refund;
        # completing the whole order remains mandatory before submission.
        if report.order_id and report.order.status != Order.Status.COMPLETED:
            return Response({'code': 400, 'msg': '服务完成后才能提交报单，已退款订单不可提交'})
        if report.order_id:
            from orders.segment_reports import supports as segment_reports_support
            if segment_reports_support(report.order):
                return Response({'code':400, 'msg':'请按各次履约记录分别提交报单'})
        raw_commission_rate = request.data.get('commission_rate')
        if raw_commission_rate in (None, ''):
            commission_rate = (
                report.order.commission_rate
                if report.order_id
                else 0
            )
        else:
            try:
                commission_rate = int(raw_commission_rate)
            except (TypeError, ValueError):
                return Response({'code': 400, 'msg': '请填写平台抽成比例'})
        if not 0 <= commission_rate <= 100:
            return Response({'code': 400, 'msg': '平台抽成比例需在 0 到 100 之间'})
        if report.order_id:
            share = report.order.providers.filter(provider_id=report.provider_id).first()
            from orders.services import required_report_providers
            if share and share.released_at and report.provider_id not in required_report_providers(report.order):
                return Response({'code': 400, 'msg': '本人无结算份额，无需提交报单'})
            from orders.points_service_payment import needs_integrated_processing
            if needs_integrated_processing(report.order):
                from orders.points_funding_plan import PointsFundingPlanError
                from orders.points_report_test import verified_report_snapshot
                try:
                    snapshot = verified_report_snapshot(report.order, report.provider_account_id, report.provider_id)
                except PointsFundingPlanError:
                    return Response({'code': 400, 'msg': '积分订单报单或收益快照待核对'})
                commission_rate, report.amount = snapshot.commission_rate, snapshot.incoming_basis
            else:
                commission_rate = share.commission_rate if share else report.order.commission_rate
                report.amount = share.settlement_base if share else report.order.amount
        missing = []
        if not report.entry_image: missing.append('入队截图')
        if not report.completion_image: missing.append('结单截图')
        if not report.result_images.exists(): missing.append('战绩截图')
        if missing:
            return Response({'code': 400, 'msg': f"请上传：{'、'.join(missing)}"})
        report.commission_rate = commission_rate
        report.status = ProviderReport.Status.PENDING
        report.audit_remark = ''
        # This is a new review round. Previous decisions remain in the
        # recipient's notice and administrative audit log, not on the
        # pending round as though it had already been reviewed.
        report.auditor = None
        report.auditor_account = None
        report.audited_at = None
        report.submitted_at = timezone.now()
        report.save(update_fields=['amount', 'commission_rate', 'status', 'audit_remark', 'auditor', 'auditor_account', 'audited_at', 'submitted_at', 'updated_at'])
        if report.order_id:
            from engagement.confirmation import refresh_report_clock
            refresh_report_clock(report.order)
        from orders.notifier import notify_report_update
        transaction.on_commit(lambda: notify_report_update(report))
        return Response({'code': 0, 'msg': '报单已提交，等待审核', 'data': ReportSerializer(report, context={'request': request}).data})


class ReportWithdrawView(APIView):
    """陪玩撤回自己的待审核报单，保留凭证并开启新的编辑轮次。"""
    permission_classes = [IsClubAccountAuthenticated]

    @provider_report_operation
    @transaction.atomic
    def post(self, request, pk):
        report = _locked_report(request, pk)
        if not report:
            return Response({'code': 400, 'msg': '当前报单不可撤回'})
        if report.status != ProviderReport.Status.PENDING:
            return Response({'code': 400, 'msg': '仅待审核报单可撤回，请刷新核对审核结果'})
        report.status = ProviderReport.Status.DRAFT
        report.submitted_at = None
        report.save(update_fields=['status', 'submitted_at', 'updated_at'])
        if report.order_id:
            from engagement.confirmation import refresh_report_clock
            refresh_report_clock(report.order)
        from orders.notifier import notify_report_update
        transaction.on_commit(lambda: notify_report_update(report))
        return Response({'code': 0, 'msg': '报单已撤回，可修改后重新提交', 'data': ReportSerializer(report, context={'request': request}).data})


class DepositView(APIView):
    """陪玩押金：GET 返回应缴/已缴/差额与钱包余额，POST 用余额缴纳押金。

    缴纳在 transaction.atomic + select_for_update 内执行，**四件事同生共死**：
    扣陪玩钱包余额、累加 ``EscortProfile.deposit_paid``、记陪玩侧 DEPOSIT
    负数流水、给平台钱包记 DEPOSIT_INCOME 正数流水并加钱。

    最后一件是补上的：此前只扣陪玩、不给平台入账，这笔钱从陪玩余额里消失后
    在账面上**没有任何落点**——平台钱包余额不动，全站 ``sum(wallet.balance)``
    凭空少一块。与提现代扣税（``WITHDRAW`` / ``WITHDRAW_TAX``）、订单结算
    （``INCOME`` / ``SHOP_INCOME``）一样，收付两侧必须各留一条流水。
    """

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'wallet_write'

    def _overview(self, profile, wallet):
        from .club_rules import deposit_income_percent, service_clock_overview
        deposit_required = profile.effective_deposit_required
        deposit_paid = profile.deposit_paid
        return {
            'deposit_required': deposit_required,
            'deposit_paid': deposit_paid,
            'deposit_remaining': max(deposit_required - deposit_paid, 0),
            'balance': wallet.balance,
            'deposit_mode': profile.deposit_mode,
            'deposit_percent': deposit_income_percent(),
            **service_clock_overview(),
        }

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看押金'})
        profile = EscortProfile.objects.filter(account=request.account).first()
        if profile is None:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料'})
        wallet, _ = _get_wallet(request)
        from console.deposit_refund import preview_deposit_refund
        data = self._overview(profile, wallet)
        data['refund_eligibility'] = preview_deposit_refund(profile)
        return Response({'code': 0, 'data': data})

    @recover_fund_result('deposit')
    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可缴纳押金'})

        try:
            amount = int(request.data.get('amount', 0))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '金额非法'})
        if amount <= 0:
            return Response({'code': 400, 'msg': '金额非法'})

        trace_id = new_trace_id()
        # 同上：首次缴纳成功后应缴差额变小，重放会先撞「超过应缴差额」的 400，
        # 必须先把「这是重放」这件事讲清楚。
        replayed, replay_reason = peek_fund_write(
            account=request.account,
            request_id=request.data.get('client_request_id'),
        )
        if replayed:
            log_money_event(
                'deposit.duplicate',
                account_id=request.account.pk,
                amount=amount,
                trace_id=trace_id,
                idempotent_hit=True,
                reason=replay_reason,
            )
            return Response({'code': 409, 'msg': replay_reason})

        from .deposit_payment import snapshot_storage_available
        if not snapshot_storage_available():
            return Response({'code': 503, 'msg': '押金缴纳记录尚未就绪，请联系门店处理'}, status=503)

        with transaction.atomic():
            if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_active_provider_operation
                try:
                    active = lock_active_provider_operation(request.account)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系客服'}, status=503)
                if not active:
                    return Response({'code': 409, 'msg': '陪玩账号状态已变化，请重新登录'}, status=409)
            profile = (
                EscortProfile.objects.select_for_update()
                .filter(account=request.account)
                .first()
            )
            if profile is None:
                return Response({'code': 403, 'msg': '当前账号未创建大神资料'})

            if profile.deposit_mode == 'EARNINGS':
                return Response({'code': 400, 'msg': '打单抵扣模式由订单净收益自动抵扣押金'})
            target = profile.effective_deposit_required
            paid_before = profile.deposit_paid
            remaining = target - paid_before
            if remaining <= 0:
                return Response({'code': 400, 'msg': '押金已缴清，无需再缴'})
            if amount > remaining:
                return Response({'code': 400, 'msg': f'缴纳金额超过应缴差额 {remaining / 10:g} {current_currency_name()}'})

            from .deposit_cycle import deposit_cycle
            try:
                cycle = deposit_cycle(profile, increase=True)
            except ValueError as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)

            wallet, _ = _get_wallet(request, for_update=True)
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': f'钱包{current_currency_name()}不足'})

            # 全部校验通过后再占幂等键：校验失败的 `return` 会正常提交事务，
            # 提前占键会让用户补足余额后无法用同一个 client_request_id 重试。
            allowed, reason = claim_fund_write(
                account=request.account,
                request_id=request.data.get('client_request_id'),
                scope='deposit',
                fingerprint=str(amount),
            )
            if not allowed:
                log_money_event(
                    'deposit.duplicate',
                    account_id=request.account.pk,
                    amount=amount,
                    trace_id=trace_id,
                    idempotent_hit=True,
                    reason=reason,
                )
                return Response({'code': 409, 'msg': reason})

            balance_before = wallet.balance
            wallet.balance -= amount
            wallet.save(update_fields=['balance'])

            profile.deposit_paid += amount
            profile.save(update_fields=['deposit_paid', 'deposit_business_cycle'])

            provider_deposit = Transaction.objects.create(
                wallet=wallet,
                deposit_owner_id=profile.account_id,
                deposit_business_cycle=cycle,
                amount=-amount,
                tx_type=Transaction.TxType.DEPOSIT,
                balance_before=balance_before,
                balance_after=wallet.balance,
                status=Transaction.Status.SUCCESS,
                remark='缴纳押金',
            )

            # ---- 平台侧配对入账 ----
            #
            # 押金从陪玩钱包里扣走了，钱得有个去处。不给平台入账的话这笔钱
            # 在账面上直接蒸发：平台钱包余额不动，全站余额合计凭空变少，
            # 将来退还押金时平台账上也没有对应的钱可以吐出来。
            #
            # 写在同一个 ``transaction.atomic`` 内，与上面三件事同生共死——
            # 平台侧一旦失败（比如钱包行锁超时），陪玩的扣款、已缴押金累加、
            # DEPOSIT 流水会一并回滚，不存在"陪玩钱扣了、平台没收到"的中间态。
            #
            # 不传 ``order``：押金不属于任何订单，与 WITHDRAW_TAX 同理
            # （``Transaction.order`` 本就 nullable）。
            platform_wallet = get_platform_wallet(for_update=True)
            platform_before = platform_wallet.balance
            platform_wallet.balance += amount
            platform_wallet.save(update_fields=['balance'])
            platform_deposit = Transaction.objects.create(
                wallet=platform_wallet,
                deposit_owner_id=profile.account_id,
                deposit_business_cycle=cycle,
                deposit_pair=provider_deposit,
                amount=amount,
                tx_type=Transaction.TxType.DEPOSIT_INCOME,
                balance_before=platform_before,
                balance_after=platform_wallet.balance,
                status=Transaction.Status.SUCCESS,
                remark=f'收取押金：{profile.display_name or request.account.username}',
            )
            DepositPaymentSnapshot.objects.create(profile=profile, account=request.account,
                account_business_cycle=cycle,
                provider_transaction=provider_deposit, platform_transaction=platform_deposit,
                amount=amount, paid_before=paid_before, paid_after=profile.deposit_paid, target_snapshot=target)

            log_money_event(
                'deposit.pay',
                account_id=request.account.pk,
                user_id=getattr(request.legacy_user, 'pk', None),
                tx_type=Transaction.TxType.DEPOSIT,
                amount=-amount,
                balance_before=balance_before,
                balance_after=wallet.balance,
                trace_id=trace_id,
            )
            log_money_event(
                'deposit.platform_credit',
                account_id=request.account.pk,
                tx_type=Transaction.TxType.DEPOSIT_INCOME,
                amount=amount,
                balance_before=platform_before,
                balance_after=platform_wallet.balance,
                trace_id=trace_id,
            )

            result = fund_success(request, 'deposit', msg='押金缴纳成功',
                                  data=self._overview(profile, wallet))
            from .notifier import notify_wallet_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
        return result
