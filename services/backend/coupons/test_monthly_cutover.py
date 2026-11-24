import os
from datetime import datetime
from unittest import skipUnless
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from coupons.benefits import grant_periodic, restore_order_coupons
from coupons.models import UserCoupon, VipMonthlyCouponGrant, VipMonthlyCutover
from coupons.monthly import issue_monthly_coupons
from engagement.models import VipTier
from orders.models import Order
from orders.purchase_benefits import record_paid_benefits
from orders.tests.factories import make_boss_type, make_service, make_user


@override_settings(VIP_MONTHLY_DECISIONS_ENABLED=True)
class MonthlyCutoverTest(TestCase):
    zone = ZoneInfo('Asia/Shanghai')

    def setUp(self):
        self.user = make_user(); self.account = get_or_create_account_for_legacy_user(self.user)
        self.tier = VipTier.objects.create(level=2, boss_type=make_boss_type(discount_rate=100),
            recharge_threshold=200, monthly_coupon_count=2, monthly_discount=80)
        self.account.boss_type = self.tier.boss_type; self.account.save(update_fields=['boss_type'])
        VipMonthlyCutover.objects.create(activated_at=self.when(11, 7).replace(hour=0),
                                         platform_actor_id='test', operation_id='test-cutover')

    def when(self, month, day=8):
        return datetime(2026, month, day, 12, tzinfo=self.zone)

    def existing_account(self):
        ClubAccount.objects.filter(pk=self.account.pk).update(created_at=self.when(11, 1))
        self.account.refresh_from_db()

    def rows(self):
        return UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-month:')

    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                'VIP 月券切换语义随首版定价接线变化（18-盘点 VIP 月券及券条款保护待最终联调）；断言保留')
    def test_historic_unknown_is_recorded_as_zero_then_new_month_issues(self):
        self.existing_account()
        with patch('django.utils.timezone.now', return_value=self.when(11)):
            grant_periodic(self.account); grant_periodic(self.account)
        grant = VipMonthlyCouponGrant.objects.get(account=self.account)
        self.assertEqual((grant.source, grant.issued_count, grant.terms_frozen, self.rows().count()), ('LEGACY', 0, True, 0))
        with patch('django.utils.timezone.now', return_value=self.when(12, 1)):
            grant_periodic(self.account)
        self.assertEqual((VipMonthlyCouponGrant.objects.count(), self.rows().count()), (2, 2))

    def test_new_account_gets_current_month_and_repeat_does_not_top_up(self):
        ClubAccount.objects.filter(pk=self.account.pk).update(created_at=self.when(11))
        self.account.refresh_from_db()
        with patch('django.utils.timezone.now', return_value=self.when(11)):
            grant_periodic(self.account)
            self.tier.monthly_coupon_count = 3; self.tier.save(update_fields=['monthly_coupon_count'])
            grant_periodic(self.account)
        grant = VipMonthlyCouponGrant.objects.get(account=self.account)
        self.assertEqual((grant.source, grant.issued_count, self.rows().count()), ('SYSTEM', 2, 2))

    def test_existing_coupons_adopted_without_changing_terms(self):
        from coupons.benefits import issue
        self.existing_account()
        prior = issue(self.account, f'vip-month:{self.account.pk}:1:2026-11:0', name='旧月券', rate=90)[0]
        with patch('django.utils.timezone.now', return_value=self.when(11)):
            grant_periodic(self.account)
        prior.refresh_from_db(); prior.coupon.refresh_from_db()
        self.assertEqual((self.rows().count(), prior.coupon.discount_rate, prior.vip_terms_locked_by_downgrade), (1, 90, True))

    def test_new_business_cycle_after_cutover_is_not_an_old_zero_decision(self):
        from engagement.models import RetirementAudit
        from orders.tests.factories import make_console_user
        self.existing_account()
        operator = get_or_create_account_for_legacy_user(make_console_user())
        audit = RetirementAudit.objects.create(account=self.account, business_cycle=1, operator=operator, reason='隔离退店')
        RetirementAudit.objects.filter(pk=audit.pk).update(created_at=self.when(11))
        ClubAccount.objects.filter(pk=self.account.pk).update(business_cycle=2)
        self.account.refresh_from_db()
        with patch('django.utils.timezone.now', return_value=self.when(11)):
            grant_periodic(self.account)
        grant = VipMonthlyCouponGrant.objects.get(account=self.account)
        self.assertEqual((grant.business_cycle, grant.source, grant.issued_count), (2, 'SYSTEM', 2))

    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                'VIP 月券切换语义随首版定价接线变化（18-盘点 VIP 月券及券条款保护待最终联调）；断言保留')
    def test_missing_prior_cycle_evidence_fails_closed(self):
        from rest_framework.exceptions import ValidationError
        self.existing_account()
        ClubAccount.objects.filter(pk=self.account.pk).update(business_cycle=2)
        self.account.refresh_from_db()
        with patch('django.utils.timezone.now', return_value=self.when(11)), self.assertRaises(ValidationError):
            grant_periodic(self.account)
        self.assertFalse(VipMonthlyCouponGrant.objects.exists())
        with patch('django.utils.timezone.now', return_value=self.when(12, 1)):
            grant_periodic(self.account)
        self.assertEqual(VipMonthlyCouponGrant.objects.get(account=self.account).issued_count, 2)

    def test_missing_cutover_fails_closed_without_grant(self):
        from rest_framework.exceptions import ValidationError
        from coupons.monthly_cutover import grant_current_month
        VipMonthlyCutover.objects.all().delete()
        with self.assertRaises(ValidationError):
            grant_current_month(self.account, self.tier)
        self.assertFalse(VipMonthlyCouponGrant.objects.exists())
        self.assertFalse(self.rows().exists())

    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                'VIP 月券切换语义随首版定价接线变化（18-盘点 VIP 月券及券条款保护待最终联调）；断言保留')
    def test_enabled_code_without_store_cutover_keeps_legacy_until_activation(self):
        VipMonthlyCutover.objects.all().delete()
        with patch('django.utils.timezone.now', return_value=self.when(11)):
            grant_periodic(self.account)
        self.assertFalse(VipMonthlyCouponGrant.objects.exists())
        self.assertEqual(self.rows().count(), 1)

    @override_settings(VIP_MONTHLY_DECISIONS_ENABLED=False)
    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                'VIP 月券切换语义随首版定价接线变化（18-盘点 VIP 月券及券条款保护待最终联调）；断言保留')
    def test_existing_marker_blocks_reverting_to_legacy_issuance(self):
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            grant_periodic(self.account)
        self.assertFalse(VipMonthlyCouponGrant.objects.exists())
        self.assertFalse(self.rows().exists())

    def test_used_then_refunded_monthly_coupon_retains_usage_terms(self):
        issued_at = self.when(11)
        grant = issue_monthly_coupons(self.account, self.tier.pk, now=issued_at, allow_new_period=True)
        voucher = self.rows().select_related('coupon').first()
        service = make_service(price=10000)
        order = Order.objects.create(customer=self.user, customer_account=self.account, service=service, amount=8000)
        record_paid_benefits(order, voucher)
        self.tier.monthly_discount = 90; self.tier.save(update_fields=['monthly_discount'])
        issue_monthly_coupons(self.account, self.tier.pk, now=issued_at)
        restore_order_coupons(order)
        issue_monthly_coupons(self.account, self.tier.pk, now=issued_at)
        voucher.refresh_from_db(); voucher.coupon.refresh_from_db(); grant.refresh_from_db()
        self.assertEqual((voucher.status, voucher.coupon.discount_rate, voucher.vip_terms_locked_by_downgrade), ('UNUSED', 80, True))


@override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                   SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                   SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SEGMENT_REPORT_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True)
class MonthlyCouponRealPaymentTest(TestCase):
    def test_payment_admin_edit_and_actual_full_refund_keep_used_terms(self):
        from console.grab_config import save_grab_window
        from engagement.models import ClubOperationConfig
        save_grab_window(30, 0, lambda *args: None)
        ClubOperationConfig.objects.get_or_create(pk=1)
        from django.utils import timezone
        from rest_framework.test import APIClient
        from club_accounts.tokens import issue_account_tokens
        from engagement.models import PointFundingLot
        from orders.tests.factories import make_console_user
        from wallet.services import get_wallet

        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        tier = VipTier.objects.create(level=2, boss_type=make_boss_type(discount_rate=100),
            recharge_threshold=200, monthly_coupon_count=1, monthly_discount=80)
        account.boss_type = tier.boss_type; account.save(update_fields=['boss_type'])
        wallet = get_wallet(account=account, user=user); wallet.balance = 20000; wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=account, source_key='monthly-refund-real', kind='PREPAID', remaining=20000)
        issue_monthly_coupons(account, tier.pk, now=timezone.now(), allow_new_period=True)
        voucher = UserCoupon.objects.get(account=account, coupon__grant_key__startswith='vip-month:')
        service = make_service(price=10000)
        staff = make_console_user(['order:view', 'order:refund', 'coupon:view', 'coupon:edit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        options = boss.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json').data['data']['options']
        selected = next(row for row in options if row['coupon_id'] == voucher.pk)
        result = boss.post('/api/orders/create/', {'service_id': service.pk, 'discount_selection_token': selected['selection_token'],
            'expected_amount': 8000, 'client_request_id': 'monthly-vip-payment'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.vip_terms_locked_by_downgrade), ('USED', True))
        edit = admin.patch(f'/api/admin/coupons/{voucher.coupon_id}/', {'discount_rate': 90}, format='json')
        self.assertNotEqual(edit.data.get('code', 400), 0)
        tier.monthly_discount = 90; tier.save(update_fields=['monthly_discount'])
        issue_monthly_coupons(account, tier.pk, now=timezone.now())
        preview = admin.get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refund = admin.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '隔离月券全额退款',
            'plan_token': preview.data['data']['plan_token'], 'client_request_id': 'monthly-vip-refund'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        issue_monthly_coupons(account, tier.pk, now=timezone.now())
        voucher.refresh_from_db(); voucher.coupon.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual((voucher.status, voucher.coupon.discount_rate, wallet.balance), ('UNUSED', 80, 20000))
