from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from datetime import timedelta
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from announcements.models import Announcement
from audition.models import AuditionLink, AuditionSignup
from banners.models import Banner
from chat.models import ChatMessage, ChatSession
from chat.notifier import notify_chat_message, notify_chat_session_update
from coupons.models import Coupon, UserCoupon
from orders.models import (
    Evaluation,
    GameCategory,
    KookDispatchRecord,
    Order,
    OrderProvider,
    OrderStatusLog,
    ServiceCategory,
    ServiceItem,
)
from orders.ratings import rollback_escort_rating
from orders.settlement import compute_split
from orders.state_machine import IllegalTransitionError, log_only, transition
from orders.views import (
    PENDING_TIMEOUT_MINUTES,
    _push_message_safe,
    _schedule_auto_cancel,
)
from orders.notifier import notify_order_update
from orders.kook_dispatch import enqueue_kook_dispatch
from promotions.models import Promotion
from site_messages.models import Message
from site_messages.utils import create_message
from support.models import SupportContactCard
from users.models import (
    Achievement, BossType, CheckinGift, CheckinMonthProgress, CheckinRuleConfig,
    EscortLevel, EscortProfile, EscortSchedule,
)
from wallet.models import (
    COMMISSION_RATE_KEY,
    MIN_WITHDRAW_AMOUNT_KEY,
    DisposeRecord,
    ProviderReport,
    RechargeRecord,
    SystemConfig,
    Transaction,
    Wallet,
    WithdrawRequest,
    get_commission_rate,
    get_min_withdraw_amount,
    get_platform_wallet,
)

from .mixins import EnvelopeViewSetMixin
from .models import AdminMembership, AdminRole
from .permissions import (
    PERMISSION_GROUPS,
    IsConsoleUser,
    get_user_permissions,
)
from .serializers import (
    AdminAchievementSerializer,
    AdminAnnouncementSerializer,
    AdminAuditionLinkSerializer,
    AdminAuditionSignupSerializer,
    AdminBannerSerializer,
    AdminBossTypeSerializer,
    AdminChatMessageSerializer,
    AdminChatSessionSerializer,
    AdminCheckinGiftSerializer,
    AdminCheckinProgressSerializer,
    AdminCheckinRuleSerializer,
    AdminCouponSerializer,
    AdminCreateEscortSerializer,
    AdminCreateMembershipSerializer,
    AdminDisposeRecordSerializer,
    AdminEscortLevelSerializer,
    AdminEscortSerializer,
    AdminEvaluationSerializer,
    AdminGameCategorySerializer,
    AdminMembershipSerializer,
    AdminMessageSerializer,
    AdminOrderLogSerializer,
    AdminOrderSerializer,
    AdminProviderReportSerializer,
    AdminPromotionSerializer,
    AdminRechargeRecordSerializer,
    AdminRoleSerializer,
    AdminServiceItemSerializer,
    AdminServiceCategorySerializer,
    AdminSupportCardSerializer,
    AdminTransactionSerializer,
    AdminUserCouponSerializer,
    AdminUserSerializer,
    AdminWalletSerializer,
    AdminWithdrawSerializer,
)

User = get_user_model()


def _truthy(value):
    return str(value).lower() in ('1', 'true', 'yes')


def _resolve_provider_report_commission(provider):
    """解析报单审核抽成率：陪玩等级优先，否则回退全局配置。"""
    profile = EscortProfile.objects.filter(user=provider).select_related('level').first()
    level = getattr(profile, 'level', None)
    if level is not None and level.is_active:
        return level.commission_rate, f'等级抽成（{level.name}）'
    return get_commission_rate(), '全局默认'


# ---------------- 仪表盘 ----------------
class DashboardView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        orders = Order.objects.all()
        paid_income = (
            Transaction.objects.filter(tx_type=Transaction.TxType.PAY)
            .aggregate(total=Sum('amount'))['total'] or 0
        )
        today = timezone.localdate()
        today_orders = orders.filter(created_at__date=today)

        status_rows = orders.values('status').annotate(count=Count('id'))
        status_map = {row['status']: row['count'] for row in status_rows}

        return Response({
            'code': 0,
            'data': {
                'user_total': User.objects.count(),
                'customer_total': User.objects.filter(role=User.Role.CUSTOMER).count(),
                'provider_total': User.objects.filter(role=User.Role.PROVIDER).count(),
                'order_total': orders.count(),
                'today_order_total': today_orders.count(),
                'order_amount_total': orders.aggregate(total=Sum('amount'))['total'] or 0,
                'paid_amount_total': abs(paid_income),
                'order_status': status_map,
                'recent_orders': AdminOrderSerializer(
                    orders.select_related('customer', 'provider').order_by('-created_at')[:10],
                    many=True,
                ).data,
            },
        })


# ---------------- 陪玩数据看板 ----------------
class PlayerDashboardView(APIView):
    """陪玩维度经营聚合看板。

    - KPI/排行榜：按「已支付」订单口径(payment_status=PAID)，支持日期区间筛选。
    - 二次 KPI（押金/罚款/奖励/钱包/冻结/已结算工资）：取全量当前快照，不受日期筛选影响。
    - 男陪/女陪：按陪玩档案 gender(MALE/FEMALE)拆分，UNKNOWN 计入总额但不进男女拆分。
    """

    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not request.user.is_superuser and \
                'player_dashboard:view' not in get_user_permissions(request.user):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)

        order_qs = Order.objects.filter(payment_status=Order.PaymentStatus.PAID)
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        if start_date:
            order_qs = order_qs.filter(created_at__date__gte=start_date)
        if end_date:
            order_qs = order_qs.filter(created_at__date__lte=end_date)

        return Response({
            'code': 0,
            'data': {
                'kpi': self._build_kpi(order_qs),
                'secondary': self._build_secondary(),
                'rank': self._build_rank(order_qs),
            },
        })

    @staticmethod
    def _build_kpi(order_qs):
        rows = order_qs.values(
            'provider__escort_profile__gender', 'service__service_category__is_gift',
        ).annotate(amount=Sum('amount'))

        Gender = EscortProfile.Gender
        kpi = {
            'total_amount': 0, 'game_amount': 0, 'gift_amount': 0,
            'male_game': 0, 'female_game': 0, 'male_gift': 0, 'female_gift': 0,
        }
        for row in rows:
            amount = row['amount'] or 0
            gender = row['provider__escort_profile__gender']
            is_gift = row['service__service_category__is_gift']
            kpi['total_amount'] += amount
            if is_gift:
                kpi['gift_amount'] += amount
                if gender == Gender.MALE:
                    kpi['male_gift'] += amount
                elif gender == Gender.FEMALE:
                    kpi['female_gift'] += amount
            else:
                kpi['game_amount'] += amount
                if gender == Gender.MALE:
                    kpi['male_game'] += amount
                elif gender == Gender.FEMALE:
                    kpi['female_game'] += amount
        return kpi

    @staticmethod
    def _build_secondary():
        escort_agg = EscortProfile.objects.aggregate(
            total_deposit=Sum('deposit_paid'),
            total_penalty=Sum('total_penalty'),
            total_reward=Sum('total_reward'),
        )
        wallet_agg = Wallet.objects.filter(
            user__role=User.Role.PROVIDER,
        ).aggregate(balance=Sum('balance'), frozen=Sum('frozen_amount'))
        balance = wallet_agg['balance'] or 0
        frozen = wallet_agg['frozen'] or 0
        settled = WithdrawRequest.objects.filter(
            status=WithdrawRequest.Status.APPROVED,
        ).aggregate(total=Sum('amount'))['total'] or 0
        return {
            'total_deposit': escort_agg['total_deposit'] or 0,
            'total_penalty': escort_agg['total_penalty'] or 0,
            'total_reward': escort_agg['total_reward'] or 0,
            'total_wallet': balance + frozen,
            'frozen_amount': frozen,
            'settled_salary': settled,
        }

    @staticmethod
    def _build_rank(order_qs):
        rows = list(order_qs.filter(provider__isnull=False).values(
            'provider',
            'provider__nickname', 'provider__username',
            'provider__escort_profile__escort_no',
            'provider__escort_profile__gender',
            'provider__escort_profile__display_name',
        ).annotate(amount=Sum('amount'), income=Sum('provider_income'), cnt=Count('id')))

        def item(row, value):
            return {
                'nickname': (row['provider__escort_profile__display_name']
                             or row['provider__nickname']
                             or row['provider__username']),
                'escort_no': row['provider__escort_profile__escort_no'] or '',
                'gender': row['provider__escort_profile__gender'] or EscortProfile.Gender.UNKNOWN,
                'value': value,
            }

        def top(metric):
            ranked = sorted(rows, key=lambda r: r[metric] or 0, reverse=True)[:10]
            return [item(r, r[metric] or 0) for r in ranked]

        return {
            'amount': top('amount'),
            'income': top('income'),
            'count': top('cnt'),
        }


# ---------------- 用户 ----------------
class UserViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminUserSerializer
    http_method_names = ['get', 'patch', 'put', 'head', 'options']
    default_perm = 'user:view'
    required_perms = {
        'update': 'user:edit',
        'partial_update': 'user:edit',
    }

    def get_queryset(self):
        # 老板信息管理：仅老板（CUSTOMER），不含陪玩/客服/管理员
        qs = (
            User.objects.filter(role=User.Role.CUSTOMER)
            .select_related('wallet', 'boss_type', 'inviter')
            .order_by('-date_joined')
        )
        # 编号 / 昵称 / 手机号 三个独立条件，相互 AND 叠加（支持输入即查提示）
        boss_no = self.request.query_params.get('boss_no')
        if boss_no:
            qs = qs.filter(boss_no__icontains=boss_no)
        nickname = self.request.query_params.get('nickname')
        if nickname:
            qs = qs.filter(nickname__icontains=nickname)
        phone = self.request.query_params.get('phone')
        if phone:
            qs = qs.filter(phone__icontains=phone)
        is_active = self.request.query_params.get('is_active')
        if is_active is not None and is_active != '':
            qs = qs.filter(is_active=_truthy(is_active))
        return qs


# ---------------- 陪玩 ----------------
class EscortViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminEscortSerializer
    http_method_names = ['get', 'patch', 'put', 'post', 'head', 'options']
    default_perm = 'escort:view'
    required_perms = {
        'create': 'escort:edit',
        'update': 'escort:edit',
        'partial_update': 'escort:edit',
        'verify': 'escort:verify',
        'dispose': 'escort:dispose',
    }

    def get_queryset(self):
        qs = EscortProfile.objects.select_related('user').order_by('-created_at')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        verified = self.request.query_params.get('is_verified')
        if verified is not None and verified != '':
            qs = qs.filter(is_verified=_truthy(verified))
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(display_name__icontains=keyword)
                | Q(user__username__icontains=keyword)
                | Q(user__phone__icontains=keyword)
            )
        return qs

    def create(self, request, *args, **kwargs):
        """客服后台为陪玩开户：建 PROVIDER 账号 + 陪玩档案（钱包由信号自动创建）。"""
        serializer = AdminCreateEscortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            user = User.objects.create_user(
                username=data['username'],
                password=data['password'],
                nickname=data.get('nickname', ''),
                phone=data.get('phone') or None,
                role=User.Role.PROVIDER,
                is_openid_bound=True,
            )
            profile = EscortProfile.objects.create(
                user=user,
                display_name=data['display_name'],
                gender=data.get('gender', EscortProfile.Gender.UNKNOWN),
                city=data.get('city', ''),
                escort_no=data.get('escort_no', ''),
                level=data.get('level'),
                deposit_required=data.get('deposit_required', 0),
                avatar=data.get('avatar'),
                intro_video=data.get('intro_video'),
                cheat_proof=data.get('cheat_proof'),
            )
            # 头像同步回填到 CustomUser.avatar_url，供 C 端订单/评价展示
            if profile.avatar:
                user.avatar_url = request.build_absolute_uri(profile.avatar.url)
                user.save(update_fields=['avatar_url'])
        return Response(
            {'code': 0, 'data': AdminEscortSerializer(profile, context={'request': request}).data},
            status=201,
        )

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        profile = self.get_object()
        profile.is_verified = _truthy(request.data.get('is_verified', True))
        profile.save(update_fields=['is_verified'])
        return Response({'code': 0, 'data': self.get_serializer(profile).data})

    @action(detail=True, methods=['post'])
    def dispose(self, request, pk=None):
        """奖励或罚款：直接影响陪玩钱包余额，落流水 + 累加统计 + 记录。"""
        profile = self.get_object()
        dispose_type = (request.data.get('dispose_type') or '').strip().upper()
        if dispose_type not in DisposeRecord.DisposeType.values:
            return Response({'code': 400, 'msg': '请选择奖励或罚款'})
        try:
            amount = int(request.data.get('amount', 0))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请输入合法的兴安币金额'})
        if amount <= 0:
            return Response({'code': 400, 'msg': '金额必须大于 0'})
        reason = (request.data.get('reason') or '').strip()[:255]

        is_reward = dispose_type == DisposeRecord.DisposeType.REWARD
        signed = amount if is_reward else -amount

        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=profile.user_id)
            if wallet.balance + signed < 0:
                return Response({'code': 400, 'msg': '罚款后余额不能为负'})
            balance_before = wallet.balance
            wallet.balance += signed
            wallet.save(update_fields=['balance'])

            tx = Transaction.objects.create(
                wallet=wallet,
                amount=signed,
                tx_type=Transaction.TxType.REWARD if is_reward else Transaction.TxType.PENALTY,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=reason or ('奖励' if is_reward else '罚款'),
            )

            locked_profile = EscortProfile.objects.select_for_update().get(pk=profile.pk)
            if is_reward:
                locked_profile.total_reward += amount
                locked_profile.save(update_fields=['total_reward'])
            else:
                locked_profile.total_penalty += amount
                locked_profile.save(update_fields=['total_penalty'])

            record = DisposeRecord.objects.create(
                user_id=profile.user_id,
                dispose_type=dispose_type,
                amount=amount,
                reason=reason,
                operator=request.user,
                transaction=tx,
            )

        profile.refresh_from_db()
        return Response({
            'code': 0,
            'msg': '操作成功',
            'data': {
                'escort': self.get_serializer(profile).data,
                'record': AdminDisposeRecordSerializer(record).data,
            },
        })


# ---------------- 订单 ----------------
class OrderViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminOrderSerializer
    default_perm = 'order:view'
    required_perms = {
        'refund': 'order:refund',
        'cancel': 'order:cancel',
        'quick_dispatch': 'order:dispatch',
        'assign': 'order:dispatch',
        'start': 'order:dispatch',
        'complete': 'order:settle',
    }

    def get_queryset(self):
        qs = Order.objects.select_related('customer', 'provider').prefetch_related(
            'providers__provider'
        ).order_by('-created_at')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        payment = self.request.query_params.get('payment_status')
        if payment:
            qs = qs.filter(payment_status=payment.upper())
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(order_no__icontains=keyword)
                | Q(customer__username__icontains=keyword)
                | Q(customer__phone__icontains=keyword)
            )
        order_no = self.request.query_params.get('order_no')
        if order_no:
            qs = qs.filter(order_no__icontains=order_no)
        customer_username = self.request.query_params.get('customer_username')
        if customer_username:
            qs = qs.filter(customer__username__icontains=customer_username)
        customer_nickname = self.request.query_params.get('customer_nickname')
        if customer_nickname:
            qs = qs.filter(customer__nickname__icontains=customer_nickname)
        customer_phone = self.request.query_params.get('customer_phone')
        if customer_phone:
            qs = qs.filter(customer__phone__icontains=customer_phone)
        return qs

    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        order = self.get_object()
        logs = order.status_logs.select_related('operator').all()
        return Response({'code': 0, 'data': AdminOrderLogSerializer(logs, many=True).data})

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """将已有待接单订单指派给一名当前可接单的陪玩。"""
        order = self.get_object()
        provider_id = request.data.get('provider_id')
        if not provider_id:
            return Response({'code': 400, 'msg': '请选择陪玩'})
        try:
            provider = User.objects.select_related('escort_profile').get(
                id=provider_id, role=User.Role.PROVIDER,
            )
        except User.DoesNotExist:
            return Response({'code': 404, 'msg': '陪玩不存在'})
        profile = getattr(provider, 'escort_profile', None)
        if profile is None or profile.status != EscortProfile.Status.AVAILABLE:
            return Response({'code': 400, 'msg': '该陪玩当前不可接单'})
        if not EscortSchedule.provider_is_scheduled_now(provider):
            return Response({'code': 400, 'msg': '该陪玩当前不在接单档期'})

        def pre_check(o):
            if o.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能派单')
            if o.escort_mode != Order.EscortMode.SINGLE:
                raise IllegalTransitionError('双陪订单请通过快捷派单创建并同时指定两名陪玩')

        def side_effect(o):
            o.provider = provider
            o.provider_name_snapshot = profile.display_name or provider.nickname or provider.username
            EscortProfile.objects.filter(user=provider).update(status=EscortProfile.Status.BUSY)
            OrderProvider.objects.get_or_create(
                order=o,
                provider=provider,
                defaults={
                    'provider_name_snapshot': o.provider_name_snapshot,
                    'settlement_base': o.amount,
                    'commission_type': OrderProvider.CommissionType.PERCENT,
                    'commission_rate': o.commission_rate,
                    'provider_income': o.provider_income,
                },
            )

        try:
            order = transition(
                order.id,
                Order.Status.GRABBED,
                operator=request.user,
                action=OrderStatusLog.Action.ASSIGN,
                reason='客服指派陪玩',
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=['provider', 'provider_name_snapshot'],
            )
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许派单'})

        notify_order_update(order)
        _push_message_safe(
            recipient_id=provider.id,
            title='客服派单给你',
            preview=f'客服为你派单「{order.service_name_snapshot}」，请尽快开始服务',
            msg_type='ORDER',
            related_order_id=order.id,
        )
        _push_message_safe(
            recipient_id=order.customer_id,
            title='已为你指派大神',
            preview=f'{order.provider_name_snapshot}已接单你的「{order.service_name_snapshot}」',
            msg_type='ORDER',
            related_order_id=order.id,
        )
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '派单成功'})

    @action(detail=False, methods=['get'], url_path='suggest-commission')
    def suggest_commission(self, request):
        """派单时按 service_id + provider_id 解析建议抽成率(%)，供前端默认带出后可手改。

        返回 source（来源层级）与 source_label（可读来源），便于客服知晓抽成依据。
        """
        from orders.pricing import (
            resolve_active_promotion,
            resolve_commission_rate_with_source,
        )

        service_id = request.query_params.get('service_id')
        provider_id = request.query_params.get('provider_id')
        if not service_id:
            return Response({'code': 400, 'msg': '缺少 service_id'})
        try:
            service = ServiceItem.objects.get(id=service_id)
        except ServiceItem.DoesNotExist:
            return Response({'code': 404, 'msg': '服务不存在'})
        escort_profile = None
        if provider_id:
            escort_profile = EscortProfile.objects.filter(
                user_id=provider_id, user__role=User.Role.PROVIDER,
            ).select_related('level').first()
        promotion = resolve_active_promotion(service)
        rate, source = resolve_commission_rate_with_source(service, escort_profile, promotion)
        label_map = {
            'promotion': f'活动定价（{promotion.title}）' if promotion else '活动定价',
            'level': f'等级抽成（{escort_profile.level.name}）'
                     if escort_profile and getattr(escort_profile, 'level', None) else '等级抽成',
            'service': '商品抽成',
            'global': '全局默认',
        }
        return Response({'code': 0, 'data': {
            'commission_rate': rate,
            'source': source,
            'source_label': label_map.get(source, ''),
        }})

    @action(detail=False, methods=['post'], url_path='dispatch')
    def quick_dispatch(self, request):
        """客服代派单：替指定老板下单（扣其钱包余额），可选直接指派陪玩。

        资金口径复用 C 端下单流程：立即扣减目标老板余额，生成 PAID 订单并落拆账字段；
        结算仍在订单完成/报单审核时进行。指派陪玩则订单直接转 GRABBED 并置陪玩 BUSY，
        留空则保持 PENDING 并投递超时自动取消任务。
        """
        # ---- 入参校验 ----
        customer_id = request.data.get('customer_id')
        service_id = request.data.get('service_id')
        if not customer_id:
            return Response({'code': 400, 'msg': '请选择下单老板'})
        if not service_id:
            return Response({'code': 400, 'msg': '请选择服务'})
        try:
            game_rounds = int(request.data.get('game_rounds', 1))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '局数必须为整数'})
        if game_rounds < 1:
            return Response({'code': 400, 'msg': '局数至少为 1'})
        remark = (request.data.get('remark') or '').strip()[:255]

        try:
            customer = User.objects.get(id=customer_id, role=User.Role.CUSTOMER)
        except User.DoesNotExist:
            return Response({'code': 404, 'msg': '老板不存在'})
        try:
            service = ServiceItem.objects.get(id=service_id, is_active=True)
        except ServiceItem.DoesNotExist:
            return Response({'code': 404, 'msg': '服务不存在或已下架'})

        # ---- 打手入参：escort_mode + providers[]（兼容旧的单 provider_id）----
        escort_mode = (request.data.get('escort_mode') or Order.EscortMode.SINGLE).upper()
        if escort_mode not in Order.EscortMode.values:
            return Response({'code': 400, 'msg': '陪玩模式无效'})
        providers_input = request.data.get('providers')
        if not providers_input:
            # 兼容旧参数：单个 provider_id
            legacy_id = request.data.get('provider_id')
            providers_input = [{'provider_id': legacy_id}] if legacy_id else []
        if not isinstance(providers_input, list):
            return Response({'code': 400, 'msg': 'providers 格式错误'})
        # 去除空项
        providers_input = [p for p in providers_input if p and p.get('provider_id')]
        expected = 2 if escort_mode == Order.EscortMode.DOUBLE else 1
        if providers_input and len(providers_input) != expected:
            return Response({
                'code': 400,
                'msg': f'{"双陪需指定 2 名" if expected == 2 else "单陪最多 1 名"}打手',
            })
        # 双陪必须显式指定两名打手（不支持留空进大厅）
        if escort_mode == Order.EscortMode.DOUBLE and len(providers_input) != 2:
            return Response({'code': 400, 'msg': '双陪需指定 2 名打手'})
        # 不可重复指派同一打手
        picked_ids = [str(p['provider_id']) for p in providers_input]
        if len(set(picked_ids)) != len(picked_ids):
            return Response({'code': 400, 'msg': '不可重复指派同一打手'})

        provider_objs = []
        for item in providers_input:
            try:
                pu = User.objects.select_related('escort_profile__level').get(
                    id=item['provider_id'], role=User.Role.PROVIDER
                )
            except User.DoesNotExist:
                return Response({'code': 404, 'msg': '指定的陪玩不存在'})
            if pu.escort_profile.status != EscortProfile.Status.AVAILABLE:
                return Response({'code': 400, 'msg': f'{pu.escort_profile.display_name}当前不可接单'})
            from users.models import EscortSchedule
            if not EscortSchedule.provider_is_scheduled_now(pu):
                return Response({'code': 400, 'msg': f'{pu.escort_profile.display_name}当前不在接单档期'})
            provider_objs.append((pu, item))

        # 主打手（回填 Order.provider 兼容旧字段与流转）
        provider = provider_objs[0][0] if provider_objs else None

        # ---- 金额与拆账（复用 C 端计价口径：老板折扣+活动折扣，代派单不含优惠券）----
        from orders.pricing import (
            PricingError,
            compute_order_amount,
            resolve_active_promotion,
            resolve_commission_rate,
        )
        original_amount = service.price * game_rounds
        boss_type = getattr(customer, 'boss_type', None)
        boss_rate = boss_type.discount_rate if (boss_type and boss_type.is_active) else 100
        promotion = resolve_active_promotion(service)
        try:
            pricing = compute_order_amount(
                original_amount=original_amount,
                boss_rate=boss_rate,
                promotion=promotion,
            )
        except PricingError as exc:
            return Response({'code': 400, 'msg': str(exc)})
        amount = pricing.amount

        # 资金守恒：实付金额先在打手之间平均分配（余数给靠前打手），
        # 再按各自抽成规则结算，避免双陪按两份全额结算造成平台超付。
        from orders.models import OrderProvider as _OP
        provider_rows = []
        provider_count = len(provider_objs)
        settlement_bases = []
        if provider_count:
            base, remainder = divmod(amount, provider_count)
            settlement_bases = [base + (1 if idx < remainder else 0) for idx in range(provider_count)]
        for idx, (pu, item) in enumerate(provider_objs):
            settlement_base = settlement_bases[idx]
            escort_profile = getattr(pu, 'escort_profile', None)
            ctype = item.get('commission_type') or _OP.CommissionType.PERCENT
            if ctype == _OP.CommissionType.FIXED:
                try:
                    fixed = int(item.get('commission_fixed') or 0)
                except (TypeError, ValueError):
                    return Response({'code': 400, 'msg': '固定抽成额必须为非负兴安币'})
                if fixed < 0:
                    return Response({'code': 400, 'msg': '固定抽成额必须为非负兴安币'})
                fixed = min(fixed, settlement_base)
                provider_rows.append({
                    'provider': pu,
                    'settlement_base': settlement_base,
                    'commission_type': _OP.CommissionType.FIXED,
                    'commission_rate': 0,
                    'commission_fixed': fixed,
                    'provider_income': settlement_base - fixed,
                })
            else:
                rate_in = item.get('commission_rate')
                if rate_in is None or rate_in == '':
                    rate = resolve_commission_rate(service, escort_profile, promotion)
                else:
                    try:
                        rate = min(max(int(rate_in), 0), 100)
                    except (TypeError, ValueError):
                        return Response({'code': 400, 'msg': '抽成率必须为 0-100 的整数'})
                provider_rows.append({
                    'provider': pu,
                    'settlement_base': settlement_base,
                    'commission_type': _OP.CommissionType.PERCENT,
                    'commission_rate': rate,
                    'commission_fixed': 0,
                    'provider_income': settlement_base * (100 - rate) // 100,
                })

        # 订单级拆账汇总，保证打手实得总和 + 推荐分佣 + 平台留存 = 实付。
        if provider_rows:
            primary = provider_rows[0]
            total_provider_income = sum(row['provider_income'] for row in provider_rows)
            platform_cut = amount - total_provider_income
            inviter_rate = customer.inviter_commission_rate if getattr(customer, 'inviter', None) else 0
            inviter_commission = platform_cut * min(max(int(inviter_rate), 0), 100) // 100
            from orders.settlement import OrderSplit
            split = OrderSplit(
                commission_rate=primary['commission_rate'],
                provider_income=total_provider_income,
                inviter_commission=inviter_commission,
                shop_income=platform_cut - inviter_commission,
            )
        else:
            primary_rate = resolve_commission_rate(service, None, promotion)
            split = compute_split(
                amount,
                primary_rate,
                customer.inviter_commission_rate if getattr(customer, 'inviter', None) else 0,
            )
        inviter = getattr(customer, 'inviter', None)

        # ---- 扣款建单 ----
        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(user=customer)
            if not wallet.is_active:
                return Response({'code': 403, 'msg': '该老板钱包不可用'})
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': '该老板兴安币不足，请先通过企微客服充值'})

            balance_before = wallet.balance
            wallet.balance -= amount
            wallet.save(update_fields=['balance'])

            order = Order.objects.create(
                customer=customer,
                provider=provider,
                service=service,
                amount=amount,
                game_rounds=game_rounds,
                remark=remark,
                status=Order.Status.PENDING,
                escort_mode=escort_mode,
                payment_status=Order.PaymentStatus.PAID,
                auto_cancel_at=timezone.now() + timedelta(minutes=PENDING_TIMEOUT_MINUTES),
                original_amount=pricing.original_amount,
                boss_discount=pricing.boss_discount,
                promo_discount=pricing.promo_discount,
                coupon_discount=pricing.coupon_discount,
                promotion=promotion,
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter=inviter,
                inviter_commission=split.inviter_commission,
                shop_income=split.shop_income,
            )
            # 打手明细落库
            for row in provider_rows:
                pu = row['provider']
                OrderProvider.objects.create(
                    order=order,
                    provider=pu,
                    provider_name_snapshot=pu.nickname or pu.username,
                    settlement_base=row['settlement_base'],
                    commission_type=row['commission_type'],
                    commission_rate=row['commission_rate'],
                    commission_fixed=row['commission_fixed'],
                    provider_income=row['provider_income'],
                )
            Transaction.objects.create(
                wallet=wallet,
                order=order,
                amount=-amount,
                tx_type=Transaction.TxType.PAY,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=f'客服代派单：{order.order_no}',
            )
            log_only(
                order,
                action=OrderStatusLog.Action.CREATE,
                operator=request.user,
                reason='客服代派单',
            )

        # ---- 可选指派陪玩 ----
        if provider is not None:
            all_provider_ids = [row['provider'].id for row in provider_rows]

            def side_effect(o):
                o.provider = provider
                o.provider_name_snapshot = provider.nickname or provider.username
                EscortProfile.objects.filter(user_id__in=all_provider_ids).update(
                    status=EscortProfile.Status.BUSY,
                )

            try:
                order = transition(
                    order.id,
                    Order.Status.GRABBED,
                    operator=request.user,
                    action=OrderStatusLog.Action.ASSIGN,
                    side_effect=side_effect,
                    update_fields=['provider', 'provider_name_snapshot'],
                )
            except IllegalTransitionError as exc:
                return Response({'code': 400, 'msg': str(exc) or '指派失败'})

            for row in provider_rows:
                _push_message_safe(
                    recipient_id=row['provider'].id,
                    title='客服派单给你',
                    preview=f'客服为你派单「{order.service_name_snapshot}」，请尽快开始服务',
                    msg_type='ORDER',
                    related_order_id=order.id,
                )
            _push_message_safe(
                recipient_id=order.customer_id,
                title='已为你指派大神',
                preview=f'{order.provider_name_snapshot}已接单你的「{order.service_name_snapshot}」',
                msg_type='ORDER',
                related_order_id=order.id,
            )
        else:
            _schedule_auto_cancel(order)
            enqueue_kook_dispatch(order, KookDispatchRecord.Trigger.NEW_ORDER)
            _push_message_safe(
                recipient_id=order.customer_id,
                title='客服已为你下单',
                preview=f'客服为你下单「{order.service_name_snapshot}」，正在为你匹配大神',
                msg_type='ORDER',
                related_order_id=order.id,
            )

        notify_order_update(order)
        return Response({
            'code': 0,
            'msg': '派单成功',
            'data': self.get_serializer(order).data,
        })

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """客服推进：已接单 → 服务中（保留中间流转过程）。"""
        order = self.get_object()

        def side_effect(o):
            for row in o.providers.all():
                if row.provider_id:
                    _push_message_safe(
                        recipient_id=row.provider_id,
                        title='订单已开始服务',
                        preview=f'「{o.service_name_snapshot}」已进入服务中',
                        msg_type='ORDER',
                        related_order_id=o.id,
                    )

        try:
            order = transition(
                order.id,
                Order.Status.IN_SERVICE,
                operator=request.user,
                action=OrderStatusLog.Action.START,
                side_effect=side_effect,
            )
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许开始服务'})
        notify_order_update(order)
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '已开始服务'})

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """客服后台完成结算：服务中 → 已完成。

        逐个给打手钱包入账（按 OrderProvider 各自实得），推荐人分佣与平台留存按单份入账，
        并恢复相关陪玩状态、累加完成计数。无 OrderProvider 明细时回落主打手单份口径。
        """
        order = self.get_object()

        def side_effect(o):
            provider_rows = list(o.providers.select_for_update().all())
            settled_provider_ids = []
            if provider_rows:
                for row in provider_rows:
                    if not row.provider_id or row.settled_at:
                        continue
                    p_wallet, _ = Wallet.objects.select_for_update().get_or_create(
                        user_id=row.provider_id,
                    )
                    before = p_wallet.balance
                    p_wallet.balance += row.provider_income
                    p_wallet.save(update_fields=['balance'])
                    Transaction.objects.create(
                        wallet=p_wallet,
                        order=o,
                        amount=row.provider_income,
                        tx_type=Transaction.TxType.INCOME,
                        balance_before=before,
                        balance_after=p_wallet.balance,
                        remark=f'订单收入：{o.order_no}',
                    )
                    row.settled_at = timezone.now()
                    row.save(update_fields=['settled_at'])
                    settled_provider_ids.append(row.provider_id)
            elif o.provider_id:
                # 兼容旧订单：无打手明细时按订单级 provider_income 给主打手入账
                income = o.provider_income or o.amount
                p_wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=o.provider_id)
                before = p_wallet.balance
                p_wallet.balance += income
                p_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=p_wallet,
                    order=o,
                    amount=income,
                    tx_type=Transaction.TxType.INCOME,
                    balance_before=before,
                    balance_after=p_wallet.balance,
                    remark=f'订单收入：{o.order_no}',
                )
                settled_provider_ids.append(o.provider_id)

            # 推荐人分佣入账（单份）
            if o.inviter_id and o.inviter_commission:
                inv_wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=o.inviter_id)
                inv_before = inv_wallet.balance
                inv_wallet.balance += o.inviter_commission
                inv_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=inv_wallet,
                    order=o,
                    amount=o.inviter_commission,
                    tx_type=Transaction.TxType.INCOME,
                    balance_before=inv_before,
                    balance_after=inv_wallet.balance,
                    remark=f'推荐分佣：{o.order_no}',
                )
            # 平台留存入账（单份）
            if o.shop_income:
                platform_wallet = get_platform_wallet(for_update=True)
                shop_before = platform_wallet.balance
                platform_wallet.balance += o.shop_income
                platform_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=platform_wallet,
                    order=o,
                    amount=o.shop_income,
                    tx_type=Transaction.TxType.SHOP_INCOME,
                    balance_before=shop_before,
                    balance_after=platform_wallet.balance,
                    remark=f'平台收入：{o.order_no}',
                )
            # 恢复相关陪玩状态并累加完成计数
            if settled_provider_ids:
                EscortProfile.objects.filter(user_id__in=settled_provider_ids).update(
                    status=EscortProfile.Status.AVAILABLE,
                )
                for profile in EscortProfile.objects.filter(user_id__in=settled_provider_ids):
                    profile.completed_order_count = (profile.completed_order_count or 0) + 1
                    profile.save(update_fields=['completed_order_count'])

        try:
            order = transition(
                order.id,
                Order.Status.COMPLETED,
                operator=request.user,
                action=OrderStatusLog.Action.COMPLETE,
                side_effect=side_effect,
            )
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许完成'})
        notify_order_update(order)
        _push_message_safe(
            recipient_id=order.customer_id,
            title='订单已完成',
            preview=f'你的「{order.service_name_snapshot}」已完成，去评价赢积分',
            msg_type='ORDER',
            related_order_id=order.id,
        )
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '订单已完成'})

    @action(detail=True, methods=['post'])
    def refund(self, request, pk=None):
        order = self.get_object()
        reason = (request.data.get('reason') or '').strip()[:255]
        if not reason:
            return Response({'code': 400, 'msg': '请填写退款原因'})
        try:
            order = self._do_refund(order.id, request.user, reason)
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许退款'})
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '已退款'})

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        order = self.get_object()
        reason = (request.data.get('reason') or '').strip()[:255]
        if order.status != Order.Status.PENDING:
            return Response({'code': 400, 'msg': '仅待接单订单可直接取消，其它状态请用退款'})
        try:
            order = self._do_refund(order.id, request.user, reason or '后台取消',
                                    action=OrderStatusLog.Action.CANCEL)
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许取消'})
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '已取消'})

    @staticmethod
    def _do_refund(order_id, operator, reason, action=OrderStatusLog.Action.REFUND):
        def pre_check(order):
            if order.status in Order.TERMINAL_STATUSES:
                raise IllegalTransitionError('订单已是终态，无法退款')

        def side_effect(order):
            if order.payment_status == Order.PaymentStatus.PAID:
                wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=order.customer_id)
                balance_before = wallet.balance
                wallet.balance += order.amount
                wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=wallet,
                    order=order,
                    amount=order.amount,
                    tx_type=Transaction.TxType.REFUND,
                    balance_before=balance_before,
                    balance_after=wallet.balance,
                    remark=f'后台退款：{order.order_no}',
                )
                order.payment_status = Order.PaymentStatus.REFUNDED
                order.refunded_at = timezone.now()
            order.cancel_reason = reason
            provider_ids = list(order.providers.values_list('provider_id', flat=True))
            if order.provider_id:
                provider_ids.append(order.provider_id)
            EscortProfile.objects.filter(user_id__in=provider_ids).update(
                status=EscortProfile.Status.AVAILABLE,
            )
            from coupons.models import UserCoupon
            UserCoupon.objects.filter(
                order=order, status=UserCoupon.Status.USED,
            ).update(status=UserCoupon.Status.UNUSED, order=None, used_at=None)

        return transition(
            order_id,
            Order.Status.CANCELLED,
            operator=operator,
            action=action,
            reason=reason,
            pre_check=pre_check,
            side_effect=side_effect,
            update_fields=['cancel_reason', 'payment_status', 'refunded_at'],
        )


# ---------------- 评价管理 ----------------
class EvaluationViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """评价管理：列表 / 筛选 / 详情 / 代回复 / 删除（删除回退陪玩评分聚合）。"""

    serializer_class = AdminEvaluationSerializer
    default_perm = 'evaluation:view'
    required_perms = {
        'reply': 'evaluation:reply',
        'destroy': 'evaluation:delete',
    }

    def get_queryset(self):
        qs = (
            Evaluation.objects.select_related('customer', 'provider', 'order')
            .order_by('-created_at')
        )
        score = self.request.query_params.get('score')
        if score:
            qs = qs.filter(score=score)
        only_unreplied = self.request.query_params.get('only_unreplied')
        if only_unreplied in ('1', 'true', 'True'):
            qs = qs.filter(reply_content='')
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(order__order_no__icontains=keyword)
                | Q(customer__username__icontains=keyword)
                | Q(customer__nickname__icontains=keyword)
                | Q(provider__username__icontains=keyword)
                | Q(provider__nickname__icontains=keyword)
            )
        order_no = self.request.query_params.get('order_no')
        if order_no:
            qs = qs.filter(order__order_no__icontains=order_no)
        customer_username = self.request.query_params.get('customer_username')
        if customer_username:
            qs = qs.filter(customer__username__icontains=customer_username)
        customer_nickname = self.request.query_params.get('customer_nickname')
        if customer_nickname:
            qs = qs.filter(customer__nickname__icontains=customer_nickname)
        provider_username = self.request.query_params.get('provider_username')
        if provider_username:
            qs = qs.filter(provider__username__icontains=provider_username)
        provider_nickname = self.request.query_params.get('provider_nickname')
        if provider_nickname:
            qs = qs.filter(provider__nickname__icontains=provider_nickname)
        return qs

    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        content = (request.data.get('reply_content') or '').strip()
        if not content:
            return Response({'code': 400, 'msg': '回复内容不能为空'})
        evaluation = self.get_object()
        evaluation.reply_content = content[:1000]
        evaluation.replied_at = timezone.now()
        evaluation.save(update_fields=['reply_content', 'replied_at', 'updated_at'])
        return Response({
            'code': 0,
            'data': self.get_serializer(evaluation).data,
            'msg': '已回复',
        })

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            evaluation = Evaluation.objects.select_for_update().get(pk=kwargs['pk'])
            rollback_escort_rating(evaluation.provider_id, evaluation.score)
            evaluation.delete()
        return Response({'code': 0, 'msg': '已删除'})


# ---------------- 钱包 ----------------
class WalletViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminWalletSerializer
    default_perm = 'wallet:view'
    required_perms = {'adjust': 'wallet:adjust', 'recharge': 'wallet:recharge'}

    def get_queryset(self):
        # 老板钱包管理：仅老板（CUSTOMER）的钱包
        qs = (
            Wallet.objects.filter(user__role=User.Role.CUSTOMER)
            .select_related('user', 'user__boss_type')
            .order_by('-updated_at')
        )
        # 编号 / 昵称 / 手机号 三个独立条件，相互 AND 叠加（支持输入即查提示）
        boss_no = self.request.query_params.get('boss_no')
        if boss_no:
            qs = qs.filter(user__boss_no__icontains=boss_no)
        nickname = self.request.query_params.get('nickname')
        if nickname:
            qs = qs.filter(user__nickname__icontains=nickname)
        phone = self.request.query_params.get('phone')
        if phone:
            qs = qs.filter(user__phone__icontains=phone)
        return qs

    @action(detail=True, methods=['post'])
    def adjust(self, request, pk=None):
        try:
            amount = int(request.data.get('amount'))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请输入合法的兴安币调账金额'})
        if amount == 0:
            return Response({'code': 400, 'msg': '调账金额不能为 0'})
        remark = (request.data.get('remark') or '').strip()[:255] or '后台调账'

        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=pk)
            if wallet.balance + amount < 0:
                return Response({'code': 400, 'msg': '调账后余额不能为负'})
            balance_before = wallet.balance
            wallet.balance += amount
            wallet.save(update_fields=['balance'])
            Transaction.objects.create(
                wallet=wallet,
                amount=amount,
                tx_type=Transaction.TxType.REWARD if amount > 0 else Transaction.TxType.WITHDRAW,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=remark,
                operator=request.user,
            )
        return Response({'code': 0, 'data': self.get_serializer(wallet).data, 'msg': '调账成功'})

    @action(detail=False, methods=['post'])
    def recharge(self, request):
        """确认企微客服收款，并按 1:10 兑换规则录入兴安币。"""
        user_id = request.data.get('user_id')
        if not user_id:
            return Response({'code': 400, 'msg': '请指定充值用户'})
        try:
            amount = int(request.data.get('amount', 0))
            gift_amount = int(request.data.get('gift_amount', 0) or 0)
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请输入合法的兴安币金额'})
        if amount <= 0:
            return Response({'code': 400, 'msg': '实充金额必须大于 0'})
        if gift_amount < 0:
            return Response({'code': 400, 'msg': '赠送金额不能为负'})
        remark = (request.data.get('remark') or '').strip()[:255]
        trade_no = (request.data.get('trade_no') or '').strip()[:64]
        proof_image = request.FILES.get('proof_image')

        if not User.objects.filter(pk=user_id).exists():
            return Response({'code': 404, 'msg': '用户不存在'})

        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=user_id)
            balance_before = wallet.balance
            recharge_tx = Transaction.objects.create(
                wallet=wallet,
                amount=amount,
                tx_type=Transaction.TxType.TOPUP,
                balance_before=balance_before,
                balance_after=balance_before + amount,
                remark=remark or '企微客服充值兑换兴安币',
            )
            wallet.balance += amount
            wallet.total_recharge += amount

            gift_tx = None
            if gift_amount > 0:
                gift_before = wallet.balance
                gift_tx = Transaction.objects.create(
                    wallet=wallet,
                    amount=gift_amount,
                    tx_type=Transaction.TxType.GIFT,
                    balance_before=gift_before,
                    balance_after=gift_before + gift_amount,
                    remark=remark or '客服赠送兴安币',
                )
                wallet.balance += gift_amount
                wallet.total_gift += gift_amount

            wallet.save(update_fields=['balance', 'total_recharge', 'total_gift'])

            record = RechargeRecord.objects.create(
                user_id=user_id,
                amount=amount,
                gift_amount=gift_amount,
                trade_no=trade_no,
                proof_image=proof_image,
                remark=remark,
                operator=request.user,
                recharge_tx=recharge_tx,
                gift_tx=gift_tx,
            )

        return Response({
            'code': 0,
            'msg': '已按 1:10 兑换规则入账兴安币',
            'data': {
                'wallet': self.get_serializer(wallet).data,
                'record': AdminRechargeRecordSerializer(record).data,
                'credited_coins': (amount + gift_amount) / 10,
                'exchange_rate': 10,
            },
        })


class RechargeRecordViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """老板充值记录：列表 / 筛选。"""

    serializer_class = AdminRechargeRecordSerializer
    default_perm = 'recharge:view'

    def get_queryset(self):
        qs = RechargeRecord.objects.select_related('user', 'user__boss_type', 'operator').order_by('-created_at')
        user_id = self.request.query_params.get('user_id')
        if user_id:
            qs = qs.filter(user_id=user_id)
        # 编号 / 昵称 / 手机号 三个独立条件，相互 AND 叠加（支持输入即查提示）
        boss_no = self.request.query_params.get('boss_no')
        if boss_no:
            qs = qs.filter(user__boss_no__icontains=boss_no)
        nickname = self.request.query_params.get('nickname')
        if nickname:
            qs = qs.filter(user__nickname__icontains=nickname)
        phone = self.request.query_params.get('phone')
        if phone:
            qs = qs.filter(user__phone__icontains=phone)
        return qs


class DisposeRecordViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """奖励罚款记录：列表 / 筛选。"""

    serializer_class = AdminDisposeRecordSerializer
    default_perm = 'dispose:view'

    def get_queryset(self):
        qs = DisposeRecord.objects.select_related('user', 'operator').order_by('-created_at')
        user_id = self.request.query_params.get('user_id')
        if user_id:
            qs = qs.filter(user_id=user_id)
        dispose_type = self.request.query_params.get('dispose_type')
        if dispose_type:
            qs = qs.filter(dispose_type=dispose_type.upper())
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(user__username__icontains=keyword)
                | Q(user__nickname__icontains=keyword)
            )
        return qs


class TransactionViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminTransactionSerializer
    default_perm = 'transaction:view'

    def get_queryset(self):
        qs = Transaction.objects.select_related('wallet', 'wallet__user', 'operator').order_by('-created_at')
        tx_type = self.request.query_params.get('tx_type')
        if tx_type:
            qs = qs.filter(tx_type=tx_type.upper())
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(tx_no__icontains=keyword)
                | Q(wallet__user__username__icontains=keyword)
            )
        return qs


# ---------------- 陪玩报单 ----------------
class ProviderReportViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminProviderReportSerializer
    default_perm = 'report:view'
    required_perms = {
        'approve': 'report:audit',
        'reject': 'report:audit',
    }

    def get_queryset(self):
        qs = ProviderReport.objects.select_related(
            'provider', 'provider__escort_profile__level', 'auditor', 'order',
        ).prefetch_related('result_images').exclude(status=ProviderReport.Status.DRAFT).order_by('-created_at')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(game_name__icontains=keyword)
                | Q(provider__username__icontains=keyword)
                | Q(provider__nickname__icontains=keyword)
            )
        game_name = self.request.query_params.get('game_name')
        if game_name:
            qs = qs.filter(game_name__icontains=game_name)
        provider_username = self.request.query_params.get('provider_username')
        if provider_username:
            qs = qs.filter(provider__username__icontains=provider_username)
        provider_nickname = self.request.query_params.get('provider_nickname')
        if provider_nickname:
            qs = qs.filter(provider__nickname__icontains=provider_nickname)
        return qs

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        with transaction.atomic():
            report = ProviderReport.objects.select_for_update().get(pk=pk)
            if report.status != ProviderReport.Status.PENDING:
                return Response({'code': 400, 'msg': '该报单已审核，无法重复操作'})

            rate, source_label = _resolve_provider_report_commission(report.provider)
            # 平台完成订单已在完成动作中结算，报单审核仅核验凭证，禁止重复入账。
            if report.order_id:
                report.status = ProviderReport.Status.APPROVED
                report.commission_rate = report.order.commission_rate
                report.payout_amount = report.order.provider_income
                report.auditor = request.user
                report.audited_at = timezone.now()
                report.save(update_fields=[
                    'status', 'commission_rate', 'payout_amount', 'auditor', 'audited_at',
                ])
                return Response({'code': 0, 'data': self.get_serializer(report).data, 'msg': '凭证审核通过'})

            payout = report.amount * (100 - rate) // 100

            wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=report.provider_id)
            balance_before = wallet.balance
            wallet.balance += payout
            wallet.save(update_fields=['balance'])
            tx = Transaction.objects.create(
                wallet=wallet,
                amount=payout,
                tx_type=Transaction.TxType.INCOME,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=(
                    f'报单入账：{report.game_name}'
                    f'（金额{report.amount / 10:g}兴安币，抽成{rate}%，{source_label}）'
                ),
            )

            report.status = ProviderReport.Status.APPROVED
            report.commission_rate = rate
            report.payout_amount = payout
            report.auditor = request.user
            report.transaction = tx
            report.audited_at = timezone.now()
            report.save(update_fields=[
                'status', 'commission_rate', 'payout_amount', 'auditor',
                'transaction', 'audited_at',
            ])

        create_message(
            recipient_id=report.provider_id,
            title='报单审核通过',
            preview=f'{report.game_name} 报单已通过，入账 {payout / 10:g} 兴安币',
            detail=(
                f'报单金额 {report.amount / 10:g} 兴安币，'
                f'平台抽成 {rate}%（{source_label}），实际入账 {payout / 10:g} 兴安币。'
            ),
        )
        return Response({'code': 0, 'data': self.get_serializer(report).data, 'msg': '已通过并入账'})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        audit_remark = (request.data.get('audit_remark') or '').strip()[:255]
        if not audit_remark:
            return Response({'code': 400, 'msg': '请填写驳回原因'})
        with transaction.atomic():
            report = ProviderReport.objects.select_for_update().get(pk=pk)
            if report.status != ProviderReport.Status.PENDING:
                return Response({'code': 400, 'msg': '该报单已审核，无法重复操作'})
            report.status = ProviderReport.Status.REJECTED
            report.audit_remark = audit_remark
            report.auditor = request.user
            report.audited_at = timezone.now()
            report.save(update_fields=['status', 'audit_remark', 'auditor', 'audited_at'])

        create_message(
            recipient_id=report.provider_id,
            title='报单审核未通过',
            preview=f'{report.game_name} 报单被驳回',
            detail=f'驳回原因：{audit_remark}',
        )
        return Response({'code': 0, 'data': self.get_serializer(report).data, 'msg': '已驳回'})


class CommissionConfigView(APIView):
    """平台抽成率配置：GET 读取，PUT 修改（需 report:audit）。"""

    permission_classes = [IsConsoleUser]

    def put(self, request):
        if not request.user.is_superuser and 'report:audit' not in get_user_permissions(request.user):
            return Response({'code': 403, 'msg': '无操作权限'})
        try:
            rate = int(request.data.get('rate'))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请输入合法的抽成率（0-100 的整数）'})
        if rate < 0 or rate > 100:
            return Response({'code': 400, 'msg': '抽成率需在 0-100 之间'})
        SystemConfig.objects.update_or_create(
            key=COMMISSION_RATE_KEY,
            defaults={'value': str(rate), 'remark': '平台报单抽成率(%)'},
        )
        return Response({'code': 0, 'data': {'rate': rate}, 'msg': '抽成率已更新'})

    def get(self, request):
        return Response({'code': 0, 'data': {'rate': get_commission_rate()}})


# ---------------- 提现申请 ----------------
class WithdrawRequestViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminWithdrawSerializer
    default_perm = 'withdraw:view'
    required_perms = {
        'approve': 'withdraw:audit',
        'reject': 'withdraw:audit',
    }

    def get_queryset(self):
        qs = WithdrawRequest.objects.select_related('user', 'auditor').order_by('-created_at')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(user__username__icontains=keyword)
                | Q(user__nickname__icontains=keyword)
                | Q(payee_name__icontains=keyword)
                | Q(payee_account__icontains=keyword)
            )
        provider_username = self.request.query_params.get('provider_username')
        if provider_username:
            qs = qs.filter(user__username__icontains=provider_username)
        provider_nickname = self.request.query_params.get('provider_nickname')
        if provider_nickname:
            qs = qs.filter(user__nickname__icontains=provider_nickname)
        payee_name = self.request.query_params.get('payee_name')
        if payee_name:
            qs = qs.filter(payee_name__icontains=payee_name)
        payee_account = self.request.query_params.get('payee_account')
        if payee_account:
            qs = qs.filter(payee_account__icontains=payee_account)
        return qs

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        payout_reference = (request.data.get('payout_reference') or '').strip()[:100]
        if not payout_reference:
            return Response({'code': 400, 'msg': '请填写实际打款流水号/凭证编号'})
        with transaction.atomic():
            withdraw = WithdrawRequest.objects.select_for_update().get(pk=pk)
            if withdraw.status != WithdrawRequest.Status.PENDING:
                return Response({'code': 400, 'msg': '该提现已审核，无法重复操作'})

            wallet = Wallet.objects.select_for_update().get(user_id=withdraw.user_id)
            if wallet.frozen_amount < withdraw.amount:
                return Response({'code': 400, 'msg': '冻结金额异常，无法通过'})
            wallet.frozen_amount -= withdraw.amount
            wallet.save(update_fields=['frozen_amount'])

            if withdraw.transaction_id:
                Transaction.objects.filter(pk=withdraw.transaction_id).update(
                    status=Transaction.Status.SUCCESS,
                    remark=f'提现到账：{withdraw.get_payee_method_display()} {withdraw.payee_account}',
                )

            withdraw.status = WithdrawRequest.Status.APPROVED
            withdraw.payout_reference = payout_reference
            withdraw.paid_at = timezone.now()
            withdraw.auditor = request.user
            withdraw.audited_at = timezone.now()
            withdraw.save(update_fields=[
                'status', 'payout_reference', 'paid_at', 'auditor', 'audited_at',
            ])

        create_message(
            recipient_id=withdraw.user_id,
            title='提现审核通过',
            preview=f'提现 {withdraw.amount / 10:g} 兴安币已通过',
            detail=f'您申请的 {withdraw.amount / 10:g} 兴安币提现已审核通过，将结算至 '
                   f'{withdraw.get_payee_method_display()}（{withdraw.payee_account}）。',
        )
        return Response({'code': 0, 'data': self.get_serializer(withdraw).data, 'msg': '已通过'})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        audit_remark = (request.data.get('audit_remark') or '').strip()[:255]
        if not audit_remark:
            return Response({'code': 400, 'msg': '请填写驳回原因'})
        with transaction.atomic():
            withdraw = WithdrawRequest.objects.select_for_update().get(pk=pk)
            if withdraw.status != WithdrawRequest.Status.PENDING:
                return Response({'code': 400, 'msg': '该提现已审核，无法重复操作'})

            wallet = Wallet.objects.select_for_update().get(user_id=withdraw.user_id)
            if wallet.frozen_amount < withdraw.amount:
                return Response({'code': 400, 'msg': '冻结金额异常，无法驳回'})
            wallet.frozen_amount -= withdraw.amount
            wallet.balance += withdraw.amount
            wallet.save(update_fields=['frozen_amount', 'balance'])

            if withdraw.transaction_id:
                Transaction.objects.filter(pk=withdraw.transaction_id).update(
                    status=Transaction.Status.FAILED,
                    remark=f'提现驳回退回：{audit_remark}',
                )

            withdraw.status = WithdrawRequest.Status.REJECTED
            withdraw.audit_remark = audit_remark
            withdraw.auditor = request.user
            withdraw.audited_at = timezone.now()
            withdraw.save(update_fields=['status', 'audit_remark', 'auditor', 'audited_at'])

        create_message(
            recipient_id=withdraw.user_id,
            title='提现审核未通过',
            preview=f'提现 {withdraw.amount / 10:g} 兴安币被驳回',
            detail=f'驳回原因：{audit_remark}。已退回 {withdraw.amount / 10:g} 兴安币至您的余额。',
        )
        return Response({'code': 0, 'data': self.get_serializer(withdraw).data, 'msg': '已驳回'})


class WithdrawConfigView(APIView):
    """最低提现金额配置：GET 读取，PUT 修改（需 withdraw:audit）。"""

    permission_classes = [IsConsoleUser]

    def put(self, request):
        if not request.user.is_superuser and 'withdraw:audit' not in get_user_permissions(request.user):
            return Response({'code': 403, 'msg': '无操作权限'})
        try:
            min_amount = int(request.data.get('min_amount'))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请输入合法的最低提现兴安币'})
        if min_amount < 0:
            return Response({'code': 400, 'msg': '最低提现金额不能为负'})
        SystemConfig.objects.update_or_create(
            key=MIN_WITHDRAW_AMOUNT_KEY,
            defaults={'value': str(min_amount), 'remark': '最低提现兴安币（账务单位）'},
        )
        return Response({'code': 0, 'data': {'min_amount': min_amount}, 'msg': '最低提现金额已更新'})

    def get(self, request):
        return Response({'code': 0, 'data': {'min_amount': get_min_withdraw_amount()}})


# ---------------- 在线客服 ----------------
class ChatSessionViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """客服工作台：会话列表 / 会话内消息 / 回复 / 标记已读（共享会话池）。"""

    serializer_class = AdminChatSessionSerializer
    default_perm = 'chat:view'
    required_perms = {
        'reply': 'chat:reply',
        'read': 'chat:reply',
    }

    def get_queryset(self):
        qs = ChatSession.objects.select_related('user').order_by('-last_message_at', '-created_at')
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(user__username__icontains=keyword)
                | Q(user__nickname__icontains=keyword)
            )
        only_unread = self.request.query_params.get('only_unread')
        if only_unread in ('1', 'true', 'True'):
            qs = qs.filter(unread_support__gt=0)
        return qs

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        session = self.get_object()
        messages = session.messages.select_related('sender').order_by('created_at')
        data = AdminChatMessageSerializer(
            messages, many=True, context={'request': request}
        ).data
        return Response({'code': 0, 'data': {'list': data, 'total': len(data)}})

    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        content = (request.data.get('content') or '').strip()
        image = request.FILES.get('image')
        if not content and not image:
            return Response({'code': 400, 'msg': '回复内容不能为空'})

        content_type = (
            ChatMessage.ContentType.IMAGE if image else ChatMessage.ContentType.TEXT
        )
        with transaction.atomic():
            session = ChatSession.objects.select_for_update().get(pk=pk)
            message = ChatMessage.objects.create(
                session=session,
                sender=request.user,
                is_from_support=True,
                content_type=content_type,
                content=content,
                image=image,
            )
            session.last_message = message.preview
            session.last_message_at = message.created_at
            session.unread_user += 1
            session.unread_support = 0
            session.save(update_fields=[
                'last_message', 'last_message_at', 'unread_user', 'unread_support', 'updated_at',
            ])
            session.messages.filter(is_from_support=False, is_read=False).update(is_read=True)

        notify_chat_message(session, message)
        return Response({
            'code': 0,
            'data': AdminChatMessageSerializer(message, context={'request': request}).data,
            'msg': '已发送',
        })

    @action(detail=True, methods=['post'])
    def read(self, request, pk=None):
        with transaction.atomic():
            session = ChatSession.objects.select_for_update().get(pk=pk)
            session.messages.filter(is_from_support=False, is_read=False).update(is_read=True)
            session.unread_support = 0
            session.save(update_fields=['unread_support', 'updated_at'])
        notify_chat_session_update(session)
        return Response({'code': 0, 'data': {'unread_support': 0}})


# ---------------- 优惠券 ----------------
class CouponViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminCouponSerializer
    default_perm = 'coupon:view'
    required_perms = {
        'create': 'coupon:edit',
        'update': 'coupon:edit',
        'partial_update': 'coupon:edit',
        'destroy': 'coupon:delete',
    }

    def get_queryset(self):
        qs = Coupon.objects.all().order_by('sort_order', '-created_at')
        is_active = self.request.query_params.get('is_active')
        if is_active is not None and is_active != '':
            qs = qs.filter(is_active=_truthy(is_active))
        return qs


class UserCouponViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminUserCouponSerializer
    default_perm = 'coupon:view'

    def get_queryset(self):
        qs = UserCoupon.objects.select_related('user', 'coupon').order_by('-claimed_at')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        coupon_id = self.request.query_params.get('coupon')
        if coupon_id:
            qs = qs.filter(coupon_id=coupon_id)
        return qs


# ---------------- 公告 ----------------
class AnnouncementViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminAnnouncementSerializer
    default_perm = 'announcement:view'
    required_perms = {
        'create': 'announcement:edit',
        'update': 'announcement:edit',
        'partial_update': 'announcement:edit',
        'destroy': 'announcement:delete',
    }

    def get_queryset(self):
        return Announcement.objects.all()


# ---------------- Banner ----------------
class BannerViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminBannerSerializer
    default_perm = 'banner:view'
    required_perms = {
        'create': 'banner:edit',
        'update': 'banner:edit',
        'partial_update': 'banner:edit',
        'destroy': 'banner:delete',
    }

    def get_queryset(self):
        return Banner.objects.all()

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        return ctx


# ---------------- 成就 ----------------
class AchievementViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminAchievementSerializer
    default_perm = 'achievement:view'
    required_perms = {
        'create': 'achievement:edit',
        'update': 'achievement:edit',
        'partial_update': 'achievement:edit',
        'destroy': 'achievement:delete',
    }

    def get_queryset(self):
        return Achievement.objects.all()


class CheckinRuleConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        rule, _ = CheckinRuleConfig.objects.get_or_create(pk=1)
        return Response({'code': 0, 'data': AdminCheckinRuleSerializer(rule).data})

    def put(self, request):
        if not request.user.is_superuser and 'checkin:edit' not in get_user_permissions(request.user):
            return Response({'code': 403, 'msg': '无操作权限'})
        rule, _ = CheckinRuleConfig.objects.get_or_create(pk=1)
        serializer = AdminCheckinRuleSerializer(rule, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'code': 0, 'data': serializer.data, 'msg': '签到规则已更新'})


class CheckinGiftViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminCheckinGiftSerializer
    default_perm = 'checkin:view'
    required_perms = {
        'create': 'checkin:edit',
        'update': 'checkin:edit',
        'partial_update': 'checkin:edit',
        'destroy': 'checkin:edit',
    }

    def get_queryset(self):
        return CheckinGift.objects.all()


class CheckinProgressViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminCheckinProgressSerializer
    default_perm = 'checkin:view'

    def get_queryset(self):
        qs = CheckinMonthProgress.objects.select_related('user')
        year = self.request.query_params.get('year')
        month = self.request.query_params.get('month')
        full = self.request.query_params.get('full_attendance')
        if year:
            qs = qs.filter(year=year)
        if month:
            qs = qs.filter(month=month)
        if full in ('1', 'true', 'True'):
            qs = qs.filter(full_attendance_awarded=True)
        return qs


# ---------------- 服务项/礼物单 ----------------
class ServiceItemViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminServiceItemSerializer
    default_perm = 'service:view'
    required_perms = {
        'create': 'service:edit',
        'update': 'service:edit',
        'partial_update': 'service:edit',
        'destroy': 'service:delete',
    }

    def get_queryset(self):
        qs = (
            ServiceItem.objects
            .select_related('game_category', 'service_category')
            .order_by('sort_order', 'id')
        )
        game_category = self.request.query_params.get('game_category')
        if game_category:
            qs = qs.filter(game_category_id=game_category)
        service_category = self.request.query_params.get('service_category')
        if service_category:
            qs = qs.filter(service_category_id=service_category)
        return qs


# ---------------- 游戏类目 / 分类 字典 ----------------
class GameCategoryViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminGameCategorySerializer
    default_perm = 'service:view'
    required_perms = {
        'create': 'service:edit',
        'update': 'service:edit',
        'partial_update': 'service:edit',
        'destroy': 'service:delete',
    }

    def get_queryset(self):
        return GameCategory.objects.all().order_by('sort_order', 'id')


class ServiceCategoryViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminServiceCategorySerializer
    default_perm = 'service:view'
    required_perms = {
        'create': 'service:edit',
        'update': 'service:edit',
        'partial_update': 'service:edit',
        'destroy': 'service:delete',
    }

    def get_queryset(self):
        return ServiceCategory.objects.all().order_by('sort_order', 'id')


# ---------------- 老板分级 ----------------
class BossTypeViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminBossTypeSerializer
    default_perm = 'boss_type:view'
    required_perms = {
        'create': 'boss_type:edit',
        'update': 'boss_type:edit',
        'partial_update': 'boss_type:edit',
        'destroy': 'boss_type:delete',
    }

    def get_queryset(self):
        return BossType.objects.all().order_by('sort_order', 'id')


# ---------------- 陪玩等级 ----------------
class EscortLevelViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminEscortLevelSerializer
    default_perm = 'escort_level:view'
    required_perms = {
        'create': 'escort_level:edit',
        'update': 'escort_level:edit',
        'partial_update': 'escort_level:edit',
        'destroy': 'escort_level:delete',
    }

    def get_queryset(self):
        qs = EscortLevel.objects.all().order_by('sort_order', 'id')
        is_active = self.request.query_params.get('is_active')
        if is_active is not None and is_active != '':
            qs = qs.filter(is_active=_truthy(is_active))
        return qs


# ---------------- 促销活动 ----------------
class PromotionViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminPromotionSerializer
    default_perm = 'promotion:view'
    required_perms = {
        'create': 'promotion:edit',
        'update': 'promotion:edit',
        'partial_update': 'promotion:edit',
        'destroy': 'promotion:delete',
    }

    def get_queryset(self):
        qs = Promotion.objects.prefetch_related('items').all()
        is_active = self.request.query_params.get('is_active')
        if is_active is not None and is_active != '':
            qs = qs.filter(is_active=_truthy(is_active))
        scope = self.request.query_params.get('scope')
        if scope:
            qs = qs.filter(scope=scope.upper())
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(Q(title__icontains=keyword) | Q(remark__icontains=keyword))
        return qs


# ---------------- 客服名片 ----------------
class SupportCardViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminSupportCardSerializer
    default_perm = 'support:view'
    required_perms = {
        'create': 'support:edit',
        'update': 'support:edit',
        'partial_update': 'support:edit',
        'destroy': 'support:delete',
    }

    def get_queryset(self):
        return SupportContactCard.objects.all()


# ---------------- 试音链接 ----------------
class AuditionLinkViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminAuditionLinkSerializer
    default_perm = 'audition:view'
    required_perms = {
        'create': 'audition:edit',
        'update': 'audition:edit',
        'partial_update': 'audition:edit',
        'destroy': 'audition:delete',
    }

    def get_queryset(self):
        qs = AuditionLink.objects.select_related('operator', 'boss_user', 'provider_user').all()
        is_active = self.request.query_params.get('is_active')
        if is_active is not None and is_active != '':
            qs = qs.filter(is_active=_truthy(is_active))
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(Q(title__icontains=keyword) | Q(remark__icontains=keyword))
        return qs

    def perform_create(self, serializer):
        serializer.save(operator=self.request.user)


# ---------------- 试音报名 ----------------
class AuditionSignupViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminAuditionSignupSerializer
    default_perm = 'audition:signup_view'
    required_perms = {
        'approve': 'audition:signup_audit',
        'reject': 'audition:signup_audit',
    }

    def get_queryset(self):
        qs = AuditionSignup.objects.select_related('link', 'applicant', 'auditor').order_by('-created_at')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        link_id = self.request.query_params.get('link')
        if link_id:
            qs = qs.filter(link_id=link_id)
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(game__icontains=keyword)
                | Q(contact__icontains=keyword)
                | Q(applicant__username__icontains=keyword)
                | Q(applicant__nickname__icontains=keyword)
            )
        return qs

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        with transaction.atomic():
            signup = AuditionSignup.objects.select_for_update().get(pk=pk)
            if signup.status != AuditionSignup.Status.PENDING:
                return Response({'code': 400, 'msg': '该报名已审核，无法重复操作'})
            signup.status = AuditionSignup.Status.APPROVED
            signup.auditor = request.user
            signup.audited_at = timezone.now()
            signup.save(update_fields=['status', 'auditor', 'audited_at'])

        create_message(
            recipient_id=signup.applicant_id,
            title='试音报名通过',
            preview=f'您报名的「{signup.link.title}」已通过审核',
            detail=f'恭喜！您报名的试音活动「{signup.link.title}」已通过审核，请留意客服后续联系。',
        )
        return Response({'code': 0, 'data': self.get_serializer(signup).data, 'msg': '已通过'})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        audit_remark = (request.data.get('audit_remark') or '').strip()[:255]
        if not audit_remark:
            return Response({'code': 400, 'msg': '请填写驳回原因'})
        with transaction.atomic():
            signup = AuditionSignup.objects.select_for_update().get(pk=pk)
            if signup.status != AuditionSignup.Status.PENDING:
                return Response({'code': 400, 'msg': '该报名已审核，无法重复操作'})
            signup.status = AuditionSignup.Status.REJECTED
            signup.audit_remark = audit_remark
            signup.auditor = request.user
            signup.audited_at = timezone.now()
            signup.save(update_fields=['status', 'audit_remark', 'auditor', 'audited_at'])

        create_message(
            recipient_id=signup.applicant_id,
            title='试音报名未通过',
            preview=f'您报名的「{signup.link.title}」未通过审核',
            detail=f'很遗憾，您报名的试音活动「{signup.link.title}」未通过。原因：{audit_remark}',
        )
        return Response({'code': 0, 'data': self.get_serializer(signup).data, 'msg': '已驳回'})


# ---------------- 站内消息 ----------------
class MessageViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminMessageSerializer
    default_perm = 'message:view'
    required_perms = {'push': 'message:push'}

    def get_queryset(self):
        qs = Message.objects.select_related('recipient').order_by('-created_at')
        msg_type = self.request.query_params.get('type')
        if msg_type:
            qs = qs.filter(type=msg_type.upper())
        recipient = self.request.query_params.get('recipient')
        if recipient:
            qs = qs.filter(recipient_id=recipient)
        return qs

    @action(detail=False, methods=['post'])
    def push(self, request):
        title = (request.data.get('title') or '').strip()
        if not title:
            return Response({'code': 400, 'msg': '请填写标题'})
        preview = (request.data.get('preview') or '').strip()
        detail = request.data.get('detail') or ''
        msg_type = (request.data.get('type') or 'SYSTEM').upper()
        broadcast = _truthy(request.data.get('broadcast', False))
        audience = (request.data.get('audience') or '').upper()
        recipient_ids = request.data.get('recipient_ids') or []

        if broadcast:
            targets = list(User.objects.filter(is_active=True).values_list('id', flat=True))
        elif audience in (User.Role.CUSTOMER, User.Role.PROVIDER):
            targets = list(User.objects.filter(is_active=True, role=audience).values_list('id', flat=True))
        else:
            if not recipient_ids:
                return Response({'code': 400, 'msg': '请选择接收人或勾选全员广播'})
            targets = list(recipient_ids)

        count = 0
        for uid in targets:
            if create_message(
                recipient_id=uid, title=title, preview=preview,
                detail=detail, msg_type=msg_type,
            ):
                count += 1
        return Response({'code': 0, 'msg': f'已推送 {count} 条', 'data': {'count': count}})


# ---------------- 角色 ----------------
class RoleViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminRoleSerializer
    default_perm = 'role:view'
    required_perms = {
        'create': 'role:edit',
        'update': 'role:edit',
        'partial_update': 'role:edit',
        'destroy': 'role:delete',
    }

    def get_queryset(self):
        return AdminRole.objects.all()

    def perform_destroy(self, instance):
        if instance.members.exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError('该角色下仍有管理员，无法删除')
        instance.delete()


class PermissionTreeView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        return Response({'code': 0, 'data': PERMISSION_GROUPS})


# ---------------- 管理员 / 客服 ----------------
class AdminMembershipViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminMembershipSerializer
    default_perm = 'admin:view'
    required_perms = {
        'create': 'admin:edit',
        'update': 'admin:edit',
        'partial_update': 'admin:edit',
        'destroy': 'admin:edit',
    }

    def get_queryset(self):
        # 今日派单额：该客服当日通过代派单生成的订单金额合计（按 CREATE 操作日志归属）
        today = timezone.localdate()
        dispatch_sq = (
            OrderStatusLog.objects.filter(
                operator_id=OuterRef('user_id'),
                action=OrderStatusLog.Action.CREATE,
                created_at__date=today,
            )
            .values('operator_id')
            .annotate(total=Sum('order__amount'))
            .values('total')
        )
        qs = (
            AdminMembership.objects.select_related('user', 'role')
            .annotate(
                today_dispatch_amount=Coalesce(
                    Subquery(dispatch_sq, output_field=IntegerField()), 0
                )
            )
        )
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(user__username__icontains=keyword)
                | Q(user__nickname__icontains=keyword)
                | Q(user__phone__icontains=keyword)
            )
        return qs

    def create(self, request, *args, **kwargs):
        serializer = AdminCreateMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            user = User.objects.create_user(
                username=data['username'],
                password=data['password'],
                nickname=data.get('nickname', ''),
                phone=data.get('phone') or None,
                role=User.Role.ADMIN,
                is_staff=True,
            )
            membership = AdminMembership.objects.create(
                user=user,
                role=data['role'],
                remark=data.get('remark', ''),
            )
        membership = self.get_queryset().get(pk=membership.pk)
        return Response(
            {'code': 0, 'data': AdminMembershipSerializer(membership).data},
            status=201,
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        user = instance.user
        data = request.data

        with transaction.atomic():
            user_fields = []
            if 'nickname' in data:
                user.nickname = (data.get('nickname') or '').strip()[:50]
                user_fields.append('nickname')
            if 'phone' in data:
                user.phone = (data.get('phone') or '').strip()[:20] or None
                user_fields.append('phone')
            if user_fields:
                user.save(update_fields=user_fields)

            membership_fields = []
            if 'remark' in data:
                instance.remark = (data.get('remark') or '').strip()[:255]
                membership_fields.append('remark')
            # 超管的角色与启用状态受保护，不可通过本接口变更
            if not user.is_superuser:
                if 'role' in data and data.get('role'):
                    try:
                        instance.role = AdminRole.objects.get(pk=data['role'])
                    except AdminRole.DoesNotExist:
                        return Response({'code': 400, 'msg': '角色不存在'})
                    membership_fields.append('role')
                if 'is_active' in data:
                    instance.is_active = _truthy(data.get('is_active'))
                    membership_fields.append('is_active')
            if membership_fields:
                instance.save(update_fields=membership_fields)

        instance = self.get_queryset().get(pk=instance.pk)
        return Response({'code': 0, 'data': AdminMembershipSerializer(instance).data})

    def perform_destroy(self, instance):
        if instance.user.is_superuser:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('超级管理员不可删除')
        instance.delete()
