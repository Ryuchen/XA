"""清空数据库业务数据并重建全流程联调数据。

流程：flush 全库 → 重建系统数据（平台账户/超管/4个后台角色）→
造 3 客服 + 8 老板 + 20 陪玩 + 4 游戏/服务项 + 全流程订单业务数据。

⚠️ 破坏性操作：flush 会清空所有表数据，仅用于本地开发环境。
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from console.models import AdminMembership, AdminRole
from console.permissions import ALL_PERMISSIONS
from coupons.models import Coupon, UserCoupon
from orders.models import (
    Evaluation,
    GameCategory,
    Order,
    OrderStatusLog,
    ServiceCategory,
    ServiceItem,
)
from site_messages.models import Message
from users.models import BossType, EscortLevel, EscortProfile
from wallet.models import (
    DisposeRecord,
    ProviderReport,
    ProviderReportImage,
    Transaction,
    Wallet,
    WithdrawRequest,
)

PASSWORD = 'test1234'
SUPERUSER_NAME = 'admin'
SUPERUSER_PASSWORD = 'admin123'
CUSTOMER_PREFIX = 'boss_'
PROVIDER_PREFIX = 'provider_'
ORDER_PREFIX = 'XA-DEMO-'

# 4 个后台角色种子：与 console/migrations/0002_seed_roles.py 保持一致。
ROLE_SEEDS = [
    {'code': 'super_admin', 'name': '超级管理员', 'description': '系统最高权限',
     'sort_order': 0, 'all_permissions': True},
    {'code': 'operator', 'name': '运营', 'description': '内容与营销运营', 'sort_order': 1,
     'permissions': [
         'dashboard:view', 'user:view',
         'escort:view', 'escort:edit', 'escort:verify',
         'order:view',
         'coupon:view', 'coupon:edit', 'coupon:delete',
         'announcement:view', 'announcement:edit', 'announcement:delete',
         'banner:view', 'banner:edit', 'banner:delete',
         'achievement:view', 'achievement:edit', 'achievement:delete',
         'service:view', 'service:edit', 'service:delete',
         'message:view', 'message:push',
     ]},
    {'code': 'finance', 'name': '财务', 'description': '钱包与订单结算', 'sort_order': 2,
     'permissions': [
         'dashboard:view', 'user:view',
         'order:view', 'order:refund', 'order:cancel',
         'wallet:view', 'wallet:adjust', 'transaction:view',
         'coupon:view',
     ]},
    {'code': 'support', 'name': '客服', 'description': '用户支持与消息', 'sort_order': 3,
     'permissions': [
         'dashboard:view', 'user:view', 'order:view',
         'message:view', 'message:push',
         'support:view', 'support:edit', 'support:delete',
         'announcement:view',
     ]},
]

GAME_NAMES = ['三角洲行动', '无畏契约', '英雄联盟', '王者荣耀']
SERVICE_NAMES = ['战术护航', '英雄教学', '排位上分', '娱乐陪玩']


class Command(BaseCommand):
    help = '清空全库并重建系统数据 + 3客服/8老板/20陪玩/4游戏/全流程订单联调数据'

    def add_arguments(self, parser):
        parser.add_argument('--customers', type=int, default=8)
        parser.add_argument('--providers', type=int, default=20)
        parser.add_argument('--password', default=PASSWORD)

    def handle(self, *args, **options):
        # flush 需在独立事务中先行提交，随后造数再包一层原子事务。
        self.stdout.write(self.style.WARNING('正在清空数据库全部数据（flush）...'))
        call_command('flush', interactive=False, verbosity=0)
        self.stdout.write(self.style.SUCCESS('数据库已清空'))
        self._seed(options)

    @transaction.atomic
    def _seed(self, options):
        customer_count = max(1, options['customers'])
        provider_count = max(1, options['providers'])
        password = options['password']
        User = get_user_model()
        now = timezone.now()

        # ---------------- 系统数据重建 ----------------
        roles = self._rebuild_roles()
        self._rebuild_platform_account(User)
        superuser = self._rebuild_superuser(User, roles['super_admin'])

        # ---------------- 3 客服（分绑运营/财务/客服） ----------------
        cs_specs = [
            ('cs_operator', '运营客服', 'operator'),
            ('cs_finance', '财务客服', 'finance'),
            ('cs_support', '售后客服', 'support'),
        ]
        operator = None
        for index, (username, nickname, role_code) in enumerate(cs_specs, 1):
            user = User.objects.create_user(
                username=username, password=password, nickname=nickname,
                role=User.Role.OPERATOR, phone=f'13990000{index:03d}', can_login=True,
            )
            membership = AdminMembership.objects.create(user=user)
            membership.roles.set([roles[role_code]])
            if operator is None:
                operator = user  # 首个客服承载订单/审核操作人

        # ---------------- 基础配置：老板/陪玩等级、游戏、服务项 ----------------
        boss_types = [
            BossType.objects.create(name=name, discount_rate=rate, color=color, sort_order=idx, remark='联调等级')
            for idx, (name, rate, color) in enumerate(
                [('普通老板', 100, ''), ('白银老板', 98, '#9AA5B1'),
                 ('黄金老板', 95, '#C99A2E'), ('黑钻老板', 90, '#2E2A45')], 1)
        ]
        escort_levels = [
            EscortLevel.objects.create(name=name, commission_rate=rate, sort_order=idx, remark='联调等级')
            for idx, (name, rate) in enumerate(
                [('新秀', 25), ('进阶', 22), ('精英', 18), ('王牌', 15)], 1)
        ]
        game_categories = [
            GameCategory.objects.create(name=name, sort_order=idx, remark='联调游戏')
            for idx, name in enumerate(GAME_NAMES, 1)
        ]
        service_category = ServiceCategory.objects.create(
            name='陪玩服务', sort_order=1, remark='联调服务类目')
        services = []
        for index, (game, service_name) in enumerate(zip(game_categories, SERVICE_NAMES), 1):
            services.append(ServiceItem.objects.create(
                name=f'{game.name}·{service_name}',
                description=f'{game.name}{service_name}联调参考商品',
                price=3600 + index * 700,
                game_category=game,
                service_category=service_category,
                commission_rate=20,
                cover_url=f'https://picsum.photos/seed/xa-service-{index}/320/320',
                highlights=['实名认证', '平台担保', '售后保障'],
                sort_order=index,
                is_active=True,
            ))

        # ---------------- 8 老板 ----------------
        customers = []
        for index in range(1, customer_count + 1):
            user = User.objects.create_user(
                username=f'{CUSTOMER_PREFIX}{index:02d}', password=password,
                nickname=f'老板{index:02d}号', real_name=f'老板测试{index:02d}',
                phone=f'13888{index:06d}', openid=f'boss_openid_{index:02d}',
                role=User.Role.CUSTOMER,
                boss_type=boss_types[(index - 1) % len(boss_types)],
                game_region=['微信区', 'QQ区', '安卓区', '苹果区'][(index - 1) % 4],
                game_nickname=f'老板游戏名{index:02d}', game_uid=f'BOSS-UID-{index:04d}',
                is_phone_verified=index % 3 != 0, is_openid_bound=True, can_login=True,
            )
            Wallet.objects.update_or_create(user=user, defaults={
                'balance': 50000 + index * 5000, 'total_recharge': 100000 + index * 10000,
                'total_gift': index * 500, 'frozen_amount': 0,
            })
            customers.append(user)

        # ---------------- 20 陪玩 ----------------
        providers = []
        for index in range(1, provider_count + 1):
            user = User.objects.create_user(
                username=f'{PROVIDER_PREFIX}{index:02d}', password=password,
                nickname=f'打手{index:02d}号', real_name=f'打手测试{index:02d}',
                phone=f'13777{index:06d}', role=User.Role.PROVIDER, can_login=True,
            )
            profile = EscortProfile.objects.create(
                user=user,
                display_name=f'{GAME_NAMES[(index - 1) % len(GAME_NAMES)]}大神·{index:02d}',
                gender=EscortProfile.Gender.FEMALE if index % 3 == 0 else EscortProfile.Gender.MALE,
                bio='联调测试打手，可用于抢单、报单、评价回复和提现测试。',
                city=['北京', '上海', '广州', '深圳', '杭州'][(index - 1) % 5],
                service_area=GAME_NAMES[(index - 1) % len(GAME_NAMES)],
                price_per_hour=5000 + index * 200,
                rank_tier=['钻石', '星耀', '王者', '巅峰赛1800'][(index - 1) % 4],
                level=escort_levels[(index - 1) % len(escort_levels)],
                win_rate=70 + (index % 20),
                status=EscortProfile.Status.AVAILABLE,
                is_verified=index % 5 != 0,
                deposit_required=20000, deposit_paid=20000 if index % 4 != 0 else 10000,
                total_reward=index * 100, total_penalty=(index % 4) * 50,
                rating_avg=4 + (index % 10) / 10, rating_count=5 + index,
                completed_order_count=10 + index * 2,
            )
            # 可接游戏（n:n）：每个陪玩绑定 1~3 个游戏，主项为其 service_area 对应游戏，
            # 再按序追加相邻游戏，体现"一个陪玩接多种游戏"。
            game_count = 1 + (index % 3)
            escort_games = [
                game_categories[(index - 1 + offset) % len(game_categories)]
                for offset in range(game_count)
            ]
            profile.game_categories.set(escort_games)
            # 可接服务项（n:n）：陪玩可接其绑定游戏下的全部服务项，体现"选游戏再选具体类目"。
            escort_game_ids = {game.id for game in escort_games}
            escort_services = [s for s in services if s.game_category_id in escort_game_ids]
            profile.service_items.set(escort_services)
            Wallet.objects.update_or_create(user=user, defaults={
                'balance': 30000 + index * 4000, 'frozen_amount': 0,
            })
            providers.append(user)

        # ---------------- 优惠券 ----------------
        coupon_unused = Coupon.objects.create(
            name='无门槛100兴安币券', discount_type=Coupon.DiscountType.DIRECT,
            threshold=0, amount=1000, valid_to=now + timedelta(days=90), is_active=True)
        coupon_used = Coupon.objects.create(
            name='满500减80兴安币券', discount_type=Coupon.DiscountType.THRESHOLD,
            threshold=5000, amount=800, valid_to=now + timedelta(days=90), is_active=True)

        # ---------------- 全流程订单 ----------------
        order_count = self._seed_orders(
            customers, providers, services, operator, now,
            coupon_unused, coupon_used, provider_count)

        # 订单造完后按实际进行中订单数（GRABBED + IN_SERVICE）回填陪玩状态：
        # 达到并发上限的置 BUSY 退出抢单池，其余保持 AVAILABLE，确保状态与数据一致。
        for provider in providers:
            active = Order.objects.filter(
                Q(provider=provider) | Q(providers__provider=provider),
                status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE],
            ).distinct().count()
            EscortProfile.objects.filter(user=provider).update(
                status=EscortProfile.Status.BUSY
                if active >= Order.MAX_CONCURRENT_ORDERS
                else EscortProfile.Status.AVAILABLE,
            )

        # ---------------- 提现/奖惩/陪玩消息 ----------------
        self._seed_provider_flows(providers, operator, now)

        self.stdout.write(self.style.SUCCESS(
            f'联调数据重建完成：客服 3 个，老板 {len(customers)} 个，'
            f'打手 {len(providers)} 个，订单 {order_count} 笔'))
        self.stdout.write(f'超管：{SUPERUSER_NAME} / {SUPERUSER_PASSWORD}')
        self.stdout.write(f'客服：cs_operator / cs_finance / cs_support（密码 {password}）')
        self.stdout.write(f'老板：{CUSTOMER_PREFIX}01 ~ {CUSTOMER_PREFIX}%02d（密码 {password}）' % customer_count)
        self.stdout.write(f'打手：{PROVIDER_PREFIX}01 ~ {PROVIDER_PREFIX}%02d（密码 {password}）' % provider_count)
        available_count = EscortProfile.objects.filter(
            user__in=providers, status=EscortProfile.Status.AVAILABLE,
        ).count()
        self.stdout.write(
            f'可抢单打手：{available_count} 个（其余因进行中订单已达 '
            f'{Order.MAX_CONCURRENT_ORDERS} 单上限暂置 BUSY）')

    # ------------------------------------------------------------------
    # 系统数据重建
    # ------------------------------------------------------------------
    @staticmethod
    def _rebuild_roles():
        roles = {}
        for seed in ROLE_SEEDS:
            perms = ALL_PERMISSIONS if seed.get('all_permissions') else seed.get('permissions', [])
            role = AdminRole.objects.create(
                code=seed['code'], name=seed['name'], description=seed['description'],
                permissions=perms, sort_order=seed['sort_order'], is_active=True)
            roles[seed['code']] = role
        return roles

    @staticmethod
    def _rebuild_platform_account(User):
        username = getattr(settings, 'PLATFORM_SYSTEM_USERNAME', '__platform__')
        user = User.objects.create(
            username=username, password=make_password(None),
            is_active=False, is_staff=False, is_superuser=False,
            role=User.Role.ADMIN, nickname='平台账户')
        Wallet.objects.get_or_create(user=user)
        return user

    @staticmethod
    def _rebuild_superuser(User, super_role):
        user = User.objects.create_superuser(
            username=SUPERUSER_NAME, password=SUPERUSER_PASSWORD, nickname='超级管理员')
        membership = AdminMembership.objects.create(user=user)
        membership.roles.set([super_role])
        return user

    # ------------------------------------------------------------------
    # 订单全流程
    # ------------------------------------------------------------------
    def _seed_orders(self, customers, providers, services, operator, now,
                     coupon_unused, coupon_used, provider_count):
        order_count = 0
        report_statuses = [
            ProviderReport.Status.DRAFT, ProviderReport.Status.PENDING,
            ProviderReport.Status.APPROVED, ProviderReport.Status.REJECTED,
        ]
        status_specs = [
            ('PENDING', Order.Status.PENDING),
            ('GRABBED', Order.Status.GRABBED),
            ('SERVICE', Order.Status.IN_SERVICE),
            ('DONE-EVAL', Order.Status.COMPLETED),
            ('DONE-REPORT', Order.Status.COMPLETED),
            ('CANCELLED', Order.Status.CANCELLED),
        ]
        for customer_index, customer in enumerate(customers, 1):
            customer_orders = []
            for stage_index, (stage_code, status) in enumerate(status_specs):
                service = services[(customer_index + stage_index - 1) % len(services)]
                provider = None
                if status == Order.Status.GRABBED:
                    provider = providers[6 + ((customer_index - 1) % max(1, provider_count - 6))] \
                        if provider_count > 6 else providers[0]
                elif status == Order.Status.IN_SERVICE:
                    provider = providers[6 + ((customer_index + 4) % max(1, provider_count - 6))] \
                        if provider_count > 6 else providers[0]
                elif status == Order.Status.COMPLETED:
                    provider = providers[(customer_index - 1) % provider_count]

                rounds = 1 + ((customer_index + stage_index) % 4)
                original_amount = service.price * rounds
                boss_discount = (customer_index % 4) * 100
                coupon_discount = 800 if stage_code == 'DONE-EVAL' else 0
                amount = max(100, original_amount - boss_discount - coupon_discount)
                commission_rate = provider.escort_profile.level.commission_rate if provider else 20
                provider_income = amount * (100 - commission_rate) // 100
                created_at = now - timedelta(days=stage_index, hours=customer_index)
                order = Order.objects.create(
                    order_no=f'{ORDER_PREFIX}{customer_index:02d}-{stage_code}',
                    customer=customer, provider=provider, service=service,
                    amount=amount, original_amount=original_amount,
                    boss_discount=boss_discount, coupon_discount=coupon_discount,
                    commission_rate=commission_rate, provider_income=provider_income,
                    shop_income=amount - provider_income, game_rounds=rounds,
                    game_region=customer.game_region, game_nickname=customer.game_nickname,
                    game_uid=customer.game_uid,
                    remark=[
                        '希望普通话清晰，开局前先确认英雄和位置。',
                        '已经约好时间，请提前五分钟联系，优先稳定上分。',
                        '正在服务中，要求积极沟通并及时同步战况。',
                        '服务顺利完成，用于测试评价和打手回复。',
                        '服务已完成，等待打手上传入队、结单和战绩截图。',
                        '老板改变计划取消，用于测试退款和取消原因。',
                    ][stage_index],
                    payment_status=(
                        Order.PaymentStatus.REFUNDED if status == Order.Status.CANCELLED
                        else Order.PaymentStatus.PAID),
                    status=status,
                    auto_cancel_at=now + timedelta(minutes=30) if status == Order.Status.PENDING else None,
                    grabbed_at=created_at + timedelta(minutes=3) if status in (
                        Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED) else None,
                    in_service_at=created_at + timedelta(minutes=8) if status in (
                        Order.Status.IN_SERVICE, Order.Status.COMPLETED) else None,
                    completed_at=created_at + timedelta(hours=1) if status == Order.Status.COMPLETED else None,
                    cancelled_at=created_at + timedelta(minutes=6) if status == Order.Status.CANCELLED else None,
                    refunded_at=created_at + timedelta(minutes=7) if status == Order.Status.CANCELLED else None,
                    cancel_reason='临时有事，申请取消并退款' if status == Order.Status.CANCELLED else '',
                )
                Order.objects.filter(pk=order.pk).update(created_at=created_at, updated_at=created_at)
                self._seed_status_logs(order, operator, created_at)
                customer_orders.append(order)
                order_count += 1

                if status != Order.Status.CANCELLED:
                    Transaction.objects.create(
                        wallet=customer.wallet, tx_no=f'DEMO-PAY-{customer_index:02d}-{stage_index}',
                        order=order, amount=-amount, tx_type=Transaction.TxType.PAY,
                        balance_before=customer.wallet.balance + amount,
                        balance_after=customer.wallet.balance,
                        remark=f'联调订单支付：{order.order_no}')
                else:
                    Transaction.objects.create(
                        wallet=customer.wallet, tx_no=f'DEMO-REFUND-{customer_index:02d}',
                        order=order, amount=amount, tx_type=Transaction.TxType.REFUND,
                        balance_before=customer.wallet.balance - amount,
                        balance_after=customer.wallet.balance,
                        remark=f'联调订单退款：{order.order_no}')

                if status == Order.Status.COMPLETED and provider:
                    Transaction.objects.create(
                        wallet=provider.wallet, tx_no=f'DEMO-INCOME-{customer_index:02d}-{stage_index}',
                        order=order, amount=provider_income, tx_type=Transaction.TxType.INCOME,
                        balance_before=provider.wallet.balance - provider_income,
                        balance_after=provider.wallet.balance,
                        remark=f'联调订单收入：{order.order_no}')

                if stage_code == 'DONE-EVAL' and provider:
                    Evaluation.objects.create(
                        order=order, customer=customer, provider=provider,
                        score=4 + customer_index % 2, skill_score=5,
                        attitude_score=4 + customer_index % 2, communication_score=4,
                        content='打手技术稳定，沟通及时，整体体验很好。',
                        reply_content=('感谢老板认可，下次继续带你上分！' if customer_index % 2 else ''),
                        replied_at=(now - timedelta(hours=customer_index) if customer_index % 2 else None))

                if stage_code == 'DONE-REPORT' and provider:
                    report_status = report_statuses[(customer_index - 1) % len(report_statuses)]
                    audited = report_status in (ProviderReport.Status.APPROVED, ProviderReport.Status.REJECTED)
                    report = ProviderReport.objects.create(
                        provider=provider, order=order, game_name=service.name,
                        description='平台订单完成后的联调报单，包含三类截图。',
                        amount=amount, entry_image='reports/entry/entry.gif',
                        completion_image='reports/completion/completion.gif',
                        status=report_status, commission_rate=commission_rate,
                        payout_amount=provider_income if report_status == ProviderReport.Status.APPROVED else 0,
                        remark='请核对局数、战绩和结单时间。',
                        audit_remark=(
                            '截图完整，审核通过' if report_status == ProviderReport.Status.APPROVED
                            else '战绩截图不清晰，请重新提交' if report_status == ProviderReport.Status.REJECTED
                            else ''),
                        auditor=operator if audited else None,
                        audited_at=now if audited else None)
                    ProviderReportImage.objects.create(report=report, image='reports/results/result.gif')

            unused = UserCoupon.objects.create(user=customer, coupon=coupon_unused)
            UserCoupon.objects.create(
                user=customer, coupon=coupon_used, status=UserCoupon.Status.USED,
                order=customer_orders[3], used_at=now - timedelta(days=1))
            Message.objects.bulk_create([
                Message(recipient=customer, type=Message.Type.ORDER, title='大神已接单',
                        preview=f'{customer_orders[1].provider_name_snapshot or "测试大神"}已接单',
                        related_order_id=customer_orders[1].id,
                        action_url=f'/pages/orderList/index?orderId={customer_orders[1].id}',
                        is_read=False),
                Message(recipient=customer, type=Message.Type.ORDER, title='订单已完成，待评价',
                        preview='本次服务已完成，欢迎评价本次体验',
                        related_order_id=customer_orders[4].id,
                        action_url=f'/pages/orderList/index?orderId={customer_orders[4].id}',
                        is_read=customer_index % 2 == 0),
                Message(recipient=customer, type=Message.Type.PROMOTION, title='联调优惠券到账',
                        preview=f'{unused.coupon.name}已放入你的券包',
                        action_url='/pages/coupon/mine/index', is_read=False),
            ])
        return order_count

    def _seed_provider_flows(self, providers, operator, now):
        withdraw_statuses = [
            WithdrawRequest.Status.PENDING, WithdrawRequest.Status.APPROVED,
            WithdrawRequest.Status.REJECTED,
        ]
        for index, provider in enumerate(providers, 1):
            wallet = provider.wallet
            withdraw_status = withdraw_statuses[(index - 1) % len(withdraw_statuses)]
            amount = 10000 + (index % 4) * 5000
            tx_status = (
                Transaction.Status.PENDING if withdraw_status == WithdrawRequest.Status.PENDING
                else Transaction.Status.SUCCESS if withdraw_status == WithdrawRequest.Status.APPROVED
                else Transaction.Status.FAILED)
            tx = Transaction.objects.create(
                wallet=wallet, tx_no=f'DEMO-WITHDRAW-{index:02d}', amount=-amount,
                tx_type=Transaction.TxType.WITHDRAW,
                balance_before=wallet.balance + amount, balance_after=wallet.balance,
                status=tx_status, remark='联调提现申请')
            WithdrawRequest.objects.create(
                user=provider, amount=amount,
                payee_method=[
                    WithdrawRequest.PayeeMethod.WECHAT, WithdrawRequest.PayeeMethod.ALIPAY,
                    WithdrawRequest.PayeeMethod.BANK][(index - 1) % 3],
                payee_account=f'demo-payee-{index:02d}', payee_name=provider.real_name,
                status=withdraw_status, remark='提现测试数据',
                audit_remark=(
                    '财务已打款' if withdraw_status == WithdrawRequest.Status.APPROVED
                    else '收款信息不匹配' if withdraw_status == WithdrawRequest.Status.REJECTED
                    else ''),
                payout_reference=f'DEMO-PAYOUT-{index:04d}' if withdraw_status == WithdrawRequest.Status.APPROVED else '',
                paid_at=now if withdraw_status == WithdrawRequest.Status.APPROVED else None,
                auditor=operator if withdraw_status != WithdrawRequest.Status.PENDING else None,
                transaction=tx,
                audited_at=now if withdraw_status != WithdrawRequest.Status.PENDING else None)
            if withdraw_status == WithdrawRequest.Status.PENDING:
                wallet.frozen_amount = amount
                wallet.save(update_fields=['frozen_amount'])

            dispose_type = DisposeRecord.DisposeType.REWARD if index % 2 else DisposeRecord.DisposeType.PENALTY
            dispose_amount = 200 + index * 50
            signed_amount = dispose_amount if dispose_type == DisposeRecord.DisposeType.REWARD else -dispose_amount
            dispose_tx = Transaction.objects.create(
                wallet=wallet, tx_no=f'DEMO-DISPOSE-{index:02d}', amount=signed_amount,
                tx_type=(Transaction.TxType.REWARD if signed_amount > 0 else Transaction.TxType.PENALTY),
                balance_before=wallet.balance - signed_amount, balance_after=wallet.balance,
                remark='联调奖励记录' if signed_amount > 0 else '联调处罚记录')
            DisposeRecord.objects.create(
                user=provider, dispose_type=dispose_type, amount=dispose_amount,
                reason='服务质量优秀奖励' if signed_amount > 0 else '迟到扣款测试',
                operator=operator, transaction=dispose_tx)
            Message.objects.bulk_create([
                Message(recipient=provider, type=Message.Type.ORDER, title='你有新的订单进度',
                        preview='请在接单页面查看当前服务订单',
                        action_url='/pages/orders/index', is_read=False),
                Message(recipient=provider, type=Message.Type.SYSTEM, title='提现状态更新',
                        preview=f'提现申请当前状态：{withdraw_status}',
                        action_url='/pages/wallet/index', is_read=index % 2 == 0),
            ])

    @staticmethod
    def _seed_status_logs(order, operator, created_at):
        steps = [(OrderStatusLog.Action.CREATE, '', Order.Status.PENDING, '老板测试下单')]
        if order.status in (Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED):
            steps.append((OrderStatusLog.Action.GRAB, Order.Status.PENDING, Order.Status.GRABBED, '打手测试接单'))
        if order.status in (Order.Status.IN_SERVICE, Order.Status.COMPLETED):
            steps.append((OrderStatusLog.Action.START, Order.Status.GRABBED, Order.Status.IN_SERVICE, '开始服务'))
        if order.status == Order.Status.COMPLETED:
            steps.append((OrderStatusLog.Action.COMPLETE, Order.Status.IN_SERVICE, Order.Status.COMPLETED, '服务完成'))
        if order.status == Order.Status.CANCELLED:
            steps.append((OrderStatusLog.Action.CANCEL, Order.Status.PENDING, Order.Status.CANCELLED, '老板取消'))
        for index, (action, from_status, to_status, reason) in enumerate(steps):
            log = OrderStatusLog.objects.create(
                order=order, action=action, from_status=from_status, to_status=to_status,
                operator=operator if action == OrderStatusLog.Action.CREATE else order.provider,
                reason=reason)
            OrderStatusLog.objects.filter(pk=log.pk).update(
                created_at=created_at + timedelta(minutes=index * 5))
