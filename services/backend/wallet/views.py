from django.db import transaction
from django.db.models import Q, Sum
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from users.models import EscortProfile

from .models import (
    DisposeRecord,
    ProviderReport,
    ProviderReportImage,
    Transaction,
    WithdrawRequest,
    compute_withdraw_tax,
    get_min_withdraw_amount,
    get_withdraw_tax_rate,
)
from .serializers import ReportSerializer, WithdrawRequestSerializer
from .services import claim_fund_write, get_wallet, peek_fund_write
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


class WalletInfoView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        wallet, _ = _get_wallet(request)
        return Response({'code': 0, 'data': {'balance': wallet.balance}})


class TopupView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        # 客户端不再允许直接修改余额。企微客服确认收款后，
        # 再由管理端按 1:10 兑换规则确认兴安币入账。
        return Response({
            'code': 403,
            'msg': '请联系企业微信客服充值，确认收款后按 1:10 兑换规则入账兴安币',
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

        from orders.models import Order

        dispose_qs = DisposeRecord.objects.filter(account=request.account).order_by('-created_at')
        dispose_records = [{
            'id': item.id,
            'type': item.dispose_type,
            'type_display': item.get_dispose_type_display(),
            'amount': item.amount,
            'signed_amount': item.amount if item.dispose_type == DisposeRecord.DisposeType.REWARD else -item.amount,
            'reason': item.reason,
            'created_at': item.created_at.isoformat(),
        } for item in dispose_qs]

        gift_orders = Order.objects.filter(
            Q(provider_account=request.account)
            | Q(providers__provider_account=request.account),
            status=Order.Status.COMPLETED,
            service__service_category__is_gift=True,
        ).select_related('customer', 'service').prefetch_related('providers').distinct().order_by('-completed_at', '-created_at')

        gift_records = []
        total_gift_amount = 0
        total_gift_income = 0
        for order in gift_orders:
            assignment = next(
                (
                    item for item in order.providers.all()
                    if item.provider_account_id == request.account.id
                ),
                None,
            )
            provider_income = assignment.provider_income if assignment else order.provider_income
            total_gift_amount += order.amount
            total_gift_income += provider_income
            gift_records.append({
                'id': order.id,
                'order_no': order.order_no,
                'service_name': order.service_name_snapshot or order.service.name,
                'customer_name': order.customer.nickname or order.customer.username or '老板',
                'amount': order.amount,
                'provider_income': provider_income,
                'completed_at': (order.completed_at or order.updated_at).isoformat(),
            })

        total_reward = sum(item['amount'] for item in dispose_records if item['type'] == DisposeRecord.DisposeType.REWARD)
        total_penalty = sum(item['amount'] for item in dispose_records if item['type'] == DisposeRecord.DisposeType.PENALTY)
        return Response({'code': 0, 'data': {
            'summary': {
                'total_reward': total_reward,
                'total_penalty': total_penalty,
                'total_gift_amount': total_gift_amount,
                'total_gift_income': total_gift_income,
                'gift_count': len(gift_records),
            },
            'dispose_records': dispose_records,
            'gift_records': gift_records,
        }})


class WithdrawView(APIView):
    # 出账接口：限流只作用于 POST（见 common.throttling.WriteScopedRateThrottle）。
    throttle_scope = 'wallet_write'
    """提现：GET 返回提现配置与我的申请记录，POST 提交新的提现申请。"""

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        wallet, _ = _get_wallet(request)
        requests_qs = WithdrawRequest.objects.filter(account=request.account)
        serializer = WithdrawRequestSerializer(requests_qs, many=True)
        return Response({
            'code': 0,
            'data': {
                'balance': wallet.balance,
                'frozen_amount': wallet.frozen_amount,
                'min_amount': get_min_withdraw_amount(),
                'tax_rate': get_withdraw_tax_rate(),
                'requests': serializer.data,
            },
        })

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可申请提现'})

        try:
            amount = int(request.data.get('amount', 0))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '金额非法'})
        if amount <= 0:
            return Response({'code': 400, 'msg': '金额非法'})

        min_amount = get_min_withdraw_amount()
        if amount < min_amount:
            return Response({'code': 400, 'msg': f'最低提现为 {min_amount / 10:g} 兴安币'})

        payee_method = (request.data.get('payee_method') or '').strip().upper()
        if payee_method not in WithdrawRequest.PayeeMethod.values:
            return Response({'code': 400, 'msg': '请选择有效的收款方式'})
        payee_account = (request.data.get('payee_account') or '').strip()[:100]
        payee_name = (request.data.get('payee_name') or '').strip()[:50]
        if not payee_account or not payee_name:
            return Response({'code': 400, 'msg': '请填写收款账号与收款人姓名'})
        remark = (request.data.get('remark') or '').strip()[:255]

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

        with transaction.atomic():
            wallet, _ = _get_wallet(request, for_update=True)
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': '兴安币余额不足'})

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
            tax_rate = get_withdraw_tax_rate()
            tax_amount = compute_withdraw_tax(amount, tax_rate)
            actual_amount = amount - tax_amount

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
                    f'预计到账 {actual_amount / 10:g} 兴安币）'
                    if tax_amount else '提现申请'
                ),
            )
            withdraw = WithdrawRequest.objects.create(
                user=request.legacy_user,
                account=request.account,
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
                f'；代扣税 {tax_rate}% 计 {tax_amount / 10:g} 兴安币，'
                f'实际到账 {actual_amount / 10:g} 兴安币'
            )
        return Response({
            'code': 0,
            'msg': submit_msg,
            'data': {
                'balance': wallet.balance,
                'frozen_amount': wallet.frozen_amount,
                'request': WithdrawRequestSerializer(withdraw).data,
            },
        })


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
        items = qs.order_by('-created_at')[start:start + page_size]

        data = [{
            'id': t.id,
            'tx_no': t.tx_no,
            'amount': t.amount,
            'tx_type': t.tx_type,
            'balance_before': t.balance_before,
            'balance_after': t.balance_after,
            'status': t.status,
            'remark': t.remark,
            'created_at': t.created_at.isoformat(),
        } for t in items]

        return Response({
            'code': 0,
            'data': {
                'transactions': data,
                'total': total,
                'page': page,
                'page_size': page_size,
                'balance': wallet.balance,
                'frozen_amount': wallet.frozen_amount,
            }
        })


class ReportListCreateView(APIView):
    """陪玩报单：GET 查询记录，POST 按已完成订单创建上传草稿。"""

    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        reports = ProviderReport.objects.filter(
            provider_account=request.account,
        ).select_related('order').prefetch_related('result_images')
        serializer = ReportSerializer(reports, many=True, context={'request': request})
        return Response({'code': 0, 'data': serializer.data})

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可提交报单'})
        from orders.models import Order
        try:
            order_id = int(request.data.get('order_id', 0))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请选择已完成订单'})
        order = Order.objects.filter(
            Q(provider_account=request.account)
            | Q(providers__provider_account=request.account),
            pk=order_id, status=Order.Status.COMPLETED,
        ).select_related('service').distinct().first()
        if not order:
            return Response({'code': 400, 'msg': '订单不存在、未完成或不属于当前陪玩'})
        report, _ = ProviderReport.objects.get_or_create(
            provider_account=request.account,
            order=order,
            defaults={
                'provider': request.legacy_user,
                'game_name': order.service_name_snapshot or order.service.name,
                'description': order.remark, 'amount': order.amount,
                'status': ProviderReport.Status.DRAFT,
            },
        )
        serializer = ReportSerializer(report, context={'request': request})
        return Response({'code': 0, 'msg': '报单草稿已创建', 'data': serializer.data})


class ReportImageUploadView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        report = ProviderReport.objects.filter(
            pk=pk,
            provider_account=request.account,
        ).first()
        if not report or report.status not in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
            return Response({'code': 400, 'msg': '当前报单不可上传凭证'})
        image = request.FILES.get('image')
        kind = (request.data.get('kind') or '').upper()
        if not image or kind not in ('ENTRY', 'COMPLETION', 'RESULT'):
            return Response({'code': 400, 'msg': '截图类型或文件无效'})
        if kind == 'ENTRY':
            report.entry_image = image
            report.save(update_fields=['entry_image', 'updated_at'])
        elif kind == 'COMPLETION':
            report.completion_image = image
            report.save(update_fields=['completion_image', 'updated_at'])
        else:
            if report.result_images.count() >= 9:
                return Response({'code': 400, 'msg': '战绩截图最多上传9张'})
            ProviderReportImage.objects.create(report=report, image=image)
        report.refresh_from_db()
        return Response({'code': 0, 'data': ReportSerializer(report, context={'request': request}).data})


class ReportSubmitView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, pk):
        report = ProviderReport.objects.filter(
            pk=pk,
            provider_account=request.account,
        ).first()
        if not report or report.status not in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
            return Response({'code': 400, 'msg': '当前报单不可提交'})
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
        missing = []
        if not report.entry_image: missing.append('入队截图')
        if not report.completion_image: missing.append('结单截图')
        if not report.result_images.exists(): missing.append('战绩截图')
        if missing:
            return Response({'code': 400, 'msg': f"请上传：{'、'.join(missing)}"})
        report.commission_rate = commission_rate
        report.status = ProviderReport.Status.PENDING
        report.audit_remark = ''
        report.save(update_fields=['commission_rate', 'status', 'audit_remark', 'updated_at'])
        return Response({'code': 0, 'msg': '报单已提交，等待审核', 'data': ReportSerializer(report, context={'request': request}).data})


class DepositView(APIView):
    """陪玩押金：GET 返回应缴/已缴/差额与钱包余额，POST 用余额缴纳押金。

    缴纳在 transaction.atomic + select_for_update 内执行：扣钱包余额、
    累加 EscortProfile.deposit_paid、记一笔 DEPOSIT 负数流水。
    """

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'wallet_write'

    def _overview(self, profile, wallet):
        deposit_required = profile.deposit_required
        deposit_paid = profile.deposit_paid
        return {
            'deposit_required': deposit_required,
            'deposit_paid': deposit_paid,
            'deposit_remaining': max(deposit_required - deposit_paid, 0),
            'balance': wallet.balance,
        }

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看押金'})
        profile = EscortProfile.objects.filter(account=request.account).first()
        if profile is None:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料'})
        wallet, _ = _get_wallet(request)
        return Response({'code': 0, 'data': self._overview(profile, wallet)})

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

        with transaction.atomic():
            profile = (
                EscortProfile.objects.select_for_update()
                .filter(account=request.account)
                .first()
            )
            if profile is None:
                return Response({'code': 403, 'msg': '当前账号未创建大神资料'})

            remaining = profile.deposit_required - profile.deposit_paid
            if remaining <= 0:
                return Response({'code': 400, 'msg': '押金已缴清，无需再缴'})
            if amount > remaining:
                return Response({'code': 400, 'msg': f'缴纳金额超过应缴差额 {remaining / 10:g} 兴安币'})

            wallet, _ = _get_wallet(request, for_update=True)
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': '钱包兴安币不足'})

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
            profile.save(update_fields=['deposit_paid'])

            Transaction.objects.create(
                wallet=wallet,
                amount=-amount,
                tx_type=Transaction.TxType.DEPOSIT,
                balance_before=balance_before,
                balance_after=wallet.balance,
                status=Transaction.Status.SUCCESS,
                remark='缴纳押金',
            )
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

        return Response({
            'code': 0,
            'msg': '押金缴纳成功',
            'data': self._overview(profile, wallet),
        })
