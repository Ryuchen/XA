"""Real grab receipts distinguish retries, competitors and new operations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from site_messages.models import Message
from wallet.models import IdempotencyKey


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class GrabReceiptTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def prepare(self, team=False):
        other = make_provider(display_name='另一位陪玩')
        for user in (self.provider, other):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        if team:
            self.service.billing_provider_count = 2
            self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'dispatch_mode': 'BOSS_SELECT' if team else 'AUTO_GRAB', 'client_request_id': 'grab-receipt-order'}, format='json'))
        Order.objects.filter(pk=data['id']).update(pool_entered_at=timezone.now()-timedelta(hours=1))
        return Order.objects.get(pk=data['id']), other

    def test_grab_replay_competitor_and_later_order_state(self):
        order, other = self.prepare()
        url = f'/api/orders/orders/{order.pk}/grab/'
        self.login(self.provider)
        payload = {'team_size': 1, 'client_request_id': 'grab-original'}
        self.ok(self.client.post(url, payload, format='json'))
        replay = self.ok(self.client.post(url, payload, format='json'))
        self.assertTrue(replay['request_replayed'])
        self.assertEqual(order.status_logs.filter(action='GRAB').count(), 1)
        self.assertEqual(Message.objects.filter(recipient_account=self.accounts[self.boss.pk], related_order_id=order.pk, title='大神已接单').count(), 1)
        changed = self.client.post(url, {'team_size': 2, 'partner_id': self.accounts[other.pk].pk,
                                        'client_request_id': 'grab-original'}, format='json')
        self.assertEqual(changed.status_code, 409)
        self.assertNotEqual(self.client.post(url, {'team_size': 1, 'client_request_id': 'grab-new'}, format='json').data['code'], 0)
        self.assertNotEqual(self.client.post(url, {'team_size': 1}, format='json').data['code'], 0)
        self.login(other)
        self.assertNotEqual(self.client.post(url, {'team_size': 1, 'client_request_id': 'grab-other'}, format='json').data['code'], 0)
        self.assertFalse(IdempotencyKey.objects.filter(account=self.accounts[other.pk], request_id='grab-other').exists())
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/reject/', {'reason': '隔离测试时间冲突'}, format='json'))
        replay = self.ok(self.client.post(url, payload, format='json'))
        self.assertEqual((replay['status'], replay['request_replayed']), ('PENDING', True))
        self.login(other)
        self.ok(self.client.post(url, {'team_size': 1, 'client_request_id': 'grab-other'}, format='json'))
        self.login(self.provider)
        replay = self.ok(self.client.post(url, payload, format='json'))
        self.assertEqual(replay['status'], 'GRABBED')
        self.assertFalse(replay['can_operate'])
        self.assertNotIn('START', replay['allowed_actions'])

    def test_team_application_retry_does_not_duplicate_invitation(self):
        order, partner = self.prepare(team=True)
        self.login(self.provider)
        url = f'/api/orders/orders/{order.pk}/grab/'
        payload = {'team_size': 2, 'partner_id': self.accounts[partner.pk].pk, 'client_request_id': 'team-application'}
        self.ok(self.client.post(url, payload, format='json'))
        self.assertTrue(self.ok(self.client.post(url, payload, format='json'))['request_replayed'])
        self.assertEqual(OrderApplication.objects.filter(order=order).count(), 1)
        self.assertEqual(Message.objects.filter(recipient_account=self.accounts[partner.pk], related_order_id=order.pk, title='收到组队邀请').count(), 1)
        changed = self.client.post(url, {'team_size': 1, 'client_request_id': 'team-application'}, format='json')
        self.assertEqual(changed.status_code, 409)

    def test_concurrent_retry_and_competitor(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL row locks')
        order, other = self.prepare()
        tokens = [issue_account_tokens(self.accounts[user.pk])[0] for user in (self.provider, other)]
        barrier = Barrier(3)
        def send(index):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0 if index < 2 else 1])
                barrier.wait(timeout=10)
                response = client.post(f'/api/orders/orders/{order.pk}/grab/',
                    {'team_size': 1, 'client_request_id': 'same-grab' if index < 2 else 'competitor-grab'}, format='json')
                return response.data['code']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(send, range(3)))
        self.assertIn(results, ([0, 0, 400], [400, 400, 0]))
        self.assertEqual(order.status_logs.filter(action='GRAB').count(), 1)
        self.assertEqual(Message.objects.filter(recipient_account=self.accounts[self.boss.pk], related_order_id=order.pk, title='大神已接单').count(), 1)
