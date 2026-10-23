from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import KookBinding, PointLedger
from .views import IsPointsMember
from .kook_points import challenge, enabled_config


class KookBindingView(APIView):
    permission_classes=[IsPointsMember]
    def get(self,request):
        cfg=enabled_config();binding=KookBinding.objects.filter(account=request.account).first()
        return Response({'code':0,'data':{
            'enabled':bool(cfg),'connected':bool(cfg and cfg.heartbeat_at and (timezone.now()-cfg.heartbeat_at).total_seconds()<90),
            'bound':bool(binding),'kook_user_id':binding.user_id if binding else '',
            'nickname':binding.nickname if binding else '',
            'online_seconds':binding.online_milliseconds//1000 if binding else 0,
            'online_points':binding.awarded_points if binding else 0,
            'checked_in':PointLedger.objects.filter(account=request.account,event_key=f'KOOK_CHECKIN:{request.account.pk}:{timezone.localdate().isoformat()}').exists(),
            'channel_name':'签到大厅','channel_id':cfg.checkin_channel_id if cfg else '',
            'rules':'本服务器全部语音频道累计在线，每满2小时1积分，零头累计；签到大厅每日签到5积分。'
        }})
    def post(self,request):
        return Response({'code':0,'data':{'code':challenge(request.account),'expires_in':600,'instruction':'请用本人KOOK账号在签到大厅发送：绑定 <验证码>'}})
