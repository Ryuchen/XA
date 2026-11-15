"""Experimental grab intent APIs serialize with lifecycle on disposable MySQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, get_ident
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.db.models import F
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts import provider_lifecycle
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from console.grab_config import save_grab_window
from orders.models import Order, OrderGrabReceiptProbe, OrderGrabTeamDraft
from orders.tests import test_game_commission_payment as game_fixture
from orders.tests import test_grab_team_flow as team_fixture
from orders.tests import test_grab_single_flow as single_fixture
from stores.context import store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.test_deposit_lifecycle_mysql import require_isolated


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True,
    GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabTeamLifecycleMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True
    link = game_fixture.GameCommissionPaymentTest.link
    auth = game_fixture.GameCommissionPaymentTest.auth
    create = game_fixture.GameCommissionPaymentTest.create
    prepare = team_fixture.GrabTeamFlowTest.prepare
    invite = team_fixture.GrabTeamFlowTest.invite

    def setUp(self):
        require_isolated(self)
        self.store = xinan_store()
        self.client = APIClient(HTTP_HOST='xa.esports.club')
        with store_context(self.store):
            game_fixture.GameCommissionPaymentTest.setUp(self)
            # A longer test window leaves time for the real lock competition.
            with patch('orders.tests.test_grab_team_flow.save_grab_window',
                       side_effect=lambda seconds, version, audit: save_grab_window(
                           2 if 'snapshot' in self._testMethodName else 300, version, audit)):
                self.prepare()
            self.tokens = {a.pk: issue_account_tokens(a)[0] for a in [self.boss_account, *self.accounts]}

    def api(self, account):
        return APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + self.tokens[account.pk])

    def race(self, target, action, *, allocation=False):
        entered = Event()
        actual = provider_lifecycle.lock_provider_operations

        def observe(accounts):
            entered.set()
            return actual(accounts)

        def post():
            close_old_connections()
            try:
                return action()
            finally:
                close_old_connections()

        hook = ('orders.grab_allocation_lifecycle.lock_provider_operations' if allocation
                else 'club_accounts.provider_lifecycle.lock_provider_operations')
        with patch(hook, side_effect=observe):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with store_context(self.store), transaction.atomic():
                    provider_lifecycle.lock_provider_closure(target)
                    future = pool.submit(post)
                    self.assertTrue(entered.wait(15), 'grab did not reach the lifecycle lock')
                    db = connections['store_b']
                    deadline, waiting = monotonic() + 8, False
                    while monotonic() < deadline:
                        with db.cursor() as cursor:
                            cursor.execute('SHOW FULL PROCESSLIST')
                            waiting = any(row[3] == db.settings_dict['NAME'] and row[4] == 'Query' and row[7]
                                and 'CLUB_ACCOUNTS_PROVIDERLIFECYCLELOCK' in str(row[7]).upper()
                                and 'LOCK IN SHARE MODE' in str(row[7]).upper() for row in cursor.fetchall())
                        if waiting:
                            break
                        sleep(.05)
                    self.assertTrue(waiting, 'grab did not wait for the lifecycle lock')
                    Order.objects.select_for_update(nowait=True).get(pk=self.order.pk)
                    ClubAccount.objects.filter(pk=target.pk).update(
                        license_membership_active=False, business_cycle=F('business_cycle') + 1)
                return future.result(timeout=30)

    def test_invite_rechecks_partner_before_creating_receipt_or_team(self):
        client = self.api(self.accounts[0])
        result = self.race(self.accounts[1], lambda: client.post(
            f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'life-invite'}, format='json'))
        self.assertEqual(result.status_code, 400, result.data)
        with store_context(self.store):
            self.assertFalse(OrderGrabTeamDraft.objects.exists())
            self.assertFalse(OrderGrabReceiptProbe.objects.exists())

    def test_confirmation_rechecks_captain_and_boss_can_cancel(self):
        with store_context(self.store):
            team_id = self.invite()
        client = self.api(self.accounts[1])
        result = self.race(self.accounts[0], lambda: client.post(
            f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id': 'life-confirm'}, format='json'))
        self.assertEqual(result.status_code, 400, result.data)
        with store_context(self.store):
            self.assertEqual(OrderGrabTeamDraft.objects.get(pk=team_id).status, 'INVITING')
            self.assertFalse(OrderGrabReceiptProbe.objects.filter(kind='CONFIRM').exists())
        result = self.api(self.boss_account).post(f'/api/orders/orders/{self.order.pk}/cancel/',
            {'client_request_id':'life-boss-cancel'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.store):
            self.order.refresh_from_db()
            self.wallet.refresh_from_db()
            self.assertEqual((self.order.status, self.wallet.balance), ('CANCELLED', 100000))

    def test_snapshot_records_each_team_member_and_blocks_unintegrated_assignment(self):
        from orders.grab_team_flow import resolve
        with store_context(self.store):
            team_id = self.invite()
        result = self.api(self.accounts[1]).post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id':'snapshot-team-confirm'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.store):
            receipts = {r.kind: r for r in OrderGrabReceiptProbe.objects.filter(kind__in=['INVITE', 'CONFIRM'])}
            for kind, account in [('INVITE', self.accounts[0]), ('CONFIRM', self.accounts[1])]:
                self.assertEqual((receipts[kind].actor_id, receipts[kind].actor_business_cycle,
                                  receipts[kind].actor_cycle_guarded), (account.pk, account.business_cycle, True))
            ClubAccount.objects.filter(pk=self.accounts[0].pk).update(business_cycle=F('business_cycle') + 1)
            sleep(2.1)
            self.assertEqual(resolve(self.order.pk), 'REOPENED')
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_snapshot_valid_team_assigns_both_members(self):
        from orders.grab_team_flow import resolve
        with store_context(self.store):
            team_id = self.invite()
        result = self.api(self.accounts[1]).post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id':'snapshot-valid-team-confirm'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        sleep(2.1)
        with store_context(self.store):
            self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.GRABBED)
            self.assertEqual(set(self.order.providers.values_list('provider_account_id', flat=True)),
                             {a.pk for a in self.accounts})

    def test_team_withdrawal_after_snapshot_requires_retry(self):
        with store_context(self.store):
            team_id = self.invite()
        client = self.api(self.accounts[1])
        confirmed = client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id':'allocation-confirm'}, format='json')
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        def withdraw():
            result = client.post(f'/api/orders/grab-team/invitations/{team_id}/withdraw/',
                {'client_request_id':'allocation-withdraw'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        outcome = GrabSingleLifecycleMySQLTest._pause_allocation_after_snapshot(self, withdraw, team=True)
        self.assertEqual(outcome, 'WAIT_LIFECYCLE_RETRY')
        with store_context(self.store):
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.PENDING)
            self.assertEqual(OrderGrabTeamDraft.objects.get(pk=team_id).status, 'WITHDRAWN')


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
    GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabSingleLifecycleMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True
    link = game_fixture.GameCommissionPaymentTest.link
    auth = game_fixture.GameCommissionPaymentTest.auth
    create = game_fixture.GameCommissionPaymentTest.create
    prepare = single_fixture.GrabSingleFlowTest.prepare
    api = GrabTeamLifecycleMySQLTest.api
    race = GrabTeamLifecycleMySQLTest.race

    def setUp(self):
        require_isolated(self)
        self.store = xinan_store()
        self.client = APIClient(HTTP_HOST='xa.esports.club')
        with store_context(self.store):
            game_fixture.GameCommissionPaymentTest.setUp(self)
            with patch('orders.tests.test_grab_single_flow.save_grab_window',
                       side_effect=lambda seconds, version, audit: save_grab_window(
                           2 if 'snapshot' in self._testMethodName else 300, version, audit)):
                self.prepare()
            self.auth(self.boss_account)
            result = self.create(dispatch_mode='AUTO_GRAB')
            self.assertEqual(result.data['code'], 0, result.data)
            self.order = Order.objects.get(pk=result.data['data']['id'])
            self.tokens = {a.pk: issue_account_tokens(a)[0] for a in [self.boss_account, *self.accounts]}

    def test_single_grab_rechecks_member_before_receipt(self):
        client = self.api(self.accounts[0])
        result = self.race(self.accounts[0], lambda: client.post(
            f'/api/orders/orders/{self.order.pk}/grab/',
            {'team_size': 1, 'client_request_id': 'life-single'}, format='json'))
        self.assertEqual(result.status_code, 400, result.data)
        with store_context(self.store):
            self.assertFalse(OrderGrabReceiptProbe.objects.exists())

    def test_snapshot_records_actor_cycle_and_blocks_unintegrated_assignment(self):
        from orders.grab_receipt_probe import locked_actor_membership_matches
        from orders.grab_single_flow import resolve
        client = self.api(self.accounts[0])
        data = {'team_size':1, 'client_request_id':'snapshot-single'}
        result = client.post(f'/api/orders/orders/{self.order.pk}/grab/', data, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        replay = client.post(f'/api/orders/orders/{self.order.pk}/grab/', data, format='json')
        self.assertEqual(replay.data['code'], 0, replay.data)
        with store_context(self.store), transaction.atomic():
            receipt = OrderGrabReceiptProbe.objects.get(kind='SINGLE')
            self.assertEqual((receipt.actor_business_cycle, receipt.actor_cycle_guarded),
                             (self.accounts[0].business_cycle, True))
            provider_lifecycle.lock_provider_operation(self.accounts[0])
            self.assertTrue(locked_actor_membership_matches(receipt))
            receipt.actor_cycle_guarded = False
            self.assertFalse(locked_actor_membership_matches(receipt))
            receipt.actor_cycle_guarded, receipt.actor_business_cycle = True, None
            self.assertFalse(locked_actor_membership_matches(receipt))
            receipt.actor_business_cycle = self.accounts[0].business_cycle + 1
            self.assertFalse(locked_actor_membership_matches(receipt))
        sleep(2.1)
        with store_context(self.store):
            OrderGrabReceiptProbe.objects.filter(pk=receipt.pk).update(actor_business_cycle=None)
            self.assertEqual(resolve(self.order.pk), 'WAIT_LIFECYCLE_FACTS')
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.PENDING)
            OrderGrabReceiptProbe.objects.filter(pk=receipt.pk).update(actor_business_cycle=self.accounts[0].business_cycle)
            self.assertEqual(resolve(self.order.pk), 'ASSIGNED')

    def test_snapshot_expired_invalid_candidate_cancels(self):
        from orders.grab_receipt_probe import _db_clock
        from orders.grab_single_flow import resolve
        from orders.models import OrderGrabSingleRoundState
        self._first_candidate()
        with store_context(self.store):
            ClubAccount.objects.filter(pk=self.accounts[0].pk).update(
                license_membership_active=False, business_cycle=F('business_cycle') + 1)
        sleep(2.1)
        with store_context(self.store):
            state = OrderGrabSingleRoundState.objects.select_related('round').get(round__pool__order=self.order)
            _, precision, driver = _db_clock()
            after_total = state.round.total_deadline + timedelta(seconds=1)
            # The waiting deadline is normally at least a minute; only the DB
            # clock observation is advanced to exercise this boundary quickly.
            with patch('orders.grab_receipt_probe._db_clock', return_value=(after_total, precision, driver)):
                self.assertEqual(resolve(self.order.pk), 'CANCELLED')
            self.order.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((self.order.status, self.wallet.balance), (Order.Status.CANCELLED, 100000))

    def _pause_allocation_after_snapshot(self, intervene, *, team=False):
        from orders import grab_allocation_lifecycle
        if team:
            from orders.grab_team_flow import resolve
        else:
            from orders.grab_single_flow import resolve
        captured, resume = Event(), Event()
        actual = grab_allocation_lifecycle.lock
        caller = get_ident()

        def paused(snapshot):
            if get_ident() == caller:
                return actual(snapshot)
            captured.set()
            if not resume.wait(15):
                raise AssertionError('allocation test did not resume')
            return actual(snapshot)

        def run():
            close_old_connections()
            try:
                with store_context(self.store):
                    return resolve(self.order.pk)
            finally:
                close_old_connections()

        with patch('orders.grab_allocation_lifecycle.lock', side_effect=paused):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(run)
                try:
                    self.assertTrue(captured.wait(15), 'allocator did not reach candidate snapshot')
                    intervene()
                finally:
                    resume.set()
                return future.result(timeout=30)

    def _first_candidate(self):
        result = self.api(self.accounts[0]).post(f'/api/orders/orders/{self.order.pk}/grab/',
            {'team_size':1, 'client_request_id':'allocation-first'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)

    def test_candidate_added_after_snapshot_requires_retry(self):
        self._first_candidate()
        def second():
            result = self.api(self.accounts[1]).post(f'/api/orders/orders/{self.order.pk}/grab/',
                {'team_size':1, 'client_request_id':'allocation-second'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(self._pause_allocation_after_snapshot(second), 'WAIT_LIFECYCLE_RETRY')
        with store_context(self.store):
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.PENDING)
            self.assertEqual(OrderGrabReceiptProbe.objects.filter(kind='SINGLE').count(), 2)

    def test_cancel_registered_after_snapshot_wins(self):
        from orders.grab_receipt_probe import record_intent
        from orders.models import OrderGrabSingleRoundState
        self._first_candidate()
        def cancel():
            # Persist the actual cancellation intent between the two allocator
            # transactions, without independently running its resolver.
            with store_context(self.store):
                state = OrderGrabSingleRoundState.objects.get(round__pool__order=self.order)
                result = record_intent(state.round_id, self.boss_account.pk, 'BOSS_CANCEL',
                    'allocation-cancel', {'reason':'客户取消'}, single_flow=True)
                self.assertEqual(result.state, 'BEFORE')
        self.assertEqual(self._pause_allocation_after_snapshot(cancel), 'CANCELLED')
        with store_context(self.store):
            self.order.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((self.order.status, self.wallet.balance), (Order.Status.CANCELLED, 100000))

    def test_member_changes_while_allocation_waits_requires_retry(self):
        from orders.grab_single_flow import resolve
        self._first_candidate()
        def run():
            with store_context(self.store):
                return resolve(self.order.pk)
        self.assertEqual(self.race(self.accounts[0], run, allocation=True), 'WAIT_LIFECYCLE_RETRY')
        with store_context(self.store):
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_committed_intent_finishes_evidence_after_membership_changes(self):
        from orders import grab_receipt_probe
        sampled, continue_evidence, second_lock = Event(), Event(), Event()
        clock, lock = grab_receipt_probe._db_clock, provider_lifecycle.lock_provider_operations
        clock_count, lock_count = [0], [0]

        def sample():
            value = clock()
            clock_count[0] += 1
            if clock_count[0] == 2:
                sampled.set()
                if not continue_evidence.wait(15):
                    raise AssertionError('evidence test did not resume')
            return value

        def observe(accounts):
            lock_count[0] += 1
            if lock_count[0] == 2:
                second_lock.set()
            return lock(accounts)

        def post():
            close_old_connections()
            try:
                return self.api(self.accounts[0]).post(f'/api/orders/orders/{self.order.pk}/grab/',
                    {'team_size': 1, 'client_request_id':'life-evidence'}, format='json')
            finally:
                close_old_connections()

        with patch('orders.grab_receipt_probe._db_clock', side_effect=sample), \
             patch('club_accounts.provider_lifecycle.lock_provider_operations', side_effect=observe):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(post)
                try:
                    self.assertTrue(sampled.wait(15), 'intent was not committed')
                    with store_context(self.store), transaction.atomic():
                        provider_lifecycle.lock_provider_closure(self.accounts[0])
                        continue_evidence.set()
                        self.assertTrue(second_lock.wait(15), 'evidence did not reach lifecycle lock')
                        db = connections['store_b']
                        deadline, waiting = monotonic() + 8, False
                        while monotonic() < deadline:
                            with db.cursor() as cursor:
                                cursor.execute('SHOW FULL PROCESSLIST')
                                waiting = any(row[3] == db.settings_dict['NAME'] and row[4] == 'Query' and row[7]
                                    and 'CLUB_ACCOUNTS_PROVIDERLIFECYCLELOCK' in str(row[7]).upper()
                                    and 'LOCK IN SHARE MODE' in str(row[7]).upper() for row in cursor.fetchall())
                            if waiting:
                                break
                            sleep(.05)
                        self.assertTrue(waiting, 'evidence was not blocked by lifecycle lock')
                        Order.objects.select_for_update(nowait=True).get(pk=self.order.pk)
                        ClubAccount.objects.filter(pk=self.accounts[0].pk).update(
                            license_membership_active=False, business_cycle=F('business_cycle') + 1)
                finally:
                    continue_evidence.set()
                result = future.result(timeout=30)
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.store):
            row = OrderGrabReceiptProbe.objects.get(kind='SINGLE')
            self.assertEqual(row.state, 'BEFORE')
            self.assertEqual(row.pass_evidence.state, 'KNOWN')
