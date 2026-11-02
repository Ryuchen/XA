from django.db import transaction
from rest_framework.response import Response
from wallet.models import ProviderReport
from .models import Order, OrderProvider
from .state_machine import transition, IllegalTransitionError
from .services import complete_fulfillment, push_message_safe
from .notifier import notify_order_update
from .settlement import SettlementError

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
    progress = team_progress(order)
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
        with transaction.atomic():
            order=Order.objects.select_for_update().get(pk=order_id)
            assignment=OrderProvider.objects.filter(order=order,provider_account=request.account,released_at__isnull=True).first()
            if not assignment or request.account.account_type != 'PROVIDER':
                return Response({'code':403,'msg':'仅本单参与陪玩可提交截图'})
            expected = Order.Status.GRABBED if field == 'entry_image' else Order.Status.IN_SERVICE
            if order.status != expected or order.payment_status != 'PAID':
                return Response({'code':400,'msg':'当前订单状态不能提交该截图，请刷新订单'})
            existing_account_report = ProviderReport.objects.filter(order=order, provider_account=request.account).first()
            if existing_account_report and existing_account_report.provider_id != getattr(request.legacy_user, 'pk', None):
                return Response({'code':409,'msg':'报单归属需客服核对'}, status=409)
            report,_=ProviderReport.objects.get_or_create(order=order,provider=request.legacy_user,
                defaults={'provider_account':request.account,'game_name':order.service_name_snapshot,'description':order.remark,
                          'amount':assignment.settlement_base,'status':'DRAFT'})
            if report.provider_account_id and report.provider_account_id != request.account.pk:
                return Response({'code':409,'msg':'报单归属需客服核对'}, status=409)
            fields = [field, 'updated_at']
            if not report.provider_account_id:
                report.provider_account = request.account
                fields.append('provider_account')
            setattr(report,field,image);report.save(update_fields=fields)
            if field == 'completion_image' and assignment.team_completion_pending:
                assignment.team_completion_pending = False
                assignment.save(update_fields=['team_completion_pending'])
            from .team_progress import team_progress
            progress = team_progress(order)
            ready = bool(progress and progress['total_count'] == 2 and progress['submitted_count'] == 2)
            if ready:
                order=transition(order.pk,Order.Status.IN_SERVICE if field=='entry_image' else Order.Status.COMPLETED,
                    operator=request.legacy_user,operator_account=request.account,
                    side_effect=(lambda current: complete_fulfillment(current,operator=request.legacy_user,operator_account=request.account)) if field=='completion_image' else None)
                if field == 'completion_image':
                    transaction.on_commit(lambda current=order: push_message_safe(
                        recipient_id=current.customer_id,
                        recipient_account_id=current.customer_account_id,
                        title='订单已完成',
                        preview=f'你的「{current.service_name_snapshot}」已完成，可在订单中评价',
                        msg_type='ORDER', related_order_id=current.pk))
            transaction.on_commit(lambda: notify_order_update(order))
        from .serializers import OrderSerializer
        return Response({'code':0,'msg':('两人截图已齐，服务状态已更新' if ready else '截图已提交，等待队友提交'), 'data':OrderSerializer(order,context={'request':request}).data})
    except Order.DoesNotExist:
        return Response({'code':404,'msg':'订单不存在'})
    except (IllegalTransitionError, SettlementError) as exc:
        return Response({'code':400,'msg':str(exc)})
