from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from console.permissions import IsConsoleUser, get_account_permissions
from console.pagination import ConsolePagination
from .models import OrderTimeoutConfigHistory
from .timeout import current_timeout_config


def config_response(row):
    return {'minutes': row.minutes, 'revision': row.revision}


class OrderTimeoutConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if 'order:view' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权查看待接单时限'}, status=403)
        return Response({'code': 0, 'data': config_response(current_timeout_config())})

    def put(self, request):
        if not {'order:view', 'order:timeout'}.issubset(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权修改待接单时限'}, status=403)
        minutes, revision = request.data.get('minutes'), request.data.get('revision')
        if type(minutes) is not int or not 1 <= minutes <= 1440 or type(revision) is not int or revision < 0:
            return Response({'code': 400, 'msg': '时限须为 1 至 1440 的整数分钟，版本号须为非负整数'}, status=400)
        with transaction.atomic():
            row = current_timeout_config(lock=True)
            if row.revision != revision:
                return Response({'code': 409, 'errorCode': 'CONFIG_CHANGED',
                                 'msg': '配置已被其他管理员修改，请刷新后再保存'}, status=409)
            previous = row.minutes
            row.minutes = minutes
            row.revision += 1
            row.save(update_fields=['minutes', 'revision', 'updated_at'])
            OrderTimeoutConfigHistory.objects.create(operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150],
                previous_minutes=previous, minutes=minutes, revision=row.revision)
        return Response({'code': 0, 'data': config_response(row)})


class OrderTimeoutConfigHistoryView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if 'audit:view' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权查看时限变更记录'}, status=403)
        paginator = ConsolePagination()
        rows = paginator.paginate_queryset(OrderTimeoutConfigHistory.objects.order_by('-revision'), request, view=self)
        return paginator.get_paginated_response([{
            'id': row.pk, 'previous_minutes': row.previous_minutes, 'minutes': row.minutes,
            'revision': row.revision, 'operator_name': row.operator_name, 'created_at': row.created_at,
        } for row in rows])
