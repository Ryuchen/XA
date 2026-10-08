from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.viewsets import ReadOnlyModelViewSet
from club_accounts.permissions import IsClubAccountAuthenticated
from console.mixins import EnvelopeViewSetMixin
from .models import Order, AfterSale
from engagement.confirmation import aftersale_deadline


class AfterSaleSerializer(serializers.ModelSerializer):
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    class Meta:
        model = AfterSale
        fields = '__all__'


class AfterSaleView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    def get(self, request, order_id):
        order = get_object_or_404(Order, pk=order_id, customer_account=request.account)
        row = order.aftersales.first()
        return Response({'code': 0, 'data': AfterSaleSerializer(row).data if row else None})

    @transaction.atomic
    def post(self, request, order_id):
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id, customer_account=request.account)
        reason = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('reason'))
        existing = order.aftersales.first()
        if existing:
            return Response({'code': 0, 'data': AfterSaleSerializer(existing).data, 'msg': '售后已提交'})
        deadline = aftersale_deadline(order)
        if (order.status != 'COMPLETED' or order.payment_status != 'PAID'
                or not deadline or timezone.now() >= deadline or order.consumption_confirmed_at):
            raise serializers.ValidationError('订单已结束售后期或不符合售后条件')
        row = AfterSale.objects.create(order=order, account=request.account, reason=reason)
        return Response({'code': 0, 'data': AfterSaleSerializer(row).data, 'msg': '售后已提交，处理完成前暂停实销确认'})


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
        Order.objects.select_for_update().get(pk=initial.order_id)
        row = AfterSale.objects.select_for_update().get(pk=initial.pk)
        if row.status != 'OPEN':
            raise serializers.ValidationError('该售后已处理')
        row.resolution = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('resolution'))
        row.status = 'RESOLVED'
        row.operator = request.account
        row.resolved_at = timezone.now()
        row.save()
        return Response({'code': 0, 'data': self.get_serializer(row).data})

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def refund(self, request, pk=None):
        from .state_machine import transition, IllegalTransitionError
        from .services import refund_to_customer
        from .settlement import SettlementError
        initial = self.get_object()
        order = Order.objects.select_for_update().get(pk=initial.order_id)
        row = AfterSale.objects.select_for_update().get(pk=initial.pk)
        if row.status != 'OPEN':
            raise serializers.ValidationError('该售后已处理')
        resolution = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('resolution'))
        try:
            transition(order.pk, 'CANCELLED', operator=request.legacy_user, operator_account=request.account,
                reason=resolution, side_effect=lambda o: refund_to_customer(o, resolution),
                update_fields=['payment_status', 'refunded_at'])
        except (IllegalTransitionError, SettlementError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        row.status = 'REFUNDED'
        row.resolution = resolution
        row.operator = request.account
        row.resolved_at = timezone.now()
        row.save()
        return Response({'code': 0, 'data': self.get_serializer(row).data})
