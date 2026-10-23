from django.db import transaction
from rest_framework.response import Response
from wallet.models import ProviderReport
from .models import Order, OrderProvider
from .state_machine import transition, IllegalTransitionError
from .services import complete_fulfillment
from .notifier import notify_order_update
from .settlement import SettlementError

def team_evidence_submitted(order, account, field):
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
            report,_=ProviderReport.objects.get_or_create(order=order,provider_account=request.account,
                defaults={'provider':request.legacy_user,'game_name':order.service_name_snapshot,'description':order.remark,
                          'amount':assignment.settlement_base,'status':'DRAFT'})
            setattr(report,field,image);report.save(update_fields=[field,'updated_at'])
            participants=list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id',flat=True))
            submitted=ProviderReport.objects.filter(order=order,provider_account_id__in=participants).exclude(**{field:''}).exclude(**{field:None}).count()
            ready=len(participants)==2 and submitted==2
            if ready:
                order=transition(order.pk,Order.Status.IN_SERVICE if field=='entry_image' else Order.Status.COMPLETED,
                    operator=request.legacy_user,operator_account=request.account,
                    side_effect=(lambda current: complete_fulfillment(current,operator=request.legacy_user,operator_account=request.account)) if field=='completion_image' else None)
            transaction.on_commit(lambda: notify_order_update(order))
        from .serializers import OrderSerializer
        return Response({'code':0,'msg':('两人截图已齐，服务状态已更新' if ready else '截图已提交，等待队友提交'), 'data':OrderSerializer(order,context={'request':request}).data})
    except Order.DoesNotExist:
        return Response({'code':404,'msg':'订单不存在'})
    except (IllegalTransitionError, SettlementError) as exc:
        return Response({'code':400,'msg':str(exc)})
