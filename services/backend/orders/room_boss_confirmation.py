"""Owner confirmation of a reviewed room; settlement still follows report rules."""
from contextlib import ExitStack

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from .models import Order, OrderRoom, OrderRoomCompletionRequest, OrderRoomConfirmation
from .room_completion_review import _completion_evidence_ready, _data, _participants, _payment_eligible
from .room_objections import _valid_text
from .state_machine import IllegalTransitionError, transition
from .settlement import SettlementError
from .room_completion_lifecycle import boss_confirmation


def preflight_confirmation(order, room, row, wait):
    """Validate room facts while the caller holds order, room and wait locks."""
    participants = _participants(order)
    if (wait.status != 'RUNNING' or row.status != 'VERIFIED' or order.status != Order.Status.IN_SERVICE
            or not _payment_eligible(order, participants) or order.room_required_snapshot is not True
            or order.settled_at is not None or participants != row.participants
            or room.objections.filter(status='OPEN').exists() or not _completion_evidence_ready(order, participants)):
        return None, '房间状态、参与人或结单凭证已变化，请联系客服核对'
    from .refunds import negotiated_aftersale_blocks_completion
    if negotiated_aftersale_blocks_completion(order):
        return None, '售后处理中，暂不能结单'
    if (order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL or order.service_clock_version is None
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None):
        return None, '订单结算或售后时限快照待核对'
    return participants, None


def complete_confirmation_locked(order, row, wait, participants, *, actor, legacy_actor, key, at, automatic=False):
    """Persist one confirmation and finish fulfilment in the caller's transaction."""
    from .services import complete_fulfillment
    from .segment_reports import enter_points_report_scope
    from .points_funding_plan import PointsFundingPlanError
    with ExitStack() as scopes:
        if order.payment_status == Order.PaymentStatus.PAID:
            try:
                order = enter_points_report_scope(scopes, order, include_untransferred=True)
                from .points_transfer_refund_access import permits
                if permits(order, phase='report'):
                    from .points_funding_evidence import verify_order_funding
                    verify_order_funding(order.pk)
            except (ValidationError, PointsFundingPlanError) as exc:
                raise SettlementError('积分订单履约和资金凭证待核对') from exc
        wait.status, wait.confirmed_at, wait.confirmed_by = ('AUTO' if automatic else 'CONFIRMED'), at, actor
        wait.confirmation_request_id = key
        wait.save(update_fields=['status', 'confirmed_at', 'confirmed_by', 'confirmation_request_id'])
        order = transition(order.pk, Order.Status.COMPLETED, operator=legacy_actor, operator_account=actor,
            room_confirmation_id=wait.pk, reason='房间超时自动确认' if automatic else '老板确认房间结单',
            side_effect=lambda current: complete_fulfillment(current, operator=legacy_actor, operator_account=actor))
    from site_messages.utils import create_cycle_message
    from site_messages.order_cycle import recipient_cycle
    text = '房间已超时自动确认，报单和收益请按流程查看。' if automatic else '老板已确认订单，报单和收益请按流程查看。'
    from site_messages.room_confirmation_notice import enabled, record
    recovery = enabled()
    for account_id in participants:
        cycle = recipient_cycle(order, account_id)
        if recovery:
            record(wait, account_id=account_id, account_type='PROVIDER', cycle=cycle,
                   title='房间订单已完成', preview=text)
        else:
            create_cycle_message(recipient_account_id=account_id, recipient_business_cycle=cycle,
                msg_type='ORDER', title='房间订单已完成', preview=text, related_order_id=order.pk)
    boss_title = '房间订单已自动完成' if automatic else '房间订单已完成'
    boss_preview = ('房间确认时限已到，订单已自动完成。' if automatic
                    else '你已确认房间结单，订单已完成。')
    if recovery:
        record(wait, account_id=order.customer_account_id, account_type='BOSS', cycle=order.business_cycle,
               title=boss_title, preview=boss_preview)
    else:
        create_cycle_message(recipient_account_id=order.customer_account_id, recipient_business_cycle=order.business_cycle,
            msg_type='ORDER', title=boss_title, preview=boss_preview, related_order_id=order.pk)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    return order


class RoomBossConfirmView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @boss_confirmation
    @transaction.atomic
    def post(self, request, order_id, completion_id):
        if not (getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False)
                and getattr(settings, 'ROOM_BOSS_CONFIRMATION_ENABLED', False)):
            return Response({'code': 503, 'msg': '房间结单流程尚未开放'}, status=503)
        if (request.account.account_type != ClubAccount.AccountType.BOSS
                or not 0 < order_id <= 9223372036854775807 or not 0 < completion_id <= 9223372036854775807):
            return Response({'code': 404, 'msg': '结单申请不存在'}, status=404)
        order = Order.objects.select_for_update().filter(pk=order_id, customer_account=request.account).first()
        room = OrderRoom.objects.select_for_update().filter(order=order).first() if order else None
        row = (OrderRoomCompletionRequest.objects.select_for_update().select_related('room').filter(
            pk=completion_id, room=room).first() if room else None)
        wait = OrderRoomConfirmation.objects.select_for_update().filter(request=row).first() if row else None
        if not wait:
            return Response({'code': 404, 'msg': '结单申请不存在'}, status=404)
        if (not isinstance(request.data, dict) or set(request.data) != {'client_request_id'}
                or not _valid_text(request.data['client_request_id'], 64)):
            return Response({'code': 400, 'msg': '请填写有效请求编号'}, status=400)
        key = request.data['client_request_id']
        if wait.status == 'CONFIRMED':
            if (wait.confirmed_by_id == request.account.pk and wait.confirmation_request_id == key
                    and order.status == Order.Status.COMPLETED):
                return Response({'code': 0, 'data': _data(row), 'msg': '已确认结单'})
            return Response({'code': 409, 'msg': '该结单申请已处理，请查看订单'}, status=409)
        participants, error = preflight_confirmation(order, room, row, wait)
        if error:
            return Response({'code': 409, 'msg': error}, status=409)
        now = timezone.now()
        try:
            # A failed completion must roll back the confirmation too.  The
            # nested savepoint matters because expected errors are returned.
            with transaction.atomic():
                order = complete_confirmation_locked(order, row, wait, participants, actor=request.account,
                    legacy_actor=request.legacy_user, key=key, at=now)
        except (IllegalTransitionError, SettlementError) as exc:
            return Response({'code': 409, 'msg': str(exc) or '订单结单待核对'}, status=409)
        return Response({'code': 0, 'data': _data(row), 'msg': '已确认结单，收益仍按报单及售后规则处理'})
