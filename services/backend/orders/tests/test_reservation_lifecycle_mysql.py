"""Reservation creation competes with provider closure on disposable MySQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.db.models import F
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts import provider_lifecycle
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from orders.models import Reservation, ServiceItem
from orders.models import Order, ReservationSeat
from orders.tests import test_game_commission_payment as game_fixture
from orders.tests.factories import make_provider, make_service
from orders.tests.test_reservation_api import ReservationApiTestBase, slot
from orders.reservation_services import create_reservation
from orders.reservation_team import create_team_reservation, decide
from stores.context import store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.test_deposit_lifecycle_mysql import require_isolated
from users.models import EscortProfile


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationLifecycleMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True
    create_payload = ReservationApiTestBase.create_payload

    def setUp(self):
        require_isolated(self)
        self.store = xinan_store()
        self.client = APIClient()
        with store_context(self.store):
            ReservationApiTestBase.setUp(self)
            token, _ = issue_account_tokens(self.boss_account)
            self.payload = self.create_payload()
        self.client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)

    def test_reservation_rechecks_membership_after_wait_before_service_lock(self):
        entered = Event()
        actual = provider_lifecycle.lock_provider_operations

        def observe(accounts):
            entered.set()
            return actual(accounts)

        def post():
            close_old_connections()
            try:
                return self.client.post('/api/orders/reservations/', self.payload, format='json')
            finally:
                close_old_connections()

        with patch('club_accounts.provider_lifecycle.lock_provider_operations', side_effect=observe):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with store_context(self.store), transaction.atomic():
                    provider_lifecycle.lock_provider_closure(self.provider_account)
                    future = pool.submit(post)
                    self.assertTrue(entered.wait(15))
                    self.assertFalse(future.done())
                    ServiceItem.objects.select_for_update(nowait=True).get(pk=self.service.pk)
                    ClubAccount.objects.filter(pk=self.provider_account.pk).update(
                        license_membership_active=False, business_cycle=F('business_cycle') + 1)
                result = future.result(timeout=30)
        self.assertEqual(result.data['code'], 400, result.data)
        self.assertIn('身份已变化', result.data['msg'])
        with store_context(self.store):
            self.assertFalse(Reservation.objects.exists())


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    GAME_COMMISSION_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class SelectedTeamLifecycleMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True
    link = game_fixture.GameCommissionPaymentTest.link
    auth = game_fixture.GameCommissionPaymentTest.auth

    def setUp(self):
        require_isolated(self)
        self.store = xinan_store()
        self.client = APIClient(HTTP_HOST='xa.esports.club')
        with store_context(self.store):
            game_fixture.GameCommissionPaymentTest.setUp(self)
            self.service.billing_provider_count = 2
            self.service.save(update_fields=['billing_provider_count'])

    def test_team_checkout_rechecks_both_before_service_and_wallet_locks(self):
        entered = Event()
        actual = provider_lifecycle.lock_provider_operations

        def observe(accounts):
            entered.set()
            return actual(accounts)

        def post():
            close_old_connections()
            try:
                return self.client.post('/api/orders/create/', {'service_id': self.service.pk,
                    'provider_ids': [account.pk for account in self.accounts],
                    'client_request_id': 'lifecycle-team-checkout'}, format='json')
            finally:
                close_old_connections()

        with patch('club_accounts.provider_lifecycle.lock_provider_operations', side_effect=observe):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with store_context(self.store), transaction.atomic():
                    provider_lifecycle.lock_provider_closure(self.accounts[1])
                    future = pool.submit(post)
                    self.assertTrue(entered.wait(15))
                    self.assertFalse(future.done())
                    ServiceItem.objects.select_for_update(nowait=True).get(pk=self.service.pk)
                    ClubAccount.objects.filter(pk=self.accounts[1].pk).update(
                        license_membership_active=False, business_cycle=F('business_cycle') + 1)
                result = future.result(timeout=30)
        self.assertEqual(result.status_code, 409, result.data)
        with store_context(self.store):
            self.wallet.refresh_from_db()
            self.assertEqual(self.wallet.balance, 100000)
            self.assertFalse(Order.objects.exists())


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ReservationDecisionLifecycleMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True

    def setUp(self):
        require_isolated(self)
        self.store = xinan_store()
        self.client = APIClient()
        with store_context(self.store):
            ReservationApiTestBase.setUp(self)
            self.members = [self.provider_account]
            for _ in range(2):
                user = make_provider(deposit_mode='EARNINGS')
                account = get_or_create_account_for_legacy_user(user)
                link_legacy_relations(user, account)
                EscortProfile.objects.filter(user=user).update(account=account)
                self.members.append(account)
            self.team_service = make_service(price=10000, billing_provider_count=2, price_unit='小时')
            self.start = slot(hour=10)
            self.end = self.start + timedelta(minutes=90)
            self.tokens = {a.pk: issue_account_tokens(a)[0] for a in [self.boss_account, *self.members]}

    def client_for(self, account):
        return APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + self.tokens[account.pk])

    def team(self):
        with store_context(self.store):
            return create_team_reservation(customer_account=self.boss_account, customer=self.boss,
                providers=self.members[:2], service=self.team_service, start_time=self.start, end_time=self.end)

    def race(self, target, action, *, plural, service=None):
        entered = Event()
        name = 'lock_provider_operations' if plural else 'lock_provider_operation'
        actual = getattr(provider_lifecycle, name)

        def observe(value):
            entered.set()
            return actual(value)

        def post():
            close_old_connections()
            try:
                return action()
            finally:
                close_old_connections()

        with patch('club_accounts.provider_lifecycle.' + name, side_effect=observe):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with store_context(self.store), transaction.atomic():
                    provider_lifecycle.lock_provider_closure(target)
                    future = pool.submit(post)
                    self.assertTrue(entered.wait(15), 'request did not reach lifecycle lock')
                    self.assertFalse(future.done())
                    if service is not None:
                        ServiceItem.objects.select_for_update(nowait=True).get(pk=service.pk)
                    ClubAccount.objects.filter(pk=target.pk).update(
                        license_membership_active=False, business_cycle=F('business_cycle') + 1)
                return future.result(timeout=30)

    def test_single_confirmation_rechecks_but_rejection_can_complete(self):
        with store_context(self.store):
            booking = create_reservation(customer_account=self.boss_account, customer=self.boss,
                provider_account=self.members[0], service=self.service, start_time=self.start, end_time=self.end)
        client = self.client_for(self.members[0])
        path = f'/api/orders/reservations/{booking.pk}/'
        result = self.race(self.members[0], lambda: client.post(path+'confirm/', {}, format='json'), plural=False)
        self.assertEqual(result.data['code'], 400, result.data)
        with store_context(self.store):
            booking.refresh_from_db()
            self.assertEqual(booking.status, Reservation.Status.PENDING)
            ClubAccount.objects.filter(pk=self.members[0].pk).update(license_membership_active=True)
            self.members[0].refresh_from_db()
            token, _ = issue_account_tokens(self.members[0])
        client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+token)
        result = self.race(self.members[0], lambda: client.post(path+'reject/', {}, format='json'), plural=False)
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.store):
            booking.refresh_from_db()
            self.assertEqual(booking.status, Reservation.Status.REJECTED)

    def test_team_confirmation_rechecks_actor(self):
        booking = self.team()
        client = self.client_for(self.members[1])
        result = self.race(self.members[1], lambda: client.post(
            f'/api/orders/reservations/{booking.pk}/confirm/', {}, format='json'), plural=True)
        self.assertEqual(result.data['code'], 400, result.data)
        with store_context(self.store):
            self.assertEqual(booking.seats.get(number=2).status, ReservationSeat.Status.PENDING)

    def test_team_rejection_completes_after_actor_membership_changes(self):
        booking = self.team()
        with store_context(self.store):
            decide(booking.pk, self.members[0], accept=True)
        client = self.client_for(self.members[1])
        result = self.race(self.members[1], lambda: client.post(
            f'/api/orders/reservations/{booking.pk}/reject/', {}, format='json'), plural=True)
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.store):
            self.assertEqual(booking.seats.get(number=1).status, ReservationSeat.Status.CONFIRMED)
            self.assertEqual(booking.seats.get(number=2).status, ReservationSeat.Status.VACANT)

    def test_team_replacement_rechecks_new_member_before_service_lock(self):
        booking = self.team()
        with store_context(self.store):
            decide(booking.pk, self.members[0], accept=True)
            decide(booking.pk, self.members[1], accept=False)
        client = self.client_for(self.boss_account)
        result = self.race(self.members[2], lambda: client.post(
            f'/api/orders/reservations/{booking.pk}/replace-provider/',
            {'seat_number': 2, 'provider_account_id': self.members[2].pk}, format='json'),
            plural=True, service=self.team_service)
        self.assertEqual(result.data['code'], 400, result.data)
        with store_context(self.store):
            seat = booking.seats.get(number=2)
            self.assertEqual(seat.status, ReservationSeat.Status.VACANT)
            self.assertIsNone(seat.provider_account_id)
