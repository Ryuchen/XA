from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Barrier, Event, current_thread
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction, Wallet


def screenshot():
    output = BytesIO()
    Image.new('RGB', (4, 4), 'white').save(output, format='PNG')
    return SimpleUploadedFile('team.png', output.getvalue(), content_type='image/png')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class StaffTeamDispatchTest(APITestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=self.media.name))
        self.boss = make_user()
        self.staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'report:view', 'report:audit'])
        self.providers = [make_provider() for _ in range(3)]
        self.accounts = {user.pk: get_or_create_account_for_legacy_user(user)
                         for user in [self.boss, self.staff, *self.providers]}
        self.staff.admin_membership.account = self.accounts[self.staff.pk]
        self.staff.admin_membership.save(update_fields=['account'])
        for user in self.providers:
            EscortProfile.objects.filter(user=user).update(account=self.accounts[user.pk])
        self.service = make_service(price=5001, billing_provider_count=2, commission_override=True,
                                    commission_type='FIXED', commission_fixed=1001)
        make_wallet(self.boss, balance=100000)
        for name in ('_schedule_auto_cancel', 'notify_order_update'):
            patcher = patch('orders.views.' + name)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('notify_order_update', '_push_message_safe'):
            patcher = patch('console.views.' + name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def auth(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def create(self):
        self.auth(self.boss)
        response = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': 'staff-team',
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return Order.objects.get(pk=response.data['data']['id'])

    def assign(self, order, providers=None):
        self.auth(self.staff)
        return self.client.post(f'/api/admin/orders/{order.pk}/assign/',
                                {'provider_ids': [self.accounts[p.pk].pk for p in (providers or self.providers[:2])]},
                                format='json')

    def test_full_two_person_evidence_and_approval_settle_once(self):
        order = self.create()
        self.assertEqual((order.status, order.amount, order.required_provider_count, order.dispatch_route),
                         ('PENDING', 10002, 2, 'STAFF'))
        self.assertFalse(order.providers.exists())
        # Catalog changes after payment do not alter the purchased amount or fixed commission.
        self.service.price = 9999
        self.service.commission_fixed = 3000
        self.service.save(update_fields=['price', 'commission_fixed'])
        response = self.assign(order)
        self.assertEqual(response.data['code'], 0, response.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.escort_mode, order.requires_boss_selection), ('GRABBED', 'DOUBLE', True))
        self.assertEqual(order.amount, 10002)
        self.assertIsNone(order.auto_cancel_at)
        shares = list(order.providers.order_by('pk'))
        self.assertEqual([row.settlement_base for row in shares], [5001, 5001])
        self.assertEqual([row.commission_fixed for row in shares], [501, 500])
        self.assertEqual(order.provider_income + order.shop_income + order.inviter_commission, order.amount)
        self.assertEqual(self._push_message_safe.call_count, 3)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        for index, user in enumerate(self.providers[:2]):
            self.auth(user)
            reject = self.client.post(f'/api/orders/orders/{order.pk}/reject/', {}, format='json')
            self.assertEqual(reject.data['code'], 400)
            result = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            order.refresh_from_db()
            self.assertEqual(order.status, 'GRABBED' if index == 0 else 'IN_SERVICE')
        for index, user in enumerate(self.providers[:2]):
            self.auth(user)
            result = self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            order.refresh_from_db()
            self.assertEqual(order.status, 'IN_SERVICE' if index == 0 else 'COMPLETED')
        for index, user in enumerate(self.providers[:2]):
            self.auth(user)
            report = ProviderReport.objects.get(order=order, provider=user)
            result = self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual(self.client.post(f'/api/wallet/reports/{report.pk}/submit/').data['code'], 0)
            self.auth(self.staff)
            approval = self.client.post(f'/api/admin/reports/{report.pk}/approve/')
            self.assertEqual(approval.data['code'], 0, approval.data)
            if index == 0:
                order.refresh_from_db()
                self.assertIsNone(order.settled_at)
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        from engagement.confirmation import process_order
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            process_order(order.pk)
            process_order(order.pk)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)

    def test_validation_failure_refund_and_existing_assignments_are_atomic(self):
        order = self.create()
        self.auth(self.staff)
        url = f'/api/admin/orders/{order.pk}/assign/'
        first, second = [self.accounts[user.pk].pk for user in self.providers[:2]]
        for ids in ([], [first], [first, first], [first, 999999], ['1', second]):
            result = self.client.post(url, {'provider_ids': ids}, format='json')
            self.assertNotEqual(result.data['code'], 0)
        EscortProfile.objects.filter(user=self.providers[1]).update(is_verified=False)
        self.assertNotEqual(self.assign(order).data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, 'PENDING')
        self.assertFalse(order.providers.exists())
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        EscortProfile.objects.filter(user=self.providers[1]).update(is_verified=True)
        EscortProfile.objects.filter(user=self.providers[1]).update(status=EscortProfile.Status.BUSY)
        self.assertNotEqual(self.assign(order).data['code'], 0)
        self.assertFalse(order.providers.exists())
        EscortProfile.objects.filter(user=self.providers[1]).update(status=EscortProfile.Status.AVAILABLE)
        OrderProvider.objects.create(order=order, provider=self.providers[2], provider_account=self.accounts[self.providers[2].pk])
        self.assertNotEqual(self.assign(order).data['code'], 0)
        self.assertEqual(order.providers.count(), 1)
        order.providers.all().delete()
        self.auth(self.staff)
        refund = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '客户取消'})
        self.assertEqual(refund.data['code'], 0, refund.data)
        self.assertNotEqual(self.assign(order).data['code'], 0)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)

    def test_expired_order_cannot_be_assigned_before_timeout_worker_runs(self):
        order = self.create()
        Order.objects.filter(pk=order.pk).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        result = self.assign(order)
        self.assertNotEqual(result.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, 'PENDING')
        self.assertFalse(order.providers.exists())
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)

    def test_partial_team_entry_then_refund_does_not_start_or_settle(self):
        order = self.create()
        self.assertEqual(self.assign(order).data['code'], 0)
        self.auth(self.providers[0])
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                                         {'entry_image': screenshot()}, format='multipart').data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, 'GRABBED')
        self.auth(self.staff)
        response = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '客户取消'})
        self.assertEqual(response.data['code'], 0, response.data)
        self.auth(self.providers[1])
        response = self.client.post(f'/api/orders/orders/{order.pk}/start/',
                                    {'entry_image': screenshot()}, format='multipart')
        self.assertNotEqual(response.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class StaffTeamDispatchConcurrencyTest(TransactionTestCase):
    def test_shared_provider_last_capacity_is_claimed_by_at_most_one_team(self):
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        staff = make_console_user(['order:view', 'order:dispatch'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        users = [make_provider() for _ in range(3)]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        service = make_service(price=5001, billing_provider_count=2)
        orders = [make_order(boss, service=service, customer_account=boss_account, dispatch_route='STAFF',
                             amount=10002, auto_cancel_at=timezone.now() + timedelta(minutes=5)) for _ in range(2)]
        token, _ = issue_account_tokens(staff_account)
        first_checked, second_started, second_done, release = Event(), Event(), Event(), Event()
        from console.views import _capacity_reject_reason
        def observe_capacity(*args, **kwargs):
            result = _capacity_reject_reason(*args, **kwargs)
            if current_thread().name == 'team-first' and not first_checked.is_set():
                first_checked.set()
                if not release.wait(10):
                    raise AssertionError('First team lock was not released')
            return result
        def assign(order, ids):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                if current_thread().name == 'team-second':
                    second_started.set()
                return client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_ids': ids}, format='json').data
            finally:
                close_old_connections()
                if current_thread().name == 'team-second':
                    second_done.set()
        def run_as(name, order, ids):
            current_thread().name = name
            return assign(order, ids)
        with patch.object(Order, 'MAX_CONCURRENT_ORDERS', 1), patch('console.views.notify_order_update'), \
             patch('console.views._push_message_safe'), patch('console.views._capacity_reject_reason', side_effect=observe_capacity), \
             ThreadPoolExecutor(max_workers=2) as workers:
            one = workers.submit(run_as, 'team-first', orders[0], [accounts[0].pk, accounts[1].pk])
            try:
                self.assertTrue(first_checked.wait(10), 'First team did not reach the capacity check')
                two = workers.submit(run_as, 'team-second', orders[1], [accounts[0].pk, accounts[2].pk])
                self.assertTrue(second_started.wait(10), 'Second team did not start')
                self.assertFalse(second_done.wait(1), 'Second team bypassed the shared capacity lock')
            finally:
                release.set()
            results = [one.result(timeout=20), two.result(timeout=20)]
        self.assertEqual(sum(result['code'] == 0 for result in results), 1, results)
        self.assertEqual(Order.objects.filter(pk__in=[o.pk for o in orders], status='GRABBED').count(), 1)
        self.assertEqual(OrderProvider.objects.filter(order__in=orders, provider_account=accounts[0]).count(), 1)

    def test_refund_racing_team_assignment_never_loses_or_duplicates_funds(self):
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        make_wallet(boss, balance=100000)
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        users = [make_provider() for _ in range(2)]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        service = make_service(price=5001, billing_provider_count=2)
        boss_token, _ = issue_account_tokens(boss_account)
        staff_token, _ = issue_account_tokens(staff_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        with patch('orders.views._schedule_auto_cancel'), patch('orders.views.notify_order_update'):
            created = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'STAFF',
                                  'client_request_id': 'staff-refund-race'}, format='json').data
        self.assertEqual(created['code'], 0, created)
        order_id = created['data']['id']
        barrier = Barrier(2)
        def post(path, data):
            close_old_connections()
            try:
                worker = APIClient()
                worker.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
                barrier.wait(timeout=10)
                return worker.post(path, data, format='json').data
            finally:
                close_old_connections()
        with patch('console.views.notify_order_update'), patch('console.views._push_message_safe'), \
             ThreadPoolExecutor(max_workers=2) as workers:
            assigned = workers.submit(post, f'/api/admin/orders/{order_id}/assign/',
                                      {'provider_ids': [account.pk for account in accounts]})
            refunded = workers.submit(post, f'/api/admin/orders/{order_id}/refund/', {'reason': '客户取消'})
            assign_result, refund_result = assigned.result(timeout=20), refunded.result(timeout=20)
        self.assertEqual(refund_result['code'], 0, refund_result)
        self.assertIn(assign_result['code'], (0, 400))
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(user=boss).balance, 100000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
