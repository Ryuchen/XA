from django.db import IntegrityError
from stores.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from club_accounts.models import ClubAccount, LegacyAccountMap
from common.compliance import (
    PROVIDER_DISPLAY_NAME, check_content, record_content_receipt,
)
from common.media import build_media_url
from console.mixins import EnvelopeViewSetMixin
from stores.closure import closure_refusal_response
from stores.context import current_store
from users.models import CustomUser, EscortLevel, EscortProfile, ProviderRegistration
from users.provider_registration import decrypt_identity
from wallet.models import CashDepositTopup, Wallet, Transaction, get_platform_wallet


class RegistrationAdminSerializer(EntityNumberModelSerializer):
    id_number_masked = serializers.SerializerMethodField()
    id_number = serializers.SerializerMethodField()
    id_front_url = serializers.SerializerMethodField()
    id_back_url = serializers.SerializerMethodField()
    deposit_proof_url = serializers.SerializerMethodField()
    reviewer_name = serializers.SerializerMethodField()
    level_name = serializers.CharField(source='level.name', default='')
    status_display = serializers.CharField(source='get_status_display')

    class Meta:
        model = ProviderRegistration
        fields = (
            'id', 'username', 'phone', 'real_name', 'display_name', 'id_number_masked', 'id_number',
            'id_front_url', 'id_back_url', 'deposit_proof_url', 'status', 'status_display',
            'deposit_mode', 'deposit_required', 'deposit_paid', 'revision', 'level', 'level_name', 'account',
            'review_note', 'reviewer_name', 'reviewed_at', 'submitted_at',
        )

    def detail(self):
        return getattr(self.context.get('view'), 'action', None) == 'retrieve'

    def get_id_number_masked(self, obj):
        return '*' * 14 + obj.id_number_last4

    def get_id_number(self, obj):
        return decrypt_identity(obj.id_number_ciphertext) if self.detail() else ''

    def image(self, obj, field):
        return build_media_url(self.context.get('request'), getattr(obj, field)) if self.detail() else ''

    def get_id_front_url(self, obj):
        return self.image(obj, 'id_front')

    def get_id_back_url(self, obj):
        return self.image(obj, 'id_back')

    def get_deposit_proof_url(self, obj):
        return self.image(obj, 'deposit_proof')

    def get_reviewer_name(self, obj):
        return (obj.reviewer.nickname or obj.reviewer.username) if obj.reviewer_id else ''


class RegistrationReviewInput(serializers.Serializer):
    revision = serializers.IntegerField(min_value=1)
    review_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


class RegistrationApproveInput(RegistrationReviewInput):
    level_id = serializers.PrimaryKeyRelatedField(queryset=EscortLevel.objects.filter(is_active=True))
    identity_confirmed = serializers.BooleanField()
    deposit_confirmed = serializers.BooleanField(default=False)
    deposit_paid = serializers.IntegerField(min_value=0, max_value=2_147_483_647, default=0)

    def validate(self, attrs):
        if not attrs['identity_confirmed']:
            raise serializers.ValidationError('请完成实名资料核对')
        return attrs


class RegistrationRejectInput(RegistrationReviewInput):
    review_note = serializers.CharField(max_length=500, allow_blank=False)


class ProviderRegistrationViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = RegistrationAdminSerializer
    default_perm = 'registration:view'
    required_perms = {
        'list': 'registration:view', 'retrieve': 'registration:view',
        'approve': ['registration:view', 'registration:audit'], 'reject': ['registration:view', 'registration:audit'],
    }
    filterset_fields = ['status']
    search_fields = ['username', 'phone', 'real_name', 'display_name']
    ordering_fields = ['submitted_at', 'id']

    def get_queryset(self):
        queryset = ProviderRegistration.objects.select_related('reviewer', 'level', 'account').order_by('-submitted_at', '-id')
        if self.action == 'list':
            status = (self.request.query_params.get('status') or '').strip()
            search = (self.request.query_params.get('search') or '').strip()
            if status:
                if status not in ProviderRegistration.Status.values:
                    raise serializers.ValidationError({'status': '无效的审核状态'})
                queryset = queryset.filter(status=status)
            if search:
                queryset = queryset.filter(Q(username__icontains=search) | Q(phone__icontains=search)
                                           | Q(real_name__icontains=search) | Q(display_name__icontains=search))
        return queryset

    def _review(self, request, approve):
        serializer = RegistrationApproveInput(data=request.data) if approve else RegistrationRejectInput(data=request.data)
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': '请检查审核信息', 'data': serializer.errors}, status=400)
        data = serializer.validated_data
        try:
            with transaction.atomic():
                row = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
                if row.revision != data['revision']:
                    return Response({'code': 409, 'msg': '申请资料已更新，请重新打开后审核'}, status=409)
                if row.status == 'APPROVED' and approve:
                    return Response({'code': 0, 'data': {'id': row.pk, 'account_id': row.account_id, 'status': row.status}})
                if row.status != 'PENDING':
                    return Response({'code': 409, 'msg': '该申请已处理，请刷新列表'}, status=409)
                if approve:
                    if row.deposit_mode == 'CASH' and (not data['deposit_confirmed'] or data['deposit_paid'] < row.deposit_required):
                        return Response({'code': 400, 'msg': '请核实现金押金实际到账，金额不得低于应缴金额'}, status=400)
                    if row.deposit_mode == 'EARNINGS' and (data['deposit_paid'] != 0 or data['deposit_confirmed']):
                        return Response({'code': 400, 'msg': '单抵方式由后续净收益抵扣，本次不得登记现金押金'}, status=400)
                    if (ClubAccount.objects.filter(username__iexact=row.username).exists()
                            or CustomUser.objects.filter(username__iexact=row.username).exists()):
                        return Response({'code': 409, 'msg': '登录账号已被占用，请联系管理员处理账号冲突'}, status=409)
                    # Submission only checked PROVIDER_REGISTRATION_DISPLAY_NAME;
                    # approval must validate the profile name against the current rules.
                    display_name_result = check_content(PROVIDER_DISPLAY_NAME, row.display_name)
                    # The original APPROVED response above is a read-back, not a
                    # new account. Every genuinely new approval rechecks closure
                    # after the registration lock and all review validations.
                    denied = closure_refusal_response(current_store())
                    if denied is not None:
                        if denied.status_code == 409:
                            denied.data['msg'] = '本店处于闭店公示或待结清阶段，暂不能审核新建陪玩账号'
                        return denied
                    account = ClubAccount.objects.create(
                        username=row.username, password=row.password_hash, account_type='PROVIDER',
                        nickname=row.display_name, real_name=row.real_name, phone=row.phone,
                    )
                    user = CustomUser.objects.create(
                        username=row.username, password=row.password_hash, role='PROVIDER',
                        nickname=row.display_name, real_name=row.real_name, phone=row.phone,
                        boss_no=account.account_no,
                    )
                    LegacyAccountMap.objects.create(account=account, legacy_user_id=user.pk, legacy_role='PROVIDER')
                    wallet, _ = Wallet.objects.get_or_create(user=user)
                    wallet.account = account
                    wallet.save(update_fields=['account'])
                    profile = EscortProfile.objects.create(
                        account=account, user=user, display_name=row.display_name, level=data['level_id'],
                        is_verified=True, deposit_mode=row.deposit_mode,
                        deposit_required=row.deposit_required, deposit_paid=data['deposit_paid'],
                        deposit_business_cycle=account.business_cycle if data['deposit_paid'] else None,
                        status='OFFLINE',
                    )
                    if display_name_result is not None:
                        record_content_receipt(
                            'escort_profile', profile.pk, PROVIDER_DISPLAY_NAME, display_name_result,
                            actor_account=request.account,
                        )
                    if row.deposit_mode == 'CASH':
                        # External cash goes directly to the store deposit pool;
                        # it is never a provider wallet recharge.
                        amount = data['deposit_paid']
                        platform = get_platform_wallet(for_update=True)
                        before = platform.balance
                        if before + amount > 2147483647:
                            transaction.set_rollback(True)
                            return Response({'code': 400, 'msg': '门店押金账户余额超过系统范围'}, status=400)
                        platform.balance += amount
                        platform.save(update_fields=['balance'])
                        platform_tx = Transaction.objects.create(
                            wallet=platform, amount=amount, tx_type='DEPOSIT_INCOME',
                            balance_before=before, balance_after=platform.balance, operator_account=request.account,
                            remark=f'注册现金押金收款 #{row.pk}',
                        )
                        deposit_topup = CashDepositTopup.objects.create(profile=profile, account=account, operator=request.account,
                            account_business_cycle=account.business_cycle,
                            kind=CashDepositTopup.Kind.INITIAL, source_registration=row,
                            amount=amount, paid_before=0, paid_after=amount, target_snapshot=row.deposit_required,
                            reason='入驻审核核验现金押金', client_request_id=f'registration-{row.pk}',
                            platform_transaction=platform_tx)
                        from site_messages.deposit_event_notice import record_topup
                        record_topup(deposit_topup)
                    row.account = account
                    row.level = data['level_id']
                    row.deposit_paid = data['deposit_paid']
                    row.status = 'APPROVED'
                else:
                    row.status = 'REJECTED'
                row.review_note = data['review_note']
                row.reviewer = request.account
                row.reviewed_at = timezone.now()
                row.save()
        except IntegrityError:
            return Response({'code': 409, 'msg': '申请或账号已被其他操作处理，请刷新后重试'}, status=409)
        return Response({'code': 0, 'data': {'id': row.pk, 'account_id': row.account_id, 'status': row.status}})

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._review(request, True)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._review(request, False)
