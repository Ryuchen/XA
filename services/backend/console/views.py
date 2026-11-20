import hashlib
import json
import logging
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from django.db import IntegrityError
from stores.db import transaction
from stores.context import current_currency_name
from django.db.models import Case, CharField, Count, Exists, F, IntegerField, Max, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDate
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework import serializers as rest_serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from announcements.models import Announcement
from audition.models import AuditionLink, AuditionSignup
from banners.models import Banner
from chat.models import ChatMessage, ChatSession
from chat.notifier import schedule_chat_message, schedule_chat_session_update
from club_accounts.models import ClubAccount, LegacyAccountMap
from console.models import AdminAuditLog, AdminMembership, AdminRole
from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
from coupons.models import Coupon, UserCoupon
from orders.models import (
    AfterSale,
    Evaluation,
    GameCategory,
    KookDispatchRecord,
    Order,
    OrderProvider,
    OrderStatusLog,
    Reservation,
    ServiceCategory,
    ServiceItem,
)
from orders.gift_refund_lifecycle import protect_gift_refund
from orders.ratings import refresh_escort_rating
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
    count_capacity_orders,
    ensure_provider_dispatch_locks,
    lock_provider_dispatch,
    mark_escort_busy,
    pending_timeout_deadline,
    push_message_safe as _push_message_safe,
    refresh_escort_status,
    refund_to_customer,
    schedule_auto_cancel as _schedule_auto_cancel,
    settle_order,
    complete_fulfillment,
    sync_single_provider_share,
    approve_order_report,
)
from orders.notifier import notify_order_update, notify_report_update, notify_evaluation_update
from orders.refunds import RefundError, build_refund_plan
from orders.kook_dispatch import enqueue_kook_dispatch
from orders.dispatching import dispatch_preview, enqueue_wecom_dispatch, queue_dispatch
from promotions.models import Promotion
from site_messages.models import Message
from site_messages.utils import create_message
from support.models import SupportContactCard
from users.models import (
    Achievement, BossType, CheckinGift, CheckinMonthProgress, CheckinRuleConfig,
    EscortLevel, EscortProfile, EscortSchedule, ProviderRegistration,
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
from wallet.services import WalletAddressingError, claim_fund_write, get_wallet, peek_fund_write
from wallet.receipts import fund_success, recover_fund_result
from common.media import build_media_url

from .ban_utils import active_ban_for, apply_ban, lift_ban
from .deposit_refund import refund_deposit
from .dashboard import DashboardRangeSerializer, player_order_metrics
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
    AdminChatReadSerializer,
    AdminChatReplySerializer,
    AdminChatSessionSerializer,
    AdminCheckinGiftSerializer,
    AdminCheckinProgressSerializer,
    AdminCheckinRuleSerializer,
    AdminCouponSerializer,
    AdminCreateEscortSerializer,
    AdminCreateMembershipSerializer,
    AdminUpdateMembershipSerializer,
    AdminDisposeRecordSerializer,
    AdminEscortLevelSerializer,
    AdminEscortSerializer,
    AdminEvaluationSerializer,
    AdminGameCategorySerializer,
    AdminMembershipSerializer,
    AdminMessageSerializer,
    AdminOrderLogSerializer,
    AdminOrderDetailSerializer,
    AdminOrderSerializer,
    AdminOrderWithIdentitySerializer,
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
    """Resolve an explicitly linked legacy identity without guessing from IDs."""
    raw = str(account_id or '')
    if not raw.isascii() or not raw.isdigit() or len(raw) > 19 or not 0 < int(raw) <= 9223372036854775807:
        return None
    account_id = int(raw)
    mapped = LegacyAccountMap.objects.filter(account_id=account_id).values_list('legacy_user_id', flat=True).first()
    wallet_user = Wallet.objects.filter(account_id=account_id).values_list('user_id', flat=True).first()
    if wallet_user:
        return wallet_user
    if mapped and Wallet.objects.filter(user_id=mapped, account__isnull=False).exclude(account_id=account_id).exists():
        return None
    return mapped


def _truthy(value):
    return str(value).lower() in ('1', 'true', 'yes')


# ---------------- 仪表盘 ----------------
class DashboardView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        permissions = get_account_permissions(request.account)
        if 'dashboard:view' not in permissions:
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        if 'order:view' in permissions:
            from orders.reservation_expiration import expire_due_reservations
            expire_due_reservations()
        # The direct-payment domain is currently simulation-only and cannot
        # represent real customer payment. Keep its test orders out of the
        # operational snapshot until a distinct live payment source exists.
        orders = Order.objects.filter(direct_payment__isnull=True)
        from orders.refunds import with_net_amount,PAID_STATES
        snapshot_at = timezone.now()
        from .serializers import pending_report_order_block_q
        operational_reports = ProviderReport.objects.filter(Q(order__isnull=True) | Q(order__direct_payment__isnull=True))
        paid = with_net_amount(orders.filter(payment_status__in=PAID_STATES)).aggregate(
            amount=Sum('_net_paid_amount'), count=Count('id'),
            gift_amount=Sum('_net_paid_amount', filter=Q(is_gift_snapshot=True)),
        )
        pending_paid = orders.filter(status=Order.Status.PENDING, payment_status=Order.PaymentStatus.PAID).aggregate(
            total=Count('id'),
            staff=Count('id', filter=Q(dispatch_route=Order.DispatchRoute.STAFF)),
            public=Count('id', filter=Q(dispatch_route=Order.DispatchRoute.PUBLIC)),
            overdue=Count('id', filter=Q(auto_cancel_at__lte=snapshot_at)),
        )
        pending_withdrawals = None
        pending_subsidies = None
        if 'wallet:view' in permissions and getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            from orders.subsidies import pending_subsidy_shares
            pending_subsidies = pending_subsidy_shares().aggregate(
                total=Count('subsidy__order_id', distinct=True), amount=Sum('amount'))
        if 'withdraw:view' in permissions:
            pending_withdrawals = WithdrawRequest.objects.filter(status=WithdrawRequest.Status.PENDING, workflow_version=2).aggregate(
                total=Count('id'), unclaimed=Count('id', filter=Q(claimed_by_account__isnull=True)),
                mine=Count('id', filter=Q(claimed_by_account=request.account)),
            )
        pending_claims = None
        pending_tag_reviews = None
        ready_tag_reviews = None
        if 'engagement:fulfill' in permissions:
            from engagement.models import RewardClaim
            from engagement.serializers import with_claim_return_status
            from engagement.queries import tags_ready_for_review
            ready_tag_reviews = tags_ready_for_review(RewardClaim.objects.all()).count()
            pending_claims = RewardClaim.objects.aggregate(
                pending=Count('id', filter=Q(status=RewardClaim.Status.PENDING)),
                fulfilling=Count('id', filter=Q(status=RewardClaim.Status.FULFILLING)),
            )
            pending_tag_reviews = with_claim_return_status(RewardClaim.objects.filter(
                kind_snapshot='TAG', status=RewardClaim.Status.COMPLETED)).filter(tag_needs_review=True).count()
        today = timezone.localdate(snapshot_at)
        zone = timezone.get_current_timezone()
        today_start = timezone.make_aware(datetime.combine(today, time.min), zone)
        today_subsidy = None
        if 'wallet:view' in permissions and getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            from wallet.models import Transaction
            today_subsidy = Transaction.objects.filter(
                Q(order__isnull=True) | Q(order__direct_payment__isnull=True),
                wallet__user__username=settings.PLATFORM_SYSTEM_USERNAME,
                tx_type=Transaction.TxType.SUBSIDY_EXPENSE, status=Transaction.Status.SUCCESS,
                created_at__gte=today_start, created_at__lte=snapshot_at,
            ).aggregate(paid=Sum('amount', filter=Q(amount__lt=0)),
                        recovered=Sum('amount', filter=Q(amount__gt=0)))
        from orders.models import OrderRefund
        today_refunds = OrderRefund.objects.filter(
            order__direct_payment__isnull=True, created_at__gte=today_start,
            created_at__lte=snapshot_at, amount__gt=0,
        ).aggregate(amount=Sum('amount'), operations=Count('id'), orders=Count('order_id', distinct=True))
        today_orders = orders.filter(created_at__date=today)
        today_counts = today_orders.aggregate(total=Count('id'), gifts=Count('id', filter=Q(is_gift_snapshot=True)))

        status_rows = orders.values('status').annotate(count=Count('id'))
        status_map = {row['status']: row['count'] for row in status_rows}
        first_day = today - timedelta(days=6)
        start = timezone.make_aware(datetime.combine(first_day, time.min), zone)
        end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min), zone)
        daily = orders.filter(created_at__gte=start, created_at__lt=end).annotate(
            day=TruncDate('created_at', tzinfo=zone),
        ).values('day').annotate(count=Count('id'))
        daily_counts = {row['day']: row['count'] for row in daily}
        # Cohort net value follows the order's creation day, with refunds
        # reflected in the current snapshot rather than the refund day.
        daily_net = with_net_amount(orders.filter(
            created_at__gte=start, created_at__lt=end, payment_status__in=PAID_STATES,
        )).annotate(day=TruncDate('created_at', tzinfo=zone)).values('day').annotate(
            amount=Sum('_net_paid_amount'),
        )
        daily_net_amounts = {row['day']: row['amount'] or 0 for row in daily_net}

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
                'today_order_total': today_counts['total'],
                'today_service_order_total': today_counts['total'] - today_counts['gifts'],
                'today_gift_order_total': today_counts['gifts'],
                'today_refund_amount': today_refunds['amount'] or 0,
                'today_refund_operations': today_refunds['operations'],
                'today_refund_orders': today_refunds['orders'],
                'today_subsidy_paid': -(today_subsidy['paid'] or 0) if today_subsidy is not None else None,
                'today_subsidy_recovered': (today_subsidy['recovered'] or 0) if today_subsidy is not None else None,
                'snapshot_at': snapshot_at.isoformat(),
                'timezone': str(zone),
                'pending_paid_total': pending_paid['total'],
                'pending_paid_overdue': pending_paid['overdue'],
                'pending_paid_routes': {'staff': pending_paid['staff'], 'public': pending_paid['public']},
                'pending_unpaid_total': orders.filter(status=Order.Status.PENDING, payment_status=Order.PaymentStatus.UNPAID).count(),
                'pending_report_total': operational_reports.filter(status=ProviderReport.Status.PENDING).count() if 'report:view' in permissions else None,
                'pending_subsidy_total': pending_subsidies['total'] if pending_subsidies is not None else None,
                'pending_subsidy_amount': (pending_subsidies['amount'] or 0) if pending_subsidies is not None else None,
                'pending_report_order_blocked_total': operational_reports.filter(pending_report_order_block_q(operational_reports)).count() if 'report:view' in permissions else None,
                'unreplied_evaluation_total': Evaluation.objects.filter(reply_content='', voided_at__isnull=True, order__direct_payment__isnull=True).count() if 'evaluation:view' in permissions else None,
                'pending_withdraw_total': pending_withdrawals['total'] if pending_withdrawals is not None else None,
                'pending_withdraw_unclaimed': pending_withdrawals['unclaimed'] if pending_withdrawals is not None else None,
                'pending_withdraw_mine': pending_withdrawals['mine'] if pending_withdrawals is not None else None,
                'pending_claim_total': pending_claims['pending'] if pending_claims is not None else None,
                'fulfilling_claim_total': pending_claims['fulfilling'] if pending_claims is not None else None,
                'pending_tag_review_total': pending_tag_reviews,
                'ready_tag_review_total': ready_tag_reviews,
                'open_aftersale_order_total': orders.filter(Exists(AfterSale.objects.filter(order_id=OuterRef('pk'), status='OPEN'))).count() if 'order:view' in permissions else None,
                'legacy_team_inflight_total': orders.filter(escort_mode=Order.EscortMode.DOUBLE, requires_boss_selection=False,
                    status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE]).count() if 'order:view' in permissions else None,
                'pending_reservation_total': Reservation.objects.filter(status=Reservation.Status.PENDING).count() if 'order:view' in permissions else None,
                'confirmed_reservation_total': Reservation.objects.filter(status=Reservation.Status.CONFIRMED).count() if 'order:view' in permissions else None,
                'unread_chat_session_total': ChatSession.objects.filter(unread_support__gt=0).count() if 'chat:view' in permissions else None,
                'daily_new_orders': [
                    {'date': (first_day + timedelta(days=i)).isoformat(),
                     'count': daily_counts.get(first_day + timedelta(days=i), 0),
                     'net_amount': daily_net_amounts.get(first_day + timedelta(days=i), 0)}
                    for i in range(7)
                ],
                'order_amount_total': paid['amount'] or 0,
                'gift_order_net_amount': paid['gift_amount'] or 0,
                'paid_amount_total': paid['amount'] or 0,
                'paid_order_total': paid['count'],
                'order_status': status_map,
                'recent_orders': (AdminOrderWithIdentitySerializer if 'order:view' in permissions else AdminOrderSerializer)(
                    orders.select_related('customer', 'customer_account', 'provider__escort_profile', 'provider_account__escort_profile', 'source_order').prefetch_related('providers__provider_account__escort_profile', 'providers__provider__escort_profile').order_by('-created_at')[:10],
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

        snapshot_at = timezone.now()
        from orders.refunds import PAID_STATES
        order_qs = Order.objects.filter(payment_status__in=PAID_STATES, direct_payment__isnull=True)
        date_range = DashboardRangeSerializer(data=request.query_params)
        date_range.is_valid(raise_exception=True)
        start_date = date_range.validated_data.get('start_date')
        end_date = date_range.validated_data.get('end_date')
        if start_date:
            order_qs = order_qs.filter(created_at__date__gte=start_date)
        if end_date:
            order_qs = order_qs.filter(created_at__date__lte=end_date)

        kpi, rank, income_review = player_order_metrics(order_qs)
        return Response({
            'code': 0,
            'data': {
                'snapshot_at': snapshot_at.isoformat(),
                'timezone': str(timezone.get_current_timezone()),
                'kpi': kpi,
                'secondary': self._build_secondary(),
                'rank': rank,
                'income_review': income_review,
            },
        })

    @staticmethod
    def _build_secondary():
        escort_agg = EscortProfile.objects.aggregate(
            total_deposit=Sum('deposit_paid'),
            total_penalty=Sum('total_penalty'),
            total_reward=Sum('total_reward'),
        )
        wallet_agg = Wallet.objects.filter(
            Q(account__account_type=ClubAccount.AccountType.PROVIDER)
            | Q(account__isnull=True, user__role=User.Role.PROVIDER),
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

    事务中的最终校验必须先取得 ``lock_provider_dispatch`` 名额锁。
    ``active_orders_for`` 会排除已转出或已结算的成员明细。

    Returns:
        str | None: 还接得下返回 ``None``；超限返回给客服看的拒绝理由。
    """
    others = count_capacity_orders(
        account_id=account_id, exclude_order_ids=exclude_order_ids,
    )
    from orders.capacity import current_capacity_limit
    capacity_limit = current_capacity_limit()
    if others < capacity_limit:
        return None
    who = display_name or '该陪玩'
    return (
        f'{who}已达同时进行 {capacity_limit} 单上限'
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

    if role == 'provider':
        ensure_provider_dispatch_locks([account.pk])
    try:
        with transaction.atomic():
            # All dispatch paths acquire this same per-provider lock before
            # checking eligibility and claiming capacity. Hold it through the
            # active-order check and account update so a non-forced ban cannot
            # use a count made stale by a concurrently committed assignment.
            # The ban must not lock an order row after taking this lock: the
            # assignment lock order is order -> provider capacity lock.
            if role == 'provider':
                lock_provider_dispatch([account.pk])
                active_count, active_sample = _provider_active_orders(account)
            else:
                # Boss bans intentionally do not block on active orders.
                active_count, active_sample = _customer_active_orders(account)

            if role == 'provider' and not force and active_count:
                return Response({
                    'code': 409,
                    'msg': f'该账户还有 {active_count} 笔在途订单，请先处理完再封禁',
                    'data': {
                        'active_order_count': active_count,
                        'active_order_ids': [row[0] for row in active_sample],
                        'active_order_nos': [row[1] for row in active_sample],
                        'truncated': active_count > len(active_sample),
                    },
                }, status=409)

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


def _ban_history(view, account):
    records = AccountBan.objects.filter(account=account).select_related(
        'account', 'operator_account', 'lifted_by_account',
    ).order_by('-banned_at', '-id')
    page = view.paginate_queryset(records)
    response = view.get_paginated_response(BanRecordSerializer(page, many=True).data)
    active = records.filter(status=AccountBan.Status.ACTIVE).first()
    response.data['data']['active'] = BanRecordSerializer(active).data if active else None
    response.data['data']['account_active'] = account.is_active
    response.data['data']['can_login'] = account.can_login
    return response


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
        'retire': 'user:retire',
        'retire_preview': 'user:retire',
    }

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        # Serialize profile edits with VIP recalculation so an older form
        # cannot write the previous grade back after a concurrent change.
        from engagement.models import ClubOperationConfig
        from engagement.services import config
        from engagement.vip_transition import transition_guard_enabled
        if transition_guard_enabled():
            config()
            ClubOperationConfig.objects.select_for_update().get(pk=1)
            list(ClubAccount.objects.select_for_update().filter(pk=kwargs.get('pk'),
                account_type=ClubAccount.AccountType.BOSS).values_list('pk', flat=True))
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def retire(self, request, pk=None):
        from engagement.retirement import retire
        if request.data.get('confirm') is not True:
            raise rest_serializers.ValidationError('请确认退店清空内容')
        account = retire(self.get_object(), request.account, request.data.get('reason'),
                         request.data.get('confirmation_token'))
        return Response({'code': 0, 'msg': '业务权益已重置，历史订单和财务记录已保留',
            'data': {'business_cycle':account.business_cycle}})

    @action(detail=True, methods=['get'], url_path='retire-preview')
    def retire_preview(self, request, pk=None):
        from engagement.retirement import preview_retirement
        return Response({'code': 0, 'data': preview_retirement(self.get_object(), request.account)})

    def create(self, request, *args, **kwargs):
        """显式关闭建号入口：老板账户由老板端注册产生，后台不许凭空造。"""
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
            qs = qs.filter(account_no__icontains=boss_no)
        nickname = self.request.query_params.get('nickname')
        if nickname:
            qs = qs.filter(nickname__icontains=nickname)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(nickname__icontains=keyword) | Q(username__icontains=keyword)
                           | Q(account_no__icontains=keyword))
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

    @action(detail=True, methods=['get'], url_path='ban-history')
    def ban_history(self, request, pk=None):
        return _ban_history(self, self.get_object())


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
        # 押金退还是出账动作，与奖惩分开授权：能改档案的人未必能动押金池。
        'deposit_refund': 'escort:deposit_refund',
        'deposit_topups': 'escort:deposit_topup',
        # 封禁权限点跨老板/陪玩共用一套，运营心智里「封人」就是一件事。
        'ban': 'user:ban',
        'unban': 'user:ban',
    }

    @action(detail=True, methods=['get'], url_path='ban-history')
    def ban_history(self, request, pk=None):
        profile = self.get_object()
        if not profile.account_id:
            return Response({'code': 400, 'msg': '该陪玩尚未绑定业务账户'}, status=400)
        return _ban_history(self, profile.account)

    @action(detail=True, methods=['get', 'post'], url_path='deposit-refund')
    def deposit_refund(self, request, pk=None):
        """押金退还：把陪玩已缴押金按金额退回其钱包（DP-1）。

        账务编排全部收敛在 ``console.deposit_refund.refund_deposit``，
        这里只做路由层的前置分支与 ``{code,data,msg}`` 信封包装。
        """
        try:
            profile = self.get_object()
        except Http404:
            return Response({'code': 404, 'msg': '陪玩不存在'})
        if profile.account_id is None:
            return Response({'code': 403, 'msg': '该陪玩未绑定业务账户'})

        if request.method == 'GET':
            from .deposit_refund import preview_deposit_refund
            return Response({'code': 0, 'data': preview_deposit_refund(profile)})

        code, msg, data = refund_deposit(
            request,
            profile_id=profile.pk,
            amount=request.data.get('amount', 0),
            reason=request.data.get('reason', ''),
            client_request_id=request.data.get('client_request_id'),
        )
        if code != 0:
            return Response({'code': code, 'msg': msg, 'data': data})
        return Response({'code': 0, 'msg': msg, 'data': data})

    @action(detail=True, methods=['get', 'post'], url_path='deposit-topups')
    def deposit_topups(self, request, pk=None):
        from .cash_deposit import list_topups, register_topup
        profile = self.get_object()
        return list_topups(request, profile) if request.method == 'GET' else register_topup(request, profile.pk)

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
        ).prefetch_related('game_categories', 'service_items','qualifications__game_category').order_by('-created_at')
        from users.qualifications import filtered_qualifications
        qualification_filter=filtered_qualifications(self.request.query_params,minimum=False)
        if qualification_filter is not None:qs=qs.filter(qualification_filter).distinct()
        gender=self.request.query_params.get('gender')
        if gender:qs=qs.filter(gender=gender)
        pass_tier=self.request.query_params.get('pass_tier')
        if pass_tier=='NONE':qs=qs.filter(Q(pass_tier='')|Q(pass_expires_at__lte=timezone.now())|Q(pass_expires_at__isnull=True))
        elif pass_tier:qs=qs.filter(pass_tier=pass_tier,pass_expires_at__gt=timezone.now())
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        verified = self.request.query_params.get('is_verified')
        if verified is not None and verified != '':
            qs = qs.filter(is_verified=_truthy(verified))
        account_username = self.request.query_params.get('account_username')
        if account_username is not None:
            account_username = account_username.strip()
            if not account_username or len(account_username) > 150:
                raise rest_serializers.ValidationError('登录账号参数无效')
            qs = qs.filter(account__username=account_username)
        game_id = self.request.query_params.get('game_category')
        if game_id:
            if not game_id.isascii() or not game_id.isdigit() or len(game_id) > 18:
                raise rest_serializers.ValidationError('游戏类目参数无效')
            qs = qs.filter(game_categories__id=int(game_id)).distinct()
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
                is_openid_bound=False,
            )
            user = User.objects.create_user(
                username=data['username'],
                password=data['password'],
                nickname=data.get('nickname', ''),
                phone=data.get('phone') or None,
                role=User.Role.PROVIDER,
                is_openid_bound=False,
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
                deposit_mode=data.get('deposit_mode', 'CASH'),
                avatar=data.get('avatar'),
                avatar_source='STAFF_UPLOAD' if data.get('avatar') else 'UNKNOWN',
                intro_video=data.get('intro_video'),
                cheat_proof=data.get('cheat_proof'),
            )
            game_categories = data.get('game_categories')
            if game_categories:
                profile.game_categories.set(game_categories)
            service_items = data.get('service_items')
            if service_items:
                profile.service_items.set(service_items)
            from users.qualification_serializers import save_qualifications
            if 'qualifications' in data:save_qualifications(profile,data['qualifications'])
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
    @recover_fund_result('admin.dispose')
    def dispose(self, request, pk=None):
        """奖励或罚款：直接影响陪玩钱包余额，落流水 + 累加统计 + 记录。"""
        profile = self.get_object()
        dispose_type = request.data.get('dispose_type')
        dispose_type = dispose_type.strip().upper() if isinstance(dispose_type, str) else ''
        if dispose_type not in DisposeRecord.DisposeType.values:
            return Response({'code': 400, 'msg': '请选择奖励或罚款'})
        try:
            amount = rest_serializers.IntegerField(min_value=1, max_value=2147483647).run_validation(request.data.get('amount'))
        except rest_serializers.ValidationError:
            return Response({'code': 400, 'msg': f'请输入合法的{current_currency_name()}金额'})
        if amount <= 0:
            return Response({'code': 400, 'msg': '金额必须大于 0'})
        reason = request.data.get('reason', '')
        if not isinstance(reason, str) or len(reason.strip()) > 255:
            return Response({'code': 400, 'msg': '原因须为255字以内的文本'})
        reason = reason.strip()

        is_reward = dispose_type == DisposeRecord.DisposeType.REWARD
        signed = amount if is_reward else -amount

        lifecycle_provider = None
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
            lifecycle_provider = ClubAccount.objects.filter(pk=profile.account_id,
                account_type=ClubAccount.AccountType.PROVIDER).first()
            if not lifecycle_provider:
                return Response({'code': 409, 'msg': '陪玩账号归属待核对'}, status=409)
            try:
                ensure_provider_lifecycle(lifecycle_provider)
            except ProviderLifecycleError:
                return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系客服'}, status=503)

        with transaction.atomic():
            if lifecycle_provider is not None:
                from club_accounts.provider_lifecycle import lock_provider_operation
                try:
                    lock_provider_operation(lifecycle_provider)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系客服'}, status=503)
                if not ClubAccount.objects.filter(pk=lifecycle_provider.pk,
                        account_type=ClubAccount.AccountType.PROVIDER, license_membership_active=True,
                        business_cycle=lifecycle_provider.business_cycle).exists():
                    return Response({'code': 409, 'msg': '陪玩账号周期已变化，请重新核对'}, status=409)
            wallet = get_wallet(
                user_id=profile.user_id,
                account_id=profile.account_id,
                for_update=True,
            )
            replayed, duplicate_reason = peek_fund_write(account=request.account, request_id=request.data.get('client_request_id'))
            if replayed:
                return Response({'code': 409, 'msg': duplicate_reason}, status=409)
            if wallet.balance + signed < 0:
                return Response({'code': 400, 'msg': '罚款后余额不能为负'})
            if wallet.balance + signed > 2147483647:
                return Response({'code': 400, 'msg': '奖励后余额超过系统范围'})
            locked_profile = EscortProfile.objects.select_for_update().get(pk=profile.pk)
            if lifecycle_provider is not None and (locked_profile.account_id != lifecycle_provider.pk
                    or locked_profile.user_id != profile.user_id):
                return Response({'code': 409, 'msg': '陪玩档案归属已变化，请重新核对'}, status=409)
            total = locked_profile.total_reward if is_reward else locked_profile.total_penalty
            if total + amount > 2147483647:
                return Response({'code': 400, 'msg': '奖罚累计超过系统范围'})
            allowed, duplicate_reason = claim_fund_write(
                account=request.account, request_id=request.data.get('client_request_id'),
                scope='admin.dispose', fingerprint=f'{profile.pk}:{dispose_type}:{amount}:{reason}',
            )
            if not allowed:
                return Response({'code': 409, 'msg': duplicate_reason}, status=409)
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
                operator=request.legacy_user,
                operator_account=request.account,
            )

            if is_reward:
                locked_profile.total_reward += amount
                locked_profile.save(update_fields=['total_reward'])
            else:
                locked_profile.total_penalty += amount
                locked_profile.save(update_fields=['total_penalty'])

            record = DisposeRecord.objects.create(
                user_id=profile.user_id,
                account_id=wallet.account_id,
                dispose_type=dispose_type,
                amount=amount,
                reason=reason,
                operator=request.legacy_user,
                operator_account=request.account,
                transaction=tx,
            )
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
            return fund_success(request, 'admin.dispose', data={
                'record': AdminDisposeRecordSerializer(record).data,
                'balance_after': wallet.balance,
            }, msg='奖罚已记账')


# ---------------- 订单 ----------------
STAFF_DIRECT_VERIFY_LIMIT = 500


class OrderViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminOrderSerializer
    default_perm = 'order:view'
    required_perms = {
        'refund': 'order:refund',
        'refund_preview': 'order:refund',
        'negotiated_refund_options': 'order:refund',
        'subsidy_negotiation_options': 'order:refund',
        'subsidy_negotiation_preview': 'order:refund',
        'subsidy_negotiation_confirm': 'order:refund',
        'negotiated_refund_preview': 'order:refund',
        'negotiated_refund': 'order:refund',
        'cancel': 'order:cancel',
        'quick_dispatch': 'order:dispatch',
        'enable_team_evidence': 'order:dispatch',
        'dispatch_message': 'order:dispatch',
        'dispatch_quote': 'order:dispatch',
        'assign': 'order:dispatch',
        'start': 'order:dispatch',
        'transfer': 'order:dispatch',
        'complete': 'order:settle',
    }

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return AdminOrderDetailSerializer
        if self.action == 'list':
            return AdminOrderWithIdentitySerializer
        return super().get_serializer_class()

    @action(detail=True, methods=['post'], url_path='enable-team-evidence')
    @transaction.atomic
    def enable_team_evidence(self, request, pk=None):
        """Operator-confirmed upgrade for a legacy team that has not started."""
        if request.data.get('confirmed') is not True:
            return Response({'code': 400, 'msg': '请先与双方核实服务尚未开始'})
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        if (order.escort_mode != Order.EscortMode.DOUBLE or order.requires_boss_selection
                or order.status != Order.Status.GRABBED or order.payment_status != Order.PaymentStatus.PAID):
            return Response({'code': 409, 'msg': '订单状态已变化，请刷新后核对'})
        members = list(order.providers.filter(released_at__isnull=True).values_list('provider_id', 'provider_account_id'))
        if ProviderReport.objects.filter(order=order).exists():
            return Response({'code': 409, 'msg': '订单已有报单，无法切换；请按原流程完成服务，由双方分别补齐凭证并提交报单'})
        if (len(members) != 2 or not all(user_id and account_id for user_id, account_id in members)
                or len({account_id for _, account_id in members}) != 2
                or order.status_logs.filter(action__in=[OrderStatusLog.Action.START, OrderStatusLog.Action.TRANSFER]).exists()):
            return Response({'code': 409, 'msg': '订单已有履约记录或成员待核对，请联系双方人工处理'})
        order.requires_boss_selection = True
        order.save(update_fields=['requires_boss_selection', 'updated_at'])
        log_only(order, action=OrderStatusLog.Action.TEAM_WORKFLOW, operator=request.legacy_user,
                 operator_account=request.account, reason='客服核实服务尚未开始，启用双方凭证流程')
        transaction.on_commit(lambda: notify_order_update(order))
        for user_id, account_id in members:
            transaction.on_commit(lambda user_id=user_id, account_id=account_id: _push_message_safe(
                recipient_id=user_id, recipient_account_id=account_id, title='双陪履约方式已更新',
                preview=f'「{order.service_name_snapshot}」请两位陪玩分别提交入队和结单截图。',
                msg_type='ORDER', related_order_id=order.pk))
        transaction.on_commit(lambda: _push_message_safe(
            recipient_id=order.customer_id, recipient_account_id=order.customer_account_id,
            title='双陪服务进度已更新',
            preview=f'「{order.service_name_snapshot}」将由两位陪玩分别提交入队和结单截图，可在订单中查看进度。',
            msg_type='ORDER', related_order_id=order.pk))
        return Response({'code': 0, 'msg': '已启用双方凭证流程', 'data': self.get_serializer(order).data})

    def get_queryset(self):
        qs = Order.objects.select_related('customer', 'customer_account', 'provider__escort_profile', 'provider_account__escort_profile', 'service__service_category', 'direct_payment__fee_quote', 'source_order').prefetch_related(
            'providers__provider__escort_profile', 'providers__provider_account__escort_profile', 'aftersales'
        ).order_by('-created_at', '-pk')
        if self.action == 'retrieve':
            from orders.applications_models import OrderApplication
            pending = OrderApplication.objects.filter(order_id=OuterRef('pk'), status=OrderApplication.Status.PENDING).order_by()
            def count_subquery(rows):
                return Coalesce(Subquery(rows.values('order_id').annotate(n=Count('pk')).values('n')[:1], output_field=IntegerField()), Value(0))
            qs = qs.annotate(_pending_applications=count_subquery(pending),
                             _waiting_partner_applications=count_subquery(pending.filter(partner__isnull=False, partner_confirmed=False)))
        if self.request.query_params.get('exclude_direct_test') == '1':
            qs = qs.filter(direct_payment__isnull=True)
        kind = self.request.query_params.get('order_kind')
        if kind:
            if kind not in ('SERVICE', 'GIFT'):
                raise rest_serializers.ValidationError('订单类型无效')
            qs = qs.filter(is_gift_snapshot=(kind == 'GIFT'))
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        payment = self.request.query_params.get('payment_status')
        if payment:
            qs = qs.filter(payment_status=payment.upper())
        dispatch_route = self.request.query_params.get('dispatch_route')
        if dispatch_route:
            qs = qs.filter(dispatch_route=dispatch_route.upper())
        if self.request.query_params.get('overdue_pending') == '1':
            qs = qs.filter(status=Order.Status.PENDING, payment_status=Order.PaymentStatus.PAID,
                           auto_cancel_at__lte=timezone.now())
        if self.request.query_params.get('legacy_team') == '1':
            qs = qs.filter(escort_mode=Order.EscortMode.DOUBLE, requires_boss_selection=False,
                           status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE])
        actionable_staff = self.request.query_params.get('actionable_staff') == '1'
        if self.request.query_params.get('aftersale_open') == '1':
            open_ticket = AfterSale.objects.filter(order_id=OuterRef('pk'), status='OPEN')
            qs = qs.filter(Exists(open_ticket))
            if not actionable_staff:
                # Queue age belongs to the current request, not the order's
                # original creation date; a resolved case can be reopened.
                qs = qs.annotate(_open_aftersale_at=Subquery(open_ticket.values('created_at')[:1])).order_by('_open_aftersale_at', 'pk')
        def customer_field_match(field, value):
            # Match the same identity displayed in AdminOrderSerializer. A
            # linked business account takes precedence over legacy fields.
            return Q(**{f'customer_account__{field}__icontains': value}) | (
                Q(customer_account__isnull=True) & Q(**{f'customer__{field}__icontains': value})
            )
        keyword = self.request.query_params.get('keyword')
        if keyword:
            from identifiers.services import entity_ids_for_code
            qs = qs.filter(
                Q(order_no__icontains=keyword)
                | Q(pk__in=entity_ids_for_code(keyword, 'orders.order'))
                | customer_field_match('username', keyword)
                | customer_field_match('phone', keyword)
            )
        order_no = self.request.query_params.get('order_no')
        if order_no:
            from identifiers.services import entity_ids_for_code
            qs = qs.filter(Q(order_no__icontains=order_no) | Q(pk__in=entity_ids_for_code(order_no, 'orders.order')))
        order_no_exact = self.request.query_params.get('order_no_exact')
        if order_no_exact:
            from identifiers.services import entity_ids_for_code
            qs = qs.filter(Q(order_no=order_no_exact) | Q(pk__in=entity_ids_for_code(order_no_exact, 'orders.order', exact=True)))
        created_day = self.request.query_params.get('created_day')
        if created_day:
            try:
                day = datetime.strptime(created_day, '%Y-%m-%d').date()
                if day.isoformat() != created_day or not 1000 <= day.year <= 9998:
                    raise ValueError
            except ValueError:
                raise rest_serializers.ValidationError('订单创建日期无效')
            zone = timezone.get_current_timezone()
            start = timezone.make_aware(datetime.combine(day, time.min), zone)
            end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), zone)
            qs = qs.filter(created_at__gte=start, created_at__lt=end)
            self._created_day_applied = {'date': created_day, 'timezone': str(zone)}
        refund_day = self.request.query_params.get('refund_day')
        if refund_day:
            try:
                day = datetime.strptime(refund_day, '%Y-%m-%d').date()
                if day.isoformat() != refund_day or not 1000 <= day.year <= 9998:
                    raise ValueError
            except ValueError:
                raise rest_serializers.ValidationError('退款日期无效')
            zone = timezone.get_current_timezone()
            start = timezone.make_aware(datetime.combine(day, time.min), zone)
            end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), zone)
            from orders.models import OrderRefund
            refunds = OrderRefund.objects.filter(
                order_id=OuterRef('pk'), amount__gt=0, created_at__gte=start, created_at__lt=end,
            ).order_by()
            totals = refunds.values('order_id').annotate(amount=Sum('amount'), count=Count('pk'), latest=Max('created_at'))
            qs = qs.filter(Exists(refunds)).annotate(
                _refund_day_amount=Subquery(totals.values('amount')[:1]),
                _refund_day_count=Subquery(totals.values('count')[:1]),
                _refund_day_latest=Subquery(totals.values('latest')[:1]),
            )
            self._refund_day_applied = {'date': refund_day, 'timezone': str(zone)}
        customer_account_id = self.request.query_params.get('customer_account_id')
        if customer_account_id:
            if not customer_account_id.isascii() or not customer_account_id.isdigit() or len(customer_account_id) > 18 or int(customer_account_id) <= 0:
                raise rest_serializers.ValidationError('老板账户编号无效')
            account_id = int(customer_account_id)
            legacy_ids = LegacyAccountMap.objects.filter(account_id=account_id).values('legacy_user_id')
            qs = qs.filter(Q(customer_account_id=account_id) | Q(customer_account__isnull=True, customer_id__in=legacy_ids))
        customer_username = self.request.query_params.get('customer_username')
        if customer_username:
            qs = qs.filter(customer_field_match('username', customer_username))
        customer_nickname = self.request.query_params.get('customer_nickname')
        if customer_nickname:
            qs = qs.filter(customer_field_match('nickname', customer_nickname))
        customer_phone = self.request.query_params.get('customer_phone')
        if customer_phone:
            qs = qs.filter(customer_field_match('phone', customer_phone))
        if actionable_staff:
            from orders.staff_dispatch_readiness import dispatch_assignment_allowed, staff_dispatch_candidate_q
            from django.db import DatabaseError
            # Verify a bounded set of direct-payment candidates *before*
            # pagination and COUNT. Balance orders do not go through this
            # scan and remain visible even if the direct-payment bound is hit.
            now = timezone.now()
            candidate_q = staff_dispatch_candidate_q(now=now)
            base = qs.filter(Q(auto_cancel_at__isnull=True) | Q(auto_cancel_at__gt=now),
                             dispatch_route=Order.DispatchRoute.STAFF,
                             status=Order.Status.PENDING, payment_status=Order.PaymentStatus.PAID,
                             provider__isnull=True, provider_account__isnull=True)
            # Check the earliest deadlines first before applying the bounded
            # direct-payment verification, then use the same order for pages.
            queue_order = (F('auto_cancel_at').asc(nulls_last=True), 'created_at', 'pk')
            candidates = qs.filter(candidate_q).order_by(*queue_order)
            needs_review = base.filter(direct_payment__isnull=False).exclude(candidate_q).exists()
            direct_rows = list(candidates.filter(direct_payment__isnull=False).select_related(
                'direct_payment__fee_quote')[:STAFF_DIRECT_VERIFY_LIMIT + 1])
            if len(direct_rows) > STAFF_DIRECT_VERIFY_LIMIT:
                needs_review = True
                direct_rows = direct_rows[:STAFF_DIRECT_VERIFY_LIMIT]
            valid_ids = []
            for order in direct_rows:
                try:
                    if dispatch_assignment_allowed(order, now=now):
                        valid_ids.append(order.pk)
                    else:
                        needs_review = True
                except DatabaseError:
                    raise
                except Exception:
                    # Do not leak or silently count malformed financial data.
                    logging.getLogger(__name__).warning('待派订单校验失败: order_id=%s', order.pk)
                    needs_review = True
            self._staff_queue_needs_review = needs_review
            qs = candidates.filter(Q(direct_payment__isnull=True) | Q(pk__in=valid_ids)).order_by(*queue_order)
        elif self.request.query_params.get('overdue_pending') == '1' and self.request.query_params.get('aftersale_open') != '1':
            qs = qs.order_by('auto_cancel_at', 'created_at', 'pk')
        elif refund_day and self.request.query_params.get('aftersale_open') != '1':
            qs = qs.order_by('-_refund_day_latest', '-pk')
            self._refund_day_order = 'latest_desc'
        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if request.query_params.get('order_kind') in ('SERVICE', 'GIFT') and response.data.get('code') == 0:
            response.data['data']['order_kind_applied'] = request.query_params['order_kind']
        if request.query_params.get('exclude_direct_test') == '1' and response.data.get('code') == 0:
            response.data['data']['exclude_direct_test_applied'] = True
        if request.query_params.get('legacy_team') == '1' and response.data.get('code') == 0:
            response.data['data']['legacy_team_applied'] = True
        if request.query_params.get('overdue_pending') == '1' and response.data.get('code') == 0:
            response.data['data']['overdue_pending_applied'] = True
            if request.query_params.get('actionable_staff') != '1' and request.query_params.get('aftersale_open') != '1':
                response.data['data']['overdue_pending_order'] = 'deadline_asc'
        if request.query_params.get('aftersale_open') == '1' and request.query_params.get('actionable_staff') != '1' and response.data.get('code') == 0:
            response.data['data']['aftersale_queue_order'] = 'opened_asc'
        if request.query_params.get('order_no_exact') and response.data.get('code') == 0:
            response.data['data']['order_no_exact_applied'] = True
        if request.query_params.get('customer_account_id') and response.data.get('code') == 0:
            response.data['data']['customer_account_id_applied'] = str(int(request.query_params['customer_account_id']))
        if request.query_params.get('created_day') and response.data.get('code') == 0:
            response.data['data']['created_day_applied'] = getattr(self, '_created_day_applied', None)
        if request.query_params.get('refund_day') and response.data.get('code') == 0:
            response.data['data']['refund_day_applied'] = getattr(self, '_refund_day_applied', None)
            response.data['data']['refund_day_order'] = getattr(self, '_refund_day_order', None)
        if request.query_params.get('actionable_staff') == '1' and response.data.get('code') == 0:
            response.data['data']['staff_queue_needs_review'] = bool(getattr(self, '_staff_queue_needs_review', True))
            response.data['data']['staff_queue_order'] = 'deadline_asc'
        return response

    @action(detail=False, methods=['get'], url_path='dispatch-quote')
    def dispatch_quote(self, request):
        from dataclasses import asdict
        from orders.pricing import compute_order_amount, resolve_active_promotion
        try:
            rounds = int(request.query_params.get('game_rounds', 1))
            if not 1 <= rounds <= 999:
                raise ValueError
            service = ServiceItem.objects.get(pk=int(request.query_params.get('service_id', 0)), is_active=True)
            boss = ClubAccount.objects.select_related('boss_type').get(
                pk=int(request.query_params.get('customer_id', 0)), account_type=ClubAccount.AccountType.BOSS, is_active=True,
            )
        except (ValueError, TypeError, ServiceItem.DoesNotExist, ClubAccount.DoesNotExist):
            return Response({'code': 400, 'msg': '请选择有效的老板、服务和局数'}, status=400)
        from orders.purchase_benefits import is_experience, prepare_purchase
        coupon_id = rest_serializers.IntegerField(min_value=1, allow_null=True).run_validation(request.query_params.get('user_coupon_id'))
        token = request.query_params.get('discount_selection_token')
        options = []
        selected = None
        recommended = None
        if not is_experience(service) and not service.is_addon and not (
                service.service_category_id and service.service_category.is_gift):
            from orders.discount_recommendation import service_discount_options
            recommended = asdict(service_discount_options(boss, service, rounds)[0])
        discount_enabled = getattr(settings, 'DISCOUNT_COMBINATIONS_PAYMENT_ENABLED', False) and not is_experience(service)
        if discount_enabled:
            if coupon_id:
                raise rest_serializers.ValidationError('请通过优惠组合选择优惠券')
            from orders.discount_recommendation import service_discount_options
            from orders.discount_selection import preview_selected_discount, signed_option
            from orders.pricing import DiscountResult
            candidates = service_discount_options(boss, service, rounds)
            options = [{**asdict(item), 'selection_token': signed_option(boss, service, rounds, item)} for item in candidates]
            token = token or options[0]['selection_token']
            selected, promotion, coupon = preview_selected_discount(boss, service, rounds, token)
            pricing = DiscountResult(selected.original_amount, selected.boss_discount, selected.promo_discount,
                                     selected.coupon_discount, selected.amount)
            for option in options:
                if option['key'] == selected.key:
                    option['selection_token'] = token
                    break
        else:
            if token:
                raise rest_serializers.ValidationError('优惠组合付款暂未开放')
            pricing, promotion, coupon = prepare_purchase(boss, service, rounds, coupon_id)
        points_quote = None
        points_token = None
        if 'preview_points_hundredths' in request.query_params:
            from orders.points_checkout_test import quote_for_checkout, require_isolated_checkout
            formal_points = getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            if not formal_points:
                require_isolated_checkout()
            selected_points = rest_serializers.IntegerField(min_value=0).run_validation(
                request.query_params['preview_points_hundredths'])
            from engagement.funding_wallet import parse_funding_selection
            gift, compensation = parse_funding_selection(request.query_params)
            quote_data = {**asdict(pricing), 'service': service, 'promotion': promotion,
                          'game_rounds': rounds, 'gift_balance_amount': gift,
                          'compensation_balance_amount': compensation, 'user_coupon': coupon,
                          'discount_selection_token': token}
            if formal_points:
                from orders.grab_single_flow import enabled as single_grab_enabled
                from orders.grab_team_flow import enabled as team_grab_enabled
                from orders.points_business_access import checkout
                from orders.points_staff_checkout import pending_quote_data
                if (service.fulfillment_provider_count not in (1, 2) or not
                        (team_grab_enabled() if service.fulfillment_provider_count == 2 else single_grab_enabled())):
                    raise rest_serializers.ValidationError('当前积分抢单报价暂不可用')
                quote_data = pending_quote_data(quote_data)
                with transaction.atomic(), checkout(account=boss, service=service):
                    if quote_data.get('commission_allocation_pending'):
                        from orders.delta_personal_commission import verify_formal_points_checkout
                        locked_service = ServiceItem.objects.select_for_update().get(pk=service.pk)
                        verify_formal_points_checkout(quote_data, account=boss, service=locked_service)
                    points_quote, points_token = quote_for_checkout(boss, quote_data, selected_points)
            else:
                points_quote, points_token = quote_for_checkout(boss, quote_data, selected_points)
        data = asdict(pricing)
        if points_quote:
            data['points_checkout'] = {**points_quote['calculation'],
                                       'checkout_test_token': points_token, 'test_only': not formal_points}
        data.update({'discount_selection_enabled': discount_enabled,
                     'discount_selection_token': token if discount_enabled else None,
                     'discount_option_key': selected.key if selected else None,
                     'discount_options': options, 'recommended_discount': recommended})
        from orders.account_fields import effective
        try:
            data['account_fields'] = effective(service)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        from engagement.funding_wallet import funding_quote, parse_funding_selection
        gift, compensation = parse_funding_selection(request.query_params)
        if getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False):
            from wallet.services import get_wallet
            wallet = get_wallet(account=boss, create=False)
            data.update(funding_quote(wallet, points_quote['calculation']['payable_units'] if points_quote else pricing.amount, gift, compensation))
        else:
            data['funding_selection_enabled'] = False
        data['user_coupon_id'] = coupon.pk if coupon else None
        data['commission_policy'] = 'EXPERIENCE' if service.experience_kind else service.commission_policy
        from orders.commission import quote, override_from
        from orders.pricing import PricingError
        try:
            data.update(quote(service,pricing.amount,promotion=promotion,override=override_from(request.query_params)))
        except PricingError as exc:
            return Response({'code':400,'msg':str(exc)},status=400)
        return Response({'code': 0, 'data': data})

    @action(detail=True, methods=['get', 'post'], url_path='dispatch-message')
    def dispatch_message(self, request, pk=None):
        order = self.get_object()
        if request.method == 'GET':
            return Response({'code': 0, 'data': dispatch_preview(order)})
        channels = request.data.get('channels')
        if (not isinstance(channels, list) or not 1 <= len(channels) <= 2
                or any(c not in ('KOOK', 'WECOM') for c in channels)
                or len(set(channels)) != len(channels)):
            return Response({'code': 400, 'msg': '请选择有效的发单渠道'}, status=400)
        expected = request.data.get('confirmation_keys')
        if (not isinstance(expected, dict) or set(expected) != set(channels)
                or any(not isinstance(value, str) or len(value) != 64 for value in expected.values())):
            return Response({'code': 400, 'msg': '请先读取发单预览并确认渠道'}, status=400)
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)
            preview, plans = dispatch_preview(order, include_plans=True)
            if not preview['can_send']:
                return Response({'code': 409, 'msg': preview['reason']}, status=409)
            current = {item['channel']: item['confirmation_key'] for item in preview['channels']}
            if any(current[channel] != expected[channel] for channel in channels):
                return Response({'code': 409, 'errorCode': 'DISPATCH_PREVIEW_CHANGED', 'msg': '订单、渠道或文案已变化，请刷新并重新确认'}, status=409)
            if any(not plans[c]['ready'] for c in channels):
                return Response({'code': 400, 'msg': '所选渠道尚未配置，可先复制文案'}, status=400)
            for channel in channels:
                queue_dispatch(order, channel, manual=True, prepared=plans[channel])
        return Response({'code': 0, 'data': dispatch_preview(order), 'msg': '已处理发单请求'})

    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        order = self.get_object()
        logs = order.status_logs.select_related('operator').all()
        return Response({'code': 0, 'data': AdminOrderLogSerializer(logs, many=True).data})

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """New service orders are allocated by the pool or by the boss's audition choice."""
        self.get_object()
        return Response({
            'code': 409,
            'msg': '客服安排的新单由抢单池自动分发；试音由老板选人，已安排订单请使用改派或转单流程',
        }, status=409)

    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        """转单：服务中订单将部分或全部打手转交新打手。

        对每一名被转出的旧打手：按客服填写的比例/固定额保留其应得份额，
        剩余份额转给新打手。审核结算单须旧、新及留守打手全部报单通过后入账；
        旧完成结算模式保留转出时立即入账的行为。
        同时对旧打手扣除罚款。旧打手余额（含本次刚结算入账的分钱）不足以扣罚款时，
        整笔转单失败并回滚。旧打手状态恢复可接单，新打手置忙碌。
        """
        order = self.get_object()
        if order.status == Order.Status.GRABBED:
            if not getattr(settings, 'PRESTART_HANDOFF_ENABLED', False):
                return Response({'code': 400, 'msg': '服务开始前的改派尚未开放'})
            from orders.prestart_handoff import reassign
            try:
                order, changes = reassign(order.pk, request.data, operator=request.account, operator_user=request.legacy_user)
            except IllegalTransitionError as exc:
                return Response({'code': 400, 'msg': str(exc)})
            for old_id, new_id in changes:
                _push_message_safe(recipient_account_id=old_id, title='订单服务安排已调整',
                    preview=f'「{order.service_name_snapshot}」已由其他陪玩接替，无需提交本单报单', msg_type='ORDER', related_order_id=order.pk)
                _push_message_safe(recipient_account_id=new_id, title='客服为你安排订单',
                    preview=f'请查看「{order.service_name_snapshot}」并按约定开始服务', msg_type='ORDER', assignment_alert=True, related_order_id=order.pk)
            _push_message_safe(recipient_id=order.customer_id, recipient_account_id=order.customer_account_id,
                title='订单服务安排已调整', preview=f'「{order.service_name_snapshot}」已更换陪玩', msg_type='ORDER', related_order_id=order.pk)
            return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '改派成功'})
        from orders.segment_financial_capture import enabled as segment_capture_enabled
        game_transfer_enabled = bool(getattr(settings, 'SEGMENT_GAME_TRANSFER_ENABLED', False) and segment_capture_enabled())
        standard_transfer_enabled = bool(getattr(settings, 'SEGMENT_STANDARD_TRANSFER_ENABLED', False) and segment_capture_enabled())
        from orders.principal_segments import enabled_for_order
        from orders.ordinary_transfer_capability import enabled as refunded_transfer_enabled
        from orders.models import OrderTransferNegotiationEvidence, OrdinaryPostRefundTransferRevision, NegotiationExecution, OrderOriginalShareTransfer
        from orders.negotiation_zero import marked_execution_id
        zero_id = marked_execution_id(order)
        positive_before_first_hint = bool(refunded_transfer_enabled() and not zero_id
            and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            and NegotiationExecution.objects.filter(refund__order=order,
                termination_processed_at__isnull=False).exists())
        refunded_transfer_hint = bool(refunded_transfer_enabled()
            and (OrderTransferNegotiationEvidence.objects.filter(order=order,
                execution__termination_processed_at__isnull=False).exists()
                or (zero_id and OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    snapshot__zero_pretransfer_execution_id=zero_id).exists())
                or positive_before_first_hint or OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    snapshot__positive_pretransfer_execution_id__isnull=False).exists()))
        standard_transfer_enabled = standard_transfer_enabled or enabled_for_order(order) or refunded_transfer_hint
        game_transfer_enabled = game_transfer_enabled or bool(refunded_transfer_hint and order.game_commission_snapshot)
        from wallet.refund_recovery_order import enabled as recovery_enabled
        from orders.segment_settlement import (enabled as segment_settlement_enabled,
                                                completion_transfer_order_eligible)
        completion_transfer_test = (order.settlement_mode == Order.SettlementMode.COMPLETION
                                    and standard_transfer_enabled and (completion_transfer_order_eligible(order)
                                                                      or refunded_transfer_hint))
        if order.settlement_mode == Order.SettlementMode.COMPLETION and not completion_transfer_test:
            return Response({'code': 400, 'msg': '该订单的逐段报单及结算尚未接通，暂不能转单'})
        if not order.game_commission_snapshot and not standard_transfer_enabled:
            return Response({'code': 400, 'msg': '转入陪玩的抽成快照尚未接通，暂不能转单'})
        recovery_transfer_enabled = bool(recovery_enabled() and segment_settlement_enabled()
            and (game_transfer_enabled if order.game_commission_snapshot else standard_transfer_enabled)
            and order.settlement_mode == Order.SettlementMode.REPORT_APPROVAL)
        if order.game_commission_snapshot and not game_transfer_enabled:
            return Response({'code': 400, 'msg': '游戏抽成订单的转入快照尚未完成，请暂勿转单'})
        if order.status != Order.Status.IN_SERVICE:
            return Response({'code': 400, 'msg': '仅服务中的订单可转单'})
        from orders.direct_payment_models import DirectPaymentIntent
        if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
            return Response({'code': 400, 'msg': '微信支付订单转单及结算尚未开放'})

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
            if new_profile is None or new_profile.status == EscortProfile.Status.OFFLINE:
                return Response({'code': 400, 'msg': f'{new_provider.nickname or new_provider.username} 当前不可接单'})
            if not EscortSchedule.provider_is_scheduled_now(new_provider):
                return Response({'code': 400, 'msg': f'{new_provider.nickname or new_provider.username} 当前不在接单档期'})
            from orders.paid_service_scope import acceptance_error
            if acceptance_error(new_profile, order, allow_refund_recovery=recovery_transfer_enabled,
                                refund_recovery_order_id=order.pk if recovery_transfer_enabled else None):
                return Response({'code': 400, 'msg': f'{new_provider.nickname or new_provider.username} 当前不符合原订单接单要求'})
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
                'original_share': item.get('original_share'),
            })

        reason = (request.data.get('reason') or '转单').strip()[:255]
        ensure_provider_dispatch_locks(cmd['new_provider_account'].pk for cmd in parsed)

        try:
            from orders.points_transfer_revision_scope import revision_scope, active as funding_revision_active
            from orders.transfer_lifecycle import capture as capture_transfer_lifecycle, lock as lock_transfer_lifecycle
            lifecycle = capture_transfer_lifecycle([
                (cmd['old_provider_account_id'], cmd['new_provider_account'].pk) for cmd in parsed
            ])
            with transaction.atomic(), revision_scope(order.pk):
                lock_transfer_lifecycle(lifecycle)
                lifecycle_cycles = ({account.pk: account.business_cycle for account in lifecycle[0]}
                                    if lifecycle is not None else None)
                incoming_ids = {cmd['new_provider_account'].pk for cmd in parsed}
                incoming_cycles = (lifecycle_cycles if lifecycle_cycles is not None else dict(
                    ClubAccount.objects.select_for_update().filter(pk__in=incoming_ids,
                        account_type=ClubAccount.AccountType.PROVIDER).order_by('pk')
                    .values_list('pk', 'business_cycle')))
                if any(type(incoming_cycles.get(pk)) is not int or incoming_cycles[pk] <= 0 for pk in incoming_ids):
                    raise IllegalTransitionError('转入陪玩业务周期待核对')
                locked_order = Order.objects.select_for_update().get(pk=order.pk)
                if locked_order.status != Order.Status.IN_SERVICE:
                    raise IllegalTransitionError('订单状态已变更，无法转单')
                # Serialize persisted payment evidence with refund and settlement
                # before using it to authorize any part of this transfer.
                from orders.financial_lock import lock_order_money
                lock_order_money()
                from orders.points_addon_receipt_usage import prelock as prelock_order_receipts
                prelock_order_receipts(locked_order)
                in_flight_principal = enabled_for_order(locked_order)
                from orders.ordinary_transfer_capability import permits as refunded_transfer_permits
                in_flight_refund = refunded_transfer_permits(locked_order)
                if positive_before_first_hint and not in_flight_refund:
                    from orders.ordinary_positive_pretransfer import verify as verify_positive_pretransfer
                    try:
                        in_flight_refund = bool(verify_positive_pretransfer(locked_order, before=timezone.now()))
                    except DjangoValidationError as exc:
                        raise IllegalTransitionError('转单前退款原协商待核对') from exc
                if positive_before_first_hint and in_flight_refund:
                    from orders.models import OrderOriginalAttribution, OrderSegmentFinancialCoverage
                    attribution = OrderOriginalAttribution.objects.select_for_update().filter(order=locked_order).first()
                    coverage = (OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution=attribution).first()
                                if attribution else None)
                    reason = '协商退款后原价份额待核对'
                    if (not attribution or not coverage or attribution.status != 'REVIEW'
                            or attribution.review_reason != reason or coverage.status != 'REVIEW'
                            or coverage.review_reason != reason or coverage.source != 'WALLET_NO_POINTS'
                            or coverage.deal_amount != locked_order.amount
                            or attribution.original_amount != locked_order.original_amount
                            or sum(attribution.segments.values_list('original_share', flat=True))
                               != attribution.original_amount + attribution.rounding_surplus):
                        raise IllegalTransitionError('退款后的原价参与份额待核对')
                    attribution.status, attribution.review_reason = 'ACTIVE', ''
                    attribution.save(update_fields=['status', 'review_reason'])
                    coverage.status, coverage.review_reason = 'ACTIVE', ''
                    coverage.save(update_fields=['status', 'review_reason'])
                if refunded_transfer_hint and not in_flight_refund:
                    raise IllegalTransitionError('退款后转单资金凭证已变化，请先核对')
                if standard_transfer_enabled and not segment_capture_enabled() and not in_flight_principal and not in_flight_refund:
                    raise IllegalTransitionError('订单付款或分段事实已变化，请先核对')
                if completion_transfer_test and not completion_transfer_order_eligible(locked_order) and not in_flight_refund:
                    raise IllegalTransitionError('订单付款或退款状态已变化，请先核对')
                from orders.models import OrderPlatformSubsidy, OrderSegmentFinancialCoverage
                subsidy_segment = OrderSegmentFinancialCoverage.objects.filter(
                    attribution__order=locked_order, source='PLATFORM_SUBSIDY').exists()
                if OrderPlatformSubsidy.objects.filter(order=locked_order).exists():
                    from orders.subsidy_segment_capture import transfer_testing_enabled
                    if (not subsidy_segment or not transfer_testing_enabled()
                            or locked_order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL):
                        raise IllegalTransitionError('安全箱服务中转单的逐段补贴和结算尚未接通')
                if (locked_order.room_required_snapshot is True
                        and locked_order.settlement_mode == Order.SettlementMode.COMPLETION):
                    raise IllegalTransitionError('房间订单仍使用旧结算模式，请先核对并迁移后再转单')
                from orders.segment_financial_capture import enabled as segment_capture_enabled, enforcement_enabled as segment_enforcement_enabled
                if segment_enforcement_enabled() or in_flight_principal:
                    from orders.models import OrderSegmentFinancialCoverage
                    if OrderSegmentFinancialCoverage.objects.filter(attribution__order=locked_order).exists():
                        if not segment_capture_enabled() and not in_flight_principal and not in_flight_refund:
                            raise IllegalTransitionError('分段成交记录暂不可用，请先恢复记录后再转单')
                        if locked_order.settlement_mode == Order.SettlementMode.COMPLETION and not completion_transfer_test:
                            raise IllegalTransitionError('该订单仍使用旧结算模式，请先完成结算规则迁移')
                        if any(cmd['split_value'] for cmd in parsed):
                            raise IllegalTransitionError('分段订单不支持手工填写旧收益分配，请按原价份额核算')
                from orders.refunds import negotiated_aftersale_blocks_completion
                if negotiated_aftersale_blocks_completion(locked_order):
                    raise IllegalTransitionError('售后处理中，请先处理售后再转单')
                if DirectPaymentIntent.objects.filter(order_id=locked_order.pk).exists():
                    raise IllegalTransitionError('微信支付订单转单及结算尚未开放')
                if subsidy_segment:
                    from orders.subsidies import _locked_subsidy_shares
                    try:
                        _locked_subsidy_shares(locked_order)
                    except SettlementError as exc:
                        raise IllegalTransitionError('安全箱原补贴及抽成快照待核对') from exc
                from orders.points_service_payment import needs_integrated_processing
                third_transfer_capture = None
                if needs_integrated_processing(locked_order):
                    from django.db import DatabaseError
                    from orders.points_transfer_capture import enabled as points_transfer_capture_enabled, locked_deal
                    if not points_transfer_capture_enabled():
                        raise IllegalTransitionError('积分订单转单的资金处理尚未接通')
                    try:
                        from orders.models import OrderOriginalShareTransfer
                        from orders.points_funding_plan import plan_verified_order
                        if locked_order.refund_records.exists():
                            from orders.points_transfer_refund_access import verification_scope
                            from orders.points_funding_evidence import verify_order_funding
                            with verification_scope(locked_order):
                                if locked_order.refund_records.count() >= 3:
                                    from orders.points_third_transfer import capture_locked
                                    third_transfer_capture = capture_locked(locked_order)
                                elif locked_order.refund_records.count() == 2:
                                    from orders.points_transfer_followup_funding import plan_locked
                                    plan_locked(locked_order.pk, allow_in_service=True)
                                else:
                                    from orders.points_transfer_refunded_funding import plan_refunded_transfer
                                    plan_refunded_transfer(locked_order.pk)
                                if locked_order.refund_records.count() < 3:
                                    verify_order_funding(locked_order.pk, allow_unstarted=True)
                        elif funding_revision_active(locked_order):
                            locked_deal(locked_order, allow_existing_allocations=True)
                            from orders.points_funding_evidence import verify_order_funding
                            verify_order_funding(locked_order.pk, allow_unstarted=True)
                        elif not OrderOriginalShareTransfer.objects.filter(
                                previous_segment__attribution__order=locked_order).exists():
                            locked_deal(locked_order)
                            plan_verified_order(locked_order.pk)
                        else:
                            locked_deal(locked_order)
                    except (rest_serializers.ValidationError, ValueError, DatabaseError) as exc:
                        raise IllegalTransitionError('积分订单付款或收益快照待核对') from exc
                game_catalog = None
                if locked_order.game_commission_snapshot or standard_transfer_enabled:
                    from orders.models import ServiceItem, OrderSegmentFinancialCoverage
                    from orders.segment_financial_capture import TRANSFER_REVIEW, TRANSFER_RATE_REVIEW
                    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution__order=locked_order).first()
                    if ((locked_order.game_commission_snapshot and not game_transfer_enabled) or not coverage or coverage.attribution.status != 'ACTIVE'
                            or (coverage.status != 'ACTIVE' and not (coverage.status == 'REVIEW'
                                and coverage.review_reason in (TRANSFER_REVIEW, TRANSFER_RATE_REVIEW)))):
                        raise IllegalTransitionError('游戏订单的分段资金记录不完整，请先核对')
                    game_catalog = ServiceItem.objects.select_for_update().get(pk=locked_order.service_id)
                    locked_order._state.fields_cache['service'] = game_catalog
                lock_provider_dispatch(cmd['new_provider_account'].pk for cmd in parsed)
                if not locked_order.providers.exists() and locked_order.provider_id:
                    OrderProvider.objects.create(
                        order=locked_order, provider_id=locked_order.provider_id,
                        provider_account_id=locked_order.provider_account_id,
                        provider_name_snapshot=locked_order.provider_name_snapshot,
                        settlement_base=locked_order.amount,
                        provider_income=locked_order.provider_income,
                        commission_rate=locked_order.commission_rate,
                        commission_type=locked_order.commission_type,
                        commission_fixed=locked_order.commission_fixed if locked_order.commission_type == 'FIXED' else 0,
                    )
                rows = {
                    row.provider_account_id: row
                    for row in locked_order.providers.select_for_update().all()
                    if row.provider_account_id
                }
                if lifecycle_cycles is not None and any(
                        row.provider_account_id in lifecycle_cycles
                        and row.provider_business_cycle != lifecycle_cycles[row.provider_account_id]
                        for row in rows.values()):
                    raise IllegalTransitionError('转单陪玩原席位来源周期待核对')

                from orders.team_rules import required_team_size
                if required_team_size(locked_order) == 2:
                    active_ids = {row.provider_account_id for row in rows.values() if not row.released_at}
                    from orders.ordinary_zero_post_addon import terminated_before_transfer
                    closed_account = terminated_before_transfer(locked_order)
                    if closed_account is None and not marked_execution_id(locked_order) and in_flight_refund:
                        from orders.ordinary_positive_pretransfer import verify as verify_positive_pretransfer
                        earlier_transfer = OrderOriginalShareTransfer.objects.filter(
                            previous_segment__attribution__order=locked_order).order_by('created_at', 'pk').first()
                        try:
                            prior_execution = verify_positive_pretransfer(locked_order,
                                before=earlier_transfer.created_at if earlier_transfer else timezone.now())
                            if len(prior_execution.proposal.affected_accounts) == 1:
                                closed_account = prior_execution.proposal.affected_accounts[0]
                        except DjangoValidationError:
                            # Other supported transferred-order agreements use
                            # their existing team-size rules.
                            pass
                    if closed_account is not None and (len(active_ids) != 1
                            or any(cmd['old_provider_account_id'] not in active_ids for cmd in parsed)):
                        raise IllegalTransitionError('请从当前仍在履约的席位安排转单')
                    for cmd in parsed:
                        active_ids.discard(cmd['old_provider_account_id'])
                        active_ids.add(cmd['new_provider_account'].pk)
                    if len(active_ids) != (1 if closed_account is not None else 2):
                        raise IllegalTransitionError('双陪转单后须保留两名不同的在单陪玩，不能合并为一人')

                # Cash-deposit refunds lock profile -> wallet. Lock every old
                # and new profile up front in one deterministic order; taking
                # an old wallet first would invert that order even at zero fine.
                profile_user_ids = {row.provider_id for row in rows.values() if row.provider_id}
                profile_user_ids.update(cmd['new_provider'].pk for cmd in parsed)
                list(EscortProfile.objects.select_for_update().filter(
                    user_id__in=profile_user_ids,
                ).order_by('user_id'))

                # A converted reservation still owns its original time slot.
                # Check the incoming provider while holding the same profile
                # lock used by reservation creation, then transfer the active
                # assignment in this transaction. The old slot is released by
                # the assignment change only after this check succeeds.
                from orders.models import Reservation
                booking = Reservation.objects.filter(order_id=locked_order.pk,
                    status=Reservation.Status.CONVERTED).first()
                if booking:
                    from orders.reservation_services import find_conflicting_reservations
                    for cmd in parsed:
                        if find_conflicting_reservations(cmd['new_provider_account'], booking.start_time,
                                booking.end_time, exclude_id=booking.pk).select_for_update().exists():
                            raise IllegalTransitionError('转入陪玩在原预约时段已有其他预约')

                # ---- 产能闸门：转入方还接不接得下 ----
                #
                # 必须在锁内做：入参解析段（上面那个 for item in transfers）跑在
                # 事务外，那里查完到这里落库之间是敞开的。
                #
                # 这里用「已校验过的 account 集合」而不是 {account_id: 新增数}
                # 计数器，因为 transfer 是 detail action，整批指令作用于**同一笔**
                # locked_order —— 一个人在本批次里最多新增 1 单，不存在累加。
                # 同一个新打手被重复指定也只占一个名额；双陪已在上方校验
                # 最终人数，历史单陪仍保留合并份额的兼容行为。
                #
                # 转出方释放自己的名额，不能抵扣另一名转入方的占用。
                capacity_checked = set()
                for cmd in parsed:
                    new_account_id = cmd['new_provider_account'].pk
                    if new_account_id in capacity_checked:
                        continue
                    capacity_checked.add(new_account_id)
                    new_profile = cmd['new_profile']
                    new_provider = cmd['new_provider']
                    new_profile.refresh_from_db()
                    from orders.paid_service_scope import acceptance_error
                    reject = acceptance_error(new_profile, locked_order, allow_refund_recovery=recovery_transfer_enabled,
                                              refund_recovery_order_id=locked_order.pk if recovery_transfer_enabled else None)
                    if reject:
                        raise IllegalTransitionError(reject)
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

                if game_catalog is not None:
                    if locked_order.game_commission_snapshot:
                        from orders.game_commission import lock_transfer_snapshot
                        for cmd in parsed:
                            cmd['_segment_game_plan'] = lock_transfer_snapshot(
                                locked_order, game_catalog, provider=cmd['new_profile'])
                    else:
                        from orders.segment_transfer_commission import lock_standard_transfer_plans
                        plans = lock_standard_transfer_plans(locked_order, game_catalog,
                            [cmd['new_profile'] for cmd in parsed])
                        for cmd in parsed:
                            cmd['_segment_commission_plan'] = plans[cmd['new_provider_account'].pk]
                from orders.transfer_limit import record_locked
                record_locked(locked_order, [(cmd['old_provider_account_id'], cmd['new_provider_account'].pk)
                                             for cmd in parsed])
                last_new_provider = None
                last_new_provider_account = None
                transfer_at = timezone.now()
                for cmd in parsed:
                    old_row = rows.get(cmd['old_provider_account_id'])
                    if old_row is None or old_row.settled_at or old_row.released_at:
                        raise IllegalTransitionError('所选原打手不在本单未结算打手中')
                    funded_share = None
                    funded_old = funded_new = 0
                    if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
                        from orders.models import OrderPlatformSubsidyShare
                        funded_share = OrderPlatformSubsidyShare.objects.select_for_update().filter(assignment=old_row).first()
                        if funded_share:
                            if funded_share.paid_at:
                                raise IllegalTransitionError('原陪玩的平台补贴已结算，请核对后处理')
                            current_pool = funded_share.amount - funded_share.retained_amount
                            if current_pool < 0:
                                raise IllegalTransitionError('历史补贴份额待核对')
                            kept = (min(cmd['split_value'], current_pool) if cmd['split_type'] == OrderProvider.CommissionType.FIXED
                                    else current_pool * cmd['split_value'] // 100)
                            funded_old = funded_share.retained_amount + kept
                            funded_new = current_pool - kept
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
                    if old_income and locked_order.settlement_mode == Order.SettlementMode.COMPLETION and not completion_transfer_test:
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
                    if locked_order.commission_type == 'FIXED':
                        old_row.commission_type = OrderProvider.CommissionType.FIXED
                        old_row.commission_rate = 0
                        old_row.commission_fixed = old_base_kept - old_income
                    old_row.released_at = transfer_at
                    if (locked_order.settlement_mode == Order.SettlementMode.COMPLETION
                            and not completion_transfer_test and not (funded_share and funded_old)):
                        old_row.settled_at = timezone.now()
                    old_row.save(update_fields=[
                        'provider_income', 'settlement_base', 'settled_at', 'released_at', 'commission_type', 'commission_rate', 'commission_fixed',
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
                            'provider_business_cycle': incoming_cycles[cmd['new_provider_account'].pk],
                            'provider_name_snapshot': new_name,
                            'settlement_base': transferred_base,
                            'commission_type': OrderProvider.CommissionType.FIXED if locked_order.commission_type == 'FIXED' else OrderProvider.CommissionType.PERCENT,
                            'commission_rate': 0 if locked_order.commission_type == 'FIXED' else locked_order.commission_rate,
                            'commission_fixed': transferred_base - new_income if locked_order.commission_type == 'FIXED' else 0,
                            'provider_income': new_income,
                        },
                    )
                    if not created:
                        if funded_share and OrderPlatformSubsidyShare.objects.filter(assignment=new_row, paid_at__isnull=False).exists():
                            raise IllegalTransitionError('该陪玩已收到本单平台补贴，不能重新转入')
                        if new_row.settled_at and new_row.provider_income > 0:
                            raise IllegalTransitionError('该打手已收到本单转单结算收益，不能重新转入同一历史订单')
                        if new_row.released_at and locked_order.escort_mode == Order.EscortMode.DOUBLE:
                            # Keep the old report image as evidence, but require
                            # a fresh completion action for this assignment.
                            new_row.team_completion_pending = True
                        new_row.provider_income += new_income
                        new_row.settlement_base += transferred_base
                        if locked_order.commission_type == 'FIXED':
                            new_row.commission_type = OrderProvider.CommissionType.FIXED
                            new_row.commission_rate = 0
                            new_row.commission_fixed = new_row.settlement_base - new_row.provider_income
                        new_row.settled_at = None
                        new_row.released_at = None
                        new_row.provider_business_cycle = incoming_cycles[cmd['new_provider_account'].pk]
                        new_row.save(update_fields=[
                            'provider_income', 'settlement_base', 'settled_at', 'released_at', 'commission_type', 'commission_rate', 'commission_fixed', 'team_completion_pending', 'provider_business_cycle',
                        ])
                    if funded_share:
                        funded_share.amount = funded_old
                        funded_share.save(update_fields=['amount'])
                        if (locked_order.settlement_mode == Order.SettlementMode.COMPLETION and funded_old
                                and not locked_order.aftersales.filter(status='OPEN').exists()):
                            from orders.subsidies import settle_platform_subsidy_share
                            if settle_platform_subsidy_share(funded_share.pk, transferring_order=locked_order):
                                old_row.settled_at = timezone.now()
                                old_row.save(update_fields=['settled_at'])
                        replacement, was_created = OrderPlatformSubsidyShare.objects.get_or_create(
                            subsidy=funded_share.subsidy, assignment=new_row, defaults={'amount': funded_new})
                        if not was_created:
                            replacement.retained_amount = replacement.amount
                            replacement.amount += funded_new
                            replacement.save(update_fields=['amount', 'retained_amount'])
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

                # Existing evidence drafts were created at entry with the old
                # share. Keep those drafts aligned with the transferred shares;
                # submitted/audited reports remain historical records.
                changed_accounts = {cmd['old_provider_account_id'] for cmd in parsed}
                changed_accounts.update(cmd['new_provider_account'].pk for cmd in parsed)
                changed_shares = {row.provider_account_id: row for row in locked_order.providers.filter(
                    provider_account_id__in=changed_accounts)}
                for draft in ProviderReport.objects.select_for_update().filter(
                        order=locked_order, provider_account_id__in=changed_accounts,
                        status=ProviderReport.Status.DRAFT):
                    share = changed_shares.get(draft.provider_account_id)
                    if share and (draft.amount != share.settlement_base or draft.commission_rate != share.commission_rate):
                        draft.amount = share.settlement_base
                        draft.commission_rate = share.commission_rate
                        draft.save(update_fields=['amount', 'commission_rate', 'updated_at'])
                        transaction.on_commit(lambda draft=draft: notify_report_update(draft))

                from orders.work_stints import transferred as record_work_transfer
                try:
                    record_work_transfer(locked_order, parsed, at=transfer_at, operator=request.account)
                except ValueError as exc:
                    raise IllegalTransitionError(str(exc)) from exc
                from orders.original_attribution import service_transferred
                service_transferred(locked_order, parsed, operator=request.account, at=transfer_at)
                zero_followup_transfer = False
                if not needs_integrated_processing(locked_order):
                    from orders.negotiation_zero import marked_execution_id
                    from orders.models import OrdinaryTransferFollowupRound, OrdinaryPostRefundTransferRevision
                    zero_id = marked_execution_id(locked_order)
                    if (zero_id and OrdinaryPostRefundTransferRevision.objects.filter(order=locked_order,
                            snapshot__zero_pretransfer_execution_id=zero_id).exists()
                            and OrdinaryTransferFollowupRound.objects.filter(order=locked_order,
                                execution__termination_processed_at__isnull=False).exists()):
                        from orders.models import OrderOriginalShareTransfer
                        from orders.ordinary_post_refund_transfer import record_locked
                        from orders.ordinary_post_refund_release import record_locked as release_transfer_funding
                        events = list(OrderOriginalShareTransfer.objects.select_for_update().filter(
                            previous_segment__attribution__order=locked_order, created_at=transfer_at).order_by('pk'))
                        if len(events) != len(parsed):
                            raise IllegalTransitionError('追加退款后转单的履约事实缺失')
                        try:
                            for event in events:
                                revision = record_locked(locked_order, event)
                                release_transfer_funding(revision)
                        except (rest_serializers.ValidationError, ValueError) as exc:
                            raise IllegalTransitionError('追加退款后转单资金版本待核对') from exc
                        zero_followup_transfer = True
                if (not zero_followup_transfer and not needs_integrated_processing(locked_order) and not locked_order.refund_records.exists()
                        and not OrderTransferNegotiationEvidence.objects.filter(order=locked_order).exists()):
                    from orders.models import OrderAddonPayment, OrderPointsFundingAllocation, OrderOriginalShareTransfer
                    from orders.negotiation_zero import marked_execution_id
                    zero_before_transfer = bool(marked_execution_id(locked_order))
                    if (zero_before_transfer or (OrderAddonPayment.objects.filter(intent__order=locked_order).exists()
                            and OrderPointsFundingAllocation.objects.filter(service_order=locked_order).exists())):
                        from orders.ordinary_post_refund_transfer import record_pre_main_locked
                        from orders.ordinary_post_refund_release import record_locked as release_pre_main_transfer
                        events = list(OrderOriginalShareTransfer.objects.select_for_update().filter(
                            previous_segment__attribution__order=locked_order, created_at=transfer_at).order_by('pk'))
                        if len(events) != len(parsed):
                            raise IllegalTransitionError('主单退款前转单的履约事实缺失')
                        try:
                            for event in events:
                                revision = record_pre_main_locked(locked_order, event)
                                release_pre_main_transfer(revision)
                        except (rest_serializers.ValidationError, ValueError) as exc:
                            raise IllegalTransitionError('主单退款前转单资金版本待核对') from exc
                if not needs_integrated_processing(locked_order) and (positive_before_first_hint or
                        OrderTransferNegotiationEvidence.objects.filter(order=locked_order,
                            execution__termination_processed_at__isnull=False).exists() or
                        OrdinaryPostRefundTransferRevision.objects.filter(order=locked_order,
                            snapshot__positive_pretransfer_execution_id__isnull=False).exists()):
                    from orders.ordinary_transfer_capability import permits as ordinary_transfer_permits
                    if ordinary_transfer_permits(locked_order):
                        from orders.models import OrderOriginalShareTransfer
                        from orders.ordinary_post_refund_transfer import record_locked
                        events = list(OrderOriginalShareTransfer.objects.select_for_update().filter(
                            previous_segment__attribution__order=locked_order, created_at=transfer_at).order_by('pk'))
                        if len(events) != len(parsed):
                            raise IllegalTransitionError('退款后转单的履约事实缺失')
                        try:
                            for event in events:
                                revision = record_locked(locked_order, event)
                                from orders.ordinary_post_refund_release import record_locked as release_post_refund
                                release_post_refund(revision)
                        except (rest_serializers.ValidationError, ValueError) as exc:
                            raise IllegalTransitionError('退款后转单资金版本待核对') from exc
                if subsidy_segment:
                    from orders.subsidy_transfer_capture import sync_transfer_shares
                    try:
                        sync_transfer_shares(locked_order)
                    except SettlementError as exc:
                        raise IllegalTransitionError('安全箱逐段补贴待核对') from exc
                if needs_integrated_processing(locked_order) and funding_revision_active(locked_order):
                    try:
                        if locked_order.refund_records.exists():
                            from orders.models import OrderOriginalShareTransfer
                            from orders.points_transfer_refund_access import verification_scope
                            from orders.points_post_refund_transfer import record_locked as record_points_refunded_transfer
                            events = list(OrderOriginalShareTransfer.objects.select_for_update().filter(
                                previous_segment__attribution__order=locked_order, created_at=transfer_at).order_by('pk'))
                            if len(events) != 1 or len(parsed) != 1:
                                raise IllegalTransitionError('积分退款后转单须逐席办理')
                            with verification_scope(locked_order):
                                if locked_order.refund_records.count() >= 3:
                                    if third_transfer_capture is None:
                                        raise IllegalTransitionError('第三轮转单缺少原资金预核')
                                    from orders.points_third_transfer import record_locked as record_third_transfer
                                    record_third_transfer(locked_order, events[0], third_transfer_capture)
                                else:
                                    record_points_refunded_transfer(locked_order, events[0], allow_pending=True)
                        else:
                            from orders.points_transfer_revision import record_latest_revision_locked
                            record_latest_revision_locked(locked_order)
                    except (rest_serializers.ValidationError, ValueError, DatabaseError) as exc:
                        raise IllegalTransitionError('积分转单补资调整凭证待核对') from exc
                if recovery_transfer_enabled:
                    from wallet.refund_recovery_order import reserve_many
                    try:
                        reserve_many([cmd['new_profile'] for cmd in parsed], locked_order, allow_in_service=True)
                    except ValueError as exc:
                        raise IllegalTransitionError(str(exc)) from exc
                from orders.room_completion_review import invalidate_for_transfer_locked
                invalidate_for_transfer_locked(locked_order, request.account)
                log_only(
                    locked_order,
                    action=OrderStatusLog.Action.TRANSFER,
                    operator=request.legacy_user,
                    operator_account=request.account,
                    reason=reason,
                )
                order = locked_order
                transaction.on_commit(lambda: notify_order_update(order))
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '转单失败'})

        # A teammate who remains on a two-person service must also learn about
        # the handoff, including when they were offline during the update.
        if order.escort_mode == Order.EscortMode.DOUBLE:
            for row in rows.values():
                if row.provider_account_id in changed_accounts or row.released_at:
                    continue
                _push_message_safe(
                    recipient_id=row.provider_id,
                    recipient_account_id=row.provider_account_id,
                    title='服务搭档已更换',
                    preview=f'「{order.service_name_snapshot}」的服务搭档已更换，请查看订单并确认服务安排',
                    msg_type='ORDER', related_order_id=order.id,
                )
        for cmd in parsed:
            _push_message_safe(
                recipient_id=rows[cmd['old_provider_account_id']].provider_id,
                recipient_account_id=cmd['old_provider_account_id'],
                title='订单已转出',
                preview=(
                    f'你的「{order.service_name_snapshot}」已转交其他大神，'
                    + ('请按履约情况提交报单，收益将在整单完成后核算'
                       if subsidy_segment else '本单无保留份额，无需提交报单'
                       if rows[cmd['old_provider_account_id']].settlement_base <= 0 else
                       '保留收益待整单报单通过后结算'
                       if order.settlement_mode == Order.SettlementMode.REPORT_APPROVAL
                       else '本次转单份额已结算')
                ),
                msg_type='ORDER',
                related_order_id=order.id,
            )
            _push_message_safe(
                recipient_id=cmd['new_provider'].id,
                recipient_account_id=cmd['new_provider_account'].pk,
                title='客服转单给你',
                preview=f'客服为你转入「{order.service_name_snapshot}」，请接手并继续服务，完成后提交结单截图',
                msg_type='ORDER',
                assignment_alert=True,
                related_order_id=order.id,
            )
        _push_message_safe(
            recipient_id=order.customer_id,
            recipient_account_id=order.customer_account_id,
            title='已为你更换大神',
            preview=f'你的「{order.service_name_snapshot}」已更换服务大神',
            msg_type='ORDER',
            related_order_id=order.id,
        )
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '转单成功'})

    @action(detail=False, methods=['get'], url_path='suggest-commission')
    def suggest_commission(self, request):
        """派单时按 service_id + provider_id 解析适用抽成率(%)。

        返回 source（来源层级）与 source_label（可读来源），便于客服知晓抽成依据。
        """
        from orders.pricing import (
            resolve_active_promotion,
            resolve_commission_rate_with_source,
            promotion_available_for_new_order,
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
        promotion = resolve_active_promotion(service) if promotion_available_for_new_order() else None
        from orders.commission import quote, override_from, integer
        from orders.pricing import PricingError
        try:
            amount = integer(request.query_params.get('amount',service.price * service.billing_provider_count),'订单金额')
            data = quote(service,amount,profile=escort_profile,promotion=promotion,override=override_from(request.query_params))
        except PricingError as exc:
            return Response({'code':400,'msg':str(exc)},status=400)
        rate, source = data['commission_rate'],data['commission_source']
        label_map = {
            'promotion': f'活动定价（{promotion.title}）' if promotion else '活动定价',
            'level': f'等级抽成（{escort_profile.level.name}）'
                     if escort_profile and getattr(escort_profile, 'level', None) else '等级抽成',
            'service': '商品抽成',
            'global': '全局默认',
            'club_companion': '陪玩单统一20%',
            'club_experience': '体验单：低于100元整单抽3元，100元及以上抽10%',
        }
        if source in label_map:
            data['source_label'] = data['commission_source_label'] = label_map[source]
        return Response({'code': 0, 'data': data})

    @action(detail=False, methods=['post'], url_path='dispatch')
    @recover_fund_result('admin.dispatch')
    def quick_dispatch(self, request):
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            customer = _business_account(request.data.get('customer_id'), ClubAccount.AccountType.BOSS)
            if not customer:
                return Response({'code': 404, 'msg': '老板不存在'})
            mapping = LegacyAccountMap.objects.filter(account=customer).first()
            legacy = User.objects.filter(pk=mapping.legacy_user_id).first() if mapping else None
            from club_accounts.provider_lifecycle import ProviderLifecycleError
            from orders.referral_cycle import capture, lock
            try:
                snapshot = capture(customer, legacy, [])
                with transaction.atomic():
                    lock(snapshot)
                    request._dispatch_referral_lifecycle = (customer.pk, snapshot)
                    return self._quick_dispatch_entry(request)
            except (ProviderLifecycleError, ValueError) as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
        return self._quick_dispatch_entry(request)

    def _quick_dispatch_entry(self, request):
        if (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                and request.data.get('points_checkout_test_token')):
            customer = _business_account(request.data.get('customer_id'), ClubAccount.AccountType.BOSS)
            service = ServiceItem.objects.filter(pk=request.data.get('service_id'), is_active=True).first()
            if not customer or not service or service.fulfillment_provider_count not in (1, 2):
                raise rest_serializers.ValidationError('老板或服务不存在')
            from orders.points_business_access import checkout
            with transaction.atomic(), checkout(account=customer, service=service):
                return self._quick_dispatch(request)
        return self._quick_dispatch(request)

    def _quick_dispatch(self, request):
        """客服代派单：替指定老板下单（扣其钱包余额），可选直接指派陪玩。

        资金口径复用 C 端下单流程：立即扣减目标老板余额，生成 PAID 订单并落拆账字段；
        结算仍在订单完成/报单审核时进行。指派陪玩则订单直接转 GRABBED 并置陪玩 BUSY，
        留空则保持 PENDING 并投递超时自动取消任务。
        """
        replayed, replay_reason = peek_fund_write(
            account=request.account, request_id=request.data.get('client_request_id'),
        )
        if replayed:
            return Response({'code': 409, 'msg': replay_reason})
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
        if not 1 <= game_rounds <= 999:
            return Response({'code': 400, 'msg': '购买数量必须为 1 至 999 的整数'})
        remark = (request.data.get('remark') or '').strip()[:255]
        from orders.account_fields import FIELDS as ORDER_ACCOUNT_FIELDS
        game_fields = {field: rest_serializers.CharField(max_length=50, allow_blank=True).run_validation(
            request.data.get(field, '')) for field in ORDER_ACCOUNT_FIELDS}

        try:
            customer_account = _business_account(
                customer_id,
                ClubAccount.AccountType.BOSS,
            )
            if customer_account is None:
                raise ClubAccount.DoesNotExist
            mapping = LegacyAccountMap.objects.filter(account=customer_account).first()
            customer = User.objects.filter(pk=mapping.legacy_user_id).first() if mapping else None
            if mapping and customer is None:
                raise User.DoesNotExist
        except (ClubAccount.DoesNotExist, User.DoesNotExist):
            return Response({'code': 404, 'msg': '老板不存在'})
        try:
            service = ServiceItem.objects.get(id=service_id, is_active=True)
        except ServiceItem.DoesNotExist:
            return Response({'code': 404, 'msg': '服务不存在或已下架'})

        if service.service_category and service.service_category.is_gift:
            return Response({'code': 400, 'msg': '礼物由老板在赠礼入口确认金额后赠送，不进入派单流程'})

        provider_gender = request.data.get('provider_gender', 'ANY')
        if provider_gender not in Order.ProviderGender.values:
            return Response({'code': 400, 'msg': '陪玩性别参数无效'})
        if provider_gender != 'ANY' and service.service_type != 'COMPANION':
            return Response({'code': 400, 'msg': '仅陪玩服务可选择男陪或女陪'})
        from orders.gameplay import validate_service_gameplay
        validate_service_gameplay(service, game_rounds, companion_assignment=request.data.get('escort_mode') == 'DOUBLE')

        # ---- 打手入参：escort_mode + providers[]（兼容旧的单 provider_id）----
        escort_mode = (request.data.get('escort_mode') or Order.EscortMode.SINGLE).upper()
        if escort_mode not in Order.EscortMode.values:
            return Response({'code': 400, 'msg': '陪玩模式无效'})
        if service.fulfillment_provider_count == 2 and escort_mode != Order.EscortMode.DOUBLE:
            return Response({'code': 400, 'msg': '该套餐须由两名陪玩履约，请选择双陪'})
        if service.fulfillment_provider_count != 2 and escort_mode == Order.EscortMode.DOUBLE:
            return Response({'code': 400, 'msg': '单陪套餐不能按双陪派单，请选择明确的双陪套餐'})
        providers_input = request.data.get('providers')
        if not providers_input:
            # 兼容旧参数：单个 provider_id
            legacy_id = request.data.get('provider_id')
            providers_input = [{'provider_id': legacy_id}] if legacy_id else []
        if not isinstance(providers_input, list) or any(not isinstance(p, dict) for p in providers_input):
            return Response({'code': 400, 'msg': 'providers 格式错误'})
        from orders.commission import override_from
        from orders.pricing import PricingError
        try:
            override_from(request.data)
            for item in providers_input:
                if any(item.get(key) not in (None, '') for key in
                       ('commission_type', 'commission_rate', 'commission_fixed', 'commission_fixed_total')):
                    raise PricingError('不允许修改单笔订单抽成，请使用门店适用规则')
        except PricingError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        # 去除空项
        providers_input = [p for p in providers_input if p and p.get('provider_id')]
        if providers_input or request.data.get('provider_id'):
            return Response({'code': 400, 'msg': '客服代下新单统一进入抢单池，请勿指定陪玩'}, status=400)
        from orders.grab_single_flow import enabled as single_grab_enabled
        from orders.grab_team_flow import enabled as team_grab_enabled
        window_available = team_grab_enabled() if escort_mode == Order.EscortMode.DOUBLE else single_grab_enabled()
        if not window_available:
            return Response({'code': 503, 'errorCode': 'GRAB_WINDOW_UNAVAILABLE',
                             'msg': '抢单暂不可用，请稍后代下单'}, status=503)
        expected = 2 if escort_mode == Order.EscortMode.DOUBLE else 1
        if providers_input and len(providers_input) != expected:
            return Response({
                'code': 400,
                'msg': f'{"双陪需指定 2 名" if expected == 2 else "单陪最多 1 名"}打手',
            })
        if escort_mode == Order.EscortMode.DOUBLE and len(providers_input) != 2 and not team_grab_enabled():
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
            profile = getattr(provider_account, 'escort_profile', None)
            if profile is None:
                return Response({'code': 400, 'msg': '陪玩资料未建立'})
            if profile.status == EscortProfile.Status.OFFLINE:
                return Response({'code': 400, 'msg': '该陪玩当前不可接单'})
            reject = profile.order_acceptance_error(service, provider_gender=provider_gender)
            if reject:
                return Response({'code': 400, 'msg': reject})
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

        from dataclasses import asdict
        from orders.purchase_benefits import is_experience, prepare_purchase, lock_purchase, record_paid_benefits
        from orders.pricing import resolve_commission_rate
        coupon_id = rest_serializers.IntegerField(min_value=1, allow_null=True).run_validation(request.data.get('user_coupon_id'))
        discount_token = request.data.get('discount_selection_token')
        discount_enabled = getattr(settings, 'DISCOUNT_COMBINATIONS_PAYMENT_ENABLED', False) and not is_experience(service)
        if discount_enabled:
            if coupon_id or not discount_token:
                raise rest_serializers.ValidationError('请先读取并确认优惠组合')
            from orders.discount_selection import preview_selected_discount
            from orders.pricing import DiscountResult
            selected, promotion, user_coupon = preview_selected_discount(customer_account, service, game_rounds, discount_token)
            pricing = DiscountResult(selected.original_amount, selected.boss_discount, selected.promo_discount,
                                     selected.coupon_discount, selected.amount)
        else:
            if discount_token:
                raise rest_serializers.ValidationError('优惠组合付款暂未开放')
            pricing, promotion, user_coupon = prepare_purchase(customer_account, service, game_rounds, coupon_id)
        amount = pricing.amount
        from engagement.funding_wallet import parse_funding_selection
        gift_balance_amount, compensation_balance_amount = parse_funding_selection(request.data)
        points_token = request.data.get('points_checkout_test_token')
        points_quote = None
        if points_token:
            from orders.points_checkout_test import verify_checkout_quote, assignment_enabled
            from orders.grab_single_flow import enabled as single_grab_enabled
            if not assignment_enabled() or (escort_mode == Order.EscortMode.SINGLE and not single_grab_enabled()):
                raise rest_serializers.ValidationError('积分抢单链路尚未开放')
            quote_data = {**asdict(pricing), 'service': service, 'promotion': promotion,
                          'game_rounds': game_rounds, 'gift_balance_amount': gift_balance_amount,
                          'compensation_balance_amount': compensation_balance_amount,
                          'user_coupon': user_coupon, 'discount_selection_token': discount_token}
            if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
                from orders.points_staff_checkout import pending_quote_data
                quote_data = pending_quote_data(quote_data)
            points_quote, points_receipt_token = verify_checkout_quote(customer_account, quote_data, points_token)
            amount = points_quote['calculation']['payable_units']
        boss_confirmation_method = request.data.get('boss_confirmation_method', '')
        confirmation_required = bool(points_quote or getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False)
                                     or discount_enabled)
        if confirmation_required:
            if not isinstance(boss_confirmation_method, str) or not 2 <= len(boss_confirmation_method.strip()) <= 50:
                raise rest_serializers.ValidationError('请记录老板确认方式')
            boss_confirmation_method = boss_confirmation_method.strip()
        boss_confirmation = request.data.get('boss_confirmation', '')
        if confirmation_required:
            if not isinstance(boss_confirmation, str) or not 2 <= len(boss_confirmation.strip()) <= 500:
                return Response({'code': 400, 'msg': '请记录老板明确确认的付款选择'})
            boss_confirmation = boss_confirmation.strip()
        else:
            boss_confirmation = ''

        # 管理端与老板端一样，确认价过期时先阻断扣款，再由用户确认新报价。
        if 'expected_amount' in request.data:
            expected_amount = rest_serializers.IntegerField(min_value=0).run_validation(request.data['expected_amount'])
            if expected_amount != amount:
                return Response({
                    'code': 409, 'msg': '价格或优惠已变化，请确认最新报价后重新派单',
                    'errorCode': 'PRICE_CHANGED',
                })

        # 旧的结算基数仍沿用现有计算，单笔抽成不接受客服覆盖。
        from orders.models import OrderProvider as _OP
        provider_specs = []
        for row_account, pu, item in provider_objs:
            escort_profile = row_account.escort_profile
            provider_specs.append({
                'commission_type': _OP.CommissionType.PERCENT,
                'commission_rate': resolve_commission_rate(service, escort_profile, promotion),
            })

        from orders.pricing import club_provider_specs, PricingError
        from orders.commission import resolve, override_from, fixed_specs, snapshot
        profiles = [account.escort_profile for account, _pu, _item in provider_objs]
        try:
            requested = override_from(request.data)
            commission_plan = resolve(service,amount,profile=profiles[0] if profiles else None,promotion=promotion,override=requested)
            if commission_plan.get('commission_allocation_pending'):
                selected_specs = []
            elif commission_plan['commission_type'] == 'FIXED':
                selected_specs = fixed_specs(amount,len(profiles),commission_plan['commission_fixed'])
            elif commission_plan['commission_override']:
                selected_specs = [{'commission_rate':commission_plan['commission_rate']} for _ in profiles]
            else:
                club_specs = club_provider_specs(service,profiles,amount)
                selected_specs = club_specs if club_specs is not None else provider_specs
        except PricingError as exc:
            return Response({'code':400,'msg':str(exc)})
        commission_pending = bool(commission_plan.get('commission_allocation_pending'))
        from orders.delta_personal_commission import mixed_funding_available, points_funding_available
        mixed_unavailable = bool((gift_balance_amount or compensation_balance_amount) and not mixed_funding_available())
        from orders.points_business_access import permits as points_permits
        formal_pending_points = bool(points_quote and commission_pending
            and points_permits(account=customer_account, service=service, phase='checkout', kind='service')
            and quote_data.get('commission_allocation_pending')
            and quote_data.get('commission_snapshot') == snapshot(commission_plan))
        points_unavailable = bool(points_quote and not (points_funding_available() or formal_pending_points))
        safe_box_subsidy_basis = 0
        if (commission_pending and not profiles and service.fulfillment_provider_count == 1
                and getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False) and not points_quote):
            from orders.purchase_benefits import vip_safe_box_subsidy_basis
            safe_box_subsidy_basis = vip_safe_box_subsidy_basis(
                customer_account, service, game_rounds, user_coupon, pricing)
        zero_pending = bool(commission_pending and amount == 0 and safe_box_subsidy_basis
                            and not profiles and service.fulfillment_provider_count == 1)
        if zero_pending:
            from orders.grab_clock_contract import production_enabled
            from orders.grab_single_flow import enabled as single_window_enabled
            from orders.segment_financial_capture import subsidy_capture_enabled
            from orders.paid_service_scope import enabled as paid_scope_enabled
            zero_pending = bool(production_enabled() and single_window_enabled() and subsidy_capture_enabled()
                                and paid_scope_enabled() and getattr(settings, 'ORIGINAL_ATTRIBUTION_ENABLED', False)
                                and getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False))
        if zero_pending and not confirmation_required:
            boss_confirmation_method = request.data.get('boss_confirmation_method', '')
            boss_confirmation = request.data.get('boss_confirmation', '')
            if (not isinstance(boss_confirmation_method, str) or not 2 <= len(boss_confirmation_method.strip()) <= 50
                    or not isinstance(boss_confirmation, str) or not 2 <= len(boss_confirmation.strip()) <= 500):
                return Response({'code': 400, 'msg': '请记录老板明确确认的用券选择和确认方式'})
            boss_confirmation_method, boss_confirmation = boss_confirmation_method.strip(), boss_confirmation.strip()
        if commission_pending and (profiles or (amount <= 0 and not zero_pending) or points_unavailable
                or mixed_unavailable):
            return Response({'code': 400, 'msg': '三角洲待分配抽成暂仅支持未指定陪玩的普通店币付款'})
        personal_game_snapshots = None
        if not commission_pending and commission_plan.get('game_commission_snapshot', {}).get('personal_rules'):
            if not profiles or amount <= 0 or points_unavailable or mixed_unavailable:
                return Response({'code': 400, 'msg': '三角洲本人抽成当前仅支持指定陪玩的普通店币付款'})
            try:
                personal_game_snapshots = [resolve(service, amount, profile=person, promotion=promotion)
                    ['game_commission_snapshot'] for person in profiles]
            except PricingError as exc:
                return Response({'code': 400, 'msg': str(exc)})
        shares = build_provider_shares(amount, selected_specs)
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
        from orders.inviter import buyer_inviter
        inviter, inviter_account, inviter_rate = buyer_inviter(customer_account, customer)
        if commission_pending:
            from types import SimpleNamespace
            split = SimpleNamespace(commission_rate=None, provider_income=None,
                inviter_commission=amount * inviter_rate // 100, shop_income=None)
        elif shares:
            try:
                split = aggregate_order_split(amount, shares, inviter_rate)
            except SettlementError as exc:
                return Response({'code': 400, 'msg': str(exc)})
        else:
            split = compute_split(amount, inviter_commission_rate=inviter_rate,
                commission_type=commission_plan['commission_type'],commission_fixed=commission_plan['commission_fixed'],commission_rate=commission_plan['commission_rate'])
        from orders.paid_service_scope import capture as capture_paid_scope, verify as verify_paid_scope, record as record_paid_scope
        paid_scope = capture_paid_scope(service)
        ensure_provider_dispatch_locks(row_account.pk for row_account, _pu, _item in provider_objs)
        # ---- 扣款建单 ----
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            referral_lifecycle = getattr(request, '_dispatch_referral_lifecycle', None)
            locked_service = ServiceItem.objects.select_for_update().get(pk=service.pk)
            if (locked_service.game_category_id != service.game_category_id
                    or locked_service.fulfillment_provider_count != service.fulfillment_provider_count
                    or locked_service.billing_provider_count != service.billing_provider_count):
                return Response({'code': 409, 'msg': '服务资料规则已变化，请重新核对'}, status=409)
            from orders.account_fields import lock_and_validate
            try:
                lock_and_validate(locked_service, game_fields)
            except rest_serializers.ValidationError as exc:
                return Response({'code': 400, 'msg': str(exc.detail[0])}, status=400)
            if getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False) or discount_token or paid_scope is not None:
                ServiceItem.objects.select_for_update().get(pk=service.pk)
            # 所有目标打手先按固定顺序取名额锁；拒绝发生在扣款之前。
            lock_provider_dispatch(row_account.pk for row_account, _pu, _item in provider_objs)
            if formal_pending_points:
                from orders.delta_personal_commission import verify_formal_points_checkout
                verify_formal_points_checkout(quote_data, account=customer_account, service=locked_service)
            locked_personal_profiles = {}
            if personal_game_snapshots is not None:
                from orders.delta_personal_commission import lock_providers
                try:
                    locked_personal_profiles = {p.account_id: p for p in lock_providers(
                        [account.pk for account, _pu, _item in provider_objs])}
                except ValueError as exc:
                    return Response({'code': 409, 'msg': str(exc)}, status=409)
            for row_account, _pu, _item in provider_objs:
                profile = EscortProfile.objects.select_related('account', 'user', 'level').get(account=row_account)
                reject = profile.order_acceptance_error(service, provider_gender=provider_gender)
                if reject:
                    return Response({'code': 400, 'msg': reject})
                reject = _capacity_reject_reason(
                    row_account.pk,
                    display_name=row_account.escort_profile.display_name,
                )
                if reject:
                    return Response({'code': 400, 'msg': reject})

            if getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False):
                from orders.game_commission import lock_and_verify_payment_snapshot
                try:
                    if commission_pending:
                        from orders.delta_personal_commission import lock_and_verify_pending_snapshot
                        lock_and_verify_pending_snapshot(service.pk, commission_plan['game_commission_snapshot'])
                    elif personal_game_snapshots is not None:
                        for (account, _pu, _item), expected in zip(provider_objs, personal_game_snapshots):
                            lock_and_verify_payment_snapshot(service.pk, expected,
                                provider=locked_personal_profiles[account.pk])
                        from orders.delta_personal_commission import verify_payment_shares
                        verify_payment_shares([account for account, _pu, _item in provider_objs],
                                              personal_game_snapshots, selected_specs)
                    else:
                        lock_and_verify_payment_snapshot(service.pk, commission_plan.get('game_commission_snapshot', {}))
                        from orders.game_commission import verify_payment_shares
                        verify_payment_shares(commission_plan.get('game_commission_snapshot', {}), selected_specs)
                except ValueError as exc:
                    return Response({'code': 409, 'errorCode': 'COMMISSION_CHANGED', 'msg': str(exc)}, status=409)

            try:
                verify_paid_scope(service.pk, paid_scope)
            except ValueError as exc:
                return Response({'code': 409, 'errorCode': 'SERVICE_CHANGED', 'msg': str(exc)}, status=409)
            if referral_lifecycle is not None:
                buyer_id, referral_snapshot = referral_lifecycle
                if customer_account.pk != buyer_id:
                    return Response({'code': 409, 'msg': '老板账号已变化，请重新下单'}, status=409)
                from orders.referral_cycle import validate
                buyer_locked = ClubAccount.objects.select_for_update().get(pk=buyer_id)
                try:
                    validate(referral_snapshot, buyer_locked, customer, [])
                except ValueError as exc:
                    return Response({'code': 409, 'msg': str(exc)}, status=409)
            wallet = get_wallet(
                user=customer, account=customer_account, for_update=True,
            )
            if not wallet.is_active:
                return Response({'code': 403, 'msg': '该老板钱包不可用'})
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': f'该老板{current_currency_name()}不足，请先通过企微客服充值'})
            from engagement.funding_wallet import funding_quote
            funding = funding_quote(wallet, amount, gift_balance_amount, compensation_balance_amount)
            if not funding['balance_sufficient']:
                return Response({'code': 400, 'msg': '所选资金来源余额不足，请重新核对'})

            from orders.timeout import current_timeout_config
            timeout_minutes = current_timeout_config().minutes
            if discount_token:
                from orders.discount_selection import lock_selected_discount
                locked, locked_promotion, locked_coupon = lock_selected_discount(customer_account, service, game_rounds, discount_token)
                if ((locked.original_amount, locked.boss_discount, locked.promo_discount, locked.coupon_discount, locked.amount)
                        != (pricing.original_amount, pricing.boss_discount, pricing.promo_discount, pricing.coupon_discount, pricing.amount)
                        or getattr(locked_promotion, 'pk', None) != getattr(promotion, 'pk', None)):
                    raise rest_serializers.ValidationError('优惠条件已变化，请重新报价')
            else:
                locked_coupon = lock_purchase(customer_account, service, game_rounds, user_coupon, pricing,
                                              expected_promotion=promotion)
            if safe_box_subsidy_basis and vip_safe_box_subsidy_basis(
                    customer_account, service, game_rounds, locked_coupon, pricing) != safe_box_subsidy_basis:
                raise rest_serializers.ValidationError('安全箱优惠资格已变化，请重新报价')
            allowed, reason = claim_fund_write(
                account=request.account, request_id=request.data.get('client_request_id'),
                scope='admin.dispatch',
                fingerprint=(f'{customer_account.pk}:{service.pk}:{game_rounds}:{amount}:{picked_ids}:{provider_gender}'
                             f':support={request.data.get("support_contact_id") or ""}'
                             f':gift={gift_balance_amount}:compensation={compensation_balance_amount}:confirmation={boss_confirmation}'
                             f':method={boss_confirmation_method}:points={points_quote["calculation"]["points_hundredths"] if points_quote else 0}'
                             f':discount={selected.key if discount_enabled else "legacy"}'
                             f':game={json.dumps(game_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}'),
            )
            if not allowed:
                return Response({'code': 409, 'msg': reason})

            balance_before = wallet.balance

            from support.assignment import selected_card
            support_contact = selected_card(request.data.get('support_contact_id'), lock=True)
            order = Order.objects.create(
                customer=customer,
                customer_account=customer_account,
                support_contact=support_contact,
                responsible_staff_id=support_contact.staff_account_id if support_contact else None,
                provider=provider,
                provider_account=provider_account,
                service=service,
                amount=amount,
                game_rounds=game_rounds,
                **game_fields,
                remark=remark,
                status=Order.Status.PENDING,
                dispatch_route=(Order.DispatchRoute.STAFF if (getattr(settings, 'GRAB_POOL_ENTRY_SNAPSHOT_ENABLED', False)
                                or getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False))
                                else Order.DispatchRoute.PUBLIC),
                escort_mode=escort_mode,
                # Team fulfilment requires each assigned provider's evidence;
                # this existing flag distinguishes the current team workflow
                # from historical DOUBLE orders which used single-step actions.
                requires_boss_selection=(escort_mode == Order.EscortMode.DOUBLE
                    and (bool(provider_rows) or not team_grab_enabled())),
                provider_gender=provider_gender,
                payment_status=Order.PaymentStatus.PAID,
                pending_timeout_minutes=timeout_minutes,
                auto_cancel_at=pending_timeout_deadline(minutes=timeout_minutes),
                original_amount=pricing.original_amount,
                boss_discount=pricing.boss_discount,
                promo_discount=pricing.promo_discount,
                coupon_discount=pricing.coupon_discount,
                promotion=promotion,
                **snapshot(commission_plan),
                commission_allocation_pending=commission_pending,
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter=inviter,
                inviter_account=inviter_account,
                inviter_commission=split.inviter_commission,
                shop_income=split.shop_income,
            )
            if referral_lifecycle is not None:
                from orders.referral_cycle import record
                record(order, referral_lifecycle[1])
            from orders.grab_pool_snapshot import initialize_entry
            try:
                grab_pool = initialize_entry(order)
            except ValueError as exc:
                raise rest_serializers.ValidationError(str(exc)) from exc
            record_paid_scope(order, paid_scope)
            from orders.original_attribution import payment_created
            if not points_quote:
                payment_created(order)
            if safe_box_subsidy_basis:
                from orders.models import OrderPlatformSubsidy
                subsidy = OrderPlatformSubsidy.objects.create(order=order, coupon_id_snapshot=locked_coupon.pk,
                                                              basis=safe_box_subsidy_basis)
                from orders.segment_financial_capture import register_subsidy
                register_subsidy(order, subsidy)
            record_paid_benefits(order, locked_coupon)
            # 打手明细落库
            if boss_confirmation:
                order.funding_confirmation = {'actor_account_id': request.account.pk,
                    'confirmation': boss_confirmation, 'method': boss_confirmation_method,
                    'service_id': service.pk, 'game_rounds': game_rounds,
                    'original_amount': pricing.original_amount, 'deal_amount': pricing.amount,
                    'payable_amount': amount, 'coupon_id': getattr(locked_coupon, 'pk', None),
                    'discount_selection': selected.key if discount_enabled else None,
                    'gift_balance_amount': gift_balance_amount,
                    'compensation_balance_amount': compensation_balance_amount,
                    'points_hundredths': points_quote['calculation']['points_hundredths'] if points_quote else 0,
                    'recorded_at': timezone.now().isoformat()}
                order.save(update_fields=['funding_confirmation'])
            from engagement.service_funding_route import use_order_funding_allocation
            funding_allocated = use_order_funding_allocation(amount=amount,
                    funding_selection_enabled=funding['funding_selection_enabled'])
            if funding_allocated:
                from engagement.funding_wallet import allocate_locked_order_funding, snapshot_service_funding_terms
                allocate_locked_order_funding(order, wallet, gift=gift_balance_amount, compensation=compensation_balance_amount,
                                              points_checkout=bool(points_quote))
            else:
                from engagement.services import allocate_order_points
                allocate_order_points(order, wallet, balance_before)
            wallet.balance -= amount
            wallet.save(update_fields=['balance'])
            for index, row in enumerate(provider_rows):
                pu = row['provider']
                OrderProvider.objects.create(
                    order=order,
                    provider=pu,
                    provider_account=row['provider_account'],
                    provider_business_cycle=row['provider_account'].business_cycle,
                    provider_name_snapshot=row['provider_account'].escort_profile.display_name or pu.nickname or pu.username,
                    settlement_base=row['settlement_base'],
                    commission_type=row['commission_type'],
                    commission_rate=row['commission_rate'],
                    game_commission_snapshot=(personal_game_snapshots[index]
                        if personal_game_snapshots is not None else order.game_commission_snapshot),
                    commission_fixed=row['commission_fixed'],
                    provider_income=row['provider_income'],
                )
            payment = Transaction.objects.create(
                wallet=wallet,
                order=order,
                amount=-amount,
                tx_type=Transaction.TxType.PAY,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=f'客服代派单：{order.order_no}',
                operator=request.legacy_user,
                operator_account=request.account,
            )
            if funding_allocated:
                snapshot_service_funding_terms(order, paid_at=payment.created_at)
            if points_quote:
                from orders.points_service_payment import record_locked
                record_locked(order_id=order.pk, payment_id=payment.pk, token=points_receipt_token)
                order.refresh_from_db()
                payment_created(order)
            from orders.grab_opening_intent import capture_isolated_payment
            capture_isolated_payment(order, payment, grab_pool)
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
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
                    o.provider_name_snapshot = provider_account.escort_profile.display_name or provider.nickname or provider.username
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
                    transaction.set_rollback(True)
                    return Response({'code': 400, 'msg': str(exc) or '指派失败'})

                for row in provider_rows:
                    _push_message_safe(
                        recipient_id=row['provider'].id,
                        recipient_account_id=row['provider_account'].pk,
                        title='客服派单给你',
                        preview=f'客服为你派单「{order.service_name_snapshot}」，请尽快开始服务',
                        msg_type='ORDER',
                        assignment_alert=True,
                        related_order_id=order.id,
                    )
                participant_names = [row['provider_account'].escort_profile.display_name
                                     or row['provider'].nickname or row['provider'].username for row in provider_rows]
                from stores.display import provider_display_name
                participant_names = [provider_display_name(name) for name in participant_names]
                _push_message_safe(
                    recipient_id=order.customer_id,
                    recipient_account_id=order.customer_account_id,
                    title='已为你指派双陪' if len(participant_names) == 2 else '已为你指派大神',
                    preview=(f'{"、".join(participant_names)}已接单你的「{order.service_name_snapshot}」'),
                    msg_type='ORDER',
                    related_order_id=order.id,
                )
            else:
                # quick_dispatch itself is atomic: workers must not receive an
                # order ID before its debit and order have committed.
                transaction.on_commit(lambda order=order: _schedule_auto_cancel(order))
                enqueue_kook_dispatch(order, KookDispatchRecord.Trigger.NEW_ORDER)
                enqueue_wecom_dispatch(order, KookDispatchRecord.Trigger.NEW_ORDER)
                _push_message_safe(
                    recipient_id=order.customer_id,
                    recipient_account_id=order.customer_account_id,
                    recipient_business_cycle=order.business_cycle,
                    title='客服已为你下单',
                    preview=f'客服为你下单「{order.service_name_snapshot}」，正在为你匹配大神',
                    msg_type='ORDER',
                    related_order_id=order.id,
                )

            # A rolled-back order must not be broadcast to other clients.
            transaction.on_commit(lambda order=order: notify_order_update(order))
            return fund_success(request, 'admin.dispatch', data=self.get_serializer(order).data, msg='派单成功')

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """客服推进：已接单 → 服务中（保留中间流转过程）。"""
        order = self.get_object()
        from orders.models import Reservation
        booked = Reservation.objects.filter(order_id=order.pk, status=Reservation.Status.CONVERTED).exists()
        start_accounts = set()
        if booked:
            participant_accounts = list(order.providers.filter(released_at__isnull=True)
                                        .values_list('provider_account_id', flat=True))
            if (participant_accounts and any(account_id is None for account_id in participant_accounts)) or not order.provider_account_id:
                return Response({'code': 400, 'msg': '预约陪玩账号尚未核对，不能开始服务'})
            start_accounts = set(participant_accounts) | {order.provider_account_id}
            ensure_provider_dispatch_locks(start_accounts)

        def pre_check(o):
            if o.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付或已退款，不能开始服务')
            if not o.provider_id:
                raise IllegalTransitionError('请先指派陪玩')
            if booked:
                current_accounts = set(o.providers.filter(released_at__isnull=True)
                                       .values_list('provider_account_id', flat=True)) | {o.provider_account_id}
                if current_accounts != start_accounts:
                    raise IllegalTransitionError('预约陪玩已变更，请刷新后重新开始')

        def side_effect(o):
            from site_messages.staff_start_notice import enabled
            if enabled():
                return
            guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            for row in o.providers.filter(released_at__isnull=True):
                if row.provider_id:
                    if guarded and (not row.provider_account_id or not row.provider_business_cycle):
                        import logging
                        logging.getLogger(__name__).warning('客服开工通知原周期待核对 order=%s assignment=%s', o.pk, row.pk)
                        continue
                    preview = f'「{o.service_name_snapshot}」已进入服务中'
                    transaction.on_commit(lambda user_id=row.provider_id, account_id=row.provider_account_id,
                        cycle=row.provider_business_cycle, order_id=o.pk, preview=preview: _push_message_safe(
                            recipient_id=user_id, recipient_account_id=account_id,
                            recipient_business_cycle=cycle, require_cycle=guarded,
                            title='订单已开始服务', preview=preview, msg_type='ORDER', related_order_id=order_id))

        try:
            order = transition(
                order.id,
                Order.Status.IN_SERVICE,
                operator=request.legacy_user,
                operator_account=request.account,
                action=OrderStatusLog.Action.START,
                pre_check=pre_check,
                side_effect=side_effect,
                dispatch_account_ids=start_accounts,
            )
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许开始服务'})
        notify_order_update(order)
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '已开始服务'})

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """服务中 → 已完成；新单释放名额并待报单审核，旧单保留原结算方式。"""
        order = self.get_object()

        def pre_check(o):
            if o.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付或已退款，不能完成服务')
            if not o.provider_id:
                raise IllegalTransitionError('订单没有履约陪玩，请先核对派单')
            from orders.participation_entry import pending_transfer_segments
            if pending_transfer_segments(o).exists():
                raise IllegalTransitionError('有转入陪玩尚未确认入队，请先核对')

        def side_effect(o):
            # 与打手端共用履约完成服务，结算时机由订单快照决定。
            complete_fulfillment(
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
                pre_check=pre_check,
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
            preview=f'你的「{order.service_name_snapshot}」已完成，可在订单中评价',
            msg_type='ORDER',
            related_order_id=order.id,
        )
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '订单已完成'})

    @action(detail=True, methods=['post'])
    @protect_gift_refund('order')
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
                plan_token=request.data.get('plan_token'),
                refund_amount=request.data.get('refund_amount'),
                client_request_id=request.data.get('client_request_id'),
            )
        except RefundError as exc:
            return Response({'code':409 if exc.code in ('REFUND_PLAN_CHANGED','REFUND_REQUEST_CONFLICT') else 400,
                             'errorCode':exc.code,'msg':str(exc),'data':exc.data})
        except (IllegalTransitionError, SettlementError, WalletAddressingError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许退款'})
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '已退款'})

    @action(detail=True, methods=['get'], url_path='refund-preview')
    def refund_preview(self, request, pk=None):
        order = self.get_object()
        from orders.points_service_payment import needs_integrated_processing
        from orders.models import Reservation
        if (needs_integrated_processing(order)
                and Reservation.objects.filter(order=order, status=Reservation.Status.CONVERTED).exists()
                and (getattr(settings, 'SERVICE_POINTS_RESERVATION_CANCEL_TESTING', False)
                     or getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True)):
            from orders.points_reserved_cancel_test import preview
            from orders.refunds import parse_refund_amount
            try:
                amount = parse_refund_amount(request.query_params.get('refund_amount'), order.amount)
                if amount != order.amount:
                    raise RefundError('预约未开始时仅支持按原支付构成全额退款')
                return Response({'code': 0, 'data': preview(order.pk, operator_account=request.account)})
            except RefundError as exc:
                return Response({'code': 400, 'errorCode': exc.code, 'msg': str(exc), 'data': exc.data})
        if (needs_integrated_processing(order) and order.status == Order.Status.GRABBED
                and not Reservation.objects.filter(order=order).exists()):
            from orders.points_assigned_cancel import preview
            from orders.refunds import parse_refund_amount
            try:
                amount = parse_refund_amount(request.query_params.get('refund_amount'), order.amount)
                if amount != order.amount:
                    raise RefundError('未开始的积分订单仅支持按原支付构成全额退款')
                return Response({'code': 0, 'data': preview(order.pk, operator_account=request.account)})
            except RefundError as exc:
                return Response({'code': 400, 'errorCode': exc.code, 'msg': str(exc), 'data': exc.data})
        if (getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False)
                and order.status in (Order.Status.IN_SERVICE, Order.Status.COMPLETED) and order.in_service_at):
            from orders.models import OrderPlatformSubsidy
            if OrderPlatformSubsidy.objects.filter(order=order).exists():
                return Response({'code': 400, 'errorCode': 'SUBSIDY_NEGOTIATION_REQUIRED',
                                 'msg': '安全箱订单须核对协商报酬'})
        try:
            preview, _ = build_refund_plan(order,refund_amount=request.query_params.get('refund_amount'))
            return Response({'code':0,'data':preview})
        except RefundError as exc:
            return Response({'code':400,'errorCode':exc.code,'msg':str(exc),'data':exc.data})

    @action(detail=True, methods=['get'], url_path='negotiated-refund-options')
    def negotiated_refund_options(self, request, pk=None):
        from orders.negotiated_refunds import options
        try:
            return Response({'code': 0, 'data': options(self.get_object())})
        except RefundError as exc:
            return Response({'code': 400, 'errorCode': exc.code, 'msg': str(exc), 'data': exc.data})

    @action(detail=True, methods=['get'], url_path='subsidy-negotiation-options')
    def subsidy_negotiation_options(self, request, pk=None):
        from orders.subsidy_negotiation import options
        try:
            return Response({'code': 0, 'data': options(self.get_object())})
        except RefundError as exc:
            return Response({'code': 400, 'errorCode': exc.code, 'msg': str(exc), 'data': exc.data})

    @action(detail=True, methods=['post'], url_path='subsidy-negotiation-preview')
    def subsidy_negotiation_preview(self, request, pk=None):
        from orders.subsidy_negotiation import preview
        try:
            return Response({'code': 0, 'data': preview(self.get_object(), request.data.get('allocations'))})
        except RefundError as exc:
            return Response({'code': 400, 'errorCode': exc.code, 'msg': str(exc), 'data': exc.data})

    @action(detail=True, methods=['post'], url_path='subsidy-negotiation-confirm')
    def subsidy_negotiation_confirm(self, request, pk=None):
        from orders.subsidy_negotiation import apply
        if request.data.get('reviewed') is not True:
            return Response({'code': 400, 'errorCode': 'REFUND_REVIEW_REQUIRED', 'msg': '请先核对协商结果'})
        try:
            order = apply(self.get_object().pk, request.data.get('allocations'), reason=request.data.get('reason'),
                request_id=request.data.get('client_request_id'), plan_token=request.data.get('plan_token'),
                operator=request.legacy_user, operator_account=request.account)
            return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '订单已协商终止'})
        except RefundError as exc:
            return Response({'code': 409 if exc.code in ('REFUND_PLAN_CHANGED', 'REFUND_REQUEST_CONFLICT') else 400,
                             'errorCode': exc.code, 'msg': str(exc), 'data': exc.data})

    @action(detail=True, methods=['post'], url_path='negotiated-refund-preview')
    def negotiated_refund_preview(self, request, pk=None):
        from orders.negotiated_refunds import plan
        try:
            preview, _ = plan(self.get_object(), request.data.get('refund_amount'), request.data.get('allocations'))
            return Response({'code': 0, 'data': preview})
        except RefundError as exc:
            return Response({'code': 400, 'errorCode': exc.code, 'msg': str(exc), 'data': exc.data})

    @action(detail=True, methods=['post'], url_path='negotiated-refund')
    def negotiated_refund(self, request, pk=None):
        from orders.negotiated_refunds import apply
        reason = (request.data.get('reason') or '').strip()[:255]
        if not reason:
            return Response({'code': 400, 'msg': '请填写退款原因'})
        if request.data.get('reviewed') is not True:
            return Response({'code': 400, 'errorCode': 'REFUND_REVIEW_REQUIRED', 'msg': '请先核对协商资料和各方承担金额'})
        try:
            order = apply(self.get_object().pk, amount=request.data.get('refund_amount'),
                allocation=request.data.get('allocations'), reason=reason,
                request_id=request.data.get('client_request_id'), plan_token=request.data.get('plan_token'),
                operator=request.legacy_user, operator_account=request.account)
            return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '协商退款已完成'})
        except RefundError as exc:
            return Response({'code': 409 if exc.code in ('REFUND_PLAN_CHANGED', 'REFUND_REQUEST_CONFLICT') else 400,
                             'errorCode': exc.code, 'msg': str(exc), 'data': exc.data})
        except (IllegalTransitionError, SettlementError, WalletAddressingError) as exc:
            return Response({'code': 400, 'msg': str(exc)})

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        from orders.direct_payment import DirectPaymentCancellationUnconfirmed
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
        except DirectPaymentCancellationUnconfirmed as exc:
            return Response({'code': 409, 'errorCode': 'DIRECT_PAYMENT_CANCELLATION_UNCONFIRMED', 'msg': str(exc)})
        except (IllegalTransitionError, SettlementError, WalletAddressingError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许取消'})
        return Response({'code': 0, 'data': self.get_serializer(order).data, 'msg': '已取消'})

    @staticmethod
    @transaction.atomic
    def _do_refund(
        order_id,
        operator,
        operator_account,
        reason,
        action=OrderStatusLog.Action.REFUND,
        plan_token=None,
        refund_amount=None,
        client_request_id=None,
    ):
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
        if action == OrderStatusLog.Action.REFUND:
            from orders.refunds import apply_admin_refund
            order = apply_admin_refund(order_id,reason=reason,operator=operator,operator_account=operator_account,
                plan_token=plan_token,refund_amount=refund_amount,client_request_id=client_request_id,notify=False)
            if not getattr(order,'_refund_replayed',False):
                transaction.on_commit(lambda:notify_order_update(order))
            return order
        def pre_check(order):
            # The initial view lookup is not locked: a provider may have
            # accepted the order before transition acquired its row lock.
            if action == OrderStatusLog.Action.CANCEL and order.status != Order.Status.PENDING:
                raise IllegalTransitionError('仅待接单订单可直接取消，其它状态请用退款')
            if order.status == Order.Status.CANCELLED:
                raise IllegalTransitionError('订单已是终态，无法退款')
            from orders.direct_payment import require_cancel_channel_confirmation
            require_cancel_channel_confirmation(order)

        def side_effect(order):
            refund_to_customer(order, reason=reason, operator=operator, operator_account=operator_account,
                               allow_settled_recovery=action == OrderStatusLog.Action.REFUND, plan_token=plan_token)
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

        order = transition(
            order_id,
            Order.Status.CANCELLED,
            operator=operator,
            operator_account=operator_account,
            action=action,
            reason=reason,
            pre_check=pre_check,
            side_effect=side_effect,
            update_fields=['cancel_reason', 'payment_status', 'refunded_at'],
            allow_settled_refund=action == OrderStatusLog.Action.REFUND,
        )
        # Refund and cancel share this commit boundary. Never announce a state
        # that an enclosing transaction may still roll back.
        transaction.on_commit(lambda: notify_order_update(order))
        return order


# ---------------- 评价管理 ----------------
class EvaluationViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """评价管理：作废时保留评价和已关联售后。"""

    serializer_class = AdminEvaluationSerializer
    default_perm = 'evaluation:view'
    required_perms = {
        'reply': 'evaluation:reply',
        'destroy': 'evaluation:delete',
    }

    def get_queryset(self):
        qs = (
            Evaluation.objects.select_related('customer', 'customer_account', 'provider', 'provider_account', 'order')
            .prefetch_related('order__providers')
            .order_by('-created_at')
        )
        def account_match(role, field, value):
            return Q(**{f'{role}_account__{field}__icontains': value}) | (
                Q(**{f'{role}_account__isnull': True}) & Q(**{f'{role}__{field}__icontains': value})
            )
        score = self.request.query_params.get('score')
        if score:
            qs = qs.filter(score=score)
        low_rating = self.request.query_params.get('low_rating')
        if low_rating is not None:
            if low_rating not in ('1', 'true', 'True'):
                raise rest_serializers.ValidationError({'low_rating': '仅支持筛选低星评价'})
            qs = qs.filter(voided_at__isnull=True).filter(
                Q(score__lte=2) | Q(skill_score__lte=2) | Q(attitude_score__lte=2)
                | Q(communication_score__lte=2))
        aftersale_state = self.request.query_params.get('low_rating_aftersale_state')
        if aftersale_state is not None:
            if aftersale_state not in ('OPENED', 'ATTACHED', 'CLOSED', 'EXPIRED', 'INELIGIBLE'):
                raise rest_serializers.ValidationError({'low_rating_aftersale_state': '低星售后状态无效'})
            qs = qs.filter(low_rating_aftersale_state=aftersale_state)
        only_unreplied = self.request.query_params.get('only_unreplied')
        if only_unreplied in ('1', 'true', 'True'):
            qs = qs.filter(reply_content='', voided_at__isnull=True)
        if self.request.query_params.get('exclude_direct_test') == '1':
            qs = qs.filter(order__direct_payment__isnull=True)
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(order__order_no__icontains=keyword)
                | account_match('customer', 'username', keyword)
                | account_match('customer', 'nickname', keyword)
                | account_match('provider', 'username', keyword)
                | account_match('provider', 'nickname', keyword)
                | Q(Exists(OrderProvider.objects.filter(order_id=OuterRef('order_id'),
                    provider_id=OuterRef('provider_id'), released_at__isnull=True,
                    provider_name_snapshot__icontains=keyword)))
                | Q(order__provider_id=F('provider_id'), order__provider_name_snapshot__icontains=keyword)
            )
        order_no = self.request.query_params.get('order_no')
        if order_no:
            qs = qs.filter(order__order_no__icontains=order_no)
        customer_username = self.request.query_params.get('customer_username')
        if customer_username:
            qs = qs.filter(account_match('customer', 'username', customer_username))
        customer_nickname = self.request.query_params.get('customer_nickname')
        if customer_nickname:
            qs = qs.filter(account_match('customer', 'nickname', customer_nickname))
        provider_username = self.request.query_params.get('provider_username')
        if provider_username:
            qs = qs.filter(account_match('provider', 'username', provider_username))
        provider_nickname = self.request.query_params.get('provider_nickname')
        if provider_nickname:
            qs = qs.filter(account_match('provider', 'nickname', provider_nickname))
        return qs

    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        content = request.data.get('reply_content', '')
        if not isinstance(content, str) or len(content.strip()) > 1000:
            return Response({'code': 400, 'msg': '回复内容须为1000字以内的文本'})
        content = content.strip()
        if not content:
            return Response({'code': 400, 'msg': '回复内容不能为空'})
        evaluation = self.get_object()
        from orders.evaluation_replies import reply_notice_preview, valid_reply_version, write_reply_if_current
        version = request.data.get('reply_version')
        if not valid_reply_version(version):
            return Response({'code': 400, 'msg': '请刷新评价后再回复'}, status=400)
        with transaction.atomic():
            reply_order = Order.objects.select_for_update().get(pk=evaluation.order_id)
            if not write_reply_if_current(evaluation, content, version, 'STAFF'):
                return Response({'code': 409, 'errorCode': 'EVALUATION_REPLY_CONFLICT', 'msg': '回复已变化，请读取最新内容后核对'}, status=409)
            from site_messages.evaluation_reply_notice import (enabled as reply_notice_enabled,
                record as record_reply_notice, schedule_immediate as schedule_reply_notice)
            reply_preview = reply_notice_preview(evaluation)
            if reply_notice_enabled():
                record_reply_notice(evaluation, reply_order, staff=True, boss_preview=reply_preview)
            else:
                schedule_reply_notice(evaluation, reply_order, staff=True, boss_preview=reply_preview)
            transaction.on_commit(lambda: notify_order_update(Order.objects.get(pk=evaluation.order_id)))
            transaction.on_commit(notify_evaluation_update)
        return Response({
            'code': 0,
            'data': self.get_serializer(evaluation).data,
            'msg': '已回复',
        })

    def destroy(self, request, *args, **kwargs):
        from orders.evaluation_replies import valid_reply_version
        version = request.data.get('reply_version')
        if not valid_reply_version(version):
            return Response({'code': 400, 'msg': '请刷新评价后再作废'}, status=400)
        reason = request.data.get('void_reason')
        if not isinstance(reason, str) or not 2 <= len(reason.strip()) <= 1000:
            return Response({'code': 400, 'msg': '请填写 2 至 1000 字作废原因'}, status=400)
        with transaction.atomic():
            evaluation = get_object_or_404(Evaluation.objects.select_for_update(), pk=kwargs['pk'])
            if evaluation.reply_version != version or evaluation.voided_at is not None:
                return Response({'code': 409, 'errorCode': 'EVALUATION_REPLY_CONFLICT', 'msg': '回复已变化，请读取最新评价后核对'}, status=409)
            evaluation.voided_at = timezone.now()
            evaluation.void_reason = reason.strip()
            evaluation.voided_by = request.account
            evaluation.reply_version += 1
            evaluation.save(update_fields=['voided_at', 'void_reason', 'voided_by', 'reply_version', 'updated_at'])
            refresh_escort_rating(evaluation.provider_id, provider_account_id=evaluation.provider_account_id)
            order_id = evaluation.order_id
            transaction.on_commit(lambda: notify_order_update(Order.objects.get(pk=order_id)))
            transaction.on_commit(notify_evaluation_update)
        return Response({'code': 0, 'data': self.get_serializer(evaluation).data, 'msg': '已作废'})


# ---------------- 钱包 ----------------
class WalletViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminWalletSerializer
    default_perm = 'wallet:view'
    required_perms = {'adjust': 'wallet:adjust', 'recharge': 'wallet:recharge', 'recharge_packages': 'wallet:recharge', 'recharge_quote': 'wallet:recharge',
                      'platform_topup': 'wallet:adjust'}

    @action(detail=False, methods=['get'], url_path='platform-funds')
    def platform_funds(self, request):
        if not getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            return Response({'code': 0, 'data': {'enabled': False}})
        from orders.subsidies import pending_subsidy_shares, subsidy_funds
        unpaid = pending_subsidy_shares()
        totals = unpaid.aggregate(orders=Count('subsidy__order_id', distinct=True), amount=Sum('amount'))
        raw_page = request.query_params.get('pending_page', '1')
        if not raw_page.isascii() or not raw_page.isdigit() or len(raw_page) > 9 or int(raw_page) < 1:
            return Response({'code': 400, 'msg': '待结算页码无效'}, status=400)
        pages = max(1, (totals['orders'] + 9) // 10)
        page = min(int(raw_page), pages)
        pending = {'order_count': totals['orders'], 'amount': totals['amount'] or 0, 'orders': [],
                   'page': page, 'pages': pages}
        if 'order:view' in get_account_permissions(request.account):
            rows = (unpaid.values('subsidy__order_id', 'subsidy__order__order_no',
                                  'subsidy__order__service_name_snapshot', 'subsidy__order__status')
                    .annotate(amount=Sum('amount')).order_by('subsidy__order_id')[(page - 1) * 10:page * 10])
            pending['orders'] = [{'id': row['subsidy__order_id'], 'order_no': row['subsidy__order__order_no'],
                'service_name': row['subsidy__order__service_name_snapshot'], 'status': row['subsidy__order__status'],
                'amount': row['amount']} for row in rows]
        wallet = Wallet.objects.filter(user__username=settings.PLATFORM_SYSTEM_USERNAME).first()
        if not wallet:
            return Response({'code': 0, 'data': {'enabled': True, 'wallet_id': None,
                'funds_status': 'NOT_INITIALIZED', 'balance': None, 'deposits': None,
                'available': None, 'pending': pending}})
        return Response({'code': 0, 'data': {'enabled': True, 'wallet_id': wallet.pk,
            'funds_status': 'AVAILABLE', **subsidy_funds(wallet), 'pending': pending}})

    @action(detail=False, methods=['post'], url_path='platform-topup')
    def platform_topup(self, request):
        if not getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            return Response({'code': 400, 'msg': '平台补贴功能尚未开放'})
        from rest_framework import serializers
        try:
            amount = serializers.IntegerField(min_value=1, max_value=2147483647).run_validation(request.data.get('amount'))
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '请输入合法的补入金额'})
        reference = request.data.get('reference')
        if not isinstance(reference, str) or not 1 <= len(reference.strip()) <= 64:
            return Response({'code': 400, 'msg': '请填写1至64字线下资金凭据编号'})
        reference = reference.strip()
        request_id = request.data.get('client_request_id')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
            return Response({'code': 400, 'msg': '缺少入账操作凭证'})
        with transaction.atomic():
            from orders.financial_lock import lock_order_money
            lock_order_money()
            wallet = get_platform_wallet(for_update=True)
            replayed, reason = peek_fund_write(account=request.account, request_id=request_id)
            if replayed:
                return Response({'code': 409, 'msg': reason}, status=409)
            if Transaction.objects.filter(wallet=wallet, tx_type=Transaction.TxType.ADJUST,
                                          external_tx_id=reference, status=Transaction.Status.SUCCESS).exists():
                return Response({'code': 409, 'msg': '该线下凭据已登记，请核对平台流水'}, status=409)
            if wallet.balance + amount > 2147483647:
                return Response({'code': 400, 'msg': '入账后平台余额超过系统范围'})
            allowed, reason = claim_fund_write(account=request.account, request_id=request_id,
                scope='admin.platform_topup', fingerprint=f'{amount}:{reference}')
            if not allowed:
                return Response({'code': 409, 'msg': reason}, status=409)
            before = wallet.balance
            wallet.balance += amount
            wallet.save(update_fields=['balance'])
            tx = Transaction.objects.create(wallet=wallet, amount=amount, tx_type=Transaction.TxType.ADJUST,
                balance_before=before, balance_after=wallet.balance, external_tx_id=reference,
                remark=f'平台线下补入：{reference}', operator=request.legacy_user, operator_account=request.account)
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
        return Response({'code': 0, 'data': {'wallet_id': wallet.pk, 'transaction_id': tx.pk, 'balance': wallet.balance},
                         'msg': '平台资金已登记入账'})

    @action(detail=False, methods=['get'], url_path='recharge-packages')
    def recharge_packages(self, request):
        from engagement.models import RechargePackage
        packages = RechargePackage.objects.filter(active=True).values('id', 'amount', 'gift_amount')
        return Response({'code': 0, 'data': [{**row, 'points_rule': 'VIP_AT_PAYMENT'} for row in packages]})

    @action(detail=False, methods=['get'], url_path='recharge-quote')
    def recharge_quote(self, request):
        from engagement.recharge_pricing import recharge_quote
        from rest_framework import serializers
        try:
            amount = serializers.IntegerField(min_value=1, max_value=2147483647).run_validation(request.query_params.get('amount'))
            quote = recharge_quote(amount)
        except (serializers.ValidationError, ValueError):
            return Response({'code': 400, 'msg': '充值金额或门店预存档位无效，请核对'}, status=400)
        return Response({'code': 0, 'data': quote})

    def get_queryset(self):
        # 按门店业务身份区分钱包；陪玩只能调账，不能普通充值。
        from rest_framework.exceptions import ValidationError
        account_type = self.request.query_params.get('account_type', 'BOSS')
        if account_type not in ('BOSS', 'PROVIDER'):
            raise ValidationError('请选择老板或打手钱包')
        role = User.Role.CUSTOMER if account_type == 'BOSS' else User.Role.PROVIDER
        qs = (
            Wallet.objects.filter(Q(account__account_type=account_type) | Q(account__isnull=True, user__role=role))
            .select_related('user', 'user__boss_type', 'account', 'account__boss_type')
            .order_by('-updated_at')
        )
        # 统一关键词在账户标识中任一匹配，仍与独立条件和账户类型叠加。
        keyword = (self.request.query_params.get('keyword') or '').strip()
        if keyword:
            qs = qs.filter(
                Q(account__account_no__icontains=keyword)
                | Q(account__isnull=True, user__boss_no__icontains=keyword)
                | Q(account__username__icontains=keyword)
                | Q(account__nickname__icontains=keyword)
                | Q(account__phone__icontains=keyword)
                | (Q(account__isnull=True) & (Q(user__username__icontains=keyword)
                    | Q(user__nickname__icontains=keyword) | Q(user__phone__icontains=keyword)))
            )
        # 编号 / 昵称 / 手机号 三个独立条件，相互 AND 叠加（支持输入即查提示）
        boss_no = self.request.query_params.get('boss_no')
        if boss_no:
            qs = qs.filter(Q(account__account_no__icontains=boss_no) | Q(account__isnull=True, user__boss_no__icontains=boss_no))
        nickname = self.request.query_params.get('nickname')
        if nickname:
            qs = qs.filter(Q(account__nickname__icontains=nickname) | Q(account__isnull=True, user__nickname__icontains=nickname))
        phone = self.request.query_params.get('phone')
        if phone:
            qs = qs.filter(Q(account__phone__icontains=phone) | Q(account__isnull=True, user__phone__icontains=phone))
        return qs

    @action(detail=True, methods=['post'])
    def adjust(self, request, pk=None):
        from rest_framework import serializers
        try:
            amount = serializers.IntegerField(min_value=-2147483647, max_value=2147483647).run_validation(request.data.get('amount'))
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': f'请输入合法的{current_currency_name()}调账金额'})
        if amount == 0:
            return Response({'code': 400, 'msg': '调账金额不能为 0'})
        remark = (request.data.get('remark') or '').strip()[:255]
        if not remark:
            return Response({'code': 400, 'msg': '请填写调账原因'}, status=400)
        from engagement.models import PointFundingLot
        balance_kind = request.data.get('balance_kind') or PointFundingLot.Kind.PREPAID
        valid_kinds = {PointFundingLot.Kind.PREPAID, PointFundingLot.Kind.GIFT, PointFundingLot.Kind.COMPENSATION}
        if balance_kind not in valid_kinds:
            return Response({'code': 400, 'msg': '请选择有效的资金类型'}, status=400)

        lifecycle_provider = None
        wallet_identity = None
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            wallet_identity = Wallet.objects.filter(pk=pk).values(
                'account_id', 'account__account_type', 'user__role').first()
            if wallet_identity and (wallet_identity['account__account_type'] == ClubAccount.AccountType.PROVIDER
                                    or (not wallet_identity['account_id'] and wallet_identity['user__role'] == User.Role.PROVIDER)):
                replayed, reason = peek_fund_write(account=request.account, request_id=request.data.get('client_request_id'))
                if replayed:
                    return Response({'code': 409, 'msg': reason}, status=409)
                lifecycle_provider = ClubAccount.objects.filter(pk=wallet_identity['account_id'],
                    account_type=ClubAccount.AccountType.PROVIDER).first()
                if not lifecycle_provider:
                    return Response({'code': 409, 'msg': '陪玩钱包账号归属待核对'}, status=409)
                from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
                try:
                    ensure_provider_lifecycle(lifecycle_provider)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请稍后重试'}, status=503)
        with transaction.atomic():
            if lifecycle_provider:
                from club_accounts.provider_lifecycle import lock_provider_operation, provider_membership_current
                try:
                    lock_provider_operation(lifecycle_provider)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请稍后重试'}, status=503)
            wallet = get_object_or_404(Wallet.objects.select_for_update().filter(
                Q(account__account_type__in=(ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER))
                | Q(account__isnull=True, user__role__in=(User.Role.CUSTOMER, User.Role.PROVIDER))
            ), pk=pk)
            is_provider = (wallet.account.account_type == ClubAccount.AccountType.PROVIDER if wallet.account_id
                           else wallet.user.role == User.Role.PROVIDER)
            if wallet_identity is not None and (wallet.account_id != wallet_identity['account_id']
                    or is_provider != (wallet_identity['account__account_type'] == ClubAccount.AccountType.PROVIDER
                                       or (not wallet_identity['account_id'] and wallet_identity['user__role'] == User.Role.PROVIDER))):
                return Response({'code': 409, 'msg': '钱包归属已变化，请刷新后重试'}, status=409)
            if is_provider and balance_kind != PointFundingLot.Kind.PREPAID:
                return Response({'code': 400, 'msg': '陪玩钱包调账不支持老板资金类型'}, status=400)
            replayed, reason = peek_fund_write(account=request.account, request_id=request.data.get('client_request_id'))
            if replayed:
                return Response({'code': 409, 'msg': reason}, status=409)
            if lifecycle_provider and (wallet.account_id != lifecycle_provider.pk
                                       or not provider_membership_current(lifecycle_provider)):
                return Response({'code': 409, 'msg': '陪玩账号或钱包归属已变化，请刷新后重试'}, status=409)
            if not wallet.is_active:
                return Response({'code': 403, 'msg': '该钱包已停用，无法调账'}, status=403)
            if wallet.balance + amount < 0:
                return Response({'code': 400, 'msg': '调账后余额不能为负'})
            if wallet.balance + amount > 2147483647:
                return Response({'code': 400, 'msg': '调账后余额超过系统范围'}, status=400)
            if amount > 0 and not wallet.account_id and balance_kind != PointFundingLot.Kind.PREPAID:
                return Response({'code': 400, 'msg': '该旧账号尚未关联门店账户，暂不能登记赠送或赔款'}, status=400)
            allowed, reason = claim_fund_write(
                account=request.account, request_id=request.data.get('client_request_id'),
                scope='admin.adjust', fingerprint=f'{wallet.pk}:{amount}:{balance_kind}:{remark}',
            )
            if not allowed:
                return Response({'code': 409, 'msg': reason}, status=409)
            balance_before = wallet.balance
            wallet.balance += amount
            wallet.save(update_fields=['balance'])
            tx = Transaction.objects.create(
                wallet=wallet,
                amount=amount,
                tx_type=Transaction.TxType.ADJUST,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=remark,
                operator=request.legacy_user,
                operator_account=request.account,
            )
            if not is_provider:
                from engagement.services import record_wallet_adjustment
                record_wallet_adjustment(wallet=wallet, tx=tx, kind=balance_kind)
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
        return Response({'code': 0, 'data': self.get_serializer(wallet).data, 'msg': '调账成功'})

    @action(detail=False, methods=['post'])
    def recharge(self, request):
        """确认企微客服收款，并按 1:10 兑换规则录入浪花。

        用户标识收口：前端 user 选择器返回 ``ClubAccount.id``，统一用 ``account_id``
        字段透传；legacy ``CustomUser.id`` 仍可由 ``user_id`` 字段兼容传入（旧后台 /
        内部工具）。两者是不同的 ID 空间，分字段传入可彻底避免"两套 ID 空间错充"。
        """
        from wallet.recharge_receipts import RechargeReplayError, find as find_recharge, fingerprint as recharge_fingerprint
        proof_for_receipt = request.FILES.get('proof_image')
        signature = recharge_fingerprint(request.data, proof_for_receipt)

        def replay_response(record):
            from wallet.models import RechargeGiftApproval
            approval = RechargeGiftApproval.objects.filter(recharge=record).first()
            return Response({'code': 0, 'msg': '已恢复原充值结果，本次未重复入账', 'data': {
                'wallet': self.get_serializer(record.recharge_tx.wallet).data,
                'record': AdminRechargeRecordSerializer(record).data,
                'credited_coins': record.total_amount / 10, 'exchange_rate': 10,
                'gift_approval_pending': bool(approval and approval.status == RechargeGiftApproval.Status.PENDING),
                'replayed': True}})

        try:
            prior = find_recharge(request.account, request.data.get('client_request_id'), signature)
        except RechargeReplayError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        if prior:
            return replay_response(prior)

        account_id = request.data.get('account_id')
        user_id = request.data.get('user_id')
        target_account = None
        if account_id not in (None, ''):
            raw = str(account_id)
            if not raw.isascii() or not raw.isdigit() or len(raw) > 19 or not 0 < int(raw) <= 9223372036854775807:
                return Response({'code': 400, 'msg': '非法的用户标识'})
            target_account = ClubAccount.objects.filter(pk=int(raw)).first()
            if target_account is None:
                return Response({'code': 404, 'msg': '用户不存在'})
            if target_account.account_type != ClubAccount.AccountType.BOSS:
                return Response({'code': 403, 'msg': '仅老板可充值；陪玩钱包请按权限调账'}, status=403)
            if not target_account.is_active:
                return Response({'code': 403, 'msg': '该账号已停用，无法充值'}, status=403)
            mapping = LegacyAccountMap.objects.filter(account=target_account).first()
            linked_wallet = Wallet.objects.filter(account=target_account).first()
            if mapping and linked_wallet and linked_wallet.user_id and mapping.legacy_user_id != linked_wallet.user_id:
                return Response({'code': 409, 'msg': '账号与钱包归属不一致，请先核对'}, status=409)
            legacy_user_id = (linked_wallet.user_id if linked_wallet else None) or (mapping.legacy_user_id if mapping else None)
        elif user_id not in (None, ''):
            try:
                legacy_user_id = int(user_id)
            except (TypeError, ValueError):
                return Response({'code': 400, 'msg': '非法的用户标识'})
            target = User.objects.filter(pk=legacy_user_id).first()
            if not target:
                legacy_user_id = None
            elif target.role != User.Role.CUSTOMER:
                return Response({'code': 403, 'msg': '仅老板可充值；陪玩钱包请按权限调账'}, status=403)
            elif not target.is_active:
                return Response({'code': 403, 'msg': '该账号已停用，无法充值'}, status=403)
        else:
            return Response({'code': 400, 'msg': '请指定充值用户'})
        if legacy_user_id is None and target_account is None:
            return Response({'code': 404, 'msg': '账号尚未关联钱包，请联系客服核对'})

        from rest_framework import serializers
        try:
            amount = serializers.IntegerField(min_value=1, max_value=2147483647).run_validation(request.data.get('amount', 0))
            requested_gift = (serializers.IntegerField(min_value=0, max_value=2147483647).run_validation(request.data['gift_amount'])
                              if request.data.get('gift_amount') not in (None, '') else None)
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': f'请输入合法的{current_currency_name()}金额'})
        if amount <= 0:
            return Response({'code': 400, 'msg': '实充金额必须大于 0'})
        remark = (request.data.get('remark') or '').strip()[:255]
        trade_no = str(request.data.get('trade_no') or '').strip()
        if len(trade_no) > 64:
            return Response({'code': 400, 'msg': '收款流水号不能超过64位'})
        proof_image = request.FILES.get('proof_image')
        if getattr(settings, 'REQUIRE_RECHARGE_PROOF', True) and (not trade_no or not proof_image):
            return Response({'code': 400, 'msg': '请填写收款流水号并上传收款凭证，核实收款后再充值'})
        if proof_image:
            from rest_framework import serializers
            try:
                serializers.ImageField(max_length=None).run_validation(proof_image)
            except (serializers.ValidationError, DjangoValidationError):
                return Response({'code': 400, 'msg': '收款凭证必须为有效图片'})

        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            from engagement.recharge_pricing import lock_recharge_packages, recharge_quote
            lock_recharge_packages()
            try:
                prior = find_recharge(request.account, request.data.get('client_request_id'), signature, lock=True)
            except RechargeReplayError as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
            if prior:
                return replay_response(prior)
            try:
                bonus = recharge_quote(amount)
            except ValueError as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
            expected_bonus_version = request.data.get('bonus_version')
            if expected_bonus_version and expected_bonus_version != bonus['version']:
                return Response({'code': 409, 'errorCode': 'RECHARGE_BONUS_CHANGED', 'msg': '预存档位已变化，请重新核对赠送金额'}, status=409)
            pending_gift = requested_gift is not None and requested_gift != bonus['gift_amount']
            gift_amount = 0 if pending_gift else bonus['gift_amount']
            try:
                wallet = get_wallet(account_id=target_account.pk, user_id=legacy_user_id, for_update=True) if target_account else get_wallet(user_id=legacy_user_id, for_update=True)
            except WalletAddressingError:
                return Response({'code': 409, 'msg': '账号与钱包归属不一致，请先核对'}, status=409)
            # 旧 user_id 可能映射到陪玩的业务账户；不得借旧身份绕过限制。
            if wallet.account_id and wallet.account.account_type != ClubAccount.AccountType.BOSS:
                transaction.set_rollback(True)
                return Response({'code': 403, 'msg': '仅老板可充值；陪玩钱包请按权限调账'}, status=403)
            if not wallet.account_id and (gift_amount or pending_gift):
                transaction.set_rollback(True)
                return Response({'code': 409, 'msg': '该历史钱包尚未关联老板账号，请先核对后再登记赠送'}, status=409)
            if not wallet.is_active:
                return Response({'code': 403, 'msg': '该钱包已停用，无法充值'})
            recharge_cycle = None
            if wallet.account_id:
                # Retirement uses the same wallet -> account lock order.  A
                # successful request is replayed above before this check; a
                # new recharge must belong to the current active membership.
                recharge_account = ClubAccount.objects.select_for_update().filter(
                    pk=wallet.account_id, account_type=ClubAccount.AccountType.BOSS).first()
                if not recharge_account or not recharge_account.is_active or not recharge_account.license_membership_active:
                    transaction.set_rollback(True)
                    return Response({'code': 409, 'msg': '老板已退店或账号停用，请先恢复本店身份后充值'}, status=409)
                recharge_cycle = recharge_account.business_cycle
            if max(wallet.balance + amount + gift_amount, wallet.total_recharge + amount, wallet.total_gift + gift_amount) > 2147483647:
                return Response({'code': 400, 'msg': '充值后余额或累计金额超过系统范围，请核对金额'})
            # 全局收款流水去重，跨客服、跨老板重复录入也不能重复入账。
            if trade_no and RechargeRecord.objects.filter(trade_no__iexact=trade_no).exists():
                return Response({'code': 409, 'msg': '该收款流水已入账，请在充值记录中核对'})
            allowed, reason = claim_fund_write(
                account=request.account,
                request_id=request.data.get('client_request_id'),
                scope='admin.recharge',
                fingerprint=f'{legacy_user_id if legacy_user_id is not None else "account:" + str(wallet.account_id)}:{amount}:{requested_gift}:{trade_no}',
            )
            if not allowed:
                return Response({'code': 409, 'msg': reason})
            try:
                with transaction.atomic():
                    record = RechargeRecord.objects.create(
                        user_id=legacy_user_id, account_id=wallet.account_id,
                        account_business_cycle=recharge_cycle,
                        amount=amount, gift_amount=gift_amount, trade_no=trade_no,
                        bonus_snapshot=bonus,
                        receipt_key=hashlib.sha256(trade_no.casefold().encode()).hexdigest() if trade_no else None,
                        proof_image=proof_image, remark=remark,
                        operator=request.legacy_user, operator_account=request.account,
                    )
            except IntegrityError:
                transaction.set_rollback(True)
                return Response({'code': 409, 'msg': '该收款流水已入账，请勿重复充值'})
            balance_before = wallet.balance
            recharge_tx = Transaction.objects.create(
                wallet=wallet,
                amount=amount,
                tx_type=Transaction.TxType.TOPUP,
                balance_before=balance_before,
                balance_after=balance_before + amount,
                remark=remark or f'企微客服充值兑换{current_currency_name()}',
                operator=request.legacy_user,
                operator_account=request.account,
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
                    remark=remark or f'客服赠送{current_currency_name()}',
                    operator=request.legacy_user,
                    operator_account=request.account,
                )
                wallet.balance += gift_amount
                wallet.total_gift += gift_amount

            wallet.save(update_fields=['balance', 'total_recharge', 'total_gift'])

            record.recharge_tx = recharge_tx
            record.gift_tx = gift_tx
            record.save(update_fields=['recharge_tx', 'gift_tx'])
            if pending_gift:
                from wallet.models import RechargeGiftApproval
                RechargeGiftApproval.objects.create(recharge=record, standard_amount=bonus['gift_amount'],
                                                     requested_amount=requested_gift)
            from engagement.services import record_recharge
            record_recharge(record)
            from wallet.recharge_receipts import save as save_recharge_receipt
            save_recharge_receipt(request.account, request.data.get('client_request_id'), signature, record)
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
            # Bind the notice to this recharge's membership and currency.
            # A delayed callback must not deliver an old recharge to a boss
            # who has since left and rejoined the store.
            from site_messages.recharge_receipt_notice import enabled as recharge_notice_enabled
            if recharge_notice_enabled():
                from site_messages.recharge_receipt_notice import record as record_recharge_notice
                record_recharge_notice(record, gift_pending=bool(pending_gift))
            else:
                recharge_currency = current_currency_name()
                transaction.on_commit(lambda: _push_message_safe(
                    recipient_id=legacy_user_id, recipient_account_id=wallet.account_id, title='充值已到账',
                    preview=(f'客服确认收款，本金到账 {amount / 10:g} {recharge_currency}，赠送待审批' if pending_gift
                             else f'客服确认收款，到账 {(amount + gift_amount) / 10:g} {recharge_currency}'),
                    recipient_business_cycle=recharge_cycle, require_cycle=bool(wallet.account_id),
                ))

        return Response({
            'code': 0,
            'msg': '本金已到账，赠送待审批' if pending_gift else '已按 1:10 兑换规则入账店币',
            'data': {
                'wallet': self.get_serializer(wallet).data,
                'record': AdminRechargeRecordSerializer(record).data,
                'credited_coins': (amount + gift_amount) / 10,
                'exchange_rate': 10,
                'gift_approval_pending': pending_gift,
            },
        })


class RechargeRecordViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """老板充值记录：列表 / 筛选。"""

    serializer_class = AdminRechargeRecordSerializer
    default_perm = 'recharge:view'
    required_perms = {'gift_decision': 'recharge:gift_approve'}

    @action(detail=True, methods=['post'], url_path='gift-decision')
    def gift_decision(self, request, pk=None):
        from wallet.models import RechargeGiftApproval
        from wallet.recharge_approval import RechargeApprovalError, resolve_recharge_gift
        decision = request.data.get('decision')
        reason = request.data.get('reason', '')
        if not isinstance(reason, str) or len(reason.strip()) > 255:
            return Response({'code': 400, 'msg': '审批说明不能超过 255 字'}, status=400)
        try:
            resolve_recharge_gift(pk, decision, reason.strip(), request.account, request.legacy_user)
        except RechargeGiftApproval.DoesNotExist:
            return Response({'code': 404, 'msg': '充值赠送审批不存在'}, status=404)
        except RechargeApprovalError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        record = self.get_queryset().get(pk=pk)
        message = '审批完成，赠送已按结果入账' if record.gift_amount else '审批完成，本次无赠送金额'
        return Response({'code': 0, 'msg': message, 'data': self.get_serializer(record).data})

    def get_queryset(self):
        qs = RechargeRecord.objects.select_related('user', 'user__boss_type', 'operator', 'operator_account', 'account', 'account__boss_type', 'gift_approval').order_by('-created_at')
        approval_status = self.request.query_params.get('gift_approval_status')
        if approval_status:
            if approval_status not in ('PENDING', 'APPROVED', 'REJECTED'):
                from rest_framework.exceptions import ValidationError
                raise ValidationError('请选择有效的赠送审批状态')
            qs = qs.filter(gift_approval__status=approval_status)
        wallet_id = self.request.query_params.get('wallet_id')
        if wallet_id:
            from rest_framework.exceptions import ValidationError
            if not wallet_id.isascii() or not wallet_id.isdigit() or len(wallet_id) > 19 or not 0 < int(wallet_id) <= 9223372036854775807:
                raise ValidationError('钱包编号无效')
            # Explicit account ownership takes precedence over legacy mapping.
            # Unlinked historical records retain the legacy wallet association.
            qs = qs.filter(Q(account__wallet__pk=int(wallet_id)) | Q(account__isnull=True, user__wallet__pk=int(wallet_id)))
        trade_no = self.request.query_params.get('trade_no', '').strip()
        if trade_no:
            qs = qs.filter(trade_no__iexact=trade_no)
        account_id = self.request.query_params.get('account_id')
        user_id = self.request.query_params.get('user_id')
        if account_id:
            if not account_id.isascii() or not account_id.isdigit() or len(account_id) > 19 or not 0 < int(account_id) <= 9223372036854775807:
                return qs.none()
            account_id = int(account_id)
            legacy_user_id = _legacy_user_id_from_account_id(account_id)
            qs = qs.filter(Q(account_id=account_id) | Q(account__isnull=True, user_id=legacy_user_id if legacy_user_id is not None else -1))
        elif user_id:
            qs = qs.filter(user_id=user_id)
        # 编号 / 昵称 / 手机号 三个独立条件，相互 AND 叠加（支持输入即查提示）
        boss_no = self.request.query_params.get('boss_no')
        if boss_no:
            qs = qs.filter(Q(account__account_no__icontains=boss_no) | Q(account__isnull=True, user__boss_no__icontains=boss_no))
        nickname = self.request.query_params.get('nickname')
        if nickname:
            qs = qs.filter(Q(account__nickname__icontains=nickname) | Q(account__isnull=True, user__nickname__icontains=nickname))
        phone = self.request.query_params.get('phone')
        if phone:
            qs = qs.filter(Q(account__phone__icontains=phone) | Q(account__isnull=True, user__phone__icontains=phone))
        return qs


class DisposeRecordViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """奖励罚款记录：列表 / 筛选。"""

    serializer_class = AdminDisposeRecordSerializer
    default_perm = 'dispose:view'

    def get_queryset(self):
        qs = DisposeRecord.objects.select_related('user', 'account', 'operator', 'operator_account').order_by('-created_at')
        account_id = self.request.query_params.get('account_id')
        user_id = self.request.query_params.get('user_id')
        if account_id:
            legacy_user_id = _legacy_user_id_from_account_id(account_id)
            valid_id = int(account_id) if account_id.isascii() and account_id.isdigit() and len(account_id) <= 19 and 0 < int(account_id) <= 9223372036854775807 else -1
            qs = qs.filter(Q(account_id=valid_id) | Q(account__isnull=True, user_id=legacy_user_id if legacy_user_id is not None else -1))
        elif user_id:
            qs = qs.filter(user_id=user_id)
        dispose_type = self.request.query_params.get('dispose_type')
        if dispose_type:
            qs = qs.filter(dispose_type=dispose_type.upper())
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(account__username__icontains=keyword)
                | Q(account__nickname__icontains=keyword)
                | (Q(account__isnull=True) & (Q(user__username__icontains=keyword) | Q(user__nickname__icontains=keyword)))
            )
        return qs


class TransactionViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminTransactionSerializer
    default_perm = 'transaction:view'

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        if args:
            from wallet.addon_transactions import addon_links
            instances = args[0] if kwargs.get('many') else [args[0]]
            serializer.context['addon_links'] = addon_links(item.pk for item in instances)
        return serializer

    def get_queryset(self):
        qs = Transaction.objects.select_related('wallet', 'wallet__user', 'wallet__account', 'operator', 'operator_account', 'order').order_by('-created_at')
        order_no = self.request.query_params.get('order_no_exact', '').strip()
        if order_no:
            from identifiers.services import entity_ids_for_code
            from orders.models import (OrderAddonPayment, OrderAddonRefund, OrderAddonRefundParticipant,
                                       OrderAddonSettlement, OrderSegmentAddonSettlement)
            matches = Order.objects.filter(Q(order_no=order_no) |
                Q(pk__in=entity_ids_for_code(order_no, 'orders.order', exact=True)))
            addon_payments = OrderAddonPayment.objects.filter(intent__order__in=matches).values('buyer_transaction_id')
            addon_refunds = OrderAddonRefund.objects.filter(order__in=matches)
            addon_participants = OrderAddonRefundParticipant.objects.filter(
                proposal__refund__order__in=matches).values('provider_transaction_id')
            addon_income = OrderAddonSettlement.objects.filter(
                payment__intent__order__in=matches).values('income_transaction_id')
            segment_addon_income = OrderSegmentAddonSettlement.objects.filter(
                payment__intent__order__in=matches).values('income_transaction_id')
            from wallet.models import RefundDepositDeficit
            addon_deposits = RefundDepositDeficit.objects.filter(addon_refund__order__in=matches).values('source_store_transaction_id')
            qs = qs.filter(Q(order__in=matches) | Q(pk__in=addon_payments)
                | Q(pk__in=addon_refunds.values('buyer_transaction_id'))
                | Q(pk__in=addon_refunds.values('provider_transaction_id'))
                | Q(pk__in=addon_participants) | Q(pk__in=addon_income) | Q(pk__in=segment_addon_income)
                | Q(pk__in=addon_deposits))
        wallet_id = self.request.query_params.get('wallet_id')
        if wallet_id:
            from rest_framework.exceptions import ValidationError
            if not wallet_id.isascii() or not wallet_id.isdigit() or len(wallet_id) > 19 or not 0 < int(wallet_id) <= 9223372036854775807:
                raise ValidationError('钱包编号无效')
            qs = qs.filter(wallet_id=int(wallet_id))
        tx_type = self.request.query_params.get('tx_type')
        if tx_type:
            qs = qs.filter(tx_type=tx_type.upper())
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(tx_no__icontains=keyword)
                | Q(wallet__account__username__icontains=keyword)
                | (Q(wallet__account__isnull=True) & Q(wallet__user__username__icontains=keyword))
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
            'provider', 'provider__escort_profile__level', 'provider_account',
            'provider_account__escort_profile', 'auditor', 'order',
        ).prefetch_related(
            'result_images', 'order__providers',
        ).exclude(status=ProviderReport.Status.DRAFT).order_by('-created_at')
        if self.request.query_params.get('exclude_direct_test') == '1':
            qs = qs.filter(Q(order__isnull=True) | Q(order__direct_payment__isnull=True))
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        def provider_match(field, value):
            return Q(**{f'provider_account__{field}__icontains': value}) | (
                Q(provider_account__isnull=True) & Q(**{f'provider__{field}__icontains': value})
            )
        def profile_name_match(value):
            return Q(provider_account__escort_profile__display_name__icontains=value) | (
                Q(provider_account__isnull=True) & Q(provider__escort_profile__display_name__icontains=value)
            )
        def profile_no_match(value):
            return Q(provider_account__escort_profile__escort_no__icontains=value) | (
                Q(provider_account__isnull=True) & Q(provider__escort_profile__escort_no__icontains=value)
            )
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(game_name__icontains=keyword)
                | provider_match('username', keyword)
                | provider_match('nickname', keyword)
                | profile_name_match(keyword)
                | profile_no_match(keyword)
            )
        game_name = self.request.query_params.get('game_name')
        if game_name:
            qs = qs.filter(game_name__icontains=game_name)
        provider_username = self.request.query_params.get('provider_username')
        if provider_username:
            qs = qs.filter(provider_match('username', provider_username))
        provider_nickname = self.request.query_params.get('provider_nickname')
        if provider_nickname:
            qs = qs.filter(provider_match('nickname', provider_nickname) | profile_name_match(provider_nickname) | profile_no_match(provider_nickname))
        if self.request.query_params.get('order_blocked') == '1':
            from .serializers import pending_report_order_block_q
            qs = qs.filter(pending_report_order_block_q(qs))
        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if request.query_params.get('exclude_direct_test') == '1' and response.data.get('code') == 0:
            response.data['data']['exclude_direct_test_applied'] = True
        return response

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        initial = self.get_object()
        if initial.order_id:
            try:
                report = approve_order_report(
                    initial.pk, operator=request.legacy_user, operator_account=request.account,
                )
            except (SettlementError, WalletAddressingError) as exc:
                return Response({'code': 400, 'msg': str(exc)})
            return Response({'code': 0, 'data': self.get_serializer(report).data, 'msg': '凭证审核通过'})
        lifecycle_provider = None
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            lifecycle_provider = ClubAccount.objects.filter(pk=initial.provider_account_id,
                account_type=ClubAccount.AccountType.PROVIDER).first()
            if not lifecycle_provider:
                return Response({'code': 409, 'msg': '报单陪玩账号归属待核对'}, status=409)
            from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
            try:
                ensure_provider_lifecycle(lifecycle_provider)
            except ProviderLifecycleError:
                return Response({'code': 503, 'msg': '报单陪玩账号状态暂不可核验'}, status=503)
        with transaction.atomic():
            if lifecycle_provider:
                from club_accounts.provider_lifecycle import lock_provider_operation
                try:
                    lock_provider_operation(lifecycle_provider)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '报单陪玩账号状态暂不可核验'}, status=503)
            report = ProviderReport.objects.select_for_update().get(pk=pk)
            if report.status != ProviderReport.Status.PENDING:
                return Response({'code': 400, 'msg': '该报单已审核，无法重复操作'})
            if lifecycle_provider and (report.order_id is not None
                    or report.provider_account_id != lifecycle_provider.pk
                    or report.provider_id != initial.provider_id
                    or not ClubAccount.objects.filter(pk=lifecycle_provider.pk,
                        account_type=ClubAccount.AccountType.PROVIDER,
                        business_cycle=lifecycle_provider.business_cycle).exists()):
                return Response({'code': 409, 'msg': '报单陪玩归属或账号周期已变化，请刷新后核对'}, status=409)

            from wallet.reporting import legacy_report_commission
            rate, source_label = legacy_report_commission(report)

            payout = report.amount * (100 - rate) // 100
            profile = EscortProfile.objects.select_for_update().filter(user_id=report.provider_id).first()

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
                    f'（金额{report.amount / 10:g}{current_currency_name()}，抽成{rate}%，{source_label}）'
                ),
            )

            report.status = ProviderReport.Status.APPROVED
            from wallet.club_rules import order_hold_hours
            from wallet.income import hold_provider_income
            hold_provider_income(tx, profile=profile, hold_hours=order_hold_hours())
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
            transaction.on_commit(lambda: notify_report_update(report))

        create_message(
            recipient_id=report.provider_id,
            recipient_account_id=report.provider_account_id,
            title='报单审核通过',
            preview=f'{report.game_name} 报单已通过，入账 {payout / 10:g} {current_currency_name()}',
            detail=(
                f'报单金额 {report.amount / 10:g} {current_currency_name()}，'
                f'平台抽成 {rate}%（{source_label}），实际入账 {payout / 10:g} {current_currency_name()}。'
            ),
            action_url='/pages/report/index',
        )
        return Response({'code': 0, 'data': self.get_serializer(report).data, 'msg': '已通过并入账'})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        audit_remark = (request.data.get('audit_remark') or '').strip()[:255]
        if not audit_remark:
            return Response({'code': 400, 'msg': '请填写驳回原因'})
        initial = self.get_object()
        lifecycle_provider = None
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            lifecycle_provider = ClubAccount.objects.filter(pk=initial.provider_account_id,
                account_type=ClubAccount.AccountType.PROVIDER).first()
            if lifecycle_provider is None:
                return Response({'code': 409, 'msg': '报单陪玩账号归属待核对'}, status=409)
            from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
            try:
                ensure_provider_lifecycle(lifecycle_provider)
            except ProviderLifecycleError:
                return Response({'code': 503, 'msg': '报单陪玩账号状态暂不可核验'}, status=503)
        with transaction.atomic():
            if lifecycle_provider:
                from club_accounts.provider_lifecycle import lock_provider_operation
                try:
                    lock_provider_operation(lifecycle_provider)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '报单陪玩账号状态暂不可核验'}, status=503)
            if initial.order_id:
                report_order = Order.objects.select_for_update().get(pk=initial.order_id)
                if lifecycle_provider:
                    from wallet.report_lifecycle import report_order_cycle_matches
                    if not report_order_cycle_matches(report_order, lifecycle_provider):
                        return Response({'code': 409, 'msg': '订单陪玩来源周期待核对'}, status=409)
            report = ProviderReport.objects.select_for_update().get(pk=pk)
            if lifecycle_provider and (report.order_id != initial.order_id
                    or report.provider_account_id != lifecycle_provider.pk
                    or report.provider_id != initial.provider_id
                    or not ClubAccount.objects.filter(pk=lifecycle_provider.pk,
                        account_type=ClubAccount.AccountType.PROVIDER,
                        business_cycle=lifecycle_provider.business_cycle).exists()):
                return Response({'code': 409, 'msg': '报单陪玩归属或账号周期已变化，请刷新后核对'}, status=409)
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
            if report.order_id:
                from engagement.confirmation import refresh_report_clock
                refresh_report_clock(report.order)
                from site_messages.order_cycle import recipient_cycle
                report_cycle = recipient_cycle(report.order, report.provider_account_id)
                from site_messages.report_approval_notice import enabled as report_notice_enabled, record as record_report_notice
                if report_notice_enabled():
                    record_report_notice(report, report.order, report_cycle,
                        f'{report.game_name} 报单被驳回', kind='REJECTED', detail=f'驳回原因：{audit_remark}')
                else:
                    from orders.services import push_message_safe
                    transaction.on_commit(lambda: push_message_safe(
                        recipient_id=report.provider_id, recipient_account_id=report.provider_account_id,
                        recipient_business_cycle=report_cycle, require_cycle=True,
                        title='报单审核未通过', preview=f'{report.game_name} 报单被驳回',
                        detail=f'驳回原因：{audit_remark}', msg_type='ORDER',
                        related_order_id=report.order_id, action_url='/pages/report/index',
                    ))
            transaction.on_commit(lambda: notify_report_update(report))

        if not report.order_id:
            create_message(
                recipient_id=report.provider_id,
                recipient_account_id=report.provider_account_id,
                title='报单审核未通过',
                preview=f'{report.game_name} 报单被驳回',
                detail=f'驳回原因：{audit_remark}',
                msg_type='SYSTEM',
                action_url='/pages/report/index',
            )
        return Response({'code': 0, 'data': self.get_serializer(report).data, 'msg': '已驳回'})


class CommissionConfigView(APIView):
    """平台抽成率配置：GET 读取，PUT 修改（需 report:audit）。"""

    permission_classes = [IsConsoleUser]

    def put(self, request):
        if 'report:audit' not in get_user_permissions(request.account):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        from orders.commission import integer, global_config, TYPE_KEY, FIXED_KEY
        from orders.pricing import PricingError
        mode = request.data.get('commission_type','PERCENT')
        try:
            if mode not in ('PERCENT','FIXED'):
                raise PricingError('请选择百分比或整单固定金额')
            fixed = integer(request.data.get('commission_fixed'),'整单固定抽成金额') if mode == 'FIXED' else 0
            rate_value = request.data.get('rate',request.data.get('commission_rate'))
            rate = integer(rate_value,'抽成率',100) if mode == 'PERCENT' or rate_value is not None else None
        except PricingError as exc:
            return Response({'code':400,'msg':str(exc)})
        with transaction.atomic():
            SystemConfig.objects.update_or_create(key=TYPE_KEY,defaults={'value':mode,'remark':'订单抽成方式；自动策略除外'})
            SystemConfig.objects.update_or_create(key=FIXED_KEY,defaults={'value':str(fixed),'remark':'整单固定抽成；内部账务单位'})
            # Standalone historical/manual reports still use this percentage.
            if rate is not None:
                SystemConfig.objects.update_or_create(key=COMMISSION_RATE_KEY,defaults={'value':str(rate),'remark':'默认百分比抽成(%)'})
        data = global_config();data['rate'] = data['commission_rate']
        return Response({'code':0,'data':data,'msg':'订单抽成配置已更新'})

    def get(self, request):
        if not {'report:view', 'report:audit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        from orders.commission import global_config
        data = global_config();data['rate'] = data['commission_rate']
        return Response({'code': 0, 'data': data})


# ---------------- 提现申请 ----------------
def _withdraw_claim_is_current(withdraw, expected_claimed_at):
    """Bind a review to the claim the operator actually inspected."""
    from django.utils.dateparse import parse_datetime
    if not isinstance(expected_claimed_at, str):
        return False
    try:
        expected = parse_datetime(expected_claimed_at)
    except (TypeError, ValueError):
        return False
    return bool(expected and timezone.is_aware(expected) and withdraw.claimed_at == expected)


class WithdrawRequestViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminWithdrawSerializer
    default_perm = 'withdraw:view'
    required_perms = {
        'claim': 'withdraw:audit',
        'release_claim': ['withdraw:view', 'withdraw:release'],
        'approve': 'withdraw:audit',
        'reject': 'withdraw:audit',
    }

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        claim_scope = request.query_params.get('claim_scope', '')
        if claim_scope:
            response.data['data']['claim_scope_applied'] = claim_scope
        if request.query_params.get('status', '').upper() == 'PENDING':
            response.data['data']['pending_order'] = 'oldest_first'
        return response

    def get_queryset(self):
        qs = WithdrawRequest.objects.select_related('user', 'account', 'auditor', 'auditor_account', 'claimed_by_account').order_by('-created_at')
        # Queue filters only apply to listing; a stale list URL must never
        # hide the record during a claim or review action.
        if self.action == 'list':
            claim_scope = self.request.query_params.get('claim_scope', '')
            if claim_scope:
                qs = qs.filter(workflow_version=2)
            if claim_scope == 'unclaimed':
                qs = qs.filter(claimed_by_account__isnull=True)
            elif claim_scope == 'mine':
                qs = qs.filter(claimed_by_account=self.request.account)
            elif claim_scope == 'other':
                qs = qs.filter(claimed_by_account__isnull=False).exclude(claimed_by_account=self.request.account)
            elif claim_scope:
                raise rest_serializers.ValidationError({'claim_scope': '领取范围无效'})
        def provider_match(field, value):
            return Q(**{f'account__{field}__icontains': value}) | Q(account__isnull=True, **{f'user__{field}__icontains': value})
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
            if self.action == 'list' and status_filter.upper() == 'PENDING':
                qs = qs.order_by('created_at', 'id')
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                provider_match('username', keyword)
                | provider_match('nickname', keyword)
                | Q(payee_name__icontains=keyword)
                | Q(payee_account__icontains=keyword)
            )
        provider_username = self.request.query_params.get('provider_username')
        if provider_username:
            qs = qs.filter(provider_match('username', provider_username))
        provider_nickname = self.request.query_params.get('provider_nickname')
        if provider_nickname:
            qs = qs.filter(provider_match('nickname', provider_nickname))
        payee_name = self.request.query_params.get('payee_name')
        if payee_name:
            qs = qs.filter(payee_name__icontains=payee_name)
        payee_account = self.request.query_params.get('payee_account')
        if payee_account:
            qs = qs.filter(payee_account__icontains=payee_account)
        return qs

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        # Conditional UPDATE is atomic across workers and also works in SQLite;
        # a retry by the same claimant is idempotent.
        with transaction.atomic():
            updated = WithdrawRequest.objects.filter(
                pk=pk, status=WithdrawRequest.Status.PENDING, workflow_version=2, claimed_by_account__isnull=True,
            ).update(claimed_by_account=request.account, claimed_at=timezone.now())
            # List query parameters must not hide a claim that was just
            # written and turn a successful mutation into a false 404.
            withdraw = WithdrawRequest.objects.select_related(
                'user', 'account', 'auditor', 'auditor_account', 'claimed_by_account',
            ).filter(pk=pk).first()
            if not withdraw:
                return Response({'code': 404, 'msg': '提现申请不存在'}, status=404)
            if withdraw.workflow_version != 2:
                return Response({'code': 409, 'msg': '历史提现须先人工核实付款状态'}, status=409)
            if withdraw.status != WithdrawRequest.Status.PENDING:
                return Response({'code': 409, 'msg': '该提现已审核，请刷新列表'}, status=409)
            if withdraw.claimed_by_account_id != request.account.pk:
                return Response({'code': 409, 'msg': '该申请已由其他客服领取，请刷新列表'}, status=409)
            if updated:
                from wallet.notifier import notify_withdraw_update
                transaction.on_commit(lambda: notify_withdraw_update(withdraw))
        return Response({'code': 0, 'data': self.get_serializer(withdraw).data, 'msg': '已领取'})

    @action(detail=True, methods=['post'], url_path='release-claim')
    def release_claim(self, request, pk=None):
        from django.utils.dateparse import parse_datetime

        reason = request.data.get('reason')
        expected_account = request.data.get('expected_claimed_by_account')
        expected_time = request.data.get('expected_claimed_at')
        if request.data.get('confirmed_unpaid') is not True:
            return Response({'code': 400, 'msg': '请确认已核实该申请尚未打款'}, status=400)
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
            return Response({'code': 400, 'msg': '请填写 255 字以内的释放原因'}, status=400)
        if type(expected_account) is not int or expected_account < 1 or not isinstance(expected_time, str):
            return Response({'code': 400, 'msg': '领取信息已变化，请刷新后重新核实'}, status=400)
        try:
            expected_time_value = parse_datetime(expected_time)
        except (TypeError, ValueError):
            expected_time_value = None
        if not expected_time_value or timezone.is_naive(expected_time_value):
            return Response({'code': 400, 'msg': '领取时间无效，请刷新后重新核实'}, status=400)

        with transaction.atomic():
            withdraw = WithdrawRequest.objects.select_for_update().filter(pk=pk).first()
            if not withdraw:
                return Response({'code': 404, 'msg': '提现申请不存在'}, status=404)
            if withdraw.workflow_version != 2:
                return Response({'code': 409, 'msg': '历史提现须先人工核实付款状态'}, status=409)
            if (withdraw.status != WithdrawRequest.Status.PENDING or not withdraw.claimed_by_account_id
                    or withdraw.claimed_by_account_id != expected_account or withdraw.claimed_at != expected_time_value):
                return Response({'code': 409, 'msg': '申请状态或领取人已变化，请刷新后重新核实'}, status=409)

            old_claimed_at = withdraw.claimed_at.isoformat()
            withdraw.claimed_by_account = None
            withdraw.claimed_at = None
            withdraw.save(update_fields=['claimed_by_account', 'claimed_at'])
            AdminAuditLog.objects.create(
                operator=request.legacy_user,
                operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150],
                method='POST', path=request.path[:255], resource='withdrawals', object_id=str(withdraw.pk),
                request_body={'reason': reason.strip(), 'confirmed_unpaid': True,
                              'previous_claimed_by_account': expected_account, 'previous_claimed_at': old_claimed_at},
                status_code=200,
                ip=request.META.get('REMOTE_ADDR') or None,
            )
            from wallet.notifier import notify_withdraw_update
            transaction.on_commit(lambda: notify_withdraw_update(withdraw))
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': self.get_serializer(withdraw).data, 'msg': '已释放，可由其他客服领取'})

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        from .withdraw_initial import store_approve
        return store_approve(self, request, pk)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        audit_remark = (request.data.get('audit_remark') or '').strip()[:255]
        if not audit_remark:
            return Response({'code': 400, 'msg': '请填写驳回原因'})
        with transaction.atomic():
            provider = None
            if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operation
                provider_id = WithdrawRequest.objects.filter(pk=pk).values_list('account_id', flat=True).first()
                provider = ClubAccount.objects.filter(pk=provider_id, account_type='PROVIDER').first()
                if provider is None:
                    return Response({'code': 409, 'msg': '提现所属陪玩待核对'}, status=409)
                try:
                    lock_provider_operation(provider)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系平台'}, status=503)
            withdraw = WithdrawRequest.objects.select_for_update().get(pk=pk)
            if provider is not None and withdraw.account_id != provider.pk:
                return Response({'code': 409, 'msg': '提现所属陪玩已变化，请重新核对'}, status=409)
            if withdraw.workflow_version != 2:
                return Response({'code': 409, 'msg': '历史提现须先人工核实付款状态，不能直接驳回'}, status=409)
            if (withdraw.status == WithdrawRequest.Status.REJECTED
                    and withdraw.auditor_account_id == request.account.pk
                    and withdraw.claimed_by_account_id == request.account.pk
                    and withdraw.audit_remark == audit_remark
                    and _withdraw_claim_is_current(withdraw, request.data.get('expected_claimed_at'))):
                return Response({'code': 0, 'data': self.get_serializer(withdraw).data, 'msg': '提现已驳回'})
            from wallet.withdraw_lifecycle import matches_cycle
            if not matches_cycle(withdraw):
                return Response({'code': 409, 'msg': '提现申请账号周期待核对'}, status=409)
            if withdraw.status != WithdrawRequest.Status.PENDING:
                return Response({'code': 400, 'msg': '该提现已审核，无法重复操作'})
            if withdraw.claimed_by_account_id != request.account.pk:
                return Response({'code': 409, 'msg': '请先领取该申请；已由其他客服领取的申请不能审核'}, status=409)
            if not _withdraw_claim_is_current(withdraw, request.data.get('expected_claimed_at')):
                return Response({'code': 409, 'msg': '领取状态已变化，请刷新后重新核对打款和申请信息'}, status=409)

            wallet = get_wallet(
                user_id=withdraw.user_id,
                account_id=withdraw.account_id,
                for_update=True,
                create=False,
            )
            if wallet is None:
                return Response({'code': 400, 'msg': '申请人钱包不存在，无法驳回'})
            if (wallet.frozen_amount < withdraw.amount or wallet.balance + withdraw.amount > 2147483647
                    or not withdraw.transaction_id or not Transaction.objects.filter(
                        pk=withdraw.transaction_id, wallet=wallet, tx_type=Transaction.TxType.WITHDRAW,
                        status=Transaction.Status.PENDING, amount=-withdraw.amount).exists()):
                return Response({'code': 400, 'msg': '冻结金额异常，无法驳回'})
            from wallet.withdraw_release import release_locked
            try:
                release_locked(withdraw, wallet, actor_kind='STORE', actor_account=request.account,
                               reason=audit_remark)
            except ValueError as exc:
                transaction.set_rollback(True)
                return Response({'code': 409, 'msg': str(exc)}, status=409)

            withdraw.status = WithdrawRequest.Status.REJECTED
            withdraw.audit_remark = audit_remark
            withdraw.auditor = request.legacy_user
            withdraw.auditor_account = request.account
            withdraw.audited_at = timezone.now()
            withdraw.save(update_fields=[
                'status', 'audit_remark', 'auditor', 'auditor_account', 'audited_at',
            ])
            from wallet.withdraw_notifications import withdrawal_message
            withdrawal_message(withdraw, 'store_rejected', reason=audit_remark)
            from wallet.notifier import notify_wallet_update, notify_withdraw_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
            transaction.on_commit(lambda: notify_withdraw_update(withdraw))

        return Response({'code': 0, 'data': self.get_serializer(withdraw).data, 'msg': '已驳回'})


class WithdrawConfigView(APIView):
    """提现规则各门店独立保存，编辑与提交共用配置锁。"""

    permission_classes = [IsConsoleUser]

    @transaction.atomic
    def put(self, request):
        if 'withdraw:config' not in get_user_permissions(request.account):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        from wallet.withdraw_rules import WithdrawConfigChanged, save_withdraw_rules
        def whole(value, maximum):
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError()
            raw = str(value).strip()
            if not raw.isascii() or not raw.isdigit() or len(raw) > 10 or int(raw) > maximum:
                raise ValueError()
            return int(raw)
        try:
            min_amount = whole(request.data.get('min_amount'), 2147483647)
            tax_rate = whole(request.data.get('tax_rate'), 100)
            threshold = whole(request.data['balance_threshold'], 2147483647) if 'balance_threshold' in request.data else None
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请填写有效的最低金额、余额门槛和 0–100 的整数费率'})
        version = request.data.get('expected_version')
        if version is not None and (not isinstance(version, str) or len(version) != 64):
            return Response({'code': 400, 'msg': '配置版本无效，请刷新'})
        try:
            current = save_withdraw_rules(min_amount, tax_rate, threshold, version=version)
        except WithdrawConfigChanged as exc:
            return Response({'code': 409, 'errorCode': 'WITHDRAW_CONFIG_CHANGED', 'msg': str(exc)}, status=409)
        return Response({
            'code': 0,
            'data': current,
            'msg': '提现配置已更新',
        })

    def get(self, request):
        if not {'withdraw:view', 'withdraw:audit', 'withdraw:config'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        from wallet.withdraw_rules import withdraw_rule_snapshot
        return Response({'code': 0, 'data': withdraw_rule_snapshot()})


# ---------------- 在线客服 ----------------
class ChatSessionViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    """客服工作台：会话列表 / 会话内消息 / 回复 / 标记已读（共享会话池）。"""

    serializer_class = AdminChatSessionSerializer
    default_perm = 'chat:view'
    required_perms = {
        'reply': 'chat:reply',
        'read': 'chat:reply',
        'message_status': 'chat:reply',
        'claim': 'chat:reply',
        'release': 'chat:view',
        'transfer': 'chat:view',
        'receivers': 'chat:view',
        'assignment_history': 'chat:view',
    }

    def get_queryset(self):
        qs = ChatSession.objects.select_related('account', 'user', 'claimed_by').order_by('-last_message_at', '-created_at')
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(account__username__icontains=keyword)
                | Q(account__nickname__icontains=keyword)
                | Q(account__account_no__icontains=keyword)
                | Q(user__username__icontains=keyword)
                | Q(user__nickname__icontains=keyword)
            )
        only_unread = self.request.query_params.get('only_unread')
        if only_unread in ('1', 'true', 'True'):
            qs = qs.filter(unread_support__gt=0)
        return qs

    @staticmethod
    def _require_content(request, session):
        from chat.authorization import can_read_session
        from rest_framework.exceptions import PermissionDenied
        if not can_read_session(request.account, session):
            raise PermissionDenied('请先领取会话，或联系主管分配')

    @action(detail=False, methods=['get'])
    def receivers(self, request):
        from console.permissions import get_account_permissions, is_console_account
        accounts = ClubAccount.objects.filter(account_type=ClubAccount.AccountType.STAFF, is_active=True,
            can_login=True, admin_membership__is_active=True).select_related('admin_membership').prefetch_related('admin_membership__roles')
        data = [{'id': item.pk, 'name': item.nickname or item.username} for item in accounts
                if is_console_account(item) and {'chat:view', 'chat:reply'} <= get_account_permissions(item)]
        return Response({'code': 0, 'data': data})

    def _change_assignment(self, request, pk, action_name):
        from chat.models import ChatAssignmentEvent
        from console.permissions import get_account_permissions, is_console_account
        from rest_framework.exceptions import PermissionDenied
        permissions = get_account_permissions(request.account)
        if 'chat:view' not in permissions:
            raise PermissionDenied('缺少查看会话权限')
        expected = request.data.get('expected_version')
        if type(expected) is not int or expected < 0:
            return Response({'code': 400, 'msg': '请刷新会话后重试'}, status=400)
        reason = request.data.get('reason', '')
        if not isinstance(reason, str) or len(reason.strip()) > 500:
            return Response({'code': 400, 'msg': '接待调整原因不能超过 500 字'}, status=400)
        reason = reason.strip()
        with transaction.atomic():
            from chat.sessions import lock_owner_snapshot
            owner_snapshot = lock_owner_snapshot(pk)
            session = get_object_or_404(ChatSession.objects.select_for_update(), pk=pk)
            if owner_snapshot is not None and session.account_id != owner_snapshot[0]:
                return Response({'code': 409, 'msg': '会话归属已变化，请刷新后重试'}, status=409)
            if session.claim_version != expected:
                return Response({'code': 409, 'msg': '会话接待状态已变化，请刷新后重试'}, status=409)
            supervisor = 'chat:supervise' in permissions
            previous_id = session.claimed_by_id
            # PRD 15: a supervisor changing another agent's active reception
            # records the reason together with the before/after assignees.
            if (supervisor and previous_id is not None and previous_id != request.account.pk
                    and action_name in ('release', 'transfer') and not reason):
                return Response({'code': 400, 'msg': '请填写主管调整接待的原因'}, status=400)
            if action_name == 'claim':
                if previous_id is not None:
                    return Response({'code': 409, 'msg': '会话已被领取'}, status=409)
                target = request.account
            else:
                if not supervisor and (previous_id != request.account.pk or 'chat:reply' not in permissions):
                    raise PermissionDenied('只有接待客服或主管可以调整')
                if action_name == 'release':
                    if previous_id is None:
                        return Response({'code': 409, 'msg': '会话已在共享池中'}, status=409)
                    target = None
                else:
                    target_id = request.data.get('account_id')
                    if type(target_id) is not int or target_id <= 0:
                        return Response({'code': 400, 'msg': '请选择接待客服'}, status=400)
                    target = ClubAccount.objects.filter(pk=target_id).first()
                    if not target or not is_console_account(target) or not {'chat:view', 'chat:reply'} <= get_account_permissions(target):
                        return Response({'code': 400, 'msg': '所选客服当前不可接待'}, status=400)
                    if target.pk == previous_id:
                        return Response({'code': 409, 'msg': '该客服已在接待'}, status=409)
            session.claimed_by = target
            session.claimed_at = timezone.now() if target else None
            session.claim_version += 1
            from chat.notifier import stage_chat_session_update
            intent_key = stage_chat_session_update(session, owner_snapshot=owner_snapshot)
            fields = ['claimed_by', 'claimed_at', 'claim_version', 'updated_at']
            if intent_key:
                fields.append('refresh_notification_intents')
            session.save(update_fields=fields)
            assignment_event = ChatAssignmentEvent.objects.create(session=session, actor=request.account, previous_id=previous_id,
                current=target, action=action_name.upper(), version=session.claim_version, reason=reason,
                acting_as_supervisor=supervisor and action_name != 'claim' and previous_id != request.account.pk)
            if action_name == 'transfer':
                from chat.transfer_notices import record as record_transfer_notice
                record_transfer_notice(assignment_event)
            schedule_chat_session_update(session, owner_snapshot=owner_snapshot, intent_key=intent_key)
        return Response({'code': 0, 'data': self.get_serializer(session).data})

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        return self._change_assignment(request, pk, 'claim')

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        return self._change_assignment(request, pk, 'release')

    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        return self._change_assignment(request, pk, 'transfer')

    @action(detail=True, methods=['get'], url_path='assignment-history')
    def assignment_history(self, request, pk=None):
        from chat.models import ChatAssignmentEvent
        from console.permissions import get_account_permissions
        from rest_framework.exceptions import PermissionDenied
        if 'chat:supervise' not in get_account_permissions(request.account):
            raise PermissionDenied('只有主管可以查看接待流转记录')
        session = get_object_or_404(ChatSession, pk=pk)
        before = request.query_params.get('before_version')
        events = ChatAssignmentEvent.objects.filter(session=session).select_related('actor', 'previous', 'current').order_by('-version')
        if before is not None:
            if not before.isascii() or not before.isdigit() or len(before) > 19 or int(before) < 1:
                return Response({'code': 400, 'msg': '接待记录位置无效'}, status=400)
            events = events.filter(version__lt=int(before))
        rows = list(events[:51])

        def account_data(account):
            return {'id': account.pk, 'name': account.nickname or account.username} if account else None

        return Response({'code': 0, 'data': {'list': [
            {'version': event.version, 'action': event.action, 'actor': account_data(event.actor),
             'previous': account_data(event.previous), 'current': account_data(event.current),
             'reason': event.reason, 'acting_as_supervisor': event.acting_as_supervisor, 'created_at': event.created_at}
            for event in rows[:50]], 'has_more': len(rows) > 50,
            'next_before_version': rows[49].version if len(rows) > 50 else None}})

    @action(detail=True, methods=['get'])
    @transaction.atomic
    def messages(self, request, pk=None):
        session = get_object_or_404(ChatSession.objects.select_for_update(), pk=pk)
        self._require_content(request, session)
        messages = session.messages.select_related('sender', 'sender_account')
        if request.query_params.get('cursor') == '1':
            from chat.views import MESSAGE_PAGE_SIZE
            total = messages.count()
            before = request.query_params.get('before_id')
            if before is not None:
                if not before.isascii() or not before.isdigit() or len(before) > 18 or int(before) < 1:
                    return Response({'code': 400, 'msg': '聊天记录位置无效'}, status=400)
                anchor = session.messages.filter(pk=int(before)).only('id', 'created_at').first()
                if anchor is None:
                    return Response({'code': 400, 'msg': '聊天记录位置已变化，请重新加载'}, status=400)
                messages = messages.filter(Q(created_at__lt=anchor.created_at) | Q(created_at=anchor.created_at, pk__lt=anchor.pk))
            rows = list(messages.order_by('-created_at', '-id')[:MESSAGE_PAGE_SIZE + 1])
            has_more = len(rows) > MESSAGE_PAGE_SIZE
            rows = list(reversed(rows[:MESSAGE_PAGE_SIZE]))
            data = AdminChatMessageSerializer(rows, many=True, context={'request': request}).data
            return Response({'code': 0, 'data': {'list': data, 'total': total, 'has_more': has_more,
                                                'next_before_id': rows[0].pk if has_more else None}})
        messages = messages.order_by('created_at', 'id')
        data = AdminChatMessageSerializer(
            messages, many=True, context={'request': request}
        ).data
        return Response({'code': 0, 'data': {'list': data, 'total': len(data)}})

    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        serializer = AdminChatReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data['content']
        image = serializer.validated_data.get('image')
        read_through_id = serializer.validated_data.get('read_through_id')
        client_id = serializer.validated_data.get('client_message_id')

        content_type = (
            ChatMessage.ContentType.IMAGE if image else ChatMessage.ContentType.TEXT
        )
        fingerprint = ''
        if client_id:
            import hashlib
            digest = hashlib.sha256()
            digest.update(content_type.encode('utf-8') + b'\x00' + content.encode('utf-8') + b'\x00')
            if image:
                for chunk in image.chunks():
                    digest.update(chunk)
                image.seek(0)
            fingerprint = digest.hexdigest()
        with transaction.atomic():
            from chat.sessions import lock_owner_snapshot
            owner_snapshot = lock_owner_snapshot(pk)
            session = get_object_or_404(ChatSession.objects.select_for_update(), pk=pk)
            if owner_snapshot is not None and session.account_id != owner_snapshot[0]:
                return Response({'code': 409, 'msg': '会话归属已变化，请刷新后重试'}, status=409)
            self._require_content(request, session)
            if read_through_id is not None:
                from chat.read_state import validate_read_through
                validate_read_through(session, read_through_id)
            if client_id:
                existing = session.messages.filter(sender_account=request.account, client_message_id=client_id).first()
                if existing:
                    if not existing.is_from_support or existing.message_fingerprint != fingerprint:
                        return Response({'code': 409, 'msg': '消息内容已变化，请重新发送'}, status=409)
                    return Response({'code': 0, 'data': AdminChatMessageSerializer(existing, context={'request': request}).data, 'msg': '已发送'})
            message = ChatMessage.objects.create(
                session=session,
                sender=request.legacy_user,
                sender_account=request.account,
                is_from_support=True,
                content_type=content_type,
                content=content,
                image=image,
                client_message_id=client_id,
                message_fingerprint=fingerprint,
                notification_intent=getattr(settings, 'CHAT_NOTIFICATION_OUTBOX_ENABLED', False),
                notification_recipient_id=owner_snapshot[0] if owner_snapshot else None,
                notification_recipient_cycle=owner_snapshot[1] if owner_snapshot else None,
            )
            session.last_message = message.preview
            session.last_message_at = message.created_at
            session.unread_user += 1
            from chat.read_state import mark_read_through
            if read_through_id is not None:
                session.unread_support = mark_read_through(session, from_support=False, through_id=read_through_id)
            session.save(update_fields=[
                'last_message', 'last_message_at', 'unread_user', 'unread_support', 'updated_at',
            ])
            schedule_chat_message(session, message, owner_snapshot=owner_snapshot)
        return Response({
            'code': 0,
            'data': AdminChatMessageSerializer(message, context={'request': request}).data,
            'msg': '已发送',
        })

    @action(detail=True, methods=['get'], url_path='message-status')
    @transaction.atomic
    def message_status(self, request, pk=None):
        from chat.views import CLIENT_MESSAGE_ID
        client_id = request.query_params.get('client_message_id')
        if not isinstance(client_id, str) or not CLIENT_MESSAGE_ID.fullmatch(client_id):
            return Response({'code': 400, 'msg': '消息编号无效'}, status=400)
        session = get_object_or_404(ChatSession.objects.select_for_update(), pk=pk)
        self._require_content(request, session)
        message = session.messages.filter(sender_account=request.account, is_from_support=True, client_message_id=client_id).first()
        return Response({'code': 0, 'data': {'status': 'FOUND' if message else 'UNKNOWN',
            'message': AdminChatMessageSerializer(message, context={'request': request}).data if message else None}})

    @action(detail=True, methods=['post'])
    def read(self, request, pk=None):
        from chat.read_state import mark_read_through
        data = AdminChatReadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            from chat.sessions import lock_owner_snapshot
            owner_snapshot = lock_owner_snapshot(pk)
            session = get_object_or_404(ChatSession.objects.select_for_update(), pk=pk)
            if owner_snapshot is not None and session.account_id != owner_snapshot[0]:
                return Response({'code': 409, 'msg': '会话归属已变化，请刷新后重试'}, status=409)
            self._require_content(request, session)
            if 'message_ids' in data.validated_data:
                unread = session.messages.filter(is_from_support=False, is_read=False)
                unread.filter(pk__in=data.validated_data['message_ids']).update(is_read=True)
                session.unread_support = unread.count()
            else:
                session.unread_support = mark_read_through(session, from_support=False, through_id=data.validated_data.get('through_id'))
            from chat.notifier import stage_chat_session_update
            intent_key = stage_chat_session_update(session, owner_snapshot=owner_snapshot)
            fields = ['unread_support', 'updated_at']
            if intent_key:
                fields.append('refresh_notification_intents')
            session.save(update_fields=fields)
            schedule_chat_session_update(session, owner_snapshot=owner_snapshot, intent_key=intent_key)
        return Response({'code': 0, 'data': {'unread_support': session.unread_support}})


# ---------------- 优惠券 ----------------
class CouponViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminCouponSerializer
    default_perm = 'coupon:view'
    required_perms = {
        'create': 'coupon:edit',
        'update': 'coupon:edit',
        'partial_update': 'coupon:edit',
        'destroy': 'coupon:delete',
        'service_options': 'coupon:edit',
    }

    def get_queryset(self):
        qs = Coupon.objects.all().order_by('sort_order', '-created_at')
        if self.action in ('update', 'partial_update', 'destroy'):
            qs = qs.select_for_update()
        is_active = self.request.query_params.get('is_active')
        if is_active is not None and is_active != '':
            qs = qs.filter(is_active=_truthy(is_active))
        return qs

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        if serializer.validated_data.get('is_active', True) and serializer.validated_data.get('is_public', True):
            from stores.license_benefits import require_promotion_configuration
            require_promotion_configuration()
        from coupons.notifier import notify_coupon_catalog_changed, notify_coupon_claim_changed
        coupon = serializer.save()
        transaction.on_commit(notify_coupon_catalog_changed)
        transaction.on_commit(lambda: notify_coupon_claim_changed(coupon.pk))

    def perform_update(self, serializer):
        if (serializer.validated_data.get('is_active', serializer.instance.is_active)
                and serializer.validated_data.get('is_public', serializer.instance.is_public)):
            from stores.license_benefits import require_promotion_configuration
            require_promotion_configuration()
        from coupons.notifier import notify_coupon_catalog_changed, notify_coupon_claim_changed
        from rest_framework.exceptions import ValidationError
        protected = {'discount_type', 'threshold', 'amount', 'discount_rate', 'scope', 'scope_variant',
                     'service_ids', 'limit_units', 'valid_to', 'max_discount', 'experience_kind',
                     'platform', 'is_active', 'is_public'}
        if (any(key in serializer.validated_data and serializer.validated_data[key] != getattr(serializer.instance, key)
                for key in protected)
                and serializer.instance.user_coupons.filter(vip_terms_locked_by_downgrade=True).exists()):
            raise ValidationError('该券需保留已发条款，不能修改适用范围、折扣或有效状态')
        coupon = serializer.save()
        transaction.on_commit(notify_coupon_catalog_changed)
        transaction.on_commit(lambda: notify_coupon_claim_changed(coupon.pk))

    @action(detail=False, methods=['get'], url_path='service-options')
    def service_options(self, request):
        from orders.catalog import visible_service_q
        services = ServiceItem.objects.filter(visible_service_q(), is_addon=False, purchase_mode='DIRECT').exclude(service_category__is_gift=True).select_related('game_category')
        return Response({'code': 0, 'data': [{'id': row.pk, 'name': ' · '.join(filter(None, [
            row.game_category.name if row.game_category_id else '', row.name,
            row.get_platform_display(), row.get_security_level_display(),
        ]))} for row in services.order_by('sort_order', 'id')]})

    def perform_destroy(self, instance):
        if instance.user_coupons.exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError('该优惠券已被领取，请停用，保留领取与核销记录')
        coupon_id = instance.pk
        instance.delete()
        from coupons.notifier import notify_coupon_catalog_changed, notify_coupon_claim_changed
        transaction.on_commit(notify_coupon_catalog_changed)
        transaction.on_commit(lambda: notify_coupon_claim_changed(coupon_id))


class UserCouponViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminUserCouponSerializer
    default_perm = 'coupon:view'

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data['data']['claim_status_version'] = 1
        return response

    def get_queryset(self):
        now = timezone.now()
        expired = Q(expires_at__lte=now) | Q(expires_at__isnull=True, coupon__valid_to__lte=now)
        previous_cycle = Q(account__isnull=False) & ~Q(business_cycle=F('account__business_cycle'))
        qs = UserCoupon.objects.select_related('user', 'account', 'coupon', 'order').annotate(
            admin_status=Case(
                When(status=UserCoupon.Status.USED, then=Value('USED')),
                When(status=UserCoupon.Status.EXPIRED, then=Value('EXPIRED')),
                When(Q(status=UserCoupon.Status.UNUSED) & expired, then=Value('EXPIRED')),
                When(status=UserCoupon.Status.UNUSED, coupon__is_active=False, then=Value('DISABLED')),
                When(Q(status=UserCoupon.Status.UNUSED) & previous_cycle, then=Value('PREVIOUS_CYCLE')),
                default=Value('UNUSED'), output_field=CharField(),
            )
        ).order_by('-claimed_at', '-id')
        status_filter = (self.request.query_params.get('status') or '').strip().upper()
        if status_filter:
            if status_filter not in ('UNUSED', 'USED', 'EXPIRED', 'DISABLED', 'PREVIOUS_CYCLE'):
                raise rest_serializers.ValidationError({'status': '无效的领取状态'})
            qs = qs.filter(admin_status=status_filter)
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
    queryset = Banner.objects.all()
    serializer_class = AdminBannerSerializer
    default_perm = 'banner:view'
    required_perms = {
        'create': 'banner:edit',
        'update': 'banner:edit',
        'partial_update': 'banner:edit',
        'destroy': 'banner:delete',
    }

    def get_queryset(self):
        return self.queryset.all()

    def _content_admission(self):
        from stores.license_benefits import check_new_benefits
        outcome, _ = check_new_benefits('CONTENT_OPERATIONS')
        if outcome == 'UNKNOWN':
            return Response({'code': 503, 'msg': '暂时无法核验门店授权，请联系平台后重试'}, status=503)
        if outcome != 'ALLOW':
            return Response({'code': 403, 'msg': '门店授权暂不支持轮播配置，请联系平台'}, status=403)
        return None

    def create(self, request, *args, **kwargs):
        denied = self._content_admission()
        if denied is not None:
            return denied
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        # Validation, including image processing, may take time; use the
        # current tenant license again at the actual write boundary.
        from stores.license_benefits import require_content_operations
        require_content_operations()
        serializer.save()

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        # Inspect the locked record through the serializer so an expired
        # license cannot accidentally prevent taking a banner offline.
        self.queryset = self.get_queryset().select_for_update()
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        from stores.license_benefits import content_deactivation_only, require_content_operations
        if not content_deactivation_only(serializer.instance, serializer.validated_data):
            require_content_operations()
        serializer.save()

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

    def perform_create(self, serializer):
        from stores.license_benefits import require_achievement_configuration
        require_achievement_configuration(account=self.request.account)
        serializer.save()

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        # Serialize edits before deciding whether this is only a safe stop.
        # Existing achievement progress remains available when the tier is
        # downgraded; changing thresholds or re-enabling needs entitlement.
        serializer.instance = Achievement.objects.select_for_update().get(pk=serializer.instance.pk)
        changed = {key for key, value in serializer.validated_data.items()
                   if getattr(serializer.instance, key) != value}
        disabling_only = changed == {'is_active'} and serializer.validated_data['is_active'] is False
        if changed and not disabling_only:
            from stores.license_benefits import require_achievement_configuration
            require_achievement_configuration(account=self.request.account)
        serializer.save()


class CheckinRuleConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'checkin:view', 'checkin:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        rule, _ = CheckinRuleConfig.objects.get_or_create(pk=1)
        return Response({'code': 0, 'data': AdminCheckinRuleSerializer(rule).data})

    @transaction.atomic
    def put(self, request):
        if 'checkin:edit' not in get_user_permissions(request.account):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        rule, _ = CheckinRuleConfig.objects.select_for_update().get_or_create(pk=1)
        serializer = AdminCheckinRuleSerializer(rule, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if any(getattr(rule, key) != value for key, value in serializer.validated_data.items()):
            from stores.license_benefits import require_checkin_configuration
            require_checkin_configuration(account=request.account)
        serializer.save()
        from users.notifier import notify_checkin_config_after_commit
        notify_checkin_config_after_commit()
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
        qs = CheckinGift.objects.all()
        return qs.select_for_update() if getattr(self, '_lock_checkin_gift', False) else qs

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self._lock_checkin_gift = True
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        if serializer.validated_data.get('is_active', True):
            from stores.license_benefits import require_checkin_configuration
            require_checkin_configuration(account=self.request.account)
        serializer.save()
        from users.notifier import notify_checkin_config_after_commit
        notify_checkin_config_after_commit()

    def perform_update(self, serializer):
        current, values = serializer.instance, serializer.validated_data
        changed = {key: value for key, value in values.items() if getattr(current, key) != value}
        disabling_only = changed == {'is_active': False}
        if changed and not disabling_only and (current.is_active or values.get('is_active', current.is_active)):
            from stores.license_benefits import require_checkin_configuration
            require_checkin_configuration(account=self.request.account)
        serializer.save()
        from users.notifier import notify_checkin_config_after_commit
        notify_checkin_config_after_commit()

    def perform_destroy(self, instance):
        instance.delete()
        from users.notifier import notify_checkin_config_after_commit
        notify_checkin_config_after_commit()


class CheckinProgressViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminCheckinProgressSerializer
    default_perm = 'checkin:view'

    def get_queryset(self):
        qs = CheckinMonthProgress.objects.select_related('user', 'account')
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
from .catalog_write_lock import CatalogMutationMixin


class ServiceItemViewSet(CatalogMutationMixin, EnvelopeViewSetMixin, ModelViewSet):
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
            .filter(Q(gameplay__isnull=True) | Q(gameplay__is_archived=False))
            .select_related('game_category', 'service_category')
            .order_by('sort_order', 'id')
        )
        item_id = self.request.query_params.get('item_id', '').strip()
        if item_id:
            if not item_id.isascii() or not item_id.isdigit() or len(item_id) > 18:
                raise rest_serializers.ValidationError('商品编号无效')
            qs = qs.filter(pk=int(item_id))
        keyword = self.request.query_params.get('keyword', '').strip()
        if len(keyword) > 100:
            raise rest_serializers.ValidationError('商品名称搜索不能超过100字')
        if keyword:
            qs = qs.filter(name__icontains=keyword)
        for key in ('gameplay', 'platform', 'security_level', 'provider_kind', 'provider_grade'):
            value = self.request.query_params.get(key)
            if value:
                if key == 'gameplay' and not value.isdigit():
                    raise rest_serializers.ValidationError('玩法参数无效')
                qs = qs.filter(**{key: value})
        game_category = self.request.query_params.get('game_category')
        if game_category:
            qs = qs.filter(game_category_id=game_category)
        service_category = self.request.query_params.get('service_category')
        if service_category:
            qs = qs.filter(service_category_id=service_category)
        return qs


# ---------------- 游戏类目 / 分类 字典 ----------------
class GameCategoryViewSet(CatalogMutationMixin, EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminGameCategorySerializer
    default_perm = 'service:view'
    required_perms = {
        'create': 'service:edit',
        'update': 'service:edit',
        'partial_update': 'service:edit',
        'destroy': 'service:delete',
    }

    def get_queryset(self):
        from orders.catalog import visible_service_q
        qs = GameCategory.objects.annotate(
            service_count=Count('service_items', distinct=True),
            active_service_count=Count('service_items', filter=visible_service_q('service_items__'), distinct=True),
            escort_count=Count('escorts', distinct=True),
        ).order_by('sort_order', 'id')
        active = self.request.query_params.get('is_active')
        if active not in (None, ''):
            qs = qs.filter(is_active=_truthy(active))
        return qs

    def perform_destroy(self, instance):
        if (instance.service_items.exists() or instance.escorts.exists()
                or hasattr(instance, 'commission_profile')):
            raise rest_serializers.ValidationError('该游戏仍关联服务、陪玩或抽成配置，请使用停用以保留业务关联')
        super().perform_destroy(instance)


class ServiceCategoryViewSet(CatalogMutationMixin, EnvelopeViewSetMixin, ModelViewSet):
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

    def _lock_vip_config(self):
        from engagement.models import ClubOperationConfig
        from engagement.services import config
        config()
        ClubOperationConfig.objects.select_for_update().get(pk=1)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        self._lock_vip_config()
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self._lock_vip_config()
        list(BossType.objects.select_for_update().filter(pk=kwargs.get('pk')).values_list('pk', flat=True))
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self._lock_vip_config()
        list(BossType.objects.select_for_update().filter(pk=kwargs.get('pk')).values_list('pk', flat=True))
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        from engagement.notifier import notify_vip_config_changed
        serializer.save()
        transaction.on_commit(notify_vip_config_changed)

    def perform_update(self, serializer):
        from engagement.notifier import notify_vip_config_changed
        from engagement.models import VipTier
        if (serializer.validated_data.get('is_active') is False and serializer.instance.is_active and
                VipTier.objects.filter(boss_type_id=serializer.instance.pk, active=True).exists()):
            raise rest_serializers.ValidationError('该分级关联启用中的 VIP 等级，请先在 VIP 等级页面核对并停用')
        serializer.save()
        transaction.on_commit(notify_vip_config_changed)

    def perform_destroy(self, instance):
        from engagement.notifier import notify_vip_config_changed
        from engagement.models import VipTier
        if VipTier.objects.filter(boss_type_id=instance.pk).exists():
            raise rest_serializers.ValidationError('该分级关联 VIP 历史记录，不能删除；请在 VIP 等级页面管理')
        instance.delete()
        transaction.on_commit(notify_vip_config_changed)


# ---------------- 陪玩等级 ----------------
class EscortLevelViewSet(CatalogMutationMixin, EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminEscortLevelSerializer
    default_perm = 'escort_level:view'
    required_perms = {
        'create': 'escort_level:edit',
        'update': 'escort_level:edit',
        'partial_update': 'escort_level:edit',
        'destroy': 'escort_level:delete',
        'deletion_impact': ['escort_level:view', 'escort_level:delete'],
    }

    def get_queryset(self):
        qs = EscortLevel.objects.all().order_by('sort_order', 'id')
        is_active = self.request.query_params.get('is_active')
        if is_active is not None and is_active != '':
            qs = qs.filter(is_active=_truthy(is_active))
        return qs

    @action(detail=True, methods=['get'], url_path='deletion-impact')
    def deletion_impact(self, request, pk=None):
        level = self.get_object()
        return Response({'code': 0, 'data': {
            'id': level.pk, 'name': level.name,
            'providers': EscortProfile.objects.filter(level=level).count(),
            'services': ServiceItem.objects.filter(required_level=level).count(),
            'registrations': ProviderRegistration.objects.filter(level=level).count(),
        }})


# ---------------- 促销活动 ----------------
class PromotionViewSet(EnvelopeViewSetMixin, ModelViewSet):
    def perform_create(self, serializer):
        if serializer.validated_data.get('is_active', True):
            from stores.license_benefits import require_promotion_configuration
            require_promotion_configuration()
        serializer.save()

    def perform_update(self, serializer):
        if serializer.validated_data.get('is_active', serializer.instance.is_active):
            from stores.license_benefits import require_promotion_configuration
            require_promotion_configuration()
        serializer.save()

    serializer_class = AdminPromotionSerializer
    default_perm = 'promotion:view'
    required_perms = {
        'create': 'promotion:edit',
        'update': 'promotion:edit',
        'partial_update': 'promotion:edit',
        'destroy': 'promotion:delete',
    }

    def get_queryset(self):
        now = timezone.now()
        qs = Promotion.objects.select_related('category').prefetch_related('items').annotate(
            window_status=Case(
                When(is_active=False, then=Value('DISABLED')),
                When(start_at__gt=now, then=Value('SCHEDULED')),
                When(end_at__lt=now, then=Value('ENDED')),
                default=Value('IN_WINDOW'), output_field=CharField(),
            )
        )
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
        qs = SupportContactCard.objects.all()
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(name__icontains=keyword) | Q(company__icontains=keyword) | Q(wechat_id__icontains=keyword))
        return qs


# ---------------- 试音链接 ----------------
class AuditionLinkViewSet(EnvelopeViewSetMixin, ModelViewSet):
    serializer_class = AdminAuditionLinkSerializer
    default_perm = 'audition:view'
    required_perms = {
        'create': 'audition:edit',
        'update': 'audition:edit',
        'partial_update': 'audition:edit',
        'destroy': 'audition:delete',
        'bind_boss': 'audition:bind_boss',
        'bind_provider': 'audition:bind_provider',
    }

    def get_queryset(self):
        now = timezone.now()
        qs = AuditionLink.objects.select_related('operator', 'boss_user', 'boss_account', 'provider_user', 'provider_account').annotate(
            entry_status=Case(When(is_active=False, then=Value('DISABLED')),
                              When(expire_at__lt=now, then=Value('EXPIRED')),
                              default=Value('ACTIVE'), output_field=CharField()),
        ).order_by('-created_at', '-id')
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

    def perform_destroy(self, instance):
        if instance.signups.exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError('已有试音报名，请停用链接，保留报名与审核记录')
        instance.delete()

    @action(detail=True, methods=['post'], url_path='bind-boss')
    def bind_boss(self, request, pk=None):
        from audition.models import _gen_token
        expected_raw = request.data.get('expected_updated_at')
        try:
            expected = rest_serializers.DateTimeField().run_validation(expected_raw)
        except rest_serializers.ValidationError:
            return Response({'code': 400, 'msg': '请重新读取链接后再操作'}, status=400)
        if 'account_id' not in request.data:
            return Response({'code': 400, 'msg': '请选择老板账号或明确解除绑定'}, status=400)
        raw = request.data['account_id']
        if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0):
            return Response({'code': 400, 'msg': '老板账号标识无效'}, status=400)
        with transaction.atomic():
            link = get_object_or_404(AuditionLink.objects.select_for_update(), pk=pk)
            if link.updated_at != expected:
                return Response({'code': 409, 'msg': '链接已变化，请重新读取后核对'}, status=409)
            account = None
            if raw is not None:
                account = ClubAccount.objects.filter(pk=raw, account_type=ClubAccount.AccountType.BOSS, is_active=True, can_login=True).first()
                if not account:
                    return Response({'code': 400, 'msg': '老板账号不存在或已不可用，请重新选择'}, status=400)
            if link.boss_account_id != raw or link.boss_user_id is not None:
                link.boss_account = account
                link.boss_user = None
                link.boss_token = _gen_token()
                link.save(update_fields=['boss_account', 'boss_user', 'boss_token', 'updated_at'])
        return Response({'code': 0, 'data': self.get_serializer(link).data, 'msg': '老板入口绑定已更新'})

    @action(detail=True, methods=['post'], url_path='bind-provider')
    def bind_provider(self, request, pk=None):
        from audition.models import _gen_token
        try:
            expected = rest_serializers.DateTimeField().run_validation(request.data.get('expected_updated_at'))
        except rest_serializers.ValidationError:
            return Response({'code': 400, 'msg': '请重新读取链接后再操作'}, status=400)
        if 'account_id' not in request.data:
            return Response({'code': 400, 'msg': '请选择陪玩账号或明确解除绑定'}, status=400)
        raw = request.data['account_id']
        if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0):
            return Response({'code': 400, 'msg': '陪玩账号标识无效'}, status=400)
        with transaction.atomic():
            link = get_object_or_404(AuditionLink.objects.select_for_update(), pk=pk)
            if link.updated_at != expected:
                return Response({'code': 409, 'msg': '链接已变化，请重新读取后核对'}, status=409)
            account = None
            if raw is not None:
                account = ClubAccount.objects.filter(pk=raw, account_type=ClubAccount.AccountType.PROVIDER, is_active=True, can_login=True).first()
                if not account:
                    return Response({'code': 400, 'msg': '陪玩账号不存在或已不可用，请重新选择'}, status=400)
            if link.provider_account_id != raw or link.provider_user_id is not None:
                link.provider_account = account
                link.provider_user = None
                link.provider_token = _gen_token()
                link.save(update_fields=['provider_account', 'provider_user', 'provider_token', 'updated_at'])
        return Response({'code': 0, 'data': self.get_serializer(link).data, 'msg': '陪玩入口绑定已更新'})


# ---------------- 试音报名 ----------------
class AuditionSignupViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminAuditionSignupSerializer
    default_perm = 'audition:signup_view'
    required_perms = {
        'approve': 'audition:signup_audit',
        'reject': 'audition:signup_audit',
    }

    def get_queryset(self):
        qs = AuditionSignup.objects.select_related('link', 'applicant', 'applicant_account', 'auditor', 'auditor_account').order_by('-created_at', '-id')
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
                | Q(applicant_account__username__icontains=keyword)
                | Q(applicant_account__nickname__icontains=keyword)
                | Q(applicant_account__account_no__icontains=keyword)
            )
        return qs

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        with transaction.atomic():
            signup = get_object_or_404(AuditionSignup.objects.select_for_update(), pk=pk)
            if signup.status != AuditionSignup.Status.PENDING:
                return Response({'code': 409, 'msg': '该报名已审核，请刷新后核对'}, status=409)
            signup.status = AuditionSignup.Status.APPROVED
            signup.auditor = request.legacy_user
            signup.auditor_account = request.account
            signup.audited_at = timezone.now()
            from audition.notices import prepare, recovery_enabled, schedule
            notice = prepare(signup)
            if recovery_enabled():
                signup.audit_notice_intent = notice
            signup.save(update_fields=[
                'status', 'auditor', 'auditor_account', 'audited_at', 'audit_notice_intent',
            ])
            schedule(signup.pk, notice)
        return Response({'code': 0, 'data': self.get_serializer(signup).data, 'msg': '已通过'})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        audit_remark = (request.data.get('audit_remark') or '').strip()[:255]
        if not audit_remark:
            return Response({'code': 400, 'msg': '请填写驳回原因'})
        with transaction.atomic():
            signup = get_object_or_404(AuditionSignup.objects.select_for_update(), pk=pk)
            if signup.status != AuditionSignup.Status.PENDING:
                return Response({'code': 409, 'msg': '该报名已审核，请刷新后核对'}, status=409)
            signup.status = AuditionSignup.Status.REJECTED
            signup.audit_remark = audit_remark
            signup.auditor = request.legacy_user
            signup.auditor_account = request.account
            signup.audited_at = timezone.now()
            from audition.notices import prepare, recovery_enabled, schedule
            notice = prepare(signup)
            if recovery_enabled():
                signup.audit_notice_intent = notice
            signup.save(update_fields=[
                'status', 'audit_remark', 'auditor', 'auditor_account', 'audited_at', 'audit_notice_intent',
            ])
            schedule(signup.pk, notice)
        return Response({'code': 0, 'data': self.get_serializer(signup).data, 'msg': '已驳回'})


# ---------------- 站内消息 ----------------
class MessageViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = AdminMessageSerializer
    default_perm = 'message:view'
    required_perms = {'push': 'message:push', 'push_status': 'message:view',
                      'push_campaigns': 'message:view', 'push_retry': 'message:push'}

    def get_queryset(self):
        qs = Message.objects.select_related('recipient', 'recipient_account').order_by('-created_at', '-id')
        msg_type = self.request.query_params.get('type')
        if msg_type:
            qs = qs.filter(type=msg_type.upper())
        recipient = self.request.query_params.get('recipient')
        if recipient:
            qs = qs.filter(recipient_id=recipient)
        recipient_account = self.request.query_params.get('recipient_account')
        if recipient_account:
            if not recipient_account.isascii() or not recipient_account.isdigit() or int(recipient_account) < 1:
                raise rest_serializers.ValidationError({'recipient_account': '接收账号标识无效'})
            qs = qs.filter(recipient_account_id=int(recipient_account))
        return qs

    @action(detail=False, methods=['post'])
    def push(self, request):
        import uuid
        from site_messages.manual_push import (campaign_result, existing_campaign, fingerprint,
                                               process_campaign, stage_campaign)
        title = request.data.get('title') or ''
        preview = request.data.get('preview') or ''
        detail = request.data.get('detail') or ''
        if not all(isinstance(value, str) for value in (title, preview, detail)):
            return Response({'code': 400, 'msg': '消息标题、摘要和内容必须为文字'}, status=400)
        title = title.strip()
        if not title:
            return Response({'code': 400, 'msg': '请填写标题'})
        preview = preview.strip()
        raw_type = request.data.get('type') or 'SYSTEM'
        if not isinstance(raw_type, str):
            return Response({'code': 400, 'msg': '消息类型无效'}, status=400)
        msg_type = raw_type.upper()
        if msg_type not in Message.Type.values:
            return Response({'code': 400, 'msg': '消息类型无效'}, status=400)
        if len(title) > 100 or len(preview) > 255:
            return Response({'code': 400, 'msg': '标题最多 100 字，摘要最多 255 字'}, status=400)
        broadcast = _truthy(request.data.get('broadcast', False))
        raw_audience = request.data.get('audience') or ''
        if not isinstance(raw_audience, str):
            return Response({'code': 400, 'msg': '接收角色无效'}, status=400)
        audience = raw_audience.upper()
        def recipient_list(field):
            if hasattr(request.data, 'getlist'):
                return request.data.getlist(field)
            value = request.data.get(field) or []
            return value if isinstance(value, list) else [value]

        recipient_ids = recipient_list('recipient_ids')
        recipient_account_ids = recipient_list('recipient_account_ids')

        role_map = {
            User.Role.CUSTOMER: ClubAccount.AccountType.BOSS,
            ClubAccount.AccountType.BOSS: ClubAccount.AccountType.BOSS,
            User.Role.PROVIDER: ClubAccount.AccountType.PROVIDER,
            ClubAccount.AccountType.STAFF: ClubAccount.AccountType.STAFF,
        }
        if not broadcast and audience and audience not in role_map:
            return Response({'code': 400, 'msg': '接收角色无效'}, status=400)
        try:
            recipient_ids = sorted({int(value) for value in recipient_ids})
            recipient_account_ids = sorted({int(value) for value in recipient_account_ids})
        except (ValueError, TypeError, OverflowError):
            return Response({'code': 400, 'msg': '接收账号标识无效'}, status=400)
        if any(value < 1 for value in recipient_ids + recipient_account_ids):
            return Response({'code': 400, 'msg': '接收账号标识无效'}, status=400)
        supplied_request = request.data.get('client_request_id')
        request_id = supplied_request if supplied_request is not None else uuid.uuid4().hex
        if not isinstance(request_id, str) or not request_id or len(request_id) > 64 or request_id != request_id.strip():
            return Response({'code': 400, 'msg': '请填写有效的消息请求编号'}, status=400)
        selector = ({'broadcast': True} if broadcast else {'audience': role_map[audience]}
                    if audience in role_map else {'accounts': recipient_account_ids, 'users': recipient_ids})
        digest = fingerprint({'title': title, 'preview': preview, 'detail': detail,
                              'type': msg_type, 'selector': selector})
        campaign = existing_campaign(request.account, request_id, digest)
        if campaign:
            process_campaign(campaign)
            result = campaign_result(campaign)
            result['request_replayed'] = True
            return Response({'code': 0, 'msg': '已读取原群发请求', 'data': result})
        if broadcast or audience in role_map:
            accounts = ClubAccount.objects.filter(is_active=True, license_membership_active=True)
            if not broadcast:
                accounts = accounts.filter(account_type=role_map[audience])
            # Business accounts are the recipient identity. A legacy user is
            # optional and must not determine who receives the message.
            account_cycles = {pk: (cycle, kind) for pk, cycle, kind in
                              accounts.order_by('pk').values_list('pk', 'business_cycle', 'account_type')}
        else:
            if not recipient_ids and not recipient_account_ids:
                return Response({'code': 400, 'msg': '请选择接收人或勾选全员广播'})
            # Business account IDs and legacy user IDs are distinct namespaces.
            # The account is authoritative; a legacy recipient is optional.
            account_cycles = {}
            for rid in recipient_account_ids:
                account_id = rid
                account = ClubAccount.objects.filter(pk=account_id, is_active=True, can_login=True,
                    license_membership_active=True,
                    account_type__in=[ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER]).first()
                if not account:
                    return Response({'code': 400, 'msg': '接收账号已不可用，请重新选择'}, status=400)
                account_cycles[account.pk] = (account.business_cycle, account.account_type)
            for rid in recipient_ids:
                lid = rid
                if not User.objects.filter(pk=lid).exists():
                    return Response({'code': 400, 'msg': '接收人不存在'}, status=400)
                mapped_id = LegacyAccountMap.objects.filter(legacy_user_id=lid).values_list('account_id', flat=True).first()
                account = ClubAccount.objects.filter(pk=mapped_id, is_active=True,
                        license_membership_active=True, can_login=True).first() if mapped_id else None
                if not account:
                    return Response({'code': 400, 'msg': '接收人未关联有效门店账号'}, status=400)
                identity = (account.business_cycle, account.account_type)
                if account.pk in account_cycles and account_cycles[account.pk] != identity:
                    return Response({'code': 409, 'msg': '接收账号状态已变化，请重新选择'}, status=409)
                account_cycles[account.pk] = identity

        campaign, created = stage_campaign(creator=request.account, request_id=request_id, digest=digest,
            title=title, preview=preview, detail=detail, message_type=msg_type, selector=selector,
            account_cycles=account_cycles)
        process_campaign(campaign)
        result = campaign_result(campaign)
        result['request_replayed'] = not created
        return Response({'code': 0, 'msg': f'已创建 {result["count"]} 条站内消息', 'data': result})

    @action(detail=False, methods=['get'], url_path='push-campaigns')
    def push_campaigns(self, request):
        """List this store's durable operator requests, including incomplete work."""
        from site_messages.models import ManualMessageCampaign, ManualMessageRecipient
        rows = ManualMessageCampaign.objects.annotate(
            total=Count('recipients'),
            created_count=Count('recipients', filter=Q(recipients__status=ManualMessageRecipient.Status.CREATED)),
            pending_count=Count('recipients', filter=Q(recipients__status=ManualMessageRecipient.Status.PENDING)),
            failed_count=Count('recipients', filter=Q(recipients__status=ManualMessageRecipient.Status.PENDING)
                               & ~Q(recipients__last_error='')),
            skipped_count=Count('recipients', filter=Q(recipients__status=ManualMessageRecipient.Status.SKIPPED)),
        ).order_by('-created_at', '-pk')
        creator_id = request.query_params.get('creator_id', '')
        if creator_id:
            if not creator_id.isascii() or not creator_id.isdigit() or int(creator_id) < 1:
                return Response({'code': 400, 'msg': '操作人标识无效'}, status=400)
            rows = rows.filter(creator_id=int(creator_id))
        request_id = request.query_params.get('client_request_id', '')
        if request_id:
            if len(request_id) > 64 or request_id != request_id.strip():
                return Response({'code': 400, 'msg': '消息请求编号无效'}, status=400)
            rows = rows.filter(request_id=request_id)
        state = request.query_params.get('status', '')
        if state == 'PENDING':
            rows = rows.filter(pending_count__gt=0)
        elif state == 'FAILED':
            rows = rows.filter(failed_count__gt=0)
        elif state == 'SKIPPED':
            rows = rows.filter(skipped_count__gt=0)
        elif state == 'COMPLETED':
            rows = rows.filter(pending_count=0)
        elif state:
            return Response({'code': 400, 'msg': '群发状态无效'}, status=400)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response([{
            'campaign_id': row.pk, 'client_request_id': row.request_id,
            'creator_id': row.creator_id, 'created_at': row.created_at,
            'title': row.title, 'message_type': row.message_type, 'selector': row.selector,
            'total': row.total, 'count': row.created_count, 'pending_count': row.pending_count,
            'failed_count': row.failed_count, 'skipped_count': row.skipped_count,
        } for row in page])

    @action(detail=False, methods=['get'], url_path='push-status')
    def push_status(self, request):
        from site_messages.manual_push import campaign_result
        from site_messages.models import ManualMessageCampaign, ManualMessageRecipient
        raw_id = request.query_params.get('campaign_id', '')
        if not raw_id.isascii() or not raw_id.isdigit() or int(raw_id) < 1:
            return Response({'code': 400, 'msg': '群发记录编号无效'}, status=400)
        campaign = ManualMessageCampaign.objects.filter(pk=int(raw_id)).first()
        if not campaign:
            return Response({'code': 404, 'msg': '群发记录不存在'}, status=404)
        rows = campaign.recipients.select_related('account').order_by('pk')
        state = request.query_params.get('status', '')
        if state:
            if state == 'FAILED':
                rows = rows.filter(status=ManualMessageRecipient.Status.PENDING).exclude(last_error='')
            elif state not in ManualMessageRecipient.Status.values:
                return Response({'code': 400, 'msg': '接收状态无效'}, status=400)
            else:
                rows = rows.filter(status=state)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request)
        result = [{'id': row.pk, 'account_id': row.account_id, 'account_no': row.account.account_no,
                   'status': row.status, 'attempts': row.attempts, 'last_error': row.last_error,
                   'message_id': row.message_id, 'next_attempt_at': row.next_attempt_at if row.status == 'PENDING' else None}
                  for row in page]
        response = paginator.get_paginated_response(result)
        response.data['data']['campaign'] = campaign_result(campaign)
        response.data['data']['campaign'].update(preview=campaign.preview, detail=campaign.detail)
        return response

    @action(detail=False, methods=['post'], url_path='push-retry')
    def push_retry(self, request):
        from site_messages.manual_push import campaign_result, process_campaign
        from site_messages.models import ManualMessageCampaign
        raw_id, selected = request.data.get('campaign_id'), request.data.get('recipient_ids')
        if (type(raw_id) is not int or raw_id < 1 or not isinstance(selected, list) or not selected
                or len(selected) > 200 or any(type(item) is not int or item < 1 for item in selected)):
            return Response({'code': 400, 'msg': '请选择群发记录及最多 200 个接收人'}, status=400)
        campaign = ManualMessageCampaign.objects.filter(pk=raw_id).first()
        if not campaign:
            return Response({'code': 404, 'msg': '群发记录不存在'}, status=404)
        selected = set(selected)
        if campaign.recipients.filter(pk__in=selected).count() != len(selected):
            return Response({'code': 400, 'msg': '所选接收人不属于该群发记录'}, status=400)
        process_campaign(campaign, target_ids=selected)
        return Response({'code': 0, 'msg': '已处理所选接收人', 'data': campaign_result(campaign)})


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
        if instance.code == 'super_admin':
            from rest_framework.exceptions import ValidationError
            raise ValidationError('内置超级管理员角色不可删除')
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
        qs = AdminMembership.objects.select_related('account', 'user').prefetch_related('roles')
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(
                Q(account__username__icontains=keyword)
                | Q(account__nickname__icontains=keyword)
                | Q(account__phone__icontains=keyword)
            )
        return qs

    def today_dispatch_metrics(self):
        if not hasattr(self, '_today_dispatch_metrics'):
            from .dispatch_original_facts import today_dispatch_totals
            self._today_dispatch_metrics = today_dispatch_totals(as_of=timezone.now())
        return self._today_dispatch_metrics

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
        serializer = AdminUpdateMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

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
                if phone != account.phone:
                    account.is_phone_verified = False
                    user.is_phone_verified = False
                    account_fields.append('is_phone_verified')
                    user_fields.append('is_phone_verified')
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
                    instance.roles.set(data['roles'])
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
        from stores.license_audit import restrict_audit_logs
        qs = restrict_audit_logs(AdminAuditLog.objects.select_related('operator').all())
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
