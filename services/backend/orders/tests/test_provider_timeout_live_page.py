"""Open invitation, boss detail and admin dashboard refresh after a separate worker cancels."""
import os
import subprocess
import sys
from datetime import timedelta
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import Order, OrderApplication
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction, Wallet


@skipUnless(os.environ.get('XA_PROVIDER_TIMEOUT_REALTIME_H5_DIR'), 'Requires isolated ASGI, Redis and Celery')
class ProviderTimeoutLivePageTest(TransactionTestCase):
    def test_open_team_invitation_updates_when_worker_expires_order(self):
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        users = [make_user(), make_provider(display_name='隔离报名人'), make_provider(display_name='隔离队友')]
        staff = make_console_user(['dashboard:view', 'order:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        service = make_service(name='实时双陪套餐', price=10000, billing_provider_count=2)
        for user, account in zip(users[1:], accounts[1:]):
            user.escort_profile.account = account
            user.escort_profile.pass_tier = 'BLACK'
            user.escort_profile.pass_expires_at = timezone.now() + timedelta(days=1)
            user.escort_profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
            user.escort_profile.service_items.add(service)
        make_wallet(users[0], balance=50000)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        created = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'timeout-open-h5-isolated'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        invited = client.post(f'/api/orders/orders/{order_id}/grab/', {'team_size': 2, 'partner_id': accounts[2].pk}, format='json')
        self.assertEqual(invited.data['code'], 0, invited.data)
        OrderApplication.objects.get(order_id=order_id, applicant=accounts[1])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        detail = client.get(f'/api/orders/orders/{order_id}/').data['data']
        self.assertEqual(detail['status'], 'PENDING')
        self.assertIn('CANCEL', detail['allowed_actions'])
        staff_token = issue_account_tokens(staff_account)[0]
        staff_client = APIClient(); staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        stats = staff_client.get('/api/admin/dashboard/stats').data['data']
        self.assertEqual((stats['pending_paid_total'], stats['pending_paid_routes']['public'], stats['paid_amount_total']),
                         (1, 1, 20000))
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        env = {**os.environ, 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false'}
        worker = None

        def start_worker():
            nonlocal worker
            worker = subprocess.Popen([sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
                cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            auto_cancel_pending_order.delay(order_id)

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}')
                       for token in (tokens[0], tokens[2], staff_token)]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])

            async def wait_cancelled(socket):
                for _ in range(10):
                    event = await socket.receive_json_from(timeout=60)
                    if (event['type'] == 'order_status_update' and event['data'].get('id') == order_id
                            and event['data'].get('status') == 'CANCELLED'):
                        return event
                self.fail(f'取消更新未送达: {order_id}')

            try:
                await database_sync_to_async(start_worker)()
                boss_event = await wait_cancelled(sockets[0])
                self.assertEqual(boss_event['data']['payment_status'], 'REFUNDED')
                await wait_cancelled(sockets[1])
                await wait_cancelled(sockets[2])
            finally:
                for socket in sockets:
                    await socket.disconnect()

        try:
            async_to_sync(verify)()
            self.assertEqual(Wallet.objects.get(user=users[0]).balance, 50000)
            self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='REFUND', status='SUCCESS').count(), 1)
            detail = client.get(f'/api/orders/orders/{order_id}/').data['data']
            self.assertEqual((detail['status'], detail['payment_status']), ('CANCELLED', 'REFUNDED'))
            self.assertNotIn('CANCEL', detail['allowed_actions'])
            inbox = client.get('/api/messages/', {'type': 'ORDER'}).data['data']['messages']
            self.assertTrue(any(row['related_order_id'] == order_id and row['title'] == '订单超时已取消' for row in inbox))
            stats = staff_client.get('/api/admin/dashboard/stats').data['data']
            self.assertEqual((stats['pending_paid_total'], stats['pending_paid_routes']['public'], stats['paid_amount_total']),
                             (0, 0, 0))
        finally:
            if worker and worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill(); worker.wait(timeout=10)