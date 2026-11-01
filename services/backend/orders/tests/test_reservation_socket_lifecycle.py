"""Reservation changes reach real authenticated socket consumers after commit."""
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ReservationSocketLifecycleTest(APITransactionTestCase):
    def test_multiple_devices_rejection_conversion_and_reconnect(self):
        boss, provider = make_user(), make_provider()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        make_wallet(boss, balance=10000)
        service = make_service(price=1000)
        staff = make_console_user(['order:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, staff_account)
        AdminMembership.objects.filter(user=staff).update(account=staff_account)
        tokens = [issue_account_tokens(account)[0] for account in (boss_account, provider_account, staff_account)]
        clients = []
        for token in tokens:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            clients.append(client)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={tokens[index]}')
                       for index in (0, 0, 1, 1, 2)]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])

            async def updated(socket, reservation_id):
                for _ in range(8):
                    event = await socket.receive_json_from(timeout=2)
                    if event['type'] == 'reservation_status_update' and event['data']['reservation_id'] == reservation_id:
                        return
                self.fail(f'预约 {reservation_id} 状态事件未送达')

            async def create(when):
                response = await database_sync_to_async(clients[0].post)('/api/orders/reservations/', {
                    'provider_account_id': provider_account.pk, 'service_id': service.pk,
                    'start_time': when.isoformat(), 'end_time': (when + timedelta(hours=1)).isoformat(),
                }, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                return response.data['data']['id']

            try:
                rejected_id = await create(start)
                for socket in sockets:
                    await updated(socket, rejected_id)
                response = await database_sync_to_async(clients[1].post)(
                    f'/api/orders/reservations/{rejected_id}/reject/', {'reason': '测试档期'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                for socket in sockets:
                    await updated(socket, rejected_id)
                self.assertEqual((await database_sync_to_async(clients[0].get)(
                    f'/api/orders/reservations/{rejected_id}/')).data['data']['status'], 'REJECTED')

                converted_id = await create(start + timedelta(hours=2))
                for socket in sockets:
                    await updated(socket, converted_id)
                response = await database_sync_to_async(clients[1].post)(
                    f'/api/orders/reservations/{converted_id}/confirm/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                for socket in sockets:
                    await updated(socket, converted_id)
                await sockets[1].disconnect()
                quote = await database_sync_to_async(clients[0].get)(f'/api/orders/reservations/{converted_id}/quote/')
                self.assertEqual(quote.data['code'], 0, quote.data)
                response = await database_sync_to_async(clients[0].post)(
                    f'/api/orders/reservations/{converted_id}/convert/', {
                        'client_request_id': 'socket-convert-test', 'expected_amount': quote.data['data']['amount'],
                        'expected_service': quote.data['data']['service'],
                    }, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                for index in (0, 2, 3, 4):
                    await updated(sockets[index], converted_id)
                sockets[1] = WebsocketCommunicator(application, f'/ws/orders/?token={tokens[0]}')
                self.assertTrue((await sockets[1].connect())[0])
                detail = await database_sync_to_async(clients[0].get)(f'/api/orders/reservations/{converted_id}/')
                self.assertEqual((detail.data['data']['status'], detail.data['data']['order']),
                                 ('CONVERTED', response.data['data']['order']['id']))
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()

    def test_create_confirm_cancel_reaches_the_other_party_and_not_unrelated_users(self):
        boss, provider, other = make_user(), make_provider(), make_user()
        accounts = []
        for user in (boss, provider, other):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        EscortProfile.objects.filter(user=provider).update(account=accounts[1])
        service = make_service(price=1000)
        viewer, no_view = make_console_user(['order:view']), make_console_user([])
        for staff in (viewer, no_view):
            account = get_or_create_account_for_legacy_user(staff)
            link_legacy_relations(staff, account)
            AdminMembership.objects.filter(user=staff).update(account=account)
            accounts.append(account)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        clients = []
        for token in tokens:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            clients.append(client)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])

            async def expect(socket, event_type, reservation_id=None):
                # Reservation and message invalidations may arrive in either order.
                for _ in range(4):
                    event = await socket.receive_json_from(timeout=2)
                    if event['type'] == event_type:
                        if reservation_id is not None:
                            self.assertEqual(event['data'], {'reservation_id': reservation_id})
                        return
                self.fail(f'{event_type} not received')

            try:
                response = await database_sync_to_async(clients[0].post)('/api/orders/reservations/', {
                    'provider_account_id': accounts[1].pk, 'service_id': service.pk,
                    'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
                }, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                reservation_id = response.data['data']['id']
                await expect(sockets[1], 'reservation_status_update', reservation_id)
                await expect(sockets[0], 'reservation_status_update', reservation_id)
                await expect(sockets[3], 'reservation_status_update', reservation_id)
                self.assertTrue(await sockets[2].receive_nothing(timeout=0.1))
                self.assertTrue(await sockets[4].receive_nothing(timeout=0.1))
                response = await database_sync_to_async(clients[1].post)(
                    f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                await expect(sockets[0], 'reservation_status_update', reservation_id)
                await expect(sockets[1], 'reservation_status_update', reservation_id)
                await expect(sockets[3], 'reservation_status_update', reservation_id)
                self.assertTrue(await sockets[2].receive_nothing(timeout=0.1))
                response = await database_sync_to_async(clients[0].post)(
                    f'/api/orders/reservations/{reservation_id}/cancel/', {'reason': '测试档期变更'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                await expect(sockets[1], 'reservation_status_update', reservation_id)
                await expect(sockets[0], 'reservation_status_update', reservation_id)
                await expect(sockets[3], 'reservation_status_update', reservation_id)
                self.assertTrue(await sockets[2].receive_nothing(timeout=0.1))
                detail = await database_sync_to_async(clients[1].get)(f'/api/orders/reservations/{reservation_id}/')
                self.assertEqual(detail.data['data']['status'], 'CANCELLED')
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()
