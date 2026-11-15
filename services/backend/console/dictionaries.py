"""Allowlisted operational dictionaries and a catalog of existing rule editors."""
from django.db import IntegrityError
from stores.db import transaction
from django.db.models import Count, Q
from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.validators import UniqueTogetherValidator

from .dictionary_models import BusinessDictionaryItem
from .catalog_write_lock import CatalogMutationMixin
from .mixins import EnvelopeViewSetMixin
from .pagination import ConsolePagination
from .permissions import IsConsoleUser, get_account_permissions


TYPE_DESCRIPTIONS = {
    'SALARY_POSITION': '薪资核算岗位，与后台角色及权限独立；薪资金额与提成规则在岗位薪资中配置。',
    'AD_CHANNEL': '每日投流成本的渠道选项；新增渠道不自动认定收入归属。',
    'COST_CATEGORY': '运营支出的分类；不改变订单、钱包和薪资账务。',
}
CONSUMER_PERMISSIONS = {
    'SALARY_POSITION': {'salary:view', 'salary:edit'},
    'AD_CHANNEL': {'cost:view', 'cost:edit'},
    'COST_CATEGORY': {'cost:view', 'cost:edit'},
}
LEGACY_DICTIONARIES = [
    {'id': 'game-categories', 'name': '游戏类目', 'resource': 'game-categories',
     'path': 'service-items', 'permission': 'service:view', 'edit_permission': 'service:edit',
     'description': '服务商品页面的“游戏类目”页签；保留商品、陪玩和历史关联。'},
    {'id': 'service-categories', 'name': '服务分类', 'resource': 'service-categories',
     'path': 'service-items', 'permission': 'service:view', 'edit_permission': 'service:edit',
     'description': '服务商品页面的“服务分类”页签；礼物标记及订单类别使用原业务规则。'},
    {'id': 'boss-types', 'name': '老板分级', 'resource': 'boss-types',
     'path': 'boss-types', 'permission': 'boss_type:view', 'edit_permission': 'boss_type:edit',
     'description': '保留分级折扣、会员关联与原编辑权限。'},
    {'id': 'escort-levels', 'name': '陪玩等级', 'resource': 'escort-levels',
     'path': 'escort-levels', 'permission': 'escort_level:view', 'edit_permission': 'escort_level:edit',
     'description': '保留等级抽成、资格及已有订单快照；不迁入普通键值字典。'},
]
POLICY_ENTRIES = [
    {'id': 'commission', 'name': '平台抽成', 'path': 'reports', 'permission': 'report:view',
     'description': '报单审核中的抽成配置；保留百分比、整单固定额和自动规则。'},
    {'id': 'withdraw', 'name': '提现规则', 'path': 'withdrawals', 'permission': 'withdraw:view',
     'description': '提现管理中的最低金额及税率配置。'},
    {'id': 'payment-fee', 'name': '支付费率', 'path': 'payment-fee', 'permission': 'payment_fee:view',
     'description': '使用原支付费率校验、修订历史与报价口径。'},
    {'id': 'deposit-rules', 'name': '押金与结款', 'path': 'deposit-rules', 'permission': 'deposit_rule:view',
     'description': '押金资格、结款比例与实销规则仍由专用配置管理。'},
    {'id': 'provider-pass', 'name': '接单通行证', 'path': 'provider-passes', 'permission': 'escort:view',
     'edit_permission': 'escort:edit', 'description': '配置本店通行证价格和各档接单可见延迟；购买后生效 24 小时。'},
    {'id': 'recharge-packages', 'name': '预存档位及赠送', 'path': 'club-operations',
     'permission': 'engagement:view', 'edit_permission': 'engagement:edit',
     'description': '配置本店充值门槛及赠送，消费积分按付款时会员规则计算。'},
    {'id': 'order-controls', 'name': '待接单时限与峰谷限流', 'path': 'orders', 'permission': 'order:view',
     'description': '订单页面对应配置入口；实际修改仍校验时限或限流权限。'},
    {'id': 'provider-capacity', 'name': '陪玩接单名额', 'path': 'orders', 'permission': 'order:view',
     'edit_permission': 'dictionary:edit', 'description': '本店统一同时接单上限，已确认的预约开始时可暂时超出。'},
    {'id': 'grab-window', 'name': '抢单窗口', 'path': 'orders', 'permission': 'order:view',
     'edit_permission': 'dictionary:edit', 'description': '配置本店抢单等待窗口；订单分发状态以实际订单为准。'},
    {'id': 'order-account-fields', 'name': '下单游戏资料', 'path': 'dictionaries', 'permission': 'dictionary:view',
     'edit_permission': 'dictionary:edit', 'description': '配置本店游戏与服务的资料必填项；服务规则优先。'},
    {'id': 'club-config', 'name': '会员与积分规则', 'path': 'club-operations', 'permission': 'engagement:view',
     'description': '保留会员、积分、权益和转盘的专用模型与校验。'},
    {'id': 'kook-dispatch', 'name': 'KOOK 派单', 'path': 'kook-dispatch', 'permission': 'dispatch_config:view',
     'description': '机器人密钥继续密文存储，不进入通用字典或配置值列表。'},
]


def _type(value, *, required=True):
    if value in (None, '') and not required:
        return None
    if value not in BusinessDictionaryItem.Type.values:
        raise ValidationError('请选择有效的字典类型')
    return value


def _denied():
    return Response({'code': 403, 'msg': '无字典操作权限'}, status=403)


class DictionaryItemSerializer(EntityNumberModelSerializer):
    code = serializers.RegexField(
        r'^[A-Z][A-Z0-9_]{0,63}$', max_length=64,
        error_messages={'invalid': '编码须以大写字母开头，仅含大写字母、数字和下划线'},
    )
    sort_order = serializers.IntegerField(min_value=0, max_value=2147483647, required=False)

    class Meta:
        model = BusinessDictionaryItem
        fields = ['id', 'type', 'code', 'name', 'remark', 'sort_order', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
        validators = [UniqueTogetherValidator(
            queryset=BusinessDictionaryItem.objects.all(), fields=['type', 'code'],
            message='该字典类型下的编码已存在',
        )]

    def validate(self, attrs):
        from .store_roles import is_store_finance
        kind = attrs.get('type', getattr(self.instance, 'type', ''))
        code = attrs.get('code', getattr(self.instance, 'code', ''))
        name = attrs.get('name', getattr(self.instance, 'name', ''))
        if kind == 'SALARY_POSITION' and is_store_finance(code, name):
            raise ValidationError('门店不设置财务岗位；平台财务薪资由平台管理')
        if self.instance is not None:
            for key in ('type', 'code'):
                if key in attrs and attrs[key] != getattr(self.instance, key):
                    raise ValidationError({key: '创建后不可修改；请修改名称或停用旧项'})
        return attrs


class DictionaryItemViewSet(CatalogMutationMixin, EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = DictionaryItemSerializer
    default_perm = 'dictionary:view'
    required_perms = {action: 'dictionary:edit' for action in ['create', 'update', 'partial_update', 'destroy']}

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                serializer.save()
        except IntegrityError:
            raise ValidationError('该字典类型下的编码已存在，请刷新后核对') from None

    def get_queryset(self):
        qs = BusinessDictionaryItem.objects.all()
        kind = _type(self.request.query_params.get('type'), required=False)
        if kind:
            qs = qs.filter(type=kind)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(name__icontains=keyword) | Q(code__icontains=keyword))
        active = self.request.query_params.get('is_active')
        if active not in (None, ''):
            if active not in ('true', 'false', '1', '0'):
                raise ValidationError('启用状态无效')
            qs = qs.filter(is_active=active in ('true', '1'))
        return qs.order_by('type', 'sort_order', 'id')


class DictionaryCatalogView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        permissions = get_account_permissions(request.account)
        if not {'dictionary:view', 'dictionary:edit'}.intersection(permissions):
            return _denied()
        counts = {row['type']: row['count'] for row in BusinessDictionaryItem.objects.values('type').annotate(count=Count('id'))}
        from .config_readiness import selected_configuration_evidence
        return Response({'code': 0, 'data': {
            'setup_evidence_capability': 'SELECTED_RULES_V1',
            'setup_evidence': selected_configuration_evidence(permissions),
            'types': [{'type': kind, 'name': name, 'description': TYPE_DESCRIPTIONS[kind],
                       'count': counts.get(kind, 0)} for kind, name in BusinessDictionaryItem.Type.choices],
            'legacy': [{**entry, 'can_view': entry['permission'] in permissions,
                        'can_edit': entry['edit_permission'] in permissions} for entry in LEGACY_DICTIONARIES],
            'policies': [{**entry, 'can_view': (entry['permission'] in permissions
                          or (entry['id'] == 'provider-pass' and 'escort:edit' in permissions)),
                          **({'can_edit': entry['edit_permission'] in permissions}
                             if 'edit_permission' in entry else {})} for entry in POLICY_ENTRIES],
            'readonly': [
                '订单、支付、提现、售后状态由实际业务流程驱动，不能在字典中任意增删。',
                '账务类型、资格档位组合及金额转换仍由业务代码管理；后台权限角色不等同于薪资岗位。',
            ],
        }})


class DictionaryOptionsView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        kind = _type(request.query_params.get('type'))
        permissions = get_account_permissions(request.account)
        allowed = CONSUMER_PERMISSIONS[kind] | {'dictionary:view', 'dictionary:edit'}
        if not allowed.intersection(permissions):
            return _denied()
        qs = BusinessDictionaryItem.objects.filter(type=kind, is_active=True).order_by('sort_order', 'id')
        keyword = request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(name__icontains=keyword) | Q(code__icontains=keyword))
        pagination = ConsolePagination()
        page = pagination.paginate_queryset(qs, request, view=self)
        return pagination.get_paginated_response([
            {'id': item.id, 'type': item.type, 'code': item.code, 'name': item.name, 'is_active': item.is_active}
            for item in page
        ])
