from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from orders.models import Evaluation, Order, ServiceItem


class Command(BaseCommand):
    help = '为指定陪玩生成抢单池、已接单、服务中、已完成四阶段参考订单'

    def add_arguments(self, parser):
        parser.add_argument('--provider', default='pw_black', help='陪玩用户名')

    def handle(self, *args, **options):
        User = get_user_model()
        username = options['provider']
        try:
            provider = User.objects.get(username=username, role=User.Role.PROVIDER)
        except User.DoesNotExist as exc:
            raise CommandError(f'陪玩账号 {username} 不存在') from exc

        customer, _ = User.objects.get_or_create(
            username='sample_boss_orders',
            defaults={'nickname': '体验老板·小安', 'role': User.Role.CUSTOMER, 'phone': '13800008888'},
        )
        service, _ = ServiceItem.objects.get_or_create(
            name='王者荣耀·排位护航（参考）',
            defaults={'description': '陪玩端全阶段展示参考服务', 'price': 6800, 'is_active': True},
        )

        now = timezone.now()
        samples = [
            ('SAMPLE-PENDING-001', Order.Status.PENDING, None, 13600, 10880, 2,
             '微信区·至尊星耀', '希望主玩打野或射手，沟通积极，不压力队友；开局前先确认英雄。'),
            ('SAMPLE-GRABBED-001', Order.Status.GRABBED, provider, 6800, 5440, 1,
             'QQ区·王者低星', '已约好下午开局，请提前五分钟联系我，优先补位。'),
            ('SAMPLE-SERVICE-001', Order.Status.IN_SERVICE, provider, 20400, 16320, 3,
             '微信区·荣耀王者', '正在冲分，稳定运营，不开麦外放，输一局休息五分钟。'),
            ('SAMPLE-COMPLETED-001', Order.Status.COMPLETED, provider, 10200, 8160, 2,
             'QQ区·永恒钻石', '娱乐为主，希望多交流英雄思路，结束后提醒我确认订单。'),
        ]
        created = 0
        for index, (order_no, status, assigned, amount, income, rounds, region, remark) in enumerate(samples):
            order, was_created = Order.objects.update_or_create(
                order_no=order_no,
                defaults={
                    'customer': customer, 'provider': assigned, 'service': service,
                    'amount': amount, 'original_amount': amount, 'provider_income': income,
                    'shop_income': amount - income, 'commission_rate': 20,
                    'game_rounds': rounds, 'game_region': region,
                    'game_nickname': '今晚一定上分', 'game_uid': f'XA-DEMO-{index + 1:02d}',
                    'remark': remark, 'status': status,
                    'payment_status': Order.PaymentStatus.PAID,
                    'auto_cancel_at': now + timedelta(days=7) if status == Order.Status.PENDING else None,
                    'grabbed_at': now - timedelta(minutes=35) if status != Order.Status.PENDING else None,
                    'in_service_at': now - timedelta(minutes=20) if status in (Order.Status.IN_SERVICE, Order.Status.COMPLETED) else None,
                    'completed_at': now - timedelta(minutes=5) if status == Order.Status.COMPLETED else None,
                },
            )
            # 让无卡陪玩也能立即看到参考抢单；其余阶段制造合理时间顺序。
            Order.objects.filter(pk=order.pk).update(created_at=now - timedelta(minutes=10 + index * 15))
            created += int(was_created)

            if status == Order.Status.COMPLETED:
                Evaluation.objects.update_or_create(
                    order=order,
                    defaults={
                        'customer': customer, 'provider': provider,
                        'score': 5, 'skill_score': 5, 'attitude_score': 5,
                        'communication_score': 5,
                        'content': '配合很默契，沟通及时，游戏思路也讲得很清楚，下次还会再约！',
                    },
                )

        self.stdout.write(self.style.SUCCESS(
            f'已为 {username} 准备 4 条阶段参考订单（新增 {created}，更新 {4 - created}）'
        ))
