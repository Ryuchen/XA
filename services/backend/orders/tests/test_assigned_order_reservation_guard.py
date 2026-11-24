"""Assigned orders cannot be rejected and paid appointments keep their slot."""
import json
import os
from datetime import timedelta
from unittest import skipUnless

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

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '转单先校验转入陪玩抽成快照（原预约时段冲突校验在其后）属转单正式契约深水区（18-盘点 转单抽成快照待联调）；最终联调启用')
    def test_transfer_checks_original_slot_and_moves_its_lock(self):
        replacement = make_provider(deposit_mode='EARNINGS')
        self.accounts[replacement.pk] = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = self.accounts[replacement.pk]
        replacement.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge())
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        booking_id, order_id = self.book(self.provider, start, 'reservation-transfer-1')
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        competing = self.ok(self.client.post('/api/orders/reservations/', {
            'service_id': self.service.pk, 'provider_account_id': self.accounts[replacement.pk].pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json'))
        payload = {'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
            'new_provider_id': self.accounts[replacement.pk].pk, 'split_type': 'PERCENT',
            'split_value': 0, 'penalty_amount': 0}]}
        self.login(self.admin)
        blocked = self.client.post(f'/api/admin/orders/{order_id}/transfer/', payload, format='json')
        self.assertNotEqual(blocked.data['code'], 0, blocked.data)
        self.assertIn('原预约时段', blocked.data['msg'])
        self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, self.accounts[self.provider.pk].pk)
        self.login(self.boss)
        self.ok(self.client.post(f"/api/orders/reservations/{competing['id']}/cancel/", {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', payload, format='json'))
        self.assertFalse(find_conflicting_reservations(self.accounts[self.provider.pk], start,
                                                      start + timedelta(hours=1)).exists())
        self.assertTrue(find_conflicting_reservations(self.accounts[replacement.pk], start,
            start + timedelta(hours=1)).filter(pk=booking_id).exists())
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
