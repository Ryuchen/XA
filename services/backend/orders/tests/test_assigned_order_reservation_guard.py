"""Assigned orders cannot be rejected and paid appointments keep their slot."""
import json
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, Reservation
from orders.reservation_services import find_conflicting_reservations
from orders.services import count_active_orders, count_capacity_orders, refresh_escort_status
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from wallet.models import SystemConfig, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AssignedOrderReservationGuardTest(APITransactionTestCase):
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order

    def setUp(self):
        flow.ReleaseFlowTest.setUp(self)
        from console.reservation_deadlines import save_deadlines
        from engagement.models import ClubOperationConfig
        save_deadlines(30, 60, 0, lambda *_: None)
        ClubOperationConfig.objects.get_or_create(pk=1)
        self.provider.escort_profile.deposit_mode = 'EARNINGS'
        self.provider.escort_profile.save(update_fields=['deposit_mode'])

    def book(self, provider, start, key):
        self.login(self.boss)
        booking = self.ok(self.client.post('/api/orders/reservations/', {
            'service_id': self.service.pk, 'provider_account_id': self.accounts[provider.pk].pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json'))
        self.login(provider)
        self.ok(self.client.post(f"/api/orders/reservations/{booking['id']}/confirm/", {}, format='json'))
        self.login(self.boss)
        quote = self.ok(self.client.get(f"/api/orders/reservations/{booking['id']}/quote/"))
        order = self.ok(self.client.post(f"/api/orders/reservations/{booking['id']}/convert/", {
            'client_request_id': key, 'expected_amount': quote['amount'],
            'expected_service': quote['service'],
        }, format='json'))['order']
        return booking['id'], order['id']

    def test_assigned_order_cannot_be_rejected_even_by_legacy_client(self):
        order = self.new_order()
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertIn('START', detail['allowed_actions'])
        self.assertNotIn('REJECT', detail['allowed_actions'])
        self.assertIs(detail['can_reject'], False)
        before = (order.status, order.provider_account_id, order.auto_cancel_at,
                  order.status_logs.count(), order.transactions.count())
        wallet_before = Wallet.objects.get(account=self.accounts[self.boss.pk]).balance
        response = self.client.post(f'/api/orders/orders/{order.pk}/reject/', {'reason': '临时有事'}, format='json')
        self.assertEqual(response.data['errorCode'], 'ORDER_SELF_REJECTION_DISABLED')
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id, order.auto_cancel_at,
                          order.status_logs.count(), order.transactions.count()), before)
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance, wallet_before)

    def test_future_paid_booking_does_not_consume_capacity_but_remains_outstanding(self):
        self.ok(self.recharge())
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        _, order_id = self.book(self.provider, start, 'reservation-capacity-1')
        account = self.accounts[self.provider.pk]
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.status, 'AVAILABLE')
        self.assertEqual(count_active_orders(account), 1)  # Still blocks deposit return / account closure.
        self.assertEqual(count_capacity_orders(account), 0)
        refresh_escort_status(account_ids=[account.pk])
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.status, 'AVAILABLE')
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot(), 'client_request_id': 'reservation-start-once'},
                                 format='multipart'))
        replay = self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot(), 'client_request_id': 'reservation-start-once'},
                                 format='multipart'))
        self.assertTrue(replay['request_replayed'])
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual((self.provider.escort_profile.status, count_capacity_orders(account)), ('BUSY', 1))

    def test_booking_can_be_paid_and_started_when_normal_capacity_is_full(self):
        self.ok(self.recharge())
        balance_before = Wallet.objects.get(account=self.accounts[self.boss.pk]).balance
        account = self.accounts[self.provider.pk]
        from orders.capacity import CONFIG_KEY
        SystemConfig.objects.create(key=CONFIG_KEY, value=json.dumps({'max_concurrent_orders': 2, 'version': 1}))
        for _ in range(2):
            Order.objects.create(customer=self.boss, customer_account=self.accounts[self.boss.pk],
                provider=self.provider, provider_account=account, service=self.service,
                status='IN_SERVICE', payment_status='PAID', amount=1000)
        self.provider.escort_profile.status = 'BUSY'
        self.provider.escort_profile.save(update_fields=['status'])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        _, order_id = self.book(self.provider, start, 'reservation-capacity-full')
        self.assertEqual(count_capacity_orders(account), 2)
        self.assertEqual(count_active_orders(account), 3)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(count_capacity_orders(account), 3)
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance,
                         balance_before - Order.objects.get(pk=order_id).amount)

    def test_staff_starts_booking_at_full_capacity(self):
        self.ok(self.recharge())
        account = self.accounts[self.provider.pk]
        for _ in range(Order.MAX_CONCURRENT_ORDERS):
            Order.objects.create(customer=self.boss, customer_account=self.accounts[self.boss.pk],
                provider=self.provider, provider_account=account, service=self.service,
                status='IN_SERVICE', payment_status='PAID', amount=1000)
        self.provider.escort_profile.status = 'BUSY'
        self.provider.escort_profile.save(update_fields=['status'])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        _, order_id = self.book(self.provider, start, 'reservation-staff-full')
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/start/', {}, format='json'))
        self.assertEqual(count_capacity_orders(account), Order.MAX_CONCURRENT_ORDERS + 1)

    def test_paid_appointment_blocks_overlap_until_staff_refunds(self):
        self.ok(self.recharge())
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        booking_id, order_id = self.book(self.provider, start, 'reservation-no-reject-1')
        self.login(self.provider)
        response = self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '无法接单'}, format='json')
        self.assertEqual(response.data['errorCode'], 'ORDER_SELF_REJECTION_DISABLED')
        self.login(self.boss)
        overlap = self.client.post('/api/orders/reservations/', {
            'service_id': self.service.pk, 'provider_account_id': self.accounts[self.provider.pk].pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json')
        self.assertNotEqual(overlap.data['code'], 0, overlap.data)
        self.assertTrue(find_conflicting_reservations(self.accounts[self.provider.pk], start,
                                                     start + timedelta(hours=1)).filter(pk=booking_id).exists())
        self.assertFalse(find_conflicting_reservations(self.accounts[self.provider.pk], start + timedelta(hours=1),
                                                      start + timedelta(hours=2)).exists())
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order_id}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/refund/', {
            'reason': '客服协调取消', 'plan_token': preview['plan_token'],
            'client_request_id': 'reservation-cancel-1',
        }, format='json'))
        self.assertFalse(find_conflicting_reservations(self.accounts[self.provider.pk], start,
                                                      start + timedelta(hours=1)).exists())
        self.assertEqual(Order.objects.get(pk=order_id).payment_status, 'REFUNDED')
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='REFUND').count(), 1)

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True)
    def test_transfer_checks_original_slot_and_moves_its_lock(self):
        replacement = make_provider(deposit_mode='EARNINGS')
        self.accounts[replacement.pk] = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = self.accounts[replacement.pk]
        replacement.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge())
        now = timezone.localtime()
        start = (now + timedelta(days=2)).replace(hour=23, minute=30, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        booking_id, order_id = self.book(self.provider, start, 'reservation-transfer-1')
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        competing = self.ok(self.client.post('/api/orders/reservations/', {
            'service_id': self.service.pk, 'provider_account_id': self.accounts[replacement.pk].pk,
            'start_time': start.isoformat(), 'end_time': end.isoformat(),
        }, format='json'))
        payload = {'reason': '客服协调原预约转单', 'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
            'new_provider_id': self.accounts[replacement.pk].pk,
            'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方确认原价份额'}}]}
        transfer_url = f'/api/admin/orders/{order_id}/transfer/'
        self.login(self.admin)
        blocked = self.client.post(transfer_url, payload, format='json')
        self.assertNotEqual(blocked.data['code'], 0, blocked.data)
        self.assertIn('原预约时段', blocked.data['msg'])
        self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, self.accounts[self.provider.pk].pk)
        self.login(self.boss)
        self.ok(self.client.post(f"/api/orders/reservations/{competing['id']}/cancel/", {}, format='json'))

        schedule_url = '/api/users/escorts/schedule/'
        today_slot = {'weekday': now.weekday(), 'start_minute': 0, 'end_minute': 1440}
        overnight_slot = {'weekday': start.weekday(), 'start_minute': 1410, 'end_minute': 15}
        self.login(replacement)
        self.ok(self.client.put(schedule_url, {'slots': [today_slot, overnight_slot]}, format='json'))
        orig = Order.objects.get(pk=order_id)
        tx_before = list(orig.transactions.values_list('pk', 'amount'))
        transfers_before = orig.status_logs.filter(action='TRANSFER').count()
        self.login(self.admin)
        blocked = self.client.post(transfer_url, payload, format='json')
        self.assertEqual(blocked.data['code'], 400, blocked.data)
        self.assertIn('未覆盖原预约时段', blocked.data['msg'])
        orig.refresh_from_db()
        self.assertEqual((orig.status, orig.provider_account_id), ('IN_SERVICE', self.accounts[self.provider.pk].pk))
        self.assertEqual(list(orig.transactions.values_list('pk', 'amount')), tx_before)
        self.assertEqual(orig.status_logs.filter(action='TRANSFER').count(), transfers_before)
        self.assertTrue(find_conflicting_reservations(self.accounts[self.provider.pk], start, end).filter(pk=booking_id).exists())
        self.assertFalse(find_conflicting_reservations(self.accounts[replacement.pk], start, end).exists())

        self.login(replacement)
        self.ok(self.client.put(schedule_url, {'slots': [today_slot, {**overnight_slot, 'end_minute': 30}]}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(transfer_url, payload, format='json'))
        self.assertFalse(find_conflicting_reservations(self.accounts[self.provider.pk], start, end).exists())
        self.assertTrue(find_conflicting_reservations(self.accounts[replacement.pk], start, end).filter(pk=booking_id).exists())
        self.assertEqual(list(orig.transactions.values_list('pk', 'amount')), tx_before)
        self.assertEqual(Reservation.objects.get(pk=booking_id).status, 'CONVERTED')
        self.login(replacement)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertTrue(detail['reservation_origin']['can_open'])
        booking = self.ok(self.client.get(f'/api/orders/reservations/{booking_id}/'))
        self.assertEqual(booking['provider']['account_id'], self.accounts[self.provider.pk].pk)
        self.assertEqual([item['account_id'] for item in booking['current_providers']], [self.accounts[replacement.pk].pk])
        bookings = self.ok(self.client.get('/api/orders/reservations/', {'role':'provider'}))
        self.assertIn(booking_id, [item['id'] for item in bookings['items']])
        self.login(self.provider)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/reservations/{booking_id}/'))['provider']['account_id'],
                         self.accounts[self.provider.pk].pk)
        other = make_provider(deposit_mode='EARNINGS')
        self.accounts[other.pk] = get_or_create_account_for_legacy_user(other)
        other.escort_profile.account = self.accounts[other.pk]; other.escort_profile.save(update_fields=['account'])
        self.login(other)
        denied = self.client.get(f'/api/orders/reservations/{booking_id}/')
        self.assertEqual(denied.data['code'], 404)
        self.login(self.boss)
        booking = self.ok(self.client.get(f'/api/orders/reservations/{booking_id}/'))
        self.assertEqual(booking['current_providers'][0]['account_id'], self.accounts[replacement.pk].pk)
