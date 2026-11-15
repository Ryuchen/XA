"""Staff-attested completion of an old playground order, without linking sites."""
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import LegacyAccountMap
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Transaction, Wallet
from .models import LegacyRoomCompletion, Order, OrderParticipationSegment, OrderRoom
from .reservation_permissions import staff_can
from .services import complete_fulfillment
from .settlement import SettlementError
from .state_machine import IllegalTransitionError, transition


class LegacyRoomCompletionInput(serializers.Serializer):
    customer_account_id = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    provider_account_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=9223372036854775807), min_length=1, max_length=2)
    confirmed_at = serializers.DateTimeField()
    evidence = serializers.CharField(min_length=5, max_length=1000, trim_whitespace=True)
    client_request_id = serializers.CharField(min_length=1, max_length=64, trim_whitespace=True)

    def validate_provider_account_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError('陪玩不能重复')
        return sorted(value)


def _data(record):
    return {'order_id': record.order_id, 'customer_account_id': record.customer_id,
            'provider_account_ids': record.provider_accounts, 'confirmed_at': record.confirmed_at,
            'recorded_at': record.recorded_at, 'operator_id': record.operator_id,
            'evidence': record.evidence, 'client_request_id': record.request_id}


class LegacyRoomCompletionView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id):
        if not (staff_can(request.account, 'order:view') and staff_can(request.account, 'support:edit')):
            return Response({'code': 403, 'msg': '无权查看历史订单核实记录'}, status=403)
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '历史结单核实记录不存在'}, status=404)
        record = LegacyRoomCompletion.objects.filter(order_id=order_id).first()
        if not record:
            return Response({'code': 404, 'msg': '历史结单核实记录不存在'}, status=404)
        return Response({'code': 0, 'data': _data(record)})

    @transaction.atomic
    def post(self, request, order_id):
        if not (staff_can(request.account, 'order:view') and staff_can(request.account, 'support:edit')):
            return Response({'code': 403, 'msg': '无权核实历史订单'}, status=403)
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        if not isinstance(request.data, dict) or set(request.data) != {
                'customer_account_id', 'provider_account_ids', 'confirmed_at', 'evidence', 'client_request_id'}:
            return Response({'code': 400, 'msg': '请填写订单身份、确认时间和核实依据'}, status=400)
        serializer = LegacyRoomCompletionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        previous = LegacyRoomCompletion.objects.select_for_update().filter(order=order).first()
        if previous:
            matches = (previous.operator_id == request.account.pk and previous.request_id == values['client_request_id']
                       and previous.customer_id == values['customer_account_id']
                       and previous.provider_accounts == values['provider_account_ids']
                       and previous.confirmed_at == values['confirmed_at'] and previous.evidence == values['evidence'])
            if not matches:
                return Response({'code': 409, 'msg': '历史订单已有核实记录，请查看原记录'}, status=409)
            if order.status != Order.Status.COMPLETED or order.completed_at != previous.confirmed_at:
                return Response({'code': 409, 'msg': '历史结单状态待核对'}, status=409)
            return Response({'code': 0, 'data': _data(previous), 'msg': '历史结单已登记'})
        if (order.room_required_snapshot is not None or OrderRoom.objects.filter(order=order).exists()
                or order.is_gift_snapshot or order.source_order_id or order.status != Order.Status.IN_SERVICE
                or order.payment_status != Order.PaymentStatus.PAID or order.settled_at
                or order.refund_records.exists() or order.aftersales.filter(status='OPEN').exists()):
            return Response({'code': 409, 'msg': '订单状态或历史资金待核对，暂不能登记旧站结单'}, status=409)
        if (order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL or order.service_clock_version is None
                or order.service_aftersale_hours is None or order.service_freeze_hours is None
                or order.service_deposit_percent is None):
            return Response({'code': 409, 'msg': '历史订单的售后和收益时钟尚未核实'}, status=409)
        confirmed = values['confirmed_at']
        if (not order.in_service_at or confirmed < order.in_service_at or confirmed < order.created_at
                or confirmed > timezone.now()
                or values['customer_account_id'] != order.customer_account_id or not order.customer_id
                or not LegacyAccountMap.objects.filter(account_id=order.customer_account_id,
                                                       legacy_user_id=order.customer_id).exists()
                or not Wallet.objects.filter(account_id=order.customer_account_id, user_id=order.customer_id).exists()):
            return Response({'code': 409, 'msg': '老板身份或旧站确认时间待核对'}, status=409)
        assignments = list(order.providers.select_for_update().filter(released_at__isnull=True))
        expected_count = 2 if order.escort_mode == Order.EscortMode.DOUBLE else 1
        if (len(assignments) != expected_count or any(not row.provider_id or not row.provider_account_id for row in assignments)
                or sorted(row.provider_account_id for row in assignments) != values['provider_account_ids']
                or order.provider_account_id not in values['provider_account_ids']
                or order.providers.filter(settled_at__isnull=False).exists()
                or order.transactions.filter(status=Transaction.Status.SUCCESS,
                    tx_type__in=[Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME]).exists()):
            return Response({'code': 409, 'msg': '陪玩身份或历史收益待核对'}, status=409)
        identities = {(row.provider_id, row.provider_account_id) for row in assignments}
        profiles = set(EscortProfile.objects.filter(account_id__in=values['provider_account_ids'])
                       .values_list('user_id', 'account_id'))
        mappings = set(LegacyAccountMap.objects.filter(account_id__in=values['provider_account_ids'])
                       .values_list('legacy_user_id', 'account_id'))
        latest_segment = (OrderParticipationSegment.objects.filter(attribution__order=order, started_at__isnull=False)
                          .order_by('-started_at').values_list('started_at', flat=True).first())
        if profiles != identities or not identities.issubset(mappings) or (latest_segment and latest_segment > confirmed):
            return Response({'code': 409, 'msg': '陪玩身份或履约时间待核对'}, status=409)
        try:
            def finish(current):
                current.completed_at = confirmed
                complete_fulfillment(current, operator=request.legacy_user, operator_account=request.account)
            order = transition(order.pk, Order.Status.COMPLETED, operator=request.legacy_user,
                operator_account=request.account, action='COMPLETE', reason='客服核实旧站老板已确认结单',
                side_effect=finish, update_fields=['completed_at'])
        except (IllegalTransitionError, SettlementError) as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        record = LegacyRoomCompletion.objects.create(order=order, operator=request.account,
            customer_id=order.customer_account_id, provider_accounts=values['provider_account_ids'],
            confirmed_at=confirmed, evidence=values['evidence'], request_id=values['client_request_id'])
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=request.path[:255], resource='legacy-room-completion', object_id=str(order.pk),
            request_body={'customer_account_id': record.customer_id, 'provider_account_ids': record.provider_accounts,
                'confirmed_at': confirmed.isoformat(), 'evidence': record.evidence,
                'client_request_id': record.request_id}, status_code=200)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': _data(record), 'msg': '历史结单已核实'})
