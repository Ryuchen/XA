from orders.tests.test_release_flow import ReleaseFlowTest
"""Cross-entry regressions for the boss → dispatch → fulfillment → report flow."""
from datetime import timedelta
import tempfile
from uuid import uuid4
from unittest.mock import patch, AsyncMock

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider
from orders.services import count_active_orders
from orders.notifier import notify_order_update
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class OrderClosureAuditTest(APITestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        for target in ('orders.views._schedule_auto_cancel', 'orders.views.enqueue_kook_dispatch', 'orders.views.enqueue_wecom_dispatch', 'orders.views.notify_order_update'):
            self.enterContext(patch(target))
        self.boss = make_user()
        self.first, self.second = make_provider(), make_provider()
        self.admin = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'order:settle', 'report:audit'])
        self.accounts = {}
        for user in (self.boss, self.first, self.second, self.admin):
            self.register(user)
        self.service = make_service(price=10000, commission_rate=20)
        make_wallet(self.boss, balance=100000)

    def register(self, user):
        account = get_or_create_account_for_legacy_user(user)
        for relation in ('escort_profile', 'admin_membership'):
            record = getattr(user, relation, None)
            if record:
                record.account = account
                record.save(update_fields=['account'])
        self.accounts[user.pk] = account
        return account

    def login(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def grab_and_choose(self, order):
        credentials=dict(self.client._credentials)
        data=self.ok(self.client.post(f'/api/orders/orders/{order.pk}/grab/'))
        self.login(self.boss)
        self.ok(self.client.post(f"/api/orders/orders/{order.pk}/applications/{data['my_applications'][0]['id']}/select/"))
        self.client.credentials(**credentials)

    def new_order(self):
        self.login(self.boss)
        row = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'client_request_id': str(uuid4())}, format='json'))
        Order.objects.filter(pk=row['id']).update(created_at=timezone.now() - timedelta(hours=1))
        return Order.objects.get(pk=row['id'])

    def test_assigned_then_rejected_then_grabbed_order_only_pays_the_final_provider(self):
        order = self.new_order()
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': self.accounts[self.first.pk].pk}))
        self.login(self.first)
        draft = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/reject/', {'reason': '档期变更'}))
        old = OrderProvider.objects.get(order=order, provider=self.first)
        self.assertEqual(old.provider_income, 0)
        self.assertIsNotNone(old.released_at)
        self.assertEqual(count_active_orders(self.accounts[self.first.pk]), 0)
        self.login(self.second)
        self.grab_and_choose(order)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=self.second)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.first)
        report_data = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == draft['id'])
        self.assertFalse(report_data['can_edit'])
        self.assertEqual(self.client.post(f"/api/wallet/reports/{draft['id']}/submit/").data['code'], 400)
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        ReleaseFlowTest.settle_after_window(self)
        self.assertEqual(Wallet.objects.get(user=self.first).balance, 0)
        self.assertEqual(Wallet.objects.get(user=self.second).balance, 8000)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)
        self.first.escort_profile.refresh_from_db()
        self.assertEqual(self.first.escort_profile.completed_order_count, 0)

    def test_same_provider_can_rejoin_after_rejection_without_duplicate_shares(self):
        order = self.new_order()
        self.login(self.first)
        self.grab_and_choose(order)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/reject/'))
        self.grab_and_choose(order)
        self.assertEqual(order.providers.count(), 1)
        share = order.providers.get()
        self.assertIsNone(share.released_at)
        self.assertEqual(share.provider_income, 8000)

    def test_reassigning_legacy_pending_order_releases_stale_provider_share(self):
        order = self.new_order()
        stale = OrderProvider.objects.create(
            order=order, provider=self.first, provider_account=self.accounts[self.first.pk],
            settlement_base=10000, provider_income=8000, commission_rate=20,
        )
        self.login(self.second)
        self.grab_and_choose(order)
        stale.refresh_from_db()
        self.assertIsNotNone(stale.released_at)
        self.assertEqual(stale.provider_income, 0)
        self.assertEqual(sum(order.providers.values_list('provider_income', flat=True)), 8000)

    def test_busy_provider_with_capacity_sees_grab_action_and_can_take_second_order(self):
        first, second = self.new_order(), self.new_order()
        self.login(self.first)
        self.grab_and_choose(first)
        rows = self.ok(self.client.get('/api/orders/orders/?status=pending'))
        self.assertIn('GRAB', next(r for r in rows if r['id'] == second.pk)['allowed_actions'])
        self.grab_and_choose(second)
        self.assertEqual(count_active_orders(self.accounts[self.first.pk]), 2)

    def test_unverified_or_unpaid_deposit_is_blocked_at_every_assignment_entry(self):
        order = self.new_order()
        for fields in ({'is_verified': False, 'deposit_required': 0}, {'is_verified': True, 'deposit_required': 100, 'deposit_paid': 0}):
            with self.subTest(fields=fields):
                type(self.first.escort_profile).objects.filter(user=self.first).update(**fields)
                self.login(self.first)
                self.assertEqual(self.ok(self.client.get('/api/orders/orders/?status=pending')), [])
                self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/').data['code'], 400)
                self.login(self.boss)
                self.assertEqual(self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'provider_id': self.accounts[self.first.pk].pk}).data['code'], 400)
                self.login(self.admin)
                for path in (f'/api/admin/orders/{order.pk}/assign/', f'/api/orders/orders/{order.pk}/assign/'):
                    self.assertEqual(self.client.post(path, {'provider_id': self.accounts[self.first.pk].pk}).data['code'], 400)
                self.assertEqual(self.client.post('/api/admin/orders/dispatch/', {'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk, 'provider_id': self.accounts[self.first.pk].pk}).data['code'], 400)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)

    def test_read_only_staff_cannot_bypass_admin_permissions_using_legacy_order_routes(self):
        reader = make_console_user(['order:view'])
        self.register(reader)
        order = self.new_order()
        self.login(reader)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/assign/', {'provider_id': self.accounts[self.first.pk].pk}).status_code, 403)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/refund/', {'reason': '越权退款'}).status_code, 403)

    def test_refunded_order_cannot_edit_existing_report_evidence(self):
        order = self.new_order()
        self.login(self.first)
        self.grab_and_choose(order)
        report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}))
        uploaded = self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '售后终止'}))
        self.login(self.first)
        self.assertFalse(self.ok(self.client.get('/api/wallet/reports/'))[0]['can_edit'])
        self.assertEqual(self.client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': 'ENTRY', 'image': screenshot()}, format='multipart').data['code'], 400)
        self.assertEqual(self.client.delete(f"/api/wallet/reports/{report['id']}/images/{uploaded['result_images'][0]['id']}/").data['code'], 400)

    def test_expired_order_cannot_be_assigned_before_timeout_worker_runs(self):
        order = self.new_order()
        Order.objects.filter(pk=order.pk).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.login(self.admin)
        for path in (f'/api/admin/orders/{order.pk}/assign/', f'/api/orders/orders/{order.pk}/assign/'):
            self.assertEqual(self.client.post(path, {'provider_id': self.accounts[self.first.pk].pk}).data['code'], 400)

    def test_admin_cannot_start_or_complete_an_unpaid_order(self):
        order = self.new_order()
        self.login(self.admin)
        for state, action in ((Order.Status.GRABBED, 'start'), (Order.Status.IN_SERVICE, 'complete')):
            Order.objects.filter(pk=order.pk).update(status=state, payment_status=Order.PaymentStatus.UNPAID, provider=self.first)
            self.assertEqual(self.client.post(f'/api/admin/orders/{order.pk}/{action}/').data['code'], 400)
            order.refresh_from_db()
            self.assertEqual(order.status, state)
        self.assertEqual(Wallet.objects.get(user=self.first).balance, 0)

    def test_legacy_refund_cannot_refund_already_paid_provider_shares(self):
        order = self.new_order()
        self.login(self.first)
        self.grab_and_choose(order)
        order.providers.update(settled_at=timezone.now())
        self.login(self.admin)
        response = self.client.post(f'/api/orders/orders/{order.pk}/refund/', {'reason': '测试已结算防护'})
        self.assertEqual(response.data['code'], 400)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.GRABBED)

    def test_admin_dispatch_rejects_stale_quote_before_debit_then_accepts_reconfirmed_price(self):
        self.login(self.admin)
        payload = {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'expected_amount': 10000, 'client_request_id': str(uuid4()),
        }
        self.service.price = 15000
        self.service.save(update_fields=['price'])
        response = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(response.data['errorCode'], 'PRICE_CHANGED')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
        self.assertFalse(Order.objects.exists())
        payload['expected_amount'] = 15000
        self.ok(self.client.post('/api/admin/orders/dispatch/', payload, format='json'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 85000)

    def test_public_broadcast_contains_no_private_order_data(self):
        order = self.new_order()
        with patch('orders.notifier.get_channel_layer') as get_layer:
            layer = get_layer.return_value
            layer.group_send = AsyncMock()
            notify_order_update(order)
            public_events = [call.args[1]['data'] for call in layer.group_send.call_args_list if call.args[0] == 'providers']
        self.assertEqual(public_events, [{'id': order.pk, 'status': order.status}])

    def test_broken_notification_channel_does_not_fail_committed_order(self):
        order = self.new_order()
        with patch('orders.notifier.get_channel_layer', side_effect=RuntimeError('channel unavailable')):
            with self.assertLogs('orders.notifier', level='WARNING'):
                notify_order_update(order)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)
