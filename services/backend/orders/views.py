from datetime import timedelta
from collections.abc import Mapping
from contextlib import ExitStack

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from stores.db import transaction
from stores.context import current_currency_name
from django.db.models import Avg, Count, F, Prefetch, Q, Sum, prefetch_related_objects
from django.utils import timezone
from rest_framework import serializers as rest_serializers
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.permissions import IsClubAccountAuthenticated, resolve_club_account
from console.permissions import HasConsolePerm, get_account_permissions
from users.models import EscortProfile, EscortSchedule
from wallet.models import IdempotencyKey, ProviderReport, Transaction, get_platform_wallet
from wallet.receipts import fingerprint_for, fund_success, recover_fund_result
from wallet.services import (
    WalletAddressingError,
    claim_fund_write,
    get_wallet,
    peek_fund_write,
)
from common.logging_utils import log_money_event, new_trace_id
from common.media import build_media_url

from .traffic import OrderTrafficThrottle
from .capacity import current_capacity_limit

from .models import (
    Evaluation, GameCategory, KookDispatchRecord, Order, OrderProvider, OrderStatusLog,
    ServiceFavorite, ServiceItem, OrderApplication,
)
from .catalog import visible_service_q
from .kook_dispatch import enqueue_kook_dispatch
from .dispatching import enqueue_wecom_dispatch
from .notifier import notify_order_update, notify_evaluation_update
from .ratings import refresh_escort_rating
from .services import (
    count_capacity_orders,
    ensure_provider_dispatch_locks,
    lock_provider_dispatch,
    mark_escort_busy,
    pending_timeout_deadline,
    push_message_safe as _push_message_safe,
    refund_to_customer as _refund_to_customer,
    schedule_auto_cancel as _schedule_auto_cancel,
    settle_order,
    complete_fulfillment,
    sync_single_provider_share,
)
from .settlement import SettlementError, assert_order_conserved, compute_split
from .serializers import (
    CreateEvaluationSerializer,
    CreateOrderSerializer,
    EvaluationSerializer,
    OrderSerializer,
    ServiceItemDetailSerializer,
    ServiceItemSerializer,
)
from .state_machine import IllegalTransitionError, log_only, transition
from .order_operation_receipts import fulfillment_receipt, grab_receipt
from .purchase_benefits import account_service_q

User = get_user_model()

# 商品详情页评价列表最多展示条数
MAX_SERVICE_EVALUATIONS = 20

# 订单列表 ?status= 的对外取值 -> 内部状态。
#
# 对外一律小写下划线，与内部枚举解耦：内部把「服务中」叫 IN_SERVICE，
# 对外历史上叫 in_progress，这层映射就是为了不让内部改名波及客户端。
# 未收录的取值一律 400（见 OrderListView），不再静默放行。
ORDER_STATUS_FILTER_MAP = {
    'pending': Order.Status.PENDING,
    'grabbed': Order.Status.GRABBED,
    'in_progress': Order.Status.IN_SERVICE,
    'completed': Order.Status.COMPLETED,
    'cancelled': Order.Status.CANCELLED,
}


def _account_for_legacy_user(user):
    if user is None:
        return None
    mapping = LegacyAccountMap.objects.select_related('account').filter(
        legacy_user_id=user.id,
    ).first()
    return mapping.account if mapping else None


class ServiceListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        account = resolve_club_account(request)
        services = ServiceItem.objects.filter(visible_service_q(), account_service_q(account)).select_related('gameplay', 'game_category', 'service_category', 'required_level')
        for key in ('gameplay', 'platform', 'security_level'):
            value = request.query_params.get(key)
            if value:
                if key == 'gameplay' and not value.isdigit():
                    return Response({'code': 400, 'msg': '玩法参数无效'}, status=400)
                services = services.filter(**{key: value})
        category = request.query_params.get('category', '').upper()
        if category == 'GIFT':
            services = services.filter(
                service_category__is_active=True,
                service_category__is_gift=True,
            )
        game_category = request.query_params.get('game_category')
        if game_category:
            services = services.filter(game_category_id=game_category)
        service_category = request.query_params.get('service_category')
        if service_category:
            services = services.filter(service_category_id=service_category)
        services = services.order_by('sort_order', 'id')
        coupon_id = request.query_params.get('coupon_id')
        if coupon_id:
            from coupons.benefits import applies_to
            from orders.purchase_benefits import usable_coupons
            if account is None:
                return Response({'code': 401, 'msg': '登录后可查看优惠券适用套餐'}, status=401)
            if not coupon_id.isascii() or not coupon_id.isdigit() or len(coupon_id) > 18:
                return Response({'code': 400, 'msg': '优惠券参数无效'})
            coupon = usable_coupons(account).filter(pk=int(coupon_id)).first()
            if not coupon:
                return Response({'code': 400, 'msg': '优惠券已不可用，请返回我的券重新查看'})
            services = [service for service in services if applies_to(coupon.coupon, service)]
        serializer = ServiceItemSerializer(services, many=True)
        return Response({'code': 0, 'data': serializer.data})


class GameCategoryListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        categories = GameCategory.objects.filter(is_active=True).order_by('sort_order', 'id')
        return Response({
            'code': 0,
            'data': [
                {
                    'id': item.id,
                    'name': item.name,
                    'remark': item.remark,
                    'icon_url': build_media_url(request, item.icon),
                }
                for item in categories
            ],
        })


class ServiceDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, service_id):
        account = resolve_club_account(request)
        try:
            service = ServiceItem.objects.filter(visible_service_q(), account_service_q(account)).get(id=service_id)
        except ServiceItem.DoesNotExist:
            return Response({'code': 404, 'msg': '服务不存在'})
        coupon_id = request.query_params.get('coupon_id')
        coupon = None
        if coupon_id:
            from coupons.benefits import applies_to
            from orders.purchase_benefits import usable_coupons
            if account is None:
                return Response({'code': 401, 'msg': '登录后可查看优惠券适用套餐'}, status=401)
            if not coupon_id.isascii() or not coupon_id.isdigit() or len(coupon_id) > 18:
                return Response({'code': 400, 'msg': '优惠券参数无效'})
            coupon = usable_coupons(account).filter(pk=int(coupon_id)).first()
            if not coupon or not applies_to(coupon.coupon, service):
                return Response({'code': 400, 'msg': '优惠券已不可用或不适用于此套餐，请返回重新选择'})
        return Response({
            'code': 0,
            'data': ServiceItemDetailSerializer(service, context={'request': request, 'selected_coupon': coupon}).data,
        })


class FavoriteListView(APIView):
    """我的收藏：返回当前老板收藏的服务列表。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        favorites = ServiceFavorite.objects.filter(
            account=request.account,
        ).select_related('service', 'service__gameplay', 'service__game_category', 'service__service_category', 'service__required_level')
        available_ids = set(ServiceItem.objects.filter(
            visible_service_q(), account_service_q(request.account),
            favorited_by__account=request.account,
        ).values_list('id', flat=True))
        data = []
        for fav in favorites:
            if fav.service_id in available_ids:
                item = dict(ServiceItemSerializer(fav.service).data, available=True)
            else:
                item = {'id': fav.service_id, 'name': fav.service.name, 'description': '',
                        'price': 0, 'available': False}
            data.append(item)
        return Response({'code': 0, 'data': data})


class FavoriteRemoveView(APIView):
    """Remove an existing favorite, including one no longer available."""
    permission_classes = [IsClubAccountAuthenticated]

    def delete(self, request, service_id):
        ServiceFavorite.objects.filter(account=request.account, service_id=service_id).delete()
        return Response({'code': 0, 'msg': '已取消收藏', 'data': {'favorited': False}})


class FavoriteToggleView(APIView):
    """收藏 / 取消收藏：幂等切换，返回当前是否已收藏。"""
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        service_id = request.data.get('service_id')
        if not service_id:
            return Response({'code': 400, 'msg': '缺少 service_id'})
        try:
            service = ServiceItem.objects.filter(visible_service_q(), account_service_q(request.account)).get(id=service_id)
        except ServiceItem.DoesNotExist:
            return Response({'code': 404, 'msg': '服务不存在'})

        favorite = ServiceFavorite.objects.filter(
            account=request.account,
            service=service,
        ).first()
        if favorite:
            favorite.delete()
            return Response({'code': 0, 'msg': '已取消收藏', 'data': {'favorited': False}})

        ServiceFavorite.objects.create(
            user=request.legacy_user,
            account=request.account,
            service=service,
        )
        return Response({'code': 0, 'msg': '已收藏', 'data': {'favorited': True}})


# 排行榜返回条数
RANKING_LIMIT = 5

# 消费榜爵位阈值（累计浪花消费的内部账务值；从高到低，取首个达标）
CONSUME_TITLE_TIERS = [
    (100000 * 100, '王'),
    (50000 * 100, '公'),
    (10000 * 100, '侯'),
    (6000 * 100, '伯'),
    (3000 * 100, '子'),
    (0, '男'),
]

# 接单榜爵位阈值（累计接单量，单位：单；从高到低，取首个达标）
ORDER_TITLE_TIERS = [
    (200, '王'),
    (100, '公'),
    (60, '侯'),
    (30, '伯'),
    (10, '子'),
    (0, '男'),
]


def _title_for(value, tiers):
    for threshold, title in tiers:
        if value >= threshold:
            return title
    return tiers[-1][1]


class RankingView(APIView):
    """首页风云榜：老板消费榜 + 陪玩接单榜（基于已完成订单聚合）。
    支持 ?period=day|week|month，按 completed_at 过滤；默认 month。
    """
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        # Only the public leaderboards are optional; dispatch ranking and
        # personal order statistics are core business and remain available.
        from stores.license_benefits import check_license_benefits
        admission, _ = check_license_benefits('ACHIEVEMENTS_RANKINGS')
        if admission == 'UNKNOWN':
            return Response({'code': 503, 'msg': '暂时无法核验门店授权，请联系平台后重试'}, status=503)
        if admission != 'ALLOW':
            return Response({'code': 403, 'msg': '门店授权暂不允许查看排行榜，请联系门店管理员'}, status=403)
        period = request.query_params.get('period', 'month')
        if period not in ('day', 'week', 'month'):
            period = 'month'

        today = timezone.localdate()
        if period == 'day':
            start_date = today
        elif period == 'week':
            start_date = today - timedelta(days=today.weekday())
        else:
            start_date = today.replace(day=1)
        start_dt = timezone.make_aware(
            timezone.datetime.combine(start_date, timezone.datetime.min.time()),
            timezone.get_current_timezone(),
        )

        from .customer_ranking import customer_totals
        now = timezone.now()
        consume_rows, accounts, legacy_by_account = customer_totals(start=start_dt, now=now)
        ranking_complete = all(row['complete'] for row in consume_rows)
        consume_rows.sort(key=lambda row: (not row['complete'], -row['total'], row['account_id'] or 0, row['user_id'] or 0))
        consume_rows = consume_rows[:RANKING_LIMIT]
        from .provider_ranking import counts as provider_counts
        counts, order_ranking_complete = provider_counts(start_dt, now)
        profiles = {profile.account_id: profile for profile in EscortProfile.objects.select_related('account', 'user').filter(
            account_id__in=counts)}
        from .grab_original_facts import original_amount_facts
        facts = original_amount_facts(list(profiles), since=start_dt, as_of=now)
        ordered_accounts = sorted(profiles, key=lambda pk: (-counts[pk], pk))[:RANKING_LIMIT]
        user_ids = [row['user_id'] or legacy_by_account.get(row['account_id']) for row in consume_rows]
        users = {u.id: u for u in User.objects.filter(id__in=user_ids)}

        def display(uid):
            user = users.get(uid)
            if not user:
                return '神秘用户', ''
            return (user.nickname or user.username or '用户'), (user.avatar_url or '')

        consume_rank = []
        for idx, row in enumerate(consume_rows):
            user_id = row['user_id'] or legacy_by_account.get(row['account_id'])
            account = accounts.get(row['account_id'])
            name, avatar = display(user_id)
            if account:
                name = account.nickname or account.username or name
                avatar = account.avatar_url or avatar
            total = row['total'] if row['complete'] else None
            consume_rank.append({
                'rank': idx + 1 if ranking_complete else None,
                'user_id': user_id,
                'customer_account_id': row['account_id'],
                'nickname': name,
                'avatar': avatar,
                'total_amount': total,
                'amount_complete': row['complete'],
                'order_count': row['order_count'],
                'title': _title_for(total, CONSUME_TITLE_TIERS) if total is not None else None,
            })

        from stores.display import provider_display_name
        order_rank = []
        for idx, account_id in enumerate(ordered_accounts):
            profile = profiles[account_id]
            identity = profile.account
            count = counts[account_id]
            order_rank.append({
                'rank': idx + 1,
                'user_id': profile.user_id,
                'provider_account_id': (account_id if profile.is_verified and identity.is_active
                                        and identity.can_login and identity.can_view else None),
                'nickname': profile.display_name or identity.nickname or identity.username,
                'display_name': provider_display_name(profile.display_name or identity.nickname or identity.username),
                'avatar': identity.avatar_url or '',
                'order_count': count,
                'total_amount': facts[account_id].amount,
                'excess_refund_amount_raw': facts[account_id].excess_refund_units,
                'title': _title_for(count, ORDER_TITLE_TIERS),
            })

        return Response({
            'code': 0,
            'data': {
                'period': period,
                'consume_rank': consume_rank,
                'consume_rank_complete': ranking_complete,
                'order_rank': order_rank,
                'order_rank_complete': order_ranking_complete,
            },
        })


class OrderListView(APIView):
    throttle_classes = [OrderTrafficThrottle]
    traffic_action = 'pool'
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        account = request.account
        context = {'request': request}
        status_filter = request.query_params.get('status')
        if account.account_type == ClubAccount.AccountType.PROVIDER:
            if status_filter == 'pending':
                # 待接单池：返回尚未被接单的 PENDING 订单，供陪玩抢单
                context['_provider_pool_view'] = True
                from .pool_visibility import visible_provider_pool
                orders, read_state = visible_provider_pool(account)
                if read_state is not None:
                    context['_provider_read_state'] = read_state[:4]
                    context['_provider_capacity_limit'] = read_state[4]

            else:
                # A rejected assignment is retained for history but has no
                # service share. Do not surface it as a current assignment
                # after another provider has accepted the same order.
                current_tabs = status_filter in ('grabbed', 'in_progress')
                meaningful_share = (Q(providers__released_at__isnull=True) |
                                    Q(providers__settlement_base__gt=0) |
                                    Q(providers__provider_income__gt=0))
                member = Q(providers__provider_account=account)
                ownership = Q(provider_account=account) | (member & meaningful_share if current_tabs else member)
                if request.legacy_user:
                    legacy_member = Q(providers__provider_account__isnull=True, providers__provider=request.legacy_user)
                    ownership |= Q(provider_account__isnull=True, provider=request.legacy_user) | (
                        legacy_member & meaningful_share if current_tabs else legacy_member)
                orders = Order.objects.filter(ownership).distinct()
        elif account.account_type == ClubAccount.AccountType.STAFF:
            if 'order:view' not in get_account_permissions(account):
                return Response({'code': 403, 'msg': '无订单查看权限'}, status=403)
            orders = Order.objects.all()
        else:
            ownership = Q(customer_account=account)
            if request.legacy_user:
                ownership |= Q(customer_account__isnull=True, customer=request.legacy_user)
            orders = Order.objects.filter(ownership)

        if status_filter and status_filter != 'all':
            backend_status = ORDER_STATUS_FILTER_MAP.get(status_filter)
            if backend_status is None:
                # 不认识的筛选值以前会被静默忽略，于是前端拼错一个 status
                # （例如把 in_progress 写成 in_service）就会拿到「全部订单」，
                # 看上去像后端漏了过滤，实则是契约没对齐。直接报错，问题当场暴露。
                return Response({
                    'code': 400,
                    'msg': (
                        f'不支持的订单状态筛选：{status_filter}；'
                        f'可选值 all/{"/".join(ORDER_STATUS_FILTER_MAP)}'
                    ),
                })
            orders = orders.filter(status=backend_status)

        paginated = account.account_type == ClubAccount.AccountType.PROVIDER and status_filter != 'pending' and 'page' in request.query_params
        if paginated:
            page_value = request.query_params.get('page', '1')
            size_value = request.query_params.get('page_size', '20')
            if any(not value.isascii() or not value.isdigit() or int(value) < 1 for value in (page_value, size_value)):
                return Response({'code': 400, 'msg': '页码和每页数量须为正整数'}, status=400)
            page, page_size = int(page_value), min(int(size_value), 100)
            query = request.query_params.get('q', '').strip()
            if query:
                from identifiers.services import entity_ids_for_code
                orders = orders.filter(Q(order_no__icontains=query[:64]) | Q(pk__in=entity_ids_for_code(query[:64], 'orders.order')))

        for key in ('provider_kind','platform','security_level','grade'):
            value=request.query_params.get(key,'')
            if value:
                from users.qualifications import KINDS, PLATFORMS, SECURITY, GRADES
                if value not in {'provider_kind':KINDS,'platform':PLATFORMS,'security_level':SECURITY,'grade':GRADES}[key]:
                    return Response({'code':400,'msg':'订单资格筛选参数无效'},status=400)
                orders=orders.filter(**{'provider_requirement_snapshot__'+key:value})
        game=request.query_params.get('game_category','')
        if game:
            if not game.isascii() or not game.isdigit():return Response({'code':400,'msg':'游戏类目参数无效'},status=400)
            from .paid_service_scope import enabled as paid_scope_enabled
            if paid_scope_enabled():
                orders=orders.filter(Q(paid_service_scope__scope__game_category_id=int(game)) |
                                     Q(paid_service_scope__isnull=True, service__game_category_id=int(game)))
            else:
                orders=orders.filter(service__game_category_id=int(game))
        requester_assignments = Q(provider_account=account)
        if account.account_type == ClubAccount.AccountType.PROVIDER and request.legacy_user:
            # Historical shares can predate business-account backfill. Only
            # fall back to the matching legacy user when no account is stored.
            requester_assignments |= Q(provider_account__isnull=True, provider=request.legacy_user)
        rejection_actor = Q(operator_account=account)
        if account.account_type == ClubAccount.AccountType.PROVIDER and request.legacy_user:
            rejection_actor |= Q(operator_account__isnull=True, operator=request.legacy_user)
        orders = orders.select_related(
            'customer', 'customer__boss_type', 'provider', 'service', 'source_order',
            'service__service_category', 'service__gameplay', 'service__game_category',
            'service__required_level', 'support_contact', 'reservation', 'direct_payment__fee_quote'
        ).prefetch_related('evaluations', Prefetch(
            'providers', queryset=OrderProvider.objects.filter(
                requester_assignments | Q(released_at__isnull=True)).order_by('pk'),
            to_attr='_visible_participants',
        ), Prefetch('applications', queryset=OrderApplication.objects.select_related('applicant__escort_profile__level','partner__escort_profile__level').prefetch_related('voices__recording'), to_attr='application_list')).order_by('-created_at', '-pk')
        from .paid_service_scope import enabled as paid_scope_enabled
        if paid_scope_enabled():
            orders = orders.select_related('paid_service_scope')
        if paginated:
            total = orders.count()
            page = min(page, max(1, (total + page_size - 1) // page_size))
            orders = orders[(page - 1) * page_size:page * page_size]
        orders = list(orders)
        legacy_id = getattr(request.legacy_user, 'pk', None) if account.account_type == ClubAccount.AccountType.PROVIDER else None
        for order in orders:
            order.requester_assignments = [row for row in order._visible_participants
                if row.provider_account_id == account.pk or
                (legacy_id and row.provider_account_id is None and row.provider_id == legacy_id)]
            order.active_participants = [row for row in order._visible_participants if row.released_at is None]
        released = [order for order in orders if any(row.released_at for row in order.requester_assignments)]
        if released:
            prefetch_related_objects(released, Prefetch(
                'status_logs', queryset=OrderStatusLog.objects.filter(rejection_actor, action=OrderStatusLog.Action.REJECT).only('order_id', 'created_at'),
                to_attr='requester_rejections'))
        serializer = OrderSerializer(orders, many=True, context=context)
        if paginated:
            return Response({'code': 0, 'data': {'list': serializer.data, 'total': total, 'page': page, 'page_size': page_size}})
        return Response({'code': 0, 'data': serializer.data})


class OrderDetailView(APIView):
    """A scoped detail endpoint for shared order links and realtime refreshes."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id):
        account = request.account
        orders = Order.objects.all()
        if account.account_type == ClubAccount.AccountType.BOSS:
            ownership = Q(customer_account=account)
            if request.legacy_user:
                ownership |= Q(customer_account__isnull=True, customer=request.legacy_user)
            orders = orders.filter(ownership)
        elif account.account_type == ClubAccount.AccountType.PROVIDER:
            ownership = Q(provider_account=account) | Q(providers__provider_account=account)
            if request.legacy_user:
                ownership |= Q(provider_account__isnull=True, provider=request.legacy_user) | Q(
                    providers__provider_account__isnull=True, providers__provider=request.legacy_user)
            orders = orders.filter(ownership).distinct()
        elif account.account_type != ClubAccount.AccountType.STAFF:
            orders = orders.none()
        elif 'order:view' not in get_account_permissions(account):
            return Response({'code': 403, 'msg': '无订单查看权限'}, status=403)
        order = orders.select_related(
            'customer', 'customer__boss_type', 'provider', 'service', 'source_order',
            'service__service_category', 'support_contact',
        ).prefetch_related('evaluations').filter(pk=order_id).first()
        context = {'request': request, 'validate_application_readiness': True}
        if order is None and account.account_type == ClubAccount.AccountType.PROVIDER:
            # Deep links must show exactly the same public facts as the pool;
            # never turn knowledge of an ID into access to an invisible order.
            from .pool_visibility import visible_provider_pool
            pool, read_state = visible_provider_pool(account)
            order = pool.select_related('customer', 'customer__boss_type', 'provider', 'service',
                'source_order', 'service__service_category', 'support_contact').prefetch_related('evaluations').filter(pk=order_id).first()
            if order is not None and read_state is not None:
                context.update(_provider_pool_view=True, _provider_read_state=read_state[:4],
                               _provider_capacity_limit=read_state[4])
        if order is None:
            return Response({'code': 404, 'msg': '订单不存在或无权查看'}, status=404)
        return Response({'code': 0, 'data': OrderSerializer(
            order, context=context,
        ).data})


class CreateOrderView(APIView):
    throttle_classes = [OrderTrafficThrottle]
    traffic_action = 'create'
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    @recover_fund_result('order.create')
    def post(self, request):
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        # The ordinary route remains unchanged. A formal points attempt binds
        # this buyer and service across quote verification and the existing
        # atomic debit/receipt writer; downstream lifecycle gates still apply.
        if (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                and isinstance(request.data, Mapping) and request.data.get('points_checkout_test_token')):
            from .points_business_access import checkout
            try:
                service_id = rest_serializers.IntegerField(min_value=1).run_validation(request.data.get('service_id'))
                service = ServiceItem.objects.filter(pk=service_id, is_active=True).first()
                if not service:
                    return Response({'code': 404, 'msg': '服务不存在或不可用'}, status=404)
                # Dispatch mutex rows must exist before entering the business
                # transaction; never create an FK row while holding service or wallet locks.
                raw_ids = request.data.get('provider_ids')
                if raw_ids is None:
                    raw_ids = [request.data.get('provider_id')] if request.data.get('provider_id') else []
                if isinstance(raw_ids, list) and 1 <= len(raw_ids) <= 2:
                    provider_ids = [rest_serializers.IntegerField(min_value=1).run_validation(value) for value in raw_ids]
                    from .services import ensure_provider_dispatch_locks
                    if (len(set(provider_ids)) == len(provider_ids)
                            and ClubAccount.objects.filter(pk__in=provider_ids,
                                account_type=ClubAccount.AccountType.PROVIDER).count() == len(provider_ids)):
                        ensure_provider_dispatch_locks(provider_ids)
                referral_lifecycle = None
                if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                    from .referral_cycle import capture
                    referral_lifecycle = capture(request.account, request.legacy_user, raw_ids)
                with transaction.atomic():
                    if referral_lifecycle is not None:
                        from .referral_cycle import lock
                        lock(referral_lifecycle)
                        request._ordinary_referral_lifecycle = referral_lifecycle
                    with checkout(account=request.account, service=service):
                        return self._post_impl(request)
            except rest_serializers.ValidationError as exc:
                return Response({'code': 400, 'msg': _format_serializer_errors(exc.detail)}, status=400)
            except (ValueError, ProviderLifecycleError) as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from .referral_cycle import capture, lock
            raw_ids = request.data.get('provider_ids')
            if raw_ids is None:
                raw_ids = [request.data.get('provider_id')] if request.data.get('provider_id') else []
            try:
                snapshot = capture(request.account, request.legacy_user, raw_ids)
                from .services import ensure_provider_dispatch_locks
                ensure_provider_dispatch_locks(raw_ids)
                with transaction.atomic():
                    lock(snapshot)
                    request._ordinary_referral_lifecycle = snapshot
                    return self._post_impl(request)
            except (ProviderLifecycleError, ValueError) as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
        return self._post_impl(request)

    def _post_impl(self, request):
        if isinstance(request.data, Mapping) and 'preview_points_hundredths' in request.data:
            return Response({'code': 400, 'errorCode': 'POINTS_PAYMENT_UNAVAILABLE',
                             'msg': '积分支付尚未开放，请移除积分试算后重新确认'}, status=400)
        serializer = CreateOrderSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _format_serializer_errors(serializer.errors)})

        data = serializer.validated_data
        # Do not take payment for an order that must enter an automatic pool
        # when that pool cannot safely register and order requests.
        if (not data['provider_accounts'] and
                (data['dispatch_mode'] == 'AUTO_GRAB' or data['dispatch_route'] == Order.DispatchRoute.STAFF)):
            from .grab_single_flow import enabled as single_window_enabled
            from .grab_team_flow import enabled as team_window_enabled
            usable = team_window_enabled() if data['required_provider_count'] == 2 else single_window_enabled()
            if not usable:
                return Response({'code': 503, 'msg': '抢单暂不可用，请稍后下单或联系客服',
                                 'errorCode': 'GRAB_WINDOW_UNAVAILABLE'}, status=503)

        service = serializer.validated_data['service']
        game_rounds = serializer.validated_data['game_rounds']
        remark = serializer.validated_data['remark']
        game_region = serializer.validated_data['game_region']
        game_nickname = serializer.validated_data['game_nickname']
        game_uid = serializer.validated_data['game_uid']
        provider_gender = serializer.validated_data['provider_gender']
        amount = serializer.validated_data['amount']
        gift_balance_amount = serializer.validated_data['gift_balance_amount']
        compensation_balance_amount = serializer.validated_data['compensation_balance_amount']
        if gift_balance_amount + compensation_balance_amount > amount:
            return Response({'code': 400, 'msg': '赠送和赔付不能超过应付金额'})
        expected_amount = serializer.validated_data.get('expected_amount')
        if expected_amount is not None and expected_amount != amount:
            return Response({'code': 409, 'msg': '价格或优惠已变化，请刷新报价后重新确认',
                             'errorCode': 'PRICE_CHANGED'})
        provider = serializer.validated_data.get('provider')
        providers = serializer.validated_data['providers']
        provider_accounts = serializer.validated_data['provider_accounts']
        support_contact = serializer.validated_data.get('support_contact')
        user_coupon = serializer.validated_data.get('user_coupon')
        commission_rate = serializer.validated_data['commission_rate']
        inviter = serializer.validated_data.get('inviter')
        provider_account = provider_accounts[0] if provider_accounts else None
        inviter_account = serializer.validated_data['inviter_account']
        inviter_commission_rate = serializer.validated_data['inviter_commission_rate']
        promotion = serializer.validated_data.get('promotion')
        original_amount = serializer.validated_data['original_amount']
        boss_discount = serializer.validated_data['boss_discount']
        promo_discount = serializer.validated_data['promo_discount']
        coupon_discount = serializer.validated_data['coupon_discount']

        points_token = serializer.validated_data.get('points_checkout_test_token')
        points_quote = None
        if points_token:
            from .points_business_access import checkout_shape_allowed, permits
            if (permits(account=request.account, service=service, phase='checkout', kind='service')
                    and not checkout_shape_allowed(serializer.validated_data)):
                raise rest_serializers.ValidationError('当前订单暂不能使用积分抵扣，请调整下单方式或联系门店')
            from .points_checkout_test import verify_checkout_quote
            try:
                points_quote, points_receipt_token = verify_checkout_quote(request.account, serializer.validated_data, points_token)
            except rest_serializers.ValidationError as exc:
                return Response({'code': 400, 'msg': _format_serializer_errors(exc.detail)}, status=400)
            from .grab_team_flow import enabled as team_grab_enabled
            from .grab_single_flow import enabled as single_grab_enabled
            from .points_checkout_test import assignment_enabled as points_assignment_enabled
            pool_requested = (serializer.validated_data['dispatch_mode'] == 'AUTO_GRAB'
                              or serializer.validated_data['dispatch_route'] == Order.DispatchRoute.STAFF)
            team_grab = (pool_requested
                         and serializer.validated_data['required_provider_count'] == 2
                         and team_grab_enabled() and points_assignment_enabled())
            single_grab = (pool_requested
                           and serializer.validated_data['required_provider_count'] == 1
                           and single_grab_enabled() and points_assignment_enabled())
            specified_points_assignment = (provider is not None
                        and serializer.validated_data['dispatch_mode'] == 'BOSS_SELECT'
                        and serializer.validated_data['dispatch_route'] == Order.DispatchRoute.PUBLIC
                        and points_assignment_enabled())
            if ((provider is not None and not specified_points_assignment)
                    or (provider is None and not (team_grab or single_grab)
                        and (serializer.validated_data['dispatch_route'] != Order.DispatchRoute.PUBLIC
                             or serializer.validated_data['dispatch_mode'] != 'BOSS_SELECT'))):
                raise rest_serializers.ValidationError('积分订单派单与收益尚未接通')
            amount = points_quote['calculation']['payable_units']
            if gift_balance_amount + compensation_balance_amount > amount:
                raise rest_serializers.ValidationError('赠送和赔付不能超过积分抵扣后的应付金额')

        commission_spec = serializer.validated_data['commission_spec']
        commission_pending = serializer.validated_data.get('commission_allocation_pending', False)
        personal_game_snapshots = serializer.validated_data.get('personal_game_snapshots')
        from .delta_personal_commission import mixed_funding_available, points_funding_available
        mixed_unavailable = bool((gift_balance_amount or compensation_balance_amount) and not mixed_funding_available())
        from .points_business_access import permits as points_permits, checkout_shape_allowed
        formal_personal_points = bool(points_quote and personal_game_snapshots is not None
            and checkout_shape_allowed(serializer.validated_data)
            and points_permits(account=request.account, service=service, phase='checkout', kind='service'))
        formal_pending_points = bool(points_quote and commission_pending
            and checkout_shape_allowed(serializer.validated_data)
            and points_permits(account=request.account, service=service, phase='checkout', kind='service'))
        points_unavailable = bool(points_quote and not (points_funding_available() or formal_personal_points or formal_pending_points))
        safe_box_subsidy_basis = 0
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False) and not points_quote:
            from .purchase_benefits import vip_safe_box_subsidy_basis
            safe_box_subsidy_basis = vip_safe_box_subsidy_basis(
                request.account, service, game_rounds, user_coupon, serializer.validated_data['pricing'])
        if personal_game_snapshots is not None and (not providers or (amount <= 0 and not safe_box_subsidy_basis)
                                                     or points_unavailable or mixed_unavailable):
            raise rest_serializers.ValidationError('当前订单的陪玩资格或付款条件不满足，请重新选择或联系门店')
        if commission_pending:
            zero_pending = bool(amount == 0 and safe_box_subsidy_basis and not providers
                and serializer.validated_data['required_provider_count'] == 1
                and (serializer.validated_data['dispatch_mode'] == 'AUTO_GRAB'
                     or serializer.validated_data['dispatch_route'] == Order.DispatchRoute.STAFF))
            if zero_pending:
                from .grab_clock_contract import production_enabled
                from .grab_single_flow import enabled as single_window_enabled
                from .segment_financial_capture import subsidy_capture_enabled
                from .paid_service_scope import enabled as paid_scope_enabled
                zero_pending = bool(production_enabled() and single_window_enabled() and subsidy_capture_enabled()
                    and paid_scope_enabled() and getattr(settings, 'ORIGINAL_ATTRIBUTION_ENABLED', False))
            if providers or (amount <= 0 and not zero_pending) or points_unavailable or mixed_unavailable:
                raise rest_serializers.ValidationError('当前订单的派单或付款条件不满足，请重新选择或联系门店')
            from types import SimpleNamespace
            split = SimpleNamespace(commission_rate=None, provider_income=None,
                inviter_commission=amount * inviter_commission_rate // 100, shop_income=None)
        else:
            split = compute_split(amount, inviter_commission_rate=inviter_commission_rate, **commission_spec)
        selected_shares = None
        if len(providers) == 2:
            from .settlement import build_provider_shares, aggregate_order_split
            selected_shares = build_provider_shares(amount, serializer.validated_data['provider_specs'])
            split = aggregate_order_split(amount, selected_shares, inviter_commission_rate)
        trace_id = new_trace_id()

        # 只读探测重放：首单成功后余额已扣，重放会先撞「余额不足」返回 400，
        # 把「上一单其实已经下成功了」这个真实原因盖掉。先明确回 409。
        replayed, replay_reason = peek_fund_write(
            account=request.account,
            request_id=request.data.get('client_request_id'),
        )
        if replayed:
            log_money_event(
                'order.create.duplicate',
                account_id=request.account.pk,
                trace_id=trace_id,
                idempotent_hit=True,
                reason=replay_reason,
            )
            return Response({'code': 409, 'msg': replay_reason})

        from .paid_service_scope import capture as capture_paid_scope, verify as verify_paid_scope, record as record_paid_scope
        paid_scope = capture_paid_scope(service)
        ensure_provider_dispatch_locks(account.pk for account in provider_accounts)
        with transaction.atomic():
            referral_lifecycle = getattr(request, '_ordinary_referral_lifecycle', None)
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            if provider_accounts and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_active_provider_operations
                try:
                    eligible = lock_active_provider_operations(provider_accounts)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩状态暂不可核验，请稍后重试'}, status=503)
                if not eligible:
                    return Response({'code': 409, 'msg': '陪玩门店身份已变化，请重新选择'}, status=409)
            # Serialize a service's game mapping and the versioned store
            # dictionary with the final validation, before any funds move.
            locked_service = ServiceItem.objects.select_for_update().get(pk=service.pk)
            if locked_service.game_category_id != service.game_category_id:
                return Response({'code': 409, 'errorCode': 'SERVICE_CHANGED', 'msg': '服务资料规则已变化，请重新核对'}, status=409)
            if (locked_service.price != service.price
                    or locked_service.billing_provider_count != service.billing_provider_count
                    or locked_service.fulfillment_provider_count != serializer.validated_data['required_provider_count']):
                return Response({'code': 400, 'errorCode': 'SERVICE_CHANGED',
                                 'msg': '商品信息已变化，请刷新报价后重新确认'})
            from .account_fields import lock_and_validate
            try:
                lock_and_validate(locked_service, {'game_region': game_region, 'game_nickname': game_nickname, 'game_uid': game_uid})
            except rest_serializers.ValidationError as exc:
                return Response({'code': 400, 'msg': _format_serializer_errors(exc.detail)}, status=400)
            if paid_scope is not None:
                ServiceItem.objects.select_for_update().get(pk=service.pk)
            if serializer.validated_data.get('discount_selection_token'):
                # Lock service before wallet, consistently with other paid
                # service paths; final discount validation is under both.
                ServiceItem.objects.select_for_update().get(pk=service.pk)
            if getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False):
                # Keep the same service -> provider -> game rule lock order as
                # reservation conversion and staff dispatch.
                ServiceItem.objects.select_for_update().get(pk=service.pk)
            expected_service = serializer.validated_data.get('expected_service')
            if expected_service is not None:
                # Serialize admin edits with the final snapshot check and debit.
                locked_service = ServiceItem.objects.select_for_update().get(pk=service.pk)
                def snapshot(item):
                    return {'name': item.name, 'price_unit': item.price_unit,
                            'service_type': item.service_type, 'game_category': item.game_category_id,
                            'billing_provider_count': item.billing_provider_count, 'fulfillment_provider_count': item.fulfillment_provider_count, 'unit_price': item.price,
                            'gameplay': item.gameplay_id, 'platform': item.platform,
                            'security_level': item.security_level, 'provider_kind': item.provider_kind,
                            'provider_grade': item.provider_grade, 'required_level': item.required_level_id,
                            'service_category': item.service_category_id, 'purchase_mode': item.purchase_mode,
                            'is_addon': item.is_addon, 'experience_kind': item.experience_kind,
                            'allows_discount_coupons': item.allows_discount_coupons}
                current = snapshot(locked_service)
                if (not locked_service.is_active
                        or any(current[key] != value for key, value in expected_service.items())
                        or current != snapshot(service)):
                    return Response({'code': 400, 'errorCode': 'SERVICE_CHANGED',
                                     'msg': '商品信息已变化，请刷新报价后重新确认'})
            expected_quote = serializer.validated_data.get('expected_quote')
            if expected_quote is not None and any(
                    serializer.validated_data[key] != value for key, value in expected_quote.items()):
                return Response({'code': 400, 'msg': '价格或优惠已变化，请刷新报价后重新确认',
                                 'errorCode': 'PRICE_CHANGED'})
            if provider_accounts:
                lock_provider_dispatch([account.pk for account in provider_accounts])
            locked_personal_profiles = {}
            if personal_game_snapshots is not None:
                from .delta_personal_commission import lock_providers
                try:
                    locked_personal_profiles = {p.account_id: p for p in lock_providers(
                        [account.pk for account in provider_accounts])}
                except ValueError as exc:
                    return Response({'code': 409, 'msg': str(exc)}, status=409)
            for selected_account in provider_accounts:
                current_profile = EscortProfile.objects.select_related('account', 'user', 'level').get(account=selected_account)
                if current_profile.status == EscortProfile.Status.OFFLINE:
                    return Response({'code': 400, 'msg': '指定的大神当前不可接单'})
                reason = current_profile.order_acceptance_error(service, provider_gender=provider_gender,
                                                                  allow_refund_recovery=True)
                if reason:
                    return Response({'code': 400, 'msg': reason})
                if count_capacity_orders(selected_account) >= current_capacity_limit():
                    return Response({'code': 400, 'msg': '该大神接单名额已满，请重新选择'})
            if getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False):
                from .game_commission import lock_and_verify_payment_snapshot
                try:
                    if commission_pending:
                        from .delta_personal_commission import lock_and_verify_pending_snapshot
                        lock_and_verify_pending_snapshot(service.pk,
                            serializer.validated_data['commission_snapshot']['game_commission_snapshot'])
                    elif personal_game_snapshots is not None:
                        for account, expected in zip(provider_accounts, personal_game_snapshots):
                            lock_and_verify_payment_snapshot(service.pk, expected,
                                provider=locked_personal_profiles[account.pk])
                        from .delta_personal_commission import verify_payment_shares
                        specs = (serializer.validated_data['provider_specs'] if len(provider_accounts) == 2
                                 else [serializer.validated_data['commission_spec']])
                        verify_payment_shares(provider_accounts, personal_game_snapshots, specs)
                    else:
                        lock_and_verify_payment_snapshot(service.pk,
                            serializer.validated_data['commission_snapshot']['game_commission_snapshot'])
                        from .game_commission import verify_payment_shares
                        specs = (serializer.validated_data['provider_specs'] if len(provider_accounts) == 2
                                 else [serializer.validated_data['commission_spec']] if provider_accounts else [])
                        verify_payment_shares(serializer.validated_data['commission_snapshot']['game_commission_snapshot'], specs)
                except ValueError as exc:
                    return Response({'code': 409, 'errorCode': 'COMMISSION_CHANGED', 'msg': str(exc)}, status=409)
            if formal_personal_points or formal_pending_points:
                from .delta_personal_commission import verify_formal_points_checkout
                verify_formal_points_checkout(serializer.validated_data, account=request.account, service=locked_service)
            try:
                verify_paid_scope(service.pk, paid_scope)
            except ValueError as exc:
                return Response({'code': 409, 'errorCode': 'SERVICE_CHANGED', 'msg': str(exc)}, status=409)
            if referral_lifecycle is not None:
                from .referral_cycle import validate
                buyer_locked = ClubAccount.objects.select_for_update().get(pk=request.account.pk)
                try:
                    validate(referral_lifecycle, buyer_locked, request.legacy_user, provider_accounts)
                except ValueError as exc:
                    return Response({'code': 409, 'msg': str(exc)}, status=409)
            wallet = get_wallet(
                account=request.account, user=request.legacy_user, for_update=True,
            )
            if not wallet.is_active:
                return Response({'code': 403, 'msg': '钱包不可用'})
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': f'{current_currency_name()}不足，请联系企业微信客服充值'})

            from .timeout import current_timeout_config
            # The committed value observed here is the new order's immutable
            # snapshot; configuration edits need not serialize all payments.
            timeout_minutes = current_timeout_config().minutes
            expected_timeout = serializer.validated_data.get('expected_timeout_minutes')
            if expected_timeout is not None and expected_timeout != timeout_minutes:
                return Response({'code': 409, 'errorCode': 'TIMEOUT_CHANGED',
                                 'msg': '待接单时限已变化，请刷新后重新确认'})

            from .purchase_benefits import lock_purchase, record_paid_benefits
            discount_token = serializer.validated_data.get('discount_selection_token')
            if discount_token:
                from .discount_selection import lock_selected_discount
                selected, locked_promotion, locked_coupon = lock_selected_discount(request.account, service, game_rounds, discount_token)
                expected = serializer.validated_data['pricing']
                if ((selected.original_amount, selected.boss_discount, selected.promo_discount,
                     selected.coupon_discount, selected.amount) != (expected.original_amount, expected.boss_discount,
                     expected.promo_discount, expected.coupon_discount, expected.amount)
                        or getattr(locked_promotion, 'pk', None) != getattr(promotion, 'pk', None)):
                    raise rest_serializers.ValidationError('优惠条件已变化，请重新报价')
            else:
                locked_coupon = lock_purchase(request.account, service, game_rounds,
                    user_coupon, serializer.validated_data['pricing'], expected_promotion=promotion)
            if safe_box_subsidy_basis and vip_safe_box_subsidy_basis(
                    request.account, service, game_rounds, locked_coupon,
                    serializer.validated_data['pricing']) != safe_box_subsidy_basis:
                raise rest_serializers.ValidationError('安全箱券或补贴条件已变化，请重新报价')
            # 下单是出账动作。幂等键在「校验全过、即将扣款」这一刻抢占：
            # 弱网重试的第二个请求会在这里被拦下，而不是变成两笔扣款 + 两张单。
            # 放在校验之后，是因为视图里的 `return Response(400)` 属于正常返回，
            # atomic 会照常提交 —— 提前占键会让余额不足的用户充值后无法重试。
            allowed, reason = claim_fund_write(
                account=request.account,
                request_id=request.data.get('client_request_id'),
                scope='order.create',
                fingerprint=(f'{service.id}|{amount}|{game_rounds}|{game_uid}|{provider_gender}|'
                    f'{",".join(str(account.pk) for account in provider_accounts)}'
                    f'|support={support_contact.pk if support_contact else ""}'
                    f'|gift={gift_balance_amount}|compensation={compensation_balance_amount}'
                    f'|discount={serializer.validated_data.get("discount_option_key", "legacy")}'
                    + (f'|points={points_quote["calculation"]["points_hundredths"]}'
                       f'|points_discount={points_quote["calculation"]["discount_units"]}' if points_quote else '')
                    + ('|dispatch_route=STAFF' if serializer.validated_data['dispatch_route'] == Order.DispatchRoute.STAFF else '')),
            )
            if not allowed:
                log_money_event(
                    'order.create.duplicate',
                    account_id=request.account.pk,
                    amount=amount,
                    trace_id=trace_id,
                    idempotent_hit=True,
                    reason=reason,
                )
                return Response({'code': 409, 'msg': reason})

            from support.assignment import selected_card
            support_contact = selected_card(support_contact.pk, lock=True,
                expected_staff_id=support_contact.staff_account_id) if support_contact else None
            balance_before = wallet.balance

            order = Order.objects.create(
                customer=request.legacy_user,
                customer_account=request.account,
                provider=None,
                escort_mode=(Order.EscortMode.DOUBLE if serializer.validated_data['required_provider_count'] == 2
                             else Order.EscortMode.SINGLE),
                service=service,
                support_contact=support_contact,
                responsible_staff_id=support_contact.staff_account_id if support_contact else None,
                amount=amount,
                game_rounds=game_rounds,
                remark=remark,
                game_region=game_region,
                game_nickname=game_nickname,
                game_uid=game_uid,
                provider_gender=provider_gender,
                dispatch_route=serializer.validated_data['dispatch_route'],
                status=Order.Status.PENDING,
                payment_status=Order.PaymentStatus.PAID,
                pending_timeout_minutes=timeout_minutes,
                # Keep the team-fulfilment contract used after selecting a
                # two-person application: each member supplies their evidence.
                requires_boss_selection=(provider is None or len(providers) == 2)
                    and serializer.validated_data['dispatch_mode'] == 'BOSS_SELECT'
                    and serializer.validated_data['dispatch_route'] == Order.DispatchRoute.PUBLIC,
                auto_cancel_at=(
                    None if provider is not None
                    else pending_timeout_deadline(minutes=timeout_minutes)
                ),
                original_amount=original_amount,
                boss_discount=boss_discount,
                promo_discount=promo_discount,
                coupon_discount=coupon_discount,
                promotion=promotion,
                **serializer.validated_data['commission_snapshot'],
                commission_allocation_pending=commission_pending,
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter=inviter,
                inviter_account=inviter_account,
                inviter_commission=split.inviter_commission,
                shop_income=split.shop_income,
            )
            if referral_lifecycle is not None:
                from .referral_cycle import record
                record(order, referral_lifecycle)
            from .grab_pool_snapshot import initialize_entry
            try:
                grab_pool = initialize_entry(order)
            except ValueError as exc:
                raise rest_serializers.ValidationError(str(exc)) from exc
            record_paid_scope(order, paid_scope)
            from .original_attribution import payment_created
            # A points order cannot capture its deal basis until its payment
            # and points evidence have been written in this transaction.
            if not points_quote:
                payment_created(order)
            if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
                from .purchase_benefits import vip_safe_box_subsidy_basis
                from .models import OrderPlatformSubsidy
                basis = vip_safe_box_subsidy_basis(request.account, service, game_rounds,
                                                   locked_coupon, serializer.validated_data['pricing'])
                if basis:
                    subsidy = OrderPlatformSubsidy.objects.create(order=order, coupon_id_snapshot=locked_coupon.pk, basis=basis)
                    from .segment_financial_capture import register_subsidy
                    register_subsidy(order, subsidy)
            from engagement.service_funding_route import use_order_funding_allocation
            funding_allocated = use_order_funding_allocation(amount=amount,
                    funding_selection_enabled=getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False))
            if funding_allocated:
                from engagement.funding_wallet import allocate_locked_order_funding, snapshot_service_funding_terms
                allocate_locked_order_funding(order, wallet, gift=gift_balance_amount, compensation=compensation_balance_amount,
                                              points_checkout=bool(points_quote))
            else:
                from engagement.services import allocate_order_points
                allocate_order_points(order, wallet, balance_before)
            wallet.balance -= amount
            wallet.save(update_fields=['balance'])
            payment = Transaction.objects.create(
                wallet=wallet,
                order=order,
                amount=-amount,
                tx_type=Transaction.TxType.PAY,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=f'订单支付：{order.order_no}',
            )
            if funding_allocated:
                snapshot_service_funding_terms(order, paid_at=payment.created_at)
            if points_quote:
                from .points_service_payment import record_locked
                record_locked(order_id=order.pk, payment_id=payment.pk, token=points_receipt_token)
                order.refresh_from_db()
                payment_created(order)
            if amount == 0:
                # Opening verifies the consumed benefits and zero-payment
                # ledger in the same transaction.
                record_paid_benefits(order, locked_coupon)
            from .grab_opening_intent import capture_isolated_payment
            capture_isolated_payment(order, payment, grab_pool)
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
            log_money_event(
                'order.create',
                account_id=request.account.pk,
                user_id=getattr(request.legacy_user, 'pk', None),
                order_no=order.order_no,
                order_id=order.pk,
                tx_type=Transaction.TxType.PAY,
                amount=-amount,
                balance_before=balance_before,
                balance_after=wallet.balance,
                trace_id=trace_id,
            )

            if amount != 0:
                record_paid_benefits(order, locked_coupon)

            log_only(
                order,
                action=OrderStatusLog.Action.CREATE,
                operator=request.legacy_user,
                operator_account=request.account,
                reason='下单',
            )

            if provider is not None:
                def assign_selected(o):
                    o.provider = provider
                    o.provider_account = provider_account
                    o.provider_name_snapshot = (
                        provider.escort_profile.display_name
                        or provider.nickname or provider.username
                    )
                    if selected_shares is None:
                        sync_single_provider_share(o)
                    else:
                        for index, (selected, selected_account, share) in enumerate(zip(providers, provider_accounts, selected_shares)):
                            OrderProvider.objects.create(
                                order=o, provider=selected, provider_account=selected_account,
                                provider_business_cycle=selected_account.business_cycle,
                                provider_name_snapshot=selected.escort_profile.display_name or selected.nickname or selected.username,
                                settlement_base=share.settlement_base,
                                commission_type=share.commission_type,
                                commission_rate=share.commission_rate,
                                game_commission_snapshot=(personal_game_snapshots[index]
                                    if personal_game_snapshots is not None else o.game_commission_snapshot),
                                commission_fixed=share.commission_fixed,
                                provider_income=share.provider_income,
                            )
                    from wallet.refund_recovery_order import enabled as recovery_grab_enabled, reserve_many
                    if recovery_grab_enabled():
                        try:
                            reserve_many(serializer.validated_data['provider_profiles'], o)
                        except ValueError as exc:
                            raise IllegalTransitionError(str(exc)) from exc
                    mark_escort_busy(
                        provider_ids=[selected.pk for selected in providers],
                        account_ids=[account.pk for account in provider_accounts],
                    )

                try:
                    order = transition(
                        order.id,
                        Order.Status.GRABBED,
                        operator=request.legacy_user,
                        operator_account=request.account,
                        action=OrderStatusLog.Action.ASSIGN,
                        reason='老板指定陪玩',
                        side_effect=assign_selected,
                        update_fields=['provider', 'provider_account', 'provider_name_snapshot'],
                    )
                except IllegalTransitionError as exc:
                    transaction.set_rollback(True)
                    return Response({'code': 400, 'msg': str(exc) or '指定陪玩失败'})

                from site_messages.order_assignment_notice import enabled as assignment_notice_enabled, record_all, schedule_immediate
                if assignment_notice_enabled():
                    assignment_log = order.status_logs.filter(action=OrderStatusLog.Action.ASSIGN,
                        from_status=Order.Status.PENDING, to_status=Order.Status.GRABBED,
                        operator_account=request.account, reason='老板指定陪玩').order_by('-pk').first()
                    if not assignment_log:
                        raise ValueError('指定订单事件待核对')
                    record_all(order, payment, assignment_log, [item.pk for item in provider_accounts])
                else:
                    schedule_immediate(order)

            result = fund_success(request, 'order.create', msg='下单成功',
                                  data=OrderSerializer(order, context={'request': request}).data)

        if provider is None:
            _schedule_auto_cancel(order)
            if order.dispatch_route == Order.DispatchRoute.PUBLIC and not order.requires_boss_selection:
                enqueue_kook_dispatch(order, KookDispatchRecord.Trigger.NEW_ORDER)
                enqueue_wecom_dispatch(order, KookDispatchRecord.Trigger.NEW_ORDER)
        notify_order_update(order)

        return result


class ResolveOrderRequestView(APIView):
    """Serialize resolution with order.create's wallet lock; seal only an unused key."""
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '仅下单老板可以核对请求'}, status=403)
        request_id = request.data.get('client_request_id')
        payload = request.data.get('payload')
        if (not isinstance(request_id, str) or not request_id or request_id != request_id.strip()
                or len(request_id) > 64 or request_id.startswith('auto:')
                or not isinstance(payload, dict) or payload.get('client_request_id') != request_id):
            return Response({'code': 400, 'msg': '请提供原下单请求和有效交易编号'}, status=400)
        # Preserve any deployment SCRIPT_NAME prefix used by the original URL.
        create_path = request.path[:-len('resolve/')]
        fingerprint = fingerprint_for(create_path, payload)
        with transaction.atomic():
            # Same wallet row and lock order as CreateOrderView before claiming the key.
            get_wallet(account=request.account, user=request.legacy_user, for_update=True)
            receipt, created = IdempotencyKey.objects.get_or_create(
                account=request.account, request_id=request_id,
                defaults={'scope': 'order.create.sealed', 'request_fingerprint': fingerprint},
            )
            if created:
                return Response({'code': 0, 'data': {'status': 'SEALED'}})
            if receipt.scope not in ('order.create', 'order.create.sealed') or (
                    receipt.request_fingerprint and receipt.request_fingerprint != fingerprint):
                return Response({'code': 409, 'errorCode': 'REQUEST_ID_CONFLICT',
                                 'msg': '交易编号已用于其他操作，请联系客服核对'}, status=409)
            if not receipt.request_fingerprint:
                return Response({'code': 409, 'errorCode': 'FUND_RESULT_UNAVAILABLE',
                                 'msg': '无法确认历史交易结果，请联系客服核对'}, status=409)
            if receipt.scope == 'order.create.sealed':
                return Response({'code': 0, 'data': {'status': 'SEALED'}})
            if receipt.response is None:
                return Response({'code': 409, 'errorCode': 'FUND_RESULT_UNAVAILABLE',
                                 'msg': '无法确认历史交易结果，请联系客服核对'}, status=409)
            return Response({'code': 0, 'data': {'status': 'COMMITTED', 'fundResult': receipt.response}})


class OrderDiscountOptionsPreviewView(APIView):
    """Opt-in, read-only recommendation preview; never authorizes a debit."""
    throttle_classes = [OrderTrafficThrottle]
    traffic_action = 'quote'
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        payment_enabled = bool(getattr(settings, 'DISCOUNT_COMBINATIONS_PAYMENT_ENABLED', False))
        if not getattr(settings, 'DISCOUNT_OPTIONS_PREVIEW_ENABLED', False) and not payment_enabled:
            return Response({'code': 404, 'msg': '优惠组合预览尚未开放',
                             'errorCode': 'DISCOUNT_PREVIEW_DISABLED'}, status=404)
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '仅老板本人可查看优惠组合'}, status=403)
        try:
            service_id = rest_serializers.IntegerField(min_value=1).run_validation(request.data.get('service_id'))
            units = rest_serializers.IntegerField(min_value=1, max_value=999).run_validation(request.data.get('game_rounds', 1))
        except rest_serializers.ValidationError:
            return Response({'code': 400, 'msg': '请选择有效的服务及数量'}, status=400)
        from .catalog import visible_service_q
        service = ServiceItem.objects.filter(visible_service_q(), pk=service_id).select_related('service_category', 'gameplay').first()
        if service is None:
            return Response({'code': 404, 'msg': '服务不存在或不可用'}, status=404)
        from .purchase_benefits import is_experience
        if is_experience(service) or (service.service_category_id and service.service_category.is_gift) or service.is_addon:
            return Response({'code': 0, 'data': {'applicable': False, 'options': [],
                'payment_selection_enabled': False, 'preview_only': True}})
        from dataclasses import asdict
        from .discount_recommendation import service_discount_options
        from .discount_selection import signed_option
        options = service_discount_options(request.account, service, units)
        return Response({'code': 0, 'data': {'applicable': True, 'options': [
            {**asdict(item), 'selection_token': signed_option(request.account, service, units, item)} for item in options],
            'recommended_key': options[0].key, 'preview_only': not payment_enabled,
            'payment_selection_enabled': payment_enabled}})


class OrderQuoteView(APIView):
    throttle_classes = [OrderTrafficThrottle]
    traffic_action = 'quote'
    """下单试算：与正式下单复用同一校验和计价器，但不扣款、不占券。"""

    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        preview_requested = isinstance(request.data, Mapping) and 'preview_points_hundredths' in request.data
        selected_points = request.data.get('preview_points_hundredths') if preview_requested else None
        if preview_requested:
            if not getattr(settings, 'POINTS_REDEMPTION_PREVIEW_ENABLED', False):
                return Response({'code': 400, 'errorCode': 'POINTS_PREVIEW_DISABLED',
                                 'msg': '积分试算尚未开放'}, status=400)
            if request.account.account_type != ClubAccount.AccountType.BOSS:
                return Response({'code': 403, 'msg': '仅老板本人可查看积分试算'}, status=403)
            if type(selected_points) is not int or not 0 <= selected_points <= 2147483647:
                return Response({'code': 400, 'msg': '请选择有效的积分数量'}, status=400)
        serializer = CreateOrderSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _format_serializer_errors(serializer.errors)})

        data = serializer.validated_data
        wallet = get_wallet(user=request.legacy_user, account=request.account)
        promotion = data.get('promotion')
        amount = data['amount']
        points_preview = None
        if preview_requested:
            from .points_redemption_preview import preview_service_points
            try:
                points_preview = preview_service_points(account=request.account, service=data['service'],
                                                        deal_amount=amount, selected_points=selected_points)
            except ValueError as exc:
                return Response({'code': 409, 'errorCode': 'POINTS_PREVIEW_UNAVAILABLE',
                                 'msg': str(exc)}, status=409)
            if getattr(settings, 'SERVICE_POINTS_CHECKOUT_TESTING', False) and points_preview.get('selected_groups'):
                from .points_checkout_test import quote_for_checkout, require_isolated_checkout
                require_isolated_checkout()
                _, points_preview['checkout_test_token'] = quote_for_checkout(request.account, data, selected_points)
                points_preview['checkout_test_only'] = True
            elif getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True and points_preview.get('selected_groups'):
                from .points_business_access import checkout, checkout_shape_allowed
                if checkout_shape_allowed(data):
                    if data.get('commission_allocation_pending'):
                        from .grab_single_flow import enabled as single_grab_enabled
                        from .grab_team_flow import enabled as team_grab_enabled
                        grab_ready = team_grab_enabled() if data['required_provider_count'] == 2 else single_grab_enabled()
                        if not grab_ready:
                            raise rest_serializers.ValidationError('抢单暂不可用，请稍后重新报价')
                    from .points_checkout_test import quote_for_checkout
                    from engagement.funding_wallet import funding_quote as points_funding_quote
                    try:
                        if data.get('personal_game_snapshots') is not None:
                            from .services import ensure_provider_dispatch_locks
                            ensure_provider_dispatch_locks([item.pk for item in data['provider_accounts']])
                        with transaction.atomic(), checkout(account=request.account, service=data['service']):
                            if data.get('personal_game_snapshots') is not None or data.get('commission_allocation_pending'):
                                from .services import lock_provider_dispatch
                                from .delta_personal_commission import verify_formal_points_checkout
                                locked_service = ServiceItem.objects.select_for_update().get(pk=data['service'].pk)
                                if data['provider_accounts']:
                                    lock_provider_dispatch([item.pk for item in data['provider_accounts']])
                                verify_formal_points_checkout(data, account=request.account, service=locked_service)
                            calculation, token = quote_for_checkout(request.account, data, selected_points)
                            current_funding = points_funding_quote(wallet, calculation['calculation']['payable_units'],
                                data['gift_balance_amount'], data['compensation_balance_amount'])
                    except rest_serializers.ValidationError as exc:
                        return Response({'code': 409, 'msg': _format_serializer_errors(exc.detail)}, status=409)
                    points_preview.update(checkout_test_token=token, checkout_test_only=False, preview_only=False,
                        payment_available=current_funding['balance_sufficient'],
                        notice='报价按当前余额与规则计算，付款时会重新核对')
        gift = data['gift_balance_amount']
        compensation = data['compensation_balance_amount']
        from engagement.funding_wallet import funding_quote
        funding = funding_quote(wallet, amount, gift, compensation)
        from .timeout import current_timeout_config
        recommended = None
        from .purchase_benefits import is_experience
        service = data['service']
        if not is_experience(service) and not service.is_addon and not (
                service.service_category_id and service.service_category.is_gift):
            from dataclasses import asdict
            from .discount_recommendation import service_discount_options
            recommended = asdict(service_discount_options(request.account, service, data['game_rounds'])[0])
        return Response({'code': 0, 'data': {
            'original_amount': data['original_amount'],
            'boss_discount': data['boss_discount'],
            'promo_discount': data['promo_discount'],
            'coupon_discount': data['coupon_discount'],
            'amount': amount,
            'pending_timeout_minutes': current_timeout_config().minutes,
            'promotion': ({'id': promotion.id, 'title': promotion.title} if promotion else None),
            'user_coupon_id': data['user_coupon'].pk if data.get('user_coupon') else None,
            'recommended_discount': recommended,
            'wallet_balance': wallet.balance,
            **({'points_preview': points_preview} if preview_requested else {}),
            **funding,
        }})


class GiftContextView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        from .gifts import gift_context, gift_available, recipient_data, GIFT_COMMISSION_THRESHOLD, GIFT_COMMISSION_RATE, MAX_GIFT_QUANTITY
        from stores.license_benefits import check_new_benefits
        license_admission, _ = check_new_benefits('GIFTS')
        if license_admission == 'UNKNOWN':
            return Response({'code': 503, 'errorCode': 'LICENSE_UNAVAILABLE',
                             'msg': '本店授权状态暂时无法核验，请稍后重试或联系门店'}, status=503)
        try:
            if request.query_params.get('browse') == '1' and not request.query_params.get('order_id') and not request.query_params.get('provider_id'):
                from users.models import EscortProfile
                if request.account.account_type != ClubAccount.AccountType.BOSS:
                    raise rest_serializers.ValidationError('仅老板可以赠送礼物')
                source = None
                recipients = EscortProfile.objects.select_related('account', 'user').filter(
                    is_verified=True, account__account_type=ClubAccount.AccountType.PROVIDER,
                    account__is_active=True, account__can_login=True, account__can_view=True)
                search = request.query_params.get('search', '').strip()[:50]
                if search:
                    recipients = recipients.filter(Q(display_name__icontains=search) | Q(account__nickname__icontains=search))
                recipients = list(recipients.order_by('id')[:50])
            else:
                source, recipients = gift_context(request, order_id=request.query_params.get('order_id'),
                                                  provider_id=request.query_params.get('provider_id'))
        except rest_serializers.ValidationError as exc:
            return Response({'code': 400, 'msg': str(exc.detail[0])})
        gifts = ServiceItem.objects.filter(
            is_active=True, service_category__is_active=True, service_category__is_gift=True,
            purchase_mode='DIRECT', is_addon=False, gameplay__isnull=True,
        ).select_related('service_category', 'game_category', 'required_level').order_by('sort_order', 'id')
        wallet = get_wallet(account=request.account, user=request.legacy_user)
        from engagement.services import wallet_funding_parts
        principal = wallet_funding_parts(wallet)['prepaid_balance']
        return Response({'code': 0, 'data': {
            'source_order': {'id': source.pk, 'order_no': source.order_no} if source else None,
            'recipients': [recipient_data(item) for item in recipients],
            'gifts': ServiceItemSerializer([gift for gift in gifts if gift_available(gift)]
                                           if license_admission == 'ALLOW' else [], many=True).data,
            'gifting_available': license_admission == 'ALLOW',
            'gifting_unavailable_reason': ('' if license_admission == 'ALLOW' else
                                           '本店当前授权暂不支持新增赠礼，请联系门店管理员'),
            'balance': wallet.balance, 'prepaid_balance': principal, 'wallet_active': wallet.is_active,
            'commission_threshold': GIFT_COMMISSION_THRESHOLD,
            'default_commission_rate': GIFT_COMMISSION_RATE, 'max_quantity': MAX_GIFT_QUANTITY,
        }})


class TipOrderView(APIView):
    """直接赠礼与已完成订单赠礼共用一次扣款、结算及结果恢复。"""

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    @staticmethod
    def _admission_refusal():
        from stores.license_benefits import check_new_benefits
        admission, _ = check_new_benefits('GIFTS')
        if admission == 'UNKNOWN':
            return Response({'code': 503, 'errorCode': 'LICENSE_UNAVAILABLE',
                             'msg': '本店授权状态暂时无法核验，请稍后重试或联系门店'}, status=503)
        if admission != 'ALLOW':
            return Response({'code': 403, 'errorCode': 'LICENSE_GIFTS_UNAVAILABLE',
                             'msg': '本店当前授权暂不支持新增赠礼，请联系门店管理员'}, status=403)

    @recover_fund_result('order.tip')
    def post(self, request, order_id=None):
        denied = self._admission_refusal()
        if denied is not None:
            return denied
        if 'items' in request.data:
            from .gift_basket import send_basket
            return send_basket(self, request, order_id=order_id)
        return self.create_one(request, order_id)

    def create_one(self, request, order_id=None):
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '仅老板可以赠送礼物'})
        from .gifts import gift_context, gift_commission_rate, positive_id, MAX_GIFT_QUANTITY, MAX_LEDGER_AMOUNT
        try:
            gift_service_id = positive_id(request.data.get('gift_service_id'), '礼物')
        except rest_serializers.ValidationError as exc:
            return Response({'code': 400, 'msg': str(exc.detail[0])})
        quantity = request.data.get('quantity', 1)
        if type(quantity) is not int or not 1 <= quantity <= MAX_GIFT_QUANTITY:
            return Response({'code': 400, 'msg': f'赠送数量须为 1–{MAX_GIFT_QUANTITY} 的整数'})

        trace_id = new_trace_id()
        # 只读探测重放：首单成功后余额已扣，重放会先撞「余额不足」返回 400，
        # 把「上一单其实已经下成功了」这个真实原因盖掉。先明确回 409。
        replayed, replay_reason = peek_fund_write(
            account=request.account,
            request_id=request.data.get('client_request_id'),
        )
        if replayed:
            log_money_event(
                'order.tip.duplicate',
                account_id=request.account.pk,
                trace_id=trace_id,
                idempotent_hit=True,
                reason=replay_reason,
            )
            return Response({'code': 409, 'msg': replay_reason})

        lifecycle_participants = None
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from .gift_basket import LineRequest
            if isinstance(request, LineRequest):
                lifecycle_participants = request.lifecycle_participants
                if lifecycle_participants is None:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系客服'}, status=503)
            else:
                from .gift_lifecycle import participants
                try:
                    lifecycle_participants = participants(request, order_id)
                except rest_serializers.ValidationError as exc:
                    return Response({'code': 400, 'msg': str(exc.detail[0])})

        with transaction.atomic():
            if lifecycle_participants and not isinstance(request, LineRequest):
                from club_accounts.provider_lifecycle import ProviderLifecycleError
                from .gift_lifecycle import lock_and_check
                try:
                    active = lock_and_check(*lifecycle_participants)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系客服'}, status=503)
                if not active:
                    return Response({'code': 409, 'msg': '收礼陪玩状态已变化，请重新选择'}, status=409)
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            try:
                # A source-order refund uses order -> money mutex -> wallets.
                # Gifting follows that direction so neither can hold the
                # buyer wallet while waiting for the other's source order.
                source_order, recipients = gift_context(request, order_id=order_id,
                    provider_id=request.data.get('provider_id'), lock=True)
                from .financial_lock import lock_order_money
                lock_order_money()
                customer_wallet = get_wallet(user=request.legacy_user, account=request.account, for_update=True)
                gift_service = ServiceItem.objects.select_for_update().select_related('service_category').get(
                    id=gift_service_id, is_active=True, service_category__is_active=True,
                    service_category__is_gift=True, purchase_mode='DIRECT', is_addon=False, gameplay__isnull=True,
                )
            except rest_serializers.ValidationError as exc:
                return Response({'code': 400, 'msg': str(exc.detail[0])})
            except ServiceItem.DoesNotExist:
                return Response({'code': 400, 'msg': '礼物不存在或已下架'})
            from .gifts import gift_available, naming_title
            if not gift_available(gift_service):
                return Response({'code': 400, 'msg': '该限定礼物不在售卖时间内或价格未符合起步价'})
            try:
                title = naming_title(request.data.get('naming_title', ''), required=gift_service.gift_naming_days > 0)
            except rest_serializers.ValidationError as exc:
                return Response({'code': 400, 'msg': str(exc.detail[0])})
            selected_id = request.data.get('provider_id')
            if selected_id is None and len(recipients) > 1:
                return Response({'code': 400, 'msg': '请选择本次服务的收礼陪玩'})
            profile = next((item for item in recipients if item.account_id == int(selected_id)), recipients[0]) if selected_id is not None else recipients[0]
            if lifecycle_participants and profile.account_id != lifecycle_participants[0].pk:
                return Response({'code': 409, 'msg': '收礼陪玩已变化，请重新选择'}, status=409)
            from wallet.refund_deposit import protection_enabled as deficit_protected
            if deficit_protected():
                profile = EscortProfile.objects.select_for_update().get(pk=profile.pk)
            provider, provider_account = profile.user, profile.account
            amount = gift_service.price * quantity
            if not 0 < amount <= MAX_LEDGER_AMOUNT:
                return Response({'code': 400, 'msg': '礼物总金额超出支持范围'})
            expected_amount = request.data.get('expected_amount')
            if expected_amount is not None:
                if isinstance(expected_amount, bool) or not isinstance(expected_amount, int) or expected_amount < 0:
                    return Response({'code': 400, 'msg': '礼物确认金额无效'})
                if expected_amount != amount:
                    return Response({'code': 409, 'msg': '礼物价格已变化，请刷新后重新确认',
                                     'errorCode': 'PRICE_CHANGED'})
            if not customer_wallet.is_active:
                return Response({'code': 403, 'msg': '钱包不可用'})
            if customer_wallet.balance < amount:
                return Response({'code': 400, 'msg': f'{current_currency_name()}不足，请联系企业微信客服充值'})
            from engagement.services import wallet_funding_parts
            if wallet_funding_parts(customer_wallet)['prepaid_balance'] < amount:
                return Response({'code': 400, 'msg': '本金余额不足，礼物只能使用本金支付'})

            commission_rate = gift_commission_rate(gift_service, amount)
            # Referral commission applies to service orders only.  A gift is
            # an independent order and its entire platform cut stays with the store.
            split = compute_split(amount, commission_rate)
            provider_wallet = get_wallet(user=provider, account=provider_account, for_update=True)
            if not provider_wallet.is_active:
                return Response({'code': 400, 'msg': '收礼陪玩钱包不可用'})
            if provider_wallet.balance + split.provider_income > MAX_LEDGER_AMOUNT:
                return Response({'code': 400, 'msg': '收礼陪玩余额超出支持范围，请联系客服'})
            if provider_wallet.frozen_amount + split.provider_income > MAX_LEDGER_AMOUNT:
                return Response({'code': 400, 'msg': '收礼陪玩冻结余额超出支持范围，请联系客服'})

            # 资金守恒闸门：礼物单同样必须满足
            # 陪玩实得 + 推荐分佣 + 平台留存 <= 老板实付。
            # 必须在建单之前校验 —— 视图里的 `return` 会让 atomic 正常提交，
            # 建完单再返回 400 会留下一张没付过钱的「已完成」礼物单。
            try:
                assert_order_conserved(
                    amount,
                    [split.provider_income],
                    split.inviter_commission,
                    split.shop_income,
                )
            except SettlementError as exc:
                return Response({'code': 400, 'msg': str(exc)})

            # 校验全部通过后再占幂等键：连点两次只会送出一份礼物。
            # （校验失败的 `return` 会正常提交事务，提前占键会误伤合法重试。）
            allowed, reason = claim_fund_write(
                account=request.account,
                request_id=request.data.get('client_request_id'),
                scope='order.tip',
                fingerprint=f'{order_id}|{provider_account.pk}|{gift_service_id}|{quantity}|{amount}',
            )
            if not allowed:
                log_money_event(
                    'order.tip.duplicate',
                    account_id=request.account.pk,
                    order_id=order_id,
                    trace_id=trace_id,
                    idempotent_hit=True,
                    reason=reason,
                )
                return Response({'code': 409, 'msg': reason})

            from engagement.models import ClubOperationConfig
            from engagement.services import config as operation_config
            operation_config()
            gift_vip_enabled = ClubOperationConfig.objects.select_for_update().get(pk=1).vip_enabled
            from stores.certificate_admission import enabled as certificate_enforcement_enabled
            if certificate_enforcement_enabled():
                denied = self._admission_refusal()
                if denied is not None:
                    transaction.set_rollback(True)
                    return denied
            now = timezone.now()
            # 礼物单必须和普通订单一样写全 account 维度。
            # 历史实现只写了 legacy user 外键，导致后台按 account 统计
            # （陪玩收益榜、老板消费榜、邀请人分佣报表）整批漏掉打赏流水 ——
            # 钱确实到账了，报表上却查无此单。
            gift_order = Order.objects.create(
                customer=request.legacy_user,
                customer_account=request.account,
                provider=provider,
                provider_account=provider_account,
                service=gift_service,
                gift_provider_business_cycle=(lifecycle_participants[0].business_cycle
                    if lifecycle_participants else provider_account.business_cycle),
                source_order=source_order,
                support_contact=source_order.support_contact if source_order else None,
                amount=amount,
                original_amount=amount,
                game_rounds=quantity,
                status=Order.Status.COMPLETED,
                payment_status=Order.PaymentStatus.PAID,
                gift_vip_enabled_snapshot=gift_vip_enabled,
                completed_at=now,
                settlement_mode=Order.SettlementMode.COMPLETION,
                settled_at=now,
                remark=f'赠送给{profile.display_name or provider.nickname or provider.username}，共 {quantity} 件',
                provider_name_snapshot=profile.display_name or provider.nickname or provider.username,
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter_commission=split.inviter_commission,
                shop_income=split.shop_income,
            )

            customer_before = customer_wallet.balance
            from engagement.services import allocate_order_points, award_order_points, award_gift_benefits
            allocate_order_points(gift_order, customer_wallet, customer_before, principal_only=True)
            award_order_points(gift_order)
            award_gift_benefits(gift_order)
            from engagement.services import grant_gift_naming
            grant_gift_naming(gift_order, title)
            customer_wallet.balance -= amount
            customer_wallet.save(update_fields=['balance'])
            gift_payment = Transaction.objects.create(
                wallet=customer_wallet, order=gift_order, amount=-amount,
                tx_type=Transaction.TxType.GIFT,
                balance_before=customer_before, balance_after=customer_wallet.balance,
                remark=f'礼物打赏：{gift_service.name}',
            )
            from engagement.services import confirm_gift_vip_consumption
            confirm_gift_vip_consumption(gift_order)
            log_money_event(
                'order.tip.pay',
                account_id=request.account.pk,
                user_id=getattr(request.legacy_user, 'pk', None),
                order_no=gift_order.order_no,
                order_id=gift_order.pk,
                tx_type=Transaction.TxType.GIFT,
                amount=-amount,
                balance_before=customer_before,
                balance_after=customer_wallet.balance,
                trace_id=trace_id,
            )

            provider_before = provider_wallet.balance
            provider_wallet.balance += split.provider_income
            provider_wallet.save(update_fields=['balance'])
            gift_income = Transaction.objects.create(
                wallet=provider_wallet, order=gift_order, amount=split.provider_income,
                tx_type=Transaction.TxType.GIFT,
                balance_before=provider_before, balance_after=provider_wallet.balance,
                remark=f'收到礼物：{gift_service.name}',
            )
            from wallet.income import hold_gift_income
            hold_gift_income(gift_income, paid_at=now, aftersale_hours=gift_order.gift_aftersale_hours,
                             freeze_hours=gift_order.gift_freeze_hours)
            log_money_event(
                'order.tip.income',
                account_id=getattr(provider_account, 'pk', None),
                user_id=provider.pk,
                order_no=gift_order.order_no,
                order_id=gift_order.pk,
                tx_type=Transaction.TxType.GIFT,
                amount=split.provider_income,
                balance_before=provider_before,
                balance_after=provider_wallet.balance,
                trace_id=trace_id,
            )

            changed_wallets = [customer_wallet, provider_wallet]
            if split.shop_income:
                platform_wallet = get_platform_wallet(for_update=True)
                platform_before = platform_wallet.balance
                platform_wallet.balance += split.shop_income
                platform_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=platform_wallet, order=gift_order, amount=split.shop_income,
                    tx_type=Transaction.TxType.SHOP_INCOME,
                    balance_before=platform_before, balance_after=platform_wallet.balance,
                    remark=f'礼物平台收入：{gift_order.order_no}',
                )
                changed_wallets.append(platform_wallet)
            from wallet.notifier import notify_wallet_update
            for wallet in {wallet.pk: wallet for wallet in changed_wallets}.values():
                transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
            log_only(
                gift_order, action=OrderStatusLog.Action.CREATE,
                operator=request.legacy_user, reason=f'完成订单赠礼，来源订单 {source_order.order_no}' if source_order else '老板直接赠礼',
            )
            from site_messages.gift_receipt_notice import enabled as gift_notice_enabled, record as record_gift_notice
            gift_preview = f'{request.account.nickname or getattr(request.legacy_user, "nickname", "") or "老板"}送给你「{gift_service.name}」×{quantity}'
            if gift_notice_enabled():
                record_gift_notice(gift_order, gift_payment, gift_income, preview=gift_preview)
            result = fund_success(request, 'order.tip', msg='礼物已送达',
                                  data=OrderSerializer(gift_order, context={'request': request}).data)

        if not gift_notice_enabled():
            guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            gift_cycle = gift_order.gift_provider_business_cycle
            if guarded and not gift_cycle:
                import logging
                logging.getLogger(__name__).warning('赠礼通知原周期待核对 order=%s', gift_order.pk)
            else:
                transaction.on_commit(lambda: _push_message_safe(
                    recipient_id=provider.id, recipient_account_id=provider_account.pk,
                    title='收到老板礼物', preview=gift_preview, msg_type='PROMOTION',
                    related_order_id=gift_order.id, recipient_business_cycle=gift_cycle, require_cycle=guarded))
        transaction.on_commit(lambda: notify_order_update(gift_order))
        return result


class GrabOrderView(APIView):
    throttle_classes = [OrderTrafficThrottle]
    traffic_action = 'grab'
    throttle_scope = 'order_write'
    permission_classes = [IsClubAccountAuthenticated]

    @grab_receipt
    def post(self, request, order_id):
        from .grab_single_flow import applies, submit
        if applies(order_id):
            return submit(request, order_id)
        if Order.objects.filter(pk=order_id, requires_boss_selection=True).exists():
            from .applications import ApplyOrderView
            return ApplyOrderView().post(request, order_id)
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可接单'})

        # The old direct grab would assign the first caller, bypassing the
        # ordered window. An unsnapshotted paid order remains cancellable by
        # its owner, but must not be falsely assigned without a round.
        if Order.objects.filter(
                pk=order_id, status=Order.Status.PENDING, payment_status=Order.PaymentStatus.PAID,
                provider__isnull=True, provider_account__isnull=True,
                requires_boss_selection=False, source_order__isnull=True, is_gift_snapshot=False,
                required_provider_count=1, escort_mode=Order.EscortMode.SINGLE).exists():
            # A concurrent opening may have committed after the route read.
            # The timed receipt must start outside the legacy transaction.
            if not transaction.get_connection().in_atomic_block and applies(order_id):
                return submit(request, order_id)
            return Response({'code': 503, 'msg': '抢单暂不可用，请联系客服核对订单'}, status=503)

        try:
            escort_profile = request.account.escort_profile
        except EscortProfile.DoesNotExist:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料，无法接单'})
        reason = escort_profile.order_acceptance_error(allow_refund_recovery=True)
        if reason:
            return Response({'code': 400, 'msg': reason})

        active_count = count_capacity_orders(request.account)
        capacity_limit = current_capacity_limit()
        if active_count >= capacity_limit:
            return Response({
                'code': 400,
                'msg': f'最多同时进行 {capacity_limit} 单，请完成当前订单后再接单',
            })

        ensure_provider_dispatch_locks([request.account.pk])

        def pre_check(order):
            from .models import OrderGrabPoolSnapshot
            if (order.dispatch_route != Order.DispatchRoute.PUBLIC
                    and not OrderGrabPoolSnapshot.objects.filter(order=order).exists()):
                raise IllegalTransitionError('该订单由客服派单')
            if order.provider_account_id is not None or order.provider_id is not None:
                raise IllegalTransitionError('订单已被其他大神接单')
            from .team_rules import required_team_size
            if required_team_size(order) != 1:
                raise IllegalTransitionError('双陪套餐须两名陪玩组队报名，不能单人直接抢单')
            if order.escort_mode != Order.EscortMode.SINGLE or order.source_order_id or order.is_gift_snapshot:
                raise IllegalTransitionError('该订单不能进入公共抢单流程')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能接单')
            current_profile = EscortProfile.objects.select_related('account', 'user', 'level').get(pk=escort_profile.pk)
            from .paid_service_scope import acceptance_error
            reason = acceptance_error(current_profile, order, allow_refund_recovery=True)
            if reason:
                raise IllegalTransitionError(reason)
            if order.auto_cancel_at and order.auto_cancel_at <= timezone.now():
                raise IllegalTransitionError('订单已超时，请刷新抢单池')
            from .grab_pool_visibility import order_visibility_time
            try:
                visible_at = order_visibility_time(order, current_profile)
            except ValueError as exc:
                raise IllegalTransitionError(str(exc)) from exc
            if timezone.now() < visible_at:
                remaining = max(1, int((visible_at - timezone.now()).total_seconds()) + 1)
                raise IllegalTransitionError(f'当前通行证需等待 {remaining} 秒后才能抢此单')
            # 二次校验并发上限，规避拉取抢单池到点击抢单之间的间隙
            capacity_limit = current_capacity_limit()
            if count_capacity_orders(request.account) >= capacity_limit:
                raise IllegalTransitionError(
                    f'最多同时进行 {capacity_limit} 单，请完成当前订单后再接单'
                )

        def side_effect(order):
            order.provider = request.legacy_user
            order.provider_account = request.account
            order.provider_name_snapshot = (escort_profile.display_name or request.account.nickname
                                            or request.legacy_user.nickname or request.legacy_user.username)
            sync_single_provider_share(order)
            from wallet.refund_recovery_order import enabled as recovery_grab_enabled, reserve
            if recovery_grab_enabled():
                try:
                    reserve(escort_profile, order)
                except ValueError as exc:
                    raise IllegalTransitionError(str(exc)) from exc
            # BUSY 表示已有履约中的订单；并发上限由名额计数独立控制。
            mark_escort_busy(
                provider_ids=[request.legacy_user.id],
                account_ids=[request.account.pk],
            )

        try:
            order = transition(
                order_id,
                Order.Status.GRABBED,
                operator=request.legacy_user,
                operator_account=request.account,
                pre_check=pre_check,
                side_effect=side_effect,
                dispatch_account_ids=[request.account.pk],
                update_fields=['provider', 'provider_account', 'provider_name_snapshot'],
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前订单状态不允许接单'})

        transaction.on_commit(lambda: notify_order_update(order))

        from stores.display import provider_display_name
        display_name = provider_display_name(order.provider_name_snapshot) or '大神'
        transaction.on_commit(lambda display_name=display_name: _push_message_safe(
            recipient_id=order.customer_id,
            recipient_account_id=order.customer_account_id,
            title='大神已接单',
            preview=f'{display_name}接单了你的「{order.service_name_snapshot}」',
            msg_type='ORDER',
            related_order_id=order.id,
        ))

        return Response({
            'code': 0,
            'msg': '接单成功',
            'data': OrderSerializer(order, context={'request': request}).data,
        })


class StartOrderServiceView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @fulfillment_receipt('start', 'entry_image')
    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可开始服务'})

        entry_image = request.FILES.get('entry_image')
        if settings.REQUIRE_ORDER_EVIDENCE_IMAGES and not entry_image:
            return Response({'code': 400, 'msg': '请上传入队截图'})
        if entry_image:
            try:
                rest_serializers.ImageField().run_validation(entry_image)
            except (rest_serializers.ValidationError, DjangoValidationError):
                return Response({'code': 400, 'msg': '入队截图必须为有效图片'})

        if Order.objects.filter(pk=order_id, escort_mode='DOUBLE', requires_boss_selection=True).exists():
            from .team_fulfillment import submit_team_evidence
            return submit_team_evidence(request, order_id, 'entry_image', entry_image)

        # Serialize a booking starting with new claims on this provider's
        # capacity. Starting the confirmed booking itself is never refused
        # just because the ordinary capacity limit is already reached.
        ensure_provider_dispatch_locks([request.account.pk])

        def pre_check(order):
            from .ownership import owns_order_party
            # The team workflow may be enabled after the initial routing read
            # but before transition() obtains this order's row lock.
            if order.escort_mode == Order.EscortMode.DOUBLE and order.requires_boss_selection:
                raise IllegalTransitionError('双陪凭证流程已更新，请刷新后分别提交入队截图')
            if not owns_order_party(order, request, 'provider'):
                raise IllegalTransitionError('仅订单关联的大神可开始服务')
            from wallet.report_lifecycle import report_order_cycle_matches
            if not report_order_cycle_matches(order, request.account):
                raise IllegalTransitionError('订单陪玩所属周期待核对，请联系客服')
            if request.legacy_user is None:
                raise IllegalTransitionError('陪玩账号缺少关联档案，请联系客服核对')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能开始服务')

        def side_effect(order):
            # 开始服务落库入队截图：为该订单+陪玩建立/更新报单草稿。
            from .points_service_payment import needs_integrated_processing
            snapshot = None
            if needs_integrated_processing(order):
                from .points_funding_plan import PointsFundingPlanError
                from .points_report_test import verified_report_snapshot
                try:
                    snapshot = verified_report_snapshot(order, request.account.pk, request.legacy_user.pk)
                except PointsFundingPlanError as exc:
                    raise IllegalTransitionError('积分订单报单或收益快照待核对') from exc
            report, _ = ProviderReport.objects.get_or_create(
                provider=request.legacy_user, order=order,
                defaults={
                    'provider_account': request.account,
                    'game_name': order.service_name_snapshot or (order.service.name if order.service_id else ''),
                    'description': order.remark, 'amount': snapshot.incoming_basis if snapshot else order.amount,
                    **({'commission_rate': snapshot.commission_rate} if snapshot else {}),
                    'status': ProviderReport.Status.DRAFT,
                },
            )
            if snapshot and (report.provider_account_id != request.account.pk or report.amount != snapshot.incoming_basis
                             or report.commission_rate != snapshot.commission_rate):
                raise IllegalTransitionError('积分订单报单归属或金额待核对')
            if entry_image:
                report.entry_image = entry_image
                report.save(update_fields=['entry_image', 'updated_at'])

        def start_transition():
            return transition(order_id, Order.Status.IN_SERVICE, operator=request.legacy_user,
                operator_account=request.account, pre_check=pre_check, side_effect=side_effect,
                dispatch_account_ids=[request.account.pk])
        try:
            from .points_service_payment import needs_integrated_processing
            candidate = Order.objects.filter(pk=order_id).first()
            if (candidate and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                    and needs_integrated_processing(candidate)):
                from .points_business_access import service_order
                with transaction.atomic(), service_order(order_id, phase='report'):
                    order = start_transition()
            else:
                order = start_transition()
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前订单状态不允许开始服务'})

        transaction.on_commit(lambda: notify_order_update(order))

        return Response({
            'code': 0,
            'msg': '订单已进入服务中',
            'data': OrderSerializer(order, context={'request': request}).data,
        })


class CompleteOrderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @fulfillment_receipt('complete', 'completion_image')
    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可完成订单'})

        # Room orders use this endpoint only to collect each provider's
        # fulfilment evidence. Boss confirmation remains a separate transition.
        room_order = Order.objects.filter(pk=order_id, room_required_snapshot=True).first()
        if room_order:
            from .rooms import message_role
            if message_role(room_order, request.account) != 'PROVIDER':
                return Response({'code': 404, 'msg': '订单不存在'}, status=404)
            if not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False):
                return Response({'code': 409, 'msg': '房间订单需经客服核实并由老板确认，结单流程尚未开放'}, status=409)

        completion_image = request.FILES.get('completion_image')
        if settings.REQUIRE_ORDER_EVIDENCE_IMAGES and not completion_image:
            return Response({'code': 400, 'msg': '请上传结单截图'})
        if completion_image:
            try:
                rest_serializers.ImageField().run_validation(completion_image)
            except (rest_serializers.ValidationError, DjangoValidationError):
                return Response({'code': 400, 'msg': '结单截图必须为有效图片'})

        if Order.objects.filter(pk=order_id, escort_mode='DOUBLE', requires_boss_selection=True).exists():
            from .team_fulfillment import submit_team_evidence
            return submit_team_evidence(request, order_id, 'completion_image', completion_image)

        def pre_check(order):
            from .ownership import owns_order_party
            if not owns_order_party(order, request, 'provider'):
                raise IllegalTransitionError('仅订单关联的大神可完成订单')
            from wallet.report_lifecycle import report_order_cycle_matches
            if not report_order_cycle_matches(order, request.account):
                raise IllegalTransitionError('订单陪玩所属周期待核对，请联系客服')
            from .participation_entry import pending_transfer_segments
            if pending_transfer_segments(order, account_id=request.account.pk).exists():
                raise IllegalTransitionError('请先确认本次转入并提交入队凭证')
            if request.legacy_user is None:
                raise IllegalTransitionError('陪玩账号缺少关联档案，请联系客服核对')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能完成订单')
            from .refunds import negotiated_aftersale_blocks_completion
            if negotiated_aftersale_blocks_completion(order):
                raise IllegalTransitionError('售后处理中，暂不能结单，请等待客服处理')

        def side_effect(order):
            # 顺序很关键：先把结单截图落库，再结算。
            # settle_order 里的凭证闸门查的就是这张报单，若先结算后存图，
            # 陪玩每次点「完成」都会因为「缺结单截图」被自己刚上传的图卡住。
            from .points_service_payment import needs_integrated_processing
            from .models import OrderOriginalShareTransfer
            if needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).exists():
                from .points_transfer_funding import plan_points_transfer
                from .points_funding_plan import PointsFundingPlanError
                try:
                    plan_points_transfer(order.pk)
                except PointsFundingPlanError as exc:
                    raise IllegalTransitionError('积分转单履约和资金凭证待核对') from exc
                if completion_image:
                    from .original_attribution import completion_evidence
                    completion_evidence(order, request.account.pk, completion_image)
                return
            snapshot = None
            if needs_integrated_processing(order):
                from .points_funding_plan import PointsFundingPlanError
                from .points_report_test import verified_report_snapshot
                try:
                    snapshot = verified_report_snapshot(order, request.account.pk, request.legacy_user.pk)
                except PointsFundingPlanError as exc:
                    raise IllegalTransitionError('积分订单报单或收益快照待核对') from exc
            report, _ = ProviderReport.objects.get_or_create(
                provider=request.legacy_user, order=order,
                defaults={
                    'provider_account': request.account,
                    'game_name': order.service_name_snapshot or (order.service.name if order.service_id else ''),
                    'description': order.remark, 'amount': snapshot.incoming_basis if snapshot else order.amount,
                    **({'commission_rate': snapshot.commission_rate} if snapshot else {}),
                    'status': ProviderReport.Status.DRAFT,
                },
            )
            if snapshot and (report.provider_account_id != request.account.pk or report.amount != snapshot.incoming_basis
                             or report.commission_rate != snapshot.commission_rate):
                raise IllegalTransitionError('积分订单报单归属或金额待核对')
            if room_order and report.status not in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
                raise IllegalTransitionError('当前报单不可修改结单凭证')
            if completion_image:
                report.completion_image = completion_image
            # 完成履约生成待提交草稿；新订单在全体报单通过后结算。
            report.status = ProviderReport.Status.DRAFT
            report.save(update_fields=[
                *(['completion_image'] if completion_image else []),
                'status',
                'updated_at',
            ])
            if completion_image:
                from .original_attribution import completion_evidence
                completion_evidence(order, request.account.pk, report.completion_image.name)

        def finish_order(order):
            side_effect(order)
            complete_fulfillment(order, operator=request.legacy_user, operator_account=request.account)

        try:
            if room_order:
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=order_id)
                    from .rooms import message_role
                    if (order.room_required_snapshot is not True or order.status != Order.Status.IN_SERVICE
                            or message_role(order, request.account) != 'PROVIDER'):
                        raise IllegalTransitionError('房间订单状态或参与人已变化')
                    if order.room.completion_requests.filter(status='VERIFIED',
                            confirmation__status__in=['RUNNING', 'PAUSED']).exists():
                        raise IllegalTransitionError('结单申请已核实，凭证不能再修改')
                    pre_check(order)
                    side_effect(order)
                    from .work_stints import personal_completion
                    personal_completion(order, request.account.pk)
                    transaction.on_commit(lambda: notify_order_update(order))
                return Response({'code': 0, 'msg': '结单凭证已保存，请继续提交房间结单申请',
                    'data': OrderSerializer(order, context={'request': request}).data})
            with transaction.atomic(), ExitStack() as scopes:
                def check_completion(order):
                    pre_check(order)
                    if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
                        from .models import OrderOriginalShareTransfer
                        from .points_service_payment import needs_integrated_processing
                        if needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
                                previous_segment__attribution__order=order).exists():
                            from .points_transfer_refund_access import after_transfer
                            scopes.enter_context(after_transfer(order.pk, phase='report'))

                order = transition(
                    order_id,
                    Order.Status.COMPLETED,
                    operator=request.legacy_user,
                    operator_account=request.account,
                    pre_check=check_completion,
                    side_effect=finish_order,
                    customer_completion_notice=True,
                )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前订单状态不允许完成'})
        except SettlementError as exc:
            # 分账超额已被闸门阻断，整笔事务回滚，订单保持服务中待人工核对
            return Response({'code': 400, 'msg': f'{exc}，请联系客服核对分账'})
        except WalletAddressingError as exc:
            return Response({'code': 400, 'msg': f'钱包数据异常：{exc}'})

        transaction.on_commit(lambda: notify_order_update(order))

        return Response({
            'code': 0,
            'msg': '订单已完成',
            'data': OrderSerializer(order, context={'request': request}).data,
        })


class CancelOrderView(APIView):
    """客户主动取消未接单订单（仅 PENDING 状态可取消，全额退款）"""
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        from .grab_single_flow import applies, cancel
        if applies(order_id):
            return cancel(request, order_id)
        from .grab_team_flow import applies as team_applies, cancel as team_cancel
        if team_applies(order_id):
            return team_cancel(request, order_id)
        from .models import OrderGrabPoolOpeningIntent
        if OrderGrabPoolOpeningIntent.objects.filter(order_id=order_id, state='REVIEW',
                                                      order__grab_pool_snapshot__isnull=True).exists():
            from .grab_customer_review import request_cancellation
            return request_cancellation(request, order_id, request_key=request.data.get('client_request_id'))
        from .direct_payment import DirectPaymentCancellationUnconfirmed, require_cancel_channel_confirmation
        reason = (request.data.get('reason') or '').strip()[:255]
        refund_fact = {}

        class GrabCancellationReroute(Exception):
            def __init__(self, route):
                self.route = route

        def pre_check(order):
            from .ownership import owns_order_party
            if request.account.account_type != ClubAccount.AccountType.BOSS or not owns_order_party(order, request, 'customer'):
                raise IllegalTransitionError('仅订单所属客户可取消')
            if order.status != Order.Status.PENDING:
                raise IllegalTransitionError('订单已被接单，无法取消')
            # The opening worker uses this same order lock. Its round may have
            # committed after the initial unlocked route check above.
            if applies(order.pk):
                raise GrabCancellationReroute('single')
            if team_applies(order.pk):
                raise GrabCancellationReroute('team')
            if OrderGrabPoolOpeningIntent.objects.filter(
                    order=order, state='REVIEW', order__grab_pool_snapshot__isnull=True).exists():
                raise GrabCancellationReroute('review')
            require_cancel_channel_confirmation(order)

        def side_effect(order):
            from .points_service_payment import needs_integrated_processing
            points_paid = needs_integrated_processing(order)
            if points_paid:
                from .points_service_timeout import refund_customer_cancel_locked
                refund_fact['refund'] = refund_customer_cancel_locked(order, operator=request.legacy_user, operator_account=request.account)
                order._points_cancel_refunded = True
            else:
                refund_fact['refund'] = _refund_to_customer(order, reason='客户取消订单')
            order.cancel_reason = reason or '客户取消'

        try:
            with transaction.atomic(), ExitStack() as stack:
                if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                    from console.payroll_event_barrier import lock_payment_refund_events
                    lock_payment_refund_events()
                locked = Order.objects.select_for_update().get(pk=order_id)
                pre_check(locked)
                from .applications import ApplicationError, _enter_assignment_scope
                try:
                    _enter_assignment_scope(stack, locked)
                except ApplicationError as exc:
                    raise SettlementError(str(exc)) from exc
                order = transition(
                    order_id,
                    Order.Status.CANCELLED,
                    operator=request.legacy_user,
                    operator_account=request.account,
                    action=OrderStatusLog.Action.CANCEL,
                    reason=reason,
                    pre_check=pre_check,
                    side_effect=side_effect,
                    update_fields=['cancel_reason', 'payment_status', 'refunded_at'],
                )
                from site_messages.order_customer_cancel_notice import prepare
                prepare(order, refund=refund_fact.get('refund'))
        except GrabCancellationReroute as exc:
            # record_intent owns its outer transactions, so dispatch only
            # after the ordinary cancellation transaction has rolled back.
            if exc.route == 'single':
                return cancel(request, order_id)
            if exc.route == 'team':
                return team_cancel(request, order_id)
            from .grab_customer_review import request_cancellation
            return request_cancellation(request, order_id, request_key=request.data.get('client_request_id'))
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except DirectPaymentCancellationUnconfirmed as exc:
            return Response({'code': 409, 'errorCode': 'DIRECT_PAYMENT_CANCELLATION_UNCONFIRMED', 'msg': str(exc)})
        except (IllegalTransitionError, SettlementError, WalletAddressingError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许取消'})

        transaction.on_commit(lambda: notify_order_update(order))

        # 若有陪玩抢单后又被取消（理论不会到这里，因 pre_check 限定 PENDING），仍保护性推送
        if order.provider_id:
            _push_message_safe(
                recipient_id=order.provider_id,
                title='订单已被取消',
                preview=f'你接的「{order.service_name_snapshot}」已被客户取消',
                msg_type='ORDER',
                related_order_id=order.id,
            )

        return Response({
            'code': 0,
            'msg': (f'订单已取消，款项已退回{current_currency_name()}余额'
                    + ('，抵扣积分已退回' if getattr(order, '_points_cancel_refunded', False) else '')
                    if order.payment_status == Order.PaymentStatus.REFUNDED and order.amount > 0 else '订单已取消'),
            'data': OrderSerializer(order, context={'request': request}).data,
        })


class RejectOrderView(APIView):
    """Keep the legacy endpoint safe for already installed clients."""
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        # No order lookup is needed: the action is unavailable for every
        # account and state, so a stale or forged client cannot mutate a paid
        # assignment or obtain any information about someone else's order.
        return Response({'code': 400, 'errorCode': 'ORDER_SELF_REJECTION_DISABLED',
                         'msg': '订单已分配后不能自行拒单，请联系客服协调处理'})


class RefundOrderView(APIView):
    """运营强制退款：支持 GRABBED / IN_SERVICE 状态，全额退款"""
    permission_classes = [IsClubAccountAuthenticated, HasConsolePerm]
    default_perm = 'order:refund'

    from .gift_refund_lifecycle import protect_gift_refund

    @protect_gift_refund('id')
    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '仅客服可强制退款'})

        reason = (request.data.get('reason') or '').strip()[:255]
        if not reason:
            return Response({'code': 400, 'msg': '请填写退款原因'})

        try:
            from .refunds import apply_admin_refund
            order = apply_admin_refund(order_id,reason=f'客服强制退款：{reason}',operator=request.legacy_user,
                operator_account=request.account,plan_token=request.data.get('plan_token'),
                refund_amount=request.data.get('refund_amount'),client_request_id=request.data.get('client_request_id'),notify=False)
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except (IllegalTransitionError, SettlementError, WalletAddressingError) as exc:
            from .refunds import RefundError
            if isinstance(exc, RefundError):
                return Response({'code':409 if exc.code in ('REFUND_PLAN_CHANGED','REFUND_REQUEST_CONFLICT') else 400,
                                 'errorCode':exc.code,'msg':str(exc),'data':exc.data})
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许退款'})

        if getattr(order,'_refund_replayed',False):
            return Response({'code':0,'msg':'退款已处理','data':OrderSerializer(order,context={'request':request}).data})
        transaction.on_commit(lambda: notify_order_update(order))

        return Response({
            'code': 0,
            'msg': '订单已强制退款',
            'data': OrderSerializer(order, context={'request': request}).data,
        })


class EvaluateOrderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        serializer = CreateEvaluationSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _format_serializer_errors(serializer.errors)})

        order = serializer.context['order']
        score = serializer.validated_data['score']
        target = serializer.validated_data['evaluation_target']

        from club_accounts.provider_lifecycle import ProviderLifecycleError
        from .rating_aftersale_lifecycle import capture, verify
        try:
            rating_provider = capture(order, target, (score, serializer.validated_data['skill_score'],
                serializer.validated_data['attitude_score'], serializer.validated_data['communication_score']))
        except ProviderLifecycleError:
            return Response({'code': 409, 'msg': '评价陪玩或售后来源周期待核对'}, status=409)

        with transaction.atomic():
            if rating_provider is not None:
                from club_accounts.provider_lifecycle import lock_provider_operation
                try:
                    lock_provider_operation(rating_provider)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请稍后重试'}, status=503)
            # Serialize simultaneous submissions for the same completed order.
            # Validation happens before the transaction, so another request may
            # have created the evaluation while this request was waiting.
            order = Order.objects.select_for_update().get(pk=order.pk)
            if rating_provider is not None and not verify(order, rating_provider):
                return Response({'code': 409, 'msg': '评价陪玩或售后来源周期待核对'}, status=409)
            from .evaluation_targets import evaluation_targets
            current = next((item for item in evaluation_targets(order)
                            if item['provider_legacy_id'] == target['provider_legacy_id']
                            and item['provider_account_id'] == target['provider_account_id']), None)
            if order.status != Order.Status.COMPLETED or current is None:
                return Response({'code': 400, 'msg': '订单或陪玩状态已变化，请刷新后重试'})
            if Evaluation.objects.filter(order_id=order.pk, provider_id=current['provider_legacy_id']).exists():
                return Response({'code': 400, 'msg': '该陪玩已评价，请查看订单详情'})
            evaluation = Evaluation.objects.create(
                order=order,
                customer=order.customer,
                provider_id=current['provider_legacy_id'],
                customer_account=request.account,
                provider_account_id=current['provider_account_id'],
                score=score,
                skill_score=serializer.validated_data['skill_score'],
                attitude_score=serializer.validated_data['attitude_score'],
                communication_score=serializer.validated_data['communication_score'],
                content=serializer.validated_data.get('content', ''),
                is_anonymous=serializer.validated_data.get('is_anonymous', False),
            )
            from .evaluation_aftersale import register_low_rating
            register_low_rating(evaluation, order)
            # 回写陪玩师评分聚合
            refresh_escort_rating(evaluation.provider_id, provider_account_id=evaluation.provider_account_id)
            if score >= 4:
                from engagement.services import broadcast
                broadcast('REVIEW', f'evaluation:{evaluation.pk}', account=request.account,
                    anonymous=evaluation.is_anonymous, provider=current['name'])
            from site_messages.aftersale_open_notice import enabled as low_rating_notice_enabled
            if (low_rating_notice_enabled() and min(evaluation.score, evaluation.skill_score,
                    evaluation.attitude_score, evaluation.communication_score) <= 2):
                from site_messages.low_rating_notice import record as record_low_rating_notice
                record_low_rating_notice(evaluation, order)
            elif (min(evaluation.score, evaluation.skill_score, evaluation.attitude_score,
                      evaluation.communication_score) > 2
                    and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
                from site_messages.evaluation_notice import record as record_evaluation_notice, schedule as schedule_evaluation_notice
                if getattr(settings, 'EVALUATION_NOTICE_RECOVERY_ENABLED', False):
                    record_evaluation_notice(evaluation, order)
                else:
                    schedule_evaluation_notice(evaluation, order)
            else:
                transaction.on_commit(lambda: _push_message_safe(
                    recipient_id=evaluation.provider_id,
                    recipient_account_id=evaluation.provider_account_id,
                    title='收到新的服务评价',
                    preview=f'「{order.service_name_snapshot}」收到评价，可在订单中查看并回复',
                    msg_type='ORDER', related_order_id=order.pk))
            transaction.on_commit(lambda: notify_order_update(Order.objects.get(pk=order.pk)))
            transaction.on_commit(notify_evaluation_update)

        aftersale_state = evaluation.low_rating_aftersale_state
        message = {
            'OPENED': '评价成功，已为该陪玩创建售后',
            'ATTACHED': '评价成功，已关联该陪玩的处理中售后',
            'CLOSED': '评价成功，本次未新建或重开售后',
            'EXPIRED': '评价成功，售后期已结束，本次低星不会创建售后',
            'INELIGIBLE': '评价成功，当前订单不符合创建售后的条件',
        }.get(aftersale_state, '评价成功')
        data = dict(EvaluationSerializer(evaluation).data)
        data['low_rating_aftersale_state'] = aftersale_state
        return Response({'code': 0, 'msg': message, 'data': data})


class ServiceEvaluationListView(APIView):
    """C 端商品详情页：某服务的评价列表 + 平均分。"""

    permission_classes = [AllowAny]

    def get(self, request, service_id):
        evaluations = (
            Evaluation.objects.filter(order__service_id=service_id, voided_at__isnull=True)
            .select_related('customer', 'customer_account', 'provider', 'provider_account', 'order')
            .prefetch_related('order__providers')
            .order_by('-created_at')
        )
        total = evaluations.count()
        avg = evaluations.aggregate(avg=Avg('score'))['avg'] or 0
        limited = evaluations[:MAX_SERVICE_EVALUATIONS]
        return Response({
            'code': 0,
            'data': {
                'list': EvaluationSerializer(limited, many=True).data,
                'total': total,
                'avg_score': round(float(avg), 1),
            },
        })


class MyEvaluationListView(APIView):
    """陪玩端：我收到的评价列表 + 平均分。"""

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看收到的评价'})
        ownership = Q(provider_account=request.account)
        if request.legacy_user is not None:
            ownership |= Q(provider_account__isnull=True, provider=request.legacy_user)
        evaluations = (
            Evaluation.objects.filter(ownership, voided_at__isnull=True)
            .select_related('customer', 'customer_account', 'provider', 'provider_account', 'order')
            .prefetch_related('order__providers')
            .order_by('-created_at', '-id')
        )
        totals = evaluations.aggregate(avg=Avg('score'), total=Count('id'),
                                       pending=Count('id', filter=Q(reply_content='')))
        filtered = evaluations
        for field, parameter in (('order_id', 'order_id'), ('id', 'evaluation_id')):
            value = request.query_params.get(parameter)
            if value is not None:
                if not value.isascii() or not value.isdigit() or int(value) < 1:
                    return Response({'code': 400, 'msg': '评价查询参数无效'}, status=400)
                filtered = filtered.filter(**{field: int(value)})
        filtered_total = filtered.count() if filtered is not evaluations else totals['total']
        page_value = request.query_params.get('page')
        page_size_value = request.query_params.get('page_size')
        page = page_size = None
        if page_value is not None or page_size_value is not None:
            page_value, page_size_value = page_value or '1', page_size_value or '20'
            if any(not value.isascii() or not value.isdigit() or int(value) < 1
                   for value in (page_value, page_size_value)):
                return Response({'code': 400, 'msg': '分页参数无效'}, status=400)
            page, page_size = int(page_value), min(int(page_size_value), 100)
            filtered = filtered[(page - 1) * page_size:page * page_size]
        return Response({
            'code': 0,
            'data': {
                'list': EvaluationSerializer(filtered, many=True).data,
                'total': totals['total'], 'pending_total': totals['pending'],
                'filtered_total': filtered_total, 'page': page, 'page_size': page_size,
                'avg_score': round(float(totals['avg'] or 0), 1),
            },
        })


class ReplyEvaluationView(APIView):
    """陪玩端：回复自己收到的评价。"""

    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, evaluation_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅被评价的陪玩可回复'})
        content = request.data.get('reply_content', '')
        if not isinstance(content, str) or len(content.strip()) > 1000:
            return Response({'code': 400, 'msg': '回复内容须为1000字以内的文本'})
        content = content.strip()
        if not content:
            return Response({'code': 400, 'msg': '回复内容不能为空'})
        try:
            evaluation = Evaluation.objects.get(id=evaluation_id, voided_at__isnull=True)
        except Evaluation.DoesNotExist:
            return Response({'code': 404, 'msg': '评价不存在'})
        owns_evaluation = evaluation.provider_account_id == request.account.pk if evaluation.provider_account_id else (
            request.legacy_user is not None and evaluation.provider_id == request.legacy_user.pk
        )
        if not owns_evaluation:
            return Response({'code': 403, 'msg': '仅被评价的陪玩可回复'})
        from .evaluation_replies import reply_notice_preview, valid_reply_version, write_reply_if_current
        version = request.data.get('reply_version')
        if not valid_reply_version(version):
            return Response({'code': 400, 'msg': '请刷新评价后再回复'}, status=400)
        with transaction.atomic():
            reply_order = Order.objects.select_for_update().get(pk=evaluation.order_id)
            if not write_reply_if_current(evaluation, content, version, 'PROVIDER'):
                return Response({'code': 409, 'errorCode': 'EVALUATION_REPLY_CONFLICT', 'msg': '回复已变化，请刷新评价后核对'}, status=409)
            from site_messages.evaluation_reply_notice import (enabled as reply_notice_enabled,
                record as record_reply_notice, schedule_immediate as schedule_reply_notice)
            reply_preview = reply_notice_preview(evaluation)
            if reply_notice_enabled():
                record_reply_notice(evaluation, reply_order, staff=False, boss_preview=reply_preview)
            else:
                schedule_reply_notice(evaluation, reply_order, staff=False, boss_preview=reply_preview)
            transaction.on_commit(lambda: notify_order_update(Order.objects.get(pk=evaluation.order_id)))
            transaction.on_commit(notify_evaluation_update)
        return Response({
            'code': 0,
            'msg': '回复成功',
            'data': EvaluationSerializer(evaluation).data,
        })


class AssignOrderView(APIView):
    """Customer-service new orders are allocated through the grab pool."""
    permission_classes = [IsClubAccountAuthenticated, HasConsolePerm]
    default_perm = 'order:dispatch'

    def post(self, request, order_id):
        if not Order.objects.filter(pk=order_id).exists():
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        return Response({
            'code': 409,
            'msg': '客服安排的新单由抢单池自动分发，不能直接指定陪玩；已安排订单请使用改派或转单流程',
        }, status=409)


class OrderStatsView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        account = request.account
        if account.account_type == ClubAccount.AccountType.PROVIDER:
            orders = Order.objects.filter(
                Q(provider_account=account)
                | Q(providers__provider_account=account)
            ).distinct()
        elif account.account_type == ClubAccount.AccountType.STAFF:
            orders = Order.objects.all()
        else:
            orders = Order.objects.filter(customer_account=account)

        total = orders.count()
        pending = orders.filter(status=Order.Status.PENDING).count()
        grabbed = orders.filter(status=Order.Status.GRABBED).count()
        in_service = orders.filter(status=Order.Status.IN_SERVICE).count()
        completed = orders.filter(status=Order.Status.COMPLETED).count()
        cancelled = orders.filter(status=Order.Status.CANCELLED).count()

        return Response({
            'code': 0,
            'data': {
                'total': total,
                'pending': pending,
                'grabbed': grabbed,
                'in_service': in_service,
                'completed': completed,
                'cancelled': cancelled,
            }
        })


# ---------- 内部工具函数 ----------
#
# 结算 / 退款 / 站内信推送 / 超时投递等实现已收口到 orders.services，
# 本模块顶部按旧名 import 进来（_refund_to_customer / _push_message_safe /
# _schedule_auto_cancel），调用点保持不变。


def _format_serializer_errors(errors):
    """把 DRF 的嵌套错误结构压成一句人话，用于统一信封的 msg 字段。"""
    if isinstance(errors, dict):
        first_value = next(iter(errors.values()))
        return _format_serializer_errors(first_value)
    if isinstance(errors, list) and errors:
        return _format_serializer_errors(errors[0])
    return str(errors)
