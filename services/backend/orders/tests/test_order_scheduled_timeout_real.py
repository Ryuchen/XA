"""Real Redis/Celery ETA: an unassigned zero-price order expires and returns its coupon."""
import os
import subprocess
import sys
import time
from unittest import skipUnless

from django.conf import settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import UserCoupon
from orders.models import Order, OrderTimeoutConfig
from orders.tests.factories import make_console_user, make_coupon, make_service, make_user, make_wallet


@skipUnless(os.environ.get('XA_ORDER_ETA_TEST') == '1', 'Requires an isolated Redis and Celery worker')
class OrderScheduledTimeoutTest(APITransactionTestCase):
    def test_zero_order_expires_at_real_deadline_and_returns_coupon(self):
        self.assertEqual(settings.DATABASES['default']['NAME'], os.environ['XA_ASGI_NETWORK_TEST_DB'])
        OrderTimeoutConfig.objects.update_or_create(pk=1, defaults={'minutes': 1})
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        wallet = make_wallet(boss, balance=50000)
        service = make_service(price=20000, billing_provider_count=2)
        coupon = make_coupon(discount_type='DIRECT', threshold=0, amount=40000)
        staff = make_console_user(['order:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        boss_client, admin_client = APIClient(), APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        admin_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        claim = boss_client.post(f'/api/coupons/{coupon.pk}/claim/')
        self.assertEqual(claim.data['code'], 0, claim.data)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        worker = subprocess.Popen([sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            response = boss_client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'STAFF',
                'user_coupon_id': voucher.pk, 'client_request_id': 'scheduled-zero-order'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            order_id = response.data['data']['id']
            order = Order.objects.get(pk=order_id)
            self.assertEqual((order.status, order.amount, order.pending_timeout_minutes), ('PENDING', 0, 1))
            limit = time.monotonic() + 85
            while time.monotonic() < limit:
                order.refresh_from_db()
                if order.status == 'CANCELLED':
                    break
                self.assertIsNone(worker.poll(), 'Celery worker stopped before the deadline')
                time.sleep(1)
            self.assertEqual(order.status, 'CANCELLED', 'Scheduled task did not cancel the order')
            voucher.refresh_from_db(); wallet.refresh_from_db()
            self.assertEqual((voucher.status, voucher.order_id, wallet.balance), ('UNUSED', None, 50000))
            boss_detail = boss_client.get(f'/api/orders/orders/{order_id}/')
            admin_detail = admin_client.get(f'/api/admin/orders/{order_id}/')
            self.assertEqual(boss_detail.data['code'], 0, boss_detail.data)
            self.assertEqual(admin_detail.data['code'], 0, admin_detail.data)
            self.assertEqual(boss_detail.data['data']['status'], 'CANCELLED')
            self.assertEqual(admin_detail.data['data']['status'], 'CANCELLED')
            messages = boss_client.get('/api/messages/', {'type': 'ORDER'}).data['data']['messages']
            self.assertTrue(any(row['related_order_id'] == order_id and row['title'] == '订单超时已取消' for row in messages))
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill(); worker.wait(timeout=10)
