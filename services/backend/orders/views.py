from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.permissions import IsClubAccountAuthenticated
from users.models import EscortProfile, EscortSchedule
from wallet.models import ProviderReport, Transaction, get_platform_wallet
from wallet.services import (
    WalletAddressingError,
    claim_fund_write,
    get_wallet,
    peek_fund_write,
)
from common.logging_utils import log_money_event, new_trace_id
from common.media import build_media_url

from .models import (
    Evaluation, GameCategory, KookDispatchRecord, Order, OrderStatusLog,
    ServiceFavorite, ServiceItem,
)
from .kook_dispatch import enqueue_kook_dispatch
from .notifier import notify_order_update
from .ratings import apply_escort_rating
from .services import (
    count_active_orders,
    mark_escort_busy,
    pending_timeout_deadline,
    push_message_safe as _push_message_safe,
    refresh_escort_status,
    refund_to_customer as _refund_to_customer,
    schedule_auto_cancel as _schedule_auto_cancel,
    settle_order,
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
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        services = ServiceItem.objects.filter(is_active=True)
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
        serializer = ServiceItemSerializer(services, many=True)
        return Response({'code': 0, 'data': serializer.data})


class GameCategoryListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

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
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, service_id):
        try:
            service = ServiceItem.objects.get(id=service_id, is_active=True)
        except ServiceItem.DoesNotExist:
            return Response({'code': 404, 'msg': '服务不存在'})
        return Response({
            'code': 0,
            'data': ServiceItemDetailSerializer(service, context={'request': request}).data,
        })


class FavoriteListView(APIView):
    """我的收藏：返回当前老板收藏的服务列表。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        favorites = ServiceFavorite.objects.filter(
            account=request.account,
            service__is_active=True,
        ).select_related('service')
        data = [
            ServiceItemSerializer(fav.service).data
            for fav in favorites
        ]
        return Response({'code': 0, 'data': data})


class FavoriteToggleView(APIView):
    """收藏 / 取消收藏：幂等切换，返回当前是否已收藏。"""
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        service_id = request.data.get('service_id')
        if not service_id:
            return Response({'code': 400, 'msg': '缺少 service_id'})
        try:
            service = ServiceItem.objects.get(id=service_id, is_active=True)
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

# 消费榜爵位阈值（累计兴安币消费的内部账务值；从高到低，取首个达标）
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

        completed = Order.objects.filter(
            status=Order.Status.COMPLETED,
            completed_at__gte=start_dt,
        )

        consume_rows = (
            completed.values('customer_id')
            .annotate(total=Sum('amount'), order_count=Count('id'))
            .order_by('-total')[:RANKING_LIMIT]
        )
        order_rows = (
            completed.exclude(provider_id=None)
            .values('provider_id')
            .annotate(order_count=Count('id'), total=Sum('amount'))
            .order_by('-order_count')[:RANKING_LIMIT]
        )

        user_ids = (
            [row['customer_id'] for row in consume_rows]
            + [row['provider_id'] for row in order_rows]
        )
        users = {u.id: u for u in User.objects.filter(id__in=user_ids)}

        def display(uid):
            user = users.get(uid)
            if not user:
                return '神秘用户', ''
            return (user.nickname or user.username or '用户'), (user.avatar_url or '')

        consume_rank = []
        for idx, row in enumerate(consume_rows):
            name, avatar = display(row['customer_id'])
            total = row['total'] or 0
            consume_rank.append({
                'rank': idx + 1,
                'user_id': row['customer_id'],
                'nickname': name,
                'avatar': avatar,
                'total_amount': total,
                'order_count': row['order_count'],
                'title': _title_for(total, CONSUME_TITLE_TIERS),
            })

        order_rank = []
        for idx, row in enumerate(order_rows):
            name, avatar = display(row['provider_id'])
            count = row['order_count']
            order_rank.append({
                'rank': idx + 1,
                'user_id': row['provider_id'],
                'nickname': name,
                'avatar': avatar,
                'order_count': count,
                'total_amount': row['total'] or 0,
                'title': _title_for(count, ORDER_TITLE_TIERS),
            })

        return Response({
            'code': 0,
            'data': {
                'period': period,
                'consume_rank': consume_rank,
                'order_rank': order_rank,
            },
        })


class OrderListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        account = request.account
        user = request.legacy_user
        status_filter = request.query_params.get('status')
        if account.account_type == ClubAccount.AccountType.PROVIDER:
            if status_filter == 'pending':
                # 待接单池：返回尚未被接单的 PENDING 订单，供陪玩抢单
                profile = EscortProfile.objects.filter(account=account).select_related('level').first()
                if (
                    profile is None
                    or profile.status == EscortProfile.Status.OFFLINE
                    or count_active_orders(account) >= Order.MAX_CONCURRENT_ORDERS
                    or not EscortSchedule.provider_is_scheduled_now(user)
                ):
                    orders = Order.objects.none()
                else:
                    delay = profile.order_visibility_delay_seconds
                    visible_before = timezone.now() - timedelta(seconds=delay)
                    orders = Order.objects.filter(
                        Q(auto_cancel_at__isnull=True) | Q(auto_cancel_at__gt=timezone.now()),
                        provider_account__isnull=True,
                        status=Order.Status.PENDING,
                        payment_status=Order.PaymentStatus.PAID,
                        created_at__lte=visible_before,
                    )
                    # 档位准入：仅保留服务不限档、或要求档位不高于本人档位的单。
                    # 高档位陪玩可接低档位单，低档位不可接高档位单。
                    orders = orders.filter(
                        Q(service__required_level__isnull=True)
                        | Q(service__required_level__sort_order__lte=profile.level_rank)
                    )
            else:
                orders = Order.objects.filter(
                    Q(provider_account=account)
                    | Q(providers__provider_account=account)
                ).distinct()
        elif account.account_type == ClubAccount.AccountType.STAFF:
            orders = Order.objects.all()
        else:
            orders = Order.objects.filter(customer_account=account)

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

        orders = orders.select_related(
            'customer', 'customer__boss_type', 'provider', 'service',
            'service__service_category', 'evaluation', 'support_contact'
        ).order_by('-created_at')
        serializer = OrderSerializer(orders, many=True, context={'request': request})
        return Response({'code': 0, 'data': serializer.data})


class CreateOrderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _format_serializer_errors(serializer.errors)})

        service = serializer.validated_data['service']
        game_rounds = serializer.validated_data['game_rounds']
        remark = serializer.validated_data['remark']
        game_region = serializer.validated_data['game_region']
        game_nickname = serializer.validated_data['game_nickname']
        game_uid = serializer.validated_data['game_uid']
        amount = serializer.validated_data['amount']
        provider = serializer.validated_data.get('provider')
        support_contact = serializer.validated_data.get('support_contact')
        user_coupon = serializer.validated_data.get('user_coupon')
        commission_rate = serializer.validated_data['commission_rate']
        inviter = serializer.validated_data.get('inviter')
        provider_account = _account_for_legacy_user(provider)
        inviter_account = _account_for_legacy_user(inviter)
        inviter_commission_rate = serializer.validated_data['inviter_commission_rate']
        promotion = serializer.validated_data.get('promotion')
        original_amount = serializer.validated_data['original_amount']
        boss_discount = serializer.validated_data['boss_discount']
        promo_discount = serializer.validated_data['promo_discount']
        coupon_discount = serializer.validated_data['coupon_discount']

        split = compute_split(amount, commission_rate, inviter_commission_rate)
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

        with transaction.atomic():
            wallet = get_wallet(
                account=request.account, user=request.legacy_user, for_update=True,
            )
            if not wallet.is_active:
                return Response({'code': 403, 'msg': '钱包不可用'})
            if wallet.balance < amount:
                return Response({'code': 400, 'msg': '兴安币不足，请联系企业微信客服充值'})

            # 锁定并校验优惠券（防并发重复使用）
            locked_coupon = None
            if user_coupon is not None:
                from coupons.models import UserCoupon
                locked_coupon = (
                    UserCoupon.objects.select_for_update()
                    .filter(
                        id=user_coupon.id,
                        account=request.account,
                        status=UserCoupon.Status.UNUSED,
                    )
                    .first()
                )
                if locked_coupon is None:
                    return Response({'code': 400, 'msg': '优惠券已使用或已失效'})

            # 下单是出账动作。幂等键在「校验全过、即将扣款」这一刻抢占：
            # 弱网重试的第二个请求会在这里被拦下，而不是变成两笔扣款 + 两张单。
            # 放在校验之后，是因为视图里的 `return Response(400)` 属于正常返回，
            # atomic 会照常提交 —— 提前占键会让余额不足的用户充值后无法重试。
            allowed, reason = claim_fund_write(
                account=request.account,
                request_id=request.data.get('client_request_id'),
                scope='order.create',
                fingerprint=f'{service.id}|{amount}|{game_rounds}|{game_uid}',
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

            balance_before = wallet.balance
            wallet.balance -= amount
            wallet.save(update_fields=['balance'])

            order = Order.objects.create(
                customer=request.legacy_user,
                customer_account=request.account,
                provider=None,
                service=service,
                support_contact=support_contact,
                amount=amount,
                game_rounds=game_rounds,
                remark=remark,
                game_region=game_region,
                game_nickname=game_nickname,
                game_uid=game_uid,
                status=Order.Status.PENDING,
                payment_status=Order.PaymentStatus.PAID,
                auto_cancel_at=(
                    None if provider is not None
                    else pending_timeout_deadline()
                ),
                original_amount=original_amount,
                boss_discount=boss_discount,
                promo_discount=promo_discount,
                coupon_discount=coupon_discount,
                promotion=promotion,
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter=inviter,
                inviter_account=inviter_account,
                inviter_commission=split.inviter_commission,
                shop_income=split.shop_income,
            )
            Transaction.objects.create(
                wallet=wallet,
                order=order,
                amount=-amount,
                tx_type=Transaction.TxType.PAY,
                balance_before=balance_before,
                balance_after=wallet.balance,
                remark=f'订单支付：{order.order_no}',
            )
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

            if locked_coupon is not None:
                from coupons.models import UserCoupon
                locked_coupon.status = UserCoupon.Status.USED
                locked_coupon.order = order
                locked_coupon.used_at = timezone.now()
                locked_coupon.save(update_fields=['status', 'order', 'used_at'])

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
                    mark_escort_busy(
                        provider_ids=[provider.id],
                        account_ids=[getattr(provider_account, 'pk', None)],
                    )

                order = transition(
                    order.id,
                    Order.Status.GRABBED,
                    operator=request.legacy_user,
                    operator_account=request.account,
                    action=OrderStatusLog.Action.ASSIGN,
                    reason='老板指定陪玩',
                    side_effect=assign_selected,
                    update_fields=[
                        'provider', 'provider_account', 'provider_name_snapshot',
                    ],
                )

        if provider is None:
            _schedule_auto_cancel(order)
            enqueue_kook_dispatch(order, KookDispatchRecord.Trigger.NEW_ORDER)
        else:
            _push_message_safe(
                recipient_id=provider.id,
                title='老板指定你服务',
                preview=f'你收到一笔「{order.service_name_snapshot}」订单，请尽快开始服务',
                msg_type='ORDER',
                related_order_id=order.id,
            )

        notify_order_update(order)

        return Response({
            'code': 0,
            'msg': '下单成功',
            'data': OrderSerializer(order, context={'request': request}).data,
        })


class OrderQuoteView(APIView):
    """下单试算：与正式下单复用同一校验和计价器，但不扣款、不占券。"""

    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _format_serializer_errors(serializer.errors)})

        data = serializer.validated_data
        wallet = get_wallet(user=request.legacy_user, account=request.account)
        promotion = data.get('promotion')
        amount = data['amount']
        return Response({'code': 0, 'data': {
            'original_amount': data['original_amount'],
            'boss_discount': data['boss_discount'],
            'promo_discount': data['promo_discount'],
            'coupon_discount': data['coupon_discount'],
            'amount': amount,
            'promotion': ({'id': promotion.id, 'title': promotion.title} if promotion else None),
            'wallet_balance': wallet.balance,
            'balance_sufficient': wallet.is_active and wallet.balance >= amount,
        }})


class TipOrderView(APIView):
    """老板对已完成服务单快捷赠送礼物；即时扣款、结算并生成礼物单。"""

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '仅老板可以赠送礼物'})
        try:
            gift_service_id = int(request.data.get('gift_service_id'))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '请选择礼物'})

        try:
            gift_service = ServiceItem.objects.select_related('service_category').get(
                id=gift_service_id,
                is_active=True,
                service_category__is_active=True,
                service_category__is_gift=True,
            )
        except ServiceItem.DoesNotExist:
            return Response({'code': 400, 'msg': '礼物不存在或已下架'})

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

        with transaction.atomic():
            try:
                source_order = Order.objects.select_for_update().select_related(
                    'provider__escort_profile__level', 'service__service_category', 'support_contact',
                ).get(id=order_id, customer=request.legacy_user)
            except Order.DoesNotExist:
                return Response({'code': 404, 'msg': '原订单不存在'})
            if source_order.status != Order.Status.COMPLETED:
                return Response({'code': 400, 'msg': '服务完成后才可以赠送礼物'})
            if source_order.service.service_category and source_order.service.service_category.is_gift:
                return Response({'code': 400, 'msg': '礼物单不能重复打赏'})
            provider = source_order.provider
            if provider is None:
                return Response({'code': 400, 'msg': '原订单未关联陪玩，无法打赏'})

            amount = gift_service.price
            customer_wallet = get_wallet(
                user=request.legacy_user, account=request.account, for_update=True,
            )
            if not customer_wallet.is_active:
                return Response({'code': 403, 'msg': '钱包不可用'})
            if customer_wallet.balance < amount:
                return Response({'code': 400, 'msg': '兴安币不足，请联系企业微信客服充值'})

            from .pricing import resolve_commission_rate
            commission_rate = resolve_commission_rate(
                gift_service, getattr(provider, 'escort_profile', None), None,
            )
            inviter = request.legacy_user.inviter
            inviter_rate = request.legacy_user.inviter_commission_rate if inviter else 0
            split = compute_split(amount, commission_rate, inviter_rate)

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
                fingerprint=f'{order_id}|{gift_service_id}',
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

            now = timezone.now()
            # 礼物单必须和普通订单一样写全 account 维度。
            # 历史实现只写了 legacy user 外键，导致后台按 account 统计
            # （陪玩收益榜、老板消费榜、邀请人分佣报表）整批漏掉打赏流水 ——
            # 钱确实到账了，报表上却查无此单。
            provider_account = (
                source_order.provider_account
                or _account_for_legacy_user(provider)
            )
            inviter_account = _account_for_legacy_user(inviter)
            gift_order = Order.objects.create(
                customer=request.legacy_user,
                customer_account=request.account,
                provider=provider,
                provider_account=provider_account,
                service=gift_service,
                source_order=source_order,
                support_contact=source_order.support_contact,
                amount=amount,
                original_amount=amount,
                status=Order.Status.COMPLETED,
                payment_status=Order.PaymentStatus.PAID,
                completed_at=now,
                remark=f'赠送给{source_order.provider_name_snapshot or provider.nickname or provider.username}',
                provider_name_snapshot=(
                    source_order.provider_name_snapshot
                    or provider.nickname
                    or provider.username
                ),
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter=inviter,
                inviter_account=inviter_account,
                inviter_commission=split.inviter_commission,
                shop_income=split.shop_income,
            )

            customer_before = customer_wallet.balance
            customer_wallet.balance -= amount
            customer_wallet.save(update_fields=['balance'])
            Transaction.objects.create(
                wallet=customer_wallet, order=gift_order, amount=-amount,
                tx_type=Transaction.TxType.GIFT,
                balance_before=customer_before, balance_after=customer_wallet.balance,
                remark=f'礼物打赏：{gift_service.name}',
            )
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

            provider_wallet = get_wallet(user=provider, for_update=True)
            provider_before = provider_wallet.balance
            provider_wallet.balance += split.provider_income
            provider_wallet.save(update_fields=['balance'])
            Transaction.objects.create(
                wallet=provider_wallet, order=gift_order, amount=split.provider_income,
                tx_type=Transaction.TxType.GIFT,
                balance_before=provider_before, balance_after=provider_wallet.balance,
                remark=f'收到礼物：{gift_service.name}',
            )
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

            if inviter and split.inviter_commission:
                inviter_wallet = get_wallet(user=inviter, for_update=True)
                inviter_before = inviter_wallet.balance
                inviter_wallet.balance += split.inviter_commission
                inviter_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=inviter_wallet, order=gift_order, amount=split.inviter_commission,
                    tx_type=Transaction.TxType.INCOME,
                    balance_before=inviter_before, balance_after=inviter_wallet.balance,
                    remark=f'礼物推荐分佣：{gift_order.order_no}',
                )
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
            log_only(
                gift_order, action=OrderStatusLog.Action.CREATE,
                operator=request.legacy_user, reason=f'完成订单快捷打赏，来源订单 {source_order.order_no}',
            )

        _push_message_safe(
            recipient_id=provider.id,
            title='收到老板礼物',
            preview=f'{request.legacy_user.nickname or "老板"}送给你「{gift_service.name}」',
            msg_type='PROMOTION',
            related_order_id=gift_order.id,
        )
        return Response({
            'code': 0,
            'msg': '礼物已送达',
            'data': OrderSerializer(gift_order, context={'request': request}).data,
        })


class GrabOrderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可接单'})

        try:
            escort_profile = request.account.escort_profile
        except EscortProfile.DoesNotExist:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料，无法接单'})
        if escort_profile.status == EscortProfile.Status.OFFLINE:
            return Response({'code': 400, 'msg': '当前处于离线状态，无法接单'})
        if not EscortSchedule.provider_is_scheduled_now(request.legacy_user):
            return Response({'code': 400, 'msg': '当前时间不在你的接单档期内'})

        active_count = count_active_orders(request.account)
        if active_count >= Order.MAX_CONCURRENT_ORDERS:
            return Response({
                'code': 400,
                'msg': f'最多同时进行 {Order.MAX_CONCURRENT_ORDERS} 单，请完成当前订单后再接单',
            })

        def pre_check(order):
            if order.provider_account_id is not None:
                raise IllegalTransitionError('订单已被其他大神接单')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能接单')
            if not escort_profile.can_take_service(order.service):
                raise IllegalTransitionError('该订单要求更高的陪玩档位，你暂无法接单')
            if order.auto_cancel_at and order.auto_cancel_at <= timezone.now():
                raise IllegalTransitionError('订单已超时，请刷新抢单池')
            visible_at = order.created_at + timedelta(
                seconds=escort_profile.order_visibility_delay_seconds,
            )
            if timezone.now() < visible_at:
                remaining = max(1, int((visible_at - timezone.now()).total_seconds()) + 1)
                raise IllegalTransitionError(f'当前通行证需等待 {remaining} 秒后才能抢此单')
            # 二次校验并发上限，规避拉取抢单池到点击抢单之间的间隙
            if count_active_orders(request.account) >= Order.MAX_CONCURRENT_ORDERS:
                raise IllegalTransitionError(
                    f'最多同时进行 {Order.MAX_CONCURRENT_ORDERS} 单，请完成当前订单后再接单'
                )

        def side_effect(order):
            order.provider = request.legacy_user
            order.provider_account = request.account
            order.provider_name_snapshot = request.legacy_user.nickname or request.legacy_user.username
            # BUSY 表示已有履约中的订单；并发上限仍由 count_active_orders 独立控制。
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
                update_fields=['provider', 'provider_account', 'provider_name_snapshot'],
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前订单状态不允许接单'})

        notify_order_update(order)

        _push_message_safe(
            recipient_id=order.customer_id,
            title='大神已接单',
            preview=f'{order.provider_name_snapshot or "大神"}接单了你的「{order.service_name_snapshot}」',
            msg_type='ORDER',
            related_order_id=order.id,
        )

        return Response({
            'code': 0,
            'msg': '接单成功',
            'data': OrderSerializer(order).data,
        })


class StartOrderServiceView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可开始服务'})

        entry_image = request.FILES.get('entry_image')
        if settings.REQUIRE_ORDER_EVIDENCE_IMAGES and not entry_image:
            return Response({'code': 400, 'msg': '请上传入队截图'})

        def pre_check(order):
            if order.provider_id != request.legacy_user.id:
                raise IllegalTransitionError('仅订单关联的大神可开始服务')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能开始服务')

        def side_effect(order):
            # 开始服务落库入队截图：为该订单+陪玩建立/更新报单草稿。
            report, _ = ProviderReport.objects.get_or_create(
                provider=request.legacy_user, order=order,
                defaults={
                    'game_name': order.service_name_snapshot or (order.service.name if order.service_id else ''),
                    'description': order.remark, 'amount': order.amount,
                    'status': ProviderReport.Status.DRAFT,
                },
            )
            if entry_image:
                report.entry_image = entry_image
                report.save(update_fields=['entry_image', 'updated_at'])

        try:
            order = transition(
                order_id,
                Order.Status.IN_SERVICE,
                operator=request.legacy_user,
                pre_check=pre_check,
                side_effect=side_effect,
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前订单状态不允许开始服务'})

        notify_order_update(order)

        return Response({
            'code': 0,
            'msg': '订单已进入服务中',
            'data': OrderSerializer(order).data,
        })


class CompleteOrderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可完成订单'})

        completion_image = request.FILES.get('completion_image')
        if settings.REQUIRE_ORDER_EVIDENCE_IMAGES and not completion_image:
            return Response({'code': 400, 'msg': '请上传结单截图'})

        def pre_check(order):
            if order.provider_id != request.legacy_user.id:
                raise IllegalTransitionError('仅订单关联的大神可完成订单')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能完成订单')

        def side_effect(order):
            # 顺序很关键：先把结单截图落库，再结算。
            # settle_order 里的凭证闸门查的就是这张报单，若先结算后存图，
            # 陪玩每次点「完成」都会因为「缺结单截图」被自己刚上传的图卡住。
            report, _ = ProviderReport.objects.get_or_create(
                provider=request.legacy_user, order=order,
                defaults={
                    'game_name': order.service_name_snapshot or (order.service.name if order.service_id else ''),
                    'description': order.remark, 'amount': order.amount,
                    'status': ProviderReport.Status.DRAFT,
                },
            )
            if completion_image:
                report.completion_image = completion_image
            # 平台订单已在下方 settle_order 结算，报单审核仅核验凭证，不二次入账。
            report.status = ProviderReport.Status.PENDING
            report.save(update_fields=[
                *(['completion_image'] if completion_image else []),
                'status',
                'updated_at',
            ])

            # 结算收口到 orders.services.settle_order：与后台 complete 共用同一份
            # 凭证闸门 + 守恒闸门 + 入账口径，杜绝两条入口算出两套账。
            settle_order(
                order,
                operator=request.legacy_user,
                operator_account=request.account,
            )

        try:
            order = transition(
                order_id,
                Order.Status.COMPLETED,
                operator=request.legacy_user,
                pre_check=pre_check,
                side_effect=side_effect,
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

        notify_order_update(order)

        _push_message_safe(
            recipient_id=order.customer_id,
            title='订单已完成',
            preview=f'你的「{order.service_name_snapshot}」已完成，去评价赢积分',
            msg_type='ORDER',
            related_order_id=order.id,
        )

        return Response({
            'code': 0,
            'msg': '订单已完成',
            'data': OrderSerializer(order).data,
        })


class CancelOrderView(APIView):
    """客户主动取消未接单订单（仅 PENDING 状态可取消，全额退款）"""
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        reason = (request.data.get('reason') or '').strip()[:255]

        def pre_check(order):
            if order.customer_id != request.legacy_user.id:
                raise IllegalTransitionError('仅订单所属客户可取消')
            if order.status != Order.Status.PENDING:
                raise IllegalTransitionError('订单已被接单，无法取消')

        def side_effect(order):
            _refund_to_customer(order, reason='客户取消订单')
            order.cancel_reason = reason or '客户取消'

        try:
            order = transition(
                order_id,
                Order.Status.CANCELLED,
                operator=request.legacy_user,
                action=OrderStatusLog.Action.CANCEL,
                reason=reason,
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=['cancel_reason', 'payment_status', 'refunded_at'],
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许取消'})

        notify_order_update(order)

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
            'msg': '订单已取消，款项原路退回',
            'data': OrderSerializer(order).data,
        })


class RejectOrderView(APIView):
    """陪玩师抢单后拒单：订单回到 PENDING，清除 provider，陪玩师状态恢复 AVAILABLE"""
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        reason = (request.data.get('reason') or '').strip()[:255]

        def pre_check(order):
            if request.account.account_type != ClubAccount.AccountType.PROVIDER:
                raise IllegalTransitionError('仅大神用户可拒单')
            if order.provider_id != request.legacy_user.id:
                raise IllegalTransitionError('仅订单关联的大神可拒单')
            if order.escort_mode != Order.EscortMode.SINGLE:
                raise IllegalTransitionError('双陪订单请联系客服调整，不支持自行拒单')

        def side_effect(order):
            order.provider = None
            order.provider_account = None
            order.provider_name_snapshot = ''
            order.grabbed_at = None
            order.reject_count = (order.reject_count or 0) + 1
            order.auto_cancel_at = pending_timeout_deadline()
            # 不能无脑置 AVAILABLE：这位陪玩手上可能还有别的单在服务中。
            refresh_escort_status(
                provider_ids=[request.legacy_user.id],
                account_ids=[request.account.pk],
                exclude_order_ids=[order.pk],
            )

        try:
            order = transition(
                order_id,
                Order.Status.PENDING,
                operator=request.legacy_user,
                action=OrderStatusLog.Action.REJECT,
                reason=reason,
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=[
                    'provider', 'provider_account', 'provider_name_snapshot', 'grabbed_at',
                    'reject_count', 'auto_cancel_at',
                ],
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许拒单'})

        _schedule_auto_cancel(order)
        enqueue_kook_dispatch(order, KookDispatchRecord.Trigger.PROVIDER_REJECTED)
        notify_order_update(order)

        _push_message_safe(
            recipient_id=order.customer_id,
            title='大神已拒单',
            preview=f'你的「{order.service_name_snapshot}」已回到待接单池，等待其他大神接单',
            msg_type='ORDER',
            related_order_id=order.id,
        )

        return Response({
            'code': 0,
            'msg': '已拒单，订单已回到待接单池',
            'data': OrderSerializer(order).data,
        })


class RefundOrderView(APIView):
    """运营强制退款：支持 GRABBED / IN_SERVICE 状态，全额退款"""
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '仅客服可强制退款'})

        reason = (request.data.get('reason') or '').strip()[:255]
        if not reason:
            return Response({'code': 400, 'msg': '请填写退款原因'})

        def pre_check(order):
            if order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE):
                raise IllegalTransitionError('当前状态不允许强制退款')

        def side_effect(order):
            _refund_to_customer(order, reason=f'客服强制退款：{reason}')
            order.cancel_reason = reason
            # 释放陪玩师状态：按「是否还有其它进行中的单」重算，而不是一律置空闲。
            provider_ids = list(order.providers.values_list('provider_id', flat=True))
            account_ids = list(
                order.providers.values_list('provider_account_id', flat=True)
            )
            if order.provider_id:
                provider_ids.append(order.provider_id)
            if order.provider_account_id:
                account_ids.append(order.provider_account_id)
            refresh_escort_status(
                provider_ids=provider_ids,
                account_ids=account_ids,
                exclude_order_ids=[order.pk],
            )

        try:
            order = transition(
                order_id,
                Order.Status.CANCELLED,
                operator=request.legacy_user,
                action=OrderStatusLog.Action.REFUND,
                reason=reason,
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=['cancel_reason', 'payment_status', 'refunded_at'],
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许退款'})

        notify_order_update(order)

        _push_message_safe(
            recipient_id=order.customer_id,
            title='订单已退款',
            preview=f'客服已为你的「{order.service_name_snapshot}」办理退款，款项已原路退回',
            msg_type='ORDER',
            related_order_id=order.id,
        )
        if order.provider_id:
            _push_message_safe(
                recipient_id=order.provider_id,
                title='订单已强制退款',
                preview=f'订单「{order.order_no}」已被客服强制退款',
                msg_type='ORDER',
                related_order_id=order.id,
            )

        return Response({
            'code': 0,
            'msg': '订单已强制退款',
            'data': OrderSerializer(order).data,
        })


class EvaluateOrderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        serializer = CreateEvaluationSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _format_serializer_errors(serializer.errors)})

        order = serializer.context['order']
        score = serializer.validated_data['score']

        with transaction.atomic():
            evaluation = Evaluation.objects.create(
                order=order,
                customer=request.legacy_user,
                provider=order.provider,
                score=score,
                skill_score=serializer.validated_data['skill_score'],
                attitude_score=serializer.validated_data['attitude_score'],
                communication_score=serializer.validated_data['communication_score'],
                content=serializer.validated_data.get('content', ''),
                is_anonymous=serializer.validated_data.get('is_anonymous', False),
            )
            # 回写陪玩师评分聚合
            _update_escort_rating(order.provider_id, score)

        return Response({
            'code': 0,
            'msg': '评价成功',
            'data': EvaluationSerializer(evaluation).data,
        })


class ServiceEvaluationListView(APIView):
    """C 端商品详情页：某服务的评价列表 + 平均分。"""

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, service_id):
        evaluations = (
            Evaluation.objects.filter(order__service_id=service_id)
            .select_related('customer', 'provider', 'order')
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
        evaluations = (
            Evaluation.objects.filter(provider_id=request.legacy_user.id)
            .select_related('customer', 'provider', 'order')
            .order_by('-created_at')
        )
        avg = evaluations.aggregate(avg=Avg('score'))['avg'] or 0
        return Response({
            'code': 0,
            'data': {
                'list': EvaluationSerializer(evaluations, many=True).data,
                'total': evaluations.count(),
                'avg_score': round(float(avg), 1),
            },
        })


class ReplyEvaluationView(APIView):
    """陪玩端：回复自己收到的评价。"""

    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, evaluation_id):
        content = (request.data.get('reply_content') or '').strip()
        if not content:
            return Response({'code': 400, 'msg': '回复内容不能为空'})
        try:
            evaluation = Evaluation.objects.get(id=evaluation_id)
        except Evaluation.DoesNotExist:
            return Response({'code': 404, 'msg': '评价不存在'})
        if evaluation.provider_id != request.legacy_user.id:
            return Response({'code': 403, 'msg': '仅被评价的陪玩可回复'})

        evaluation.reply_content = content[:1000]
        evaluation.replied_at = timezone.now()
        evaluation.save(update_fields=['reply_content', 'replied_at', 'updated_at'])
        return Response({
            'code': 0,
            'msg': '回复成功',
            'data': EvaluationSerializer(evaluation).data,
        })


class AssignOrderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '仅客服可派单'})

        provider_id = request.data.get('provider_id')
        if not provider_id:
            return Response({'code': 400, 'msg': '请指定陪玩'})

        try:
            provider_account = ClubAccount.objects.select_related(
                'legacy_mapping',
            ).get(
                id=provider_id,
                account_type=ClubAccount.AccountType.PROVIDER,
            )
            provider = User.objects.get(
                id=provider_account.legacy_mapping.legacy_user_id,
            )
        except (ClubAccount.DoesNotExist, User.DoesNotExist):
            return Response({'code': 404, 'msg': '陪玩不存在'})

        profile = EscortProfile.objects.filter(account=provider_account).first()
        if profile is None or profile.status == EscortProfile.Status.OFFLINE:
            return Response({'code': 400, 'msg': '该陪玩当前不可接单'})
        if not EscortSchedule.provider_is_scheduled_now(provider):
            return Response({'code': 400, 'msg': '该陪玩当前不在接单档期'})
        if count_active_orders(provider_account) >= Order.MAX_CONCURRENT_ORDERS:
            return Response({
                'code': 400,
                'msg': f'该陪玩已达同时进行 {Order.MAX_CONCURRENT_ORDERS} 单上限',
            })

        def pre_check(order):
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能派单')

        def side_effect(order):
            order.provider = provider
            order.provider_account = provider_account
            order.provider_name_snapshot = provider.nickname or provider.username
            # 口径与陪玩自助抢单一致：接到单就是 BUSY。
            # 旧实现「未达并发上限就保持 AVAILABLE」会让同一个人在
            # 「客服派单」和「自己抢单」两条路径下显示相反的状态。
            mark_escort_busy(
                provider_ids=[provider.id],
                account_ids=[provider_account.pk],
            )

        try:
            order = transition(
                order_id,
                Order.Status.GRABBED,
                operator=request.legacy_user,
                operator_account=request.account,
                action=OrderStatusLog.Action.ASSIGN,
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=['provider', 'provider_account', 'provider_name_snapshot'],
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前订单状态不允许派单'})

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

        return Response({
            'code': 0,
            'msg': '派单成功',
            'data': OrderSerializer(order).data,
        })


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


def _update_escort_rating(provider_id: int, score: int) -> None:
    """更新陪玩师评分聚合（委托 ratings.apply_escort_rating）。"""
    apply_escort_rating(provider_id, score)


def _format_serializer_errors(errors):
    """把 DRF 的嵌套错误结构压成一句人话，用于统一信封的 msg 字段。"""
    if isinstance(errors, dict):
        first_value = next(iter(errors.values()))
        return _format_serializer_errors(first_value)
    if isinstance(errors, list) and errors:
        return _format_serializer_errors(errors[0])
    return str(errors)
