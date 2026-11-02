"""Order actions follow the same business identity as the three-end views."""
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order
from orders.tests.factories import make_provider, make_user
from orders.tests import test_release_flow as release_flow
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False)
class OrderActorRealTest(APITestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge
    new_order = release_flow.ReleaseFlowTest.new_order
    settle_after_window = release_flow.ReleaseFlowTest.settle_after_window

    def test_explicit_provider_account_starts_completes_and_settles_despite_stale_order_user(self):
        order = self.new_order()
        stale = make_provider()
        self.accounts[stale.pk] = get_or_create_account_for_legacy_user(stale)
        Order.objects.filter(pk=order.pk).update(provider=stale)
        self.login(stale)
        denied = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': release_flow.screenshot()}, format='multipart')
        self.assertNotEqual(denied.data['code'], 0)
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertIn('START', detail['allowed_actions'])
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': release_flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider_account=self.accounts[self.provider.pk])
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        self.settle_after_window()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.provider.pk]).balance, 8000)
        self.assertEqual(order.transactions.filter(tx_type=Transaction.TxType.INCOME).count(), 1)

    def test_explicit_provider_account_can_reject_but_stale_user_cannot(self):
        order = self.new_order()
        stale = make_provider()
        self.accounts[stale.pk] = get_or_create_account_for_legacy_user(stale)
        stale_boss = make_user()
        self.accounts[stale_boss.pk] = get_or_create_account_for_legacy_user(stale_boss)
        Order.objects.filter(pk=order.pk).update(provider=stale, customer=stale_boss)
        self.login(stale)
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order.pk}/reject/', {}, format='json').data['code'], 0)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/reject/', {}, format='json'))
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id, order.provider_id), ('PENDING', None, None))
        self.assertEqual(order.providers.filter(released_at__isnull=True).count(), 0)
        self.login(self.boss)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertTrue(any(item['title'] == '陪玩已拒单' for item in messages))
        self.login(stale_boss)
        other_messages = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertFalse(any(item['title'] == '陪玩已拒单' for item in other_messages))

    def test_legacy_provider_can_find_start_and_complete_order(self):
        order = self.new_order()
        Order.objects.filter(pk=order.pk).update(provider_account=None)
        order.providers.update(provider_account=None)
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertIs(detail['my_assignment_released'], False)
        rows = self.ok(self.client.get('/api/orders/orders/', {'status': 'grabbed'}))
        self.assertIs(next(row for row in rows if row['id'] == order.pk)['my_assignment_released'], False)
        order.providers.update(released_at=timezone.now())
        released_detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        released_rows = self.ok(self.client.get('/api/orders/orders/', {'status': 'grabbed'}))
        self.assertIs(released_detail['my_assignment_released'], True)
        self.assertIs(next(row for row in released_rows if row['id'] == order.pk)['my_assignment_released'], True)
        order.providers.update(released_at=None)
        self.assertIn('START', detail['allowed_actions'])
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': release_flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider_account=self.accounts[self.provider.pk])
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        self.settle_after_window()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)

    def test_legacy_boss_order_is_visible_and_cancel_refunds_original_wallet(self):
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'legacy-owner-order'}, format='json'))
        order = Order.objects.get(pk=data['id'])
        Order.objects.filter(pk=order.pk).update(customer_account=None)
        other = make_user()
        self.accounts[other.pk] = get_or_create_account_for_legacy_user(other)
        self.login(other)
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {}, format='json').data['code'], 0)
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertIn('CANCEL', detail['allowed_actions'])
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {}, format='json'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 21000)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
