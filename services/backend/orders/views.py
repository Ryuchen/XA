from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import EscortProfile, EscortSchedule
from wallet.models import Transaction, Wallet, get_platform_wallet

from .models import Evaluation, GameCategory, Order, OrderStatusLog, ServiceFavorite, ServiceItem
from .notifier import notify_order_update
from .ratings import apply_escort_rating
from .settlement import compute_split
from .serializers import (
    CreateEvaluationSerializer,
    CreateOrderSerializer,
    EvaluationSerializer,
    OrderSerializer,
    ServiceItemDetailSerializer,
    ServiceItemSerializer,
)
from .state_machine import IllegalTransitionError, log_only, transition

try:
    from site_messages.utils import create_message
except ImportError:
    create_message = None

User = get_user_model()

# PENDING 订单超时自动取消时长（分钟）
PENDING_TIMEOUT_MINUTES = 30

# 商品详情页评价列表最多展示条数
MAX_SERVICE_EVALUATIONS = 20


class ServiceListView(APIView):
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

    def get(self, request):
        categories = GameCategory.objects.filter(is_active=True).order_by('sort_order', 'id')
        return Response({
            'code': 0,
            'data': [
                {'id': item.id, 'name': item.name, 'remark': item.remark}
                for item in categories
            ],
        })


class ServiceDetailView(APIView):
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

    def get(self, request):
        favorites = ServiceFavorite.objects.filter(
            user=request.user, service__is_active=True
        ).select_related('service')
        data = [
            ServiceItemSerializer(fav.service).data
            for fav in favorites
        ]
        return Response({'code': 0, 'data': data})


class FavoriteToggleView(APIView):
    """收藏 / 取消收藏：幂等切换，返回当前是否已收藏。"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        service_id = request.data.get('service_id')
        if not service_id:
            return Response({'code': 400, 'msg': '缺少 service_id'})
        try:
            service = ServiceItem.objects.get(id=service_id, is_active=True)
        except ServiceItem.DoesNotExist:
            return Response({'code': 404, 'msg': '服务不存在'})

        favorite = ServiceFavorite.objects.filter(user=request.user, service=service).first()
        if favorite:
            favorite.delete()
            return Response({'code': 0, 'msg': '已取消收藏', 'data': {'favorited': False}})

        ServiceFavorite.objects.create(user=request.user, service=service)
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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        status_filter = request.query_params.get('status')
        if user.role == user.Role.PROVIDER:
            if status_filter == 'pending':
                # 待接单池：返回尚未被接单的 PENDING 订单，供陪玩抢单
                profile = EscortProfile.objects.filter(user=user).first()
                if (
                    profile is None
                    or profile.status != EscortProfile.Status.AVAILABLE
                    or not EscortSchedule.provider_is_scheduled_now(user)
                ):
                    orders = Order.objects.none()
                else:
                    delay = profile.order_visibility_delay_seconds
                    visible_before = timezone.now() - timedelta(seconds=delay)
                    orders = Order.objects.filter(
                        Q(auto_cancel_at__isnull=True) | Q(auto_cancel_at__gt=timezone.now()),
                        provider__isnull=True,
                        status=Order.Status.PENDING,
                        payment_status=Order.PaymentStatus.PAID,
                        created_at__lte=visible_before,
                    )
            else:
                orders = Order.objects.filter(
                    Q(provider=user) | Q(providers__provider=user)
                ).distinct()
        elif user.role == user.Role.OPERATOR:
            orders = Order.objects.all()
        else:
            orders = Order.objects.filter(customer=user)

        if status_filter and status_filter != 'all':
            status_map = {
                'pending': Order.Status.PENDING,
                'grabbed': Order.Status.GRABBED,
                'in_progress': Order.Status.IN_SERVICE,
                'completed': Order.Status.COMPLETED,
                'cancelled': Order.Status.CANCELLED,
            }
            backend_status = status_map.get(status_filter)
            if backend_status:
                orders = orders.filter(status=backend_status)

        orders = orders.select_related(
            'customer', 'provider', 'service', 'service__service_category',
            'evaluation', 'support_contact'
        ).order_by('-created_at')
        serializer = OrderSerializer(orders, many=True, context={'request': request})
        return Response({'code': 0, 'data': serializer.data})


class CreateOrderView(APIView):
    permission_classes = [IsAuthenticated]

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
        inviter_commission_rate = serializer.validated_data['inviter_commission_rate']
        promotion = serializer.validated_data.get('promotion')
        original_amount = serializer.validated_data['original_amount']
        boss_discount = serializer.validated_data['boss_discount']
        promo_discount = serializer.validated_data['promo_discount']
        coupon_discount = serializer.validated_data['coupon_discount']

        split = compute_split(amount, commission_rate, inviter_commission_rate)

        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(user=request.user)
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
                    .filter(id=user_coupon.id, user=request.user, status=UserCoupon.Status.UNUSED)
                    .first()
                )
                if locked_coupon is None:
                    return Response({'code': 400, 'msg': '优惠券已使用或已失效'})

            balance_before = wallet.balance
            wallet.balance -= amount
            wallet.save(update_fields=['balance'])

            order = Order.objects.create(
                customer=request.user,
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
                    else timezone.now() + timedelta(minutes=PENDING_TIMEOUT_MINUTES)
                ),
                original_amount=original_amount,
                boss_discount=boss_discount,
                promo_discount=promo_discount,
                coupon_discount=coupon_discount,
                promotion=promotion,
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter=inviter,
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

            if locked_coupon is not None:
                from coupons.models import UserCoupon
                locked_coupon.status = UserCoupon.Status.USED
                locked_coupon.order = order
                locked_coupon.used_at = timezone.now()
                locked_coupon.save(update_fields=['status', 'order', 'used_at'])

            log_only(
                order,
                action=OrderStatusLog.Action.CREATE,
                operator=request.user,
                reason='下单',
            )

            if provider is not None:
                def assign_selected(o):
                    o.provider = provider
                    o.provider_name_snapshot = (
                        provider.escort_profile.display_name
                        or provider.nickname or provider.username
                    )
                    EscortProfile.objects.filter(user=provider).update(
                        status=EscortProfile.Status.BUSY,
                    )

                order = transition(
                    order.id,
                    Order.Status.GRABBED,
                    operator=request.user,
                    action=OrderStatusLog.Action.ASSIGN,
                    reason='老板指定陪玩',
                    side_effect=assign_selected,
                    update_fields=['provider', 'provider_name_snapshot'],
                )

        if provider is None:
            _schedule_auto_cancel(order)
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

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _format_serializer_errors(serializer.errors)})

        data = serializer.validated_data
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
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

    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        if request.user.role != request.user.Role.CUSTOMER:
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

        with transaction.atomic():
            try:
                source_order = Order.objects.select_for_update().select_related(
                    'provider__escort_profile__level', 'service__service_category', 'support_contact',
                ).get(id=order_id, customer=request.user)
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
            customer_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=request.user)
            if not customer_wallet.is_active:
                return Response({'code': 403, 'msg': '钱包不可用'})
            if customer_wallet.balance < amount:
                return Response({'code': 400, 'msg': '兴安币不足，请联系企业微信客服充值'})

            from .pricing import resolve_commission_rate
            commission_rate = resolve_commission_rate(
                gift_service, getattr(provider, 'escort_profile', None), None,
            )
            inviter = request.user.inviter
            inviter_rate = request.user.inviter_commission_rate if inviter else 0
            split = compute_split(amount, commission_rate, inviter_rate)
            now = timezone.now()
            gift_order = Order.objects.create(
                customer=request.user,
                provider=provider,
                service=gift_service,
                source_order=source_order,
                support_contact=source_order.support_contact,
                amount=amount,
                original_amount=amount,
                status=Order.Status.COMPLETED,
                payment_status=Order.PaymentStatus.PAID,
                completed_at=now,
                remark=f'赠送给{source_order.provider_name_snapshot or provider.nickname or provider.username}',
                commission_rate=split.commission_rate,
                provider_income=split.provider_income,
                inviter=inviter,
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

            provider_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=provider)
            provider_before = provider_wallet.balance
            provider_wallet.balance += split.provider_income
            provider_wallet.save(update_fields=['balance'])
            Transaction.objects.create(
                wallet=provider_wallet, order=gift_order, amount=split.provider_income,
                tx_type=Transaction.TxType.GIFT,
                balance_before=provider_before, balance_after=provider_wallet.balance,
                remark=f'收到礼物：{gift_service.name}',
            )

            if inviter and split.inviter_commission:
                inviter_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=inviter)
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
                operator=request.user, reason=f'完成订单快捷打赏，来源订单 {source_order.order_no}',
            )

        _push_message_safe(
            recipient_id=provider.id,
            title='收到老板礼物',
            preview=f'{request.user.nickname or "老板"}送给你「{gift_service.name}」',
            msg_type='PROMOTION',
            related_order_id=gift_order.id,
        )
        return Response({
            'code': 0,
            'msg': '礼物已送达',
            'data': OrderSerializer(gift_order, context={'request': request}).data,
        })


class GrabOrderView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        if request.user.role != request.user.Role.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可接单'})

        try:
            escort_profile = request.user.escort_profile
        except EscortProfile.DoesNotExist:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料，无法接单'})

        if escort_profile.status != EscortProfile.Status.AVAILABLE:
            return Response({'code': 400, 'msg': '当前大神状态不可接单'})
        if not EscortSchedule.provider_is_scheduled_now(request.user):
            return Response({'code': 400, 'msg': '当前时间不在你的接单档期内'})

        def pre_check(order):
            if order.provider_id is not None:
                raise IllegalTransitionError('订单已被其他大神接单')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能接单')
            if order.auto_cancel_at and order.auto_cancel_at <= timezone.now():
                raise IllegalTransitionError('订单已超时，请刷新抢单池')
            visible_at = order.created_at + timedelta(
                seconds=escort_profile.order_visibility_delay_seconds,
            )
            if timezone.now() < visible_at:
                remaining = max(1, int((visible_at - timezone.now()).total_seconds()) + 1)
                raise IllegalTransitionError(f'当前通行证需等待 {remaining} 秒后才能抢此单')

        def side_effect(order):
            order.provider = request.user
            order.provider_name_snapshot = request.user.nickname or request.user.username
            # 修复 Bug：抢单后将陪玩师状态设为 BUSY
            EscortProfile.objects.filter(user=request.user).update(status=EscortProfile.Status.BUSY)

        try:
            order = transition(
                order_id,
                Order.Status.GRABBED,
                operator=request.user,
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=['provider', 'provider_name_snapshot'],
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
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        if request.user.role != request.user.Role.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可开始服务'})

        def pre_check(order):
            if order.provider_id != request.user.id:
                raise IllegalTransitionError('仅订单关联的大神可开始服务')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能开始服务')

        try:
            order = transition(
                order_id,
                Order.Status.IN_SERVICE,
                operator=request.user,
                pre_check=pre_check,
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
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        if request.user.role != request.user.Role.PROVIDER:
            return Response({'code': 403, 'msg': '仅大神用户可完成订单'})

        def pre_check(order):
            if order.provider_id != request.user.id:
                raise IllegalTransitionError('仅订单关联的大神可完成订单')
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能完成订单')

        def side_effect(order):
            provider_rows = list(order.providers.select_for_update().all())
            settled_provider_ids = []
            if provider_rows:
                for row in provider_rows:
                    if not row.provider_id or row.settled_at:
                        continue
                    provider_wallet, _ = Wallet.objects.select_for_update().get_or_create(
                        user_id=row.provider_id,
                    )
                    balance_before = provider_wallet.balance
                    provider_wallet.balance += row.provider_income
                    provider_wallet.save(update_fields=['balance'])
                    Transaction.objects.create(
                        wallet=provider_wallet, order=order, amount=row.provider_income,
                        tx_type=Transaction.TxType.INCOME,
                        balance_before=balance_before, balance_after=provider_wallet.balance,
                        remark=f'订单收入：{order.order_no}',
                    )
                    row.settled_at = timezone.now()
                    row.save(update_fields=['settled_at'])
                    settled_provider_ids.append(row.provider_id)
            else:
                provider_income = order.provider_income or order.amount
                provider_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=request.user)
                balance_before = provider_wallet.balance
                provider_wallet.balance += provider_income
                provider_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=provider_wallet, order=order, amount=provider_income,
                    tx_type=Transaction.TxType.INCOME,
                    balance_before=balance_before, balance_after=provider_wallet.balance,
                    remark=f'订单收入：{order.order_no}',
                )
                settled_provider_ids.append(request.user.id)
            # 推荐人分佣入账
            if order.inviter_id and order.inviter_commission:
                inviter_wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=order.inviter_id)
                inv_before = inviter_wallet.balance
                inviter_wallet.balance += order.inviter_commission
                inviter_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=inviter_wallet,
                    order=order,
                    amount=order.inviter_commission,
                    tx_type=Transaction.TxType.INCOME,
                    balance_before=inv_before,
                    balance_after=inviter_wallet.balance,
                    remark=f'推荐分佣：{order.order_no}',
                )
            # 店铺/平台留存入账：归集到平台系统账户钱包
            if order.shop_income:
                platform_wallet = get_platform_wallet(for_update=True)
                shop_before = platform_wallet.balance
                platform_wallet.balance += order.shop_income
                platform_wallet.save(update_fields=['balance'])
                Transaction.objects.create(
                    wallet=platform_wallet,
                    order=order,
                    amount=order.shop_income,
                    tx_type=Transaction.TxType.SHOP_INCOME,
                    balance_before=shop_before,
                    balance_after=platform_wallet.balance,
                    remark=f'平台收入：{order.order_no}',
                )
            # 修复 Bug：完成订单后陪玩师状态恢复 AVAILABLE，并累加完成订单计数
            EscortProfile.objects.filter(user_id__in=settled_provider_ids).update(
                status=EscortProfile.Status.AVAILABLE,
            )
            for profile in EscortProfile.objects.filter(user_id__in=settled_provider_ids):
                profile.completed_order_count = (profile.completed_order_count or 0) + 1
                profile.save(update_fields=['completed_order_count'])

        try:
            order = transition(
                order_id,
                Order.Status.COMPLETED,
                operator=request.user,
                pre_check=pre_check,
                side_effect=side_effect,
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前订单状态不允许完成'})

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
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        reason = (request.data.get('reason') or '').strip()[:255]

        def pre_check(order):
            if order.customer_id != request.user.id:
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
                operator=request.user,
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
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        reason = (request.data.get('reason') or '').strip()[:255]

        def pre_check(order):
            if request.user.role != request.user.Role.PROVIDER:
                raise IllegalTransitionError('仅大神用户可拒单')
            if order.provider_id != request.user.id:
                raise IllegalTransitionError('仅订单关联的大神可拒单')
            if order.escort_mode != Order.EscortMode.SINGLE:
                raise IllegalTransitionError('双陪订单请联系客服调整，不支持自行拒单')

        def side_effect(order):
            order.provider = None
            order.provider_name_snapshot = ''
            order.grabbed_at = None
            order.reject_count = (order.reject_count or 0) + 1
            order.auto_cancel_at = timezone.now() + timedelta(minutes=PENDING_TIMEOUT_MINUTES)
            EscortProfile.objects.filter(user=request.user).update(status=EscortProfile.Status.AVAILABLE)

        try:
            order = transition(
                order_id,
                Order.Status.PENDING,
                operator=request.user,
                action=OrderStatusLog.Action.REJECT,
                reason=reason,
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=[
                    'provider', 'provider_name_snapshot', 'grabbed_at',
                    'reject_count', 'auto_cancel_at',
                ],
            )
        except Order.DoesNotExist:
            return Response({'code': 404, 'msg': '订单不存在'})
        except IllegalTransitionError as exc:
            return Response({'code': 400, 'msg': str(exc) or '当前状态不允许拒单'})

        _schedule_auto_cancel(order)
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
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        if request.user.role != request.user.Role.OPERATOR:
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
            # 释放陪玩师状态
            provider_ids = list(order.providers.values_list('provider_id', flat=True))
            if order.provider_id:
                provider_ids.append(order.provider_id)
            EscortProfile.objects.filter(user_id__in=provider_ids).update(
                status=EscortProfile.Status.AVAILABLE,
            )

        try:
            order = transition(
                order_id,
                Order.Status.CANCELLED,
                operator=request.user,
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
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CreateEvaluationSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _format_serializer_errors(serializer.errors)})

        order = serializer.context['order']
        score = serializer.validated_data['score']

        with transaction.atomic():
            evaluation = Evaluation.objects.create(
                order=order,
                customer=request.user,
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

    permission_classes = [IsAuthenticated]

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

    permission_classes = [IsAuthenticated]

    def get(self, request):
        evaluations = (
            Evaluation.objects.filter(provider_id=request.user.id)
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

    permission_classes = [IsAuthenticated]

    def post(self, request, evaluation_id):
        content = (request.data.get('reply_content') or '').strip()
        if not content:
            return Response({'code': 400, 'msg': '回复内容不能为空'})
        try:
            evaluation = Evaluation.objects.get(id=evaluation_id)
        except Evaluation.DoesNotExist:
            return Response({'code': 404, 'msg': '评价不存在'})
        if evaluation.provider_id != request.user.id:
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
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        if request.user.role != request.user.Role.OPERATOR:
            return Response({'code': 403, 'msg': '仅客服可派单'})

        provider_id = request.data.get('provider_id')
        if not provider_id:
            return Response({'code': 400, 'msg': '请指定陪玩'})

        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            provider = User.objects.get(id=provider_id, role=User.Role.PROVIDER)
        except User.DoesNotExist:
            return Response({'code': 404, 'msg': '陪玩不存在'})

        profile = EscortProfile.objects.filter(user=provider).first()
        if profile is None or profile.status != EscortProfile.Status.AVAILABLE:
            return Response({'code': 400, 'msg': '该陪玩当前不可接单'})
        if not EscortSchedule.provider_is_scheduled_now(provider):
            return Response({'code': 400, 'msg': '该陪玩当前不在接单档期'})

        def pre_check(order):
            if order.payment_status != Order.PaymentStatus.PAID:
                raise IllegalTransitionError('订单未支付，不能派单')

        def side_effect(order):
            order.provider = provider
            order.provider_name_snapshot = provider.nickname or provider.username
            EscortProfile.objects.filter(user=provider).update(status=EscortProfile.Status.BUSY)

        try:
            order = transition(
                order_id,
                Order.Status.GRABBED,
                operator=request.user,
                action=OrderStatusLog.Action.ASSIGN,
                pre_check=pre_check,
                side_effect=side_effect,
                update_fields=['provider', 'provider_name_snapshot'],
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
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role == user.Role.PROVIDER:
            orders = Order.objects.filter(
                Q(provider=user) | Q(providers__provider=user)
            ).distinct()
        elif user.role == user.Role.OPERATOR:
            orders = Order.objects.all()
        else:
            orders = Order.objects.filter(customer=user)

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

def _refund_to_customer(order: Order, reason: str = '订单退款') -> None:
    """全额退款到客户钱包，并写流水。在 transaction.atomic 中调用。"""
    customer_wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=order.customer_id)
    balance_before = customer_wallet.balance
    customer_wallet.balance += order.amount
    customer_wallet.save(update_fields=['balance'])
    Transaction.objects.create(
        wallet=customer_wallet,
        order=order,
        amount=order.amount,
        tx_type=Transaction.TxType.REFUND,
        balance_before=balance_before,
        balance_after=customer_wallet.balance,
        remark=f'{reason}：{order.order_no}',
    )
    order.payment_status = Order.PaymentStatus.REFUNDED
    order.refunded_at = timezone.now()
    from coupons.models import UserCoupon
    UserCoupon.objects.filter(
        order=order, status=UserCoupon.Status.USED,
    ).update(status=UserCoupon.Status.UNUSED, order=None, used_at=None)


def _update_escort_rating(provider_id: int, score: int) -> None:
    """更新陪玩师评分聚合（委托 ratings.apply_escort_rating）。"""
    apply_escort_rating(provider_id, score)


def _schedule_auto_cancel(order: Order) -> None:
    """投递超时取消任务到 Celery；不可用时静默失败。"""
    try:
        from .tasks import auto_cancel_pending_order
        auto_cancel_pending_order.apply_async(
            args=[order.id],
            countdown=PENDING_TIMEOUT_MINUTES * 60,
        )
    except Exception:
        # Celery 未启动时由 management command 兜底
        pass


def _format_serializer_errors(errors):
    if isinstance(errors, dict):
        first_value = next(iter(errors.values()))
        return _format_serializer_errors(first_value)
    if isinstance(errors, list) and errors:
        return _format_serializer_errors(errors[0])
    return str(errors)


def _push_message_safe(*, recipient_id, title, preview='', detail='',
                       msg_type='SYSTEM', action_url='', related_order_id=None):
    """安全推送站内消息：site_messages 不可用或异常时静默失败，绝不影响主流程。"""
    if create_message is None or recipient_id is None:
        return
    if related_order_id and not action_url:
        action_url = f'/pages/orderList/index?orderId={related_order_id}'
    try:
        create_message(
            recipient_id=recipient_id,
            title=title,
            preview=preview,
            detail=detail,
            msg_type=msg_type,
            action_url=action_url,
            related_order_id=related_order_id,
        )
    except Exception:
        pass
