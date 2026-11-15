"""Staff-originated continuation requests, verified by the existing workflow."""
import hashlib

from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from wallet.models import IdempotencyKey
from .continuations import (_orders_valid, _participants_current, _protect_participants,
                            _serialize, _text, enabled)
from .models import Order, OrderContinuation
from .room_objections import _authorized


class StaffContinuationClaimView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @_protect_participants
    @transaction.atomic
    def post(self, request, order_id):
        if not enabled():
            return Response({'code': 503, 'msg': '续单核实暂未开放'}, status=503)
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权提交续单核实'}, status=403)
        data = request.data
        if (not isinstance(data, dict) or set(data) != {'original_order_id', 'client_request_id'}
                or type(data['original_order_id']) is not int or data['original_order_id'] <= 0
                or not _text(data['client_request_id'], 64)):
            return Response({'code': 400, 'msg': '请选择原订单并填写有效请求编号'}, status=400)
        customer_id = Order.objects.filter(pk=order_id).values_list('customer_account_id', flat=True).first()
        customer = ClubAccount.objects.select_for_update().filter(pk=customer_id, account_type='BOSS').first()
        orders = {item.pk: item for item in Order.objects.select_for_update().filter(
            pk__in=[order_id, data['original_order_id']]).order_by('pk')}
        original, current = orders.get(data['original_order_id']), orders.get(order_id)
        if not customer or not _orders_valid(original, current, customer):
            return Response({'code': 404, 'msg': '原订单或续单不属于同一老板当前业务周期'}, status=404)
        if not _participants_current(request, current):
            return Response({'code': 409, 'msg': '续单陪玩账号或参与人已变化，请刷新后重试'}, status=409)
        fingerprint = hashlib.sha256(f'{original.pk}:{current.pk}:{customer.pk}:{customer.business_cycle}'.encode()).hexdigest()
        receipt, created = IdempotencyKey.objects.get_or_create(
            account=request.account, request_id=data['client_request_id'], defaults={
                'scope': 'continuation.staff_claim', 'request_fingerprint': fingerprint})
        if not created and (receipt.scope != 'continuation.staff_claim' or receipt.request_fingerprint != fingerprint):
            return Response({'code': 409, 'msg': '请求编号已用于其他操作'}, status=409)
        row = OrderContinuation.objects.select_for_update().filter(continuation_order=current).first()
        if row and (row.original_order_id != original.pk or row.customer_id != customer.pk
                    or row.business_cycle != customer.business_cycle):
            if created:
                receipt.delete()
            return Response({'code': 409, 'msg': '该续单已有其他核实申请'}, status=409)
        if not created:
            if not row or receipt.response != {'claim_id': row.pk}:
                return Response({'code': 409, 'msg': '申请回执待核对'}, status=409)
            return Response({'code': 0, 'data': _serialize(row), 'msg': '已提交客服核实'})
        if not row:
            key = 's:' + hashlib.sha256(f'{request.account.pk}:{request.account.business_cycle}:{data["client_request_id"]}'.encode()).hexdigest()[:62]
            row = OrderContinuation.objects.create(original_order=original, continuation_order=current,
                customer=customer, business_cycle=customer.business_cycle, request_id=key)
        receipt.response = {'claim_id': row.pk}
        receipt.save(update_fields=['response'])
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=getattr(request, 'legacy_user', None), operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=request.path[:255], resource='continuation-staff-claim', object_id=str(row.pk),
            request_body={'original_order_id': original.pk, 'continuation_order_id': current.pk}, status_code=200)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': _serialize(row), 'msg': '续单申请已登记，等待客服核实'})
