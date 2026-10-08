from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet, GenericViewSet
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.views import APIView
from console.mixins import EnvelopeViewSetMixin
from console.permissions import HasConsolePerm
from club_accounts.models import ClubAccount
from .models import (Broadcast, BroadcastRule, ClubContent, LotteryCampaign, LotteryPrize,
    MemberProfile, PointLedger, RechargePackage, RewardClaim, RewardItem, VipTier, GiftBenefitRule)
from .serializers import (BroadcastSerializer, BroadcastRuleSerializer, CampaignSerializer,
    ClaimSerializer, ConfigSerializer, ContentSerializer, PackageSerializer, RewardSerializer,
    TierSerializer, GiftBenefitSerializer)
from .services import config, process_claim, record_external_points, validate_campaign


class RewardViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = RewardItem.objects.all()
    serializer_class = RewardSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        # 与兑换/抽奖串行读取，禁止用旧库存覆盖刚完成的扣减。
        self.queryset = RewardItem.objects.select_for_update().all()
        return super().update(request, *args, **kwargs)


class GiftBenefitViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = GiftBenefitRule.objects.order_by('id')
    serializer_class = GiftBenefitSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}


class ClaimViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    queryset = RewardClaim.objects.select_related('account', 'reward').all()
    serializer_class = ClaimSerializer
    default_perm = 'engagement:fulfill'
    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('status'):
            qs = qs.filter(status=self.request.query_params['status'])
        return qs
    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        get_object_or_404(self.get_queryset(), pk=pk)
        state = serializers.ChoiceField(choices=RewardClaim.Status.choices).run_validation(request.data.get('status'))
        note = serializers.CharField(max_length=255).run_validation(request.data.get('note'))
        claim = process_claim(pk, state, note, request.account)
        return Response({'code': 0, 'data': ClaimSerializer(claim).data})


class BroadcastRuleViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = BroadcastRule.objects.order_by('id')
    serializer_class = BroadcastRuleSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}
    # 删除规则会触发默认规则重建，运营应使用启停开关。
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']


class BroadcastViewSet(EnvelopeViewSetMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    queryset = Broadcast.objects.all()
    serializer_class = BroadcastSerializer
    default_perm = 'engagement:view'
    required_perms = {'update': 'engagement:edit', 'partial_update': 'engagement:edit'}

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self.queryset = self.queryset.select_for_update()
        return super().update(request, *args, **kwargs)


class ProviderNamingViewSet(EnvelopeViewSetMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    from .models import ProviderNaming
    from .serializers import ProviderNamingSerializer
    queryset = ProviderNaming.objects.select_related('provider', 'sponsor').order_by('-updated_at')
    serializer_class = ProviderNamingSerializer
    default_perm = 'engagement:view'
    required_perms = {'update': 'engagement:edit', 'partial_update': 'engagement:edit'}

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self.queryset = self.queryset.select_for_update()
        return super().update(request, *args, **kwargs)


class ContentViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = ClubContent.objects.all()
    serializer_class = ContentSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}


class TierViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = VipTier.objects.select_related('boss_type').all()
    serializer_class = TierSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}
    @action(detail=False, methods=['get'], url_path='service-options')
    def service_options(self, request):
        from django.db.models import Q
        from orders.models import ServiceItem
        rows = ServiceItem.objects.filter(is_active=True, is_addon=False, purchase_mode='DIRECT').exclude(service_category__is_gift=True).select_related('game_category').order_by('id')
        keyword = request.query_params.get('keyword', '').strip()[:100]
        if keyword:
            rows = rows.filter(Q(name__icontains=keyword) | Q(catalog_family__icontains=keyword))
        return Response({'code': 0, 'data': [{'id': row.pk, 'name': row.name,
            'game': row.game_category.name if row.game_category else '', 'price': row.price,
            'unit': row.price_unit} for row in rows[:500]]})


class PackageViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = RechargePackage.objects.all()
    serializer_class = PackageSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}


class CampaignViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = LotteryCampaign.objects.order_by('-id')
    serializer_class = CampaignSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy', 'configure']}
    def perform_create(self, serializer):
        if serializer.validated_data.get('enabled'):
            raise serializers.ValidationError('请先创建活动并配置完整奖池，再启用')
        serializer.save()
    def perform_update(self, serializer):
        raise serializers.ValidationError('请使用奖池配置操作保存完整活动，避免部分更新概率')
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def configure(self, request, pk=None):
        campaign = get_object_or_404(LotteryCampaign.objects.select_for_update(), pk=pk)
        version = serializers.IntegerField(min_value=1).run_validation(request.data.get('version'))
        if version != campaign.version:
            raise serializers.ValidationError('配置已被其他人员修改，请刷新后重试')
        data = dict(request.data)
        serializer = CampaignSerializer(campaign, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        rows = data.get('prizes')
        if not isinstance(rows, list) or not 2 <= len(rows) <= 20:
            raise serializers.ValidationError('奖项数量应为 2–20 个')
        existing = {p.pk: p for p in campaign.prizes.all()}
        keep = set()
        reward_ids = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise serializers.ValidationError('奖池数据格式不正确')
            reward_id = serializers.IntegerField(min_value=1).run_validation(row.get('reward'))
            probability = serializers.IntegerField(min_value=0, max_value=10000).run_validation(row.get('probability'))
            if reward_id in reward_ids:
                raise serializers.ValidationError('同一奖品不能重复配置')
            reward_ids.add(reward_id)
            get_object_or_404(RewardItem, pk=reward_id)
            prize, _ = LotteryPrize.objects.update_or_create(campaign=campaign, reward_id=reward_id,
                defaults={'probability': probability, 'sort_order': index})
            keep.add(prize.pk)
        # 已抽中的奖项受 PROTECT 保护，只能保留并把概率设为 0。
        removed = campaign.prizes.exclude(pk__in=keep)
        if removed.filter(lotterydraw__isnull=False).exists():
            raise serializers.ValidationError('已有中奖记录的奖项请将概率设为 0，不能删除')
        removed.delete()
        campaign = serializer.save(version=campaign.version + 1)
        if campaign.starts_at and campaign.ends_at and campaign.ends_at <= campaign.starts_at:
            raise serializers.ValidationError('结束时间必须晚于开始时间')
        if campaign.enabled:
            validate_campaign(campaign, list(campaign.prizes.select_related('reward')))
        return Response({'code': 0, 'data': CampaignSerializer(campaign).data})


class OperationConfigView(APIView):
    permission_classes = [HasConsolePerm]
    default_perm = 'engagement:view'
    required_perms = {'put': 'engagement:edit'}
    def get_permissions(self):
        self.default_perm = 'engagement:edit' if self.request.method == 'PUT' else 'engagement:view'
        return super().get_permissions()
    def get(self, request):
        return Response({'code': 0, 'data': ConfigSerializer(config()).data})
    def put(self, request):
        serializer = ConfigSerializer(config(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'code': 0, 'data': serializer.data})


class ExternalPointsView(APIView):
    permission_classes = [HasConsolePerm]
    default_perm = 'engagement:points'
    def post(self, request):
        account_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('account_id'))
        account = get_object_or_404(ClubAccount, pk=account_id, account_type='BOSS', is_active=True)
        kind = serializers.ChoiceField(choices=['KOOK_ONLINE', 'KOOK_CHECKIN']).run_validation(request.data.get('kind'))
        day = serializers.DateField().run_validation(request.data.get('day'))
        units = serializers.IntegerField(min_value=1, max_value=86400).run_validation(request.data.get('online_seconds', 1))
        source_id = serializers.CharField(max_length=100).run_validation(request.data.get('source_id'))
        ledger = record_external_points(account, kind=kind, day=day, units=units, source_id=source_id, operator=request.account)
        return Response({'code': 0, 'data': {'id': ledger.pk, 'points': ledger.amount / 100}})


class ContentImageUploadView(APIView):
    permission_classes = [HasConsolePerm]
    default_perm = 'engagement:edit'
    def post(self, request):
        from common.catalog_images import save_catalog_upload
        from django.core.exceptions import ValidationError as ImageValidationError
        try:
            upload = serializers.ImageField().run_validation(request.FILES.get('image'))
        except ImageValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        if upload.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('图片大小不能超过 10MB')
        try:
            data = save_catalog_upload(upload, 'club-catalog/operations')
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error
        return Response({'code': 0, 'data': data})
