from django.utils import timezone
from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import KookBinding, PointLedger
from .views import IsPointsMember
from .kook_points import challenge, enabled_config


class KookBindingView(APIView):
    permission_classes=[IsPointsMember]
    def get(self,request):
        cfg=enabled_config();binding=KookBinding.objects.filter(account=request.account).first()
        from .kook_online_rules import active_rule
        rule = active_rule()
        guarded = (request.account.account_type == 'BOSS' or
                   (request.account.account_type == 'PROVIDER' and
                    getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)))
        current = bool(binding and (not guarded or (request.account.license_membership_active
                       and binding.business_cycle == request.account.business_cycle)))
        totals_valid = bool(current and binding.cycle_online_base <= binding.online_milliseconds
                            and binding.cycle_awarded_base <= binding.awarded_points)
        return Response({'code':0,'data':{
            'enabled':bool(cfg),'connected':bool(cfg and cfg.heartbeat_at and (timezone.now()-cfg.heartbeat_at).total_seconds()<90),
            'bound':current,'rebind_required':bool(binding and guarded and not current),
            'kook_user_id':binding.user_id if current else '',
            'nickname':binding.nickname if current else '',
            'online_seconds':((binding.online_milliseconds-binding.cycle_online_base)//1000
                              if totals_valid else (None if current else 0)),
            'online_points':((binding.awarded_points-binding.cycle_awarded_base)
                             if totals_valid else (None if current else 0)),
            'totals_status':'REVIEW' if current and not totals_valid else 'CURRENT' if current else 'UNBOUND',
            'checked_in':PointLedger.objects.filter(account=request.account,event_key=f'KOOK_CHECKIN:{request.account.pk}:{timezone.localdate().isoformat()}').exists(),
            'channel_name':'签到大厅','channel_id':cfg.checkin_channel_id if cfg else '',
            'rules':f'本服务器全部语音频道累计在线，每满{rule.window_ms // 1000}秒{rule.points}积分，零头累计；签到大厅每日签到{cfg.daily_checkin_points if cfg else 5}积分。'
        }})
    def post(self,request):
        return Response({'code':0,'data':{'code':challenge(request.account),'expires_in':600,'instruction':'请用本人KOOK账号在签到大厅发送：绑定 <验证码>'}})
