"""Previously frozen service income must honor unresolved, scoped aftersales."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import AfterSale, Order, OrderProvider
from orders.services import settle_order
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.db import transaction
from wallet.income import release_due_income
from wallet.models import IncomeHold, Transaction
from wallet.services import get_wallet


class HistoricalIncomeAftersaleFixture:
    def setUp(self):
        self.boss = make_user()
        self.customer = get_or_create_account_for_legacy_user(self.boss)
        self.players = [make_provider(deposit_mode='CASH'), make_provider(deposit_mode='CASH')]
        self.accounts = [get_or_create_account_for_legacy_user(player) for player in self.players]
        for player, account in zip(self.players, self.accounts):
            player.escort_profile.account = account
            player.escort_profile.save(update_fields=['account'])
        get_wallet(user=self.boss, account=self.customer)
        for player, account in zip(self.players, self.accounts):
            get_wallet(user=player, account=account)
        self.order = make_order(self.boss, service=make_service(price=10000), provider=self.players[0],
            customer_account=self.customer, provider_account=self.accounts[0], status='COMPLETED',
            payment_status='PAID', settlement_mode='COMPLETION', escort_mode='DOUBLE',
            provider_income=8000, shop_income=2000, completed_at=timezone.now())
        Order.objects.filter(pk=self.order.pk).update(income_hold_hours=72, service_clock_version=None)
        for player, account in zip(self.players, self.accounts):
            OrderProvider.objects.create(order=self.order, provider=player, provider_account=account,
                                         settlement_base=5000, provider_income=4000, commission_rate=20)
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=self.order.pk)
            self.assertEqual(set(settle_order(order)), {player.pk for player in self.players})
        self.holds = list(IncomeHold.objects.filter(income_transaction__order=self.order).order_by('wallet_id'))
        self.assertEqual(len(self.holds), 2)
        self.now = timezone.now()+timedelta(hours=73)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class HistoricalIncomeAftersaleTest(HistoricalIncomeAftersaleFixture, TestCase):
    def test_legacy_hold_waits_for_original_aftersale_deadline_without_resetting_hold(self):
        completed = self.order.completed_at
        early = completed + timedelta(hours=24)
        IncomeHold.objects.filter(pk__in=[hold.pk for hold in self.holds]).update(release_at=early)
        self.assertEqual(release_due_income(now=early), 0)
        self.assertEqual(release_due_income(now=completed+timedelta(hours=72)-timedelta(microseconds=1)), 0)
        self.assertEqual(release_due_income(now=completed+timedelta(hours=72)), 2)
        self.assertEqual(release_due_income(now=completed+timedelta(hours=72)), 0)
        for hold in self.holds:
            hold.refresh_from_db()
            self.assertEqual(hold.release_at, early)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='EARNINGS_RELEASE').count(), 2)

    def test_incomplete_clock_does_not_block_unrelated_income(self):
        early = self.order.completed_at + timedelta(hours=24)
        IncomeHold.objects.filter(pk__in=[hold.pk for hold in self.holds]).update(release_at=early)
        wallet=self.holds[0].wallet
        income=Transaction.objects.create(wallet=wallet, order=None, amount=1, tx_type='INCOME')
        other=IncomeHold.objects.create(wallet=wallet, income_transaction=income, amount=1, release_at=early)
        wallet.frozen_amount += 1; wallet.save(update_fields=['frozen_amount'])
        self.assertEqual(release_due_income(now=early, limit=1), 1)
        other.refresh_from_db()
        self.assertIsNotNone(other.released_at)
        self.assertFalse(IncomeHold.objects.filter(pk__in=[hold.pk for hold in self.holds],
                                                   released_at__isnull=False).exists())

    def test_stale_candidate_rechecks_original_aftersale_deadline(self):
        early = self.order.completed_at + timedelta(hours=24)
        hold = self.holds[0]
        IncomeHold.objects.filter(pk=hold.pk).update(release_at=early)
        with patch('wallet.income._due_income_candidates', return_value=[(hold.pk, hold.wallet_id, self.order.pk)]):
            self.assertEqual(release_due_income(now=early), 0)
        hold.refresh_from_db()
        self.assertIsNone(hold.released_at)

    def test_recorded_aftersale_hours_remain_authoritative(self):
        completed = self.order.completed_at
        Order.objects.filter(pk=self.order.pk).update(service_aftersale_hours=48)
        IncomeHold.objects.filter(pk__in=[hold.pk for hold in self.holds]).update(
            release_at=completed+timedelta(hours=24))
        self.assertEqual(release_due_income(now=completed+timedelta(hours=47)), 0)
        self.assertEqual(release_due_income(now=completed+timedelta(hours=48)), 2)

    def test_missing_completion_time_requires_review_before_release(self):
        Order.objects.filter(pk=self.order.pk).update(completed_at=None)
        self.assertEqual(release_due_income(now=self.now), 0)
        self.assertFalse(IncomeHold.objects.filter(pk__in=[hold.pk for hold in self.holds],
                                                   released_at__isnull=False).exists())

    def test_only_affected_player_waits_and_cannot_starve_other_player(self):
        case = AfterSale.objects.create(order=self.order, account=self.customer, reason='核实第一位陪玩',
            status='OPEN', affected_provider_account_ids=[self.accounts[0].pk])
        self.assertEqual(release_due_income(now=self.now, limit=1), 1)
        for hold in self.holds:
            hold.refresh_from_db()
        self.assertIsNone(self.holds[0].released_at)
        self.assertIsNotNone(self.holds[1].released_at)
        self.assertEqual(release_due_income(now=self.now, limit=1), 0)
        old_deadline = self.holds[0].release_at
        case.status='RESOLVED'; case.save(update_fields=['status'])
        self.assertEqual(release_due_income(now=self.now, limit=1), 1)
        self.holds[0].refresh_from_db()
        self.assertEqual(self.holds[0].release_at, old_deadline)
        self.assertEqual(release_due_income(now=self.now, limit=1), 0)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='EARNINGS_RELEASE').count(), 2)

    def test_stale_scan_rechecks_unscoped_case_before_unlocking_money(self):
        AfterSale.objects.create(order=self.order, account=self.customer, reason='整单仍在售后', status='OPEN')
        hold=self.holds[0]
        with patch('wallet.income._due_income_candidates', return_value=[(hold.pk, hold.wallet_id, self.order.pk)]):
            self.assertEqual(release_due_income(now=self.now), 0)
        hold.refresh_from_db()
        self.assertIsNone(hold.released_at)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='EARNINGS_RELEASE').exists())

    def test_more_than_one_page_of_blocked_holds_does_not_starve_later_income(self):
        AfterSale.objects.create(order=self.order, account=self.customer, reason='整单售后', status='OPEN')
        wallet = self.holds[0].wallet
        Transaction.objects.bulk_create([
            Transaction(wallet=wallet, order=self.order, amount=1, tx_type='INCOME', tx_no=f'TXPAGE{index:04}')
            for index in range(500)
        ])
        blocked = list(Transaction.objects.filter(tx_no__startswith='TXPAGE').order_by('pk'))
        eligible = Transaction.objects.create(wallet=wallet, order=None, amount=1, tx_type='INCOME')
        IncomeHold.objects.bulk_create([
            IncomeHold(wallet=wallet, income_transaction=income, amount=1, release_at=self.holds[0].release_at)
            for income in [*blocked, eligible]
        ])
        wallet.frozen_amount += 501
        wallet.save(update_fields=['frozen_amount'])
        self.assertEqual(release_due_income(now=self.now, limit=1), 1)
        self.assertIsNotNone(IncomeHold.objects.get(income_transaction=eligible).released_at)
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=self.order,
                                                   released_at__isnull=False).exists())


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnlessDBFeature('has_select_for_update')
class HistoricalIncomeAftersaleRaceTest(HistoricalIncomeAftersaleFixture, TransactionTestCase):
    def test_committed_aftersale_is_seen_after_waiting_for_order_lock(self):
        case_locked, commit_case, candidate_seen, scan_finished = Event(), Event(), Event(), Event()
        hold = self.holds[0]
        candidate = [(hold.pk, hold.wallet_id, self.order.pk)]

        def create_case():
            close_old_connections()
            try:
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=self.order.pk)
                    AfterSale.objects.create(order=order, account_id=self.customer.pk, reason='履约售后',
                                             status='OPEN', affected_provider_account_ids=[self.accounts[0].pk])
                    case_locked.set()
                    if not commit_case.wait(15):
                        raise AssertionError('After-sale transaction was not released')
            finally:
                close_old_connections()

        def stale_candidate(*args):
            candidate_seen.set()
            return candidate

        def scan():
            close_old_connections()
            try:
                return release_due_income(now=self.now)
            finally:
                scan_finished.set()
                close_old_connections()

        with patch('wallet.income._due_income_candidates', side_effect=stale_candidate), ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(create_case)
            try:
                self.assertTrue(case_locked.wait(10))
                scanner = pool.submit(scan)
                self.assertTrue(candidate_seen.wait(10))
                self.assertFalse(scan_finished.wait(1), 'Scanner must wait for the order lock')
            finally:
                commit_case.set()
            writer.result(timeout=15)
            self.assertEqual(scanner.result(timeout=15), 0)
        hold.refresh_from_db()
        self.assertIsNone(hold.released_at)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='EARNINGS_RELEASE').exists())

    def test_changed_completion_time_is_seen_after_waiting_for_order_lock(self):
        locked, commit_change, candidate_seen, scan_finished = Event(), Event(), Event(), Event()
        hold = self.holds[0]
        candidate = [(hold.pk, hold.wallet_id, self.order.pk)]

        def change_completion():
            close_old_connections()
            try:
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=self.order.pk)
                    order.completed_at = self.now-timedelta(hours=1)
                    order.save(update_fields=['completed_at'])
                    locked.set()
                    if not commit_change.wait(15):
                        raise AssertionError('Completion-time transaction was not released')
            finally:
                close_old_connections()

        def stale_candidate(*args):
            candidate_seen.set()
            return candidate

        def scan():
            close_old_connections()
            try:
                return release_due_income(now=self.now)
            finally:
                scan_finished.set()
                close_old_connections()

        with patch('wallet.income._due_income_candidates', side_effect=stale_candidate), ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(change_completion)
            try:
                self.assertTrue(locked.wait(10))
                scanner = pool.submit(scan)
                self.assertTrue(candidate_seen.wait(10))
                self.assertFalse(scan_finished.wait(1), 'Scanner must wait for the order lock')
            finally:
                commit_change.set()
            writer.result(timeout=15)
            self.assertEqual(scanner.result(timeout=15), 0)
        hold.refresh_from_db()
        self.assertIsNone(hold.released_at)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class HistoricalIncomeAftersaleStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_and_wallet_ids_cannot_affect_another_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        rows = []
        for index, store in enumerate((resolve_store(), xinan_store())):
            with store_context(store):
                HistoricalIncomeAftersaleFixture.setUp(self)
                if index:
                    AfterSale.objects.create(order=self.order, account=self.customer, reason='本店售后', status='OPEN')
                rows.append((store, self.order.pk, [hold.pk for hold in self.holds], self.now))
        self.assertEqual(rows[0][1:3], rows[1][1:3])
        with store_context(rows[0][0]):
            self.assertEqual(release_due_income(now=rows[0][3]), 2)
        with store_context(rows[1][0]):
            self.assertEqual(release_due_income(now=rows[1][3]), 0)
            self.assertFalse(IncomeHold.objects.filter(pk__in=rows[1][2], released_at__isnull=False).exists())
            self.assertFalse(Transaction.objects.filter(order_id=rows[1][1], tx_type='EARNINGS_RELEASE').exists())

    def test_legacy_aftersale_deadlines_are_evaluated_in_the_current_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        rows = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                HistoricalIncomeAftersaleFixture.setUp(self)
                early=self.order.completed_at+timedelta(hours=24)
                IncomeHold.objects.filter(pk__in=[hold.pk for hold in self.holds]).update(release_at=early)
                rows.append((store, self.order.pk, [hold.pk for hold in self.holds], early, self.now))
        self.assertEqual(rows[0][1:3], rows[1][1:3])
        with store_context(rows[0][0]):
            self.assertEqual(release_due_income(now=rows[0][4]), 2)
        with store_context(rows[1][0]):
            self.assertEqual(release_due_income(now=rows[1][3]), 0)
            self.assertFalse(IncomeHold.objects.filter(pk__in=rows[1][2], released_at__isnull=False).exists())
            self.assertEqual(release_due_income(now=rows[1][4]), 2)
