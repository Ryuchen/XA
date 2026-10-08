from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import BigIntegerField, Count, Exists, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
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
    WithdrawalAccount,
    compute_withdraw_tax,
    get_min_withdraw_amount,
    get_platform_wallet,
    get_withdraw_tax_rate,
)
from .serializers import ReportSerializer, WithdrawRequestSerializer
from .services import claim_fund_write, get_wallet, peek_fund_write
from .receipts import fund_success, recover_fund_result
from .payees import validate_payee
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

        from orders.models import Order, OrderProvider

        try:
            page_size = _withdraw_integer(request.query_params.get('page_size', 20), maximum=100)
            dispose_page = _withdraw_integer(request.query_params.get('dispose_page', 1), maximum=1000000)
            gift_page = _withdraw_integer(request.query_params.get('gift_page', 1), maximum=1000000)
        except ValueError:
            return Response({'code': 400, 'msg': '分页参数非法'})

        dispose_qs = DisposeRecord.objects.filter(account=request.account).order_by('-created_at', '-id')
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

        ownership = Q(provider_account=request.account)
        if request.legacy_user is not None:
            ownership |= Q(provider_account__isnull=True, provider=request.legacy_user)
        assignments = OrderProvider.objects.filter(order_id=OuterRef('pk'))
        own_assignments = assignments.filter(ownership).filter(
            Q(released_at__isnull=True) | Q(settlement_base__gt=0) | Q(provider_income__gt=0)
        )
        credit_owner = Q(wallet__account=request.account)
        if request.legacy_user is not None:
            credit_owner |= Q(wallet__account__isnull=True, wallet__user=request.legacy_user)
        credits = Transaction.objects.filter(credit_owner).filter(
            order_id=OuterRef('pk'), tx_type=Transaction.TxType.INCOME,
            status=Transaction.Status.SUCCESS, amount__gte=0,
        ).order_by()
        # 有成员分账时以成员为准；旧订单才回退订单头。实得只来自成功入账流水。
        gift_orders = Order.objects.filter(
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
            service__service_category__is_gift=True,
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
        ).select_related('customer', 'service').order_by('-completed_at', '-created_at', '-id')
        gift_totals = gift_orders.aggregate(
            total=Count('id'), amount=Sum('amount'), income=Sum('received_income'),
        )

        gift_records = []
        gift_start = (gift_page - 1) * page_size
        for order in gift_orders[gift_start:gift_start + page_size]:
            settled_at = order.assignment_settled_at or order.settled_at
            if (order.has_credit or settled_at) and order.received_income == order.expected_income:
                settlement_status = 'SETTLED'
            elif settled_at or order.received_income > order.expected_income:
                settlement_status = 'CHECK_REQUIRED'
            else:
                settlement_status = 'PENDING'
            gift_records.append({
                'id': order.id,
                'order_no': order.order_no,
                'service_name': order.service_name_snapshot or order.service.name,
                'customer_name': order.customer.nickname or order.customer.username or '老板',
                'amount': order.amount,
                'provider_income': order.expected_income,
                'received_income': order.received_income,
                'settlement_status': settlement_status,
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
        requests_qs = WithdrawRequest.objects.filter(account=request.account)
        serializer = WithdrawRequestSerializer(requests_qs, many=True)
        tax_rate = get_withdraw_tax_rate()
        data = {
            'balance': wallet.balance,
            'frozen_amount': wallet.frozen_amount,
            'min_amount': get_min_withdraw_amount(),
            'tax_rate': tax_rate,
            'requests': serializer.data,
        }
        from .club_rules import withdraw_balance_threshold
        threshold = withdraw_balance_threshold()
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

        return Response({'code': 0, 'data': data})

    @recover_fund_result('withdraw')
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

        with transaction.atomic():
            wallet, _ = _get_wallet(request, for_update=True)
            from .club_rules import withdraw_balance_threshold
            threshold = withdraw_balance_threshold()
            if threshold is not None and wallet.balance <= threshold:
                return Response({'code': 400, 'msg': '可用余额须大于50元才可提现'})
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': '兴安币余额不足'})

            if saved_id is not None:
                saved = WithdrawalAccount.objects.select_for_update().filter(pk=saved_id, account=request.account).first()
                if saved is None or saved.version != saved_version:
                    return Response({'code': 400, 'errorCode': 'PAYEE_CHANGED',
                                     'msg': '收款账户已修改或解绑，请重新选择并核对'})
                payee_method, payee_account, payee_name = saved.payee_method, saved.payee_account, saved.payee_name

            # 与用户看到的试算不一致时不占幂等键、不冻结余额。
            min_amount = get_min_withdraw_amount()
            if amount < min_amount:
                return Response({'code': 400, 'errorCode': 'WITHDRAW_QUOTE_CHANGED', 'msg': f'最低提现为 {min_amount / 10:g} 兴安币，请重新确认'})
            tax_rate = get_withdraw_tax_rate()
            tax_amount = compute_withdraw_tax(amount, tax_rate)
            actual_amount = amount - tax_amount
            if expected_quote is not None and expected_quote != (tax_rate, tax_amount, actual_amount):
                return Response({'code': 400, 'errorCode': 'WITHDRAW_QUOTE_CHANGED', 'msg': '提现规则已更新，请重新确认试算金额'})

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
            result = fund_success(request, 'withdraw', msg=submit_msg, data={
                'balance': wallet.balance, 'frozen_amount': wallet.frozen_amount,
                'request': WithdrawRequestSerializer(withdraw).data,
            })
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
        items = qs.order_by('-created_at', '-id')[start:start + page_size]

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
                'total_income': wallet.transactions.filter(
                    tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                    amount__gt=0,
                ).aggregate(total=Sum('amount'))['total'] or 0,
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

    @transaction.atomic
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
            pk=order_id, status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED],
        ).select_related('service').distinct().first()
        if not order:
            return Response({'code': 400, 'msg': '订单不存在、未完成或不属于当前陪玩'})
        if order.source_order_id or (order.service.service_category_id and order.service.service_category.is_gift):
            return Response({'code': 400, 'msg': '礼物打赏不需要服务报单'})
        # 与开始/完成服务入口统一用 provider + order 唯一键，兼容历史未回填账户的草稿。
        share = order.providers.filter(provider_account=request.account).first()
        if share and share.released_at and share.settlement_base <= 0:
            return Response({'code': 400, 'msg': '转单后本人无结算份额，无需提交报单'})
        report, _ = ProviderReport.objects.get_or_create(
            provider=request.legacy_user,
            order=order,
            defaults={
                'provider_account': request.account,
                'game_name': order.service_name_snapshot or order.service.name,
                'description': order.remark, 'amount': share.settlement_base if share else order.amount,
                'commission_rate': share.commission_rate if share else order.commission_rate,
                'status': ProviderReport.Status.DRAFT,
            },
        )
        if not report.provider_account_id:
            report.provider_account = request.account
            report.save(update_fields=['provider_account'])
        serializer = ReportSerializer(report, context={'request': request})
        return Response({'code': 0, 'msg': '报单草稿已创建', 'data': serializer.data})


def _locked_report(request, pk):
    """与结单、退款、审核保持订单→报单的锁顺序。调用方已开启事务。"""
    from orders.models import Order
    initial = ProviderReport.objects.filter(pk=pk, provider_account=request.account).only('order_id').first()
    if initial is None:
        return None
    if initial.order_id:
        Order.objects.select_for_update().get(pk=initial.order_id)
    return ProviderReport.objects.select_for_update().get(pk=pk, provider_account=request.account)


class ReportImageUploadView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request, pk):
        from orders.services import report_edit_error
        report = _locked_report(request, pk)
        error = report_edit_error(report) if report else '当前报单不可上传凭证'
        if error:
            return Response({'code': 400, 'msg': error})
        image = request.FILES.get('image')
        kind = (request.data.get('kind') or '').upper()
        if not image or kind not in ('ENTRY', 'COMPLETION', 'RESULT'):
            return Response({'code': 400, 'msg': '截图类型或文件无效'})
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
            ProviderReportImage.objects.create(report=report, image=image)
        report.refresh_from_db()
        return Response({'code': 0, 'data': ReportSerializer(report, context={'request': request}).data})


class ReportImageDeleteView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
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
        return Response({'code': 0, 'data': ReportSerializer(report, context={'request': request}).data})


class ReportSubmitView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        from orders.services import report_edit_error
        report = _locked_report(request, pk)
        error = report_edit_error(report) if report else '当前报单不可提交'
        if error:
            return Response({'code': 400, 'msg': error})
        from orders.models import Order
        if report.order_id and (report.order.status != Order.Status.COMPLETED or report.order.payment_status != Order.PaymentStatus.PAID):
            return Response({'code': 400, 'msg': '服务完成后才能提交报单，已退款订单不可提交'})
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
            if share and share.released_at and share.settlement_base <= 0:
                return Response({'code': 400, 'msg': '本人无结算份额，无需提交报单'})
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
        report.submitted_at = timezone.now()
        report.save(update_fields=['amount', 'commission_rate', 'status', 'audit_remark', 'submitted_at', 'updated_at'])
        if report.order_id:
            from engagement.confirmation import refresh_report_clock
            refresh_report_clock(report.order)
        return Response({'code': 0, 'msg': '报单已提交，等待审核', 'data': ReportSerializer(report, context={'request': request}).data})


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
        from .club_rules import deposit_income_percent, order_hold_hours
        deposit_required = profile.effective_deposit_required
        deposit_paid = profile.deposit_paid
        return {
            'deposit_required': deposit_required,
            'deposit_paid': deposit_paid,
            'deposit_remaining': max(deposit_required - deposit_paid, 0),
            'balance': wallet.balance,
            'deposit_mode': profile.deposit_mode,
            'deposit_percent': deposit_income_percent(),
            'hold_hours': order_hold_hours(),
        }

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看押金'})
        profile = EscortProfile.objects.filter(account=request.account).first()
        if profile is None:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料'})
        wallet, _ = _get_wallet(request)
        return Response({'code': 0, 'data': self._overview(profile, wallet)})

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

        with transaction.atomic():
            profile = (
                EscortProfile.objects.select_for_update()
                .filter(account=request.account)
                .first()
            )
            if profile is None:
                return Response({'code': 403, 'msg': '当前账号未创建大神资料'})

            if profile.deposit_mode == 'EARNINGS':
                return Response({'code': 400, 'msg': '打单抵扣模式由订单净收益自动抵扣押金'})
            remaining = profile.effective_deposit_required - profile.deposit_paid
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
            Transaction.objects.create(
                wallet=platform_wallet,
                amount=amount,
                tx_type=Transaction.TxType.DEPOSIT_INCOME,
                balance_before=platform_before,
                balance_after=platform_wallet.balance,
                status=Transaction.Status.SUCCESS,
                remark=f'收取押金：{profile.display_name or request.account.username}',
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
        return result
