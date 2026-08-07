from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from announcements.models import Announcement
from audition.models import AuditionLink, AuditionSignup
from banners.models import Banner
from chat.models import ChatMessage, ChatSession
from chat.notifier import notify_chat_message, notify_chat_session_update
from club_accounts.models import ClubAccount, LegacyAccountMap
from console.models import AdminAuditLog, AdminMembership, AdminRole
from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
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
from orders.settlement import (
    SettlementError,
    aggregate_order_split,
    build_provider_shares,
    compute_split,
)
from orders.state_machine import IllegalTransitionError, log_only, transition
# 结算 / 派单副作用统一走 orders.services（领域服务层）。
# 后台曾经直接 import orders.views 的私有函数：一个 app 的视图层反向依赖
# 另一个 app 的视图层，既绕不开循环导入的风险，也让「顾客端改个视图把后台改挂」
# 成为常态。
from orders.services import (
    ACTIVE_ORDER_STATUSES,
    active_orders_for,
    count_active_orders,
    mark_escort_busy,
    pending_timeout_deadline,
    push_message_safe as _push_message_safe,
    refresh_escort_status,
    schedule_auto_cancel as _schedule_auto_cancel,
    settle_order,
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
    WITHDRAW_TAX_RATE_KEY,
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
    get_withdraw_tax_rate,
)
from wallet.services import WalletAddressingError, get_wallet
from common.media import build_media_url

from .ban_utils import active_ban_for, apply_ban, lift_ban
from .mixins import EnvelopeViewSetMixin
from .models import AccountBan, AdminAuditLog, AdminMembership, AdminRole
from .permissions import (
    PERMISSION_GROUPS,
    IsConsoleUser,
    get_account_permissions,
    get_user_permissions,
)
from .serializers import (
    AdminAchievementSerializer,
    AdminAnnouncementSerializer,
    AdminAuditLogSerializer,
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
    BanActionSerializer,
    BanRecordSerializer,
)

User = get_user_model()


def _legacy_user_for_account(account):
    mapping = getattr(account, 'legacy_mapping', None)
    if mapping is None:
        raise User.DoesNotExist
    return User.objects.get(pk=mapping.legacy_user_id)


def _business_account(account_id, account_type):
    """Resolve a business account, accepting a legacy ID during test cut-over."""
    if getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False):
        mapping = LegacyAccountMap.objects.select_related('account').filter(
            legacy_user_id=account_id,
            account__account_type=account_type,
        ).first()
        if mapping is not None:
            return mapping.account
    account = ClubAccount.objects.filter(
        id=account_id,
        account_type=account_type,
    ).first()
    if account is not None or not getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False):
        return account
    role = (
        User.Role.CUSTOMER
        if account_type == ClubAccount.AccountType.BOSS
        else User.Role.PROVIDER
    )
    legacy_user = User.objects.filter(id=account_id, role=role).first()
    if legacy_user is None:
        return None
    account = get_or_create_account_for_legacy_user(legacy_user)
    link_legacy_relations(legacy_user, account)
    return account


def _legacy_user_id_from_account_id(account_id):
    """把 ClubAccount.id 单向归一为 legacy ``CustomUser.id``（经 ``LegacyAccountMap``）。

    ``UserViewSet`` 列表返回的是 ``ClubAccount.id``，而充值 / 奖惩 / 记录落库用的是
    legacy ``CustomUser.id``。两套 ID 空间都是小整数自增，**bare integer 无法区分**——
    若把前端传入的标识当"可能是 ClubAccount.id 也可能是 legacy id"去猜，就会错充到
    恰好同号的另一位用户（两套 ID 空间错充）。因此前端必须显式用 ``account_id`` 字段
    透传 ClubAccount.id，本函数只做 ClubAccount.id → legacy 的**单向**映射，绝不反向
    把 bare integer 当 legacy 猜。

    * ``ClubAccount`` 存在且有映射：返回 ``legacy_user_id``；
    * ``ClubAccount`` 存在但缺映射：尝试用同号 legacy 用户自愈补链（仅当该 legacy 用户存在）；
    * ``ClubAccount`` 不存在或无对应 legacy 用户：返回 ``None``，由调用方判 404。
    """
    if account_id is None:
        return None
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return None
    account = ClubAccount.objects.filter(id=account_id).first()
    if account is None:
        return None
    mapping = LegacyAccountMap.objects.filter(account_id=account.id).first()
    if mapping is not None:
        return mapping.legacy_user_id
    legacy = User.objects.filter(id=account_id).first()
    if legacy is not None:
        link_legacy_relations(legacy, account)
        return legacy.id
    return None


def _truthy(value):
    return str(value).lower() in ('1', 'true', 'yes')


def _resolve_provider_report_commission(provider):
    """解析报单审核抽成率：陪玩等级优先，否则回退全局配置。"""
    profile = EscortProfile.objects.filter(user=provider).select_related('level').first()
    level = getattr(profile, 'level', None)
    if level is not None and level.is_active:
        return level.commission_rate, f'等级抽成（{level.name}）'
    return get_commission_rate(), '全局默认'


def _resolve_report_order_share(report):
    """取报单人在该订单中「自己那条」结算明细（OrderProvider）。

    双陪订单下 ``order.provider_income`` 是两名打手的汇总值，直接拿它回填
    报单的 ``payout_amount`` 会让副陪看到的审核金额远大于钱包实际到账。
    优先按 ClubAccount 维度定位，回落 legacy user 维度；老单没有明细行时
    返回 None，由调用方回退订单级字段（单陪场景两者等价）。
    """
    shares = report.order.providers.all()
    if report.provider_account_id:
        share = next(
            (s for s in shares if s.provider_account_id == report.provider_account_id), None
        )
        if share is not None:
            return share
    if report.provider_id:
        share = next((s for s in shares if s.provider_id == report.provider_id), None)
        if share is not None:
            return share
    return None


# 结算前的凭证闸门实现已移至 orders.services.require_order_evidence，
# 并由 settle_order 内部统一调用，本模块不再直接引用。


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
                'user_total': ClubAccount.objects.count(),
                'customer_total': ClubAccount.objects.filter(
                    account_type=ClubAccount.AccountType.BOSS,
                ).count(),
                'provider_total': ClubAccount.objects.filter(
                    account_type=ClubAccount.AccountType.PROVIDER,
                ).count(),
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
        if 'player_dashboard:view' not in get_user_permissions(request.account):
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


# ---------------- 封禁 / 解封（老板与陪玩共用） ----------------
# 在途订单数超过这个上限就不再逐条列号，避免把几百个单号糊到前端提示框里。
_BAN_BLOCKING_ORDER_SAMPLE = 20


def _actor_has_perm(request, code):
    """判断当前请求者是否持有某权限点。

    与 ``HasConsolePerm`` 同源：超管在 legacy 兼容开关下直接放行，
    其余走 ``get_account_permissions``。用于 action 内部的**二次**细粒度判定
    （例如 force 强制封禁需要比进入 action 更高的权限）。
    """
    if (
        getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
        and getattr(request.user, 'is_superuser', False)
    ):
        return True
    account = getattr(request, 'account', None)
    if account is None:
        return False
    return code in get_account_permissions(account)


def _summarize_orders(queryset):
    """把在途订单查询集汇总成「真实总数 + 采样列表」。

    总数与采样必须分开算：拿 ``len(采样)`` 当总数，在超过 20 笔时会给运营
    一个错的数字（真有 25 笔却提示「还有 20 笔」）。

    Returns:
        tuple[int, list[tuple[int, str]]]: ``(真实总数, 采样的 (id, order_no) 列表)``，
            采样最多 ``_BAN_BLOCKING_ORDER_SAMPLE`` 条。
    """
    total = queryset.count()
    if not total:
        return 0, []
    sample = list(
        queryset.order_by('-created_at')
        .values_list('id', 'order_no')[:_BAN_BLOCKING_ORDER_SAMPLE]
    )
    return total, sample


def _provider_active_orders(account):
    """账户作为**陪玩**的在途订单。

    口径完全复用 ``orders.services.active_orders_for``，双陪订单的
    ``OrderProvider`` 次陪维度也在内，不在这里重写 Q 条件。
    """
    return _summarize_orders(active_orders_for(account))


def _customer_active_orders(account):
    """账户作为**顾客（老板）**的在途订单。

    仅用于封禁成功后回传 ``orphaned_order_*`` 提示，**不参与阻断**。
    """
    return _summarize_orders(
        Order.objects.filter(
            customer_account=account, status__in=ACTIVE_ORDER_STATUSES,
        )
    )


def _capacity_reject_reason(account_id, *, display_name='', exclude_order_ids=()):
    """产能闸门：把一笔订单交给该陪玩前问一句「他还接得下吗」。

    陪玩端自助抢单（``orders.views.GrabOrderView``）和 C 端派单
    (``orders.views``) 都查 :attr:`Order.MAX_CONCURRENT_ORDERS`，唯独后台的
    三条派单入口（指派 / 转单 / 快捷派单）从来不查——客服可以把第 4、第 5 单
    压给同一个人，绕过全站唯一的产能保护。这个 helper 就是给那三条补上的，
    口径与 ``orders.services.count_active_orders`` 同源，不另写 Q 条件。

    Args:
        account_id: 目标陪玩的 ``ClubAccount`` 主键。
        display_name: 用于拼报错文案的展示名；留空回落「该陪玩」。
        exclude_order_ids: 计数时排除的订单。**调用方几乎总要传当前这笔单**，
            原因见下。

    为什么必须能排除当前订单：``active_orders_for`` 的条件是
    ``provider_account`` 外键 **或** ``OrderProvider`` 中间表命中，转单场景下
    转入方很可能**已经在本单里**（双陪把 A 转给同单的 B；或同一批次把多名旧
    打手转给同一个新人——``transfer`` 的 ``seen_new`` 只 add 不查重）。不排除
    本单就会把「他已经在跑的这一单」当成「他又要多背一单」，误判超限。排除后
    语义收敛成一句话：**他在别的单上是不是已经占满了**。

    为什么判定是 ``>=`` 而不是 ``>``：``others`` 是排除本单后的数量，接下这单
    之后总数是 ``others + 1``；要求 ``others + 1 <= MAX`` 即 ``others < MAX``，
    取反就是 ``others >= MAX``。

    **已知偏差，别以为这个计数口径是完备的（ORD-5 处理）**：
    ``active_orders_for`` 的 ``providers__`` 分支只看订单状态、**不看**
    ``OrderProvider.settled_at``。转单时旧打手只是被置上 ``settled_at``，
    中间表的行仍在库、订单仍是 ``IN_SERVICE``，于是他退出本单之后名额**不会
    释放**——这个人被虚占一个产能位，直到整单结束。方向上偏保守（会误拒、
    不会击穿），所以不构成资损，但状态线（已结算退出）与计数线（仍算在途）
    自相矛盾。修法是在那个分支加 ``settled_at__isnull=True``，但会改动 ORD-4
    已上线的封禁在途检查口径，混在产能这批里等于让已验收的功能重新进入未验证
    状态，故单列 ORD-5。**在 ORD-5 落地前不要在这里自行"顺手修一下"。**

    Returns:
        str | None: 还接得下返回 ``None``；超限返回给客服看的拒绝理由。
    """
    others = count_active_orders(
        account_id=account_id, exclude_order_ids=exclude_order_ids,
    )
    if others < Order.MAX_CONCURRENT_ORDERS:
        return None
    who = display_name or '该陪玩'
    return (
        f'{who}已达同时进行 {Order.MAX_CONCURRENT_ORDERS} 单上限'
        f'（当前在途 {others} 单）'
    )


def _do_ban(request, account, *, role):
    """执行封禁的公共流程，返回 DRF ``Response``。

    Args:
        request: DRF 请求。
        account: 被封禁的 ``ClubAccount``。
        role: ``'provider'`` 或 ``'boss'``，决定在途订单查哪个维度、是否阻断。

    两侧行为**有意不同**：

    - ``provider``：查陪玩维度（含双陪次席），有在途就**硬阻断 409**；
    - ``boss``：查顾客维度，**不阻断**，照常封禁，只在成功响应里回传
      ``orphaned_order_*``，让客服当场看见自己刚让哪几单失去了顾客侧。

    老板侧不阻断是刻意的，不是漏了，理由记在这里免得后人「补全」回去：

    1. 用户拍板的硬阻断只针对「有在途订单的**陪玩**」，扩到老板是自行加需求；
    2. 封老板的典型场景是欺诈/盗刷/拒付，客服要的是立刻生效。老板侧频繁弹 409
       会把客服训练成无脑勾 force，**连带陪玩侧那道真正重要的闸门一起失效**。
       一道所有人都习惯性绕过的闸门比没有闸门更糟；
    3. 老板只要挂一笔单不结就能让自己封不掉，等于给作恶者护身符。

    信息给到人，决策权留给人，不制造习惯化。

    其余规则：
      - 原因必填，空则 400；
      - 带 ``force=true`` 可跳过在途拦截，但需额外持有 ``user:ban_force``，
        否则 403。注意「有没有 force 权限」必须在「有没有在途订单」之前判，
        否则无权限者能靠观察 409 与 403 的响应差异探出目标是否在接单；
      - 重复封禁不报错：apply_ban 会收口旧记录并继承封禁前快照。

    真实 HTTP 状态码而非仅 envelope code：AdminAuditLog 中间件只对
    ``status_code < 400`` 落审计，被拦截的封禁本就没发生，不该留下成功流水。
    """
    payload = BanActionSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    reason = (payload.validated_data.get('reason') or '').strip()
    if not reason:
        return Response({'code': 400, 'msg': '封禁原因必填'}, status=400)
    expires_at = payload.validated_data.get('expires_at')
    if expires_at is not None and expires_at <= timezone.now():
        return Response({'code': 400, 'msg': '自动解封时间必须晚于当前时间'}, status=400)

    force = _truthy(request.data.get('force', False))
    if force and not _actor_has_perm(request, 'user:ban_force'):
        return Response(
            {'code': 403, 'msg': '无强制封禁权限（user:ban_force）'}, status=403,
        )

    # 无论拦不拦，都要先把在途情况算出来：拦的时候用来报错，不拦的时候用来
    # 在成功响应里告诉客服「你刚才让这几单没了对手方」。
    if role == 'provider':
        active_count, active_sample = _provider_active_orders(account)
        block_on_active = True
    else:
        active_count, active_sample = _customer_active_orders(account)
        block_on_active = False

    if block_on_active and not force and active_count:
        return Response({
            'code': 409,
            'msg': f'该账户还有 {active_count} 笔在途订单，请先处理完再封禁',
            'data': {
                'active_order_count': active_count,
                'active_order_ids': [row[0] for row in active_sample],
                'active_order_nos': [row[1] for row in active_sample],
                # 超过采样上限时明确告知列表被截断，别让运营以为就这几单。
                'truncated': active_count > len(active_sample),
            },
        }, status=409)

    try:
        ban = apply_ban(
            account,
            reason=reason,
            operator=getattr(request, 'legacy_user', None),
            operator_account=getattr(request, 'account', None),
            expires_at=expires_at,
        )
    except ValueError as exc:
        return Response({'code': 400, 'msg': str(exc)}, status=400)

    data = BanRecordSerializer(ban).data
    if active_count:
        # 封禁已生效，但这几单的一侧当事人刚被锁掉，交给人去善后。
        data['orphaned_order_count'] = active_count
        data['orphaned_order_ids'] = [row[0] for row in active_sample]
        data['orphaned_order_nos'] = [row[1] for row in active_sample]
        data['orphaned_truncated'] = active_count > len(active_sample)
    return Response({'code': 0, 'data': data})


def _do_unban(request, account):
    """执行解封的公共流程，返回 DRF ``Response``。

    幂等：账户当前没有生效封禁时返回成功（code 0），不报错——运营重复点两次
    「解封」不该看到红色报错，且并发下第二次请求本就无事可做。
    """
    payload = BanActionSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    reason = (payload.validated_data.get('reason') or '').strip()

    ban = active_ban_for(account)
    if ban is None:
        return Response({'code': 0, 'data': None, 'msg': '该账户当前没有生效的封禁'})

    ban = lift_ban(
        ban,
        lifted_by=getattr(request, 'legacy_user', None),
        lifted_by_account=getattr(request, 'account', None),
        reason=reason,
    )
    return Response({'code': 0, 'data': BanRecordSerializer(ban).data})


class BanViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """封禁审计列表：只读，供后台追溯谁在何时因何封了谁。

    写入一律走 users/escorts 的 ban/unban action，这里不开任何写口子，
    否则「审计表」自己就能被改，审计也就没了意义。
    """

    serializer_class = BanRecordSerializer
    default_perm = 'user:view'

    def get_queryset(self):
        qs = AccountBan.objects.select_related(
            'account', 'operator', 'operator_account',
            'lifted_by', 'lifted_by_account',
        ).order_by('-banned_at')
        account_id = self.request.query_params.get('account_id')
        if account_id:
            qs = qs.filter(account_id=account_id)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        return qs


# ---------------- 用户 ----------------
class UserViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminUserSerializer
    # post 仅为 ban/unban 两个 action 开放；本 ViewSet 不提供 create。
    http_method_names = ['get', 'patch', 'put', 'post', 'head', 'options']
    default_perm = 'user:view'
    required_perms = {
        'update': 'user:edit',
        'partial_update': 'user:edit',
        'ban': 'user:ban',
        'unban': 'user:ban',
    }

    def create(self, request, *args, **kwargs):
        """显式关闭建号入口：老板账户由小程序注册产生，后台不许凭空造。"""
        return Response({'code': 405, 'msg': '不支持在后台创建老板账户'}, status=405)

    def get_queryset(self):
        # 老板信息管理：仅业务账户中的老板，不含陪玩和后台人员。
        qs = (
            ClubAccount.objects.filter(account_type=ClubAccount.AccountType.BOSS)
            .select_related('wallet', 'boss_type', 'inviter')
            .order_by('-created_at')
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

    @action(detail=True, methods=['post'])
    def ban(self, request, pk=None):
        """封禁老板账户：必须给原因，落 AccountBan 审计记录。

        老板侧**不做在途订单阻断**（理由见 ``_do_ban`` docstring），
        但会在成功响应里回传 orphaned_order_* 供客服善后。
        """
        return _do_ban(request, self.get_object(), role='boss')

    @action(detail=True, methods=['post'])
    def unban(self, request, pk=None):
        """解封老板账户：幂等，按封禁前快照回滚开关。"""
        return _do_unban(request, self.get_object())


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
        # 封禁权限点跨老板/陪玩共用一套，运营心智里「封人」就是一件事。
        'ban': 'user:ban',
        'unban': 'user:ban',
    }

    @action(detail=True, methods=['post'])
    def ban(self, request, pk=None):
        """封禁陪玩：作用于其绑定的业务账户。"""
        profile = self.get_object()
        if profile.account_id is None:
            return Response(
                {'code': 400, 'msg': '该陪玩尚未绑定业务账户，无法封禁'}, status=400,
            )
        return _do_ban(request, profile.account, role='provider')

    @action(detail=True, methods=['post'])
    def unban(self, request, pk=None):
        """解封陪玩：幂等，按封禁前快照回滚开关。"""
        profile = self.get_object()
        if profile.account_id is None:
            return Response(
                {'code': 400, 'msg': '该陪玩尚未绑定业务账户，无法解封'}, status=400,
            )
        return _do_unban(request, profile.account)

    def get_queryset(self):
        qs = EscortProfile.objects.select_related(
            'account', 'user',
        ).prefetch_related('game_categories', 'service_items').order_by('-created_at')
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
                | Q(account__username__icontains=keyword)
                | Q(account__phone__icontains=keyword)
            )
        return qs

    def create(self, request, *args, **kwargs):
        """客服后台为陪玩开户：建 PROVIDER 账号 + 陪玩档案（钱包由信号自动创建）。"""
        serializer = AdminCreateEscortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            account = ClubAccount.objects.create_account(
                username=data['username'],
                password=data['password'],
                nickname=data.get('nickname', ''),
                phone=data.get('phone') or None,
                account_type=ClubAccount.AccountType.PROVIDER,
                is_openid_bound=True,
            )
            user = User.objects.create_user(
                username=data['username'],
                password=data['password'],
                nickname=data.get('nickname', ''),
                phone=data.get('phone') or None,
                role=User.Role.PROVIDER,
                is_openid_bound=True,
            )
            LegacyAccountMap.objects.create(
                legacy_user_id=user.id,
                account=account,
                legacy_role=User.Role.PROVIDER,
            )
            profile = EscortProfile.objects.create(
                user=user,
                account=account,
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
            game_categories = data.get('game_categories')
            if game_categories:
                profile.game_categories.set(game_categories)
            service_items = data.get('service_items')
            if service_items:
                profile.service_items.set(service_items)
            # 头像同步回填到 CustomUser.avatar_url，供 C 端订单/评价展示
            if profile.avatar:
                avatar_url = build_media_url(request, profile.avatar)
                account.avatar_url = avatar_url
                account.save(update_fields=['avatar_url'])
                user.avatar_url = avatar_url
                user.save(update_fields=['avatar_url'])
            Wallet.objects.filter(user=user).update(account=account)
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
            wallet = get_wallet(
                user_id=profile.user_id,
                account_id=profile.account_id,
                for_update=True,
            )
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
                operator=request.legacy_user,
                operator_account=request.account,
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
        'transfer': 'order:dispatch',
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
            provider_account = ClubAccount.objects.select_related(
                'escort_profile__level', 'legacy_mapping',
            ).get(
                id=provider_id,
                account_type=ClubAccount.AccountType.PROVIDER,
            )
            provider = _legacy_user_for_account(provider_account)
        except (ClubAccount.DoesNotExist, User.DoesNotExist):
            return Response({'code': 404, 'msg': '陪玩不存在'})
        profile = getattr(provider_account, 'escort_profile', None)
        if profile is None or profile.status != EscortProfile.Status.AVAILABLE:
            return Response({'code': 400, 'msg': '该陪玩当前不可接单'})
        if not EscortSchedule.provider_is_scheduled_now(provider):
            return Response({'code': 400, 'msg': '该陪玩当前不在接单档期'})

        def pre_check(o):
            if o.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能派单')
            if o.escort_mode != Order.EscortMode.SINGLE:
                raise IllegalTransitionError('双陪订单请通过快捷派单创建并同时指定两名陪玩')
            if not profile.can_take_service(o.service):
                raise IllegalTransitionError('该服务要求更高的陪玩档位，所选陪玩不符合')
            # 产能闸门放在 pre_check 而不是上面的入参解析段：pre_check 由
            # transition() 在 @transaction.atomic + Order.select_for_update()
            # 之内回调，解析段在事务外。放外面等于「查完再等一会儿才落库」，
            # 中间任何一条并发派单都能把这个判断作废。
            reason = _capacity_reject_reason(
                provider_account.pk,
                display_name=profile.display_name,
                exclude_order_ids=[o.pk],
            )
            if reason:
                raise IllegalTransitionError(reason)

        def side_effect(o):
            o.provider = provider
            o.provider_account = provider_account
            o.provider_name_snapshot = profile.display_name or provider.nickname or provider.username
            mark_escort_busy(
                provider_ids=[provider.id],
                account_ids=[provider_account.pk],
            )
            # 本入口已在 pre_check 中限定为单陪订单，独一打手的结算基数
            # 就是订单全额；双陪必须走快捷派单，由 build_provider_shares 均分。
            OrderProvider.objects.get_or_create(
                order=o,
                provider=provider,
                provider_account=provider_account,
                defaults={
                    'provider_name_snapshot': o.provider_name_snapshot,
                    'settlement_base': o.amount,
                    'commission_type': OrderProvider.CommissionType.PERCENT,
                    'commission_rate': o.commission_rate,
                    'commission_fixed': 0,
                    'provider_income': o.provider_income,
                },
            )

        try:
            order = transition(
                order.id,
                Order.Status.GRABBED,
                operator=request.legacy_user,
                operator_account=request.account,
                action=OrderStatusLog.Action.ASSIGN,
                reason='客服指派陪玩',
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=['provider', 'provider_account', 'provider_name_snapshot'],
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

    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        """转单：服务中订单将部分或全部打手转交新打手。

        对每一名被转出的旧打手：按客服填写的比例/固定额，从其应得份额(provider_income)
        中立即结算入账其应得部分，剩余份额转给新打手（订单完成时随新打手结算）；
        同时对旧打手扣除罚款。旧打手余额（含本次刚结算入账的分钱）不足以扣罚款时，
        整笔转单失败并回滚。旧打手状态恢复可接单，新打手置忙碌。
        """
        order = self.get_object()
        if order.status != Order.Status.IN_SERVICE:
            return Response({'code': 400, 'msg': '仅服务中的订单可转单'})

        transfers = request.data.get('transfers')
        if not isinstance(transfers, list) or not transfers:
            return Response({'code': 400, 'msg': '请至少选择一名要转出的打手'})

        # ---- 解析并校验每条转单指令 ----
        parsed = []
        seen_old = set()
        seen_new = set()
        for item in transfers:
            old_provider_id = item.get('old_provider_id')
            new_provider_id = item.get('new_provider_id')
            if not old_provider_id or not new_provider_id:
                return Response({'code': 400, 'msg': '请完整选择转出与转入打手'})
            if old_provider_id in seen_old:
                return Response({'code': 400, 'msg': '同一打手不能重复转出'})
            if int(old_provider_id) == int(new_provider_id):
                return Response({'code': 400, 'msg': '新打手不能与原打手相同'})
            split_type = (item.get('split_type') or OrderProvider.CommissionType.PERCENT).upper()
            if split_type not in OrderProvider.CommissionType.values:
                return Response({'code': 400, 'msg': '分钱方式不合法'})
            try:
                split_value = int(item.get('split_value', 0))
                penalty = int(item.get('penalty_amount', 0))
            except (TypeError, ValueError):
                return Response({'code': 400, 'msg': '请输入合法的分钱比例/金额'})
            if split_value < 0 or penalty < 0:
                return Response({'code': 400, 'msg': '分钱与罚款金额不能为负'})
            if split_type == OrderProvider.CommissionType.PERCENT and split_value > 100:
                return Response({'code': 400, 'msg': '分钱比例不能超过 100%'})

            new_provider_account = ClubAccount.objects.select_related(
                'escort_profile__level', 'legacy_mapping',
            ).filter(
                id=new_provider_id,
                account_type=ClubAccount.AccountType.PROVIDER,
            ).first()
            if new_provider_account is None:
                return Response({'code': 404, 'msg': '转入打手不存在'})
            try:
                new_provider = _legacy_user_for_account(new_provider_account)
            except User.DoesNotExist:
                return Response({'code': 404, 'msg': '转入打手兼容映射不存在'})
            new_profile = getattr(new_provider_account, 'escort_profile', None)
            if new_profile is None or new_profile.status != EscortProfile.Status.AVAILABLE:
                return Response({'code': 400, 'msg': f'{new_provider.nickname or new_provider.username} 当前不可接单'})
            if not EscortSchedule.provider_is_scheduled_now(new_provider):
                return Response({'code': 400, 'msg': f'{new_provider.nickname or new_provider.username} 当前不在接单档期'})
            if not new_profile.can_take_service(order.service):
                return Response({'code': 400, 'msg': f'{new_provider.nickname or new_provider.username} 档位不满足该服务要求'})
            seen_old.add(old_provider_id)
            seen_new.add(int(new_provider_id))
            parsed.append({
                'old_provider_account_id': int(old_provider_id),
                'new_provider': new_provider,
                'new_provider_account': new_provider_account,
                'new_profile': new_profile,
                'split_type': split_type,
                'split_value': split_value,
                'penalty': penalty,
                'reason': (item.get('reason') or '').strip()[:255],
            })

        reason = (request.data.get('reason') or '转单').strip()[:255]

        try:
            with transaction.atomic():
                locked_order = Order.objects.select_for_update().get(pk=order.pk)
                if locked_order.status != Order.Status.IN_SERVICE:
                    raise IllegalTransitionError('订单状态已变更，无法转单')
                rows = {
                    row.provider_account_id: row
                    for row in locked_order.providers.select_for_update().all()
                    if row.provider_account_id
                }

                # ---- 产能闸门：转入方还接不接得下 ----
                #
                # 必须在锁内做：入参解析段（上面那个 for item in transfers）跑在
                # 事务外，那里查完到这里落库之间是敞开的。
                #
                # 这里用「已校验过的 account 集合」而不是 {account_id: 新增数}
                # 计数器，因为 transfer 是 detail action，整批指令作用于**同一笔**
                # locked_order —— 一个人在本批次里最多新增 1 单，不存在累加。
                # 同一个新打手被重复指定（seen_new 只 add 不查重，允许把多名旧
                # 打手转给同一人）也只占一个名额，跳过即可。
                #
                # 注意：转出方**不会**因为这次转出而腾出名额。转出只是把
                # old_row.settled_at 置位，OrderProvider 行仍在库、订单仍是
                # IN_SERVICE，而 active_orders_for 不看 settled_at。所以别指望
                # 用「转出释放的位子」抵扣转入的占用（转出转入本就是不同的人，
                # 名额也不通用）。
                capacity_checked = set()
                for cmd in parsed:
                    new_account_id = cmd['new_provider_account'].pk
                    if new_account_id in capacity_checked:
                        continue
                    capacity_checked.add(new_account_id)
                    new_profile = cmd['new_profile']
                    new_provider = cmd['new_provider']
                    reject = _capacity_reject_reason(
                        new_account_id,
                        display_name=(
                            new_profile.display_name
                            or new_provider.nickname
                            or new_provider.username
                        ),
                        exclude_order_ids=[locked_order.pk],
                    )
                    if reject:
                        raise IllegalTransitionError(reject)

                last_new_provider = None
                last_new_provider_account = None
                for cmd in parsed:
                    old_row = rows.get(cmd['old_provider_account_id'])
                    if old_row is None or old_row.settled_at:
                        raise IllegalTransitionError('所选原打手不在本单未结算打手中')
                    pool = old_row.provider_income
                    if cmd['split_type'] == OrderProvider.CommissionType.FIXED:
                        old_income = min(cmd['split_value'], pool)
                    else:
                        old_income = pool * cmd['split_value'] // 100
                    new_income = pool - old_income
                    # 结算基数同步拆分：旧行定格到它实拿的部分，剩余基数随份额
                    # 转给新行，保证全单 sum(settlement_base) 始终等于实付金额。
                    old_base_kept = min(old_income, old_row.settlement_base)
                    transferred_base = old_row.settlement_base - old_base_kept

                    # 旧打手：结算入账应得份额
                    old_wallet = get_wallet(
                        user_id=old_row.provider_id,
                        account_id=old_row.provider_account_id,
                        for_update=True,
                    )
                    if old_income:
                        before = old_wallet.balance
                        old_wallet.balance += old_income
                        old_wallet.save(update_fields=['balance'])
                        Transaction.objects.create(
                            wallet=old_wallet,
                            order=locked_order,
                            amount=old_income,
                            tx_type=Transaction.TxType.INCOME,
                            balance_before=before,
                            balance_after=old_wallet.balance,
                            remark=f'转单结算：{locked_order.order_no}',
                        )
                    # 旧打手：扣罚款（余额不足则整笔失败）
                    penalty = cmd['penalty']
                    if penalty:
                        if old_wallet.balance < penalty:
                            raise IllegalTransitionError('旧打手余额不足以扣除罚款，转单失败')
                        before = old_wallet.balance
                        old_wallet.balance -= penalty
                        old_wallet.save(update_fields=['balance'])
                        tx = Transaction.objects.create(
                            wallet=old_wallet,
                            order=locked_order,
                            amount=-penalty,
                            tx_type=Transaction.TxType.PENALTY,
                            balance_before=before,
                            balance_after=old_wallet.balance,
                            remark=cmd['reason'] or f'转单罚款：{locked_order.order_no}',
                        )
                        DisposeRecord.objects.create(
                            user_id=old_row.provider_id,
                            account_id=old_row.provider_account_id,
                            dispose_type=DisposeRecord.DisposeType.PENALTY,
                            amount=penalty,
                            reason=cmd['reason'] or '转单罚款',
                            operator=request.legacy_user,
                            operator_account=request.account,
                            transaction=tx,
                        )
                        old_profile = EscortProfile.objects.select_for_update().get(
                            account_id=old_row.provider_account_id,
                        )
                        old_profile.total_penalty += penalty
                        old_profile.save(update_fields=['total_penalty'])

                    # 旧打手行：定格已结算份额与对应基数，并标记已结算
                    old_row.provider_income = old_income
                    old_row.settlement_base = old_base_kept
                    old_row.settled_at = timezone.now()
                    old_row.save(update_fields=[
                        'provider_income', 'settlement_base', 'settled_at',
                    ])
                    # 旧打手退出本单：按他手上是否还有别的单来定状态，
                    # 不能一律置空闲（他可能同时在跑另一笔订单）。
                    refresh_escort_status(
                        account_ids=[old_row.provider_account_id],
                        provider_ids=[old_row.provider_id],
                        exclude_order_ids=[locked_order.pk],
                    )

                    # 新打手行：承接剩余份额与剩余基数，订单完成时结算。
                    # 基数必须跟着份额一起转移，不能再取整单金额，
                    # 否则 sum(settlement_base) 会膨胀，超额分账又会重新打开。
                    new_provider = cmd['new_provider']
                    new_name = cmd['new_profile'].display_name or new_provider.nickname or new_provider.username
                    new_row, created = OrderProvider.objects.get_or_create(
                        order=locked_order,
                        provider_account=cmd['new_provider_account'],
                        defaults={
                            'provider': new_provider,
                            'provider_name_snapshot': new_name,
                            'settlement_base': transferred_base,
                            'commission_type': OrderProvider.CommissionType.PERCENT,
                            'commission_rate': locked_order.commission_rate,
                            'provider_income': new_income,
                        },
                    )
                    if not created:
                        new_row.provider_income += new_income
                        new_row.settlement_base += transferred_base
                        new_row.settled_at = None
                        new_row.save(update_fields=[
                            'provider_income', 'settlement_base', 'settled_at',
                        ])
                    mark_escort_busy(
                        provider_ids=[new_provider.id],
                        account_ids=[cmd['new_provider_account'].pk],
                    )
                    last_new_provider = new_provider
                    last_new_provider_account = cmd['new_provider_account']

                # 订单主打手快照更新为最后转入的新打手
                if last_new_provider is not None:
                    locked_order.provider = last_new_provider
                    locked_order.provider_account = last_new_provider_account
                    profile = getattr(last_new_provider, 'escort_profile', None)
                    locked_order.provider_name_snapshot = (
                        (profile.display_name if profile else '')
                        or last_new_provider.nickname or last_new_provider.username
                    )
                    locked_order.save(update_fields=[
                        'provider', 'provider_account', 'provider_name_snapshot',
                    ])

                log_only(
                    locked_order,
                    action=OrderStatusLog.Action.TRANSFER,
                    operator=request.legacy_user,
                    operator_account=request.account,
                    reason=reason,
                )
                order = locked_order
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '转单失败'})

        notify_order_update(order)
        for cmd in parsed:
            _push_message_safe(
                recipient_id=rows[cmd['old_provider_account_id']].provider_id,
                title='订单已转出',
                preview=f'你的「{order.service_name_snapshot}」已转交其他大神，结算已到账',
                msg_type='ORDER',
                related_order_id=order.id,
            )
            _push_message_safe(
                recipient_id=cmd['new_provider'].id,
                title='客服转单给你',
                preview=f'客服为你转入「{order.service_name_snapshot}」，请尽快开始服务',
                msg_type='ORDER',
                related_order_id=order.id,
            )
        _push_message_safe(
            recipient_id=order.customer_id,
            title='已为你更换大神',
            preview=f'你的「{order.service_name_snapshot}」已更换服务大神',
            msg_type='ORDER',
            related_order_id=order.id,
        )
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '转单成功'})

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
                account_id=provider_id,
                account__account_type=ClubAccount.AccountType.PROVIDER,
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
            customer_account = _business_account(
                customer_id,
                ClubAccount.AccountType.BOSS,
            )
            if customer_account is None:
                raise ClubAccount.DoesNotExist
            customer = _legacy_user_for_account(customer_account)
        except (ClubAccount.DoesNotExist, User.DoesNotExist):
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
                provider_account = _business_account(
                    item['provider_id'],
                    ClubAccount.AccountType.PROVIDER,
                )
                if provider_account is None:
                    raise ClubAccount.DoesNotExist
                pu = _legacy_user_for_account(provider_account)
            except (ClubAccount.DoesNotExist, User.DoesNotExist):
                return Response({'code': 404, 'msg': '指定的陪玩不存在'})
            profile = provider_account.escort_profile
            if profile.status != EscortProfile.Status.AVAILABLE:
                return Response({'code': 400, 'msg': f'{profile.display_name}当前不可接单'})
            from users.models import EscortSchedule
            if not EscortSchedule.provider_is_scheduled_now(pu):
                return Response({'code': 400, 'msg': f'{profile.display_name}当前不在接单档期'})
            if not profile.can_take_service(service):
                return Response({'code': 400, 'msg': f'{profile.display_name}档位不满足该服务要求'})
            # 产能闸门前置在扣款之前：这条路径会先扣老板的钱再建单，等到建完
            # 才发现打手接不下，钱已经出去了。picked_ids 上面已去重，双陪的
            # 两名打手各算各的，不会互相顶名额。
            reject = _capacity_reject_reason(
                provider_account.pk, display_name=profile.display_name,
            )
            if reject:
                return Response({'code': 400, 'msg': reject})
            provider_objs.append((provider_account, pu, item))

        # 主打手（回填 Order.provider 兼容旧字段与流转）
        provider_account = provider_objs[0][0] if provider_objs else None
        provider = provider_objs[0][1] if provider_objs else None

        # ---- 金额与拆账（复用 C 端计价口径：老板折扣+活动折扣，代派单不含优惠券）----
        from orders.pricing import (
            PricingError,
            compute_order_amount,
            resolve_active_promotion,
            resolve_commission_rate,
        )
        original_amount = service.price * game_rounds
        boss_type = customer_account.boss_type
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

        # 资金守恒：实付金额先在打手之间均分为各自结算基数（余数给靠前打手），
        # 再由每人按自己的抽成规则从各自基数中算实得。口径实现统一在
        # orders.settlement，这里只负责把客服填的参数整理成 spec。
        from orders.models import OrderProvider as _OP
        provider_specs = []
        for row_account, pu, item in provider_objs:
            escort_profile = row_account.escort_profile
            ctype = (item.get('commission_type') or _OP.CommissionType.PERCENT).upper()
            if ctype == _OP.CommissionType.FIXED:
                try:
                    fixed = int(item.get('commission_fixed') or 0)
                except (TypeError, ValueError):
                    return Response({'code': 400, 'msg': '固定抽成额必须为非负兴安币'})
                if fixed < 0:
                    return Response({'code': 400, 'msg': '固定抽成额必须为非负兴安币'})
                provider_specs.append({
                    'commission_type': _OP.CommissionType.FIXED,
                    'commission_fixed': fixed,
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
                provider_specs.append({
                    'commission_type': _OP.CommissionType.PERCENT,
                    'commission_rate': rate,
                })

        shares = build_provider_shares(amount, provider_specs)
        provider_rows = [
            {
                'provider_account': row_account,
                'provider': pu,
                'settlement_base': share.settlement_base,
                'commission_type': share.commission_type,
                'commission_rate': share.commission_rate,
                'commission_fixed': share.commission_fixed,
                'provider_income': share.provider_income,
            }
            for (row_account, pu, _item), share in zip(provider_objs, shares)
        ]

        # 订单级拆账汇总，保证打手实得总和 + 推荐分佣 + 平台留存 == 实付。
        inviter_rate = (
            customer_account.inviter_commission_rate
            if customer_account.inviter_id else 0
        )
        if shares:
            try:
                split = aggregate_order_split(amount, shares, inviter_rate)
            except SettlementError as exc:
                return Response({'code': 400, 'msg': str(exc)})
        else:
            primary_rate = resolve_commission_rate(service, None, promotion)
            split = compute_split(amount, primary_rate, inviter_rate)
        inviter_account = customer_account.inviter
        inviter = (
            _legacy_user_for_account(inviter_account)
            if inviter_account is not None else None
        )

        # ---- 扣款建单 ----
        with transaction.atomic():
            # 产能二次校验，收窄上面那次（跑在事务外）到落库之间的窗口。
            #
            # 位置很讲究：必须赶在扣款之前。``transaction.atomic`` 块里的**正常
            # return 会提交事务**（只有异常才回滚），钱要是先扣了再在这里拒绝，
            # 那笔扣款就实打实落库了。这里之前只做过读，提交的是空事务。
            #
            # 残留窗口：本段只是重新计数，并没有锁住「打手」这个资源，两笔不同
            # 订单同时派给同一个人时各自锁的是不同的订单行，仍可能双双放行。
            # 彻底关闭需要对 EscortProfile 加行锁并统一全局锁序，影响面覆盖
            # 陪玩端抢单与 C 端派单，单列治理，不在本次范围内。
            for row_account, _pu, _item in provider_objs:
                reject = _capacity_reject_reason(
                    row_account.pk,
                    display_name=row_account.escort_profile.display_name,
                )
                if reject:
                    return Response({'code': 400, 'msg': reject})

            wallet = get_wallet(
                user=customer, account=customer_account, for_update=True,
            )
            if not wallet.is_active:
                return Response({'code': 403, 'msg': '该老板钱包不可用'})
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': '该老板兴安币不足，请先通过企微客服充值'})

            balance_before = wallet.balance
            wallet.balance -= amount
            wallet.save(update_fields=['balance'])

            order = Order.objects.create(
                customer=customer,
                customer_account=customer_account,
                provider=provider,
                provider_account=provider_account,
                service=service,
                amount=amount,
                game_rounds=game_rounds,
                remark=remark,
                status=Order.Status.PENDING,
                escort_mode=escort_mode,
                payment_status=Order.PaymentStatus.PAID,
                auto_cancel_at=pending_timeout_deadline(),
                original_amount=pricing.original_amount,
                boss_discount=pricing.boss_discount,
                promo_discount=pricing.promo_discount,
                coupon_discount=pricing.coupon_discount,
                promotion=promotion,
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter=inviter,
                inviter_account=inviter_account,
                inviter_commission=split.inviter_commission,
                shop_income=split.shop_income,
            )
            # 打手明细落库
            for row in provider_rows:
                pu = row['provider']
                OrderProvider.objects.create(
                    order=order,
                    provider=pu,
                    provider_account=row['provider_account'],
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
                operator=request.legacy_user,
                operator_account=request.account,
                reason='客服代派单',
            )

        # ---- 可选指派陪玩 ----
        if provider is not None:
            all_provider_ids = [row['provider'].id for row in provider_rows]

            def side_effect(o):
                o.provider = provider
                o.provider_account = provider_account
                o.provider_name_snapshot = provider.nickname or provider.username
                mark_escort_busy(provider_ids=all_provider_ids)

            try:
                order = transition(
                    order.id,
                    Order.Status.GRABBED,
                    operator=request.legacy_user,
                    operator_account=request.account,
                    action=OrderStatusLog.Action.ASSIGN,
                    side_effect=side_effect,
                    update_fields=['provider', 'provider_account', 'provider_name_snapshot'],
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
                operator=request.legacy_user,
                operator_account=request.account,
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

        实际入账逻辑全部委托给 ``orders.services.settle_order``：逐个给打手钱包
        入账（按 OrderProvider 各自实得），推荐人分佣与平台留存按单份入账，
        并刷新陪玩状态、累加完成计数；无 OrderProvider 明细时回落主打手单份口径。
        与陪玩端「完成订单」共用同一实现，两条入口的账必然一致。
        """
        order = self.get_object()

        def side_effect(o):
            # 结算收口到 orders.services.settle_order：凭证闸门、守恒闸门、
            # 打手/推荐人/平台三方入账、完成计数与状态刷新全在里面，
            # 与顾客端 CompleteOrderView 走的是同一份实现。
            settle_order(
                o,
                operator=request.legacy_user,
                operator_account=request.account,
            )

        try:
            order = transition(
                order.id,
                Order.Status.COMPLETED,
                operator=request.legacy_user,
                operator_account=request.account,
                action=OrderStatusLog.Action.COMPLETE,
                side_effect=side_effect,
            )
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许完成'})
        except SettlementError as exc:
            # 分账超额已阻断，事务整体回滚，订单保持原状待人工核对
            return Response({'code': 400, 'msg': f'{exc}，请先核对该单打手分账明细'})
        except WalletAddressingError as exc:
            return Response({'code': 400, 'msg': f'钱包数据异常：{exc}'})
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
            order = self._do_refund(
                order.id,
                request.legacy_user,
                request.account,
                reason,
            )
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
            order = self._do_refund(
                order.id,
                request.legacy_user,
                request.account,
                reason or '后台取消',
                action=OrderStatusLog.Action.CANCEL,
            )
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许取消'})
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '已取消'})

    @staticmethod
    def _do_refund(
        order_id,
        operator,
        operator_account,
        reason,
        action=OrderStatusLog.Action.REFUND,
    ):
        def pre_check(order):
            if order.status in Order.TERMINAL_STATUSES:
                raise IllegalTransitionError('订单已是终态，无法退款')

        def side_effect(order):
            if order.payment_status == Order.PaymentStatus.PAID:
                wallet = get_wallet(
                    user_id=order.customer_id,
                    account_id=order.customer_account_id,
                    for_update=True,
                )
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
            account_ids = list(
                order.providers.values_list('provider_account_id', flat=True)
            )
            if order.provider_id:
                provider_ids.append(order.provider_id)
            if order.provider_account_id:
                account_ids.append(order.provider_account_id)
            # 按剩余在跑的单重算状态，避免把还在服务其它订单的陪玩标成空闲。
            refresh_escort_status(
                provider_ids=provider_ids,
                account_ids=account_ids,
                exclude_order_ids=[order.pk],
            )
            from coupons.models import UserCoupon
            UserCoupon.objects.filter(
                order=order, status=UserCoupon.Status.USED,
            ).update(status=UserCoupon.Status.UNUSED, order=None, used_at=None)

        return transition(
            order_id,
            Order.Status.CANCELLED,
            operator=operator,
            operator_account=operator_account,
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
                tx_type=Transaction.TxType.ADJUST,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=remark,
                operator=request.legacy_user,
                operator_account=request.account,
            )
        return Response({'code': 0, 'data': self.get_serializer(wallet).data, 'msg': '调账成功'})

    @action(detail=False, methods=['post'])
    def recharge(self, request):
        """确认企微客服收款，并按 1:10 兑换规则录入兴安币。

        用户标识收口：前端 user 选择器返回 ``ClubAccount.id``，统一用 ``account_id``
        字段透传；legacy ``CustomUser.id`` 仍可由 ``user_id`` 字段兼容传入（旧后台 /
        内部工具）。两者是不同的 ID 空间，分字段传入可彻底避免"两套 ID 空间错充"。
        """
        account_id = request.data.get('account_id')
        user_id = request.data.get('user_id')
        if account_id not in (None, ''):
            legacy_user_id = _legacy_user_id_from_account_id(account_id)
        elif user_id not in (None, ''):
            try:
                legacy_user_id = int(user_id)
            except (TypeError, ValueError):
                return Response({'code': 400, 'msg': '非法的用户标识'})
            if not User.objects.filter(pk=legacy_user_id).exists():
                legacy_user_id = None
        else:
            return Response({'code': 400, 'msg': '请指定充值用户'})
        if legacy_user_id is None:
            return Response({'code': 404, 'msg': '用户不存在'})

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

        with transaction.atomic():
            wallet = get_wallet(user_id=legacy_user_id, for_update=True)
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
                user_id=legacy_user_id,
                amount=amount,
                gift_amount=gift_amount,
                trade_no=trade_no,
                proof_image=proof_image,
                remark=remark,
                operator=request.legacy_user,
                operator_account=request.account,
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
        account_id = self.request.query_params.get('account_id')
        user_id = self.request.query_params.get('user_id')
        if account_id:
            # 前端 user 选择器返回 ClubAccount.id，单向映射为 legacy 用户 id 再筛选
            legacy_user_id = _legacy_user_id_from_account_id(account_id)
            qs = qs.filter(user_id=legacy_user_id if legacy_user_id is not None else -1)
        elif user_id:
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
        account_id = self.request.query_params.get('account_id')
        user_id = self.request.query_params.get('user_id')
        if account_id:
            legacy_user_id = _legacy_user_id_from_account_id(account_id)
            qs = qs.filter(user_id=legacy_user_id if legacy_user_id is not None else -1)
        elif user_id:
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
        ).prefetch_related(
            'result_images', 'order__providers',
        ).exclude(status=ProviderReport.Status.DRAFT).order_by('-created_at')
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
                # 双陪订单必须取「该报单人自己那条」OrderProvider 的实得，
                # 否则副陪看到的是订单级汇总，与钱包实际到账对不上。
                own_share = _resolve_report_order_share(report)
                report.status = ProviderReport.Status.APPROVED
                report.commission_rate = (
                    own_share.commission_rate if own_share else report.order.commission_rate
                )
                report.payout_amount = (
                    own_share.provider_income if own_share else report.order.provider_income
                )
                report.auditor = request.legacy_user
                report.auditor_account = request.account
                report.audited_at = timezone.now()
                report.save(update_fields=[
                    'status', 'commission_rate', 'payout_amount', 'auditor',
                    'auditor_account', 'audited_at',
                ])
                return Response({'code': 0, 'data': self.get_serializer(report).data, 'msg': '凭证审核通过'})

            # 无关联订单的历史手工报单：优先采用陪玩报单时填写的抽成比例。
            if report.commission_rate:
                rate = report.commission_rate
                source_label = '报单填写'

            payout = report.amount * (100 - rate) // 100

            wallet = get_wallet(
                user_id=report.provider_id,
                account_id=report.provider_account_id,
                for_update=True,
            )
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
            report.auditor = request.legacy_user
            report.auditor_account = request.account
            report.transaction = tx
            report.audited_at = timezone.now()
            report.save(update_fields=[
                'status', 'commission_rate', 'payout_amount', 'auditor', 'auditor_account',
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
            report.auditor = request.legacy_user
            report.auditor_account = request.account
            report.audited_at = timezone.now()
            report.save(update_fields=[
                'status', 'audit_remark', 'auditor', 'auditor_account', 'audited_at',
            ])

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
        if 'report:audit' not in get_user_permissions(request.account):
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

            wallet = get_wallet(
                user_id=withdraw.user_id,
                account_id=withdraw.account_id,
                for_update=True,
                create=False,
            )
            if wallet is None:
                return Response({'code': 400, 'msg': '申请人钱包不存在，无法通过'})
            if wallet.frozen_amount < withdraw.amount:
                return Response({'code': 400, 'msg': '冻结金额异常，无法通过'})
            wallet.frozen_amount -= withdraw.amount
            wallet.save(update_fields=['frozen_amount'])

            if withdraw.transaction_id:
                Transaction.objects.filter(pk=withdraw.transaction_id).update(
                    status=Transaction.Status.SUCCESS,
                    remark=(
                        f'提现到账：{withdraw.get_payee_method_display()} {withdraw.payee_account}'
                        + (
                            f'（税前 {withdraw.amount / 10:g}，代扣税 {withdraw.tax_amount / 10:g}，'
                            f'实付 {withdraw.actual_amount / 10:g} 兴安币）'
                            if withdraw.tax_amount else ''
                        )
                    ),
                )

            # 代扣税额必须落到平台钱包并留痕，否则这部分钱在账面上凭空消失。
            # 陪玩侧扣的是全额 amount，平台侧收 tax_amount，实际打款 actual_amount，三者守恒。
            if withdraw.tax_amount:
                platform_wallet = get_platform_wallet(for_update=True)
                tax_before = platform_wallet.balance
                platform_wallet.balance += withdraw.tax_amount
                platform_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=platform_wallet,
                    amount=withdraw.tax_amount,
                    tx_type=Transaction.TxType.WITHDRAW_TAX,
                    balance_before=tax_before,
                    balance_after=platform_wallet.balance,
                    operator=request.legacy_user,
                    operator_account=request.account,
                    remark=(
                        f'提现代扣税费：申请 #{withdraw.pk}，'
                        f'税前 {withdraw.amount / 10:g} 兴安币 × {withdraw.tax_rate}%'
                    ),
                )

            withdraw.status = WithdrawRequest.Status.APPROVED
            withdraw.payout_reference = payout_reference
            withdraw.paid_at = timezone.now()
            withdraw.auditor = request.legacy_user
            withdraw.auditor_account = request.account
            withdraw.audited_at = timezone.now()
            withdraw.save(update_fields=[
                'status', 'payout_reference', 'paid_at', 'auditor',
                'auditor_account', 'audited_at',
            ])

        # 通知一律以实际到账口径为准，避免陪玩按税前金额对账产生客诉。
        tax_note = (
            f'（税前 {withdraw.amount / 10:g} 兴安币，'
            f'代扣税 {withdraw.tax_rate}% 计 {withdraw.tax_amount / 10:g} 兴安币）'
            if withdraw.tax_amount else ''
        )
        create_message(
            recipient_id=withdraw.user_id,
            title='提现审核通过',
            preview=f'提现已通过，实际到账 {withdraw.actual_amount / 10:g} 兴安币',
            detail=f'您申请的提现已审核通过，实际到账 {withdraw.actual_amount / 10:g} 兴安币{tax_note}，'
                   f'将结算至 {withdraw.get_payee_method_display()}（{withdraw.payee_account}）。',
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

            wallet = get_wallet(
                user_id=withdraw.user_id,
                account_id=withdraw.account_id,
                for_update=True,
                create=False,
            )
            if wallet is None:
                return Response({'code': 400, 'msg': '申请人钱包不存在，无法驳回'})
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
            withdraw.auditor = request.legacy_user
            withdraw.auditor_account = request.account
            withdraw.audited_at = timezone.now()
            withdraw.save(update_fields=[
                'status', 'audit_remark', 'auditor', 'auditor_account', 'audited_at',
            ])

        create_message(
            recipient_id=withdraw.user_id,
            title='提现审核未通过',
            preview=f'提现 {withdraw.amount / 10:g} 兴安币被驳回',
            detail=f'驳回原因：{audit_remark}。已退回 {withdraw.amount / 10:g} 兴安币至您的余额。',
        )
        return Response({'code': 0, 'data': self.get_serializer(withdraw).data, 'msg': '已驳回'})


class WithdrawConfigView(APIView):
    """提现配置：GET 读取（最低提现金额 + 提现税率），PUT 修改（需 withdraw:audit）。"""

    permission_classes = [IsConsoleUser]

    def put(self, request):
        if 'withdraw:audit' not in get_user_permissions(request.account):
            return Response({'code': 403, 'msg': '无操作权限'})
        try:
            min_amount = int(request.data.get('min_amount'))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请输入合法的最低提现兴安币'})
        if min_amount < 0:
            return Response({'code': 400, 'msg': '最低提现金额不能为负'})

        try:
            tax_rate = int(request.data.get('tax_rate'))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请输入合法的提现税率'})
        if tax_rate < 0 or tax_rate > 100:
            return Response({'code': 400, 'msg': '提现税率需在 0-100 之间'})

        SystemConfig.objects.update_or_create(
            key=MIN_WITHDRAW_AMOUNT_KEY,
            defaults={'value': str(min_amount), 'remark': '最低提现兴安币（账务单位）'},
        )
        SystemConfig.objects.update_or_create(
            key=WITHDRAW_TAX_RATE_KEY,
            defaults={'value': str(tax_rate), 'remark': '提现税率（百分比，0-100）'},
        )
        return Response({
            'code': 0,
            'data': {'min_amount': min_amount, 'tax_rate': tax_rate},
            'msg': '提现配置已更新',
        })

    def get(self, request):
        return Response({
            'code': 0,
            'data': {
                'min_amount': get_min_withdraw_amount(),
                'tax_rate': get_withdraw_tax_rate(),
            },
        })


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
                sender=request.legacy_user,
                sender_account=request.account,
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
        if 'checkin:edit' not in get_user_permissions(request.account):
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
        serializer.save(
            operator=self.request.legacy_user,
            operator_account=self.request.account,
        )


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
            signup.auditor = request.legacy_user
            signup.auditor_account = request.account
            signup.audited_at = timezone.now()
            signup.save(update_fields=[
                'status', 'auditor', 'auditor_account', 'audited_at',
            ])

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
            signup.auditor = request.legacy_user
            signup.auditor_account = request.account
            signup.audited_at = timezone.now()
            signup.save(update_fields=[
                'status', 'audit_remark', 'auditor', 'auditor_account', 'audited_at',
            ])

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
        recipient_account_ids = request.data.get('recipient_account_ids') or []

        if broadcast:
            targets = list(User.objects.filter(is_active=True).values_list('id', flat=True))
        elif audience in (User.Role.CUSTOMER, User.Role.PROVIDER):
            targets = list(User.objects.filter(is_active=True, role=audience).values_list('id', flat=True))
        else:
            if not recipient_ids and not recipient_account_ids:
                return Response({'code': 400, 'msg': '请选择接收人或勾选全员广播'})
            # 前端 user 选择器返回 ClubAccount.id，用 recipient_account_ids 显式透传，
            # 单向映射为 legacy 用户 id，避免两套 ID 空间错充；recipient_ids 为 legacy 兼容。
            targets = []
            for rid in recipient_account_ids:
                lid = _legacy_user_id_from_account_id(rid)
                if lid is not None:
                    targets.append(lid)
            for rid in recipient_ids:
                try:
                    lid = int(rid)
                except (TypeError, ValueError):
                    continue
                if User.objects.filter(pk=lid).exists():
                    targets.append(lid)

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
                operator_account_id=OuterRef('account_id'),
                action=OrderStatusLog.Action.CREATE,
                created_at__date=today,
            )
            .values('operator_account_id')
            .annotate(total=Sum('order__amount'))
            .values('total')
        )
        qs = (
            AdminMembership.objects.select_related('account', 'user').prefetch_related('roles')
            .annotate(
                today_dispatch_amount=Coalesce(
                    Subquery(dispatch_sq, output_field=IntegerField()), 0
                )
            )
        )
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(account__username__icontains=keyword)
                | Q(account__nickname__icontains=keyword)
                | Q(account__phone__icontains=keyword)
            )
        return qs

    def create(self, request, *args, **kwargs):
        serializer = AdminCreateMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            account = ClubAccount.objects.create_account(
                username=data['username'],
                password=data['password'],
                nickname=data.get('nickname', ''),
                phone=data.get('phone') or None,
                account_type=ClubAccount.AccountType.STAFF,
            )
            user = User.objects.create_user(
                username=data['username'],
                password=None,
                nickname=data.get('nickname', ''),
                phone=data.get('phone') or None,
                role=User.Role.ADMIN,
                is_staff=False,
            )
            LegacyAccountMap.objects.create(
                legacy_user_id=user.id,
                account=account,
                legacy_role=User.Role.ADMIN,
            )
            membership = AdminMembership.objects.create(
                user=user,
                account=account,
                remark=data.get('remark', ''),
            )
            membership.roles.set(data['roles'])
        membership = self.get_queryset().get(pk=membership.pk)
        return Response(
            {'code': 0, 'data': AdminMembershipSerializer(membership).data},
            status=201,
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        account = instance.account
        user = instance.user
        data = request.data

        with transaction.atomic():
            account_fields = []
            user_fields = []
            if 'nickname' in data:
                nickname = (data.get('nickname') or '').strip()[:50]
                account.nickname = nickname
                account_fields.append('nickname')
                user.nickname = nickname
                user_fields.append('nickname')
            if 'phone' in data:
                phone = (data.get('phone') or '').strip()[:20] or None
                account.phone = phone
                account_fields.append('phone')
                user.phone = phone
                user_fields.append('phone')
            if account_fields:
                account.save(update_fields=account_fields)
            if user_fields:
                user.save(update_fields=user_fields)

            membership_fields = []
            if 'remark' in data:
                instance.remark = (data.get('remark') or '').strip()[:255]
                membership_fields.append('remark')
            is_super_admin = (
                instance.roles.filter(code='super_admin').exists()
                or bool(user and user.is_superuser)
            )
            # 业务超管的角色与启用状态受保护，不依赖 Django 超级用户字段。
            if not is_super_admin:
                if 'roles' in data:
                    role_ids = data.get('roles') or []
                    roles = list(AdminRole.objects.filter(pk__in=role_ids))
                    if len(roles) != len(set(role_ids)):
                        return Response({'code': 400, 'msg': '存在无效角色'})
                    instance.roles.set(roles)
                if 'is_active' in data:
                    instance.is_active = _truthy(data.get('is_active'))
                    membership_fields.append('is_active')
            if membership_fields:
                instance.save(update_fields=membership_fields)

        instance = self.get_queryset().get(pk=instance.pk)
        return Response({'code': 0, 'data': AdminMembershipSerializer(instance).data})

    def perform_destroy(self, instance):
        if (
            instance.roles.filter(code='super_admin').exists()
            or bool(instance.user and instance.user.is_superuser)
        ):
            from rest_framework.exceptions import ValidationError
            raise ValidationError('超级管理员不可删除')
        instance.delete()


# ---------------- 操作审计日志 ----------------
class AuditLogViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminAuditLogSerializer
    default_perm = 'audit:view'

    def get_queryset(self):
        qs = AdminAuditLog.objects.select_related('operator').all()
        operator = self.request.query_params.get('operator')
        if operator:
            qs = qs.filter(operator_id=operator)
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(operator_name__icontains=keyword)
                | Q(path__icontains=keyword)
                | Q(resource__icontains=keyword)
            )
        method = self.request.query_params.get('method')
        if method:
            qs = qs.filter(method=method.upper())
        start_date = self.request.query_params.get('start_date')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        end_date = self.request.query_params.get('end_date')
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
        return qs
