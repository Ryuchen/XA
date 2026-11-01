from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, current_thread
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderTimeoutConfig, OrderTimeoutConfigHistory
from orders.services import schedule_auto_cancel
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction, Wallet

CONFIG = '/api/admin/config/order-timeout/'


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class OrderTimeoutConfigTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.provider = make_provider()
        self.reader = make_console_user(['order:view'])
        self.editor = make_console_user(['order:view', 'order:timeout', 'order:dispatch'])
        self.write_only = make_console_user(['order:timeout'])
        self.auditor = make_console_user(['audit:view'])
        self.accounts = {u.pk: get_or_create_account_for_legacy_user(u)
                         for u in (self.boss, self.provider, self.reader, self.editor, self.write_only, self.auditor)}
        for user in (self.reader, self.editor, self.write_only, self.auditor):
            user.admin_membership.account = self.accounts[user.pk]
            user.admin_membership.save(update_fields=['account'])
        EscortProfile.objects.filter(user=self.provider).update(account=self.accounts[self.provider.pk])
        self.service = make_service(price=10000)
        make_wallet(self.boss, balance=100000)
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch', 'notify_order_update'):
            patcher = patch('orders.views.' + name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def auth(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def config(self, minutes, revision):
        self.auth(self.editor)
        return self.client.put(CONFIG, {'minutes': minutes, 'revision': revision}, format='json')

    def create(self, **fields):
        self.auth(self.boss)
        return self.client.post('/api/orders/create/', {'service_id': self.service.pk, **fields}, format='json')

    def test_permissions_validation_revision_and_atomic_audit(self):
        self.auth(self.reader)
        self.assertEqual(self.client.get(CONFIG).data['data'], {'minutes': 30, 'revision': 0})
        self.assertEqual(self.client.put(CONFIG, {'minutes': 10, 'revision': 0}, format='json').status_code, 403)
        self.auth(self.write_only)
        self.assertEqual(self.client.put(CONFIG, {'minutes': 10, 'revision': 0}, format='json').status_code, 403)
        self.auth(self.boss)
        self.assertEqual(self.client.get(CONFIG).status_code, 403)
        for bad in (0, 1441, True, 1.5, '30', None):
            self.assertEqual(self.config(bad, 0).status_code, 400)
        self.assertEqual(self.config(1, 0).data['data'], {'minutes': 1, 'revision': 1})
        self.assertEqual(self.config(1440, 0).status_code, 409)
        self.assertEqual(self.config(1440, 1).data['data'], {'minutes': 1440, 'revision': 2})
        history = list(OrderTimeoutConfigHistory.objects.order_by('revision').values_list('previous_minutes', 'minutes', 'revision'))
        self.assertEqual(history, [(30, 1, 1), (1, 1440, 2)])
        with patch('orders.timeout_views.OrderTimeoutConfigHistory.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.config(30, 2)
        self.assertEqual(OrderTimeoutConfig.objects.get(pk=1).minutes, 1440)
        self.assertEqual(OrderTimeoutConfig.objects.get(pk=1).revision, 2)

    def test_new_old_and_selected_orders_reject_using_their_own_snapshot(self):
        self.assertEqual(self.config(7, 0).data['code'], 0)
        created = self.create(provider_id=self.accounts[self.provider.pk].pk, client_request_id='selected-snapshot')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        self.assertEqual(order.pending_timeout_minutes, 7)
        self.assertIsNone(order.auto_cancel_at)
        self.assertEqual(self.config(12, 1).data['code'], 0)
        self.auth(self.provider)
        before = timezone.now()
        response = self.client.post(f'/api/orders/orders/{order.pk}/reject/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order.refresh_from_db()
        self.assertTrue(before + timedelta(minutes=7) <= order.auto_cancel_at <= timezone.now() + timedelta(minutes=7))
        # Simulate an order created before the migration, without rewriting its old deadline.
        Order.objects.filter(pk=order.pk).update(pending_timeout_minutes=None, status='GRABBED',
            provider=self.provider, provider_account=self.accounts[self.provider.pk], auto_cancel_at=None)
        self.auth(self.provider)
        before = timezone.now()
        response = self.client.post(f'/api/orders/orders/{order.pk}/reject/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order.refresh_from_db()
        self.assertTrue(before + timedelta(minutes=30) <= order.auto_cancel_at <= timezone.now() + timedelta(minutes=30))

    def test_history_requires_audit_permission_and_paginates_stable_snapshots(self):
        name = self.accounts[self.editor.pk].nickname or self.accounts[self.editor.pk].username
        for revision, minutes in enumerate((7, 15, 60)):
            self.assertEqual(self.config(minutes, revision).data['code'], 0)
        account = self.accounts[self.editor.pk]
        account.nickname = '变更后的名称'
        account.save(update_fields=['nickname'])
        self.auth(self.reader)
        self.assertEqual(self.client.get(CONFIG + 'history/').status_code, 403)
        self.auth(self.auditor)
        first = self.client.get(CONFIG + 'history/?page=1&page_size=2').data['data']
        second = self.client.get(CONFIG + 'history/?page=2&page_size=2').data['data']
        self.assertEqual((first['total'], first['page'], first['page_size']), (3, 1, 2))
        self.assertEqual([row['revision'] for row in first['list']], [3, 2])
        self.assertEqual([row['revision'] for row in second['list']], [1])
        self.assertEqual((first['list'][0]['previous_minutes'], first['list'][0]['minutes']), (15, 60))
        self.assertEqual(first['list'][0]['operator_name'], name)
        self.assertTrue(first['list'][0]['created_at'])

    def test_quote_change_cannot_debit_and_existing_deadline_is_unchanged(self):
        self.auth(self.boss)
        quote = self.client.post('/api/orders/quote/', {'service_id': self.service.pk}, format='json')
        self.assertEqual(quote.data['data']['pending_timeout_minutes'], 30)
        first = self.create(client_request_id='old-deadline')
        order = Order.objects.get(pk=first.data['data']['id'])
        deadline = order.auto_cancel_at
        self.assertEqual(self.config(60, 0).data['code'], 0)
        self.auth(self.boss)
        changed = self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'client_request_id': 'time-change', 'expected_timeout_minutes': 30}, format='json')
        self.assertEqual(changed.data['errorCode'], 'TIMEOUT_CHANGED')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)
        self.assertEqual(Transaction.objects.filter(tx_type='PAY').count(), 1)
        order.refresh_from_db()
        self.assertEqual((order.pending_timeout_minutes, order.auto_cancel_at), (30, deadline))
        second = self.create(client_request_id='new-deadline')
        new = Order.objects.get(pk=second.data['data']['id'])
        self.assertEqual(new.pending_timeout_minutes, 60)
        self.assertGreater(new.auto_cancel_at, timezone.now() + timedelta(minutes=59))

    def test_scheduler_uses_persisted_deadline_and_skips_missing_deadline(self):
        order = self.create(client_request_id='schedule').data['data']
        obj = Order.objects.get(pk=order['id'])
        with patch('orders.services.timezone.now', return_value=obj.auto_cancel_at - timedelta(seconds=61)), \
             patch('orders.tasks.auto_cancel_pending_order.apply_async') as queued:
            schedule_auto_cancel(obj)
        queued.assert_called_once_with(args=[obj.pk], countdown=61)
        obj.auto_cancel_at = None
        with patch('orders.tasks.auto_cancel_pending_order.apply_async') as queued:
            schedule_auto_cancel(obj)
        queued.assert_not_called()

    def test_old_task_does_not_cancel_a_new_round_with_later_deadline(self):
        from orders.tasks import auto_cancel_pending_order
        created = self.create(client_request_id='old-task')
        order = Order.objects.get(pk=created.data['data']['id'])
        future = timezone.now() + timedelta(minutes=90)
        Order.objects.filter(pk=order.pk).update(auto_cancel_at=future)
        with patch.object(auto_cancel_pending_order, 'retry', side_effect=RuntimeError('not due')) as retry:
            with self.assertRaisesRegex(RuntimeError, 'not due'):
                auto_cancel_pending_order.run(order.pk)
        self.assertGreater(retry.call_args.kwargs['countdown'], 0)
        order.refresh_from_db()
        self.assertEqual((order.status, order.auto_cancel_at), ('PENDING', future))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())

    def test_admin_selected_order_also_freezes_duration_for_rejection(self):
        self.assertEqual(self.config(9, 0).data['code'], 0)
        self.auth(self.editor)
        with patch('console.views._schedule_auto_cancel'):
            response = self.client.post('/api/admin/orders/dispatch/', {
                'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
                'provider_id': self.accounts[self.provider.pk].pk, 'client_request_id': 'admin-selected',
            }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(order.pending_timeout_minutes, 9)
        self.assertIsNone(order.auto_cancel_at)
        self.assertEqual(self.config(18, 1).data['code'], 0)
        self.auth(self.provider)
        before = timezone.now()
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/reject/', {}, format='json').data['code'], 0)
        order.refresh_from_db()
        self.assertTrue(before + timedelta(minutes=9) <= order.auto_cancel_at <= timezone.now() + timedelta(minutes=9))


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class OrderTimeoutConfigRaceTest(TransactionTestCase):
    def test_concurrent_first_writes_never_overwrite_revision(self):
        staff = make_console_user(['order:view', 'order:timeout'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        OrderTimeoutConfig.objects.filter(pk=1).delete()
        token, _ = issue_account_tokens(account)
        barrier = Barrier(2)
        def write(minutes):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.put(CONFIG, {'minutes': minutes, 'revision': 0}, format='json').data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = [future.result(timeout=20) for future in (workers.submit(write, 7), workers.submit(write, 60))]
        self.assertEqual(sorted(result['code'] for result in results), [0, 409], results)
        self.assertEqual(OrderTimeoutConfig.objects.get(pk=1).revision, 1)
        self.assertEqual(OrderTimeoutConfigHistory.objects.count(), 1)

    def test_uncommitted_config_does_not_block_orders_and_confirmed_snapshot_is_preserved(self):
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        make_wallet(boss, balance=100000)
        staff = make_console_user(['order:view', 'order:timeout'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        service = make_service(price=10000)
        OrderTimeoutConfig.objects.get_or_create(pk=1)
        boss_token, _ = issue_account_tokens(boss_account)
        staff_token, _ = issue_account_tokens(staff_account)
        locked, release = Event(), Event()
        from orders.models import OrderTimeoutConfigHistory
        create_history = OrderTimeoutConfigHistory.objects.create
        def pause_update(**kwargs):
            row = create_history(**kwargs)
            locked.set()
            if not release.wait(10):
                raise AssertionError('config lock was not released')
            return row
        def post(name, method, path, data, token):
            current_thread().name = name
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                return getattr(client, method)(path, data, format='json').data
            finally:
                close_old_connections()
        with patch('orders.timeout_views.OrderTimeoutConfigHistory.objects.create', side_effect=pause_update), \
             patch('orders.views._schedule_auto_cancel'), ThreadPoolExecutor(max_workers=2) as workers:
            update = workers.submit(post, 'update', 'put', CONFIG, {'minutes': 60, 'revision': 0}, staff_token)
            try:
                self.assertTrue(locked.wait(10))
                create = workers.submit(post, 'create', 'post', '/api/orders/create/', {
                    'service_id': service.pk, 'client_request_id': 'timeout-config-race',
                    'expected_timeout_minutes': 30}, boss_token)
                result = create.result(timeout=10)
            finally:
                release.set()
            changed = update.result(timeout=20)
        self.assertEqual(changed['code'], 0, changed)
        self.assertEqual(result['code'], 0, result)
        first = Order.objects.get(pk=result['data']['id'])
        self.assertEqual(first.pending_timeout_minutes, 30)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        changed_result = client.post('/api/orders/create/', {'service_id': service.pk,
            'client_request_id': 'after-config-change', 'expected_timeout_minutes': 30}, format='json').data
        self.assertEqual(changed_result['errorCode'], 'TIMEOUT_CHANGED')
        legacy = client.post('/api/orders/create/', {'service_id': service.pk,
            'client_request_id': 'legacy-after-change'}, format='json').data
        self.assertEqual(legacy['code'], 0, legacy)
        self.assertEqual(Order.objects.get(pk=legacy['data']['id']).pending_timeout_minutes, 60)
        self.assertEqual(Wallet.objects.get(user=boss).balance, 80000)
