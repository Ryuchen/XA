from django.db import transaction
from django.db.models import Q
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
    TierSerializer, GiftBenefitSerializer, with_claim_return_status)
from .services import ClaimDetailsChanged, ClaimStatusChanged, ClaimTermsChanged, config, process_claim, record_external_points, validate_campaign


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
        qs = with_claim_return_status(super().get_queryset())
        if self.request.query_params.get('status'):
            qs = qs.filter(status=self.request.query_params['status'])
        if self.request.query_params.get('source'):
            qs = qs.filter(source=self.request.query_params['source'])
        kind = self.request.query_params.get('kind')
        if kind:
            kind = serializers.ChoiceField(choices=RewardItem.Kind.choices).run_validation(kind)
            qs = qs.filter(kind_snapshot=kind)
        account_type = self.request.query_params.get('account_type')
        if account_type:
            account_type = serializers.ChoiceField(choices=['BOSS', 'PROVIDER']).run_validation(account_type)
            qs = qs.filter(account__account_type=account_type)
        if self.request.query_params.get('tag_ready_for_review') == '1':
            from .queries import tags_ready_for_review
            qs = tags_ready_for_review(qs)
        if self.request.query_params.get('needs_tag_review') == '1':
            qs = qs.filter(kind_snapshot='TAG', status='COMPLETED', tag_needs_review=True)
        search = self.request.query_params.get('search', '').strip()
        if search:
            condition = (Q(name_snapshot__icontains=search) | Q(account__nickname__icontains=search) |
                Q(account__username__icontains=search) | Q(kind_snapshot='TAG', details__tag__icontains=search))
            if search.isdigit():
                condition |= Q(pk=int(search)) | Q(account_id=int(search)) | Q(source_order_id=int(search))
            qs = qs.filter(condition)
        return qs
    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        get_object_or_404(self.get_queryset(), pk=pk)
        state = serializers.ChoiceField(choices=RewardClaim.Status.choices).run_validation(request.data.get('status'))
        note = serializers.CharField(max_length=255).run_validation(request.data.get('note'))
        expected = request.data.get('expected_details')
        if expected is not None and not isinstance(expected, dict):
            raise serializers.ValidationError('交付资料格式无效')
        expected_status = request.data.get('expected_status')
        if expected_status is not None:
            expected_status = serializers.ChoiceField(choices=RewardClaim.Status.choices).run_validation(expected_status)
        expected_terms = request.data.get('expected_terms')
        if expected_terms is not None and not isinstance(expected_terms, dict):
            raise serializers.ValidationError('权益规格格式无效')
        try:
            claim = process_claim(pk, state, note, request.account, expected_details=expected, expected_status=expected_status, expected_terms=expected_terms, tag_reviewed=request.data.get('tag_reviewed') is True)
        except ClaimDetailsChanged:
            return Response({'code': 409, 'errorCode': 'CLAIM_DETAILS_CHANGED',
                'msg': '交付资料已更新，请核对最新内容后重新提交'}, status=409)
        except ClaimStatusChanged:
            return Response({'code': 409, 'errorCode': 'CLAIM_STATUS_CHANGED',
                'msg': '交付状态已变化，请核对最新处理结果'}, status=409)
        except ClaimTermsChanged:
            return Response({'code': 409, 'errorCode': 'CLAIM_TERMS_CHANGED',
                'msg': '权益规格已变化，请核对最新时长和类型'}, status=409)
        return Response({'code': 0, 'data': ClaimSerializer(claim).data})

    @action(detail=True, methods=['post'], url_path='review-tag')
    @transaction.atomic
    def review_tag(self, request, pk=None):
        from .models import MemberTag
        claim = get_object_or_404(RewardClaim.objects.select_for_update(), pk=pk, kind_snapshot='TAG', status='COMPLETED')
        tag = get_object_or_404(MemberTag.objects.select_for_update(), account=claim.account, event_key=f'claim:{claim.pk}:tag')
        if request.data.get('tag_reviewed') is not True:
            raise serializers.ValidationError('请先确认已审核 TAG 内容符合要求')
        candidate = str(claim.details.get('tag', '')).strip() if tag.suspended_at else tag.title
        candidate = candidate or tag.title
        if request.data.get('expected_tag') != candidate:
            return Response({'code': 409, 'msg': '标签内容已变化，请重新打开后审核'}, status=409)
        if not tag.reviewed_at or tag.suspended_at:
            tag.title = candidate
            tag.reviewed_at, tag.reviewed_by = timezone.now(), request.account
            tag.suspended_at, tag.suspended_by, tag.suspension_reason = None, None, ''
            tag.save(update_fields=['title', 'reviewed_at', 'reviewed_by', 'suspended_at', 'suspended_by', 'suspension_reason'])
            from .notifier import notify_claims_after_commit
            notify_claims_after_commit()
            from club_accounts.models import LegacyAccountMap
            recipient_id = LegacyAccountMap.objects.filter(account_id=claim.account_id).values_list('legacy_user_id', flat=True).first()
            if recipient_id:
                from orders.services import push_message_safe
                transaction.on_commit(lambda: push_message_safe(recipient_id=recipient_id, recipient_account_id=claim.account_id,
                    title='标签审核通过', preview=f'「{tag.title}」已通过审核，可在权益记录查看。',
                    msg_type='SYSTEM', action_url=f'/pages/reward-claim/index?id={claim.pk}'))
        return Response({'code': 0, 'data': ClaimSerializer(claim).data})

    @action(detail=True, methods=['post'], url_path='suspend-tag')
    @transaction.atomic
    def suspend_tag(self, request, pk=None):
        from .models import MemberTag
        claim = get_object_or_404(RewardClaim.objects.select_for_update(), pk=pk, kind_snapshot='TAG', status='COMPLETED')
        tag = get_object_or_404(MemberTag.objects.select_for_update(), account=claim.account, event_key=f'claim:{claim.pk}:tag')
        reason = serializers.CharField(min_length=2, max_length=255, trim_whitespace=True).run_validation(request.data.get('reason'))
        if request.data.get('expected_tag') != tag.title:
            return Response({'code': 409, 'msg': '标签内容已变化，请重新打开后处理'}, status=409)
        if tag.suspended_at:
            if tag.suspension_reason != reason:
                return Response({'code': 409, 'msg': '标签已暂停展示，请刷新后核对处理说明'}, status=409)
            return Response({'code': 0, 'data': ClaimSerializer(claim).data})
        if not tag.reviewed_at:
            raise serializers.ValidationError('标签尚未审核，无需暂停展示')
        tag.suspended_at, tag.suspended_by, tag.suspension_reason = timezone.now(), request.account, reason
        tag.save(update_fields=['suspended_at', 'suspended_by', 'suspension_reason'])
        from .notifier import notify_claims_after_commit
        notify_claims_after_commit()
        from club_accounts.models import LegacyAccountMap
        recipient_id = LegacyAccountMap.objects.filter(account_id=claim.account_id).values_list('legacy_user_id', flat=True).first()
        if recipient_id:
            from orders.services import push_message_safe
            transaction.on_commit(lambda: push_message_safe(recipient_id=recipient_id, recipient_account_id=claim.account_id,
                title='标签暂停展示', preview=f'「{tag.title}」暂不展示，请查看说明或联系客服。',
                msg_type='SYSTEM', action_url=f'/pages/reward-claim/index?id={claim.pk}'))
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
