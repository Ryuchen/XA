from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.dispatching import channel_config, deliver_dispatch, queue_dispatch
from orders.models import Order, OrderApplication, WecomDispatchRecord
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, WECOM_DISPATCH_ENABLED=True,
                   WECOM_DISPATCH_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only')
class DispatchRouteTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.provider = make_provider()
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        EscortProfile.objects.filter(user=self.provider).update(account=self.provider_account)
        self.staff = make_console_user(['order:view', 'order:dispatch'])
        self.staff_account = get_or_create_account_for_legacy_user(self.staff)
        self.staff.admin_membership.account = self.staff_account
        self.staff.admin_membership.save(update_fields=['account'])
        self.service = make_service(price=10000, commission_rate=20)
        make_wallet(self.boss, balance=100000)
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch'):
            patcher = patch('orders.views.' + name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def auth(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def create(self, **kwargs):
        self.auth(self.boss_account)
        return self.client.post('/api/orders/create/', {'service_id': self.service.pk, **kwargs}, format='json')

    def test_staff_route_is_private_and_admin_can_assign_then_provider_rejects(self):
        response = self.create(dispatch_route='STAFF', client_request_id='staff-route')
        self.assertEqual(response.data['code'], 0)
        self.assertEqual(response.data['data']['dispatch_route'], 'STAFF')
        self.assertEqual(response.data['data']['allowed_actions'], ['CANCEL'])
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertFalse(order.requires_boss_selection)
        self.assertIsNotNone(order.auto_cancel_at)
        self.enqueue_kook_dispatch.assert_not_called()
        self.enqueue_wecom_dispatch.assert_not_called()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(minutes=5))

        self.auth(self.provider_account)
        pool = self.client.get('/api/orders/orders/?status=pending')
        self.assertNotIn(order.pk, [row['id'] for row in pool.data['data']])
        self.assertEqual(self.client.get(f'/api/orders/orders/{order.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json').data['code'], 400)
        # Even inconsistent stale data must not open the application API.
        Order.objects.filter(pk=order.pk).update(requires_boss_selection=True)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json').data['code'], 400)
        self.assertFalse(OrderApplication.objects.filter(order=order).exists())

        self.auth(self.staff_account)
        public_order = make_order(self.boss, customer_account=self.boss_account, service=self.service)
        listing = self.client.get('/api/admin/orders/?dispatch_route=STAFF&status=PENDING&payment_status=PAID')
        rows = listing.data['data']['list']
        self.assertIn(order.pk, [row['id'] for row in rows])
        self.assertNotIn(public_order.pk, [row['id'] for row in rows])
        self.assertEqual(next(row for row in rows if row['id'] == order.pk)['dispatch_route'], 'STAFF')
        assigned = self.client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': self.provider_account.pk}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)

        self.auth(self.provider_account)
        rejected = self.client.post(f'/api/orders/orders/{order.pk}/reject/', {'reason': '档期变化'}, format='json')
        self.assertEqual(rejected.data['code'], 0, rejected.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.dispatch_route, order.requires_boss_selection), ('PENDING', 'STAFF', False))
        self.assertIsNone(order.provider_account_id)
        self.assertGreater(order.auto_cancel_at, timezone.now())
        self.enqueue_kook_dispatch.assert_not_called()
        self.enqueue_wecom_dispatch.assert_not_called()
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json').data['code'], 400)

    def test_invalid_route_combinations_do_not_debit(self):
        cases = [
            {'dispatch_route': 'UNKNOWN'},
            {'dispatch_route': 'STAFF', 'dispatch_mode': 'AUTO_GRAB'},
            {'dispatch_route': 'STAFF', 'provider_id': self.provider_account.pk},
        ]
        for data in cases:
            with self.subTest(data=data):
                self.assertEqual(self.create(**data).data['code'], 400)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)

    def test_default_public_and_idempotency_consider_route(self):
        first = self.create(client_request_id='route-key')
        self.assertEqual(first.data['data']['dispatch_route'], 'PUBLIC')
        repeat = self.create(client_request_id='route-key', dispatch_route='STAFF')
        self.assertEqual(repeat.data['errorCode'], 'REQUEST_ID_CONFLICT')
        self.assertEqual(Order.objects.count(), 1)
        # Different routes also represent distinct legacy no-request-id writes.
        self.assertEqual(self.create().data['code'], 0)
        self.assertEqual(self.create(dispatch_route='STAFF').data['code'], 0)
        self.assertEqual(Order.objects.count(), 3)

    def test_late_queued_group_delivery_and_manual_queue_recheck_route(self):
        order = make_order(self.boss, customer_account=self.boss_account, service=self.service,
                           dispatch_route='STAFF', auto_cancel_at=timezone.now() + timedelta(minutes=5))
        record = WecomDispatchRecord.objects.create(order=order, trigger='NEW_ORDER', channel_id=channel_config('WECOM')[1])
        with patch('integrations.wecom.WecomClient.send_text') as send:
            self.assertIsNone(queue_dispatch(order, 'WECOM', manual=True))
            self.assertTrue(deliver_dispatch('WECOM', record.pk).startswith('skip:'))
            send.assert_not_called()
        record.refresh_from_db()
        self.assertEqual(record.status, 'SKIPPED')

    def test_staff_order_notifies_operators_without_broadcasting_pool(self):
        from orders.notifier import _notify_order_update
        order = make_order(self.boss, customer_account=self.boss_account, service=self.service,
                           dispatch_route='STAFF')
        groups = []
        with patch('orders.notifier.async_to_sync', side_effect=lambda _: lambda group, event: groups.append(group)):
            _notify_order_update(order)
        self.assertIn('operators', groups)
        self.assertIn(f'account_{self.boss_account.pk}', groups)
        self.assertNotIn('providers', groups)

    def test_staff_timeout_refunds_once(self):
        response = self.create(dispatch_route='STAFF', client_request_id='staff-timeout')
        order = Order.objects.get(pk=response.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(auto_cancel_pending_order(order.pk), 'cancelled')
        self.assertNotEqual(auto_cancel_pending_order(order.pk), 'cancelled')
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)

    def test_boss_selection_closes_at_server_deadline_and_remains_scoped(self):
        response = self.create(dispatch_route='PUBLIC', dispatch_mode='BOSS_SELECT', client_request_id='deadline-view')
        order = Order.objects.get(pk=response.data['data']['id'])
        applicant = OrderApplication.objects.create(order=order, applicant=self.provider_account)
        deadline = order.auto_cancel_at
        url = f'/api/orders/orders/{order.pk}/'
        self.auth(self.boss_account)
        with patch('orders.serializers.timezone.now', return_value=deadline - timedelta(microseconds=1)):
            row = self.client.get(url).data['data']
            self.assertFalse(row['pending_deadline_expired'])
            self.assertIn('SELECT_PROVIDER', row['allowed_actions'])
        with patch('orders.serializers.timezone.now', return_value=deadline):
            row = self.client.get(url).data['data']
            self.assertTrue(row['pending_deadline_expired'])
            self.assertNotIn('SELECT_PROVIDER', row['allowed_actions'])
            self.assertIn('CANCEL', row['allowed_actions'])
            selected = self.client.post(f'/api/orders/orders/{order.pk}/applications/{applicant.pk}/select/', {}, format='json')
            self.assertNotEqual(selected.data['code'], 0)
            self.assertEqual(Order.objects.get(pk=order.pk).status, 'PENDING')
        other_account = get_or_create_account_for_legacy_user(make_user())
        self.auth(other_account)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.auth(self.provider_account)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.auth(self.staff_account)
        with patch('orders.serializers.timezone.now', return_value=deadline):
            row = self.client.get(url).data['data']
            self.assertTrue(row['pending_deadline_expired'])
            self.assertNotIn('SELECT_PROVIDER', row['allowed_actions'])

    def test_staff_deadline_and_non_pending_deadlines_remain_distinct(self):
        response = self.create(dispatch_route='STAFF', client_request_id='staff-deadline-view')
        order = Order.objects.get(pk=response.data['data']['id'])
        self.auth(self.boss_account)
        with patch('orders.serializers.timezone.now', return_value=order.auto_cancel_at):
            row = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
            self.assertTrue(row['pending_deadline_expired'])
            self.assertEqual(row['allowed_actions'], ['CANCEL'])
        Order.objects.filter(pk=order.pk).update(auto_cancel_at=None)
        with patch('orders.serializers.timezone.now', return_value=order.auto_cancel_at + timedelta(seconds=1)):
            row = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
            self.assertFalse(row['pending_deadline_expired'])
        Order.objects.filter(pk=order.pk).update(status='GRABBED', auto_cancel_at=order.auto_cancel_at)
        with patch('orders.serializers.timezone.now', return_value=order.auto_cancel_at + timedelta(seconds=1)):
            row = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
            self.assertFalse(row['pending_deadline_expired'])


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class StaffDispatchRaceTest(TransactionTestCase):
    def test_grab_racing_staff_assignment_never_claims_private_order(self):
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider = make_provider()
        provider_account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        staff = make_console_user(['order:view', 'order:dispatch'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        order = make_order(boss, customer_account=boss_account, dispatch_route='STAFF',
                           auto_cancel_at=timezone.now() + timedelta(minutes=5))
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(minutes=5))
        provider_token, _ = issue_account_tokens(provider_account)
        staff_token, _ = issue_account_tokens(staff_account)
        barrier = Barrier(2)

        def post(path, token, data):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post(path, data, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as workers:
            grab = workers.submit(post, f'/api/orders/orders/{order.pk}/grab/', provider_token, {})
            assign = workers.submit(post, f'/api/admin/orders/{order.pk}/assign/', staff_token,
                                    {'provider_id': provider_account.pk})
            grab_result, assign_result = grab.result(timeout=15), assign.result(timeout=15)
        self.assertNotEqual(grab_result['code'], 0)
        self.assertEqual(assign_result['code'], 0, assign_result)
        order.refresh_from_db()
        self.assertEqual((order.dispatch_route, order.status, order.provider_account_id),
                         ('STAFF', 'GRABBED', provider_account.pk))

