from orders.tests.test_release_flow import ReleaseFlowTest
from copy import deepcopy
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase, APIClient

from orders.models import Gameplay, Order
from orders.dispatching import build_dispatch_text
from orders.tests.factories import make_promotion, make_service
from orders.tests import test_release_flow as release
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, REQUIRE_CLIENT_REQUEST_ID=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GameplayFlowTest(APITestCase):
    login = release.ReleaseFlowTest.login
    ok = release.ReleaseFlowTest.ok
    recharge = release.ReleaseFlowTest.recharge
    new_order = release.ReleaseFlowTest.new_order
    finish = release.ReleaseFlowTest.finish

    def setUp(self):
        release.ReleaseFlowTest.setUp(self)
        self.gameplay = Gameplay.objects.create(name='测试转盘', code='test-wheel', kind='ROULETTE', rules='按目标提交凭证')
        self.service.gameplay = self.gameplay
        self.service.platform = 'MOBILE'
        self.service.security_level = 'CONFIDENTIAL'
        self.service.roulette_pool = [
            {'name': '任务护航', 'description': '完成约定任务并提交截图', 'weight': 1},
            {'name': '撤离护航', 'description': '完成安全撤离并提交截图', 'weight': 3},
        ]
        self.service.save()
        for role in self.admin.admin_membership.roles.all():
            role.permissions += ['service:view', 'service:edit', 'service:delete']
            role.save()

    def test_public_catalog_and_four_way_filter(self):
        self.gameplay.theme = 'roulette'
        self.gameplay.cover_url = 'https://example.com/gameplay-cover.jpg'
        self.gameplay.save()
        self.assertEqual(APIClient().get('/api/orders/gameplays/').data['code'], 0)
        for platform, level in [('MOBILE', 'TOP_SECRET'), ('PC', 'CONFIDENTIAL'), ('PC', 'TOP_SECRET')]:
            make_service(gameplay=self.gameplay, platform=platform, security_level=level, roulette_pool=self.service.roulette_pool)
        self.login(self.boss)
        for platform in ('MOBILE', 'PC'):
            for level in ('CONFIDENTIAL', 'TOP_SECRET'):
                rows = self.ok(self.client.get('/api/orders/services/', {'gameplay': self.gameplay.pk, 'platform': platform, 'security_level': level}))
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['platform'], rows[0]['security_level']), (platform, level))
                self.assertEqual(rows[0]['gameplay_theme'], 'roulette')
                self.assertEqual(rows[0]['gameplay_cover_url'], '', 'Unverified foreign images are never shown as unbounded originals')
        detail = self.ok(self.client.get(f'/api/orders/services/{self.service.pk}/'))
        self.assertEqual(detail['gameplay_cover_url'], '')
        self.gameplay.is_active = False
        self.gameplay.save()
        self.assertFalse(self.ok(self.client.get('/api/orders/services/')))
        self.assertEqual(self.client.get(f'/api/orders/services/{self.service.pk}/').data['code'], 404)

    @patch('orders.gameplay.secrets.randbelow', return_value=1)
    def test_discounted_payment_target_report_and_settlement_are_consistent(self, draw):
        make_promotion(discount_rate=80, commission_rate=20)
        order = self.new_order()
        self.assertEqual((order.amount, order.provider_income, order.shop_income), (8000, 6400, 1600))
        self.assertEqual(order.roulette_result['name'], '撤离护航')
        self.assertEqual(order.gameplay_snapshot['pool'][1]['probability'], 75)
        self.assertIn('手游 · 机密', build_dispatch_text(order))
        self.assertIn('转盘目标：撤离护航', build_dispatch_text(order))
        self.assertIn('¥64.00', build_dispatch_text(order))
        self.assertNotIn('原价', build_dispatch_text(order))
        frozen = deepcopy(order.roulette_result)
        report = self.finish(order)
        rows = self.ok(self.client.get('/api/wallet/reports/'))
        # 当前报单列表为分页信封。
        rows = rows['list'] if isinstance(rows, dict) else rows
        self.assertIn('撤离护航', rows[0]['fulfillment'])
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        ReleaseFlowTest.settle_after_window(self)
        order.refresh_from_db()
        self.assertEqual(order.roulette_result, frozen)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 6400)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)
        draw.assert_called_once_with(4)

    @patch('orders.gameplay.secrets.randbelow', return_value=0)
    def test_snapshot_and_result_survive_catalog_edits_and_repeated_saves(self, draw):
        order = self.new_order()
        before = deepcopy(order.gameplay_snapshot)
        self.service.roulette_pool = [{'name': '新目标1', 'weight': 1}, {'name': '新目标2', 'weight': 1}]
        self.service.rules = '新规则'
        self.service.platform = 'PC'
        self.service.price = 12345
        self.service.save()
        self.gameplay.name = '改名玩法'
        self.gameplay.save()
        order.refresh_from_db()
        order.save()
        order.refresh_from_db()
        self.assertEqual(order.gameplay_snapshot, before)
        self.assertEqual(order.roulette_result['name'], '任务护航')
        draw.assert_called_once()

    @patch('orders.gameplay.secrets.randbelow')
    def test_quote_invalid_rounds_and_insufficient_balance_do_not_draw(self, draw):
        self.login(self.boss)
        self.ok(self.client.post('/api/orders/quote/', {'service_id': self.service.pk}))
        for rounds in (1, 2):
            response = self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'game_rounds': rounds, 'client_request_id': 'no-balance'})
            self.assertEqual(response.data['code'], 400)
        self.assertFalse(Order.objects.exists())
        draw.assert_not_called()

    def test_repeat_request_does_not_draw_or_debit_twice(self):
        order = self.new_order()
        balance = Wallet.objects.get(user=self.boss).balance
        self.login(self.boss)
        response = self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'client_request_id': 'order-1', 'roulette_result': {'index': 100}}, format='json')
        self.assertEqual(response.data['code'], 409)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, balance)
        self.assertIn(order.roulette_result['index'], (0, 1))

    def test_disabled_gameplay_rejects_new_orders_without_debit(self):
        self.ok(self.recharge())
        self.gameplay.is_active = False
        self.gameplay.save()
        self.login(self.boss)
        response = self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'client_request_id': 'disabled'})
        self.assertEqual(response.data['code'], 400)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 21000)

    def test_admin_rejects_invalid_pool_and_incomplete_specification(self):
        self.login(self.admin)
        url = f'/api/admin/service-items/{self.service.pk}/'
        for payload in ({'platform': ''}, {'security_level': ''}, {'roulette_pool': []}, {'roulette_pool': [{'name': 'x', 'weight': -1}, {'name': 'y', 'weight': 1}]}, {'roulette_pool': [{'name': 'x', 'weight': 1}, {'name': 'x', 'weight': 1}]}):
            with self.subTest(payload=payload):
                response = self.client.patch(url, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.client.patch(f'/api/admin/gameplays/{self.gameplay.pk}/', {'kind': 'STANDARD'}, format='json').status_code, 400)
        # 目录现已采用软归档：保留历史履约信息，并关闭新的自主购买。
        self.assertEqual(self.client.delete(f'/api/admin/gameplays/{self.gameplay.pk}/').status_code, 200)
        self.gameplay.refresh_from_db()
        self.assertTrue(self.gameplay.is_archived)
        self.assertFalse(self.gameplay.is_active)
        self.assertTrue(type(self.service).objects.filter(pk=self.service.pk).exists())

    def test_boss_cannot_write_gameplay_configuration(self):
        self.login(self.boss)
        self.assertEqual(self.client.post('/api/admin/gameplays/', {'name': '违规配置'}).status_code, 403)

    def test_pending_refund_preserves_result_and_restores_balance_once(self):
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'client_request_id': 'cancel-wheel'}))
        self.ok(self.client.post(f"/api/orders/orders/{data['id']}/cancel/", {'reason': '取消测试'}))
        self.assertNotEqual(self.client.post(f"/api/orders/orders/{data['id']}/cancel/").data['code'], 0)
        order = Order.objects.get(pk=data['id'])
        self.assertEqual(order.roulette_result, data['roulette_result'])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 21000)
        self.assertEqual(ProviderReport.objects.filter(order=order).count(), 0)
