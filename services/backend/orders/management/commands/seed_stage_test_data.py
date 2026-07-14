"""生成可重复执行的全阶段联调数据。所有记录均归属于 demo_* 测试账号。"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

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
from users.models import (
    BossType,
    EscortLevel,
    EscortProfile,
    ProviderPassPurchase,
)
from wallet.models import (
    DisposeRecord,
    ProviderReport,
    ProviderReportImage,
    Transaction,
    Wallet,
    WithdrawRequest,
)


PASSWORD = 'test1234'
CUSTOMER_PREFIX = 'demo_boss_'
PROVIDER_PREFIX = 'demo_provider_'
ORDER_PREFIX = 'DEMO-STAGE-'


class Command(BaseCommand):
    help = '生成20个老板、20个打手及订单/报单/提现/通行证等全阶段联调数据'

    def add_arguments(self, parser):
        parser.add_argument('--customers', type=int, default=20)
        parser.add_argument('--providers', type=int, default=20)
        parser.add_argument('--password', default=PASSWORD)

    @transaction.atomic
    def handle(self, *args, **options):
        customer_count = max(1, options['customers'])
        provider_count = max(1, options['providers'])
        password = options['password']
        User = get_user_model()
        now = timezone.now()

        # 重跑时仅清理带固定前缀的联调账号；不触碰用户手工创建的数据。
        ProviderPassPurchase.objects.filter(provider__username__startswith=PROVIDER_PREFIX).delete()
        User.objects.filter(username__startswith=CUSTOMER_PREFIX).delete()
        User.objects.filter(username__startswith=PROVIDER_PREFIX).delete()
        User.objects.filter(username='demo_stage_operator').delete()

        boss_types = [
            BossType.objects.get_or_create(
                name=name,
                defaults={'discount_rate': rate, 'sort_order': index, 'remark': '联调测试等级'},
            )[0]
            for index, (name, rate) in enumerate([
                ('测试普通老板', 100), ('测试白银老板', 98),
                ('测试黄金老板', 95), ('测试黑钻老板', 90),
            ], 1)
        ]
        escort_levels = [
            EscortLevel.objects.get_or_create(
                name=name,
                defaults={'commission_rate': rate, 'sort_order': index, 'remark': '联调测试等级'},
            )[0]
            for index, (name, rate) in enumerate([
                ('测试新秀', 25), ('测试进阶', 22), ('测试精英', 18), ('测试王牌', 15),
            ], 1)
        ]

        game_names = ['王者荣耀', '和平精英', '三角洲行动', '无畏契约', '英雄联盟', '永劫无间']
        service_names = ['排位上分', '娱乐陪玩', '战术护航', '英雄教学', '语音陪伴', '通宵车队']
        game_categories = [
            GameCategory.objects.get_or_create(
                name=name, defaults={'sort_order': index, 'remark': '联调测试游戏'},
            )[0]
            for index, name in enumerate(game_names, 1)
        ]
        service_category, _ = ServiceCategory.objects.get_or_create(
            name='测试陪玩服务', defaults={'sort_order': 90, 'remark': '全阶段联调数据'},
        )
        services = []
        for index, (game, service_name) in enumerate(zip(game_categories, service_names), 1):
            service, _ = ServiceItem.objects.update_or_create(
                name=f'{game.name}·{service_name}（联调）',
                defaults={
                    'description': f'{game.name}{service_name}全阶段参考商品',
                    'price': 3600 + index * 700,
                    'game_category': game,
                    'service_category': service_category,
                    'commission_rate': 20,
                    'cover_url': f'https://picsum.photos/seed/xa-service-{index}/320/320',
                    'highlights': ['实名认证', '平台担保', '售后保障'],
                    'sort_order': 80 + index,
                    'is_active': True,
                },
            )
            services.append(service)

        operator = User.objects.create_user(
            username='demo_stage_operator', password=password,
            nickname='全阶段测试客服', role=User.Role.OPERATOR,
            phone='13990000000', can_login=True,
        )

        customers = []
        for index in range(1, customer_count + 1):
            user = User.objects.create_user(
                username=f'{CUSTOMER_PREFIX}{index:02d}',
                password=password,
                nickname=f'测试老板{index:02d}号',
                real_name=f'老板测试{index:02d}',
                phone=f'13888{index:06d}',
                openid=f'demo_boss_openid_{index:02d}',
                role=User.Role.CUSTOMER,
                boss_type=boss_types[(index - 1) % len(boss_types)],
                game_region=['微信区', 'QQ区', '安卓区', '苹果区'][(index - 1) % 4],
                game_nickname=f'老板游戏名{index:02d}',
                game_uid=f'BOSS-UID-{index:04d}',
                is_phone_verified=index % 3 != 0,
                is_openid_bound=True,
                can_login=True,
            )
            Wallet.objects.update_or_create(
                user=user,
                defaults={
                    'balance': 50000 + index * 5000,
                    'total_recharge': 100000 + index * 10000,
                    'total_gift': index * 500,
                    'frozen_amount': 0,
                },
            )
            customers.append(user)

        pass_tiers = (
            [EscortProfile.PassTier.BLACK] * 4
            + [EscortProfile.PassTier.GOLD] * 4
            + [EscortProfile.PassTier.SILVER] * 4
            + [EscortProfile.PassTier.BRONZE] * 4
            + [''] * max(0, provider_count - 16)
        )
        providers = []
        for index in range(1, provider_count + 1):
            user = User.objects.create_user(
                username=f'{PROVIDER_PREFIX}{index:02d}',
                password=password,
                nickname=f'测试打手{index:02d}号',
                real_name=f'打手测试{index:02d}',
                phone=f'13777{index:06d}',
                role=User.Role.PROVIDER,
                can_login=True,
            )
            tier = pass_tiers[index - 1] if index <= len(pass_tiers) else ''
            EscortProfile.objects.create(
                user=user,
                display_name=f'{game_names[(index - 1) % len(game_names)]}大神·{index:02d}',
                gender=EscortProfile.Gender.FEMALE if index % 3 == 0 else EscortProfile.Gender.MALE,
                bio='全阶段联调测试打手，可用于抢单、报单、评价回复和提现测试。',
                city=['北京', '上海', '广州', '深圳', '杭州'][(index - 1) % 5],
                service_area=game_names[(index - 1) % len(game_names)],
                price_per_hour=5000 + index * 200,
                rank_tier=['钻石', '星耀', '王者', '巅峰赛1800'][(index - 1) % 4],
                level=escort_levels[(index - 1) % len(escort_levels)],
                win_rate=70 + (index % 20),
                status=EscortProfile.Status.AVAILABLE if index <= 5 else EscortProfile.Status.BUSY,
                is_verified=index % 5 != 0,
                deposit_required=20000,
                deposit_paid=20000 if index % 4 != 0 else 10000,
                total_reward=index * 100,
                total_penalty=(index % 4) * 50,
                rating_avg=4 + (index % 10) / 10,
                rating_count=5 + index,
                completed_order_count=10 + index * 2,
                pass_tier=tier,
                pass_expires_at=now + timedelta(days=30) if tier else None,
            )
            Wallet.objects.update_or_create(
                user=user,
                defaults={'balance': 30000 + index * 4000, 'frozen_amount': 0},
            )
            providers.append(user)

        # 通行证购买记录：黑/金/银/铜各4人，其余无卡，直接覆盖抢单优先级展示。
        daily_prices = {
            EscortProfile.PassTier.BLACK: 5000,
            EscortProfile.PassTier.GOLD: 3000,
            EscortProfile.PassTier.SILVER: 1000,
            EscortProfile.PassTier.BRONZE: 200,
        }
        for index, provider in enumerate(providers, 1):
            tier = provider.escort_profile.pass_tier
            if not tier:
                continue
            daily_price = daily_prices[tier]
            original = daily_price * 30
            paid = original * 95 // 100
            wallet = provider.wallet
            tx = Transaction.objects.create(
                wallet=wallet, tx_no=f'DEMO-PASS-{index:02d}', amount=-paid,
                tx_type=Transaction.TxType.PASS_PURCHASE,
                balance_before=wallet.balance + paid, balance_after=wallet.balance,
                remark=f'{provider.escort_profile.get_pass_tier_display()}30天测试购买',
            )
            ProviderPassPurchase.objects.create(
                provider=provider, tier=tier, days=30, daily_price=daily_price,
                original_amount=original, discount_amount=original - paid, paid_amount=paid,
                starts_at=now, expires_at=now + timedelta(days=30), transaction=tx,
            )

        coupon_unused, _ = Coupon.objects.get_or_create(
            name='联调无门槛100兴安币券',
            defaults={
                'discount_type': Coupon.DiscountType.DIRECT, 'threshold': 0,
                'amount': 1000, 'valid_to': now + timedelta(days=90), 'is_active': True,
            },
        )
        coupon_used, _ = Coupon.objects.get_or_create(
            name='联调满500减80兴安币券',
            defaults={
                'discount_type': Coupon.DiscountType.THRESHOLD, 'threshold': 5000,
                'amount': 800, 'valid_to': now + timedelta(days=90), 'is_active': True,
            },
        )

        order_count = 0
        report_statuses = [
            ProviderReport.Status.DRAFT,
            ProviderReport.Status.PENDING,
            ProviderReport.Status.APPROVED,
            ProviderReport.Status.REJECTED,
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
                    provider = providers[5 + ((customer_index - 1) % max(1, provider_count - 5))] if provider_count > 5 else providers[0]
                elif status == Order.Status.IN_SERVICE:
                    provider = providers[5 + ((customer_index + 4) % max(1, provider_count - 5))] if provider_count > 5 else providers[0]
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
                    customer=customer,
                    provider=provider,
                    service=service,
                    amount=amount,
                    original_amount=original_amount,
                    boss_discount=boss_discount,
                    coupon_discount=coupon_discount,
                    commission_rate=commission_rate,
                    provider_income=provider_income,
                    shop_income=amount - provider_income,
                    game_rounds=rounds,
                    game_region=customer.game_region,
                    game_nickname=customer.game_nickname,
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
                        Order.PaymentStatus.REFUNDED
                        if status == Order.Status.CANCELLED else Order.PaymentStatus.PAID
                    ),
                    status=status,
                    auto_cancel_at=now + timedelta(minutes=30) if status == Order.Status.PENDING else None,
                    grabbed_at=created_at + timedelta(minutes=3) if status in (
                        Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED,
                    ) else None,
                    in_service_at=created_at + timedelta(minutes=8) if status in (
                        Order.Status.IN_SERVICE, Order.Status.COMPLETED,
                    ) else None,
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
                        wallet=customer.wallet,
                        tx_no=f'DEMO-PAY-{customer_index:02d}-{stage_index}',
                        order=order,
                        amount=-amount,
                        tx_type=Transaction.TxType.PAY,
                        balance_before=customer.wallet.balance + amount,
                        balance_after=customer.wallet.balance,
                        remark=f'联调订单支付：{order.order_no}',
                    )
                else:
                    Transaction.objects.create(
                        wallet=customer.wallet,
                        tx_no=f'DEMO-REFUND-{customer_index:02d}',
                        order=order,
                        amount=amount,
                        tx_type=Transaction.TxType.REFUND,
                        balance_before=customer.wallet.balance - amount,
                        balance_after=customer.wallet.balance,
                        remark=f'联调订单退款：{order.order_no}',
                    )

                if status == Order.Status.COMPLETED and provider:
                    Transaction.objects.create(
                        wallet=provider.wallet,
                        tx_no=f'DEMO-INCOME-{customer_index:02d}-{stage_index}',
                        order=order,
                        amount=provider_income,
                        tx_type=Transaction.TxType.INCOME,
                        balance_before=provider.wallet.balance - provider_income,
                        balance_after=provider.wallet.balance,
                        remark=f'联调订单收入：{order.order_no}',
                    )

                if stage_code == 'DONE-EVAL' and provider:
                    Evaluation.objects.create(
                        order=order,
                        customer=customer,
                        provider=provider,
                        score=4 + customer_index % 2,
                        skill_score=5,
                        attitude_score=4 + customer_index % 2,
                        communication_score=4,
                        content='打手技术稳定，沟通及时，整体体验很好。',
                        reply_content=('感谢老板认可，下次继续带你上分！' if customer_index % 2 else ''),
                        replied_at=(now - timedelta(hours=customer_index) if customer_index % 2 else None),
                    )

                if stage_code == 'DONE-REPORT' and provider:
                    report_status = report_statuses[(customer_index - 1) % len(report_statuses)]
                    report = ProviderReport.objects.create(
                        provider=provider,
                        order=order,
                        game_name=service.name,
                        description='平台订单完成后的联调报单，包含三类截图。',
                        amount=amount,
                        entry_image='reports/entry/entry.gif',
                        completion_image='reports/completion/completion.gif',
                        status=report_status,
                        commission_rate=commission_rate,
                        payout_amount=provider_income if report_status == ProviderReport.Status.APPROVED else 0,
                        remark='请核对局数、战绩和结单时间。',
                        audit_remark=(
                            '截图完整，审核通过' if report_status == ProviderReport.Status.APPROVED
                            else '战绩截图不清晰，请重新提交' if report_status == ProviderReport.Status.REJECTED
                            else ''
                        ),
                        auditor=operator if report_status in (
                            ProviderReport.Status.APPROVED, ProviderReport.Status.REJECTED,
                        ) else None,
                        audited_at=now if report_status in (
                            ProviderReport.Status.APPROVED, ProviderReport.Status.REJECTED,
                        ) else None,
                    )
                    ProviderReportImage.objects.create(report=report, image='reports/results/result.gif')
                    ProviderReportImage.objects.create(report=report, image='reports/results/result_FPELGC0.gif')

            unused = UserCoupon.objects.create(user=customer, coupon=coupon_unused)
            UserCoupon.objects.create(
                user=customer,
                coupon=coupon_used,
                status=UserCoupon.Status.USED,
                order=customer_orders[3],
                used_at=now - timedelta(days=1),
            )
            Message.objects.bulk_create([
                Message(
                    recipient=customer, type=Message.Type.ORDER, title='大神已接单',
                    preview=f'{customer_orders[1].provider_name_snapshot or "测试大神"}已接单',
                    related_order_id=customer_orders[1].id,
                    action_url=f'/pages/orderList/index?orderId={customer_orders[1].id}',
                    is_read=False,
                ),
                Message(
                    recipient=customer, type=Message.Type.ORDER, title='订单已完成，待评价',
                    preview='本次服务已完成，欢迎评价本次体验',
                    related_order_id=customer_orders[4].id,
                    action_url=f'/pages/orderList/index?orderId={customer_orders[4].id}',
                    is_read=customer_index % 2 == 0,
                ),
                Message(
                    recipient=customer, type=Message.Type.PROMOTION, title='联调优惠券到账',
                    preview=f'{unused.coupon.name}已放入你的券包',
                    action_url='/pages/coupon/mine/index', is_read=False,
                ),
            ])

        withdraw_statuses = [
            WithdrawRequest.Status.PENDING,
            WithdrawRequest.Status.APPROVED,
            WithdrawRequest.Status.REJECTED,
        ]
        for index, provider in enumerate(providers, 1):
            wallet = provider.wallet
            withdraw_status = withdraw_statuses[(index - 1) % len(withdraw_statuses)]
            amount = 10000 + (index % 4) * 5000
            tx_status = (
                Transaction.Status.PENDING if withdraw_status == WithdrawRequest.Status.PENDING
                else Transaction.Status.SUCCESS if withdraw_status == WithdrawRequest.Status.APPROVED
                else Transaction.Status.FAILED
            )
            tx = Transaction.objects.create(
                wallet=wallet, tx_no=f'DEMO-WITHDRAW-{index:02d}', amount=-amount,
                tx_type=Transaction.TxType.WITHDRAW,
                balance_before=wallet.balance + amount,
                balance_after=wallet.balance,
                status=tx_status,
                remark='联调提现申请',
            )
            WithdrawRequest.objects.create(
                user=provider, amount=amount,
                payee_method=[
                    WithdrawRequest.PayeeMethod.WECHAT,
                    WithdrawRequest.PayeeMethod.ALIPAY,
                    WithdrawRequest.PayeeMethod.BANK,
                ][(index - 1) % 3],
                payee_account=f'demo-payee-{index:02d}',
                payee_name=provider.real_name,
                status=withdraw_status,
                remark='全阶段提现测试数据',
                audit_remark=(
                    '财务已打款' if withdraw_status == WithdrawRequest.Status.APPROVED
                    else '收款信息不匹配' if withdraw_status == WithdrawRequest.Status.REJECTED
                    else ''
                ),
                payout_reference=f'DEMO-PAYOUT-{index:04d}' if withdraw_status == WithdrawRequest.Status.APPROVED else '',
                paid_at=now if withdraw_status == WithdrawRequest.Status.APPROVED else None,
                auditor=operator if withdraw_status != WithdrawRequest.Status.PENDING else None,
                transaction=tx,
                audited_at=now if withdraw_status != WithdrawRequest.Status.PENDING else None,
            )
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
                remark='联调奖励记录' if signed_amount > 0 else '联调处罚记录',
            )
            DisposeRecord.objects.create(
                user=provider, dispose_type=dispose_type, amount=dispose_amount,
                reason='服务质量优秀奖励' if signed_amount > 0 else '迟到扣款测试',
                operator=operator, transaction=dispose_tx,
            )
            Message.objects.bulk_create([
                Message(
                    recipient=provider, type=Message.Type.ORDER, title='你有新的订单进度',
                    preview='请在接单页面查看当前服务订单',
                    action_url='/pages/orders/index', is_read=False,
                ),
                Message(
                    recipient=provider, type=Message.Type.SYSTEM, title='提现状态更新',
                    preview=f'提现申请当前状态：{withdraw_status}',
                    action_url='/pages/wallet/index', is_read=index % 2 == 0,
                ),
                Message(
                    recipient=provider, type=Message.Type.SYSTEM, title='奖惩记录更新',
                    preview='钱包内新增一条奖励或处罚记录',
                    action_url='/pages/wallet/index', is_read=False,
                ),
            ])

        self.stdout.write(self.style.SUCCESS(
            f'全阶段联调数据生成完成：老板 {len(customers)} 个，打手 {len(providers)} 个，'
            f'订单 {order_count} 笔，统一密码 {password}'
        ))
        self.stdout.write('老板账号：demo_boss_01 ~ demo_boss_%02d' % customer_count)
        self.stdout.write('打手账号：demo_provider_01 ~ demo_provider_%02d' % provider_count)
        self.stdout.write('可抢单账号：demo_provider_01 ~ demo_provider_%02d' % min(5, provider_count))

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
                order=order, action=action, from_status=from_status,
                to_status=to_status, operator=operator if action == OrderStatusLog.Action.CREATE else order.provider,
                reason=reason,
            )
            OrderStatusLog.objects.filter(pk=log.pk).update(
                created_at=created_at + timedelta(minutes=index * 5),
            )
