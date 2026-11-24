"""Room completion review and its store-local confirmation clock."""
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from .models import Order, OrderRoom, OrderRoomCompletionRequest, OrderRoomConfirmation
from .room_objections import _authorized, _valid_text, _notify_store_support
from .rooms import message_role
from .room_review_lifecycle import submit as protect_submit, review as protect_review


def _formal_points_transfer(order):
    from .points_business_access import configured
    from .segment_financial_capture import covered_transfers

    return configured() and covered_transfers(order.pk).filter(
        previous_segment__attribution__financial_coverage__source='WALLET_POINTS').exists()


def _participants(order):
    values = list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
    if (not values and order.required_provider_count == 1 and order.provider_account_id
            and not order.providers.exists()):
        values = [order.provider_account_id]
    if (None in values or len(set(values)) != len(values) or order.provider_account_id not in values):
        return None
    if len(values) != order.required_provider_count:
        if order.required_provider_count != 2 or len(values) != 1:
            return None
        from rest_framework.exceptions import ValidationError
        try:
            from .ordinary_transfer_continuation import applies
            if order.payment_status == Order.PaymentStatus.PARTIALLY_REFUNDED and _formal_points_transfer(order):
                from .points_transfer_refund_access import after_transfer
                from .points_transfer_refund_continuation import verify_locked as verify_points
                with after_transfer(order.pk, phase='report', read_only=True) as locked:
                    posting = verify_points(locked)
                    continuation = (posting.termination_in_service and not posting.completed_by_negotiation
                                    and not posting.shares.filter(account_id=values[0]).exists())
            elif applies(order):
                from .ordinary_transfer_continuation import verify_locked
                continuation = verify_locked(order)
            else:
                from .negotiation_termination import continuing_team
                continuation = continuing_team(order, verify=True)
            if not continuation:
                return None
        except (ValidationError, ValueError):
            return None
    return sorted(values)


def _payment_eligible(order, participants):
    if order.payment_status == Order.PaymentStatus.PAID:
        return True
    # A verified scoped termination can leave one original team member to
    # finish even if the accounting status is partially or fully refunded.
    if not (order.required_provider_count == 2 and participants):
        return False
    if len(participants) == 1:
        return order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED)
    if len(participants) != 2 or order.payment_status != Order.PaymentStatus.PARTIALLY_REFUNDED:
        return False
    # A past segment can be ended by negotiation while that same person has
    # subsequently returned on a different, still-running segment.
    from rest_framework.exceptions import ValidationError
    from .ordinary_transfer_continuation import applies, verify_locked
    if not applies(order):
        return False
    try:
        verify_locked(order)
        from .aftersale_segment_scope import closed_segments_locked
        from .models import OrderParticipationSegment
        closed = closed_segments_locked(order)
        active = list(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account_id__in=participants, released_at__isnull=True,
            started_at__isnull=False).values_list('pk', 'provider_account_id'))
        return bool(closed is not None and len(active) == 2
                    and {account for _, account in active} == set(participants)
                    and all(segment_id not in closed for segment_id, _ in active))
    except (ValidationError, ValueError):
        return False


def _completion_evidence_ready(order, participants):
    """Preserve existing per-provider completion evidence gates for room orders."""
    from .participation_entry import pending_transfer_segments
    if pending_transfer_segments(order).exists():
        return False
    # A provider can return to the same order with an older report image.
    # Require a completion action in the current incoming work stint before
    # accepting that image as evidence for the room's new review.
    from .models import OrderWorkStint
    incoming = list(OrderWorkStint.objects.filter(order=order, account_id=order.provider_account_id,
        incoming__isnull=False, released_at__isnull=True).values_list('started_at', 'completed_at')[:2])
    if incoming and (len(incoming) != 1 or not incoming[0][0] or not incoming[0][1]
                     or incoming[0][1] < incoming[0][0]):
        return False
    if ((order.payment_status == Order.PaymentStatus.PAID
            or (order.payment_status == Order.PaymentStatus.PARTIALLY_REFUNDED
                and order.required_provider_count == 2 and len(participants) == 1))
            and _formal_points_transfer(order)):
        from .models import OrderParticipationSegment
        current = list(OrderParticipationSegment.objects.filter(attribution__order=order, attribution__status='ACTIVE',
            released_at__isnull=True).values_list('provider_account_id', 'started_at', 'completion_image'))
        return (len(current) == len(participants) and {account for account, _, _ in current} == set(participants)
                and all(started and image for _, started, image in current)
                and not order.providers.filter(released_at__isnull=True, team_completion_pending=True).exists())
    if order.escort_mode == 'DOUBLE' and order.requires_boss_selection:
        from .team_progress import team_progress
        progress = team_progress(order)
        return bool(progress and progress['stage'] == 'COMPLETION'
                    and progress['submitted_count'] == progress['total_count'])
    if not settings.REQUIRE_ORDER_EVIDENCE_IMAGES:
        return True
    from .models import OrderOriginalShareTransfer, OrderParticipationSegment
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
            previous_segment__attribution__financial_coverage__source='WALLET_POINTS').exists():
        segments = OrderParticipationSegment.objects.filter(attribution__order=order, attribution__status='ACTIVE',
            provider_account_id=order.provider_account_id, released_at__isnull=True, started_at__isnull=False)
        return segments.exclude(completion_image='').exclude(completion_image__isnull=True).count() == 1
    from wallet.models import ProviderReport
    return ProviderReport.objects.filter(order=order, provider_account_id=order.provider_account_id).exclude(
        completion_image='').exclude(completion_image__isnull=True).exists()


def _data(row):
    wait = getattr(row, 'confirmation', None)
    return {'id': row.pk, 'order_id': row.room.order_id, 'status': row.status,
            'submitted_by': row.submitted_by_id, 'description': row.description,
            'participants': row.participants, 'created_at': row.created_at,
            'reviewed_by': row.reviewed_by_id, 'reviewed_at': row.reviewed_at, 'review_note': row.review_note,
            'confirmation': ({'status': wait.status, 'config_minutes': wait.config_minutes,
                'config_version': wait.config_version, 'started_at': wait.started_at, 'deadline': wait.deadline,
                'paused_at': wait.paused_at, 'remaining_microseconds': wait.remaining_microseconds,
                'invalidated_at': wait.invalidated_at, 'invalidation_reason': wait.invalidation_reason,
                'confirmed_at': wait.confirmed_at, 'confirmed_by': wait.confirmed_by_id,
                'review_needed_at': wait.review_needed_at, 'review_reason': wait.review_reason} if wait else None)}


def invalidate_for_transfer_locked(order, operator, reason='TRANSFER'):
    """Invalidate a reviewed room after the participating service changes."""
    if order.room_required_snapshot is not True:
        return
    from .state_machine import IllegalTransitionError
    if reason not in ('TRANSFER', 'NEGOTIATED_TERMINATION') or not operator:
        raise IllegalTransitionError('房间核实作废原因或经办人待核对')
    room = OrderRoom.objects.select_for_update().filter(order=order).first()
    if not room:
        raise IllegalTransitionError('房间记录待核对，暂不能转单')
    pending = list(OrderRoomCompletionRequest.objects.select_for_update().filter(
        room=room, status='PENDING').order_by('pk'))
    for row in pending:
        row.status = 'REJECTED'
        row.reviewed_by, row.reviewed_at = operator, timezone.now()
        row.review_note = ('履约人员变更，原申请自动失效，请当前陪玩重新提交'
                           if reason == 'TRANSFER' else '协商终止改变履约人员，原申请自动失效')
        row.review_request_id = f'auto:{reason.lower()}:{row.pk}'
        row.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note', 'review_request_id'])
    waits = list(OrderRoomConfirmation.objects.select_for_update().filter(
        request__room=room, status__in=['RUNNING', 'PAUSED']).order_by('pk'))
    if len(waits) > 1:
        raise IllegalTransitionError('房间确认记录异常，暂不能转单')
    if waits:
        wait = waits[0]
        wait.status, wait.deadline, wait.paused_at, wait.remaining_microseconds = 'INVALIDATED', None, None, None
        wait.invalidated_at, wait.invalidated_by, wait.invalidation_reason = timezone.now(), operator, reason
        wait.save(update_fields=['status', 'deadline', 'paused_at', 'remaining_microseconds',
                                 'invalidated_at', 'invalidated_by', 'invalidation_reason'])


class RoomCompletionRequestView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    def get(self, request, order_id):
        if not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False):
            return Response({'code': 503, 'msg': '房间结单流程尚未开放'}, status=503)
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        room = OrderRoom.objects.select_for_update().filter(order=order).first() if order else None
        role = message_role(order, request.account) if room else None
        if role not in ('BOSS', 'PROVIDER', 'STAFF'):
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        from console.pagination import ConsolePagination
        paginator = ConsolePagination()
        page = paginator.paginate_queryset(room.completion_requests.select_related('room').order_by('-pk'), request)
        return paginator.get_paginated_response([_data(row) for row in page])

    @protect_submit
    @transaction.atomic
    def post(self, request, order_id):
        if not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False):
            return Response({'code': 503, 'msg': '房间结单流程尚未开放'}, status=503)
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        room = OrderRoom.objects.select_for_update().filter(order=order).first() if order else None
        if not room or message_role(order, request.account) != 'PROVIDER':
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        if (not isinstance(request.data, dict) or set(request.data) != {'description', 'client_request_id'}
                or not _valid_text(request.data['description'], 1000)
                or not _valid_text(request.data['client_request_id'], 64)):
            return Response({'code': 400, 'msg': '请填写结单说明及有效请求编号'}, status=400)
        description, key = request.data['description'], request.data['client_request_id']
        existing = room.completion_requests.filter(submitted_by=request.account, request_id=key).first()
        if existing:
            if existing.description != description:
                return Response({'code': 409, 'msg': '请求编号已用于其他内容'}, status=409)
            return Response({'code': 0, 'data': _data(existing)})
        if order.status != Order.Status.IN_SERVICE:
            return Response({'code': 409, 'msg': '订单当前不能申请房间结单'}, status=409)
        if room.objections.filter(status='OPEN').exists():
            return Response({'code': 409, 'msg': '房间异议尚未处理'}, status=409)
        from .refunds import negotiated_aftersale_blocks_completion
        if negotiated_aftersale_blocks_completion(order):
            return Response({'code': 409, 'msg': '售后处理中，暂不能结单'}, status=409)
        participants = _participants(order)
        if participants is None or not _payment_eligible(order, participants):
            return Response({'code': 409, 'msg': '订单陪玩身份待核对'}, status=409)
        if not _completion_evidence_ready(order, participants):
            return Response({'code': 409, 'msg': '请先提交所需的陪玩结单凭证'}, status=409)
        if (room.completion_requests.filter(status='PENDING').exists()
                or room.completion_requests.filter(status='VERIFIED').exclude(confirmation__status='INVALIDATED').exists()):
            return Response({'code': 409, 'msg': '已有结单申请待处理'}, status=409)
        row = OrderRoomCompletionRequest.objects.create(room=room, submitted_by=request.account,
            request_id=key, description=description, participants=participants)
        _notify_store_support('房间结单待核实', '本店有房间结单申请，请在后台核实。')
        return Response({'code': 0, 'data': _data(row), 'msg': '已提交客服核实'})


class RoomCompletionReviewQueueView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False):
            return Response({'code': 503, 'msg': '房间结单流程尚未开放'}, status=503)
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权查看结单申请'}, status=403)
        from console.pagination import ConsolePagination
        paginator = ConsolePagination()
        rows = OrderRoomCompletionRequest.objects.filter(status='PENDING').select_related('room').order_by('pk')
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response([_data(row) for row in page])


class RoomConfirmationProblemQueueView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权查看待核对房间'}, status=403)
        from console.pagination import ConsolePagination
        paginator = ConsolePagination()
        rows = OrderRoomCompletionRequest.objects.filter(confirmation__status='RUNNING',
            confirmation__review_needed_at__isnull=False).select_related('room', 'confirmation').order_by('confirmation__review_needed_at', 'pk')
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response([_data(row) for row in page])


class RoomCompletionReviewView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @protect_review
    @transaction.atomic
    def post(self, request, completion_id):
        if not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False):
            return Response({'code': 503, 'msg': '房间结单流程尚未开放'}, status=503)
        if not _authorized(request.account):
            return Response({'code': 403, 'msg': '无权核实结单申请'}, status=403)
        if not 0 < completion_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '结单申请不存在'}, status=404)
        if (not isinstance(request.data, dict) or set(request.data) != {'decision', 'note', 'client_request_id'}
                or request.data['decision'] not in ('VERIFIED', 'REJECTED')
                or not _valid_text(request.data['note'], 1000)
                or not _valid_text(request.data['client_request_id'], 64)):
            return Response({'code': 400, 'msg': '请填写核实结果、说明及有效请求编号'}, status=400)
        initial = OrderRoomCompletionRequest.objects.filter(pk=completion_id).values('room__order_id').first()
        if not initial:
            return Response({'code': 404, 'msg': '结单申请不存在'}, status=404)
        order = Order.objects.select_for_update().get(pk=initial['room__order_id'])
        room = OrderRoom.objects.select_for_update().get(order=order)
        row = OrderRoomCompletionRequest.objects.select_for_update().select_related('room').get(pk=completion_id, room=room)
        decision, note, key = request.data['decision'], request.data['note'], request.data['client_request_id']
        if row.status != 'PENDING':
            if (row.status == decision and row.reviewed_by_id == request.account.pk
                    and row.review_note == note and row.review_request_id == key):
                return Response({'code': 0, 'data': _data(row)})
            return Response({'code': 409, 'msg': '该申请已核实'}, status=409)
        if order.status != Order.Status.IN_SERVICE:
            return Response({'code': 409, 'msg': '订单状态已变化，请核对'}, status=409)
        if decision == 'VERIFIED':
            from .refunds import negotiated_aftersale_blocks_completion
            participants = _participants(order)
            if (room.objections.filter(status='OPEN').exists() or participants != row.participants
                    or not _payment_eligible(order, participants)
                    or not _completion_evidence_ready(order, participants)
                    or negotiated_aftersale_blocks_completion(order)):
                return Response({'code': 409, 'msg': '房间异议、售后、结单凭证或陪玩人员待核对'}, status=409)
            from .room_confirmation_config import required_rules
            try:
                rules = required_rules(lock=True)
            except ValueError as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
        row.status, row.review_note, row.review_request_id = decision, note, key
        row.reviewed_by, row.reviewed_at = request.account, timezone.now()
        row.save(update_fields=['status', 'review_note', 'review_request_id', 'reviewed_by', 'reviewed_at'])
        if decision == 'VERIFIED':
            OrderRoomConfirmation.objects.create(request=row, config_minutes=rules['minutes'],
                config_version=rules['version'], started_at=row.reviewed_at,
                deadline=row.reviewed_at + timedelta(minutes=rules['minutes']))
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=request.path[:255], resource='room-completion-review', object_id=str(row.pk),
            request_body={'order_id': order.pk, 'decision': decision, 'note': note, 'client_request_id': key}, status_code=200)
        from site_messages.utils import create_cycle_message
        from site_messages.order_cycle import recipient_cycle
        from site_messages.room_review_notice import enabled, record
        cycle = recipient_cycle(order, row.submitted_by_id)
        title = '房间结单已核实' if decision == 'VERIFIED' else '房间结单未通过核实'
        preview = '客服已核实，等待老板确认。' if decision == 'VERIFIED' else '客服未通过结单申请，请查看核实说明。'
        if enabled():
            record(row, account_id=row.submitted_by_id, account_type='PROVIDER', cycle=cycle,
                   decision=decision, title=title, preview=preview)
        else:
            create_cycle_message(recipient_account_id=row.submitted_by_id, msg_type='ORDER',
                recipient_business_cycle=cycle, title=title, preview=preview, detail=note, related_order_id=order.pk)
        if decision == 'VERIFIED':
            if enabled():
                record(row, account_id=order.customer_account_id, account_type='BOSS', cycle=order.business_cycle,
                       decision=decision, title='房间结单待确认', preview='客服已核实，请查看房间结单申请。')
            else:
                create_cycle_message(recipient_account_id=order.customer_account_id, msg_type='ORDER',
                    recipient_business_cycle=order.business_cycle,
                    title='房间结单待确认', preview='客服已核实，请查看房间结单申请。', related_order_id=order.pk)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': _data(row), 'msg': '核实结果已记录'})
