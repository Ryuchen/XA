"""A claimed voucher follows actual admin availability across customer flows."""
from uuid import uuid4
from datetime import timedelta
import json
from pathlib import Path
import shutil
import subprocess

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import UserCoupon
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class CouponAvailabilityWorkflowTest(APITransactionTestCase):
    def setUp(self):
        boss = make_user()
        staff = make_console_user(['coupon:view', 'coupon:edit'])
        self.account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, self.account)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.wallet = make_wallet(boss, balance=50000)
        self.service = make_service(price=10000)
        self.boss = self.client_for(self.account)
        self.staff = self.client_for(staff_account)

    @staticmethod
    def client_for(account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def test_admin_disables_claimed_coupon_then_reenables_and_boss_uses_it(self):
        created = self.staff.post('/api/admin/coupons/', {
            'name': '业务验收券', 'discount_type': 'DIRECT', 'amount': 1000,
            'threshold': 0, 'is_active': True,
        }, format='json')
        self.assertIn(created.status_code, (200, 201), created.data)
        self.assertEqual(created.data['code'], 0, created.data)
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)

        def mine():
            return next(row for row in self.ok(self.boss.get('/api/coupons/mine/')) if row['id'] == voucher.pk)

        self.assertEqual((mine()['status'], mine()['availability_status']), ('UNUSED', 'UNUSED'))
        voucher.expires_at = timezone.now() - timedelta(seconds=1)
        voucher.save(update_fields=['expires_at'])
        self.assertEqual(mine()['availability_status'], 'EXPIRED')
        voucher.expires_at = None
        voucher.save(update_fields=['expires_at'])
        self.ok(self.staff.patch(f'/api/admin/coupons/{coupon_id}/', {'is_active': False}, format='json'))
        self.assertEqual((mine()['status'], mine()['availability_status']), ('UNUSED', 'DISABLED'))
        self.assertNotIn(voucher.pk, {row['id'] for row in self.ok(self.boss.get('/api/coupons/mine/', {'usable': 1}))})
        quote = self.boss.post('/api/orders/quote/', {'service_id': self.service.pk, 'user_coupon_id': voucher.pk}, format='json')
        self.assertNotEqual(quote.data.get('code', 400), 0, quote.data)
        self.assertIn('优惠券', str(quote.data))

        self.ok(self.staff.patch(f'/api/admin/coupons/{coupon_id}/', {'is_active': True}, format='json'))
        self.assertEqual(mine()['availability_status'], 'UNUSED')
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
        }, format='json'))
        self.assertEqual((order['coupon_discount'], order['amount']), (1000, 9000))
        self.assertEqual((mine()['status'], mine()['availability_status']), ('USED', 'USED'))
        self.ok(self.staff.patch(f'/api/admin/coupons/{coupon_id}/', {'is_active': False}, format='json'))
        self.assertEqual(mine()['availability_status'], 'USED')

    def test_claimed_discount_is_returned_after_cancellation_and_can_be_reused(self):
        from wallet.models import Transaction
        created = self.staff.post('/api/admin/coupons/', {
            'name': '真实退款返券', 'discount_type': 'DIRECT', 'amount': 1000,
            'threshold': 0, 'is_active': True, 'valid_to': (timezone.now() + timedelta(days=1)).isoformat(),
        }, format='json')
        self.assertIn(created.status_code, (200, 201), created.data)
        self.assertEqual(created.data['code'], 0, created.data)
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        before_deadline = voucher.valid_to

        def create_order():
            return self.ok(self.boss.post('/api/orders/create/', {
                'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
                'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
            }, format='json'))

        first = create_order()
        self.assertEqual((first['amount'], first['coupon_discount']), (9000, 1000))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 41000)
        self.assertEqual(next(row for row in self.ok(self.boss.get('/api/coupons/mine/'))
                              if row['id'] == voucher.pk)['availability_status'], 'USED')
        cancelled = self.ok(self.boss.post(f"/api/orders/orders/{first['id']}/cancel/", {'reason': '本地取消'}, format='json'))
        self.assertEqual(cancelled['refund_summary']['records'][0]['coupon_results'],
                         [{'name': '真实退款返券', 'outcome': 'RETURNED'}])
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id, voucher.valid_to), ('UNUSED', None, before_deadline))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 50000)
        mine = next(row for row in self.ok(self.boss.get('/api/coupons/mine/')) if row['id'] == voucher.pk)
        self.assertEqual(mine['availability_status'], 'UNUSED')
        second = create_order()
        self.assertEqual((second['amount'], second['coupon_discount']), (9000, 1000))
        self.assertNotEqual(first['id'], second['id'])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 41000)
        self.assertEqual(list(Transaction.objects.filter(order_id=first['id'], tx_type='PAY').values_list('amount', flat=True)), [-9000])
        self.assertEqual(list(Transaction.objects.filter(order_id=first['id'], tx_type='REFUND').values_list('amount', flat=True)), [9000])
        self.assertEqual(list(Transaction.objects.filter(order_id=second['id'], tx_type='PAY').values_list('amount', flat=True)), [-9000])
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id), ('USED', second['id']))
        # The previous refund remains a historical fact after the same coupon is used again.
        previous = self.ok(self.boss.get(f"/api/orders/orders/{first['id']}/"))
        self.assertEqual(previous['refund_summary']['records'][0]['coupon_results'][0]['outcome'], 'RETURNED')

    def test_returned_coupon_reports_expired_if_template_expires_during_order(self):
        created = self.staff.post('/api/admin/coupons/', {
            'name': '有效期返券', 'discount_type': 'DIRECT', 'amount': 1000,
            'threshold': 0, 'is_active': True, 'valid_to': (timezone.now() + timedelta(days=1)).isoformat(),
        }, format='json')
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
        }, format='json'))
        self.ok(self.staff.patch(f'/api/admin/coupons/{coupon_id}/',
                                 {'valid_to': (timezone.now() - timedelta(seconds=1)).isoformat()}, format='json'))
        cancelled = self.ok(self.boss.post(f"/api/orders/orders/{order['id']}/cancel/", {}, format='json'))
        self.assertEqual(cancelled['refund_summary']['records'][0]['coupon_results'][0]['outcome'], 'RETURNED')
        mine = next(row for row in self.ok(self.boss.get('/api/coupons/mine/')) if row['id'] == voucher.pk)
        self.assertEqual((mine['status'], mine['availability_status']), ('UNUSED', 'EXPIRED'))

    def test_zero_amount_discount_order_can_be_cancelled(self):
        from wallet.models import Transaction, Wallet
        created = self.staff.post('/api/admin/coupons/', {
            'name': '全额优惠券', 'discount_type': 'DIRECT', 'amount': 10000,
            'threshold': 0, 'is_active': True,
        }, format='json')
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
        }, format='json'))
        self.assertEqual(order['amount'], 0)
        cancelled = self.ok(self.boss.post(f"/api/orders/orders/{order['id']}/cancel/", {}, format='json'))
        self.assertEqual(cancelled['status'], 'CANCELLED')
        self.assertEqual(cancelled['refund_summary']['records'][0]['coupon_results'],
                         [{'name': '全额优惠券', 'outcome': 'RETURNED'}])
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id), ('UNUSED', None))
        self.assertEqual(Wallet.objects.get(account=self.account).balance, 50000)
        self.assertFalse(Transaction.objects.filter(order_id=order['id'], tx_type='REFUND').exists())
        # Check native presentation using the actual cancellation response.
        from django.core.serializers.json import DjangoJSONEncoder
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node runtime required for native presentation')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-coupon-refund-live.cjs')],
            input=json.dumps({'order': cancelled, 'name': '全额优惠券', 'outcome': 'RETURNED', 'zeroPayment': True}, cls=DjangoJSONEncoder),
            text=True, encoding='utf-8', capture_output=True, timeout=15, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_zero_amount_coupon_timeout_returns_coupon_without_wallet_refund(self):
        from orders.models import Order
        from orders.tasks import auto_cancel_pending_order
        from site_messages.models import Message
        from wallet.models import Transaction
        created = self.staff.post('/api/admin/coupons/', {
            'name': '超时全额券', 'discount_type': 'DIRECT', 'amount': 10000,
            'threshold': 0, 'is_active': True,
        }, format='json')
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        data = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'PUBLIC',
        }, format='json'))
        Order.objects.filter(pk=data['id']).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(auto_cancel_pending_order.run(data['id']), 'cancelled')
        order = Order.objects.get(pk=data['id'])
        voucher.refresh_from_db()
        self.assertEqual((order.status, voucher.status, voucher.order_id), ('CANCELLED', 'UNUSED', None))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())
        notice = Message.objects.get(related_order_id=order.pk, title='订单超时已取消')
        self.assertNotIn('退回兴安币余额', notice.preview)
        self.assertEqual(auto_cancel_pending_order.run(data['id']), 'skip:status=CANCELLED')

    def test_staff_cancels_zero_payment_order_and_returns_coupon(self):
        from wallet.models import Transaction
        staff = make_console_user(['order:view', 'order:cancel'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        created = self.staff.post('/api/admin/coupons/', {
            'name': '客服取消全额券', 'discount_type': 'DIRECT', 'amount': 10000,
            'threshold': 0, 'is_active': True,
        }, format='json')
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
        }, format='json'))
        self.ok(self.client_for(staff_account).post(f"/api/admin/orders/{order['id']}/cancel/",
                                                   {'reason': '测试取消'}, format='json'))
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id), ('UNUSED', None))
        self.assertFalse(Transaction.objects.filter(order_id=order['id'], tx_type='REFUND').exists())


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class CouponAvailabilityNativeHTTPTest(LiveServerTestCase):
    def test_admin_disable_refreshes_native_coupon_page(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page verification')
        boss, staff = make_user(), make_console_user(['coupon:view', 'coupon:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        from coupons.models import Coupon
        coupon = Coupon.objects.create(name='本地可用性券', discount_type='DIRECT', amount=1000)
        client = APIClient()
        token, refresh = issue_account_tokens(account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.post(f'/api/coupons/{coupon.pk}/claim/')
        self.assertEqual(response.data['code'], 0, response.data)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        admin_token, _ = issue_account_tokens(staff_account)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-coupon-live-driver.cjs')],
            input=json.dumps({'base': self.live_server_url, 'couponId': coupon.pk, 'voucherId': voucher.pk,
                'adminToken': admin_token, 'session': {'token': token, 'refresh': refresh,
                    'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '本地老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('real Django HTTP', result.stdout)
