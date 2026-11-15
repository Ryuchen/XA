"""Actual admin and provider pages against an isolated live business flow."""
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon
from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
from engagement.models import VipTier
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
from wallet.models import Wallet, get_platform_wallet


@skipUnless(os.environ.get('XA_SUBSIDY_UI_TEST') == '1', 'Requires built admin and provider pages')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SubsidyRealUITest(LiveServerTestCase):
    setUp = CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(CouponAvailabilityWorkflowTest.client_for)
    ok = CouponAvailabilityWorkflowTest.ok

    def test_admin_confirms_and_provider_sees_negotiated_income(self):
        media = TemporaryDirectory(prefix='xa-subsidy-ui-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        service = make_service(name='界面安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='界面六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'wallet:view', 'wallet:adjust', 'transaction:view', 'dashboard:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        player = make_provider(display_name='界面陪玩')
        account = get_or_create_account_for_legacy_user(player)
        player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        data = self.ok(self.boss.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'vip-ui-termination'}, format='json'))
        self.ok(self.client_for(staff_account).post(f"/api/admin/orders/{data['id']}/assign/", {'provider_id': account.pk}, format='json'))
        self.ok(self.client_for(account).post(f"/api/orders/orders/{data['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        platform = get_platform_wallet(); platform.balance = 7999; platform.save(update_fields=['balance'])
        repo = Path(settings.BASE_DIR).parent.parent
        native_request = subprocess.run(['node', str(repo / 'scripts/native-subsidy-aftersale-real.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': data['id'], 'bossId': self.account.pk,
                              'token': issue_account_tokens(self.account)[0]}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=30)
        self.assertEqual(native_request.returncode, 0, native_request.stdout + native_request.stderr)
        self.assertEqual(Order.objects.get(pk=data['id']).aftersales.get().status, 'OPEN')
        result = subprocess.run(['node', str(repo / 'scripts/admin-subsidy-termination-real.cjs')],
            input=json.dumps({'base': self.live_server_url, 'adminRoot': os.environ['XA_ADMIN_VUE_ROOT'],
                'providerRoot': os.environ['XA_PROVIDER_VUE_ROOT'], 'orderId': data['id'], 'orderNo': data['order_no'],
                'staffToken': issue_account_tokens(staff_account)[0], 'providerToken': issue_account_tokens(account)[0],
                'providerId': account.pk}), cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(pk=data['id']); voucher.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, voucher.status), ('CANCELLED', 'REFUNDED', 'USED'))
        self.assertEqual(order.aftersales.get().status, 'RESOLVED')
        self.assertIsNone(order.settled_at)
        wallet = Wallet.objects.filter(account=account).first()
        self.assertTrue(wallet is None or wallet.balance == 0)
        from wallet.models import Transaction
        self.assertTrue(Transaction.objects.filter(wallet__user__username=settings.PLATFORM_SYSTEM_USERNAME,
            external_tx_id='ui-offline-001', tx_type='ADJUST', amount=1).exists())
        from django.core.serializers.json import DjangoJSONEncoder
        def check_native_replenishment(expected_status, expected_text):
            boss_order = self.ok(self.boss.get(f"/api/orders/orders/{order.pk}/"))
            record = boss_order['refund_summary']['records'][0]
            self.assertEqual(record['safety_box_replenishment'], expected_status)
            coupon = record['coupon_results'][0]
            self.assertEqual(coupon['outcome'], 'NOT_RETURNED')
            native = subprocess.run(['node', str(repo / 'scripts/native-coupon-refund-live.cjs')],
                input=json.dumps({'order': boss_order, 'name': coupon['name'], 'outcome': 'NOT_RETURNED',
                    'zeroPayment': True, 'replenishmentText': expected_text}, cls=DjangoJSONEncoder),
                cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=15)
            self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
            page = subprocess.run(['node', str(repo / 'scripts/native-subsidy-aftersale-real.cjs')],
                input=json.dumps({'base': self.live_server_url, 'orderId': order.pk, 'bossId': self.account.pk,
                    'token': issue_account_tokens(self.account)[0], 'expectedReplenishment': expected_text}),
                cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=30)
            self.assertEqual(page.returncode, 0, page.stdout + page.stderr)
        check_native_replenishment('WAITING_SETTLEMENT', '报酬结算后，按会员权益发放下一张券')
        from engagement.tasks import retry_completion_subsidies
        self.assertEqual(retry_completion_subsidies.run(), 1)
        self.assertEqual(Wallet.objects.get(account=account).balance, 8000)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'USED')
        self.assertEqual(UserCoupon.objects.filter(account=self.account, coupon__scope='SAFE_BOX', status='UNUSED').count(), 1)
        check_native_replenishment('SETTLED', '报酬已结算，会员权益可在我的券查看')


del CouponAvailabilityWorkflowTest
