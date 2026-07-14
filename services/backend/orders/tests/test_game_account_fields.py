"""老板下单游戏资料结构化落库相关测试。

覆盖：
- POST /api/orders/create/  下单时 game_region/game_nickname/game_uid 落库
- 缺省时三字段为空串（对历史 C 端零破坏）
- GET  /api/orders/orders/?role=provider  陪玩端可读到真实游戏资料
"""

from orders.models import Order
from orders.tests.factories import (
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from rest_framework.test import APITestCase


class OrderGameAccountFieldTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        make_wallet(self.customer, balance=100000)
        self.service = make_service(price=10000)

    def test_create_order_persists_game_account(self):
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
            'game_region': 'QQ区',
            'game_nickname': '影流之主',
            'game_uid': '88888888',
            'remark': '速度上分',
        })
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(customer=self.customer)
        self.assertEqual(order.game_region, 'QQ区')
        self.assertEqual(order.game_nickname, '影流之主')
        self.assertEqual(order.game_uid, '88888888')
        self.assertEqual(order.remark, '速度上分')

    def test_create_order_without_game_account_defaults_blank(self):
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
        })
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(customer=self.customer)
        self.assertEqual(order.game_region, '')
        self.assertEqual(order.game_nickname, '')
        self.assertEqual(order.game_uid, '')

    def test_provider_order_list_returns_game_account(self):
        provider = make_provider()
        order = Order.objects.create(
            customer=self.customer,
            provider=provider,
            service=self.service,
            amount=self.service.price,
            game_region='微信区',
            game_nickname='孤影',
            game_uid='12345678',
            status=Order.Status.GRABBED,
        )
        self.client.force_authenticate(provider)
        res = self.client.get('/api/orders/orders/?role=provider')
        self.assertEqual(res.data['code'], 0)
        item = next(o for o in res.data['data'] if o['id'] == order.id)
        self.assertEqual(item['game_region'], '微信区')
        self.assertEqual(item['game_nickname'], '孤影')
        self.assertEqual(item['game_uid'], '12345678')
