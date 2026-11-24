from stores.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
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
    TierSerializer, VipUpgradeReviewSerializer, GiftBenefitSerializer, with_claim_return_status)
from .services import ClaimDetailsChanged, ClaimStatusChanged, ClaimTermsChanged, config, process_claim, record_external_points, validate_campaign
from .provider_entitlement_lifecycle import staff_provider_claim_edit, staff_provider_naming_edit
from .db_conflict import api_lock_conflict


def _require_feature(feature, message):
    from rest_framework.exceptions import PermissionDenied
    from stores.license_benefits import LicenseDecisionUnavailable, check_new_benefits
    outcome, _ = check_new_benefits(feature)
    if outcome == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if outcome != 'ALLOW':
        raise PermissionDenied(message)


class RewardViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = RewardItem.objects.all()
    serializer_class = RewardSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}
    def perform_create(self, serializer):
        values = serializer.validated_data
        if values.get('is_active', False) and values.get('in_shop', True):
            _require_feature('POINTS_SHOP', '当前门店授权不支持上架积分商城商品')
        super().perform_create(serializer)
        from .notifier import notify_reward_catalog_after_commit
        notify_reward_catalog_after_commit()
    def perform_update(self, serializer):
        instance, values = serializer.instance, serializer.validated_data
        changed = {key: value for key, value in values.items() if getattr(instance, key) != value}
        if {'kind', 'in_shop', 'vip_quota_kind'} & set(changed):
            from .vip_reward_references import referenced_tiers
            upgrades, birthdays = referenced_tiers(self._locked_vip_rewards, instance.pk)
            kind = values.get('kind', instance.kind)
            in_shop = values.get('in_shop', instance.in_shop)
            quota_kind = values.get('vip_quota_kind', instance.vip_quota_kind)
            if upgrades and (in_shop or kind not in ('SERVICE', 'NAMING', 'TAG') or quota_kind == 'BIRTHDAY_GIFT'):
                raise serializers.ValidationError('该奖励仍被 VIP 升级权益引用，请先调整等级配置')
            if birthdays and (in_shop or quota_kind != 'BIRTHDAY_GIFT'):
                raise serializers.ValidationError('该奖励仍被 VIP 生日权益引用，请先调整等级配置')
        was_listed = instance.is_active and instance.in_shop
        will_be_listed = values.get('is_active', instance.is_active) and values.get('in_shop', instance.in_shop)
        # Keep safe off-shelf actions available even if the license expired;
        # do not combine the bypass with changes to the reward's terms.
        off_shelf_only = (bool(changed) and set(changed) <= {'is_active', 'in_shop'}
                          and all(value is False for value in changed.values()))
        if changed and (will_be_listed or (was_listed and not off_shelf_only)):
            _require_feature('POINTS_SHOP', '当前门店授权不支持上架或修改积分商城商品')
        super().perform_update(serializer)
        from .notifier import notify_reward_catalog_after_commit
        notify_reward_catalog_after_commit()
    @api_lock_conflict
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        # The store and platform tier writers take this same config lock
        # before validating and saving their JSON reward references.
        from .models import ClubOperationConfig
        config()
        ClubOperationConfig.objects.select_for_update().get(pk=1)
        self._locked_vip_rewards = list(VipTier.objects.select_for_update()
            .order_by('pk').values_list('pk', 'upgrade_rewards', 'benefits'))
        self.queryset = RewardItem.objects.select_for_update().all()
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        from .vip_reward_references import referenced_tiers
        upgrades, birthdays = referenced_tiers(self._locked_vip_rewards, instance.pk)
        if upgrades or birthdays:
            raise serializers.ValidationError('该奖励仍被 VIP 升级或生日权益引用，请先移除引用')
        super().perform_destroy(instance)
        from .notifier import notify_reward_catalog_after_commit
        notify_reward_catalog_after_commit()
    @api_lock_conflict
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        # Structural edits share the VIP reference lock. Inventory and
        # stop-sale edits retain their original reward-first lock order.
        if {'kind', 'in_shop', 'vip_quota_kind'} & set(request.data):
            from .models import ClubOperationConfig
            config()
            ClubOperationConfig.objects.select_for_update().get(pk=1)
            self._locked_vip_rewards = list(VipTier.objects.select_for_update()
                .order_by('pk').values_list('pk', 'upgrade_rewards', 'benefits'))
        self.queryset = RewardItem.objects.select_for_update().all()
        return super().update(request, *args, **kwargs)


class GiftBenefitViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = GiftBenefitRule.objects.order_by('id')
    serializer_class = GiftBenefitSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}

    def perform_create(self, serializer):
        if serializer.validated_data.get('enabled', False):
            _require_feature('GIFTS', '当前门店授权不支持启用礼物权益规则')
        serializer.save()

    def perform_update(self, serializer):
        if serializer.validated_data.get('enabled', serializer.instance.enabled):
            _require_feature('GIFTS', '当前门店授权不支持修改或启用礼物权益规则')
        serializer.save()

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        # Check authorization against the current enabled state after any
        # concurrent update, not a pre-lock instance.
        self.queryset = GiftBenefitRule.objects.select_for_update().all()
        return super().update(request, *args, **kwargs)


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
    @api_lock_conflict
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
    @staff_provider_claim_edit
    @transaction.atomic
    def review_tag(self, request, pk=None):
        from .models import MemberTag
        claim = get_object_or_404(RewardClaim.objects.select_for_update(), pk=pk, kind_snapshot='TAG', status='COMPLETED')
        tag = get_object_or_404(MemberTag.objects.select_for_update(), account=claim.account, event_key=f'claim:{claim.pk}:tag')
        if tag.expires_at and tag.expires_at <= timezone.now():
            raise serializers.ValidationError('标签已到期，无法继续审核')
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
            from .notifier import notify_provider_tags_after_commit
            notify_provider_tags_after_commit(claim.account)
            from .notifier import notify_claims_after_commit
            notify_claims_after_commit()
            from club_accounts.models import LegacyAccountMap
            recipient_id = LegacyAccountMap.objects.filter(account_id=claim.account_id).values_list('legacy_user_id', flat=True).first()
            from orders.services import push_message_safe
            from site_messages.claim_cycle import recipient_cycle
            claim_cycle = recipient_cycle(claim)
            transaction.on_commit(lambda: push_message_safe(recipient_id=recipient_id, recipient_account_id=claim.account_id,
                title='标签审核通过', preview=f'「{tag.title}」已通过审核，可在权益记录查看。',
                msg_type='SYSTEM', action_url=f'/pages/reward-claim/index?id={claim.pk}',
                recipient_business_cycle=claim_cycle, require_cycle=True))
        return Response({'code': 0, 'data': ClaimSerializer(claim).data})

    @action(detail=True, methods=['post'], url_path='suspend-tag')
    @staff_provider_claim_edit
    @transaction.atomic
    def suspend_tag(self, request, pk=None):
        from .models import MemberTag
        claim = get_object_or_404(RewardClaim.objects.select_for_update(), pk=pk, kind_snapshot='TAG', status='COMPLETED')
        tag = get_object_or_404(MemberTag.objects.select_for_update(), account=claim.account, event_key=f'claim:{claim.pk}:tag')
        if tag.expires_at and tag.expires_at <= timezone.now():
            raise serializers.ValidationError('标签已到期，无需暂停展示')
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
        from .notifier import notify_provider_tags_after_commit
        notify_provider_tags_after_commit(claim.account)
        from .notifier import notify_claims_after_commit
        notify_claims_after_commit()
        from club_accounts.models import LegacyAccountMap
        recipient_id = LegacyAccountMap.objects.filter(account_id=claim.account_id).values_list('legacy_user_id', flat=True).first()
        from orders.services import push_message_safe
        from site_messages.claim_cycle import recipient_cycle
        claim_cycle = recipient_cycle(claim)
        transaction.on_commit(lambda: push_message_safe(recipient_id=recipient_id, recipient_account_id=claim.account_id,
            title='标签暂停展示', preview=f'「{tag.title}」暂不展示，请查看说明或联系客服。',
            msg_type='SYSTEM', action_url=f'/pages/reward-claim/index?id={claim.pk}',
            recipient_business_cycle=claim_cycle, require_cycle=True))
        return Response({'code': 0, 'data': ClaimSerializer(claim).data})


class BroadcastRuleViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = BroadcastRule.objects.order_by('id')
    serializer_class = BroadcastRuleSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}
    # 删除规则会触发默认规则重建，运营应使用启停开关。
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def perform_create(self, serializer):
        from stores.license_benefits import require_content_operations
        require_content_operations()
        serializer.save()

    def perform_update(self, serializer):
        from stores.license_benefits import content_deactivation_only, require_content_operations
        if not content_deactivation_only(serializer.instance, serializer.validated_data, field='enabled'):
            require_content_operations()
        serializer.save()

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self.queryset = self.get_queryset().select_for_update()
        return super().update(request, *args, **kwargs)


class BroadcastViewSet(EnvelopeViewSetMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    queryset = Broadcast.objects.all()
    serializer_class = BroadcastSerializer
    default_perm = 'engagement:view'
    required_perms = {'update': 'engagement:edit', 'partial_update': 'engagement:edit'}

    def perform_update(self, serializer):
        changed = {key for key, value in serializer.validated_data.items()
                   if getattr(serializer.instance, key) != value}
        # Keeping an existing record readable, hiding it, or repeating an
        # already-applied request does not create a new content opportunity.
        hiding_only = changed == {'is_visible'} and serializer.validated_data['is_visible'] is False
        if changed and not hiding_only:
            from stores.license_benefits import require_content_operations
            require_content_operations()
        serializer.save()

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

    @staff_provider_naming_edit
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self.queryset = self.queryset.select_for_update()
        return super().update(request, *args, **kwargs)


class ContentViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = ClubContent.objects.all()
    serializer_class = ContentSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}

    # POPUP is hidden from the current client, but is still writable through
    # this API and must not become a way around content admission.
    licensed_kinds = (ClubContent.Kind.RIGHTS, ClubContent.Kind.HOT, ClubContent.Kind.POPUP)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self._setting_up():
            return queryset.filter(kind=ClubContent.Kind.ORDER_RULES)
        return queryset

    def _setting_up(self):
        from stores.context import current_store
        from stores.models import Store
        store = current_store()
        return store is not None and store.status == Store.Status.CONFIGURING

    def _check_setup_kind(self, kind):
        if self._setting_up() and kind != ClubContent.Kind.ORDER_RULES:
            raise PermissionDenied('门店配置期间仅可维护下单须知')

    def perform_create(self, serializer):
        kind = serializer.validated_data['kind']
        self._check_setup_kind(kind)
        if kind in self.licensed_kinds:
            from stores.license_benefits import require_content_operations
            require_content_operations()
        serializer.save()

    def perform_update(self, serializer):
        self._check_setup_kind(serializer.validated_data.get('kind', serializer.instance.kind))
        optional = self.licensed_kinds
        from stores.license_benefits import content_deactivation_only, require_content_operations
        if ((serializer.instance.kind in optional or serializer.validated_data.get('kind') in optional)
                and not content_deactivation_only(serializer.instance, serializer.validated_data)):
            require_content_operations()
        serializer.save()

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self.queryset = self.get_queryset().select_for_update()
        return super().update(request, *args, **kwargs)


def _lock_empty_vip_setup_scope():
    """Serialize the empty-store exception with boss admission."""
    from club_accounts.license_quota import enabled, lock_category
    from stores.context import current_store
    from stores.models import Store
    store = current_store()
    if not enabled() or store is None or store.status != Store.Status.CONFIGURING:
        return False
    lock_category('BOSS')
    return True


def _require_vip_recalculation_or_empty_setup(locked_scope):
    # A locking read sees the current committed membership even under MySQL
    # repeatable-read. Retired accounts are included because their original
    # periods and entitlements must not be silently reinterpreted.
    if (not locked_scope or ClubAccount.objects.select_for_update().filter(
            account_type='BOSS').order_by('pk').exists()):
        raise serializers.ValidationError('会员重算及既有权益保护尚未开放，请联系平台处理后重试')


def _save_disabled_vip_tier(serializer, request, *, action):
    """Persist the configuration and its pending shutdown-cycle fact together."""
    from console.models import AdminAuditLog
    from .vip_deferred import mark_deferred
    prior = serializer.instance
    before = ({'id': prior.pk, 'active': prior.active, 'level': prior.level,
               'threshold': prior.recharge_threshold} if prior else None)
    tier = serializer.save()
    deferred = mark_deferred()
    AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
        operator_name=request.account.username[:150], method=request.method, path=request.path[:255],
        resource='vip-deferred-tier-' + action, object_id=str(tier.pk), status_code=200,
        request_body={'epoch': deferred.epoch, 'baseline_known': deferred.baseline_rules is not None,
            'before': before, 'after': {'id': tier.pk, 'active': tier.active, 'level': tier.level,
                                         'threshold': tier.recharge_threshold}})
    return tier


def _vip_recalculation_available():
    from .vip_transition import resume_guard_available
    return resume_guard_available()


class TierViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = VipTier.objects.select_related('boss_type').all()
    serializer_class = TierSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}
    required_perms['retirement_preview'] = 'engagement:edit'
    required_perms['recalculation_preview'] = 'engagement:edit'
    required_perms['creation_preview'] = 'engagement:edit'
    required_perms['reorder_preview'] = 'engagement:edit'
    required_perms['reorder'] = 'engagement:edit'
    def _lock_vip_config(self):
        from .models import ClubOperationConfig
        config()
        ClubOperationConfig.objects.select_for_update().get(pk=1)

    def _lock_tier_types(self, request, pk):
        from users.models import BossType
        current = VipTier.objects.filter(pk=pk).values_list('boss_type_id', flat=True).first()
        requested = request.data.get('boss_type')
        type_ids = {current} if current else set()
        if type(requested) is int and requested > 0:
            type_ids.add(requested)
        list(BossType.objects.select_for_update().filter(pk__in=type_ids).order_by('pk').values_list('pk', flat=True))
        self.queryset = VipTier.objects.select_for_update().select_related('boss_type')

    @api_lock_conflict
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        from django.conf import settings
        self._recalculation_locked_accounts = None
        self._empty_vip_setup_scope = (not _vip_recalculation_available()
                                       and _lock_empty_vip_setup_scope())
        if _vip_recalculation_available() and request.data.get('active', True) is not False:
            from club_accounts.license_quota import lock_category
            lock_category('BOSS')
            from .vip_threshold_change import lock_all_boss_wallets
            self._recalculation_locked_accounts = lock_all_boss_wallets()
        self._lock_vip_config()
        return super().create(request, *args, **kwargs)

    @api_lock_conflict
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        from django.conf import settings
        self._recalculation_locked_accounts = None
        self._empty_vip_setup_scope = (not _vip_recalculation_available()
                                       and _lock_empty_vip_setup_scope())
        if _vip_recalculation_available():
            old = VipTier.objects.filter(pk=kwargs.get('pk')).values('recharge_threshold', 'active', 'level').first()
            has_threshold = 'consumption_threshold' in request.data or 'recharge_threshold' in request.data
            proposed = request.data.get('consumption_threshold', request.data.get('recharge_threshold'))
            needs_wallets = (has_threshold and (type(proposed) is not int or not old or proposed != old['recharge_threshold']))
            needs_wallets |= bool(old and old['active'] != request.data.get('active', old['active']))
            if 'level' in request.data:
                requested_level = request.data['level']
                needs_wallets |= type(requested_level) is not int or not old or requested_level != old['level']
            if needs_wallets:
                from club_accounts.license_quota import lock_category
                lock_category('BOSS')
                from .vip_threshold_change import lock_all_boss_wallets
                self._recalculation_locked_accounts = lock_all_boss_wallets()
        self._lock_vip_config()
        self._lock_tier_types(request, kwargs.get('pk'))
        return super().update(request, *args, **kwargs)

    @api_lock_conflict
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self._lock_vip_config()
        self._lock_tier_types(request, kwargs.get('pk'))
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        from .notifier import notify_vip_config_changed
        from .vip_reward_references import lock_and_validate_upgrade_rewards
        lock_and_validate_upgrade_rewards(serializer.validated_data)
        if serializer.validated_data.get('active', True) and not serializer.validated_data['boss_type'].is_active:
            raise serializers.ValidationError('启用中的 VIP 等级须关联启用中的老板分级')
        from django.conf import settings
        from .models import ClubOperationConfig
        operation = ClubOperationConfig.objects.get(pk=1)
        if (not _vip_recalculation_available() and not operation.vip_enabled
                and serializer.validated_data.get('active', True)):
            _save_disabled_vip_tier(serializer, self.request, action='creation')
            transaction.on_commit(notify_vip_config_changed)
            return
        if _vip_recalculation_available() and serializer.validated_data.get('active', True):
            if self._recalculation_locked_accounts is None:
                raise serializers.ValidationError('等级状态已变化，请重新核对')
            from .vip_threshold_change import apply_creation
            apply_creation(serializer, self.request.data.get('recalculation_token'), self.request,
                           self._recalculation_locked_accounts)
        else:
            if serializer.validated_data.get('active', True):
                _require_vip_recalculation_or_empty_setup(self._empty_vip_setup_scope)
            serializer.save()
        transaction.on_commit(notify_vip_config_changed)

    def perform_update(self, serializer):
        from .notifier import notify_vip_config_changed
        from .vip_transition import resume_guard_available as transition_guard_available
        from .vip_reward_references import lock_and_validate_upgrade_rewards
        lock_and_validate_upgrade_rewards(serializer.validated_data)
        previous = (serializer.instance.active, serializer.instance.safe_box_service_ids,
                    serializer.instance.monthly_discount, serializer.instance.monthly_coupon_count,
                    serializer.instance.season_discount, serializer.instance.benefits)
        target_type = serializer.validated_data.get('boss_type', serializer.instance.boss_type)
        if serializer.validated_data.get('active', serializer.instance.active) and not target_type.is_active:
            raise serializers.ValidationError('启用中的 VIP 等级须关联启用中的老板分级')
        if target_type.pk != serializer.instance.boss_type_id:
            from club_accounts.models import ClubAccount
            if ClubAccount.objects.filter(account_type='BOSS', boss_type_id=serializer.instance.boss_type_id).exists():
                raise serializers.ValidationError('该等级仍有关联老板，不能直接更换老板分级；请先处理会员等级')
        from django.conf import settings
        from .models import ClubOperationConfig
        operation = ClubOperationConfig.objects.get(pk=1)
        if not _vip_recalculation_available() and not operation.vip_enabled:
            old = serializer.instance
            changed_threshold = serializer.validated_data.get('recharge_threshold', old.recharge_threshold) != old.recharge_threshold
            reactivating = not old.active and serializer.validated_data.get('active') is True
            level_changed = serializer.validated_data.get('level', old.level) != old.level
            deactivating = old.active and serializer.validated_data.get('active') is False
            if (changed_threshold or reactivating) and not level_changed and not deactivating:
                _save_disabled_vip_tier(serializer, self.request, action='change')
                from coupons.notifier import notify_coupon_catalog_changed
                transaction.on_commit(notify_vip_config_changed)
                transaction.on_commit(notify_coupon_catalog_changed)
                return
        if (not _vip_recalculation_available()
                and not serializer.instance.active and serializer.validated_data.get('active') is True):
            _require_vip_recalculation_or_empty_setup(self._empty_vip_setup_scope)
        if (not _vip_recalculation_available()
                and serializer.validated_data.get('level', serializer.instance.level) != serializer.instance.level):
            raise serializers.ValidationError('等级顺序重算尚未开放')
        if _vip_recalculation_available():
            reactivating = not serializer.instance.active and serializer.validated_data.get('active') is True
            renumbering = serializer.validated_data.get('level', serializer.instance.level) != serializer.instance.level
            if renumbering:
                if self._recalculation_locked_accounts is None:
                    raise serializers.ValidationError('等级顺序已变化，请重新核对')
                if any(field != 'level' and getattr(serializer.instance, field) != value
                       for field, value in serializer.validated_data.items()):
                    raise serializers.ValidationError('调整等级顺序时请先单独保存其他修改')
                from .vip_threshold_change import apply
                apply(serializer, self.request.data.get('recalculation_token'), self.request,
                      self._recalculation_locked_accounts, renumber=True)
                transaction.on_commit(notify_vip_config_changed)
                return
            if reactivating:
                if self._recalculation_locked_accounts is None:
                    raise serializers.ValidationError('等级状态已变化，请重新核对')
                for field, value in serializer.validated_data.items():
                    prior = getattr(serializer.instance, field)
                    if field in ('safe_box_service_ids', 'companion_service_ids'):
                        prior, value = prior or [], value or []
                    if field != 'active' and prior != value:
                        raise serializers.ValidationError('启用等级时请先单独保存其他修改，再核对重算影响')
                from .vip_threshold_change import apply
                apply(serializer, self.request.data.get('recalculation_token'), self.request,
                      self._recalculation_locked_accounts, reactivate=True)
                from coupons.notifier import notify_coupon_catalog_changed
                transaction.on_commit(notify_vip_config_changed)
                transaction.on_commit(notify_coupon_catalog_changed)
                return
        threshold = serializer.validated_data.get('recharge_threshold', serializer.instance.recharge_threshold)
        if not _vip_recalculation_available() and threshold != serializer.instance.recharge_threshold:
            raise serializers.ValidationError('等级门槛重算尚未开放')
        if _vip_recalculation_available() and threshold != serializer.instance.recharge_threshold:
            if self._recalculation_locked_accounts is None:
                raise serializers.ValidationError('门槛已变化，请重新核对')
            for field, value in serializer.validated_data.items():
                prior = getattr(serializer.instance, field)
                if field in ('safe_box_service_ids', 'companion_service_ids'):
                    prior, value = prior or [], value or []
                if field != 'recharge_threshold' and prior != value:
                    raise serializers.ValidationError('调整门槛时请先单独保存其他修改，再核对重算影响')
            from .vip_threshold_change import apply
            tier = apply(serializer, self.request.data.get('recalculation_token'), self.request,
                         self._recalculation_locked_accounts)
            transaction.on_commit(notify_vip_config_changed)
            return
        if serializer.instance.active and serializer.validated_data.get('active') is False:
            if not transition_guard_available():
                raise serializers.ValidationError('本店会员权益保护尚未就绪，暂不能停用等级')
            for field, value in serializer.validated_data.items():
                if field != 'active' and getattr(serializer.instance, field) != value:
                    raise serializers.ValidationError('停用等级时请先单独保存其他修改，再重新核对停用影响')
            from .tier_retirement import retire_tier
            retire_tier(serializer.instance, self.request.data.get('retirement_token'), self.request,
                        locked_account_ids=self._recalculation_locked_accounts)
        tier = serializer.save()
        # Keep the user's coupon list and the locked payment quote on the
        # same current terms. Quantity changes remain next-period decisions.
        if (previous[1], previous[2], previous[4], previous[5]) != (
                tier.safe_box_service_ids, tier.monthly_discount, tier.season_discount, tier.benefits):
            from stores.platform_setup_vip_coupon_terms import sync_tier_terms
            sync_tier_terms(tier)
        transaction.on_commit(notify_vip_config_changed)
        if previous != (tier.active, tier.safe_box_service_ids, tier.monthly_discount,
                        tier.monthly_coupon_count, tier.season_discount, tier.benefits):
            from coupons.notifier import notify_coupon_catalog_changed
            transaction.on_commit(notify_coupon_catalog_changed)

    def perform_destroy(self, instance):
        raise serializers.ValidationError('VIP 等级须保留历史记录，请使用停用流程')

    @action(detail=True, methods=['get'], url_path='retirement-preview')
    def retirement_preview(self, request, pk=None):
        from .vip_transition import resume_guard_available as transition_guard_available
        if not transition_guard_available():
            raise serializers.ValidationError('本店会员权益规则不可用，请先联系平台核对')
        from .tier_retirement import preview_retirement
        return Response({'code': 0, 'data': preview_retirement(self.get_object())})

    @action(detail=False, methods=['post'], url_path='creation-preview')
    def creation_preview(self, request):
        from django.conf import settings
        from .models import ClubOperationConfig
        from .vip_transition import resume_guard_available as transition_guard_available

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data.get('active', True):
            raise serializers.ValidationError('只有启用中的新等级需要重算核对')
        operation = ClubOperationConfig.objects.filter(pk=1).first()
        if operation is None:
            raise serializers.ValidationError('本店会员配置尚未初始化')
        if not _vip_recalculation_available() or not transition_guard_available():
            return Response({'code': 0, 'data': {'executable': False, 'notice': '新增等级重算尚未就绪'}})
        from .vip_threshold_change import preview_creation
        result = preview_creation(serializer.validated_data)
        result.update(executable=True, deferred=not operation.vip_enabled,
                      notice='VIP 停用中，新增配置将在重新启用时统一重算。' if not operation.vip_enabled
                             else '新增后立即重算本店会员并按新达等级发放权益。')
        return Response({'code': 0, 'data': result})

    @action(detail=False, methods=['post'], url_path='reorder-preview')
    def reorder_preview(self, request):
        from django.conf import settings
        from .vip_transition import resume_guard_available as transition_guard_available
        if not _vip_recalculation_available() or not transition_guard_available():
            raise serializers.ValidationError('等级顺序重算尚未开放')
        from .vip_reorder import preview
        return Response({'code': 0, 'data': preview(request.data.get('mapping'))})

    @action(detail=False, methods=['post'], url_path='reorder')
    @transaction.atomic
    def reorder(self, request):
        from django.conf import settings
        from .vip_transition import resume_guard_available as transition_guard_available
        if not _vip_recalculation_available() or not transition_guard_available():
            raise serializers.ValidationError('等级顺序重算尚未开放')
        from club_accounts.license_quota import lock_category
        lock_category('BOSS')
        from .vip_threshold_change import lock_all_boss_wallets
        accounts = lock_all_boss_wallets()
        self._lock_vip_config()
        from .vip_reorder import apply
        result = apply(request.data.get('mapping'), request.data.get('recalculation_token'), request, accounts)
        from .notifier import notify_vip_config_changed
        transaction.on_commit(notify_vip_config_changed)
        return Response({'code': 0, 'data': result})

    @action(detail=True, methods=['post'], url_path='recalculation-preview')
    def recalculation_preview(self, request, pk=None):
        """Advisory impact only; it does not authorize or apply the change."""
        from collections.abc import Mapping
        from dataclasses import replace
        from .models import ClubOperationConfig
        from .vip_recalculation_plan import plan_changes, read_store_snapshot
        from .vip_transition import resume_guard_available as transition_guard_available

        if not isinstance(request.data, Mapping):
            raise serializers.ValidationError('请提供等级配置对象')
        if set(request.data) - {'consumption_threshold', 'level', 'active'}:
            raise serializers.ValidationError('只能预览等级、消费门槛或启用状态')
        tier = self.get_object()
        serializer = self.get_serializer(tier, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        operation = ClubOperationConfig.objects.filter(pk=1).first()
        if operation is None:
            raise serializers.ValidationError('本店会员配置尚未初始化')
        before, bosses = read_store_snapshot()
        changes = serializer.validated_data
        after = tuple(replace(rule, level=changes.get('level', rule.level),
                              threshold=changes.get('recharge_threshold', rule.threshold),
                              active=changes.get('active', rule.active)) if rule.tier_id == tier.pk else rule
                      for rule in before)
        try:
            plan = plan_changes(before, after, bosses)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from None
        upgraded = sum(item.new_level > item.old_level for item in plan)
        downgraded = sum(item.new_level < item.old_level for item in plan)
        notice = ('VIP 已停用；仅展示当前单次修改的影响，重新启用时仍需核对停用前快照和历史发放记录。'
                  if not operation.vip_enabled else '仅供核对，历史权益发放及配置并发仍须核实；本预览不能执行等级重算。')
        result = {'account_count': len(bosses), 'affected_count': len(plan),
            'upgrade_count': upgraded, 'downgrade_count': downgraded,
            'candidate_upgrade_rewards': sum(len(item.newly_eligible_tier_ids) for item in plan),
            'vip_disabled': not operation.vip_enabled, 'executable': False,
            'notice': notice}
        from django.conf import settings
        if (_vip_recalculation_available() and transition_guard_available() and
                set(request.data) == {'consumption_threshold'} and
                changes.get('recharge_threshold') != tier.recharge_threshold):
            from .vip_threshold_change import preview
            result.update(preview(tier, changes['recharge_threshold']))
            result.update(executable=True, deferred=not operation.vip_enabled,
                          notice='VIP 停用中，门槛变更将在重新启用时统一重算。' if not operation.vip_enabled
                                 else '保存后立即重算本店会员并按新达等级发放权益。')
        elif (_vip_recalculation_available() and transition_guard_available() and
              set(request.data) == {'level'} and changes.get('level') != tier.level):
            from .vip_threshold_change import preview
            result.update(preview(tier, tier.recharge_threshold, new_level=changes['level']))
            result.update(executable=True, deferred=not operation.vip_enabled,
                          notice='VIP 停用中，等级顺序将在重新启用时统一重算。' if not operation.vip_enabled
                                 else '保存后立即按新顺序重算本店会员。')
        elif (_vip_recalculation_available() and transition_guard_available() and
              set(request.data) == {'active'} and changes.get('active') is True and not tier.active):
            if not tier.boss_type.is_active:
                raise serializers.ValidationError('请先启用该等级关联的老板分级')
            from .vip_threshold_change import preview
            result.update(preview(tier, tier.recharge_threshold, reactivate=True))
            result.update(executable=True, deferred=not operation.vip_enabled,
                          notice='VIP 停用中，等级变更将在重新启用时统一重算。' if not operation.vip_enabled
                                 else '启用后立即重算本店会员并按新达等级发放权益。')
        return Response({'code': 0, 'data': result})

    @action(detail=False, methods=['get'], url_path='service-options')
    def service_options(self, request):
        from django.db.models import Q
        from orders.models import ServiceItem
        kind = request.query_params.get('benefit_kind', '')
        if kind and kind not in ('COMPANION_HOUR', 'SAFEBOX_33'):
            return Response({'code': 400, 'msg': '无效的权益分类'}, status=400)
        if kind:
            from .serializers import eligible_benefit_services
            rows = eligible_benefit_services(kind)
        else:
            rows = ServiceItem.objects.filter(is_active=True, is_addon=False, purchase_mode='DIRECT').exclude(service_category__is_gift=True)
        rows = rows.select_related('game_category').order_by('id')
        eligible_rows = rows
        keyword = request.query_params.get('keyword', '').strip()[:100]
        if keyword:
            match = Q(name__icontains=keyword) | Q(catalog_family__icontains=keyword) | Q(game_category__name__icontains=keyword)
            if keyword.isascii() and keyword.isdigit() and len(keyword) <= 10:
                match |= Q(pk=int(keyword))
            rows = rows.filter(match)
        selected = request.query_params.get('selected_ids', '').strip()
        selected_ids = selected.split(',') if selected else []
        if len(selected_ids) > 100 or any(not value.isascii() or not value.isdigit() or int(value) < 1 for value in selected_ids):
            return Response({'code': 400, 'msg': '无效的已选套餐编号'}, status=400)
        # Keep eligible pre-existing selections visible even when they fall beyond
        # the initial 500 results; missing selections are genuinely ineligible.
        first = list(rows[:500])
        present = {row.pk for row in first}
        extra = list(eligible_rows.filter(pk__in=[int(value) for value in selected_ids]).exclude(pk__in=present)) if selected_ids else []
        return Response({'code': 0, 'data': [{'id': row.pk, 'name': row.name,
            'game': row.game_category.name if row.game_category else '', 'price': row.price,
            'unit': row.price_unit} for row in first + extra]})


class PackageViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = RechargePackage.objects.all()
    serializer_class = PackageSerializer
    default_perm = 'engagement:view'
    required_perms = {key: 'engagement:edit' for key in ['create', 'update', 'partial_update', 'destroy']}

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        from .recharge_pricing import lock_recharge_packages
        lock_recharge_packages()
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        from .recharge_pricing import lock_recharge_packages
        lock_recharge_packages()
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        from .recharge_pricing import lock_recharge_packages
        lock_recharge_packages()
        return super().destroy(request, *args, **kwargs)


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
        old_pity_limit = campaign.pity_limit
        from .services import lottery_pity_target
        old_pity_target = lottery_pity_target(campaign, list(campaign.prizes.select_related('reward')))
        version = serializers.IntegerField(min_value=1).run_validation(request.data.get('version'))
        if version != campaign.version:
            raise serializers.ValidationError('配置已被其他人员修改，请刷新后重试')
        data = dict(request.data)
        serializer = CampaignSerializer(campaign, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get('enabled', campaign.enabled):
            _require_feature('LOTTERY', '当前门店授权不支持启用或修改抽奖活动')
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
        new_pity_target = lottery_pity_target(campaign, list(campaign.prizes.select_related('reward')))
        limit_changed = campaign.pity_limit != old_pity_limit
        if limit_changed:
            from .models import LotteryPityState
            LotteryPityState.objects.filter(campaign=campaign).update(count=0)
        if limit_changed or (old_pity_target is None and new_pity_target is not None):
            campaign.pity_generation += 1
            campaign.save(update_fields=['pity_generation'])
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
        from .vip_transition import resume_guard_available
        data = dict(ConfigSerializer(config()).data)
        available = resume_guard_available()
        data['vip_coupon_protection_enabled'] = available
        data['vip_threshold_recalculation_enabled'] = available
        data['vip_resume_recalculation_available'] = available
        data['vip_coupon_terms_config_enabled'] = available
        # The retained shutdown baseline exists independently of whether the
        # recalculation rollout is currently enabled. Never hide pending work.
        from .models import VipDeferredRecalculation
        deferred = VipDeferredRecalculation.objects.filter(pk=1, is_open=True).first()
        data['vip_deferred'] = {'pending': deferred.pending, 'baseline_known': deferred.baseline_rules is not None,
                                'epoch': deferred.epoch} if deferred else None
        return Response({'code': 0, 'data': data})
    def put(self, request):
        from django.conf import settings
        from .models import ClubOperationConfig
        from .vip_transition import freeze_existing_vip_terms, transition_guard_available, transition_guard_enabled
        with transaction.atomic():
            locked_accounts = None
            if request.data.get('vip_enabled') is True:
                # Account admission and retirement take this lock before a
                # boss wallet, so the resume population cannot change between
                # wallet acquisition and the final locked snapshot.
                from club_accounts.license_quota import lock_category
                lock_category('BOSS')
                if ClubOperationConfig.objects.filter(pk=1, vip_enabled=False).exists():
                    from .vip_threshold_change import lock_all_boss_wallets
                    locked_accounts = lock_all_boss_wallets()
            config()
            current = ClubOperationConfig.objects.select_for_update().get(pk=1)
            serializer = ConfigSerializer(current, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            if not current.vip_enabled and serializer.validated_data.get('vip_enabled') is True:
                if locked_accounts is None:
                    raise serializers.ValidationError('VIP 状态已变化，请重新核对')
                from .vip_transition import resume_guard_available
                if not resume_guard_available():
                    raise serializers.ValidationError('本店会员权益规则尚不可用，请联系平台核对')
                from .vip_threshold_change import apply_resume
                result = apply_resume(serializer, request.data.get('vip_resume_token'), request, locked_accounts)
                from .notifier import notify_vip_config_changed
                from coupons.notifier import notify_coupon_catalog_changed
                transaction.on_commit(notify_vip_config_changed)
                transaction.on_commit(notify_coupon_catalog_changed)
                return Response({'code': 0, 'data': {**serializer.data, 'vip_resume': result}})
            if current.vip_enabled and serializer.validated_data.get('vip_enabled') is False:
                # Existing coupons use dynamic terms even when the optional
                # transition switch is off. Materialize those exact terms
                # under the same config and coupon locks before switching off.
                if transition_guard_enabled() and not transition_guard_available():
                    raise serializers.ValidationError('本店月券规则不可用，请先联系平台核对')
                result = freeze_existing_vip_terms()
                from console.models import AdminAuditLog
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=request.account.username[:150], method='PUT', path=request.path[:255],
                    resource='vip-terms-disable', object_id='1', request_body=result, status_code=200)
            if current.vip_enabled and serializer.validated_data.get('vip_enabled') is False:
                if getattr(settings, 'VIP_THRESHOLD_RECALC_ENABLED', False) and not transition_guard_available():
                    raise serializers.ValidationError('本店会员权益保护尚未就绪')
                from .vip_deferred import capture_disable_baseline, disable_evidence
                from console.models import AdminAuditLog
                baseline = capture_disable_baseline()
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=request.account.username[:150], method='PUT', path=request.path[:255],
                    resource='vip-deferred-begin', object_id=str(baseline.epoch),
                    request_body=disable_evidence(baseline), status_code=200)
            serializer.save()
        return Response({'code': 0, 'data': serializer.data})


class VipUpgradeReviewViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    from .models import VipUpgradeReview
    queryset = VipUpgradeReview.objects.select_related('account', 'tier__boss_type').order_by('-created_at', '-pk')
    serializer_class = VipUpgradeReviewSerializer
    default_perm = 'engagement:view'
    required_perms = {'resolve': 'engagement:edit'}

    def get_queryset(self):
        rows = super().get_queryset()
        status = self.request.query_params.get('status')
        if status in ('PENDING', 'RESOLVED'):
            rows = rows.filter(status=status)
        return rows

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def resolve(self, request, pk=None):
        from console.models import AdminAuditLog
        from wallet.services import get_wallet
        from .models import ClubOperationConfig, VipUpgradeReview
        from .vip_threshold_change import _issue_new_rewards

        decision = request.data.get('decision')
        note = request.data.get('note')
        if decision not in ('ALREADY_ISSUED', 'ISSUE') or not isinstance(note, str) or not 2 <= len(note.strip()) <= 500:
            raise serializers.ValidationError('请选择核对结果并填写 2 至 500 字依据')
        account_id = self.get_queryset().filter(pk=pk).values_list('account_id', flat=True).first()
        if account_id is None:
            from django.http import Http404
            raise Http404
        get_wallet(account_id=account_id, for_update=True)
        config()
        operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
        account = ClubAccount.objects.select_for_update().get(pk=account_id)
        from .vip_threshold_change import _lock_inputs
        _lock_inputs()
        review = VipUpgradeReview.objects.select_for_update().get(pk=pk, account_id=account_id)
        review.account = account
        result = 'ISSUED' if decision == 'ISSUE' else 'ALREADY_ISSUED'
        if review.status == 'RESOLVED':
            if review.resolution == result and review.resolution_note == note.strip():
                return Response({'code': 0, 'data': self.get_serializer(review).data})
            raise serializers.ValidationError('该记录已核对，不能重复处理')
        if decision == 'ISSUE':
            if not operation.vip_enabled or not review.tier.active or not review.tier.boss_type.is_active:
                raise serializers.ValidationError('VIP 或对应等级未启用，暂不能补发')
            if (account.account_type != 'BOSS' or not account.license_membership_active
                    or account.business_cycle != review.business_cycle):
                raise serializers.ValidationError('老板已退店或业务周期已变化，不能向新周期补发历史权益')
            from .models import MemberBenefitGrant
            if MemberBenefitGrant.objects.filter(account_id=review.account_id, tier_id=review.tier_id,
                    event_key__startswith=f'vip:{review.account_id}:{review.business_cycle}:', kind='UPGRADE').exists():
                raise serializers.ValidationError('已发现该等级的发放记录，请核对后选择确认已发')
            _issue_new_rewards(account, (review.tier_id,), exclude_review_id=review.pk)
        if 'quota_floors' not in review.evidence:
            from .vip_review_guard import quota_floors_for_tier
            review.evidence = {**review.evidence, 'quota_floors': quota_floors_for_tier(review.tier)}
        review.status = 'RESOLVED'
        review.resolution = result
        review.resolution_note = note.strip()
        review.resolved_by = request.account
        review.resolved_at = timezone.now()
        review.save(update_fields=['status', 'resolution', 'resolution_note', 'resolved_by', 'resolved_at', 'evidence'])
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=request.account.username[:150], method=request.method, path=request.path[:255],
            resource='vip-upgrade-review', object_id=str(review.pk), status_code=200,
            request_body={'account_id': review.account_id, 'tier_id': review.tier_id,
                          'business_cycle': review.business_cycle, 'decision': result, 'note': note.strip()})
        return Response({'code': 0, 'data': self.get_serializer(review).data})


class VipResumePreviewView(APIView):
    permission_classes = [HasConsolePerm]
    default_perm = 'engagement:edit'

    def get(self, request):
        from .vip_transition import resume_guard_available
        if not resume_guard_available():
            raise serializers.ValidationError('本店会员权益规则尚不可用，请联系平台核对')
        from .vip_threshold_change import preview_resume
        return Response({'code': 0, 'data': preview_resume()})


class ExternalPointsView(APIView):
    permission_classes = [HasConsolePerm]
    default_perm = 'engagement:points'

    def get(self, request):
        from .models import KookManualOnlineReview
        from console.pagination import ConsolePagination
        rows = KookManualOnlineReview.objects.filter(status='PENDING_REVIEW').order_by('created_at', 'pk')
        page = ConsolePagination()
        items = page.paginate_queryset(rows, request, view=self)
        return page.get_paginated_response([{'id': row.pk, 'account_id': row.account_id,
            'business_cycle': row.business_cycle, 'source_id': row.source_id,
            'started_at': row.started_at, 'ended_at': row.ended_at,
            'online_seconds': row.online_seconds, 'status': row.status,
            'reason': row.reason, 'created_at': row.created_at} for row in items])

    def post(self, request):
        account_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('account_id'))
        account = get_object_or_404(ClubAccount, pk=account_id, account_type='BOSS', is_active=True)
        kind = serializers.ChoiceField(choices=['KOOK_ONLINE', 'KOOK_CHECKIN']).run_validation(request.data.get('kind'))
        day = serializers.DateField().run_validation(request.data.get('day'))
        units = serializers.IntegerField(min_value=1, max_value=86400).run_validation(request.data.get('online_seconds', 1))
        source_id = serializers.CharField(max_length=100).run_validation(request.data.get('source_id'))
        observed_at = request.data.get('observed_at')
        if observed_at is not None:
            observed_at = serializers.DateTimeField().run_validation(observed_at)
        started_at, ended_at = request.data.get('observed_start'), request.data.get('observed_end')
        if started_at is not None:
            started_at = serializers.DateTimeField().run_validation(started_at)
        if ended_at is not None:
            ended_at = serializers.DateTimeField().run_validation(ended_at)
        result = record_external_points(account, kind=kind, day=day, units=units, source_id=source_id,
                                        operator=request.account, observed_at=observed_at,
                                        observed_start=started_at, observed_end=ended_at)
        from .models import KookManualOnlineReview
        if isinstance(result, KookManualOnlineReview):
            pending = result.status == 'PENDING_REVIEW'
            message = ('已保存待核对，尚未发放积分' if pending else
                       '该来源已裁决' if result.resolution_outcome else '该来源已登记')
            return Response({'code': 0, 'msg': message,
                'data': {'review_id': result.pk, 'status': result.status,
                         'points': result.resolution_points if result.resolution_outcome == 'ALLOW' else None}},
                status=202 if pending else 200)
        return Response({'code': 0, 'data': {'id': result.pk, 'points': result.amount / 100}})


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
