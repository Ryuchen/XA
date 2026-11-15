from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import PointFundingLot, VipTier
from engagement.services import config
from orders.models import Order, OrderPlatformSubsidy
from orders.tests.factories import make_boss_type, make_service, make_user
from wallet.services import get_wallet


@override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=True, VIP_SAFE_BOX_SUBSIDY_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class VipDiscountRepricingTest(APITestCase):
    def setUp(self):
        user = make_user(); self.account = get_or_create_account_for_legacy_user(user)
        self.wallet = get_wallet(account=self.account, user=user); self.wallet.balance = 20000; self.wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=self.account, source_key='vip-repricing-principal', kind='PREPAID', remaining=20000)
        self.old = make_service(name='原安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        self.new = make_service(name='新安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        self.tier = VipTier.objects.create(level=6, boss_type=make_boss_type(discount_rate=100), recharge_threshold=1,
            monthly_coupon_count=1, monthly_discount=80, season_discount=0, safe_box_service_ids=[self.old.pk])
        self.account.boss_type = self.tier.boss_type; self.account.save(update_fields=['boss_type'])
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now() - timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='repricing-cutover')
        grant_periodic(self.account)
        self.safe = UserCoupon.objects.select_related('coupon').get(account=self.account, coupon__scope='SAFE_BOX')
        self.monthly = UserCoupon.objects.select_related('coupon').get(account=self.account, coupon__grant_key__startswith='vip-month:')
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])

    def options(self, service):
        response = self.client.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']['options']

    def create(self, service, option):
        return self.client.post('/api/orders/create/', {'service_id': service.pk,
            'discount_selection_token': option['selection_token'], 'expected_amount': option['amount'],
            'client_request_id': 'vip-repricing-payment'}, format='json')

    def test_new_terms_visible_without_changing_coupon_then_paid_with_snapshot(self):
        self.tier.monthly_discount = 90; self.tier.season_discount = 50
        self.tier.safe_box_service_ids = [self.new.pk]
        self.tier.save(update_fields=['monthly_discount', 'season_discount', 'safe_box_service_ids'])
        old = self.options(self.old)
        self.assertFalse(any(row['coupon_id'] == self.safe.pk for row in old))
        options = self.options(self.new)
        safe = next(row for row in options if row['coupon_id'] == self.safe.pk)
        monthly = next(row for row in options if row['coupon_id'] == self.monthly.pk)
        self.assertEqual((safe['amount'], monthly['amount']), (5000, 9000))
        self.safe.coupon.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual((self.safe.coupon.discount_rate, self.safe.coupon.service_ids, self.monthly.coupon.discount_rate),
                         (0, [self.old.pk], 80))
        result = self.create(self.new, safe)
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.safe.refresh_from_db(); self.safe.coupon.refresh_from_db()
        self.assertEqual((order.amount, self.safe.status, self.safe.coupon.discount_rate, self.safe.coupon.service_ids),
                         (5000, 'USED', 50, [self.new.pk]))
        self.assertTrue(self.safe.vip_terms_locked_by_downgrade)

    def test_quote_from_before_configuration_change_cannot_debit(self):
        selected = next(row for row in self.options(self.old) if row['coupon_id'] == self.safe.pk)
        self.tier.safe_box_service_ids = [self.new.pk]; self.tier.save(update_fields=['safe_box_service_ids'])
        response = self.create(self.old, selected)
        self.assertNotEqual(response.data.get('code', 400), 0)
        self.assertFalse(Order.objects.exists())
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 20000)
        self.safe.refresh_from_db(); self.assertEqual(self.safe.status, 'UNUSED')

    def test_change_during_locked_recheck_rolls_back(self):
        selected = next(row for row in self.options(self.old) if row['coupon_id'] == self.safe.pk)
        from orders.discount_selection import lock_selected_discount
        def change_then_lock(*args):
            VipTier.objects.filter(pk=self.tier.pk).update(season_discount=50)
            return lock_selected_discount(*args)
        with patch('orders.discount_selection.lock_selected_discount', side_effect=change_then_lock):
            response = self.create(self.old, selected)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.tier.refresh_from_db(); self.assertEqual(self.tier.season_discount, 0)
        self.safe.refresh_from_db(); self.assertEqual(self.safe.status, 'UNUSED')

    def test_free_safe_box_uses_effective_terms_for_subsidy(self):
        self.tier.safe_box_service_ids = [self.new.pk]; self.tier.save(update_fields=['safe_box_service_ids'])
        selected = next(row for row in self.options(self.new) if row['coupon_id'] == self.safe.pk)
        self.assertEqual(selected['amount'], 0)
        response = self.create(self.new, selected)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(OrderPlatformSubsidy.objects.get(order=order).basis, 10000)

    def test_changed_monthly_rate_is_recorded_only_when_coupon_is_used(self):
        self.tier.monthly_discount = 90; self.tier.save(update_fields=['monthly_discount'])
        selected = next(row for row in self.options(self.old) if row['coupon_id'] == self.monthly.pk)
        self.monthly.coupon.refresh_from_db(); self.assertEqual(self.monthly.coupon.discount_rate, 80)
        result = self.create(self.old, selected)
        self.assertEqual(result.data['code'], 0, result.data)
        self.monthly.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual((self.monthly.status, self.monthly.coupon.discount_rate, self.monthly.vip_terms_locked_by_downgrade),
                         ('USED', 90, True))

    def test_locked_or_disabled_vip_coupon_keeps_original_terms(self):
        self.tier.safe_box_service_ids = [self.new.pk]; self.tier.season_discount = 50
        self.tier.save(update_fields=['safe_box_service_ids', 'season_discount'])
        self.safe.vip_terms_locked_by_downgrade = True; self.safe.save(update_fields=['vip_terms_locked_by_downgrade'])
        self.assertTrue(any(row['coupon_id'] == self.safe.pk and row['amount'] == 0 for row in self.options(self.old)))
        self.assertFalse(any(row['coupon_id'] == self.safe.pk for row in self.options(self.new)))
        self.safe.vip_terms_locked_by_downgrade = False; self.safe.save(update_fields=['vip_terms_locked_by_downgrade'])
        conf = config(); conf.vip_enabled = False; conf.save(update_fields=['vip_enabled'])
        self.assertTrue(any(row['coupon_id'] == self.safe.pk and row['amount'] == 0 for row in self.options(self.old)))
        self.assertFalse(any(row['coupon_id'] == self.safe.pk for row in self.options(self.new)))

    def test_safe_box_refresh_preserves_issued_expiry(self):
        expiry = timezone.now() + timedelta(days=1)
        self.safe.coupon.valid_to = expiry; self.safe.coupon.save(update_fields=['valid_to'])
        self.tier.season_discount = 50; self.tier.save(update_fields=['season_discount'])
        grant_periodic(self.account)
        self.safe.coupon.refresh_from_db()
        self.assertEqual((self.safe.coupon.valid_to, self.safe.coupon.discount_rate), (expiry, 50))
