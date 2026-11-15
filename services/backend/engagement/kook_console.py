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
        from .kook_online_rules import active_rule
        rule = active_rule()
        seconds, milliseconds = divmod(rule.window_ms, 1000)
        window_text = str(seconds) if not milliseconds else f'{seconds}.{milliseconds:03d}'.rstrip('0')
        return Response({'code':0,'data':{'enabled':bool(c and c.enabled),
            'connected':bool(c and c.heartbeat_at and (timezone.now()-c.heartbeat_at).total_seconds()<90),
            'status':c.status if c else '未配置','channel_count':len(c.voice_channel_ids) if c else 0,
            'heartbeat_at':c.heartbeat_at if c else None,'binding_count':KookBinding.objects.count(),
            'provider_count':KookBinding.objects.filter(account__account_type='PROVIDER').count(),
            'rules':f'全部语音频道计分；每累计{window_text}秒{rule.points}积分，零头累计；签到大厅每日{c.daily_checkin_points if c else 5}积分；按北京时间分日。'}})


class KookPointsConfigView(APIView):
    """Store-local listener configuration; actual connectivity comes from heartbeats."""
    permission_classes = [HasConsolePerm]
    default_perm = 'engagement:view'
    required_perms = {'put': 'engagement:edit'}

    def get_permissions(self):
        self.default_perm = 'engagement:edit' if self.request.method == 'PUT' else 'engagement:view'
        return super().get_permissions()

    @staticmethod
    def data(row):
        return {'revision': row.revision if row else 0, 'enabled': row.enabled if row else False,
                'daily_checkin_points': row.daily_checkin_points if row else 5,
                'guild_id': row.guild_id if row else '',
                'checkin_channel_id': row.checkin_channel_id if row else '',
                'voice_channel_ids': row.voice_channel_ids if row else [],
                'heartbeat_at': row.heartbeat_at if row else None,
                'status': row.status if row else '未配置'}

    def get(self, request):
        from .models import KookOnlineRuleDraft
        result = self.data(KookPointsConfig.objects.filter(pk=1).first())
        draft = KookOnlineRuleDraft.objects.order_by('-config_revision').first()
        from .kook_online_rules import active_rule
        active = active_rule()
        result['online_rule_draft'] = ({'revision': draft.config_revision, 'window_ms': draft.window_ms,
            'points': draft.points, 'created_at': draft.created_at,
            'published': bool(draft and hasattr(draft, 'kookonlineruleversion')),
            'active': active.source_draft_id == draft.pk} if draft else None)
        result['online_rule_active'] = {'revision': active.revision, 'window_ms': active.window_ms,
            'points': active.points, 'effective_at': active.effective_at}
        return Response({'code': 0, 'data': result})

    def put(self, request):
        from rest_framework import serializers
        from stores.db import transaction
        from stores.license_benefits import LicenseDecisionUnavailable, check_new_benefits
        class Input(serializers.Serializer):
            revision = serializers.IntegerField(min_value=0)
            enabled = serializers.BooleanField()
            daily_checkin_points = serializers.IntegerField(min_value=0, max_value=21474836, required=False)
            online_draft_window_ms = serializers.IntegerField(min_value=1000, max_value=2147483647, required=False)
            online_draft_points = serializers.IntegerField(min_value=1, max_value=21474836, required=False)
            publish_online_draft_revision = serializers.IntegerField(min_value=1, required=False)
            guild_id = serializers.RegexField(r'^[0-9]{1,64}$', allow_blank=True)
            checkin_channel_id = serializers.RegexField(r'^[0-9]{1,64}$', allow_blank=True)
        form = Input(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        if 'publish_online_draft_revision' in data and 'online_draft_window_ms' in data:
            raise serializers.ValidationError('请先保存草稿，再单独发布')
        if ('online_draft_window_ms' in data) != ('online_draft_points' in data):
            raise serializers.ValidationError('在线规则草稿须同时填写时长及积分')
        if data['enabled'] and (not data['guild_id'] or not data['checkin_channel_id']):
            raise serializers.ValidationError('启用前请填写服务器和签到频道')
        if data['enabled']:
            outcome, _ = check_new_benefits('CHECKIN')
            if outcome == 'UNKNOWN':
                raise LicenseDecisionUnavailable()
            if outcome != 'ALLOW':
                raise serializers.ValidationError('本店授权暂不允许启用 KOOK 积分')
        with transaction.atomic():
            row, _ = KookPointsConfig.objects.select_for_update().get_or_create(pk=1,
                defaults={'guild_id': '', 'checkin_channel_id': ''})
            publish_revision = data.get('publish_online_draft_revision')
            draft = published = None
            if publish_revision is not None:
                from .models import KookOnlineRuleDraft, KookOnlineRuleVersion
                draft = KookOnlineRuleDraft.objects.filter(config_revision=publish_revision).first()
                if not draft:
                    raise serializers.ValidationError('在线规则草稿不存在')
                published = KookOnlineRuleVersion.objects.filter(source_draft=draft).first()
                if published:
                    same_config = (row.enabled == data['enabled'] and row.guild_id == data['guild_id']
                        and row.checkin_channel_id == data['checkin_channel_id']
                        and row.daily_checkin_points == data.get('daily_checkin_points', row.daily_checkin_points))
                    if not same_config:
                        return Response({'code': 409, 'msg': '草稿已发布，其他配置请刷新后修改'}, status=409)
                    result = self.data(row)
                    result['online_rule_published'] = {'revision': published.revision,
                        'window_ms': published.window_ms, 'points': published.points,
                        'effective_at': published.effective_at}
                    return Response({'code': 0, 'data': result})
            if row.revision != data['revision']:
                return Response({'code': 409, 'msg': '配置已变更，请刷新重试'}, status=409)
            if data['enabled']:
                outcome, _ = check_new_benefits('CHECKIN')
                if outcome == 'UNKNOWN':
                    raise LicenseDecisionUnavailable()
                if outcome != 'ALLOW':
                    raise serializers.ValidationError('本店授权暂不允许启用 KOOK 积分')
            listener_changed = any(getattr(row, key) != data[key] for key in ('enabled', 'guild_id', 'checkin_channel_id'))
            points = data.get('daily_checkin_points', row.daily_checkin_points)
            points_changed = points != row.daily_checkin_points
            draft_changed = 'online_draft_window_ms' in data
            if listener_changed or points_changed or draft_changed or draft is not None:
                now = timezone.now()
                for key in ('enabled', 'guild_id', 'checkin_channel_id'):
                    setattr(row, key, data[key])
                row.revision += 1
                row.daily_checkin_points = points
                fields = ['enabled', 'guild_id', 'checkin_channel_id', 'revision', 'daily_checkin_points']
                if listener_changed:
                    row.listener_revision += 1
                    row.started_at = now
                    row.heartbeat_at = None
                    row.voice_channel_ids = []
                    row.status = '等待连接' if row.enabled else '已停用'
                    fields += ['listener_revision', 'started_at', 'heartbeat_at', 'voice_channel_ids', 'status']
                if points_changed:
                    from .models import KookCheckinRuleVersion
                    KookCheckinRuleVersion.objects.create(revision=row.revision, points=points,
                                                         effective_at=now, operator=request.account)
                row.save(update_fields=fields)
                if draft is not None:
                    from .models import KookOnlineRuleVersion
                    latest = KookOnlineRuleVersion.objects.order_by('-revision').first()
                    if latest and latest.effective_at and now <= latest.effective_at:
                        raise serializers.ValidationError('规则生效时刻冲突，请重试')
                    published = KookOnlineRuleVersion.objects.create(
                        revision=(latest.revision + 1 if latest else 1),
                        window_ms=draft.window_ms, points=draft.points, effective_at=now,
                        operator=request.account, source_draft=draft)
                if draft_changed:
                    from .models import KookOnlineRuleDraft
                    KookOnlineRuleDraft.objects.create(config_revision=row.revision,
                        window_ms=data['online_draft_window_ms'], points=data['online_draft_points'],
                        operator=request.account)
        result = self.data(row)
        if published:
            result['online_rule_published'] = {'revision': published.revision,
                'window_ms': published.window_ms, 'points': published.points,
                'effective_at': published.effective_at}
        if draft_changed:
            result['online_rule_draft'] = {'revision': row.revision, 'window_ms': data['online_draft_window_ms'],
                'points': data['online_draft_points'], 'active': False}
        return Response({'code': 0, 'data': result})
