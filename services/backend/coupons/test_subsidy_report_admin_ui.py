"""Report reviewers see platform-funded earnings through the real admin API."""
from datetime import timedelta
import os
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon
from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
from engagement.confirmation import process_order, refresh_report_clock
from engagement.models import VipTier
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, Wallet, get_platform_wallet


@skipUnless(os.environ.get('XA_SUBSIDY_REPORT_UI') == '1', 'Requires built admin UI')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SubsidyReportAdminUITest(LiveServerTestCase):
    setUp = CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(CouponAvailabilityWorkflowTest.client_for)
    ok = CouponAvailabilityWorkflowTest.ok

    def test_approved_report_displays_platform_funded_income(self):
        media = TemporaryDirectory(prefix='xa-subsidy-report-ui-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        service = make_service(name='报单安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier_type = make_boss_type(name='报单六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier_type, level=6, recharge_threshold=100, safe_box_service_ids=[service.pk])
        staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit', 'wallet:adjust'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        player = make_provider(display_name='报单验收陪玩')
        player_account = get_or_create_account_for_legacy_user(player)
        player.escort_profile.account = player_account
        player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        operator, provider = self.client_for(staff_account), self.client_for(player_account)
        other = make_user()
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(other, other_account)
        make_wallet(other, balance=0)
        orders = []
        for index, buyer in enumerate((self.account, other_account)):
            buyer.boss_type = tier_type
            buyer.save(update_fields=['boss_type'])
            grant_periodic(buyer)
            voucher = UserCoupon.objects.get(account=buyer, coupon__scope='SAFE_BOX')
            data = self.ok(self.client_for(buyer).post('/api/orders/create/', {'service_id': service.pk,
                'user_coupon_id': voucher.pk, 'dispatch_route': 'STAFF', 'client_request_id': f'report-ui-{index}'}, format='json'))
            self.ok(operator.post(f"/api/admin/orders/{data['id']}/assign/", {'provider_id': player_account.pk}, format='json'))
            self.ok(provider.post(f"/api/orders/orders/{data['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
            self.ok(provider.post(f"/api/orders/orders/{data['id']}/complete/", {'completion_image': screenshot()}, format='multipart'))
            report = ProviderReport.objects.get(order_id=data['id'], provider_account=player_account)
            self.ok(provider.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
            self.ok(provider.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
            self.ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
            old = timezone.now() - timedelta(hours=73)
            Order.objects.filter(pk=data['id']).update(completed_at=old)
            ProviderReport.objects.filter(pk=report.pk).update(submitted_at=old)
            refresh_report_clock(Order.objects.get(pk=data['id']))
            platform = get_platform_wallet()
            platform.balance = 7999 if index == 0 else 8000
            platform.save(update_fields=['balance'])
            self.assertEqual(bool(process_order(data['id'])), index == 1)
            snapshot = self.ok(operator.get(f'/api/admin/reports/{report.pk}/'))
            self.assertEqual((snapshot['payout_amount'], snapshot['expected_income'], snapshot['platform_subsidy_income']), (0, 8000, 8000))
            orders.append({'reportId': report.pk, 'orderNo': data['order_no'], 'settled': index == 1})
        if os.environ.get('XA_SUBSIDY_REPORT_REALTIME') == '1':
            self.ok(operator.post('/api/admin/wallets/platform-topup/',
                {'amount': 8000, 'reference': 'report-realtime-001', 'client_request_id': 'report-realtime-001'}, format='json'))
            from engagement.tasks import confirm_due_consumption
            self.assertEqual(confirm_due_consumption.run(), 1)
            settled = self.ok(operator.get(f"/api/admin/reports/{orders[0]['reportId']}/"))
            self.assertEqual(settled['payout_amount'], 8000)
            self.assertEqual(Wallet.objects.get(account=player_account).balance, 16000)


del CouponAvailabilityWorkflowTest
