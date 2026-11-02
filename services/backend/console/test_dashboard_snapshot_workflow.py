"""Classify paid orders by their recorded type after a local catalog reimport."""
import io
import json
from django.utils import timezone

from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase

from orders.management.commands.seed_club_catalog import CATALOG
from orders.models import Order
from orders.tests.factories import make_service, make_service_category
from orders.tests import test_release_flow as release_flow
from users.companion_picker import companion_service_amounts


@override_settings(DEBUG=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False)
class DashboardOrderSnapshotWorkflowTest(APITestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge
    new_order = release_flow.ReleaseFlowTest.new_order
    finish = release_flow.ReleaseFlowTest.finish
    settle_after_window = release_flow.ReleaseFlowTest.settle_after_window

    def test_paid_and_partially_refunded_types_survive_catalog_reimport(self):
        book = json.loads(CATALOG.read_text(encoding='utf-8'))
        gift_key = next(row['catalog_key'] for row in book['items'] if row['is_gift'])
        service_key = next(row['catalog_key'] for row in book['items'] if not row['is_gift'])
        self.service.catalog_key = gift_key
        self.service.save(update_fields=['catalog_key'])
        gift = make_service(name='测试礼物', price=2100,
                            service_category=make_service_category(is_gift=True),
                            catalog_key=service_key)
        profile = self.provider.escort_profile
        profile.gender = 'MALE'
        profile.save(update_fields=['gender'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'player_dashboard:view', 'dashboard:view']
        role.save(update_fields=['permissions'])

        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        self.settle_after_window()
        self.login(self.boss)
        gift_result = self.ok(self.client.post('/api/orders/gifts/send/', {
            'gift_service_id': gift.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'quantity': 1, 'expected_amount': 2100, 'client_request_id': 'snapshot-gift-1',
        }, format='json'))
        gift_order = Order.objects.get(pk=gift_result['id'])
        self.assertFalse(order.is_gift_snapshot)
        self.assertTrue(gift_order.is_gift_snapshot)

        self.login(self.admin)
        before = self.ok(self.client.get('/api/admin/dashboard/player'))['kpi']
        self.assertEqual((before['game_amount'], before['gift_amount']), (10000, 2100))
        def dashboard():
            data = self.ok(self.client.get('/api/admin/dashboard/stats'))
            today = next(row for row in data['daily_new_orders'] if row['date'] == timezone.localdate().isoformat())
            return data, today

        stats, today = dashboard()
        self.assertEqual((stats['order_amount_total'], stats['paid_order_total'], today['count'], today['net_amount']),
                         (12100, 2, 2, 12100))
        call_command('seed_club_catalog', apply=True, stdout=io.StringIO())
        self.service.refresh_from_db()
        gift.refresh_from_db()
        self.assertTrue(self.service.service_category.is_gift)
        self.assertFalse(gift.service_category.is_gift)
        after = self.ok(self.client.get('/api/admin/dashboard/player'))['kpi']
        self.assertEqual((after['game_amount'], after['gift_amount'], after['male_game'], after['male_gift']),
                         (10000, 2100, 10000, 2100))
        self.assertEqual(companion_service_amounts([profile])['monthly'][self.accounts[self.provider.pk].pk], 10000)

        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'refund_amount': 2500, 'reason': '隔离测试部分退款',
            'client_request_id': 'snapshot-partial-1', 'plan_token': preview['plan_token'],
        }, format='json'))
        after_refund = self.ok(self.client.get('/api/admin/dashboard/player'))['kpi']
        self.assertEqual((after_refund['game_amount'], after_refund['gift_amount'], after_refund['male_game'], after_refund['male_gift']),
                         (7500, 2100, 7500, 2100))
        stats, today = dashboard()
        self.assertEqual((stats['order_amount_total'], stats['paid_order_total'], today['count'], today['net_amount']),
                         (9600, 2, 2, 9600))
        self.assertEqual(companion_service_amounts([profile])['monthly'][self.accounts[self.provider.pk].pk], 7500)

        preview = self.ok(self.client.get(f'/api/admin/orders/{gift_order.pk}/refund-preview/', {'refund_amount': 500}))
        self.ok(self.client.post(f'/api/admin/orders/{gift_order.pk}/refund/', {
            'refund_amount': 500, 'reason': '隔离测试礼物部分退款',
            'client_request_id': 'snapshot-gift-partial-1', 'plan_token': preview['plan_token'],
        }, format='json'))
        after_both = self.ok(self.client.get('/api/admin/dashboard/player'))
        self.assertEqual((after_both['kpi']['game_amount'], after_both['kpi']['gift_amount'],
                          after_both['kpi']['male_game'], after_both['kpi']['male_gift']),
                         (7500, 1600, 7500, 1600))
        self.assertEqual(after_both['rank']['income'][0]['value'], 7280)
        self.assertEqual(companion_service_amounts([profile])['monthly'][self.accounts[self.provider.pk].pk], 7500)
        stats, today = dashboard()
        self.assertEqual((stats['order_amount_total'], stats['paid_order_total'], today['count'], today['net_amount']),
                         (9100, 2, 2, 9100))

        for current, remaining, key in ((gift_order, 1600, 'gift-final'), (order, 7500, 'game-final')):
            preview = self.ok(self.client.get(f'/api/admin/orders/{current.pk}/refund-preview/', {'refund_amount': remaining}))
            self.ok(self.client.post(f'/api/admin/orders/{current.pk}/refund/', {
                'refund_amount': remaining, 'reason': '隔离测试退还余款',
                'client_request_id': key, 'plan_token': preview['plan_token'],
            }, format='json'))
        stats, today = dashboard()
        self.assertEqual((stats['order_amount_total'], stats['paid_order_total'], today['count'], today['net_amount']),
                         (0, 0, 2, 0))
        self.assertEqual(self.ok(self.client.get('/api/admin/dashboard/player'))['kpi']['total_amount'], 0)
