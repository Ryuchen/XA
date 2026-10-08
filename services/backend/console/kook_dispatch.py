"""管理端 KOOK 配置、文案预览及显式测试投递。"""
from urllib.parse import urlsplit
from uuid import uuid4

from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from integrations.kook import KookClient, KookError, KookDeliveryUnknown
from integrations.kook_config import DEFAULT_TEMPLATE, TEMPLATE_FIELDS, decrypt_token, encrypt_token, get_kook_config
from orders.dispatch_templates import sample_values, template_error
from orders.kook_dispatch import build_card
from orders.models import GameCategory, Gameplay
from .models import KookDispatchConfig, KookDispatchProbe
from .permissions import IsConsoleUser, get_account_permissions


class ConfigConflict(APIException):
    status_code = 409
    default_detail = '配置已被其他管理员修改，请刷新后再保存'


class RouteInput(serializers.Serializer):
    id = serializers.RegexField(r'^[A-Za-z0-9_-]{1,64}$', required=False)
    name = serializers.CharField(max_length=60)
    enabled = serializers.BooleanField(default=True)
    channel_id = serializers.RegexField(r'^[0-9]{1,64}$')
    guild_id = serializers.RegexField(r'^[0-9]{1,64}$', allow_blank=True, default='')
    game_category_id = serializers.IntegerField(min_value=1, allow_null=True, default=None)
    gameplay_id = serializers.IntegerField(min_value=1, allow_null=True, default=None)
    platform = serializers.ChoiceField(choices=['', 'PC', 'MOBILE'], default='')
    template = serializers.CharField(max_length=1200, default=DEFAULT_TEMPLATE)
    mention_role_ids = serializers.ListField(child=serializers.RegexField(r'^[0-9]{1,64}$'), max_length=10, default=list)

    def validate_template(self, value):
        error = template_error(value)
        if error:
            raise serializers.ValidationError(error)
        return value

    def validate(self, attrs):
        for field, model in [('game_category_id', GameCategory), ('gameplay_id', Gameplay)]:
            if attrs[field] and not model.objects.filter(pk=attrs[field]).exists():
                raise serializers.ValidationError({field: '选择的游戏或玩法不存在'})
        attrs.setdefault('id', uuid4().hex)
        attrs['mention_role_ids'] = list(dict.fromkeys(attrs['mention_role_ids']))
        return attrs


class ConfigInput(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    enabled = serializers.BooleanField()
    auto_new_orders = serializers.BooleanField()
    auto_rejected_orders = serializers.BooleanField()
    bot_token = serializers.CharField(max_length=512, allow_blank=True, required=False, write_only=True)
    clear_token = serializers.BooleanField(default=False, write_only=True)
    provider_order_url = serializers.URLField(max_length=500, allow_blank=True)
    routes = RouteInput(many=True, max_length=50)

    def validate_bot_token(self, value):
        if any(c.isspace() for c in value) or value.lower().startswith('bot '):
            raise serializers.ValidationError('只填写 Bot Token 本身，不包含 Bot 前缀或空白字符')
        return value

    def validate_provider_order_url(self, value):
        parts = urlsplit(value)
        if value and (parts.scheme != 'https' or parts.username or parts.password):
            raise serializers.ValidationError('陪玩端链接必须为 HTTPS 地址，不能包含账号密码')
        return value

    def validate(self, attrs):
        ids = [r['id'] for r in attrs['routes']]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('频道规则标识不能重复')
        if attrs['clear_token'] and attrs.get('bot_token'):
            raise serializers.ValidationError('清除 Token 与更换 Token 不能同时选择')
        if attrs['enabled'] and not any(r['enabled'] for r in attrs['routes']):
            raise serializers.ValidationError('启用 KOOK 前至少需要一条启用的频道规则')
        return attrs


def config_data():
    config = get_kook_config()
    row = KookDispatchConfig.objects.filter(pk=1).first()
    return {'enabled': config.enabled, 'auto_new_orders': config.auto_new_orders,
        'auto_rejected_orders': config.auto_rejected_orders, 'routes': config.routes,
        'provider_order_url': config.provider_order_url, 'token_saved': bool(config.token),
        'token_error': bool(row and row.bot_token_ciphertext and not config.token),
        'ready': config.ready, 'source': config.source, 'revision': row.revision if row else 0,
        'updated_at': row.updated_at if row else None, 'default_template': DEFAULT_TEMPLATE,
        'template_fields': TEMPLATE_FIELDS,
        'games': list(GameCategory.objects.order_by('sort_order', 'id').values('id', 'name')),
        'gameplays': list(Gameplay.objects.order_by('sort_order', 'id').values('id', 'name')),
        'tests': [probe_data(p) for p in KookDispatchProbe.objects.order_by('-created_at')[:10]]}


def save_config(data):
    with transaction.atomic():
        previous = get_kook_config()
        row, created = KookDispatchConfig.objects.select_for_update().get_or_create(pk=1)
        if data['revision'] != row.revision:
            raise ConfigConflict()
        ciphertext = row.bot_token_ciphertext
        if data['clear_token']:
            ciphertext = ''
        elif data.get('bot_token'):
            ciphertext = encrypt_token(data['bot_token'])
        elif created and previous.source == 'environment':
            ciphertext = encrypt_token(previous.token)
        if data['enabled'] and not decrypt_token(ciphertext):
            raise serializers.ValidationError('启用 KOOK 前请保存有效的 Bot Token')
        row.bot_token_ciphertext = ciphertext
        for field in ['enabled', 'auto_new_orders', 'auto_rejected_orders', 'routes', 'provider_order_url']:
            setattr(row, field, data[field])
        row.revision += 1
        row.save()
    return row


def require_edit(request):
    if 'dispatch_config:edit' not in get_account_permissions(request.account):
        raise PermissionDenied('无 KOOK 配置权限')


class KookConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not get_account_permissions(request.account).intersection({'dispatch_config:view', 'dispatch_config:edit'}):
            raise PermissionDenied('无 KOOK 配置查看权限')
        return Response({'code': 0, 'data': config_data()})

    def put(self, request):
        require_edit(request)
        serializer = ConfigInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        save_config(serializer.validated_data)
        return Response({'code': 0, 'data': config_data(), 'msg': 'KOOK 配置已保存，后续派单立即使用新规则'})


class RouteSelection(serializers.Serializer):
    route_id = serializers.CharField(max_length=64)


def selected_route(request):
    serializer = RouteSelection(data=request.data)
    serializer.is_valid(raise_exception=True)
    config = get_kook_config()
    route = next((r for r in config.routes if r['id'] == serializer.validated_data['route_id']), None)
    if not route or not config.token:
        raise serializers.ValidationError('请先保存 Bot Token 和频道规则')
    return config, route


class KookChannelCheckView(APIView):
    permission_classes = [IsConsoleUser]

    def post(self, request):
        require_edit(request)
        config, route = selected_route(request)
        try:
            result = KookClient(token=config.token).get_channel(route['channel_id'], guild_id=route.get('guild_id', ''))
        except KookError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        return Response({'code': 0, 'data': result, 'msg': '已确认文字频道 ID 与类型；实际发送权限请通过测试卡片验证'})


class KookTemplatePreviewView(APIView):
    permission_classes = [IsConsoleUser]

    def post(self, request):
        require_edit(request)
        serializer = serializers.CharField(max_length=1200)
        template = serializer.run_validation(request.data.get('template'))
        error = template_error(template)
        if error:
            raise serializers.ValidationError(error)
        return Response({'code': 0, 'data': {'content': template.format(**sample_values())}})


def probe_data(probe):
    return {'id': str(probe.pk), 'route_id': probe.route_key, 'channel_id': probe.channel_id,
        'content': probe.content, 'status': probe.status, 'message_id': probe.message_id,
        'last_error': probe.last_error, 'created_at': probe.created_at}


class KookProbeView(APIView):
    permission_classes = [IsConsoleUser]

    def post(self, request):
        require_edit(request)
        config, route = selected_route(request)
        request_id = serializers.UUIDField().run_validation(request.data.get('request_id'))
        if request.data.get('confirm') is not True:
            raise serializers.ValidationError('请确认向指定频道发送标明测试的卡片')
        text = '【联调测试，不是真实订单】\n' + (route.get('template') or DEFAULT_TEMPLATE).format(**sample_values())
        probe, created = KookDispatchProbe.objects.get_or_create(pk=request_id, defaults={
            'route_key': route['id'], 'channel_id': route['channel_id'], 'content': text})
        if probe.route_key != route['id']:
            raise serializers.ValidationError('同一测试请求不能改投其他频道')
        if created:
            try:
                card = build_card(text, provider_order_url=config.provider_order_url)  # 测试不提醒身份组。
                result = KookClient(token=config.token).send_channel_message(
                    channel_id=probe.channel_id, content=card, nonce='xa-test-' + str(request_id))
                probe.status, probe.message_id = 'SENT', result.message_id
            except KookDeliveryUnknown:
                probe.status, probe.last_error = 'UNKNOWN', '测试发送结果未确认，请先核对频道内消息，不要重复发送'
            except KookError as exc:
                probe.status, probe.last_error = 'FAILED', str(exc)[:500]
            except Exception:
                probe.status, probe.last_error = 'UNKNOWN', '测试发送结果未确认，请先核对频道内消息'
            probe.save(update_fields=['status', 'message_id', 'last_error'])
        return Response({'code': 0, 'data': probe_data(probe)})
