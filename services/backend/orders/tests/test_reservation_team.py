from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, Reservation, ReservationSeat, ReservationSeatEvent
from orders.reservation_services import ReservationConflictError, create_reservation, find_conflicting_reservations
from orders.reservation_team import create_team_reservation, decide, refresh_pending, replace
from orders.tests.factories import make_provider, make_service
from orders.tests.test_reservation_api import ReservationApiTestBase, prepare_reservation_payment, slot
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False)
class TeamReservationDomainTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        from console.room_confirmation_config import save_room_confirmation
        save_room_confirmation(60, 0, lambda *_: None)
        prepare_reservation_payment(self.boss_account)
        self.members = [self.provider_account]
        for _ in range(2):
            user = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            EscortProfile.objects.filter(user=user).update(account=account)
            self.members.append(account)
        self.team_service = make_service(price=10000, billing_provider_count=2,
                                         fulfillment_provider_count=2, price_unit='小时')
        self.start = slot(hour=10)
        self.end = self.start + timedelta(minutes=90)

    def create_team(self):
        return create_team_reservation(customer_account=self.boss_account, customer=self.boss,
            providers=self.members[:2], service=self.team_service, start_time=self.start, end_time=self.end)

    def test_unconfigured_store_rejects_team_booking_without_reserving_slots(self):
        from orders.reservation_deadlines import CONFIG_KEY
        from wallet.models import SystemConfig
        SystemConfig.objects.filter(key=CONFIG_KEY).delete()
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])
        payload = self.create_payload(service_id=self.team_service.pk,
            provider_account_ids=[row.pk for row in self.members[:2]],
            start_time=self.start.isoformat(), end_time=self.end.isoformat(), game_rounds=1)
        result = boss.post('/api/orders/reservations/', payload, format='json')
        self.assertNotEqual(result.data['code'], 0, result.data)
        self.assertIn('尚未配置预约', result.data['msg'])
        self.assertFalse(Reservation.objects.exists())
        self.assertFalse(ReservationSeat.objects.exists())

    def test_configured_range_applies_to_single_and_team_cross_midnight(self):
        from console.reservation_deadlines import save_deadlines
        save_deadlines(30, 60, 1, lambda *_: None, slot_minutes=30,
            min_duration_minutes=60, max_duration_minutes=180, max_advance_days=2)
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])
        start = slot(hour=23, minute=30)
        for service, providers in ((self.service, [self.members[0]]),
                                   (self.team_service, self.members[:2])):
            payload = self.create_payload(service_id=service.pk,
                start_time=start.isoformat(), end_time=(start + timedelta(minutes=60)).isoformat(), game_rounds=1)
            if len(providers) == 2:
                payload.pop('provider_account_id', None)
                payload['provider_account_ids'] = [account.pk for account in providers]
            bad = dict(payload, end_time=(start + timedelta(minutes=30)).isoformat())
            self.assertNotEqual(boss.post('/api/orders/reservations/', bad, format='json').data['code'], 0)
            result = boss.post('/api/orders/reservations/', payload, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            Reservation.objects.all().delete()
        too_far = dict(payload, start_time=(start + timedelta(days=3)).isoformat(),
                       end_time=(start + timedelta(days=3, minutes=60)).isoformat())
        self.assertNotEqual(boss.post('/api/orders/reservations/', too_far, format='json').data['code'], 0)

    def test_both_confirm_separately_and_snapshot_payment_deadline(self):
        booking = self.create_team()
        self.assertEqual((booking.required_provider_count, booking.estimated_amount), (2, 30000))
        booking, error = decide(booking.pk, self.members[0], accept=True)
        self.assertEqual((booking.status, error), ('PENDING', ''))
        self.assertTrue(find_conflicting_reservations(self.members[1], self.start, self.end).exists())
        booking, error = decide(booking.pk, self.members[1], accept=True)
        self.assertEqual((booking.status, error), ('CONFIRMED', ''))
        self.assertLessEqual(booking.payment_deadline, self.start)
        self.assertEqual(list(booking.seats.values_list('status', flat=True)), ['CONFIRMED', 'CONFIRMED'])
        self.assertEqual(list(booking.seat_events.values_list('action', flat=True)),
                         ['INVITED', 'INVITED', 'CONFIRMED', 'CONFIRMED'])

    def test_reject_then_replace_preserves_first_provider_and_locks_new_slot(self):
        booking = self.create_team()
        decide(booking.pk, self.members[0], accept=True)
        booking, error = decide(booking.pk, self.members[1], accept=False, reason='无法赴约')
        self.assertEqual((booking.status, error), ('PENDING', ''))
        self.assertEqual(list(booking.seats.values_list('status', flat=True)), ['CONFIRMED', 'VACANT'])
        self.assertTrue(find_conflicting_reservations(self.members[0], self.start, self.end).exists())
        self.assertFalse(find_conflicting_reservations(self.members[1], self.start, self.end).exists())
        booking, error = replace(booking.pk, self.boss_account, 2, self.members[2])
        self.assertEqual(error, '')
        self.assertTrue(find_conflicting_reservations(self.members[2], self.start, self.end).exists())
        booking, error = decide(booking.pk, self.members[2], accept=True)
        self.assertEqual((booking.status, error), ('CONFIRMED', ''))
        self.assertEqual(list(booking.seat_events.values_list('action', flat=True)),
                         ['INVITED', 'INVITED', 'CONFIRMED', 'REJECTED', 'INVITED', 'CONFIRMED'])

    def test_single_booking_cannot_overlap_secondary_team_seat(self):
        self.create_team()
        with self.assertRaises(ReservationConflictError):
            create_reservation(customer_account=self.boss_account, customer=self.boss,
                provider_account=self.members[1], service=self.service, start_time=self.start, end_time=self.end)

    def test_confirmed_provider_remains_reserved_when_other_times_out(self):
        booking = self.create_team()
        decide(booking.pk, self.members[0], accept=True)
        with patch('django.utils.timezone.now', return_value=booking.confirmation_deadline):
            with transaction.atomic():
                locked = Reservation.objects.select_for_update().get(pk=booking.pk)
                self.assertFalse(refresh_pending(locked, now=timezone.now()))
            booking.refresh_from_db()
            self.assertEqual(booking.confirmation_deadline, booking.start_time)
            self.assertEqual(list(booking.seats.values_list('status', flat=True)), ['CONFIRMED', 'VACANT'])
            self.assertTrue(find_conflicting_reservations(self.members[0], self.start, self.end).exists())
            self.assertFalse(find_conflicting_reservations(self.members[1], self.start, self.end).exists())

    def test_confirmation_crossing_deadline_restores_seat_and_preserves_review(self):
        from orders.reservation_clock import ClockConflict
        from orders.reservation_team import _event
        booking = self.create_team()
        clock = [booking.confirmation_deadline - timedelta(seconds=1)]
        def event_then_deadline(*args, **kwargs):
            _event(*args, **kwargs)
            if args[2] == 'CONFIRMED':
                clock[0] = booking.confirmation_deadline
        with patch('django.utils.timezone.now', side_effect=lambda: clock[0]), \
             patch('orders.reservation_team._event', side_effect=event_then_deadline):
            with self.assertRaises(ClockConflict):
                decide(booking.pk, self.members[0], accept=True)
        self.assertEqual(booking.seats.get(number=1).status, 'PENDING')
        self.assertEqual(booking.seat_events.filter(action='CONFIRMED').count(), 1)
        receipt = booking.clock_receipts.get(action='TEAM_CONFIRM', seat_number=1)
        self.assertEqual((receipt.state, receipt.review_reason), ('REVIEW', 'MONOTONIC_INCONSISTENT'))
        self.assertEqual(receipt.state_after, 'PENDING')

    def test_real_api_replacement_quote_and_payment_assign_both(self):
        def client(account):
            return APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        boss, first, rejected, replacement = [client(account) for account in [self.boss_account, *self.members]]
        payload = self.create_payload(service_id=self.team_service.pk, provider_account_id=self.members[0].pk,
            provider_account_ids=[row.pk for row in self.members[:2]],
            start_time=self.start.isoformat(), end_time=self.end.isoformat(), game_rounds=1)
        created = boss.post('/api/orders/reservations/', payload, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        path = f"/api/orders/reservations/{created.data['data']['id']}/"
        self.assertEqual(len(created.data['data']['seats']), 2)
        self.assertEqual(first.post(path + 'confirm/', {}, format='json').data['code'], 0)
        self.assertEqual(rejected.post(path + 'reject/', {}, format='json').data['code'], 0)
        changed = boss.post(path + 'replace-provider/', {'seat_number': 2,
            'provider_account_id': self.members[2].pk}, format='json')
        self.assertEqual(changed.data['code'], 0, changed.data)
        self.assertEqual(replacement.post(path + 'confirm/', {}, format='json').data['code'], 0)
        quoted = boss.get(path + 'quote/')
        self.assertEqual(quoted.data['code'], 0, quoted.data)
        self.assertEqual(quoted.data['data']['amount'], 30000)
        paid = boss.post(path + 'convert/', {'expected_amount': 30000,
            'client_request_id': 'double-reservation-pay'}, format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        booking = Reservation.objects.get(pk=created.data['data']['id'])
        self.assertEqual((booking.status, booking.order.escort_mode, booking.order.required_provider_count),
                         ('CONVERTED', 'DOUBLE', 2))
        self.assertEqual(list(booking.order.providers.order_by('pk').values_list('provider_account_id', flat=True)),
                         [self.members[0].pk, self.members[2].pk])
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 70000)
        self.assertEqual(Transaction.objects.filter(order=booking.order, tx_type='PAY').count(), 1)
        history = rejected.get(path).data
        self.assertEqual(history['code'], 0, history)
        summary = history['data']
        self.assertEqual((summary['viewer_participation_status'], summary['allowed_actions']), ('HISTORICAL', []))
        for field in ('customer', 'provider', 'seats', 'current_providers', 'order', 'order_no', 'game_uid', 'remark'):
            self.assertNotIn(field, summary)
        denied = rejected.post(path + 'cancel/', {'reason': '已拒绝的陪玩不能取消预约'}, format='json')
        self.assertEqual(denied.data['code'], 404, denied.data)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'CONVERTED')

    def test_paid_team_reservation_creates_room_in_same_transaction(self):
        from orders.models import OrderRoom
        self.team_service.room_enabled = True
        self.team_service.save(update_fields=['room_enabled'])
        booking = self.create_team()
        decide(booking.pk, self.members[0], accept=True)
        decide(booking.pk, self.members[1], accept=True)
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])
        path = f'/api/orders/reservations/{booking.pk}/convert/'
        payload = {'expected_amount': 30000, 'client_request_id': 'team-room-payment'}
        with patch('orders.rooms.OrderRoom.objects.get_or_create', side_effect=RuntimeError('room write failed')):
            with self.assertRaises(RuntimeError):
                boss.post(path, payload, format='json')
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 100000)
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())
        result = boss.post(path, payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order_id = result.data['data']['order']['id']
        self.assertTrue(OrderRoom.objects.filter(order_id=order_id).exists())
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 70000)
        for account in (self.boss_account, self.members[0], self.members[1]):
            client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            self.assertEqual(client.get(f'/api/orders/orders/{order_id}/room/').status_code, 200)

    def test_worker_preserves_confirmed_seat_and_replacement_can_confirm_near_start(self):
        from orders.reservation_expiration import expire_due_reservations
        booking = self.create_team()
        decide(booking.pk, self.members[0], accept=True)
        with patch('django.utils.timezone.now', return_value=booking.confirmation_deadline):
            self.assertEqual(expire_due_reservations(), 0)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.confirmation_deadline), ('PENDING', self.start))
        self.assertEqual(list(booking.seats.values_list('status', flat=True)), ['CONFIRMED', 'VACANT'])
        with patch('django.utils.timezone.now', return_value=self.start - timedelta(minutes=10)):
            booking, error = replace(booking.pk, self.boss_account, 2, self.members[2])
            self.assertEqual(error, '')
            self.assertEqual(booking.seats.get(number=2).confirmation_deadline, self.start)
        with patch('django.utils.timezone.now', return_value=self.start - timedelta(minutes=5)):
            booking, error = decide(booking.pk, self.members[2], accept=True)
            self.assertEqual((booking.status, error, booking.payment_deadline), ('CONFIRMED', '', self.start))

    def test_only_invited_provider_can_confirm_and_only_boss_can_replace(self):
        booking = self.create_team()
        token = lambda account: APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        path = f'/api/orders/reservations/{booking.pk}/'
        self.assertNotEqual(token(self.staff_account).post(path + 'confirm/', {}, format='json').data['code'], 0)
        self.assertNotEqual(token(self.members[2]).post(path + 'confirm/', {}, format='json').data['code'], 0)
        decide(booking.pk, self.members[0], accept=True)
        decide(booking.pk, self.members[1], accept=False)
        response = token(self.staff_account).post(path + 'replace-provider/',
            {'seat_number': 2, 'provider_account_id': self.members[2].pk}, format='json')
        self.assertNotEqual(response.data['code'], 0)
        self.assertEqual(booking.seats.get(number=2).status, 'VACANT')

    def test_api_rejects_offline_or_unpaid_deposit_for_single_team_and_replacement(self):
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])
        path = '/api/orders/reservations/'
        profile = EscortProfile.objects.get(account=self.members[1])
        profile.status = EscortProfile.Status.OFFLINE
        profile.save(update_fields=['status'])
        single = self.create_payload(service_id=self.service.pk, provider_account_id=self.members[1].pk,
            start_time=self.start.isoformat(), end_time=self.end.isoformat())
        team = self.create_payload(service_id=self.team_service.pk, provider_account_id=self.members[0].pk,
            provider_account_ids=[row.pk for row in self.members[:2]],
            start_time=self.start.isoformat(), end_time=self.end.isoformat(), game_rounds=1)
        self.assertNotEqual(boss.post(path, single, format='json').data['code'], 0)
        self.assertNotEqual(boss.post(path, team, format='json').data['code'], 0)
        self.assertFalse(Reservation.objects.exists())
        profile.status = EscortProfile.Status.AVAILABLE
        profile.save(update_fields=['status'])
        booking = self.create_team()
        decide(booking.pk, self.members[0], accept=True)
        decide(booking.pk, self.members[1], accept=False)
        replacement = EscortProfile.objects.get(account=self.members[2])
        replacement.deposit_mode = 'CASH'
        replacement.deposit_paid = 0
        replacement.save(update_fields=['deposit_mode', 'deposit_paid'])
        result = boss.post(f'{path}{booking.pk}/replace-provider/',
            {'seat_number': 2, 'provider_account_id': self.members[2].pk}, format='json')
        self.assertNotEqual(result.data['code'], 0, result.data)
        self.assertEqual(booking.seats.get(number=2).status, 'VACANT')
        self.assertTrue(find_conflicting_reservations(self.members[0], self.start, self.end).exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True)
    def test_payment_creates_both_original_price_participations(self):
        booking = self.create_team()
        decide(booking.pk, self.members[0], accept=True)
        decide(booking.pk, self.members[1], accept=True)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])
        result = client.post(f'/api/orders/reservations/{booking.pk}/convert/',
                             {'client_request_id': 'double-original-evidence'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        booking.refresh_from_db()
        attribution = booking.order.original_attribution
        self.assertEqual(attribution.status, 'ACTIVE')
        self.assertEqual(list(attribution.segments.order_by('slot').values_list('provider_account_id', 'original_share')),
                         [(self.members[0].pk, 15000), (self.members[1].pk, 15000)])

    @override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_paid_team_booking_can_start_above_capacity_and_complete(self):
        from orders.services import count_capacity_orders
        booking = self.create_team()
        decide(booking.pk, self.members[0], accept=True)
        decide(booking.pk, self.members[1], accept=True)
        for account in self.members[:2]:
            from django.contrib.auth import get_user_model
            provider = get_user_model().objects.get(pk=account.legacy_mapping.legacy_user_id)
            for _ in range(3):
                Order.objects.create(customer=self.boss, customer_account=self.boss_account,
                    provider=provider, provider_account=account, service=self.service, amount=1000,
                    status=Order.Status.GRABBED, payment_status=Order.PaymentStatus.PAID)
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])
        result = boss.post(f'/api/orders/reservations/{booking.pk}/convert/',
                           {'client_request_id': 'double-over-capacity'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        booking.refresh_from_db()
        order = booking.order
        self.assertEqual([count_capacity_orders(a) for a in self.members[:2]], [3, 3])
        def evidence():
            buffer = BytesIO()
            Image.new('RGB', (2, 2), 'white').save(buffer, format='PNG')
            return SimpleUploadedFile('proof.png', buffer.getvalue(), content_type='image/png')
        with TemporaryDirectory(prefix='xa-reservation-team-') as folder, override_settings(MEDIA_ROOT=folder):
            for index, account in enumerate(self.members[:2]):
                client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                response = client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': evidence()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db()
                self.assertEqual(order.status, 'GRABBED' if index == 0 else 'IN_SERVICE')
            self.assertEqual([count_capacity_orders(a) for a in self.members[:2]], [4, 4])
            for account in self.members[:2]:
                client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                response = client.post(f'/api/orders/orders/{order.pk}/complete/',
                                       {'completion_image': evidence()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            order.refresh_from_db()
            self.assertEqual(order.status, 'COMPLETED')
            self.assertIsNone(order.settled_at)

    @override_settings(DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True)
    def test_team_uses_selected_coupon_and_replayed_payment_cannot_debit_twice(self):
        from coupons.models import Coupon, UserCoupon
        from orders.tests.factories import make_boss_type
        self.boss_account.boss_type = make_boss_type(discount_rate=85)
        self.boss_account.save(update_fields=['boss_type'])
        coupon = Coupon.objects.create(name='双陪金额券', discount_type='DIRECT', amount=1000)
        owned = UserCoupon.objects.create(account=self.boss_account, coupon=coupon)
        booking = self.create_team()
        decide(booking.pk, self.members[0], accept=True)
        decide(booking.pk, self.members[1], accept=True)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])
        path = f'/api/orders/reservations/{booking.pk}/'
        quote = client.get(path + 'quote/').data['data']
        self.assertEqual((quote['original_amount'], quote['amount']), (30000, 24500))
        payload = {'discount_selection_token': quote['discount_selection_token'],
                   'expected_amount': quote['amount'], 'client_request_id': 'team-coupon-once'}
        response = client.post(path + 'convert/', payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        booking.refresh_from_db()
        self.assertEqual(booking.order.providers.count(), 2)
        owned.refresh_from_db()
        self.assertEqual((owned.status, owned.order_id), ('USED', booking.order_id))
        self.assertNotEqual(client.post(path + 'convert/', payload, format='json').data['code'], 0)
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 75500)
        self.assertEqual(Transaction.objects.filter(order=booking.order, tx_type='PAY').count(), 1)
