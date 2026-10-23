from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from console.permissions import HasConsolePerm
from .models import KookPointsConfig,KookBinding


class KookPointsStatusView(APIView):
    permission_classes=[HasConsolePerm]
    default_perm='engagement:view'
    def get(self,request):
        c=KookPointsConfig.objects.filter(pk=1).first()
        return Response({'code':0,'data':{'enabled':bool(c and c.enabled),
            'connected':bool(c and c.heartbeat_at and (timezone.now()-c.heartbeat_at).total_seconds()<90),
            'status':c.status if c else '未配置','channel_count':len(c.voice_channel_ids) if c else 0,
            'heartbeat_at':c.heartbeat_at if c else None,'binding_count':KookBinding.objects.count(),
            'provider_count':KookBinding.objects.filter(account__account_type='PROVIDER').count(),
            'rules':'全部语音频道计分；累计2小时1积分，零头累计；签到大厅每日5积分；按北京时间分日。'}})
