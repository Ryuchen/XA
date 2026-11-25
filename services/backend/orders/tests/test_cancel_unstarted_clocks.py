"""Trusted store-DB time for unstarted cancellation and prestart handoff."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderPrestartHandoff, OrderProvider
from orders.state_machine import transition
from orders.tests import test_prestart_handoff as handoff_fixture
from orders.tests.factories import make_completed_order, make_order, make_provider, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet import test_refund_deposit_deficit as deficit_fixture
from wallet import test_refund_recovery_grab as recovery_fixture
from wallet.models import RefundDepositRecoveryPermit, Transaction, get_platform_wallet
from wallet.refund_recovery_order import cancel_unstarted
from wallet.services import get_wallet
from wallet.settlement_clocks import ClockUnavailable


@override_settings(
    REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    GRAB_SINGLE_WINDOW_ENABLED=False, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=False,
    GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False, SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    CELERY_TASK_ALWAYS_EAGER=False,
)
class CancelUnstartedClockTests(APITransactionTestCase):
    setUp = recovery_fixture.RefundRecoveryGrabTest.setUp
    login = recovery_fixture.RefundRecoveryGrabTest.login
    ok = recovery_fixture.RefundRecoveryGrabTest.ok
    recharge = recovery_fixture.RefundRecoveryGrabTest.recharge
    pool = recovery_fixture.RefundRecoveryGrabTest.pool
    setUpGap = recovery_fixture.RefundRecoveryGrabTest.setUpGap
    grab = recovery_fixture.RefundRecoveryGrabTest.grab
    add_gap_for = recovery_fixture.RefundRecoveryGrabTest.add_gap_for
    replacement = handoff_fixture.PrestartHandoffTest.replacement
    transfer = handoff_fixture.PrestartHandoffTest.transfer

    def test_state_machine_cancel_disconnect_rolls_back_then_cancel_and_permit_share_time(self):
        deficit = self.setUpGap()
        order = make_order(
            self.boss, provider=self.provider, provider_account=self.account,
            service=self.service, status='GRABBED', payment_status='PAID', amount=10000,
        )
        permit = RefundDepositRecoveryPermit.objects.create(
            profile=self.profile, account=self.account, order=order,
        )
        boss_wallet = get_wallet(account=self.accounts[self.boss.pk], user=self.boss)
        provider_wallet = get_wallet(account=self.account, user=self.provider)
        before = (boss_wallet.balance, provider_wallet.balance, Transaction.objects.count())
        observed = timezone.now() + timedelta(minutes=11)
        with patch('django.utils.timezone.now', return_value=observed), \
             patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('state cancel clock disconnected')):
            with self.assertRaises(ClockUnavailable):
                transition(order.pk, Order.Status.CANCELLED, reason='可信时钟未开工取消')
        order.refresh_from_db(); permit.refresh_from_db(); deficit.refresh_from_db()
        boss_wallet.refresh_from_db(); provider_wallet.refresh_from_db()
        self.assertEqual((order.status, order.cancelled_at), ('GRABBED', None))
        self.assertIsNone(permit.closed_at)
        self.assertEqual(deficit.remaining, 200)
        self.assertEqual((boss_wallet.balance, provider_wallet.balance, Transaction.objects.count()), before)
        with transaction.atomic():
            with self.assertRaises(ClockUnavailable):
                cancel_unstarted(order, now=observed)
        permit.refresh_from_db()
        self.assertIsNone(permit.closed_at)

        with patch('django.utils.timezone.now', return_value=observed):
            result = transition(order.pk, Order.Status.CANCELLED, reason='可信时钟未开工取消')
        permit.refresh_from_db(); deficit.refresh_from_db()
        self.assertEqual((result.status, result.cancelled_at), ('CANCELLED', observed))
        self.assertEqual((permit.closed_at, permit.close_reason), (observed, 'CANCELLED_UNSTARTED'))
        self.assertEqual(deficit.remaining, 200)
        self.assertEqual((boss_wallet.balance, provider_wallet.balance, Transaction.objects.count()), before)
        with patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('closed permit retry needs no clock')):
            with transaction.atomic():
                cancel_unstarted(result)
        permit.refresh_from_db()
        self.assertEqual((permit.closed_at, permit.close_reason), (observed, 'CANCELLED_UNSTARTED'))
        self.assertEqual(Transaction.objects.count(), before[2])

    def test_prestart_handoff_disconnect_rolls_back_assignments_then_uses_one_handoff_time(self):
        deficit = self.setUpGap()
        replacement = self.replacement('可信时钟接替陪玩')
        replacement_deficit = self.add_gap_for(replacement)
        self.ok(self.recharge())
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.account.pk,
            'client_request_id': 'clock-prestart-handoff',
        }, format='json'))
        order = Order.objects.get(pk=created['id'])
        permit = RefundDepositRecoveryPermit.objects.get(order=order, account=self.account)
        boss_wallet = get_wallet(account=self.accounts[self.boss.pk], user=self.boss)
        before = (boss_wallet.balance, order.amount, Transaction.objects.count())
        observed = timezone.now() + timedelta(minutes=13)
        with patch('django.utils.timezone.now', return_value=observed), \
             patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('handoff clock disconnected')):
            with self.assertRaises(ClockUnavailable):
                self.transfer(order.pk, self.provider, replacement)
        order.refresh_from_db(); permit.refresh_from_db(); boss_wallet.refresh_from_db()
        old = OrderProvider.objects.get(order=order, provider_account=self.account)
        self.assertEqual(order.provider_account_id, self.account.pk)
        self.assertIsNone(old.released_at)
        self.assertFalse(OrderProvider.objects.filter(
            order=order, provider_account=self.accounts[replacement.pk]).exists())
        self.assertFalse(OrderPrestartHandoff.objects.filter(order=order).exists())
        self.assertIsNone(permit.closed_at)
        self.assertEqual((boss_wallet.balance, order.amount, Transaction.objects.count()), before)

        with patch('django.utils.timezone.now', return_value=observed):
            response = self.transfer(order.pk, self.provider, replacement)
        self.ok(response)
        order.refresh_from_db(); permit.refresh_from_db(); old.refresh_from_db()
        incoming = OrderProvider.objects.get(order=order, provider_account=self.accounts[replacement.pk])
        new_permit = RefundDepositRecoveryPermit.objects.get(
            order=order, account=self.accounts[replacement.pk],
        )
        self.assertEqual(order.provider_account_id, self.accounts[replacement.pk].pk)
        self.assertEqual((old.released_at, permit.closed_at, permit.close_reason),
                         (observed, observed, 'TRANSFERRED_UNSTARTED'))
        self.assertIsNone(incoming.released_at)
        self.assertIsNone(new_permit.closed_at)
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 1)
        deficit.refresh_from_db(); replacement_deficit.refresh_from_db()
        self.assertEqual((deficit.remaining, replacement_deficit.remaining), (200, 200))
        self.assertEqual((boss_wallet.balance, order.amount, Transaction.objects.count()), before)
        with patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('closed handoff permit retry needs no clock')):
            with transaction.atomic():
                cancel_unstarted(
                    order, account_ids=[self.account.pk], reason='TRANSFERRED_UNSTARTED',
                )
        permit.refresh_from_db(); new_permit.refresh_from_db()
        self.assertEqual(permit.closed_at, observed)
        self.assertIsNone(new_permit.closed_at)
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 1)

    def test_no_permit_or_tracking_disabled_never_requires_a_clock(self):
        order = make_order(
            self.boss, provider=self.provider, provider_account=self.accounts[self.provider.pk],
            service=self.service, status='GRABBED', payment_status='PAID',
        )
        observed = timezone.now() + timedelta(minutes=17)
        with patch('django.utils.timezone.now', return_value=observed), \
             patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('no permit must not sample')):
            result = transition(order.pk, Order.Status.CANCELLED, reason='无资格取消')
        self.assertEqual((result.status, result.cancelled_at), ('CANCELLED', observed))

        second = make_order(
            self.boss, provider=self.provider, provider_account=self.accounts[self.provider.pk],
            service=self.service, status='GRABBED', payment_status='PAID',
        )
        permit = RefundDepositRecoveryPermit.objects.create(
            profile=self.provider.escort_profile, account=self.accounts[self.provider.pk], order=second,
        )
        with override_settings(
                REFUND_DEPOSIT_DEFICIT_ENABLED=False,
                REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=False,
                REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=False):
            with patch('wallet.refund_recovery_order.require_clock',
                       side_effect=ClockUnavailable('disabled tracking must not sample')):
                result = transition(second.pk, Order.Status.CANCELLED, reason='功能关闭取消')
        self.assertEqual(result.status, 'CANCELLED')
        permit.refresh_from_db()
        self.assertIsNone(permit.closed_at)


@override_settings(
    REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
)
class CancelUnstartedMultiStoreClockTests(TestCase):
    databases = {'default', 'store_b'}

    def build(self, store):
        with store_context(store):
            self.boss = make_user()
            self.provider = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
            self.account = get_or_create_account_for_legacy_user(self.provider)
            self.profile = self.provider.escort_profile
            self.profile.account = self.account
            self.profile.save(update_fields=['account'])
            self.wallet = get_wallet(account=self.account, user=self.provider)
            self.platform = get_platform_wallet()
            self.platform.balance = 10000
            self.platform.save(update_fields=['balance'])
            self.order = make_completed_order(
                self.boss, self.provider, amount=10000,
                provider_account=self.account, payment_status='PAID',
            )
            deficit = deficit_fixture.RefundDepositDeficitTest.deficit(self, 200)
            order = make_order(
                self.boss, provider=self.provider, provider_account=self.account,
                status='GRABBED', payment_status='PAID', amount=10000,
            )
            permit = RefundDepositRecoveryPermit.objects.create(
                profile=self.profile, account=self.account, order=order,
            )
            return order.pk, permit.pk, deficit.pk, self.account.pk

    def test_same_numeric_ids_close_only_own_store_with_each_attested_cancel_time(self):
        first_store, second_store = resolve_store(), xinan_store()
        first = self.build(first_store)
        second = self.build(second_store)
        self.assertEqual(first, second)
        first_time = timezone.now() + timedelta(minutes=19)
        with store_context(first_store), patch('django.utils.timezone.now', return_value=first_time):
            order = transition(first[0], Order.Status.CANCELLED, reason='声浪可信取消')
            permit = RefundDepositRecoveryPermit.objects.get(pk=first[1])
            self.assertEqual((order.cancelled_at, permit.closed_at), (first_time, first_time))
        with store_context(second_store):
            self.assertEqual(Order.objects.get(pk=second[0]).status, 'GRABBED')
            self.assertIsNone(RefundDepositRecoveryPermit.objects.get(pk=second[1]).closed_at)
        second_time = first_time + timedelta(minutes=1)
        with store_context(second_store), patch('django.utils.timezone.now', return_value=second_time):
            order = transition(second[0], Order.Status.CANCELLED, reason='兴安可信取消')
            permit = RefundDepositRecoveryPermit.objects.get(pk=second[1])
            self.assertEqual((order.cancelled_at, permit.closed_at), (second_time, second_time))
        with store_context(first_store):
            self.assertEqual(RefundDepositRecoveryPermit.objects.get(pk=first[1]).closed_at, first_time)
            self.assertEqual(RefundDepositRecoveryPermit.objects.get(pk=first[1]).account_id, first[3])
