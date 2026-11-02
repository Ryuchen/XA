from django.db import transaction
import logging
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.viewsets import ReadOnlyModelViewSet
from club_accounts.permissions import IsClubAccountAuthenticated
from wallet.services import WalletAddressingError
from console.mixins import EnvelopeViewSetMixin
from .models import Order, AfterSale
from .notifier import notify_order_update
from engagement.confirmation import aftersale_deadline
from .refunds import can_request_aftersale, RefundError


def append_history(row):
    row.history = [*row.history, {'status':row.status,'reason':row.reason,'resolution':row.resolution,
        'created_at':row.created_at.isoformat(), 'resolved_at':row.resolved_at.isoformat() if row.resolved_at else None,
        'operator_id':row.operator_id,'client_request_id':row.client_request_id}]


def notify_aftersale_reports(order):
    """Refresh report-only reviewers when a case changes the settlement context."""
    try:
        from wallet.models import ProviderReport
        from .notifier import notify_report_update
        for report in ProviderReport.objects.filter(order=order):
            notify_report_update(report, notify_owner=False)
    except Exception:
        logging.getLogger(__name__).warning('售后报单通知失败: order_id=%s', order.pk, exc_info=True)


def schedule_aftersale_notices(order, *, resolved):
    """Notify offline parties without exposing the customer's private case text."""
    from .services import push_message_safe

    recipients = []
    if resolved:
        recipients.append((order.customer_id, order.customer_account_id, '售后已处理',
                           '售后已处理，请查看订单中的处理结果。'))
    shares = list(order.providers.filter(
        Q(released_at__isnull=True) | Q(settlement_base__gt=0) | Q(provider_income__gt=0)
    ).values_list('provider_id', 'provider_account_id'))
    if not shares and order.provider_id:
        shares = [(order.provider_id, order.provider_account_id)]
    if resolved:
        title, preview = '订单售后已处理', '订单售后已处理，请查看订单；收益以结算状态及钱包流水为准。'
    else:
        title = '订单售后处理中'
        preview = ('订单售后处理中，请查看订单；已结算收益以钱包流水为准。' if order.settled_at else
                   '订单售后处理中，请查看订单；本单暂不结算。')
    recipients.extend((user_id, account_id, title, preview) for user_id, account_id in shares)
    seen_users, seen_accounts = set(), set()
    for user_id, account_id, title, preview in recipients:
        if not user_id or user_id in seen_users or (account_id and account_id in seen_accounts):
            continue
        seen_users.add(user_id)
        if account_id:
            seen_accounts.add(account_id)
        transaction.on_commit(lambda user_id=user_id, account_id=account_id, title=title, preview=preview:
            push_message_safe(recipient_id=user_id, recipient_account_id=account_id, title=title,
                              preview=preview, msg_type='ORDER', related_order_id=order.pk))


class AfterSaleSerializer(serializers.ModelSerializer):
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    class Meta:
        model = AfterSale
        fields = '__all__'


class AfterSaleView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def customer_orders(self, request):
        ownership = Q(customer_account=request.account)
        if request.legacy_user is not None:
            ownership |= Q(customer_account__isnull=True, customer=request.legacy_user)
        return Order.objects.filter(ownership)

    def get(self, request, order_id):
        order = get_object_or_404(self.customer_orders(request), pk=order_id)
        row = order.aftersales.first()
        return Response({'code': 0, 'data': AfterSaleSerializer(row).data if row else None})

    @transaction.atomic
    def post(self, request, order_id):
        order = get_object_or_404(self.customer_orders(request).select_for_update(), pk=order_id)
        reason = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('reason'))
        request_id = serializers.CharField(max_length=64, allow_blank=True).run_validation(request.data.get('client_request_id',''))
        existing = order.aftersales.first()
        if existing:
            original = ({'reason': existing.reason} if request_id and existing.client_request_id == request_id else
                        next((item for item in existing.history if request_id and item.get('client_request_id') == request_id), None))
            if original:
                if original.get('reason') != reason:
                    return Response({'code':409, 'errorCode':'AFTERSALE_REQUEST_CONFLICT',
                                     'msg':'这次修改未提交。请查看已有申请，或联系客服补充说明。'}, status=409)
                return Response({'code': 0, 'data': AfterSaleSerializer(existing).data, 'msg': '售后已提交'})
            if existing.status == 'OPEN':
                if existing.reason == reason:
                    return Response({'code': 0, 'data': AfterSaleSerializer(existing).data, 'msg': '售后已提交'})
                return Response({'code':409, 'errorCode':'AFTERSALE_ALREADY_OPEN',
                                 'msg':'已有售后处理中。本次说明未提交，请查看已有申请，或联系客服补充说明。'}, status=409)
        if not can_request_aftersale(order):
            raise serializers.ValidationError('订单已结束售后期或不符合售后条件')
        if existing:
            row = existing
            if not row.history:
                append_history(row)
            row.status, row.reason, row.resolution = 'OPEN', reason, ''
            row.operator, row.resolved_at, row.created_at = None, None, timezone.now()
            row.client_request_id = request_id
        else:
            row = AfterSale(order=order, account=request.account, reason=reason, client_request_id=request_id)
            row.created_at = timezone.now()
        append_history(row)
        row.save()
        transaction.on_commit(lambda: notify_order_update(order))
        transaction.on_commit(lambda: notify_aftersale_reports(order))
        schedule_aftersale_notices(order, resolved=False)
        return Response({'code': 0, 'data': AfterSaleSerializer(row).data, 'msg': '售后已提交，等待平台处理'})


class AfterSaleViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    queryset = AfterSale.objects.select_related('order', 'account').order_by('-id')
    serializer_class = AfterSaleSerializer
    default_perm = 'order:view'
    required_perms = {'resolve':'order:settle', 'refund':'order:refund'}

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('order'):
            qs = qs.filter(order_id=serializers.IntegerField(min_value=1).run_validation(self.request.query_params['order']))
        if self.request.query_params.get('status'):
            qs = qs.filter(status=self.request.query_params['status'])
        return qs

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def resolve(self, request, pk=None):
        initial = self.get_object()
        order = Order.objects.select_for_update().get(pk=initial.order_id)
        row = AfterSale.objects.select_for_update().get(pk=initial.pk)
        if row.status != 'OPEN':
            raise serializers.ValidationError('该售后已处理')
        row.resolution = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('resolution'))
        row.status = 'RESOLVED'
        row.operator = request.account
        row.resolved_at = timezone.now()
        append_history(row)
        row.save()
        transaction.on_commit(lambda: notify_order_update(order))
        transaction.on_commit(lambda: notify_aftersale_reports(order))
        schedule_aftersale_notices(order, resolved=True)
        return Response({'code': 0, 'data': self.get_serializer(row).data})

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def refund(self, request, pk=None):
        from .state_machine import IllegalTransitionError
        from .settlement import SettlementError
        initial = self.get_object()
        order = Order.objects.select_for_update().get(pk=initial.order_id)
        row = AfterSale.objects.select_for_update().get(pk=initial.pk)
        from .refunds import replay_refund,apply_admin_refund
        resolution = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('resolution'))
        try:
            existing = replay_refund(order,request.data.get('client_request_id'),request.data.get('refund_amount'),resolution)
        except RefundError as exc:
            return Response({'code':409,'errorCode':exc.code,'msg':str(exc),'data':exc.data})
        if existing:
            return Response({'code':0,'data':self.get_serializer(row).data})
        if row.status != 'OPEN':
            raise serializers.ValidationError('该售后已处理')
        try:
            order = apply_admin_refund(order.pk,reason=resolution,operator=request.legacy_user,operator_account=request.account,
                plan_token=request.data.get('plan_token'),refund_amount=request.data.get('refund_amount'),
                client_request_id=request.data.get('client_request_id'),notify=False)
        except RefundError as exc:
            return Response({'code':409 if exc.code in ('REFUND_PLAN_CHANGED','REFUND_REQUEST_CONFLICT') else 400,
                             'errorCode':exc.code,'msg':str(exc),'data':exc.data})
        except (IllegalTransitionError, SettlementError, WalletAddressingError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        row.status = 'REFUNDED' if order.payment_status == 'REFUNDED' else 'PARTIALLY_REFUNDED'
        row.resolution = resolution
        row.operator = request.account
        row.resolved_at = timezone.now()
        append_history(row)
        row.save()
        transaction.on_commit(lambda: notify_order_update(order))
        return Response({'code': 0, 'data': self.get_serializer(row).data})
