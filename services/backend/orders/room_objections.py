"""Store-local room objections; these are not refunds or formal aftersales."""
from datetime import timedelta
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from .models import Order, OrderRoom, OrderRoomObjection, OrderRoomConfirmation
from .reservation_permissions import staff_can


def _valid_text(value, maximum):
    return isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip()


def _data(row):
    return {'id': row.pk, 'order_id': row.room.order_id, 'reason': row.reason, 'status': row.status,
            'created_at': row.created_at, 'resolved_at': row.resolved_at, 'resolution': row.resolution,
            'confirmation_ready': row.confirmation_ready, 'remaining_microseconds': row.remaining_microseconds,
            'resolution_proposal_id': row.resolution_proposal_id}


def resolve_for_negotiated_termination_locked(order, *, proposal_id, operator, legacy_operator, audit_path=None):
    """Close an open room objection with the verified whole-order agreement.

    The caller has locked the order and verified the accepted refund/zero-refund
    posting. The objection, its invalidated timer and the refund commit together.
    """
    from .models import NegotiationProposal
    from .state_machine import IllegalTransitionError
    room = OrderRoom.objects.select_for_update().filter(order=order).first()
    if not room:
        raise IllegalTransitionError('房间记录待核对，不能结束订单')
    objections = list(room.objections.select_for_update().filter(status=OrderRoomObjection.Status.OPEN))
    if not objections:
        return
    if len(objections) != 1:
        raise IllegalTransitionError('房间异议记录异常，请先核对')
    proposal = NegotiationProposal.objects.select_related('case').filter(pk=proposal_id, case__order=order,
                                                                        status='ACCEPTED').first()
    row = objections[0]
    if not proposal or not operator or row.customer_id != order.customer_account_id:
        raise IllegalTransitionError('房间异议与协商凭证待核对')
    if row.confirmation_id:
        wait = OrderRoomConfirmation.objects.select_for_update().filter(pk=row.confirmation_id, request__room=room).first()
        if not wait or wait.status != 'INVALIDATED' or wait.invalidation_reason != 'NEGOTIATED_TERMINATION':
            raise IllegalTransitionError('房间异议关联的确认计时待核对')
        row.confirmation_ready = False
    row.status = OrderRoomObjection.Status.RESOLVED
    row.resolved_at, row.resolved_by = timezone.now(), operator
    row.resolution = '双方确认整单协商终止，协商结案时同步处理房间异议'
    row.resolution_request_id = f'negotiation-{proposal.pk}'
    row.resolution_proposal = proposal
    row.save(update_fields=['status', 'resolved_at', 'resolved_by', 'resolution', 'resolution_request_id',
                            'confirmation_ready', 'resolution_proposal'])
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=audit_path or f'/api/admin/aftersales/{proposal.case_id}/negotiation-execution/',
        resource='room-objection', object_id=str(row.pk),
        request_body={'order_id': order.pk, 'case_id': proposal.case_id, 'proposal_id': proposal.pk,
                      'resolution': row.resolution}, status_code=200)
    _notify_objection_result(order, row, '订单协商终止后，房间异议已同步处理。')


def _notify_objection_result(order, row, preview):
    from site_messages.room_objection_notice import enabled, record
    if enabled():
        record(row, account_id=order.customer_account_id, kind='RESOLVED', cycle=order.business_cycle,
               title='房间异议已处理', preview=preview)
    else:
        from site_messages.utils import create_cycle_message
        create_cycle_message(recipient_account_id=order.customer_account_id, msg_type='ORDER',
            recipient_business_cycle=order.business_cycle,
            title='房间异议已处理', preview=preview, related_order_id=order.pk)


def _notify_store_support(title='房间异议待处理', preview='本店有订单房间异议，请在后台查看处理。'):
    """Create durable generic notices only for currently authorized store staff."""
    from console.models import AdminMembership
    from site_messages.utils import create_cycle_message
    memberships = AdminMembership.objects.filter(is_active=True, account__is_active=True,
        account__can_login=True, account__account_type=ClubAccount.AccountType.STAFF).select_related('account')
    accounts = [row.account for row in memberships if staff_can(row.account, 'order:view')
                and staff_can(row.account, 'support:edit')]
    for account in accounts:
        create_cycle_message(recipient_account_id=account.pk, recipient_business_cycle=account.business_cycle,
            msg_type='SUPPORT', title=title, preview=preview)


class RoomObjectionView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def _room(self, account, order_id, *, lock=False):
        if account.account_type != ClubAccount.AccountType.BOSS or not 0 < order_id <= 9223372036854775807:
            return None, None
        orders = Order.objects.filter(pk=order_id, customer_account=account)
        if lock:
            orders = orders.select_for_update()
        order = orders.first()
        rooms = OrderRoom.objects.filter(order=order) if order else OrderRoom.objects.none()
        if lock:
            rooms = rooms.select_for_update()
        return order, rooms.first()

    def get(self, request, order_id):
        _, room = self._room(request.account, order_id)
        if not room:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        from console.pagination import ConsolePagination
        paginator = ConsolePagination()
        page = paginator.paginate_queryset(room.objections.select_related('room').order_by('-pk'), request)
        return paginator.get_paginated_response([_data(row) for row in page])

    @transaction.atomic
    def post(self, request, order_id):
        order, room = self._room(request.account, order_id, lock=True)
        if not room:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        if (not isinstance(request.data, dict) or set(request.data) != {'reason', 'client_request_id'}
                or not _valid_text(request.data['reason'], 1000) or not _valid_text(request.data['client_request_id'], 64)):
            return Response({'code': 400, 'msg': '请填写异议内容及有效请求编号'}, status=400)
        reason, key = request.data['reason'], request.data['client_request_id']
        existing = room.objections.filter(request_id=key).first()
        if existing:
            if existing.customer_id != request.account.pk or existing.reason != reason:
                return Response({'code': 409, 'errorCode': 'REQUEST_CONFLICT', 'msg': '请求编号已用于其他内容'}, status=409)
            return Response({'code': 0, 'data': _data(existing)})
        if order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE):
            return Response({'code': 400, 'msg': '订单当前不能提出房间异议'}, status=400)
        if room.objections.filter(status=OrderRoomObjection.Status.OPEN).exists():
            return Response({'code': 409, 'errorCode': 'OBJECTION_OPEN', 'msg': '已有异议待客服处理'}, status=409)
        wait = OrderRoomConfirmation.objects.select_for_update().filter(request__room=room, status='RUNNING').first()
        now, remaining = timezone.now(), None
        if wait:
            # The boss may object after the deadline until the auto-confirm
            # transaction actually completes. An elapsed clock pauses at zero.
            remaining = max(0, (wait.deadline - now) // timedelta(microseconds=1))
            wait.status, wait.paused_at, wait.deadline = 'PAUSED', now, None
            wait.remaining_microseconds = remaining
            wait.save(update_fields=['status', 'paused_at', 'deadline', 'remaining_microseconds'])
        row = OrderRoomObjection.objects.create(room=room, customer=request.account, request_id=key, reason=reason,
                                                confirmation=wait, remaining_microseconds=remaining)
        from site_messages.room_objection_notice import enabled, record_open
        if enabled():
            record_open(row)
        else:
            _notify_store_support()
        return Response({'code': 0, 'data': _data(row), 'msg': '异议已提交，等待客服处理'})


def _authorized(account):
    return staff_can(account, 'order:view') and staff_can(account, 'support:edit')


class RoomObjectionQueueView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权查看房间异议'}, status=403)
        from console.pagination import ConsolePagination
        queryset = OrderRoomObjection.objects.filter(status=OrderRoomObjection.Status.OPEN).select_related('room').order_by('created_at', 'pk')
        paginator = ConsolePagination()
        page = paginator.paginate_queryset(queryset, request)
        return paginator.get_paginated_response([_data(row) for row in page])


class RoomObjectionResolveView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    def post(self, request, objection_id):
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权处理房间异议'}, status=403)
        if not 0 < objection_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '异议不存在'}, status=404)
        if (not isinstance(request.data, dict) or set(request.data) not in (
                {'resolution', 'client_request_id'}, {'resolution', 'client_request_id', 'confirmation_ready'})
                or not _valid_text(request.data['resolution'], 1000)
                or not _valid_text(request.data['client_request_id'], 64)
                or ('confirmation_ready' in request.data and type(request.data['confirmation_ready']) is not bool)):
            return Response({'code': 400, 'msg': '请填写处理结果及有效请求编号'}, status=400)
        initial = OrderRoomObjection.objects.filter(pk=objection_id).values('room__order_id').first()
        if not initial:
            return Response({'code': 404, 'msg': '异议不存在'}, status=404)
        order = Order.objects.select_for_update().get(pk=initial['room__order_id'])
        room = OrderRoom.objects.select_for_update().get(order=order)
        row = OrderRoomObjection.objects.select_for_update().select_related('room').get(pk=objection_id, room=room)
        if not order.customer_account_id or row.customer_id != order.customer_account_id:
            return Response({'code': 409, 'errorCode': 'OBJECTION_REVIEW', 'msg': '异议与订单身份不一致，请先核对'}, status=409)
        key, resolution = request.data['client_request_id'], request.data['resolution']
        if row.status == OrderRoomObjection.Status.RESOLVED:
            if (row.resolved_by_id == request.account.pk and row.resolution_request_id == key
                    and row.resolution == resolution and row.confirmation_ready == request.data.get('confirmation_ready')):
                return Response({'code': 0, 'data': _data(row)})
            return Response({'code': 409, 'errorCode': 'OBJECTION_RESOLVED', 'msg': '异议已处理，请查看原记录'}, status=409)
        if row.confirmation_id:
            if 'confirmation_ready' not in request.data:
                return Response({'code': 400, 'msg': '请确认房间是否仍满足结单条件'}, status=400)
            wait = OrderRoomConfirmation.objects.select_for_update().select_related('request').get(pk=row.confirmation_id)
            if wait.request.room_id != room.pk:
                return Response({'code': 409, 'msg': '房间确认计时待核对'}, status=409)
            if wait.status == 'INVALIDATED' and wait.invalidation_reason in ('TRANSFER', 'NEGOTIATED_TERMINATION'):
                if request.data['confirmation_ready']:
                    return Response({'code': 409, 'msg': '订单履约人员已变化，请由当前陪玩重新申请核实'}, status=409)
            elif (wait.status != 'PAUSED' or wait.remaining_microseconds != row.remaining_microseconds
                  or wait.remaining_microseconds is None):
                return Response({'code': 409, 'msg': '房间确认计时待核对'}, status=409)
            elif request.data['confirmation_ready']:
                from .room_completion_review import _completion_evidence_ready, _participants, _payment_eligible
                from .refunds import negotiated_aftersale_blocks_completion
                participants = _participants(order)
                if (order.status != Order.Status.IN_SERVICE or not _payment_eligible(order, participants)
                        or participants != wait.request.participants or negotiated_aftersale_blocks_completion(order)):
                    return Response({'code': 409, 'msg': '订单状态或参与人已变化，请核对结单条件'}, status=409)
                if not _completion_evidence_ready(order, participants):
                    return Response({'code': 409, 'msg': '结单凭证待核对，请当前陪玩重新提交所需凭证'}, status=409)
                wait.status, wait.deadline = 'RUNNING', timezone.now() + timedelta(microseconds=wait.remaining_microseconds)
            else:
                wait.status, wait.deadline = 'INVALIDATED', None
                wait.invalidated_at, wait.invalidated_by, wait.invalidation_reason = timezone.now(), request.account, 'CONDITION_CHANGED'
            if wait.status != 'INVALIDATED' or wait.invalidation_reason not in ('TRANSFER', 'NEGOTIATED_TERMINATION'):
                wait.paused_at, wait.remaining_microseconds = None, None
                wait.save(update_fields=['status', 'deadline', 'paused_at', 'remaining_microseconds',
                                         'invalidated_at', 'invalidated_by', 'invalidation_reason'])
            row.confirmation_ready = request.data['confirmation_ready']
        elif 'confirmation_ready' in request.data:
            return Response({'code': 400, 'msg': '该异议未暂停房间确认'}, status=400)
        row.status, row.resolution, row.resolution_request_id = row.Status.RESOLVED, resolution, key
        row.resolved_at, row.resolved_by = timezone.now(), request.account
        row.save(update_fields=['status', 'resolution', 'resolution_request_id', 'resolved_at', 'resolved_by', 'confirmation_ready'])
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=request.path[:255], resource='room-objection', object_id=str(row.pk),
            request_body={'order_id': order.pk, 'resolution': resolution, 'client_request_id': key,
                          'confirmation_ready': row.confirmation_ready}, status_code=200)
        _notify_objection_result(order, row, '请查看房间中的处理结果。')
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': _data(row), 'msg': '异议处理结果已记录'})
