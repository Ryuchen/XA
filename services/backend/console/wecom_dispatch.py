"""Store-local WeCom group robot configuration; credentials are never returned."""
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction
from integrations.kook_config import decrypt_token, encrypt_token
from integrations.wecom import valid_webhook
from .kook_dispatch import ConfigConflict
from .models import WecomDispatchConfig
from .permissions import IsConsoleUser, get_account_permissions


class ConfigInput(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    enabled = serializers.BooleanField()
    auto_new_orders = serializers.BooleanField()
    auto_rejected_orders = serializers.BooleanField()
    webhook = serializers.CharField(max_length=1024, required=False, allow_blank=True, write_only=True, trim_whitespace=False)
    clear_webhook = serializers.BooleanField(default=False, write_only=True)

    def validate(self, attrs):
        webhook = attrs.get('webhook', '')
        if webhook and not valid_webhook(webhook):
            raise serializers.ValidationError({'webhook': '请填写有效的企业微信群机器人地址'})
        if webhook and attrs['clear_webhook']:
            raise serializers.ValidationError('不能同时更换和清除机器人地址')
        return attrs


def config_data(row):
    if row is None:
        return {'enabled': False, 'auto_new_orders': False, 'auto_rejected_orders': False,
                'webhook_saved': False, 'webhook_error': False, 'revision': 0, 'updated_at': None}
    webhook = decrypt_token(row.webhook_ciphertext)
    return {'enabled': row.enabled, 'auto_new_orders': row.auto_new_orders,
            'auto_rejected_orders': row.auto_rejected_orders, 'webhook_saved': bool(webhook),
            'webhook_error': bool(row.webhook_ciphertext and not webhook),
            'revision': row.revision, 'updated_at': row.updated_at}


class WecomConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not get_account_permissions(request.account).intersection({'dispatch_config:view', 'dispatch_config:edit'}):
            raise PermissionDenied('无企微配置查看权限')
        return Response({'code': 0, 'data': config_data(WecomDispatchConfig.objects.filter(pk=1).first())})

    def put(self, request):
        if 'dispatch_config:edit' not in get_account_permissions(request.account):
            raise PermissionDenied('无企微配置权限')
        serializer = ConfigInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data['enabled']:
            from stores.license_benefits import require_content_operations
            require_content_operations()
        with transaction.atomic():
            row, _ = WecomDispatchConfig.objects.select_for_update().get_or_create(pk=1)
            if row.revision != data['revision']:
                raise ConfigConflict()
            if data['clear_webhook']:
                row.webhook_ciphertext = ''
            elif data.get('webhook'):
                row.webhook_ciphertext = encrypt_token(data['webhook'])
            if data['enabled'] and not valid_webhook(decrypt_token(row.webhook_ciphertext)):
                raise serializers.ValidationError('启用企微前请保存有效的机器人地址')
            row.enabled = data['enabled']
            row.auto_new_orders = data['auto_new_orders']
            row.auto_rejected_orders = data['auto_rejected_orders']
            row.revision += 1
            row.save()
        return Response({'code': 0, 'data': config_data(row), 'msg': '企微配置已保存'})
