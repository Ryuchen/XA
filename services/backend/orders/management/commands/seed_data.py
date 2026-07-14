from django.core.management.base import BaseCommand

from orders.models import ServiceItem
from users.models import CustomUser, EscortProfile


class Command(BaseCommand):
    help = 'Seed initial data for the platform'

    def handle(self, *args, **options):
        services = [
            {'name': '三角洲行动 - 战术护航', 'description': '专业大神带你畅玩三角洲行动', 'price': 8000, 'sort_order': 1},
            {'name': '暗区突围 - 武装护航', 'description': '资深玩家带队闯暗区', 'price': 10000, 'sort_order': 2},
            {'name': '无畏契约 - 英雄教学', 'description': '专业教练指导上分', 'price': 6000, 'sort_order': 3},
            {'name': 'CSGO - 完美搭档', 'description': '老兵组队带你carry', 'price': 7000, 'sort_order': 4},
            {'name': '和平精英 - 甜蜜护航', 'description': '王牌战神带你上分', 'price': 9000, 'sort_order': 5},
            {'name': '王者荣耀 - 王者带飞', 'description': '国服选手全程带飞', 'price': 5000, 'sort_order': 6},
        ]
        for s in services:
            ServiceItem.objects.get_or_create(name=s['name'], defaults=s)

        test_provider, _ = CustomUser.objects.get_or_create(
            username='test_provider',
            defaults={'role': CustomUser.Role.PROVIDER, 'openid': 'wx_mock_provider'},
        )
        EscortProfile.objects.get_or_create(
            user=test_provider,
            defaults={
                'display_name': '北辰',
                'bio': '专业游戏陪玩',
                'price_per_hour': 8800,
                'rank_tier': '最强王者',
                'win_rate': 92,
                'is_verified': True,
            }
        )

        test_operator, _ = CustomUser.objects.get_or_create(
            username='test_operator',
            defaults={'role': CustomUser.Role.OPERATOR, 'openid': 'wx_mock_operator'},
        )

        self.stdout.write(self.style.SUCCESS(f'Seeded {len(services)} services, test provider, test operator'))
