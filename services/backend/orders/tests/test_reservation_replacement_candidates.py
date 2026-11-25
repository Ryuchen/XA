"""Read-only, future-window provider candidates for a vacant team reservation seat."""
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

from django.db import connections
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, Reservation, ReservationSeat, ReservationSeatEvent
from orders.reservation_services import create_reservation
from orders.reservation_team import create_team_reservation, decide
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_wallet
from orders.tests.test_reservation_api import slot
from stores.context import resolve_store, store_context
from stores.display import provider_display_name
from stores.tests_hosts import xinan_store
from users.models import EscortLevel, EscortProfile, EscortSchedule
from wallet.models import Wallet


@override_settings(
    ALLOWED_HOSTS=['*'],
    LEGACY_FORCE_AUTH_COMPAT=False,
    PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=False,
    PROVIDER_WECHAT_GATE_ENABLED=False,
)
class ReservationReplacementCandidateTests(APITestCase):
    databases = {'default', 'store_b'}
    base_account_id = 810000

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.fixtures = {
            self.first.slug: self.build_fixture(self.first, 'sl.esports.club'),
            self.second.slug: self.build_fixture(self.second, 'xa.esports.club'),
        }
        self.assertEqual(self.f(self.first).booking.pk, self.f(self.second).booking.pk)
        for index in range(6):
            self.assertEqual(
                self.f(self.first).providers[index].pk,
                self.f(self.second).providers[index].pk,
            )

    def f(self, store):
        return self.fixtures[store.slug]

    def pair_account(self, user, account_id, account_type):
        account = ClubAccount.objects.create(
            id=account_id, username=user.username, password=user.password,
            nickname=user.nickname, real_name=user.real_name, phone=user.phone,
            account_type=account_type, is_phone_verified=user.is_phone_verified,
        )
        paired = get_or_create_account_for_legacy_user(user)
        self.assertEqual(paired.pk, account.pk)
        return paired

    def account_client(self, account, host):
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def build_fixture(self, store, host):
        with store_context(store):
            from console.reservation_deadlines import save_deadlines
            from console.room_confirmation_config import save_room_confirmation
            save_deadlines(30, 60, 0, lambda *_: None)
            save_room_confirmation(60, 0, lambda *_: None)
            boss = make_user()
            boss_account = self.pair_account(boss, self.base_account_id + 1, 'BOSS')
            make_wallet(boss, balance=100000)
            Wallet.objects.filter(user=boss).update(account=boss_account)
            staff = make_console_user(['order:view', 'order:dispatch', 'order:cancel'])
            staff_account = self.pair_account(staff, self.base_account_id + 2, 'STAFF')
            staff.admin_membership.account = staff_account
            staff.admin_membership.save(update_fields=['account'])

            users = []
            providers = []
            names = ('保留席', '原陪玩', '活跃候选', '单陪冲突', '班次不足', '过期通行证')
            for index, name in enumerate(names):
                user = make_provider(display_name=f'{store.slug}-{name}', deposit_mode='EARNINGS')
                account = self.pair_account(user, self.base_account_id + 10 + index, 'PROVIDER')
                EscortProfile.objects.filter(user=user).update(account=account)
                make_wallet(user, balance=0)
                Wallet.objects.filter(user=user).update(account=account)
                users.append(user)
                providers.append(account)
            active = EscortProfile.objects.get(account=providers[2])
            active.pass_tier = 'BLACK'
            active.pass_expires_at = timezone.now() + timedelta(days=1)
            active.save(update_fields=['pass_tier', 'pass_expires_at'])
            expired = EscortProfile.objects.get(account=providers[5])
            expired.pass_tier = 'BLACK'
            expired.pass_expires_at = timezone.now() - timedelta(days=1)
            expired.save(update_fields=['pass_tier', 'pass_expires_at'])

            service = make_service(name=f'{store.slug}-单陪服务', price=1000)
            team_service = make_service(
                name=f'{store.slug}-双陪服务', price=10000,
                billing_provider_count=2, fulfillment_provider_count=2, price_unit='小时',
            )
            start = slot(hour=10)
            end = start + timedelta(minutes=90)
            booking = create_team_reservation(
                customer_account=boss_account, customer=boss, providers=providers[:2],
                service=team_service, start_time=start, end_time=end,
            )
            decide(booking.pk, providers[0], accept=True)
            booking, error = decide(booking.pk, providers[1], accept=False, reason='候选测试空出席位')
            self.assertEqual(error, '')
            rejected_profile = EscortProfile.objects.get(account=providers[1])
            rejected_profile.status = EscortProfile.Status.OFFLINE
            rejected_profile.save(update_fields=['status'])
            single_conflict = create_reservation(
                customer_account=boss_account, customer=boss, provider_account=providers[3],
                service=service, start_time=start, end_time=end,
            )
            EscortSchedule.objects.create(
                provider=users[4], account=providers[4],
                weekday=timezone.localtime(start).weekday(), start_minute=0, end_minute=1,
            )
            return SimpleNamespace(
                store=store, host=host, boss=boss, boss_account=boss_account,
                staff=staff, staff_account=staff_account,
                provider_users=users, providers=providers,
                service=service, team_service=team_service,
                start=start, end=end, booking=booking, single_conflict=single_conflict,
                boss_client=self.account_client(boss_account, host),
                staff_client=self.account_client(staff_account, 'admin.esports.club'),
                provider_clients=[self.account_client(account, host) for account in providers[:2]],
            )

    def path(self, fixture, seat=2, *, booking=None):
        reservation = booking or fixture.booking
        return f'/api/orders/reservations/{reservation.pk}/replacement-candidates/?seat_number={seat}'

    def candidates(self, fixture, *, seat=2, booking=None):
        response = fixture.boss_client.get(self.path(fixture, seat, booking=booking))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response.data['data']

    def test_owner_reads_future_eligible_card_fields_and_original_without_store_writes(self):
        fixture = self.f(self.first)
        with store_context(self.first):
            seats_before = list(fixture.booking.seats.values())
            event_count = ReservationSeatEvent.objects.filter(reservation=fixture.booking).count()
            reservation_count = Reservation.objects.count()
            updated_before = fixture.booking.updated_at
        with CaptureQueriesContext(connections[self.first.database_alias]) as captured:
            response = fixture.boss_client.get(self.path(fixture))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')
        write_sql = [entry['sql'] for entry in captured.captured_queries if re.match(
            r'^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', entry['sql'], re.I,
        )]
        self.assertEqual(write_sql, [])
        data = response.data['data']
        self.assertEqual(set(data), {
            'seat_number', 'service_id', 'start_time', 'end_time', 'original_provider', 'list',
        })
        self.assertEqual(data['seat_number'], 2)
        self.assertEqual(data['service_id'], fixture.team_service.pk)
        self.assertEqual(datetime.fromisoformat(data['start_time']), fixture.start)
        self.assertEqual(datetime.fromisoformat(data['end_time']), fixture.end)
        self.assertEqual(
            [row['account_id'] for row in data['list']],
            [fixture.providers[2].pk, fixture.providers[5].pk],
        )
        self.assertNotIn(fixture.providers[0].pk, [row['account_id'] for row in data['list']])
        self.assertNotIn(fixture.providers[3].pk, [row['account_id'] for row in data['list']])
        self.assertNotIn(fixture.providers[4].pk, [row['account_id'] for row in data['list']])
        for row in data['list']:
            self.assertEqual(row['id'], row['account_id'])
            self.assertIs(row['can_order'], True)
            self.assertIs(row['is_verified'], True)
            for field in ('nickname', 'display_name', 'avatar', 'gender', 'level_name', 'status',
                          'qualifications', 'service_item_ids', 'price_per_hour'):
                self.assertIn(field, row)
            self.assertTrue(all(re.fullmatch(r'[a-z][a-z0-9_]*', key) for key in row), row.keys())
        expected_name = EscortProfile.objects.get(account=fixture.providers[1]).display_name
        self.assertEqual(data['original_provider'], {
            'account_id': fixture.providers[1].pk,
            'display_name': provider_display_name(expected_name),
        })
        with store_context(self.first):
            fixture.booking.refresh_from_db()
            self.assertEqual(fixture.booking.updated_at, updated_before)
            self.assertEqual(list(fixture.booking.seats.values()), seats_before)
            self.assertEqual(ReservationSeatEvent.objects.filter(reservation=fixture.booking).count(), event_count)
            self.assertEqual(Reservation.objects.count(), reservation_count)

    def test_service_deposit_status_membership_wechat_and_avatar_all_filter_candidates(self):
        fixture = self.f(self.first)
        first_profile = EscortProfile.objects.get(account=fixture.providers[2])
        second_profile = EscortProfile.objects.get(account=fixture.providers[5])
        first_profile.is_verified = False
        first_profile.save(update_fields=['is_verified'])
        self.assertEqual([row['account_id'] for row in self.candidates(fixture)['list']], [fixture.providers[5].pk])
        second_profile.status = EscortProfile.Status.OFFLINE
        second_profile.save(update_fields=['status'])
        self.assertEqual(self.candidates(fixture)['list'], [])

        first_profile.is_verified = True
        first_profile.deposit_mode = 'CASH'
        first_profile.deposit_paid = 0
        first_profile.save(update_fields=['is_verified', 'deposit_mode', 'deposit_paid'])
        second_profile.status = EscortProfile.Status.AVAILABLE
        second_profile.save(update_fields=['status'])
        self.assertEqual([row['account_id'] for row in self.candidates(fixture)['list']], [fixture.providers[5].pk])

        high = EscortLevel.objects.create(name='候选高等级', sort_order=10, commission_rate=20)
        low = EscortLevel.objects.create(name='候选低等级', sort_order=1, commission_rate=20)
        fixture.team_service.required_level = high
        fixture.team_service.save(update_fields=['required_level'])
        first_profile.deposit_mode = 'EARNINGS'
        first_profile.level = low
        first_profile.save(update_fields=['deposit_mode', 'level'])
        second_profile.level = low
        second_profile.save(update_fields=['level'])
        self.assertEqual(self.candidates(fixture)['list'], [])
        second_profile.level = high
        second_profile.save(update_fields=['level'])
        self.assertEqual([row['account_id'] for row in self.candidates(fixture)['list']], [fixture.providers[5].pk])

        with override_settings(PROVIDER_WECHAT_GATE_ENABLED=True):
            self.assertEqual(self.candidates(fixture)['list'], [])
            account = fixture.providers[5]
            account.openid = 'candidate-wechat'
            account.is_openid_bound = True
            account.save(update_fields=['openid', 'is_openid_bound', 'updated_at'])
            self.assertEqual(self.candidates(fixture)['list'], [])
            second_profile.wechat_avatar_verified_at = timezone.now()
            second_profile.save(update_fields=['wechat_avatar_verified_at'])
            self.assertEqual([row['account_id'] for row in self.candidates(fixture)['list']], [account.pk])

        ClubAccount.objects.filter(pk=account.pk).update(license_membership_active=False)
        self.assertEqual(self.candidates(fixture)['list'], [])

    def test_original_provider_needs_matching_local_invitation_and_vacancy_event(self):
        fixture = self.f(self.first)
        original = self.candidates(fixture)['original_provider']
        self.assertEqual(original['account_id'], fixture.providers[1].pk)
        ReservationSeatEvent.objects.filter(
            reservation=fixture.booking, seat_number=2, action='INVITED',
            provider_account=fixture.providers[1],
        ).delete()
        fixture.booking.provider_name_snapshot = '不能采信的旧名称'
        fixture.booking.save(update_fields=['provider_name_snapshot'])
        fixture.providers[1].nickname = '也不能猜测的现行昵称'
        fixture.providers[1].save(update_fields=['nickname', 'updated_at'])
        self.assertIsNone(self.candidates(fixture)['original_provider'])
        ReservationSeatEvent.objects.create(
            reservation=fixture.booking, seat_number=2, action='INVITED',
            provider_account=fixture.providers[2], occurred_at=timezone.now(),
        )
        self.assertIsNone(self.candidates(fixture)['original_provider'])

    def test_empty_candidates_and_not_replaceable_or_nonvacant_seat_are_distinct(self):
        fixture = self.f(self.first)
        EscortProfile.objects.filter(account__in=fixture.providers[2:]).update(status='OFFLINE')
        empty = self.candidates(fixture)
        self.assertEqual(empty['list'], [])
        self.assertEqual(empty['original_provider']['account_id'], fixture.providers[1].pk)

        seat_two = fixture.booking.seats.get(number=2)
        seat_two.provider_account = fixture.providers[1]
        seat_two.provider = fixture.provider_users[1]
        seat_two.provider_business_cycle = fixture.providers[1].business_cycle
        seat_two.provider_name_snapshot = '已占席'
        seat_two.status = 'PENDING'
        seat_two.confirmation_deadline = fixture.start
        seat_two.save(update_fields=['provider_account', 'provider', 'provider_business_cycle',
                                     'provider_name_snapshot', 'status', 'confirmation_deadline', 'updated_at'])
        response = fixture.boss_client.get(self.path(fixture))
        self.assertEqual(response.data['code'], 409)
        self.assertEqual(response['Cache-Control'], 'no-store')
        seat_one = fixture.booking.seats.get(number=1)
        seat_one.provider_account = None
        seat_one.provider = None
        seat_one.provider_business_cycle = None
        seat_one.provider_name_snapshot = ''
        seat_one.status = 'VACANT'
        seat_one.confirmation_deadline = None
        seat_one.confirmed_at = None
        seat_one.save(update_fields=['provider_account', 'provider', 'provider_business_cycle',
                                     'provider_name_snapshot', 'status', 'confirmation_deadline', 'confirmed_at', 'updated_at'])
        response = fixture.boss_client.get(self.path(fixture))
        self.assertEqual(response.data['code'], 409)
        Reservation.objects.filter(pk=fixture.booking.pk).update(
            status='CONFIRMED', payment_deadline=fixture.start,
            payment_timeout_minutes=60, payment_config_version=1,
        )
        self.assertEqual(fixture.boss_client.get(self.path(fixture, seat=1)).data['code'], 409)

    def test_permissions_invalid_seat_and_store_query_do_not_leak_candidates(self):
        fixture = self.f(self.first)
        other = make_user()
        other_account = self.pair_account(other, self.base_account_id + 3, 'BOSS')
        other_client = self.account_client(other_account, fixture.host)
        self.assertEqual(other_client.get(self.path(fixture)).data['code'], 404)
        current_provider_denied = fixture.provider_clients[0].get(self.path(fixture))
        self.assertEqual(current_provider_denied.status_code, 403)
        self.assertEqual(current_provider_denied.data['code'], 403)
        self.assertEqual(fixture.provider_clients[1].get(self.path(fixture)).data['code'], 404)
        staff_denied = fixture.staff_client.get(self.path(fixture))
        self.assertEqual(staff_denied.status_code, 403)
        self.assertEqual(staff_denied.data['code'], 403)
        base = self.path(fixture)
        for query in ('', '?seat_number=', '?seat_number=0', '?seat_number=3', '?seat_number=x',
                      '?seat_number=2&seat_number=1', '?seat_number=2&store=shenglang',
                      '?seat_number=2&database_alias=store_b'):
            with self.subTest(query=query):
                url = base.split('?')[0] + query
                response = fixture.boss_client.get(url)
                self.assertEqual(response.data['code'], 400, response.data)
                self.assertNotIn('list', response.data.get('data', {}))
        cross_store = fixture.boss_client.get(base + '&store=xinan')
        self.assertEqual(cross_store.status_code, 400)
        anonymous = APIClient(HTTP_HOST=fixture.host).get(base)
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous['Cache-Control'], 'no-store')

    def test_same_numeric_ids_are_store_isolated_and_never_share_original_snapshot(self):
        first = self.f(self.first)
        second = self.f(self.second)
        first_data = self.candidates(first)
        second_data = self.candidates(second)
        self.assertEqual(
            [row['account_id'] for row in first_data['list']],
            [row['account_id'] for row in second_data['list']],
        )
        self.assertNotEqual(first_data['list'][0]['nickname'], second_data['list'][0]['nickname'])
        with store_context(self.second):
            EscortProfile.objects.filter(account__in=second.providers[2:]).update(status='OFFLINE')
            ReservationSeatEvent.objects.filter(
                reservation=second.booking, seat_number=2, action='INVITED',
                provider_account=second.providers[1],
            ).delete()
        changed_second = self.candidates(second)
        self.assertEqual(changed_second['list'], [])
        self.assertIsNone(changed_second['original_provider'])
        unchanged_first = self.candidates(first)
        self.assertTrue(unchanged_first['list'])
        self.assertEqual(unchanged_first['original_provider']['account_id'], first.providers[1].pk)

    def test_single_team_and_converted_original_order_conflicts_are_all_excluded(self):
        fixture = self.f(self.first)
        extra_user = make_provider(deposit_mode='EARNINGS', display_name='组队冲突另一人')
        extra = self.pair_account(extra_user, self.base_account_id + 30, 'PROVIDER')
        EscortProfile.objects.filter(user=extra_user).update(account=extra)
        make_wallet(extra_user, balance=0)
        Wallet.objects.filter(user=extra_user).update(account=extra)
        create_team_reservation(
            customer_account=fixture.boss_account, customer=fixture.boss,
            providers=[fixture.providers[5], extra], service=fixture.team_service,
            start_time=fixture.start, end_time=fixture.end,
        )
        self.assertEqual([row['account_id'] for row in self.candidates(fixture)['list']], [fixture.providers[2].pk])

        original_order = make_order(
            fixture.boss, service=fixture.service, provider=fixture.provider_users[2],
            status=Order.Status.IN_SERVICE, payment_status=Order.PaymentStatus.PAID,
            customer_account=fixture.boss_account, provider_account=fixture.providers[2],
        )
        converted = create_reservation(
            customer_account=fixture.boss_account, customer=fixture.boss,
            provider_account=fixture.providers[2], service=fixture.service,
            start_time=fixture.start, end_time=fixture.end,
        )
        Reservation.objects.filter(pk=converted.pk).update(
            status='CONVERTED', order=original_order, confirmed_at=timezone.now(),
            payment_deadline=fixture.start, payment_timeout_minutes=60, payment_config_version=1,
        )
        self.assertEqual(self.candidates(fixture)['list'], [])

    def test_schedule_must_cover_complete_window_but_current_shift_is_irrelevant(self):
        fixture = self.f(self.first)
        EscortSchedule.objects.filter(account=fixture.providers[4]).delete()
        local = timezone.localtime(fixture.start)
        window_start = local.hour * 60 + local.minute
        for start_minute, end_minute in (
                (window_start - 60, window_start + 30),
                (window_start + 30, window_start + 120)):
            EscortSchedule.objects.create(
                provider=fixture.provider_users[4], account=fixture.providers[4], weekday=local.weekday(),
                start_minute=start_minute, end_minute=end_minute,
            )
        EscortSchedule.objects.create(
            provider=fixture.provider_users[5], account=fixture.providers[5], weekday=local.weekday(),
            start_minute=window_start - 60, end_minute=window_start + 30,
        )
        data = self.candidates(fixture)
        self.assertEqual(
            [row['account_id'] for row in data['list']],
            [fixture.providers[2].pk, fixture.providers[4].pk],
        )

    def test_candidate_changed_after_read_is_still_rejected_by_final_replace(self):
        fixture = self.f(self.first)
        candidate_id = self.candidates(fixture)['list'][0]['account_id']
        self.assertEqual(candidate_id, fixture.providers[2].pk)
        create_reservation(
            customer_account=fixture.boss_account, customer=fixture.boss,
            provider_account=fixture.providers[2], service=fixture.service,
            start_time=fixture.start, end_time=fixture.end,
        )
        other_before = fixture.booking.seats.get(number=1).provider_account_id
        event_count = fixture.booking.seat_events.count()
        response = fixture.boss_client.post(
            f'/api/orders/reservations/{fixture.booking.pk}/replace-provider/',
            {'seat_number': 2, 'provider_account_id': candidate_id}, format='json',
        )
        self.assertEqual(response.data['code'], 409, response.data)
        self.assertEqual(fixture.booking.seats.get(number=2).status, 'VACANT')
        self.assertEqual(fixture.booking.seats.get(number=1).provider_account_id, other_before)
        self.assertEqual(fixture.booking.seat_events.count(), event_count)

    def test_read_does_not_expire_or_refresh_overdue_booking(self):
        fixture = self.f(self.first)
        overdue = timezone.now() - timedelta(minutes=1)
        Reservation.objects.filter(pk=fixture.booking.pk).update(confirmation_deadline=overdue)
        fixture.booking.refresh_from_db()
        before = (fixture.booking.status, fixture.booking.confirmation_deadline, fixture.booking.updated_at)
        seats_before = list(fixture.booking.seats.values())
        data = self.candidates(fixture)
        self.assertTrue(data['list'])
        fixture.booking.refresh_from_db()
        self.assertEqual(
            (fixture.booking.status, fixture.booking.confirmation_deadline, fixture.booking.updated_at), before,
        )
        self.assertEqual(list(fixture.booking.seats.values()), seats_before)
