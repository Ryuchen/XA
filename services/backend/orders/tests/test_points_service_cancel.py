"""Owner cancellation of a points-paid service order, on isolated test data."""
from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.models import PointFundingLot, PointLedger
from engagement.services import config
from orders.models import Order, OrderPointsRefund, OrderRefund
from orders.tests import test_points_service_refund as refund_fixture
from orders.tests.factories import make_user
from wallet.models import Transaction


@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_CANCEL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PointsServiceOwnerCancelTests(TestCase):
    def setUp(self):
        refund_fixture.ServicePointsRefundTests.setUp(self)
        self.order.refresh_from_db()
        self.order.auto_cancel_at = timezone.now() + timedelta(minutes=10)
        self.order.save(update_fields=['auto_cancel_at'])
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        self.url = f'/api/orders/orders/{self.order.pk}/cancel/'

    def unchanged(self):
        self.order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('PENDING', 'PAID'))
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1030, 5000))
        self.assertFalse(OrderRefund.objects.filter(order=self.order).exists())
        self.assertFalse(OrderPointsRefund.objects.filter(refund__order=self.order).exists())

    def test_owner_full_cancel_returns_original_assets_and_coupon_once(self):
        coupon = Coupon.objects.create(name='测试金额券', amount=10)
        claim = UserCoupon.objects.create(user=self.user, account=self.account, coupon=coupon,
            order=self.order, status=UserCoupon.Status.USED, restore_on_refund=True)
        operation = config(); operation.points_enabled = False; operation.save(update_fields=['points_enabled'])
        result = self.client.post(self.url, {'reason':'不再需要'}, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        self.assertIn('抵扣积分已退回', result.data['msg'])
        self.assertEqual(result.data['data']['points_payment'],
                         {'state':'CONFIRMED', 'points_hundredths':10000, 'discount_units':30})
        self.order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db(); claim.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status, self.order.cancel_reason),
                         ('CANCELLED', 'REFUNDED', '不再需要'))
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (2000, 15000))
        self.assertEqual(list(PointFundingLot.objects.order_by('kind').values_list('remaining', flat=True)), [100, 100, 1800])
        refund = OrderRefund.objects.get(order=self.order)
        self.assertEqual((refund.operator_id, refund.operator_account_id, refund.amount),
                         (self.user.pk, self.account.pk, 970))
        receipt = OrderPointsRefund.objects.get(refund=refund)
        self.assertEqual((receipt.coin_units, receipt.points_hundredths, receipt.is_final), (970, 10000, True))
        self.assertEqual((claim.status, claim.order_id), ('UNUSED', None))
        self.assertNotEqual(self.client.post(self.url, {'reason':'不再需要'}, format='json').data.get('code'), 0)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='REFUND').count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)

    def test_other_customer_cannot_cancel(self):
        other = get_or_create_account_for_legacy_user(make_user())
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
        self.assertNotEqual(client.post(self.url, {}, format='json').data.get('code'), 0)
        self.unchanged()

    def test_account_only_owner_can_cancel_without_a_legacy_identity(self):
        from club_accounts.models import LegacyAccountMap
        self.order.customer = None; self.order.save(update_fields=['customer'])
        self.wallet.user = None; self.wallet.save(update_fields=['user'])
        LegacyAccountMap.objects.filter(account=self.account).delete()
        result = self.client.post(self.url, {}, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        self.assertEqual(result.data['data']['points_payment']['state'], 'CONFIRMED')
        refund = OrderRefund.objects.get(order=self.order)
        self.assertEqual((refund.operator_id, refund.operator_account_id), (None, self.account.pk))
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (2000, 15000))

    def test_nonrefundable_coupon_remains_used_after_points_cancellation(self):
        coupon = Coupon.objects.create(name='不退还资格券', discount_type='QUALIFICATION', amount=0)
        claim = UserCoupon.objects.create(user=self.user, account=self.account, coupon=coupon,
            order=self.order, status=UserCoupon.Status.USED, restore_on_refund=False)
        result = self.client.post(self.url, {}, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.order_id), ('USED', self.order.pk))
        self.assertEqual(OrderRefund.objects.get(order=self.order).benefit_effects['order_coupon_results'],
                         [{'name':'不退还资格券', 'outcome':'NOT_RETURNED'}])

    @override_settings(SERVICE_ORDER_POINTS_CANCEL_TESTING=False)
    def test_default_closed_blocks_funds_and_status(self):
        self.assertNotEqual(self.client.post(self.url, {}, format='json').data.get('code'), 0)
        self.unchanged()

    def test_missing_original_marker_does_not_turn_into_coin_only_refund(self):
        terms = dict(self.order.funding_terms); terms.pop('points_redemption_v1')
        Order.objects.filter(pk=self.order.pk).update(funding_terms=terms)
        detail = self.client.get(f'/api/orders/orders/{self.order.pk}/')
        self.assertEqual(detail.data['data']['points_payment'], {'state':'REVIEW'})
        listing = self.client.get('/api/orders/orders/?role=customer')
        entry = next(item for item in listing.data['data'] if item['id'] == self.order.pk)
        self.assertEqual(entry['points_payment'], {'state':'REVIEW'})
        self.assertNotEqual(self.client.post(self.url, {}, format='json').data.get('code'), 0)
        self.unchanged()

    def test_owner_sees_verified_redemption_and_tampering_requires_review(self):
        detail = f'/api/orders/orders/{self.order.pk}/'
        result = self.client.get(detail)
        self.assertEqual(result.data['data']['points_payment'],
                         {'state':'CONFIRMED', 'points_hundredths':10000, 'discount_units':30})
        listing = self.client.get('/api/orders/orders/?role=customer')
        self.assertEqual(next(row for row in listing.data['data'] if row['id'] == self.order.pk)['points_payment'],
                         result.data['data']['points_payment'])
        PointLedger.objects.filter(pk=self.order.points_redemption.ledger_id).update(amount=-9999)
        result = self.client.get(detail)
        self.assertEqual(result.data['data']['points_payment'], {'state':'REVIEW'})
        self.assertNotEqual(self.client.post(self.url, {}, format='json').data.get('code'), 0)
        self.unchanged()

    def test_provider_cannot_read_customer_points_payment(self):
        from types import SimpleNamespace
        from orders.serializers import OrderSerializer
        provider = get_or_create_account_for_legacy_user(make_user(role='PROVIDER'))
        serializer = OrderSerializer(context={'request':SimpleNamespace(account=provider)})
        self.assertIsNone(serializer.get_points_payment(self.order))

    def test_missing_receipt_table_displays_review_without_querying_it(self):
        from stores.db import transaction
        from orders.models import OrderPointsRedemption
        connection = transaction.get_connection()
        names = connection.introspection.table_names
        with patch.object(connection.introspection, 'table_names',
                          side_effect=lambda cursor=None, **kwargs: [name for name in names(cursor, **kwargs)
                              if name != OrderPointsRedemption._meta.db_table]):
            detail = self.client.get(f'/api/orders/orders/{self.order.pk}/')
            self.assertEqual(detail.data['data']['points_payment'], {'state':'REVIEW'})
            listing = self.client.get('/api/orders/orders/?role=customer')
            row = next(item for item in listing.data['data'] if item['id'] == self.order.pk)
            self.assertEqual(row['points_payment'], {'state':'REVIEW'})

    def test_points_evidence_queries_are_batched_for_owner_order_list(self):
        from django.test.utils import CaptureQueriesContext
        from stores.db import transaction
        from engagement.funding_wallet import allocate_locked_order_funding, snapshot_service_funding_terms
        from orders.points_service_payment import quote, record_locked
        connection = transaction.get_connection()
        def read():
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get('/api/orders/orders/?role=customer')
                self.assertEqual(response.data.get('code'), 0, response.data)
                self.assertTrue(all(row['points_payment']['state'] == 'CONFIRMED' for row in response.data['data']))
            tables = ('orders_orderpointsredemption', 'engagement_pointledger', 'wallet_transaction',
                      'club_accounts_legacyaccountmap')
            return len([entry for entry in captured if any(table in entry['sql'].lower() for table in tables)])
        baseline = read()
        self.wallet.balance += 12000; self.wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=self.account, source_key='list-batch-extra', kind='PREPAID', remaining=12000)
        self.profile.points_balance = 120000; self.profile.save(update_fields=['points_balance'])
        price = {'original_amount':1000, 'boss_discount':0, 'promo_discount':0, 'coupon_discount':0,
                 'promotion_id':None, 'game_rounds':1}
        for _ in range(10):
            _, token = quote(self.account, self.service, price, 10000)
            with transaction.atomic():
                wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
                order = Order.objects.create(customer=self.user, customer_account=self.account, service=self.service,
                    amount=970, original_amount=1000, business_cycle=self.account.business_cycle)
                allocate_locked_order_funding(order, wallet)
                before = wallet.balance; wallet.balance -= 970; wallet.save(update_fields=['balance'])
                payment = Transaction.objects.create(wallet=wallet, order=order, amount=-970,
                    tx_type=Transaction.TxType.PAY, balance_before=before, balance_after=wallet.balance)
                snapshot_service_funding_terms(order, paid_at=payment.created_at)
                record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
        self.assertLessEqual(read(), baseline + 1)

    def test_failed_points_receipt_rolls_back_wallet_coupon_and_order(self):
        coupon = Coupon.objects.create(name='测试金额券', amount=10)
        claim = UserCoupon.objects.create(user=self.user, account=self.account, coupon=coupon,
            order=self.order, status=UserCoupon.Status.USED, restore_on_refund=True)
        with patch.object(OrderPointsRefund.objects, 'create', side_effect=RuntimeError('模拟写入失败')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, {}, format='json')
        self.unchanged(); claim.refresh_from_db()
        self.assertEqual((claim.status, claim.order_id), ('USED', self.order.pk))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='REFUND').exists())
        self.assertFalse(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').exists())

    def test_new_grab_pool_requires_its_own_cancellation_receipt_integration(self):
        from orders.grab_pool_models import OrderGrabPoolSnapshot
        now = timezone.now()
        OrderGrabPoolSnapshot.objects.create(order=self.order, opened_at=now,
            original_total_deadline=now + timedelta(minutes=1), first_deadline=now + timedelta(seconds=30),
            total_deadline=now + timedelta(minutes=1), window_seconds=30, config_version=1,
            visibility_delays={}, required_provider_count=1)
        self.assertNotEqual(self.client.post(self.url, {}, format='json').data.get('code'), 0)
        self.unchanged()

    def test_conflicting_direct_payment_evidence_blocks_cancellation(self):
        from orders.direct_payment_models import DirectPaymentIntent
        DirectPaymentIntent.objects.create(order=self.order, account=self.account,
            client_request_id='conflict', request_fingerprint='test', merchant_order_no='cancel-conflict',
            appid='test', mchid='test', openid='test', amount_fen=97)
        self.assertEqual(self.client.get(f'/api/orders/orders/{self.order.pk}/').data['data']['points_payment'],
                         {'state':'REVIEW'})
        self.assertNotEqual(self.client.post(self.url, {}, format='json').data.get('code'), 0)
        self.unchanged()


@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_CANCEL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsServiceOwnerCancelStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_and_account_ids_refund_only_the_authenticated_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from engagement.models import MemberProfile
        from wallet.models import Wallet
        fixtures = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                refund_fixture.ServicePointsRefundTests.setUp(self)
                Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=timezone.now() + timedelta(minutes=10))
                fixtures.append((store, host, self.order.pk, self.account.pk, issue_account_tokens(self.account)[0]))
        self.assertEqual(fixtures[0][2:4], fixtures[1][2:4])
        for index, (store, host, order_id, account_id, token) in enumerate(fixtures):
            client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
            result = client.post(f'/api/orders/orders/{order_id}/cancel/', {}, format='json')
            self.assertEqual(result.data.get('code'), 0, result.data)
            self.assertIn('浪花' if index == 0 else '星辉', result.data['msg'])
            with store_context(store):
                self.assertEqual((Order.objects.get(pk=order_id).status, Wallet.objects.get(account_id=account_id).balance,
                                  MemberProfile.objects.get(account_id=account_id).points_balance),
                                 ('CANCELLED', 2000, 15000))
            if index == 0:
                with store_context(fixtures[1][0]):
                    self.assertEqual((Order.objects.get(pk=order_id).status, Wallet.objects.get(account_id=account_id).balance,
                                      MemberProfile.objects.get(account_id=account_id).points_balance),
                                     ('PENDING', 1030, 5000))

