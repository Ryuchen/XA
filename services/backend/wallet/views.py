from django.db import transaction
from django.db.models import Q, Sum
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import EscortProfile

from .models import (
    DisposeRecord,
    ProviderReport,
    ProviderReportImage,
    Transaction,
    Wallet,
    WithdrawRequest,
    get_min_withdraw_amount,
)
from .serializers import ReportSerializer, WithdrawRequestSerializer


class WalletInfoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        return Response({'code': 0, 'data': {'balance': wallet.balance}})


class TopupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # 客户端不再允许直接修改余额。企微客服确认收款后，
        # 再由管理端按 1:10 兑换规则确认兴安币入账。
        return Response({
            'code': 403,
            'msg': '请联系企业微信客服充值，确认收款后按 1:10 兑换规则入账兴安币',
        })


class IncomeRecordsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
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

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != request.user.Role.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看此记录'})

        from orders.models import Order

        dispose_qs = DisposeRecord.objects.filter(user=request.user).order_by('-created_at')
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
            Q(provider=request.user) | Q(providers__provider=request.user),
            status=Order.Status.COMPLETED,
            service__service_category__is_gift=True,
        ).select_related('customer', 'service').prefetch_related('providers').distinct().order_by('-completed_at', '-created_at')

        gift_records = []
        total_gift_amount = 0
        total_gift_income = 0
        for order in gift_orders:
            assignment = next(
                (item for item in order.providers.all() if item.provider_id == request.user.id),
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
    """提现：GET 返回提现配置与我的申请记录，POST 提交新的提现申请。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        requests_qs = WithdrawRequest.objects.filter(user=request.user)
        serializer = WithdrawRequestSerializer(requests_qs, many=True)
        return Response({
            'code': 0,
            'data': {
                'balance': wallet.balance,
                'frozen_amount': wallet.frozen_amount,
                'min_amount': get_min_withdraw_amount(),
                'requests': serializer.data,
            },
        })

    def post(self, request):
        if request.user.role != request.user.Role.PROVIDER:
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

        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(user=request.user)
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': '兴安币余额不足'})

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
                remark='提现申请',
            )
            withdraw = WithdrawRequest.objects.create(
                user=request.user,
                amount=amount,
                payee_method=payee_method,
                payee_account=payee_account,
                payee_name=payee_name,
                remark=remark,
                transaction=tx,
            )

        return Response({
            'code': 0,
            'msg': '提现申请已提交，等待审核',
            'data': {
                'balance': wallet.balance,
                'frozen_amount': wallet.frozen_amount,
                'request': WithdrawRequestSerializer(withdraw).data,
            },
        })


class TransactionsView(APIView):
    """统一钱包流水：支持按类型筛选 + 分页。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
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

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        reports = ProviderReport.objects.filter(provider=request.user).select_related('order').prefetch_related('result_images')
        serializer = ReportSerializer(reports, many=True, context={'request': request})
        return Response({'code': 0, 'data': serializer.data})

    def post(self, request):
        if request.user.role != request.user.Role.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可提交报单'})
        from orders.models import Order
        try:
            order_id = int(request.data.get('order_id', 0))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请选择已完成订单'})
        order = Order.objects.filter(
            Q(provider=request.user) | Q(providers__provider=request.user),
            pk=order_id, status=Order.Status.COMPLETED,
        ).select_related('service').distinct().first()
        if not order:
            return Response({'code': 400, 'msg': '订单不存在、未完成或不属于当前陪玩'})
        report, _ = ProviderReport.objects.get_or_create(
            provider=request.user, order=order,
            defaults={
                'game_name': order.service_name_snapshot or order.service.name,
                'description': order.remark, 'amount': order.amount,
                'status': ProviderReport.Status.DRAFT,
            },
        )
        serializer = ReportSerializer(report, context={'request': request})
        return Response({'code': 0, 'msg': '报单草稿已创建', 'data': serializer.data})


class ReportImageUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        report = ProviderReport.objects.filter(pk=pk, provider=request.user).first()
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
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        report = ProviderReport.objects.filter(pk=pk, provider=request.user).first()
        if not report or report.status not in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
            return Response({'code': 400, 'msg': '当前报单不可提交'})
        missing = []
        if not report.entry_image: missing.append('入队截图')
        if not report.completion_image: missing.append('结单截图')
        if not report.result_images.exists(): missing.append('战绩截图')
        if missing:
            return Response({'code': 400, 'msg': f"请上传：{'、'.join(missing)}"})
        report.status = ProviderReport.Status.PENDING
        report.audit_remark = ''
        report.save(update_fields=['status', 'audit_remark', 'updated_at'])
        return Response({'code': 0, 'msg': '报单已提交，等待审核', 'data': ReportSerializer(report, context={'request': request}).data})


class DepositView(APIView):
    """陪玩押金：GET 返回应缴/已缴/差额与钱包余额，POST 用余额缴纳押金。

    缴纳在 transaction.atomic + select_for_update 内执行：扣钱包余额、
    累加 EscortProfile.deposit_paid、记一笔 DEPOSIT 负数流水。
    """

    permission_classes = [IsAuthenticated]

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
        if request.user.role != request.user.Role.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看押金'})
        profile = EscortProfile.objects.filter(user=request.user).first()
        if profile is None:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料'})
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        return Response({'code': 0, 'data': self._overview(profile, wallet)})

    def post(self, request):
        if request.user.role != request.user.Role.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可缴纳押金'})

        try:
            amount = int(request.data.get('amount', 0))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '金额非法'})
        if amount <= 0:
            return Response({'code': 400, 'msg': '金额非法'})

        with transaction.atomic():
            profile = (
                EscortProfile.objects.select_for_update()
                .filter(user=request.user)
                .first()
            )
            if profile is None:
                return Response({'code': 403, 'msg': '当前账号未创建大神资料'})

            remaining = profile.deposit_required - profile.deposit_paid
            if remaining <= 0:
                return Response({'code': 400, 'msg': '押金已缴清，无需再缴'})
            if amount > remaining:
                return Response({'code': 400, 'msg': f'缴纳金额超过应缴差额 {remaining / 10:g} 兴安币'})

            wallet, _ = Wallet.objects.select_for_update().get_or_create(user=request.user)
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': '钱包兴安币不足'})

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

        return Response({
            'code': 0,
            'msg': '押金缴纳成功',
            'data': self._overview(profile, wallet),
        })
