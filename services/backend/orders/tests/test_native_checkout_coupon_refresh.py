"""Real admin coupon change while the boss checkout is in the background."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeCheckoutCouponRefreshTest(LiveServerTestCase):
    def _zero_payable_breakdown_change(self, race):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss_type = make_boss_type(discount_rate=100)
        boss = make_user(boss_type=boss_type)
        staff = make_console_user(['boss_type:view', 'boss_type:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        coupon = Coupon.objects.create(name='隔离全额券', discount_type='DIRECT', amount=20000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        admin_token, _ = issue_account_tokens(admin)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-zero-breakdown-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'voucherId': voucher.pk,
                'bossTypeId': boss_type.pk, 'adminToken': admin_token, 'race': race,
                'session': {'token': token, 'refresh': refresh,
                            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '隔离老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.original_amount, order.boss_discount, order.coupon_discount), (0, 10000, 1000, 9000))
        wallet.refresh_from_db(); voucher.refresh_from_db()
        self.assertEqual((wallet.balance, voucher.status), (50000, 'USED'))

    def test_zero_payable_breakdown_change_requires_review(self):
        self._zero_payable_breakdown_change(False)

    def test_zero_payable_breakdown_change_during_confirmation_requires_review(self):
        self._zero_payable_breakdown_change(True)

    def test_zero_coupon_confirms_order_and_creates_real_zero_payment_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        wallet = make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        coupon = Coupon.objects.create(name='全额优惠券', discount_type='DIRECT', amount=10000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-coupon-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'voucherId': voucher.pk,
                'zero': True, 'session': {'token': token, 'refresh': refresh,
                                         'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '测试老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.coupon_discount), (0, 10000))
        wallet.refresh_from_db(); voucher.refresh_from_db()
        self.assertEqual((wallet.balance, voucher.status), (50000, 'USED'))

    def test_disabled_coupon_is_removed_and_checkout_uses_updated_price(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss, staff = make_user(), make_console_user(['coupon:view', 'coupon:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        coupon = Coupon.objects.create(name='测试优惠券', discount_type='DIRECT', amount=1000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        admin_token, _ = issue_account_tokens(admin)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-coupon-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'couponId': coupon.pk, 'voucherId': voucher.pk,
                'serviceId': service.pk, 'adminToken': admin_token,
                'session': {'token': token, 'refresh': refresh,
                            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '测试老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.coupon_discount, order.game_region, order.game_nickname),
                         (10000, 0, '本地大区', '本地昵称'))
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'UNUSED')

    def _zero_payable_price_change(self, race):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss, staff = make_user(), make_console_user(['service:view', 'service:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        coupon = Coupon.objects.create(name='隔离全额券', discount_type='DIRECT', amount=20000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        admin_token, _ = issue_account_tokens(admin)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-zero-price-change-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'voucherId': voucher.pk,
                'adminToken': admin_token, 'race': race,
                'session': {'token': token, 'refresh': refresh,
                            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '隔离老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.original_amount, order.coupon_discount), (0, 12000, 12000))
        wallet.refresh_from_db(); voucher.refresh_from_db()
        self.assertEqual((wallet.balance, voucher.status), (50000, 'USED'))

    def test_zero_payable_price_change_requires_review(self):
        self._zero_payable_price_change(False)

    def test_zero_payable_price_change_during_confirmation_requires_review(self):
        self._zero_payable_price_change(True)
