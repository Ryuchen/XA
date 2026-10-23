"""用两个独立数据库连接争抢最后一个名额，SQLite 不具备本测试需要的行锁。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.services import count_active_orders
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class DispatchConcurrencyTest(TransactionTestCase):
    def setUp(self):
        self.provider = make_provider()
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        profile = self.provider.escort_profile
        profile.account = self.provider_account
        profile.save(update_fields=['account'])
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        make_wallet(self.boss, balance=100000)
        self.service = make_service()
        self.first_order = self.new_pending_order()
        self.provider_token, _ = issue_account_tokens(self.provider_account)
        self.boss_token, _ = issue_account_tokens(self.boss_account)
        admin = make_console_user(['order:dispatch', 'order:view'])
        admin_account = get_or_create_account_for_legacy_user(admin)
        membership = admin.admin_membership
        membership.account = admin_account
        membership.save(update_fields=['account'])
        self.admin_token, _ = issue_account_tokens(admin_account)

    def new_pending_order(self):
        order = make_order(self.boss, service=self.service, customer_account=self.boss_account)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        return order

    def race_against(self, second_path, second_data, second_token):
        first_counted = threading.Event()
        resume_first = threading.Event()
        second_done = threading.Event()
        calls = threading.local()

        def observe_count(*args, **kwargs):
            result = count_active_orders(*args, **kwargs)
            calls.number = getattr(calls, 'number', 0) + 1
            # 第一请求已读出剩余名额，暂停到第二请求尝试进入同一资源。
            if threading.current_thread().name.endswith('_0') and calls.number == 2:
                first_counted.set()
                if not resume_first.wait(5):
                    raise AssertionError('Concurrent request did not release first worker')
            return result

        def request(path, data, token, done=None):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                return client.post(path, data, format='json').data
            finally:
                connections.close_all()
                if done is not None:
                    done.set()

        with patch.object(Order, 'MAX_CONCURRENT_ORDERS', 1), patch(
            'orders.views.count_active_orders', side_effect=observe_count,
        ), ThreadPoolExecutor(max_workers=2, thread_name_prefix='capacity') as workers:
            first = workers.submit(request, f'/api/orders/orders/{self.first_order.pk}/grab/', {}, self.provider_token)
            try:
                self.assertTrue(first_counted.wait(5), 'First request never reached its transactional capacity check')
                second = workers.submit(request, second_path, second_data, second_token, second_done)
                # 未加资源锁时第二请求会成功提交；加锁后应等待第一请求退出事务。
                second_done.wait(1)
            finally:
                resume_first.set()
            responses = [first.result(timeout=10), second.result(timeout=10)]
        self.assertEqual(sum(item['code'] == 0 for item in responses), 1, responses)
        self.assertEqual(count_active_orders(self.provider_account), 1)
        return responses

    def test_two_different_orders_cannot_claim_the_last_provider_slot(self):
        second = self.new_pending_order()
        self.race_against(f'/api/orders/orders/{second.pk}/grab/', {}, self.provider_token)

    def test_boss_selected_order_and_grab_share_the_same_capacity_gate(self):
        self.race_against('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.provider_account.pk,
            'client_request_id': 'concurrent-boss-order',
        }, self.boss_token)
        # 抢单先占名额后，另一笔指定陪玩下单必须在扣款前失败。
        from wallet.models import Wallet
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)

    def test_admin_assign_and_grab_share_the_same_capacity_gate(self):
        second = self.new_pending_order()
        self.race_against(f'/api/admin/orders/{second.pk}/assign/', {
            'provider_id': self.provider_account.pk,
        }, self.admin_token)

    def test_staff_assign_and_grab_share_the_same_capacity_gate(self):
        second = self.new_pending_order()
        self.race_against(f'/api/orders/orders/{second.pk}/assign/', {
            'provider_id': self.provider_account.pk,
        }, self.admin_token)

    def test_admin_dispatch_and_grab_share_gate_before_debit(self):
        self.race_against('/api/admin/orders/dispatch/', {
            'customer_id': self.boss_account.pk, 'service_id': self.service.pk,
            'provider_id': self.provider_account.pk,
            'client_request_id': 'concurrent-dispatch',
        }, self.admin_token)
        from wallet.models import Wallet
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)

    def test_transfer_and_grab_share_the_same_capacity_gate(self):
        old_provider = make_provider()
        old_account = get_or_create_account_for_legacy_user(old_provider)
        order = make_order(
            self.boss, service=self.service, provider=old_provider,
            customer_account=self.boss_account, provider_account=old_account,
            status=Order.Status.IN_SERVICE, provider_income=8000, shop_income=2000,
            commission_rate=20,
        )
        self.race_against(f'/api/admin/orders/{order.pk}/transfer/', {'transfers': [{
            'old_provider_id': old_account.pk, 'new_provider_id': self.provider_account.pk,
            'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
        }]}, self.admin_token)
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, old_account.pk)
