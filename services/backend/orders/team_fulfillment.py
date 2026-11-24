from stores.db import transaction
from rest_framework.response import Response
from wallet.models import ProviderReport
from .models import Order, OrderProvider
from .state_machine import transition, IllegalTransitionError
from .services import complete_fulfillment, push_message_safe
from .notifier import notify_order_update
from .settlement import SettlementError

def require_staff_team_evidence(order, evidence_name):
    """Use the active team's evidence while the state transition holds the order lock."""
    if order.escort_mode != 'DOUBLE' or not order.requires_boss_selection:
        return
    from .team_progress import team_progress
    progress = team_progress(order)
    if not progress or progress['submitted_count'] < progress['total_count']:
        raise IllegalTransitionError(f'请等本单参与陪玩分别提交{evidence_name}')

def team_report_evidence_error(report, kind):
    """First-time team evidence belongs to the order's fulfilment action."""
    if not report.order_id or report.order.escort_mode != 'DOUBLE' or not report.order.requires_boss_selection:
        return ''
    if report.order.status == 'GRABBED' and kind == 'COMPLETION':
        return '请先完成入队，服务结束后在订单页提交结单截图'
    if report.order.status == 'GRABBED' and kind == 'ENTRY' and not report.entry_image:
        return '请在订单页提交入队截图，完成入队后可在报单中补充'
    if report.order.status == 'IN_SERVICE' and kind == 'COMPLETION':
        # A transferred-out member cannot operate the team's completion action.
        # Their own evidence is still required for the retained report share and
        # does not participate in the active members' state transition.
        assignment = next((row for row in report.order.providers.all()
                           if (row.provider_account_id == report.provider_account_id
                               if report.provider_account_id else row.provider_id == report.provider_id)), None)
        if assignment and assignment.released_at:
            return ''
        if assignment and assignment.team_completion_pending:
            return '订单人员已变更，请在订单页重新提交本次结单截图'
        if not report.completion_image:
            return '请在订单页提交结单截图，完成服务后可在报单中补充'
    return ''

def team_evidence_submitted(order, account, field):
    from .team_progress import team_progress
    progress = team_progress(order, for_display=True)
    stage = 'ENTRY' if field == 'entry_image' else 'COMPLETION'
    if progress and progress['stage'] == stage:
        # 兼容此前从报单草稿上传导致两人截图齐全但状态未流转的订单；
        # 重新开放履约入口，让任一成员提交后通过正常状态机恢复。
        if progress['submitted_count'] == progress['total_count']:
            return False
        member = next((row for row in progress['members'] if row['id'] == account.pk), None)
        if member is not None:
            return member['submitted']
    return bool(ProviderReport.objects.filter(order=order,provider_account=account).exclude(**{field:''}).exclude(**{field:None}).exists())

def submit_team_evidence(request, order_id, field, image):
    if not image:
        return Response({'code':400,'msg':'双人服务请分别上传自己的入队或结单截图'})
    try:
        from contextlib import ExitStack
        with transaction.atomic(), ExitStack() as scopes:
            order=Order.objects.select_for_update().get(pk=order_id)
            assignment=OrderProvider.objects.filter(order=order,provider_account=request.account,released_at__isnull=True).first()
            if not assignment or request.account.account_type != 'PROVIDER':
                return Response({'code':403,'msg':'仅本单参与陪玩可提交截图'})
            from wallet.report_lifecycle import report_order_cycle_matches
            if not report_order_cycle_matches(order, request.account):
                return Response({'code':409,'msg':'订单陪玩所属周期待核对，请联系客服'}, status=409)
            from .points_service_payment import needs_integrated_processing
            from .segment_reports import enter_points_report_scope
            snapshot = None
            from .models import OrderOriginalShareTransfer
            order = enter_points_report_scope(scopes, order, include_untransferred=True)
            from .points_transfer_capture import enabled as points_transfer_enabled
            points_order = needs_integrated_processing(order)
            points_transferred = (field == 'completion_image' and points_transfer_enabled() and OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order,
                previous_segment__attribution__financial_coverage__source='WALLET_POINTS').exists())
            if points_transferred:
                from .points_funding_plan import PointsFundingPlanError
                from .points_transfer_funding import plan_points_transfer
                from .points_funding_evidence import verify_order_funding
                from club_accounts.models import LegacyAccountMap
                from rest_framework.exceptions import ValidationError
                try:
                    if order.payment_status == Order.PaymentStatus.PAID:
                        plan_points_transfer(order.pk, preview_unstarted=True)
                        # Completion may precede full funding, but any recorded
                        # allocation/release must already be independently valid.
                        verify_order_funding(order.pk, allow_unstarted=True)
                    else:
                        if order.refund_records.count() == 2:
                            from .models import PointsNegotiationPosting
                            from .points_transfer_followup_termination import verify_closed_or_settled_locked
                            from .points_transfer_refund_access import verification_scope
                            posting = PointsNegotiationPosting.objects.filter(
                                points_refund__redemption__order=order).order_by('points_refund__refund_id').last()
                            if not posting:
                                raise ValidationError('积分转单追加退款凭证缺失')
                            with verification_scope(order):
                                verify_closed_or_settled_locked(posting)
                        else:
                            from .points_transfer_refund_continuation import verify_locked
                            verify_locked(order)
                except (PointsFundingPlanError, ValidationError):
                    return Response({'code':400,'msg':'积分转单履约及资金凭证待核对'})
                if not request.legacy_user or not LegacyAccountMap.objects.filter(
                        account_id=request.account.pk, legacy_user_id=request.legacy_user.pk).exists():
                    return Response({'code':403,'msg':'陪玩账号归属待核对'}, status=403)
            elif points_order:
                from .points_funding_plan import PointsFundingPlanError
                from .points_report_test import verified_report_snapshot
                try:
                    snapshot = verified_report_snapshot(order, request.account.pk, getattr(request.legacy_user, 'pk', None))
                except PointsFundingPlanError:
                    return Response({'code':400,'msg':'积分订单报单或收益快照待核对'})
            expected = Order.Status.GRABBED if field == 'entry_image' else Order.Status.IN_SERVICE
            continuation = None
            from .negotiation_zero import MARKER as ZERO_NEGOTIATION_MARKER
            zero_continuation = (isinstance(order.funding_terms, dict) and ZERO_NEGOTIATION_MARKER in order.funding_terms)
            if field == 'completion_image' and order.status == expected and (order.payment_status != 'PAID' or zero_continuation):
                from .negotiation_termination import continuing_team
                from rest_framework.exceptions import ValidationError
                try:
                    from .ordinary_transfer_continuation import applies
                    if applies(order):
                        from .ordinary_transfer_continuation import verify_locked
                        verify_locked(order)
                        continuation = 'ordinary_transfer'
                    elif points_transferred:
                        from .models import OrderParticipationSegment
                        continuation = OrderParticipationSegment.objects.filter(
                            attribution__order=order, provider_account=request.account,
                            started_at__isnull=False, released_at__isnull=True).exists()
                    else:
                        continuation = continuing_team(order, verify=True)
                except (ValidationError, ValueError) as exc:
                    raise IllegalTransitionError('协商退款凭证待核对，暂不能完成订单') from exc
            if order.status != expected or (order.payment_status != 'PAID' and not continuation):
                return Response({'code':400,'msg':'当前订单状态不能提交该截图，请刷新订单'})
            if field == 'completion_image':
                if (order.room_required_snapshot is True and order.room.completion_requests.filter(status='VERIFIED',
                        confirmation__status__in=['RUNNING', 'PAUSED']).exists()):
                    return Response({'code':409,'msg':'结单申请已核实，凭证不能再修改'}, status=409)
                from .participation_entry import pending_transfer_segments
                if pending_transfer_segments(order, account_id=request.account.pk).exists():
                    return Response({'code':400,'msg':'请先确认本次转入并提交入队凭证'})
                from .refunds import negotiated_aftersale_blocks_completion
                if negotiated_aftersale_blocks_completion(order):
                    return Response({'code':400,'msg':'售后处理中，暂不能结单，请等待客服处理'})
            if points_transferred:
                from .original_attribution import completion_evidence
                completion_evidence(order, request.account.pk, image)
            else:
                existing_account_report = ProviderReport.objects.filter(order=order, provider_account=request.account).first()
                if existing_account_report and existing_account_report.provider_id != getattr(request.legacy_user, 'pk', None):
                    return Response({'code':409,'msg':'报单归属需客服核对'}, status=409)
                if (field == 'completion_image' and order.room_required_snapshot is True and existing_account_report
                        and existing_account_report.status not in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED)):
                    return Response({'code':409,'msg':'当前报单不可修改结单凭证'}, status=409)
                report,_=ProviderReport.objects.get_or_create(order=order,provider=request.legacy_user,
                    defaults={'provider_account':request.account,'game_name':order.service_name_snapshot,'description':order.remark,
                              'amount':snapshot.incoming_basis if snapshot else assignment.settlement_base,
                              'commission_rate':snapshot.commission_rate if snapshot else assignment.commission_rate,'status':'DRAFT'})
                if snapshot and (report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate):
                    return Response({'code':409,'msg':'积分订单报单金额或抽成快照待核对'}, status=409)
                if report.provider_account_id and report.provider_account_id != request.account.pk:
                    return Response({'code':409,'msg':'报单归属需客服核对'}, status=409)
                fields = [field, 'updated_at']
                if not report.provider_account_id:
                    report.provider_account = request.account
                    fields.append('provider_account')
                setattr(report,field,image);report.save(update_fields=fields)
            if field == 'completion_image' and not points_transferred:
                from .original_attribution import completion_evidence
                completion_evidence(order, request.account.pk, report.completion_image.name)
            if field == 'completion_image':
                from .work_stints import personal_completion
                completed_at = personal_completion(order, request.account.pk)
                from .provider_ranking import capture_team_completion
                capture_team_completion(order, request.account,
                                        image.name if points_transferred else report.completion_image.name,
                                        at=completed_at)
            if field == 'entry_image':
                from django.utils import timezone
                from .services import lock_provider_dispatch
                lock_provider_dispatch([request.account.pk])
                from .original_attribution import started
                entered_at = timezone.now()
                started(order, [request.account.pk], at=entered_at, source='ENTRY_IMAGE')
                from .work_stints import initial_entry
                try:
                    initial_entry(order, request.account.pk, at=entered_at, image=report.entry_image.name if not points_transferred else None)
                except ValueError as exc:
                    raise IllegalTransitionError(str(exc)) from exc
                from .models import Reservation
                if Reservation.objects.filter(order=order, status=Reservation.Status.CONVERTED).exists():
                    from .services import mark_escort_busy
                    mark_escort_busy(provider_ids=[assignment.provider_id], account_ids=[request.account.pk])
            if field == 'completion_image' and assignment.team_completion_pending:
                assignment.team_completion_pending = False
                assignment.save(update_fields=['team_completion_pending'])
            from .team_progress import team_progress
            from .points_transfer_refund_access import permits, verification_scope
            if continuation and permits(order, phase='report'):
                # Resolve the remaining seat from the verified refund while
                # the existing report scope holds the order and receipt locks.
                with verification_scope(order):
                    progress = team_progress(order)
            else:
                progress = team_progress(order)
            required_count = (order.providers.filter(released_at__isnull=True).count()
                              if continuation == 'ordinary_transfer' else 1 if continuation else 2)
            ready = bool(progress and progress['total_count'] == required_count and progress['submitted_count'] == required_count)
            room_completion = field == 'completion_image' and order.room_required_snapshot is True
            if ready and not room_completion:
                order=transition(order.pk,Order.Status.IN_SERVICE if field=='entry_image' else Order.Status.COMPLETED,
                    operator=request.legacy_user,operator_account=request.account,
                    side_effect=(lambda current: complete_fulfillment(current,operator=request.legacy_user,operator_account=request.account)) if field=='completion_image' else None,
                    customer_completion_notice=field=='completion_image')
            transaction.on_commit(lambda: notify_order_update(order))
        from .serializers import OrderSerializer
        message = ('结单凭证已齐，请继续提交房间结单申请' if ready and room_completion
                   else '两人截图已齐，服务状态已更新' if ready else '截图已提交，等待队友提交')
        return Response({'code':0,'msg':message, 'data':OrderSerializer(order,context={'request':request}).data})
    except Order.DoesNotExist:
        return Response({'code':404,'msg':'订单不存在'})
    except (IllegalTransitionError, SettlementError) as exc:
        return Response({'code':400,'msg':str(exc)})
