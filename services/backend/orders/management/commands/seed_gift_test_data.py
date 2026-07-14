from django.core.management.base import BaseCommand

from orders.models import ServiceCategory, ServiceItem


GIFTS = [
    ('元气加油', '轻轻应援一下，为大神补充元气', 100, 1),
    ('心动比心', '送上一颗小心心，感谢本次陪伴', 520, 2),
    ('星光手杖', '点亮专属星光，记录高光时刻', 1310, 3),
    ('荣耀奖杯', '为精彩操作送上荣耀奖杯', 1880, 4),
    ('浪漫烟花', '全屏烟花应援，氛围感拉满', 5200, 5),
    ('至尊皇冠', '送出至尊皇冠，致敬全场 MVP', 13140, 6),
]


class Command(BaseCommand):
    help = '创建可重复使用的礼物商品联调数据'

    def handle(self, *args, **options):
        category, _ = ServiceCategory.objects.update_or_create(
            name='礼品套餐',
            defaults={'is_gift': True, 'is_active': True, 'sort_order': 2, 'remark': '老板完单后快捷打赏'},
        )
        for name, description, price, sort_order in GIFTS:
            ServiceItem.objects.update_or_create(
                name=name,
                defaults={
                    'description': description,
                    'price': price,
                    'service_category': category,
                    'game_category': None,
                    'commission_rate': 20,
                    'cover_url': f'https://picsum.photos/seed/xa-gift-{sort_order}/320/260',
                    'highlights': ['即时送达', '陪玩可见', '钱包自动结算'],
                    'sort_order': 20 + sort_order,
                    'is_active': True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f'礼物测试数据已就绪：{len(GIFTS)} 款'))
