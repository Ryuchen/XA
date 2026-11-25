"""Closure guards on real new-order create views, with no payment/state-machine hooks."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.services import config
from orders.models import Order, Reservation
from orders.tests.factories import (
    make_console_user, make_order, make_provider, make_service,
    make_service_category, make_user, make_wallet,
)
from stores.closure_clock import CLOCK_SOURCE, ControlClockEvidence, ControlClockUnavailable
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, Store, StoreClosureEvent, StoreClosureNotice
from stores.tests_hosts import xinan_store
from users.models import EscortProfile
from wallet.models import Wallet


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
    GRAB_SINGLE_WINDOW_ENABLED=True,
    GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
    GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    PROVIDER_WECHAT_GATE_ENABLED=False,
    ALLOWED_HOSTS=['*'],
)
class ClosureNewOrderEntryTests(APITestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.closure_actor = PlatformAccount.objects.create(username='closure-order-test-actor')
        self.clock_now = datetime(2026, 11, 25, 4, 0, tzinfo=datetime_timezone.utc)
        self.clock_error = None

        def explicit_clock():
            if self.clock_error is not None:
                raise self.clock_error
            return ControlClockEvidence(self.clock_now, CLOCK_SOURCE, 'sqlite-test:default')

        patcher = patch('stores.closure.read_control_clock', side_effect=explicit_clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fixtures = {
            self.first.slug: self.build_fixture(self.first, 'sl.esports.club'),
            self.second.slug: self.build_fixture(self.second, 'xa.esports.club'),
        }
        self.assertEqual(self.fixtures[self.first.slug].boss_account.pk,
                         self.fixtures[self.second.slug].boss_account.pk)
        self.assertEqual([row.pk for row in self.fixtures[self.first.slug].provider_accounts],
                         [row.pk for row in self.fixtures[self.second.slug].provider_accounts])

    def pair_account(self, user, account_id, account_type):
        # Preallocate the same business-account number in each independent DB.
        ClubAccount.objects.create(
            id=account_id, username=user.username, password=user.password,
            nickname=user.nickname, account_type=account_type,
        )
        account = get_or_create_account_for_legacy_user(user)
        self.assertEqual(account.pk, account_id)
        return account

    def build_fixture(self, store, host):
        with store_context(store):
            boss = make_user()
            providers = [
                make_provider(deposit_mode='EARNINGS', status=EscortProfile.Status.AVAILABLE),
                make_provider(deposit_mode='EARNINGS', status=EscortProfile.Status.AVAILABLE),
            ]
            staff = make_console_user(['order:view', 'order:dispatch', 'order:cancel'])
            boss_account = self.pair_account(boss, 910001, ClubAccount.AccountType.BOSS)
            provider_accounts = [
                self.pair_account(providers[0], 910002, ClubAccount.AccountType.PROVIDER),
                self.pair_account(providers[1], 910003, ClubAccount.AccountType.PROVIDER),
            ]
            staff_account = self.pair_account(staff, 910004, ClubAccount.AccountType.STAFF)
            staff.admin_membership.account = staff_account
            staff.admin_membership.save(update_fields=['account'])
            for user, account in zip(providers, provider_accounts):
                EscortProfile.objects.filter(user=user).update(account=account)
                Wallet.objects.filter(user=user).update(account=account)
            make_wallet(boss, balance=1_000_000)
            Wallet.objects.filter(user=boss).update(account=boss_account)
            from engagement.models import PointFundingLot
            PointFundingLot.objects.create(
                account=boss_account, source_key=f'closure-fixture:{store.slug}',
                kind=PointFundingLot.Kind.PREPAID, remaining=1_000_000,
            )
            service = make_service(name='闭店守卫普通服务', price=1000, commission_rate=20)
            team_service = make_service(
                name='闭店守卫双陪服务', price=1000, billing_provider_count=2,
                fulfillment_provider_count=2, price_unit='小时', commission_rate=20,
            )
            category = make_service_category(name='闭店守卫礼物', is_gift=True)
            gift = make_service(name='闭店守卫直接礼物', price=500, service_category=category, commission_rate=20)
            config()
            from console.reservation_deadlines import save_deadlines
            from console.room_confirmation_config import save_room_confirmation
            from console.grab_config import save_grab_window
            save_deadlines(30, 60, 0, lambda *_: None)
            save_room_confirmation(60, 0, lambda *_: None)
            save_grab_window(30, 0, lambda *_: None)

            def client(account, client_host=host):
                return APIClient(
                    HTTP_HOST=client_host,
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0],
                )

            return SimpleNamespace(
                store=store, boss=boss, providers=providers, staff=staff,
                boss_account=boss_account, provider_accounts=provider_accounts,
                staff_account=staff_account, service=service, team_service=team_service,
                gift=gift, boss_client=client(boss_account),
                provider_clients=[client(row) for row in provider_accounts],
                staff_client=client(staff_account, 'admin.esports.club'),
            )

    def f(self, store):
        return self.fixtures[store.slug]

    def close(self, store):
        notice = StoreClosureNotice.objects.create(
            store=store, reason_category='OPERATOR_REQUEST', reason='内部闭店秘密原因',
            applicant=self.closure_actor, started_at=self.clock_now,
            notice_policy_version='SA02_NOTICE_30D_V1', notice_days=30,
            earliest_settlement_at=self.clock_now + timedelta(days=30),
            client_request_id=f'closure-{store.slug}', request_fingerprint='a' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )
        return notice

    def pending(self, store):
        notice = store.closure_notice
        self.clock_now = notice.earliest_settlement_at
        StoreClosureEvent.objects.create(
            store=store, notice=notice, kind='NOTICE_EXPIRED', reason='到期确认内部原因',
            actor=self.closure_actor, occurred_at=notice.earliest_settlement_at,
            client_request_id=f'expiry-{store.slug}', request_fingerprint='b' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )
        return notice

    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data['code'], 409)
        self.assertEqual(response.data['errorCode'], 'STORE_CLOSURE_RESTRICTED')
        self.assertNotIn('内部闭店秘密原因', str(response.data))

    def assert_ok(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def ordinary_payload(self, fixture, request_id):
        return {
            'service_id': fixture.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'client_request_id': request_id,
        }

    def gift_payload(self, fixture, request_id):
        return {
            'gift_service_id': fixture.gift.pk, 'provider_id': fixture.provider_accounts[0].pk,
            'quantity': 1, 'expected_amount': fixture.gift.price,
            'client_request_id': request_id,
        }

    def reservation_payload(self, fixture, *, team=False):
        start = (timezone.now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
        if team:
            return {
                'service_id': fixture.team_service.pk,
                'provider_account_ids': [row.pk for row in fixture.provider_accounts],
                'start_time': start.isoformat(), 'end_time': (start + timedelta(minutes=90)).isoformat(),
                'game_rounds': 1,
            }
        return {
            'service_id': fixture.service.pk, 'provider_account_id': fixture.provider_accounts[0].pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=2)).isoformat(),
            'game_rounds': 2,
        }

    def manual_payload(self, fixture, request_id):
        return {
            'customer_id': fixture.boss_account.pk, 'service_id': fixture.service.pk,
            'game_rounds': 1, 'expected_amount': fixture.service.price,
            'client_request_id': request_id, 'boss_confirmation': '老板已确认本次代下单',
            'boss_confirmation_method': '微信文字确认',
        }

    def test_ordinary_create_blocks_active_pending_and_preserves_existing_order(self):
        fixture = self.f(self.first)
        original_payload = self.ordinary_payload(fixture, 'closure-ordinary-before')
        created = fixture.boss_client.post('/api/orders/create/', original_payload, format='json')
        order_id = self.assert_ok(created)['id']
        self.close(self.first)
        with store_context(self.first):
            count = Order.objects.count()
            balance = Wallet.objects.get(account=fixture.boss_account).balance
        self.assert_blocked(fixture.boss_client.post(
            '/api/orders/create/', self.ordinary_payload(fixture, 'closure-ordinary-active'), format='json'))
        detail = fixture.boss_client.get(f'/api/orders/orders/{order_id}/')
        self.assertEqual(self.assert_ok(detail)['id'], order_id)
        replay = fixture.boss_client.post('/api/orders/create/', original_payload, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data.get('code'), 409)
        self.assertEqual(replay.data.get('errorCode'), 'FUND_ALREADY_PROCESSED')
        self.pending(self.first)
        self.assert_blocked(fixture.boss_client.post(
            '/api/orders/create/', self.ordinary_payload(fixture, 'closure-ordinary-pending'), format='json'))
        with store_context(self.first):
            self.assertEqual(Order.objects.count(), count)
            self.assertEqual(Wallet.objects.get(account=fixture.boss_account).balance, balance)
        other = self.f(self.second)
        other_created = other.boss_client.post(
            '/api/orders/create/', self.ordinary_payload(other, 'closure-ordinary-other-store'), format='json')
        self.assert_ok(other_created)

    def test_direct_gift_and_basket_block_but_existing_order_tip_continues(self):
        fixture = self.f(self.first)
        before = fixture.boss_client.post(
            '/api/orders/gifts/send/', self.gift_payload(fixture, 'closure-gift-before'), format='json')
        self.assert_ok(before)
        with store_context(self.first):
            source = make_order(
                fixture.boss, service=fixture.service, provider=fixture.providers[0],
                status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
                customer_account=fixture.boss_account, provider_account=fixture.provider_accounts[0],
            )
        self.close(self.first)
        self.assert_blocked(fixture.boss_client.post(
            '/api/orders/gifts/send/', self.gift_payload(fixture, 'closure-gift-active'), format='json'))
        # Existing-order gifting is a later operation, not a new direct gift order.
        existing_tip = fixture.boss_client.post(
            f'/api/orders/orders/{source.pk}/tip/', self.gift_payload(fixture, 'closure-existing-order-tip'),
            format='json')
        self.assert_ok(existing_tip)
        self.pending(self.first)
        basket = {
            'items': [{'gift_service_id': fixture.gift.pk, 'quantity': 1}],
            'provider_id': fixture.provider_accounts[0].pk, 'expected_amount': fixture.gift.price,
            'client_request_id': 'closure-gift-basket-pending', 'naming_title': '闭店礼篮',
        }
        self.assert_blocked(fixture.boss_client.post('/api/orders/gifts/send/', basket, format='json'))
        other = self.f(self.second)
        self.assert_ok(other.boss_client.post(
            '/api/orders/gifts/send/', self.gift_payload(other, 'closure-gift-other-store'), format='json'))

    def reservation_lifecycle(self, *, team):
        fixture = self.f(self.first)
        created = fixture.boss_client.post(
            '/api/orders/reservations/', self.reservation_payload(fixture, team=team), format='json')
        reservation_id = self.assert_ok(created)['id']
        self.close(self.first)
        with store_context(self.first):
            count = Reservation.objects.count()
        self.assert_blocked(fixture.boss_client.post(
            '/api/orders/reservations/', self.reservation_payload(fixture, team=team), format='json'))
        confirmed = fixture.provider_clients[0].post(
            f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json')
        self.assert_ok(confirmed)
        self.pending(self.first)
        self.assert_blocked(fixture.boss_client.post(
            '/api/orders/reservations/', self.reservation_payload(fixture, team=team), format='json'))
        if team:
            second_confirmation = fixture.provider_clients[1].post(
                f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json')
            self.assertEqual(self.assert_ok(second_confirmation)['status'], Reservation.Status.CONFIRMED)
        quote = self.assert_ok(fixture.boss_client.get(
            f'/api/orders/reservations/{reservation_id}/quote/'))
        converted = fixture.boss_client.post(f'/api/orders/reservations/{reservation_id}/convert/', {
            'client_request_id': f'closure-reservation-convert-{team}',
            'expected_amount': quote['amount'], 'expected_service': quote['service'],
        }, format='json')
        self.assert_ok(converted)
        with store_context(self.first):
            self.assertEqual(Reservation.objects.count(), count)
        other = self.f(self.second)
        other_created = other.boss_client.post(
            '/api/orders/reservations/', self.reservation_payload(other, team=team), format='json')
        self.assert_ok(other_created)

    def test_single_reservation_create_blocks_while_existing_confirmation_continues(self):
        self.reservation_lifecycle(team=False)

    def test_team_reservation_create_blocks_while_both_existing_confirmations_continue(self):
        self.reservation_lifecycle(team=True)

    def test_staff_manual_create_blocks_without_cross_store_or_existing_read_regression(self):
        fixture = self.f(self.first)
        original_payload = self.manual_payload(fixture, 'closure-manual-before')
        created = fixture.staff_client.post('/api/admin/orders/dispatch/', original_payload, format='json')
        order_id = self.assert_ok(created)['id']
        self.close(self.first)
        self.assert_blocked(fixture.staff_client.post(
            '/api/admin/orders/dispatch/', self.manual_payload(fixture, 'closure-manual-active'), format='json'))
        detail = fixture.staff_client.get(f'/api/admin/orders/{order_id}/')
        self.assertEqual(self.assert_ok(detail)['id'], order_id)
        replay = fixture.staff_client.post('/api/admin/orders/dispatch/', original_payload, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data.get('code'), 409)
        self.assertEqual(replay.data.get('errorCode'), 'FUND_ALREADY_PROCESSED')
        self.pending(self.first)
        self.assert_blocked(fixture.staff_client.post(
            '/api/admin/orders/dispatch/', self.manual_payload(fixture, 'closure-manual-pending'), format='json'))
        other = self.f(self.second)
        self.assert_ok(other.staff_client.post(
            '/api/admin/orders/dispatch/', self.manual_payload(other, 'closure-manual-other-store'), format='json'))

    def test_unknown_clock_and_control_read_failure_fail_closed_without_blocking_reads(self):
        fixture = self.f(self.first)
        order_id = self.assert_ok(fixture.boss_client.post(
            '/api/orders/create/', self.ordinary_payload(fixture, 'closure-unknown-before'), format='json'))['id']
        self.close(self.first)
        self.clock_error = ControlClockUnavailable('explicit unknown clock')
        announcement = fixture.boss_client.get('/api/stores/closure/')
        self.assertEqual(announcement.data['data']['status'], 'UNKNOWN')
        self.assert_blocked(fixture.boss_client.post(
            '/api/orders/create/', self.ordinary_payload(fixture, 'closure-unknown-active'), format='json'))
        self.assert_blocked(fixture.boss_client.post(
            '/api/orders/reservations/', self.reservation_payload(fixture), format='json'))
        self.assertEqual(fixture.boss_client.get(f'/api/orders/orders/{order_id}/').status_code, 200)
        with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control read failed')):
            unavailable = fixture.boss_client.post(
                '/api/orders/create/', self.ordinary_payload(fixture, 'closure-control-read-failure'), format='json')
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.data['errorCode'], 'STORE_CLOSURE_STATUS_UNAVAILABLE')
        self.assertEqual(fixture.boss_client.get(f'/api/orders/orders/{order_id}/').status_code, 200)
