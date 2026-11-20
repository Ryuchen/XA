from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.models import PointFundingLot
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_promotion, make_service, make_user
from wallet.services import get_wallet


@override_settings(DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                   SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class DispatchDiscountApiTest(APITestCase):
    def setUp(self):
        user = make_user(); self.boss = get_or_create_account_for_legacy_user(user)
        self.boss.boss_type = make_boss_type(name='代单九折', discount_rate=90); self.boss.save(update_fields=['boss_type'])
        self.wallet = get_wallet(account=self.boss, user=user); self.wallet.balance = 20000; self.wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=self.boss, source_key='dispatch-discount', kind='PREPAID', remaining=20000)
        config()
        from console.grab_config import save_grab_window
        save_grab_window(30, 0, lambda *args: None)
        self.service = make_service(price=10000)
        self.promotion = make_promotion(title='代单八五折', discount_rate=85)
        self.coupon = Coupon.objects.create(name='代单减300', discount_type='DIRECT', amount=3000)
        self.owned = UserCoupon.objects.create(account=self.boss, coupon=self.coupon)
        staff = make_console_user(['order:view', 'order:dispatch']); self.staff = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = self.staff; staff.admin_membership.save(update_fields=['account'])
        self.admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff)[0])
        self.params = {'customer_id': self.boss.pk, 'service_id': self.service.pk, 'game_rounds': 1}

    def quote(self, **params):
        response = self.admin.get('/api/admin/orders/dispatch-quote/', {**self.params, **params})
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def submit(self, quote, **params):
        return self.admin.post('/api/admin/orders/dispatch/', {**self.params, 'expected_amount': quote['amount'],
            'discount_selection_token': quote['discount_selection_token'], 'client_request_id': 'discount-staff',
            'boss_confirmation_method': '微信文字确认', **params}, format='json')

    def test_recommended_combination_consumes_boss_coupon_and_records_confirmation(self):
        quote = self.quote()
        self.assertEqual((quote['amount'], quote['discount_option_key']), (5500, f'promotion:{self.promotion.pk}|coupon:{self.owned.pk}'))
        rejected = self.submit(quote)
        self.assertEqual(rejected.data['code'], 400)
        self.assertFalse(Order.objects.exists())
        result = self.submit(quote, boss_confirmation='老板确认活动加金额券')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual((order.amount, order.promo_discount, order.coupon_discount), (5500, 1500, 3000))
        self.assertEqual(order.funding_confirmation['actor_account_id'], self.staff.pk)
        self.assertEqual(order.funding_allocations.get(kind='PREPAID').amount, 5500)
        self.owned.refresh_from_db(); self.assertEqual((self.owned.status, self.owned.order_id), ('USED', order.pk))

    def test_duplicate_recovers_receipt_if_coupon_validation_races_payment(self):
        from wallet.models import Transaction
        from wallet.receipts import _replay

        quote = self.quote()
        confirmation = {'boss_confirmation': '老板确认活动加金额券'}
        committed = self.submit(quote, **confirmation).data
        self.assertEqual(committed['code'], 0, committed)
        saw_first_lookup = False

        def observe_receipt_after_first_lookup(request, scope):
            nonlocal saw_first_lookup
            if not saw_first_lookup:
                saw_first_lookup = True
                return None
            return _replay(request, scope)

        # The retry passed both early checks before the first debit consumed the coupon.
        with patch('wallet.receipts._replay', side_effect=observe_receipt_after_first_lookup), \
                patch('console.views.peek_fund_write', return_value=(False, '')):
            replay = self.submit(quote, **confirmation).data
        self.assertEqual(replay.get('errorCode'), 'FUND_ALREADY_PROCESSED', replay)
        self.assertEqual(replay['fundResult'], committed)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Transaction.objects.filter(tx_type='PAY').count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 14500)

    def test_staff_selects_vip_and_coupon_remains_unused(self):
        first = self.quote()
        vip = next(item for item in first['discount_options'] if item['key'] == 'vip')
        quote = self.quote(discount_selection_token=vip['selection_token'])
        self.assertEqual((quote['amount'], quote['discount_option_key']), (9000, 'vip'))
        self.assertTrue(any(item['selection_token'] == quote['discount_selection_token'] for item in quote['discount_options']))
        response = self.submit(quote, boss_confirmation='老板确认会员折扣')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual((order.amount, order.boss_discount, order.coupon_discount), (9000, 1000, 0))
        self.owned.refresh_from_db(); self.assertEqual(self.owned.status, 'UNUSED')

    def test_coupon_changes_inside_transaction_roll_back(self):
        quote = self.quote()
        from orders.discount_selection import lock_selected_discount
        def change_then_validate(*args):
            Coupon.objects.filter(pk=self.coupon.pk).update(amount=2000)
            return lock_selected_discount(*args)
        with patch('orders.discount_selection.lock_selected_discount', side_effect=change_then_validate):
            response = self.submit(quote, boss_confirmation='老板确认活动加金额券')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 20000)
        self.coupon.refresh_from_db(); self.assertEqual(self.coupon.amount, 3000)
        self.owned.refresh_from_db(); self.assertEqual(self.owned.status, 'UNUSED')

    def test_missing_or_foreign_choice_cannot_debit(self):
        quote = self.quote()
        missing = self.admin.post('/api/admin/orders/dispatch/', {**self.params, 'boss_confirmation': '老板确认'}, format='json')
        self.assertEqual(missing.status_code, 400)
        other = get_or_create_account_for_legacy_user(make_user())
        foreign = self.admin.post('/api/admin/orders/dispatch/', {**self.params, 'customer_id': other.pk,
            'discount_selection_token': quote['discount_selection_token'], 'boss_confirmation': '老板确认'}, format='json')
        self.assertEqual(foreign.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_staff_dispatch_uses_changed_monthly_coupon_rule(self):
        from django.utils import timezone
        from coupons.monthly import issue_monthly_coupons
        from engagement.models import VipTier
        tier = VipTier.objects.create(boss_type=self.boss.boss_type, level=2, recharge_threshold=1,
                                      monthly_coupon_count=1, monthly_discount=80)
        issue_monthly_coupons(self.boss, tier.pk, now=timezone.now(), allow_new_period=True)
        monthly = UserCoupon.objects.get(account=self.boss, coupon__grant_key__startswith='vip-month:')
        tier.monthly_discount = 40; tier.save(update_fields=['monthly_discount'])
        quote = self.quote()
        self.assertEqual((quote['amount'], quote['discount_option_key']), (4000, f'coupon:{monthly.pk}'))
        response = self.submit(quote, boss_confirmation='老板确认月券折扣')
        self.assertEqual(response.data['code'], 0, response.data)
        monthly.refresh_from_db(); monthly.coupon.refresh_from_db()
        self.assertEqual((monthly.status, monthly.coupon.discount_rate), ('USED', 40))
