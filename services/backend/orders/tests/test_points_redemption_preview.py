from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from coupons.models import Coupon, UserCoupon
from engagement.models import MemberProfile, PointLedger
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import Transaction
from wallet.models import SystemConfig
from wallet.services import get_wallet


def seed_recharge(wallet, *, principal=None, gift=0):
    """Give an isolated fixture a reconcilable recharge and funding lot."""
    from engagement.services import record_recharge
    from wallet.models import RechargeRecord
    principal = wallet.balance if principal is None else principal
    previous = wallet.balance - principal - gift
    if previous < 0 or principal <= 0:
        raise ValueError('fixture recharge exceeds wallet balance')
    topup = Transaction.objects.create(wallet=wallet, tx_type='TOPUP', amount=principal,
        balance_before=previous, balance_after=previous + principal, remark='隔离测试充值')
    gift_tx = None
    if gift:
        gift_tx = Transaction.objects.create(wallet=wallet, tx_type='GIFT', amount=gift,
            balance_before=previous + principal, balance_after=wallet.balance, remark='隔离测试充值赠送')
    record = RechargeRecord.objects.create(user_id=wallet.user_id, account_id=wallet.account_id,
        amount=principal, gift_amount=gift, recharge_tx=topup, gift_tx=gift_tx)
    record_recharge(record)
    return record


@override_settings(POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PointsServiceQuoteTest(APITestCase):
    def setUp(self):
        user = make_user()
        self.account = get_or_create_account_for_legacy_user(user)
        self.account.boss_type = make_boss_type(discount_rate=90)
        self.account.save(update_fields=['boss_type'])
        self.wallet = get_wallet(user=user, account=self.account)
        self.wallet.balance = 8000; self.wallet.save(update_fields=['balance'])
        self.profile, _ = MemberProfile.objects.update_or_create(account=self.account, defaults={'points_balance': 15000})
        self.service = make_service(price=10000)
        self.operation = config()
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        save_points_terms(group_point_hundredths=10000, group_coin_units=30,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)

    def payload(self):
        coupon = Coupon.objects.create(name='减3000', discount_type='DIRECT', amount=3000)
        UserCoupon.objects.create(account=self.account, coupon=coupon)
        options = self.client.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
        token = options.data['data']['options'][0]['selection_token']
        return {'service_id': self.service.pk, 'discount_selection_token': token,
                'preview_points_hundredths': 15000, 'client_request_id': 'points-preview-001'}

    def test_real_discount_quote_uses_account_balance_without_spending(self):
        payload = self.payload()
        before = (Order.objects.count(), Transaction.objects.count(), PointLedger.objects.count())
        response = self.client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        data = response.data['data']; preview = data['points_preview']
        self.assertEqual((data['original_amount'], data['amount']), (10000, 6000))
        self.assertEqual((preview['used_point_hundredths'], preview['discount_units'],
                          preview['estimated_payable_units']), (10000, 30, 5970))
        self.assertTrue(preview['preview_only'])
        self.assertFalse(preview['payment_available'])
        self.assertEqual(data['amount'], 6000)  # Real payment price is not silently reduced.
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.points_balance, self.wallet.balance), (15000, 8000))
        self.assertEqual(before, (Order.objects.count(), Transaction.objects.count(), PointLedger.objects.count()))
        denied = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.data['errorCode'], 'POINTS_PAYMENT_UNAVAILABLE')
        self.assertEqual(before, (Order.objects.count(), Transaction.objects.count(), PointLedger.objects.count()))

    def test_invalid_selection_disabled_points_and_negative_balance(self):
        payload = self.payload()
        for value in (-1, True, 1.5, '100'):
            with self.subTest(value=value):
                result = self.client.post('/api/orders/quote/', {**payload, 'preview_points_hundredths': value}, format='json')
                self.assertEqual(result.status_code, 400)
        self.profile.points_balance = -5; self.profile.save(update_fields=['points_balance'])
        preview = self.client.post('/api/orders/quote/', payload, format='json').data['data']['points_preview']
        self.assertEqual((preview['balance_point_hundredths'], preview['used_point_hundredths']), (-5, 0))
        self.operation.points_enabled = False; self.operation.save(update_fields=['points_enabled'])
        preview = self.client.post('/api/orders/quote/', payload, format='json').data['data']['points_preview']
        self.assertFalse(preview['applicable'])
        self.assertFalse(preview['payment_available'])

    def test_unconfigured_or_invalid_terms_never_offer_a_reduction(self):
        payload = self.payload()
        SystemConfig.objects.filter(key='points_redemption_terms').delete()
        response = self.client.post('/api/orders/quote/', payload, format='json')
        self.assertFalse(response.data['data']['points_preview']['applicable'])
        SystemConfig.objects.create(key='points_redemption_terms', value='{"version":1}')
        response = self.client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual((response.status_code, response.data['errorCode']), (409, 'POINTS_PREVIEW_UNAVAILABLE'))

    @override_settings(DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=False)
    def test_quote_never_uses_a_different_boss_balance_or_creates_a_profile(self):
        other = get_or_create_account_for_legacy_user(make_user())
        MemberProfile.objects.filter(account=other).delete()
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
        response = client.post('/api/orders/quote/', {'service_id': self.service.pk,
                               'preview_points_hundredths': 10000, 'account_id': self.account.pk}, format='json')
        self.assertEqual(response.data['data']['points_preview']['balance_point_hundredths'], 0)
        self.assertFalse(MemberProfile.objects.filter(account=other).exists())

    def test_experience_offer_is_not_eligible_for_points(self):
        experience = make_service(price=1000, service_type='EXPERIENCE', experience_kind='DAILY',
                                  platform='PC', allows_discount_coupons=False)
        response = self.client.post('/api/orders/quote/', {'service_id': experience.pk,
                                    'preview_points_hundredths': 10000}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        preview = response.data['data']['points_preview']
        self.assertFalse(preview['applicable'])
        self.assertFalse(preview['payment_available'])

    def test_each_estimate_reads_current_terms_and_balance(self):
        payload = self.payload()
        first = self.client.post('/api/orders/quote/', payload, format='json').data['data']['points_preview']
        self.assertEqual((first['config']['version'], first['discount_units']), (1, 30))
        save_points_terms(group_point_hundredths=10000, group_coin_units=50,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        self.profile.points_balance = 5000; self.profile.save(update_fields=['points_balance'])
        second = self.client.post('/api/orders/quote/', payload, format='json').data['data']['points_preview']
        self.assertEqual((second['config']['version'], second['discount_units']), (2, 0))
        self.assertEqual((first['config']['version'], first['discount_units']), (1, 30))

    def test_non_mapping_quote_body_uses_existing_validation(self):
        response = self.client.post('/api/orders/quote/', [], format='json')
        self.assertEqual(response.data['code'], 400)

    @override_settings(POINTS_USAGE_SWITCHES_ENABLED=True)
    def test_preview_respects_points_usage_pause(self):
        from engagement.points_usage import lock_usage
        from stores.db import transaction
        payload = self.payload()
        with transaction.atomic():
            _, row, _ = lock_usage()
            row.value = '{"redemption_enabled":true,"shop_enabled":true,"lottery_enabled":false,"version":1}'
            row.save(update_fields=['value'])
        preview = self.client.post('/api/orders/quote/', payload, format='json').data['data']['points_preview']
        self.assertFalse(preview['applicable'])
        self.assertEqual(preview['reason'], '本店积分使用暂时暂停')

    @override_settings(POINTS_REDEMPTION_PREVIEW_ENABLED=False)
    def test_explicit_preview_is_rejected_when_feature_is_disabled(self):
        response = self.client.post('/api/orders/quote/', {'preview_points_hundredths': 0}, format='json')
        self.assertEqual((response.status_code, response.data['errorCode']), (400, 'POINTS_PREVIEW_DISABLED'))


@override_settings(POINTS_REDEMPTION_PREVIEW_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsServiceQuoteStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_quote_reads_current_store_rules_and_balance(self):
        values = []
        for store, host, group, balance in ((resolve_store(), 'sl.esports.club', 30, 10000),
                                             (xinan_store(), 'xa.esports.club', 20, 20000)):
            with store_context(store):
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                MemberProfile.objects.update_or_create(account=account, defaults={'points_balance': balance})
                config(); service = make_service(price=1000)
                save_points_terms(group_point_hundredths=10000, group_coin_units=group,
                                  max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                result = client.post('/api/orders/quote/', {'service_id': service.pk,
                                     'preview_points_hundredths': 20000}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                quote = result.data['data']['points_preview']
                values.append((quote['balance_point_hundredths'], quote['discount_units']))
        self.assertEqual(values, [(10000, 30), (20000, 40)])
