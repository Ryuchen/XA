"""Dashboard cohort amounts follow real payment, settlement and refunds."""
import os
from datetime import timedelta
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from engagement.tasks import confirm_due_consumption
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_service, make_service_category
from wallet.models import ProviderReport


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class DashboardRefundRealJourneyTest(LiveServerTestCase):
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def setUp(self):
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '仪表盘退款队列依赖正式报单结算＋实销确认任务，且日期数学跨本地午夜有时敏性（18-盘点订单售后资金组合）；最终联调启用')
    def test_historical_service_and_current_gift_show_net_by_order_day(self):
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'dashboard:view', 'player_dashboard:view']
        role.save(update_fields=['permissions'])
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))

        # Turn the real API-created order into a coherent historical record in
        # this isolated database. The actual task uses the real current clock.
        # Pin the cohort's business day independently of the test's wall clock.
        # A rolling `now - 4 days` plus the refund's three hours crosses local
        # midnight whenever this test runs after 21:00.
        created = (timezone.localtime() - timedelta(days=4)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        completed = created + timedelta(hours=2)
        submitted = completed + timedelta(minutes=10)
        Order.objects.filter(pk=order.pk).update(created_at=created, completed_at=completed,
            report_ready_at=submitted, consumption_ready_at=submitted + timedelta(hours=72),
            service_aftersale_hours=168)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=submitted)
        self.assertEqual(confirm_due_consumption(), 1)

        gift = make_service(name='隔离礼物', price=2100, commission_rate=20,
                            service_category=make_service_category(is_gift=True))
        self.login(self.boss)
        gift_order = self.ok(self.client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'quantity': 1,
            'expected_amount': 2100, 'client_request_id': 'cohort-gift'}, format='json'))
        self.login(self.admin)

        def snapshot():
            data = self.ok(self.client.get('/api/admin/dashboard/stats'))
            return data, {row['date']: row for row in data['daily_new_orders']}

        before, days = snapshot()
        historical_day = timezone.localtime(created).date().isoformat()
        today = timezone.localdate().isoformat()
        self.assertEqual((before['paid_amount_total'], days[historical_day]['net_amount'], days[today]['net_amount']),
                         (12100, 10000, 2100))

        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'refund_amount': 2500, 'reason': '隔离协商退款', 'client_request_id': 'cohort-refund',
            'plan_token': preview['plan_token'],
        }, format='json'))
        after, days = snapshot()
        self.assertEqual((after['paid_amount_total'], after['paid_order_total']), (9600, 2))
        self.assertEqual((after['today_refund_amount'],after['today_refund_orders'],after['today_refund_operations']), (2500,1,1))
        self.assertEqual((days[historical_day]['count'], days[historical_day]['net_amount']), (1, 7500))
        self.assertEqual((days[today]['count'], days[today]['net_amount']), (1, 2100))
        player = self.ok(self.client.get('/api/admin/dashboard/player'))
        self.assertEqual((player['kpi']['game_amount'], player['kpi']['gift_amount']), (7500, 2100))
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 500}))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'refund_amount':500,'reason':'隔离再次退款',
            'client_request_id':'cohort-refund-second','plan_token':preview['plan_token']},format='json'))
        after_second, days = snapshot()
        self.assertEqual((after_second['today_refund_amount'],after_second['today_refund_orders'],after_second['today_refund_operations']),(3000,1,2))
        self.assertEqual(days[historical_day]['net_amount'],7000)
        matching = self.ok(self.client.get('/api/admin/orders/', {'refund_day': today, 'exclude_direct_test': '1'}))
        self.assertEqual([row['id'] for row in matching['list']], [order.pk])
        self.assertEqual((matching['list'][0]['refund_day_amount'], matching['list'][0]['refund_day_count']), (3000, 2))
        self.assertEqual(matching['refund_day_applied'], {'date': today, 'timezone': after_second['timezone']})
        self.assertEqual(matching['refund_day_order'], 'latest_desc')
        earlier = self.ok(self.client.get('/api/admin/orders/', {'refund_day': historical_day, 'exclude_direct_test': '1'}))
        self.assertEqual(earlier['list'], [])
        # Re-date the first real refund in the isolated test database to check
        # that each day's amount is independent of the lifetime refund total.
        first_refund = order.refund_records.order_by('pk').first()
        self.assertEqual(first_refund.amount, 2500)
        first_timestamp = first_refund.created_at
        historical_refund_time = created + timedelta(hours=3)
        self.assertEqual(timezone.localdate(historical_refund_time).isoformat(), historical_day)
        order.refund_records.filter(pk=first_refund.pk).update(created_at=historical_refund_time)
        old_day = self.ok(self.client.get('/api/admin/orders/', {'refund_day': historical_day, 'exclude_direct_test': '1'}))
        current_day = self.ok(self.client.get('/api/admin/orders/', {'refund_day': today, 'exclude_direct_test': '1'}))
        self.assertEqual([row['id'] for row in old_day['list']], [order.pk])
        self.assertEqual([row['id'] for row in current_day['list']], [order.pk])
        self.assertEqual((old_day['list'][0]['refund_day_amount'], old_day['list'][0]['refund_day_count']), (2500, 1))
        self.assertEqual((current_day['list'][0]['refund_day_amount'], current_day['list'][0]['refund_day_count']), (500, 1))
        order.refund_records.filter(pk=first_refund.pk).update(created_at=first_timestamp)
        # The most recently refunded order appears first, even when it was
        # created before the other order and the refund happened on a later day.
        gift_id = gift_order['id']
        Order.objects.filter(pk=gift_id).update(created_at=created - timedelta(days=1))
        gift_preview = self.ok(self.client.get(f'/api/admin/orders/{gift_id}/refund-preview/', {'refund_amount': 500}))
        self.ok(self.client.post(f'/api/admin/orders/{gift_id}/refund/', {'refund_amount': 500,
            'reason': '隔离礼物退款排序', 'client_request_id': 'cohort-gift-refund',
            'plan_token': gift_preview['plan_token']}, format='json'))
        sorted_orders = self.ok(self.client.get('/api/admin/orders/', {'refund_day': today, 'exclude_direct_test': '1'}))
        self.assertEqual([row['id'] for row in sorted_orders['list']], [gift_id, order.pk])
        self.assertEqual((sorted_orders['list'][0]['refund_day_amount'], sorted_orders['list'][0]['refund_day_count']), (500, 1))
        self.assertTrue(sorted_orders['list'][0]['refund_day_latest'])
