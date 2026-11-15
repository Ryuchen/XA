"""Points refund deposit debit using real recorded assets and cash deposit."""
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.test import override_settings
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import OrderPointsRefund, OrderProvider, OrderRefund
from orders.points_service_refund import refund_assets_locked, verify_refund_history_locked
from orders.tests.factories import make_provider
from orders.tests.test_points_service_refund import ServicePointsRefundTests
from stores.db import transaction
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import RefundDepositDeficit, Transaction, get_platform_wallet
from wallet.refund_deposit import charge_refund_deposit
from wallet.services import get_wallet
from wallet.test_refund_deposit_direct import DirectRefundDepositTest


@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True)
class PointsRefundDepositTest(APITestCase):
    post = DirectRefundDepositTest.post

    def setUp(self):
        DirectRefundDepositTest.setUp(self)
        self.provider_profile = self.profile
        self.provider_wallet = self.wallet
        self.provider_account = self.profile.account
        ServicePointsRefundTests.setUp(self)
        OrderProvider.objects.create(order=self.order, provider=self.provider, provider_account=self.provider_account,
                                     settlement_base=970, provider_income=776, commission_rate=20)

    def make_refund(self, *, equivalent, coin):
        refund = OrderRefund.objects.create(order=self.order, amount=coin, reason='积分协商退款测试',
                                            operator=self.operator, operator_account=self.operator_account)
        proof = refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=equivalent)
        return refund, proof

    def charge(self, refund, amount, profile=None):
        return charge_refund_deposit(refund=refund, profile=profile or self.provider_profile, amount=amount,
                                     operator=self.operator, operator_account=self.operator_account)

    def test_deposit_can_cover_points_equivalent_above_refunded_coin_with_real_evidence(self):
        platform = get_platform_wallet(); before_platform = platform.balance
        with transaction.atomic():
            refund, proof = self.make_refund(equivalent=500, coin=480)
            self.assertEqual((proof.coin_units, proof.points_hundredths), (480, 5000))
            self.assertEqual(verify_refund_history_locked(refund.pk).pk, proof.pk)
            deficit = self.charge(refund, 490)
            self.assertEqual(self.charge(refund, 490).pk, deficit.pk)
        self.provider_profile.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        platform.refresh_from_db()
        self.assertEqual((self.provider_profile.deposit_paid, platform.balance), (9510, before_platform - 490))
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1510, 10000))
        self.assertEqual((deficit.amount, deficit.remaining, deficit.source_store_transaction.amount), (490, 490, -490))
        self.assertEqual(RefundDepositDeficit.objects.filter(refund=refund).count(), 1)

    def test_only_points_return_can_still_be_backed_by_provider_deposit(self):
        with transaction.atomic():
            refund, proof = self.make_refund(equivalent=10, coin=0)
            self.assertIsNone(proof.coin_transaction_id)
            self.assertEqual(proof.points_hundredths, 100)
            deficit = self.charge(refund, 10)
        self.assertEqual((refund.amount, deficit.amount), (0, 10))
        self.provider_profile.refresh_from_db(); self.assertEqual(self.provider_profile.deposit_paid, 9990)

    def test_unverified_or_over_limit_refund_and_unrelated_provider_are_rejected(self):
        with transaction.atomic():
            refund, proof = self.make_refund(equivalent=500, coin=480)
        before = (self.provider_profile.deposit_paid, get_platform_wallet().balance, Transaction.objects.count())
        for fault in ('limit', 'operator', 'points', 'source', 'switch', 'provider', 'missing-proof', 'equivalent'):
            with self.subTest(fault=fault), transaction.atomic():
                target = self.provider_profile
                if fault == 'operator':
                    Transaction.objects.filter(pk=proof.coin_transaction_id).update(operator_account=None)
                elif fault == 'points':
                    type(proof.points_ledger).objects.filter(pk=proof.points_ledger_id).update(amount=4999)
                elif fault == 'source':
                    self.order.funding_allocations.filter(kind='PREPAID').update(refunded_amount=470)
                elif fault == 'missing-proof':
                    OrderPointsRefund.objects.filter(pk=proof.pk).delete()
                elif fault == 'equivalent':
                    OrderPointsRefund.objects.filter(pk=proof.pk).update(equivalent_units=600)
                elif fault == 'provider':
                    stranger = make_provider(deposit_mode='CASH', deposit_paid=0)
                    account = get_or_create_account_for_legacy_user(stranger)
                    stranger.escort_profile.account = account; stranger.escort_profile.save(update_fields=['account'])
                    target = stranger.escort_profile
                with override_settings(SERVICE_POINTS_REFUND_DEPOSIT_TESTING=(fault != 'switch')):
                    with self.assertRaises(ValueError):
                        self.charge(refund, 510 if fault == 'limit' else 490, target)
                transaction.set_rollback(True)
        self.provider_profile.refresh_from_db()
        self.assertEqual((self.provider_profile.deposit_paid, get_platform_wallet().balance, Transaction.objects.count()), before)
        self.assertFalse(RefundDepositDeficit.objects.filter(refund=refund).exists())

    def test_failure_after_customer_refund_rolls_back_assets_and_deposit_together(self):
        self.wallet.refresh_from_db(); self.profile.refresh_from_db(); self.provider_profile.refresh_from_db()
        before = (self.wallet.balance, self.profile.points_balance, self.provider_profile.deposit_paid,
                  get_platform_wallet().balance, Transaction.objects.count())
        with self.assertRaisesMessage(RuntimeError, '记录押金失败'):
            with transaction.atomic():
                refund, _ = self.make_refund(equivalent=500, coin=480)
                with patch('wallet.refund_deposit.RefundDepositDeficit.objects.create',
                           side_effect=RuntimeError('记录押金失败')):
                    self.charge(refund, 490)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db(); self.provider_profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance, self.provider_profile.deposit_paid,
                          get_platform_wallet().balance, Transaction.objects.count()), before)
        self.assertFalse(OrderPointsRefund.objects.filter(redemption__order=self.order).exists())
        self.assertFalse(RefundDepositDeficit.objects.filter(refund__order=self.order).exists())


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PointsRefundDepositTwoStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_refund_and_provider_ids_debit_only_the_selected_store(self):
        from orders.tests.factories import make_console_user
        from users.models import EscortProfile
        stores = [resolve_store(), xinan_store()]
        facts = []
        for index, store in enumerate(stores):
            with store_context(store):
                provider = make_provider(deposit_mode='CASH', deposit_paid=0, deposit_required=10000)
                provider_account = get_or_create_account_for_legacy_user(provider)
                get_wallet(user=provider, account=provider_account)
                profile = provider.escort_profile
                profile.account = provider_account; profile.save(update_fields=['account'])
                staff = make_console_user(['escort:view', 'escort:deposit_topup', 'order:refund'])
                staff_account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
                client = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
                response = client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                    'amount': 10000, 'reason': '隔离测试押金', 'reference': f'two-store-{index}',
                    'client_request_id': 'same-store-topup'}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                ServicePointsRefundTests.setUp(self)
                OrderProvider.objects.create(order=self.order, provider=provider, provider_account=provider_account,
                                             settlement_base=970, provider_income=776, commission_rate=20)
                with transaction.atomic():
                    refund = OrderRefund.objects.create(order=self.order, amount=480, reason='隔离退款',
                                                        operator=staff, operator_account=staff_account)
                    refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
                    if index == 0:
                        charge_refund_deposit(refund=refund, profile=profile, amount=490,
                                              operator=staff, operator_account=staff_account)
                facts.append((provider_account.pk, profile.pk, self.order.pk, refund.pk))
        self.assertEqual(facts[0], facts[1])
        for index, store in enumerate(stores):
            with store_context(store):
                profile = EscortProfile.objects.get(pk=facts[index][1])
                self.assertEqual(profile.deposit_paid, 9510 if index == 0 else 10000)
                self.assertEqual(RefundDepositDeficit.objects.filter(refund_id=facts[index][3]).count(),
                                 1 if index == 0 else 0)
