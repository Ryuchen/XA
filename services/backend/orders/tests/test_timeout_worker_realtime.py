"""A real worker publishes an expired order's updates over isolated Redis."""
import os
import asyncio
import subprocess
import sys
from datetime import timedelta
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import Order, OrderApplication
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from site_messages.models import Message
from wallet.models import Transaction, Wallet


@skipUnless(os.environ.get('XA_ORDER_TIMEOUT_REALTIME_TEST') == '1', 'Requires isolated Redis and Celery')
class TimeoutWorkerRealtimeTest(APITransactionTestCase):
    def test_team_applicants_receive_worker_cancellation(self):
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        users = [make_user(), make_provider(), make_provider(), make_user()]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        service = make_service(price=10000, billing_provider_count=2)
        for user, account in zip(users[1:3], accounts[1:3]):
            user.escort_profile.account = account
            user.escort_profile.pass_tier = 'BLACK'
            user.escort_profile.pass_expires_at = timezone.now() + timedelta(days=1)
            user.escort_profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
            user.escort_profile.service_items.add(service)
        make_wallet(users[0], balance=50000)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        clients = []
        for token in tokens:
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token); clients.append(client)
        created = clients[0].post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'timeout-team-realtime-isolated'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        grabbed = clients[1].post(f'/api/orders/orders/{order_id}/grab/',
            {'team_size': 2, 'partner_id': accounts[2].pk}, format='json')
        self.assertEqual(grabbed.data['code'], 0, grabbed.data)
        application_id = OrderApplication.objects.get(order_id=order_id, applicant=accounts[1]).pk
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))

        def start_worker():
            worker = subprocess.Popen([sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
                cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                auto_cancel_pending_order.delay(order_id)
                return worker
            except BaseException:
                worker.terminate(); worker.wait(timeout=10)
                raise

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens[1:]]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])
            worker = None
            try:
                worker = await database_sync_to_async(start_worker)()
                async def wait_for_message(socket):
                    for _ in range(8):
                        event = await socket.receive_json_from(timeout=15)
                        if event['type'] == 'site_message_update':
                            return event
                    self.fail('Team member did not receive a message update')
                events = await asyncio.gather(*(wait_for_message(socket) for socket in sockets[:2]))
                self.assertEqual([event['data'] for event in events], [{}, {}])
                self.assertTrue(await sockets[2].receive_nothing(timeout=.2))
            finally:
                if worker:
                    worker.terminate()
                    try:
                        await database_sync_to_async(worker.wait)(timeout=10)
                    except subprocess.TimeoutExpired:
                        worker.kill(); await database_sync_to_async(worker.wait)(timeout=10)
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()
        for account, client in zip(accounts[1:3], clients[1:3]):
            notice = Message.objects.get(recipient_account=account, related_order_id=order_id, title='报名订单已取消')
            self.assertEqual(notice.action_url, f'/pages/orders/index?application_id={application_id}')
            detail = client.get(f'/api/orders/orders/{order_id}/applications/{application_id}/mine/').data['data']
            self.assertEqual((detail['order']['status'], detail['can_confirm']), ('CANCELLED', False))
            confirm = client.post(f'/api/orders/orders/{order_id}/applications/{application_id}/action/',
                {'action': 'CONFIRM_TEAM'}, format='json')
            self.assertNotEqual(confirm.data['code'], 0)
        self.assertEqual(Wallet.objects.get(user=users[0]).balance, 50000)
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='REFUND').count(), 1)

    def test_worker_refund_and_socket_notification_cross_process(self):
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        boss, other = make_user(), make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(boss, boss_account)
        link_legacy_relations(other, other_account)
        make_wallet(boss, balance=50000)
        tokens = [issue_account_tokens(account)[0] for account in (boss_account, other_account)]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        service = make_service(price=10000)
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'STAFF',
            'client_request_id': 'timeout-worker-realtime-isolated'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order_id = result.data['data']['id']
        # Keep the actual transition and worker; advance only the isolated deadline.
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))

        def dispatch_and_wait():
            worker = subprocess.Popen([sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
                cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                auto_cancel_pending_order.delay(order_id)
                return worker
            except BaseException:
                worker.terminate(); worker.wait(timeout=10)
                raise

        async def verify():
            owner = WebsocketCommunicator(application, f'/ws/orders/?token={tokens[0]}')
            stranger = WebsocketCommunicator(application, f'/ws/orders/?token={tokens[1]}')
            self.assertTrue((await owner.connect())[0]); self.assertTrue((await stranger.connect())[0])
            worker = None
            try:
                worker = await database_sync_to_async(dispatch_and_wait)()
                for _ in range(6):
                    event = await owner.receive_json_from(timeout=15)
                    if event['type'] == 'site_message_update':
                        break
                else:
                    self.fail('Owner did not receive the worker message update')
                self.assertEqual(event['data'], {})
                self.assertTrue(await stranger.receive_nothing(timeout=.2))
            finally:
                if worker:
                    worker.terminate()
                    try:
                        await database_sync_to_async(worker.wait)(timeout=10)
                    except subprocess.TimeoutExpired:
                        worker.kill(); await database_sync_to_async(worker.wait)(timeout=10)
                await owner.disconnect(); await stranger.disconnect()

        async_to_sync(verify)()
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(user=boss).balance, 50000)
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='REFUND').count(), 1)
        messages = client.get('/api/messages/', {'type': 'ORDER'}).data['data']['messages']
        self.assertTrue(any(row['related_order_id'] == order_id and row['title'] == '订单超时已取消' for row in messages))
