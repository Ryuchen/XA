"""New boss accounts can use vouchers without creating legacy users."""
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from coupons.models import Coupon, UserCoupon
from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AccountOnlyCouponWorkflowTest(APITransactionTestCase):
    client_for = staticmethod(CouponAvailabilityWorkflowTest.client_for)
    ok = CouponAvailabilityWorkflowTest.ok
    run_negotiation = CouponAvailabilityWorkflowTest.test_free_safe_box_negotiated_termination_recovers_and_retries

    def setUp(self):
        self.account = ClubAccount.objects.create_account(username='voucher-account-only', account_type='BOSS')
        wallet = get_wallet(account=self.account)
        wallet.balance = 50000
        wallet.save(update_fields=['balance'])
        self.boss = self.client_for(self.account)

    def test_public_coupon_claim_and_vip_termination_replenishment(self):
        public = Coupon.objects.create(name='账号领券', discount_type='DIRECT', amount=100,
                                       is_active=True, is_public=True, total_qty=10)
        self.ok(self.boss.post(f'/api/coupons/{public.pk}/claim/', {}, format='json'))
        claimed = UserCoupon.objects.get(account=self.account, coupon=public)
        self.assertIsNone(claimed.user_id)
        self.assertEqual(sum(row['id'] == claimed.pk for row in self.ok(self.boss.get('/api/coupons/mine/'))), 1)
        self.run_negotiation()
        self.assertFalse(LegacyAccountMap.objects.filter(account=self.account).exists())
        vouchers = UserCoupon.objects.filter(account=self.account, coupon__scope='SAFE_BOX')
        self.assertEqual(vouchers.filter(status='USED').count(), 1)
        self.assertEqual(vouchers.filter(status='UNUSED').count(), 1)
        self.assertFalse(vouchers.exclude(user__isnull=True).exists())


del CouponAvailabilityWorkflowTest
