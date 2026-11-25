"""Real reservation-to-order lifecycle and committed wallet invalidation."""
import asyncio
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from club_accounts.services import get_or_create_account_for_legacy_user
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ReservationMoneyLifecycleTest(APITransactionTestCase):
    def setUp(self):
        flow.ReleaseFlowTest.setUp(self)
        from engagement.models import ClubOperationConfig
        ClubOperationConfig.objects.get_or_create(pk=1)
        from console.reservation_deadlines import save_deadlines
        save_deadlines(30, 60, 0, lambda *_: None)
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    finish = flow.ReleaseFlowTest.finish
    settle_after_window = flow.ReleaseFlowTest.settle_after_window

    def test_paid_reservation_cannot_be_rejected_into_public_pool(self):
        self.ok(self.recharge())
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        self.login(self.boss)
        booking = self.ok(self.client.post('/api/orders/reservations/', {
            'service_id': self.service.pk, 'provider_account_id': self.accounts[self.provider.pk].pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/reservations/{booking['id']}/confirm/", {}, format='json'))
        self.login(self.boss)
        quote = self.ok(self.client.get(f"/api/orders/reservations/{booking['id']}/quote/"))
        order_id = self.ok(self.client.post(f"/api/orders/reservations/{booking['id']}/convert/", {
            'client_request_id': 'reservation-rejection-pool', 'expected_amount': quote['amount'],
            'expected_service': quote['service'],
        }, format='json'))['order']['id']
        self.login(self.provider)
        inbox = self.ok(self.client.get('/api/messages/'))['messages']
        notice = next(row for row in inbox if row['title'] == '预约已转为订单' and row['related_order_id'] == order_id)
        self.assertIn('原预约时段', notice['preview'])
        self.assertNotIn('尽快开始', notice['preview'])
        response = self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json')
        self.assertEqual(response.data['errorCode'], 'ORDER_SELF_REJECTION_DISABLED')
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())

    def test_transferred_provider_can_open_the_original_reservation_slot(self):
        replacement = make_provider(deposit_mode='EARNINGS')
        self.accounts[replacement.pk] = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = self.accounts[replacement.pk]
        replacement.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge())
        self.login(self.boss)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        booking = self.ok(self.client.post('/api/orders/reservations/', {
            'service_id': self.service.pk, 'provider_account_id': self.accounts[self.provider.pk].pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/reservations/{booking['id']}/confirm/", {}, format='json'))
        self.login(self.boss)
        quote = self.ok(self.client.get(f"/api/orders/reservations/{booking['id']}/quote/"))
        result = self.ok(self.client.post(f"/api/orders/reservations/{booking['id']}/convert/", {
            'client_request_id': 'reservation-transfer-origin', 'expected_amount': quote['amount'],
            'expected_service': quote['service'],
        }, format='json'))
        order_id = result['order']['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
            'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
                           'new_provider_id': self.accounts[replacement.pk].pk,
                           # Segment orders derive shares from original-price facts.
                           'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
                           'original_share': {'retained': 4000, 'transferred': 6000,
                                              'agreement': '双方确认原预约履约段原价份额'}}],
            'reason': '隔离预约转单验证',
        }, format='json'))
        self.login(replacement)
        origin = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['reservation_origin']
        self.assertEqual((origin['id'], origin['can_open']), (booking['id'], True))
        self.assertEqual(timezone.datetime.fromisoformat(origin['start_time']), start)
        reservation = self.ok(self.client.get(f"/api/orders/reservations/{booking['id']}/"))
        self.assertEqual(reservation['status'], 'CONVERTED')
        self.assertIn(self.accounts[replacement.pk].pk,
                      [entry['account_id'] for entry in reservation['current_providers']])

    def test_booking_payment_notifies_wallet_then_actual_service_settles(self):
        self.ok(self.recharge())
        self.login(self.boss)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        reservation = self.ok(self.client.post('/api/orders/reservations/', {
            'service_id': self.service.pk, 'provider_account_id': self.accounts[self.provider.pk].pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/reservations/{reservation['id']}/confirm/", {}, format='json'))
        self.login(self.boss)
        quote = self.ok(self.client.get(f"/api/orders/reservations/{reservation['id']}/quote/"))
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(f'account_{self.accounts[self.boss.pk].pk}', channel)
        result = self.ok(self.client.post(f"/api/orders/reservations/{reservation['id']}/convert/", {
            'client_request_id': 'reservation-wallet-event', 'expected_amount': quote['amount'],
            'expected_service': quote['service'],
        }, format='json'))
        order = Order.objects.get(pk=result['order']['id'])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11000)
        expected_origin = {'id': reservation['id'], 'reservation_no': reservation['reservation_no']}
        for user, url in (
            (self.boss, f'/api/orders/orders/{order.pk}/'),
            (self.provider, f'/api/orders/orders/{order.pk}/'),
            (self.admin, f'/api/admin/orders/{order.pk}/'),
        ):
            self.login(user)
            detail = self.ok(self.client.get(url))
            origin = detail['reservation_origin']
            self.assertEqual({key: origin[key] for key in expected_origin}, expected_origin)
            if user in (self.boss, self.provider):
                self.assertTrue(origin['can_open'])
            self.assertEqual(timezone.datetime.fromisoformat(origin['start_time']), start)
            self.assertEqual(timezone.datetime.fromisoformat(origin['end_time']), start + timedelta(hours=1))
        self.login(self.boss)
        reservations = self.ok(self.client.get(f"/api/orders/reservations/{reservation['id']}/"))
        self.assertEqual((reservations['order'], reservations['order_no']), (order.pk, order.order_no))

        async def received_wallet_update():
            while True:
                try:
                    event = await asyncio.wait_for(layer.receive(channel), timeout=0.2)
                except asyncio.TimeoutError:
                    return False
                if event['type'] == 'wallet_update':
                    return event['data']['account_id'] == self.accounts[self.boss.pk].pk
        self.assertTrue(async_to_sync(received_wallet_update)(), '预约成单扣款后缺少老板钱包更新事件')

        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.settle_after_window()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)
