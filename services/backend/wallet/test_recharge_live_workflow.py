"""Real operator recharge/adjustment, customer spending, and refund updates."""
import io
import tempfile
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from users.models import EscortProfile
from wallet.models import Wallet


def proof():
    data = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(data, format='PNG')
    return SimpleUploadedFile('receipt.png', data.getvalue(), content_type='image/png')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class RechargeLiveWorkflowTest(APITransactionTestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.boss, self.other = make_user(), make_user()
        self.staff = make_console_user(['wallet:recharge', 'wallet:adjust', 'order:dispatch'])
        self.wallet_reviewer = make_console_user(['wallet:view'])
        self.accounts = {u.pk: get_or_create_account_for_legacy_user(u) for u in (self.boss, self.other, self.staff, self.wallet_reviewer)}
        for user in (self.staff, self.wallet_reviewer):
            user.admin_membership.account = self.accounts[user.pk]
            user.admin_membership.save(update_fields=['account'])
        self.tokens = {pk: issue_account_tokens(account)[0] for pk, account in self.accounts.items()}
        self.service = make_service(price=10000, commission_rate=20)

    def login(self, user):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.tokens[user.pk])

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def recharge(self):
        self.login(self.staff)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 20000, 'gift_amount': 1000,
            'trade_no': 'proof-' + uuid4().hex, 'proof_image': proof(), 'client_request_id': uuid4().hex,
        }, format='multipart'))

    def adjust(self):
        self.login(self.staff)
        wallet = Wallet.objects.get(user=self.boss)
        self.ok(self.client.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {
            'amount': -500, 'remark': '核对后调账', 'client_request_id': uuid4().hex,
        }, format='json'))

    def order(self):
        self.login(self.boss)
        result = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': uuid4().hex,
        }, format='json'))
        return result['id']

    def cancel(self, order_id):
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/cancel/', {'reason': '临时取消'}, format='json'))

    def snapshot(self):
        self.login(self.boss)
        return self.ok(self.client.get('/api/wallet/transactions/', {'page': 1, 'page_size': 20}))

    def test_wallet_events_and_ledger_follow_actual_customer_journey(self):
        async def verify():
            owner = WebsocketCommunicator(application, f'/ws/orders/?token={self.tokens[self.boss.pk]}')
            other = WebsocketCommunicator(application, f'/ws/orders/?token={self.tokens[self.other.pk]}')
            self.assertTrue((await owner.connect())[0])
            self.assertTrue((await other.connect())[0])

            async def wallet_event():
                for _ in range(10):
                    event = await owner.receive_json_from(timeout=2)
                    if event['type'] == 'wallet_update':
                        self.assertEqual(event['data']['account_id'], self.accounts[self.boss.pk].pk)
                        return
                self.fail('no wallet update')

            async def drain():
                while not await owner.receive_nothing(timeout=0.05):
                    await owner.receive_json_from(timeout=1)

            async def assert_other_has_no_wallet_event():
                # Recharge may publish a public club-feed update to all bosses.
                while not await other.receive_nothing(timeout=0.1):
                    event = await other.receive_json_from(timeout=1)
                    self.assertNotEqual(event['type'], 'wallet_update')

            try:
                await database_sync_to_async(self.recharge)()
                await wallet_event()
                snapshot = await database_sync_to_async(self.snapshot)()
                self.assertEqual(snapshot['balance'], 21000)
                self.assertEqual({row['tx_type'] for row in snapshot['transactions']}, {'TOPUP', 'GIFT'})
                await assert_other_has_no_wallet_event()
                await drain()

                await database_sync_to_async(self.adjust)()
                await wallet_event()
                snapshot = await database_sync_to_async(self.snapshot)()
                self.assertEqual(snapshot['balance'], 20500)
                self.assertEqual(snapshot['transactions'][0]['tx_type'], 'ADJUST')
                await assert_other_has_no_wallet_event()
                await drain()

                order_id = await database_sync_to_async(self.order)()
                await wallet_event()
                snapshot = await database_sync_to_async(self.snapshot)()
                self.assertEqual(snapshot['balance'], 10500)
                self.assertEqual(snapshot['transactions'][0]['tx_type'], 'PAY')
                await drain()
                await database_sync_to_async(self.cancel)(order_id)
                await wallet_event()
                snapshot = await database_sync_to_async(self.snapshot)()
                self.assertEqual(snapshot['balance'], 20500)
                self.assertEqual(snapshot['transactions'][0]['tx_type'], 'REFUND')
                await assert_other_has_no_wallet_event()
            finally:
                await owner.disconnect()
                await other.disconnect()

        async_to_sync(verify)()

    def test_gift_basket_updates_boss_provider_and_wallet_reviewer(self):
        self.recharge()
        provider = make_provider(display_name='隔离收礼陪玩', is_verified=True)
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, provider_account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        provider_token, _ = issue_account_tokens(provider_account)
        category = ServiceCategory.objects.create(name='隔离礼物', is_gift=True)
        gifts = [make_service(name=f'隔离礼物{index}', price=price, service_category=category, commission_rate=20)
                 for index, price in enumerate((2000, 3000))]
        payload = {'items': [{'gift_service_id': item.pk, 'quantity': 1} for item in gifts],
                   'provider_id': provider_account.pk, 'expected_amount': 5000, 'client_request_id': uuid4().hex}

        def send():
            self.login(self.boss)
            return self.client.post('/api/orders/gifts/send/', payload, format='json').data

        async def verify():
            tokens = [self.tokens[self.boss.pk], provider_token, self.tokens[self.wallet_reviewer.pk], self.tokens[self.other.pk]]
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])

            async def read_events(socket):
                events = []
                while not await socket.receive_nothing(timeout=0.15):
                    events.append(await socket.receive_json_from(timeout=1))
                return events

            try:
                result = await database_sync_to_async(send)()
                self.assertEqual(result['code'], 0, result)
                owner_events, recipient_events, reviewer_events, other_events = [await read_events(socket) for socket in sockets]
                owner, recipient, reviewer, other = [[event['data'] for event in events if event['type'] == 'wallet_update']
                    for events in (owner_events, recipient_events, reviewer_events, other_events)]
                self.assertEqual(len(owner), 2)
                self.assertEqual({item['account_id'] for item in owner}, {self.accounts[self.boss.pk].pk})
                self.assertEqual(len(recipient), 2)
                self.assertEqual({item['account_id'] for item in recipient}, {provider_account.pk})
                self.assertTrue({self.accounts[self.boss.pk].pk, provider_account.pk}.issubset(
                    {item['account_id'] for item in reviewer}))
                self.assertEqual(other, [])
                self.assertEqual(sum(event['type'] == 'site_message_update' for event in recipient_events), 2)
                self.assertFalse(any(event['type'] == 'site_message_update' for event in other_events))
                def recipient_inbox():
                    self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
                    return self.ok(self.client.get('/api/messages/'))
                inbox = await database_sync_to_async(recipient_inbox)()
                self.assertEqual(len([row for row in inbox['messages'] if row['title'] == '收到老板礼物']), 2)
                self.assertGreaterEqual(inbox['unread'], 2)
                snapshot = await database_sync_to_async(self.snapshot)()
                self.assertEqual(snapshot['balance'], 16000)
                provider_wallet = await database_sync_to_async(Wallet.objects.get)(account=provider_account)
                self.assertEqual(provider_wallet.balance, 4000)
                self.assertNotEqual((await database_sync_to_async(send)())['code'], 0)
                for socket in sockets:
                    self.assertFalse(any(event['type'] in ('wallet_update', 'site_message_update') for event in await read_events(socket)))
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()

        # A new gift while the recipient is disconnected remains visible on return.
        payload['client_request_id'] = uuid4().hex
        self.assertEqual(send()['code'], 0)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        inbox = self.ok(self.client.get('/api/messages/'))
        self.assertEqual(len([row for row in inbox['messages'] if row['title'] == '收到老板礼物']), 4)
        self.assertGreaterEqual(inbox['unread'], 4)
        self.assertEqual(Wallet.objects.get(account=provider_account).balance, 8000)

    def test_boss_purchase_and_staff_dispatch_publish_debit_once(self):
        self.recharge()
        own_request = {'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': uuid4().hex}
        dispatch_request = {'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
                            'game_rounds': 1, 'escort_mode': 'SINGLE', 'providers': [],
                            'expected_amount': 10000, 'client_request_id': uuid4().hex}

        def submit(user, url, payload):
            self.login(user)
            return self.client.post(url, payload, format='json').data

        def rolled_back_purchase():
            with transaction.atomic():
                result = submit(self.boss, '/api/orders/create/', {
                    **own_request, 'client_request_id': uuid4().hex,
                })
                transaction.set_rollback(True)
                return result

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={self.tokens[user.pk]}')
                       for user in (self.boss, self.wallet_reviewer, self.other)]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])

            async def read_wallet(socket):
                for _ in range(15):
                    event = await socket.receive_json_from(timeout=2)
                    if event['type'] == 'wallet_update':
                        self.assertEqual(event['data']['account_id'], self.accounts[self.boss.pk].pk)
                        return
                self.fail('wallet update missing')

            async def no_wallet(socket):
                while not await socket.receive_nothing(timeout=0.1):
                    event = await socket.receive_json_from(timeout=1)
                    self.assertNotEqual(event['type'], 'wallet_update')

            try:
                rolled_back = await database_sync_to_async(rolled_back_purchase)()
                self.assertEqual(rolled_back['code'], 0, rolled_back)
                for socket in sockets:
                    await no_wallet(socket)
                self.assertEqual((await database_sync_to_async(self.snapshot)())['balance'], 21000)
                for user, url, payload, remaining in (
                    (self.boss, '/api/orders/create/', own_request, 11000),
                    (self.staff, '/api/admin/orders/dispatch/', dispatch_request, 1000),
                ):
                    result = await database_sync_to_async(submit)(user, url, payload)
                    self.assertEqual(result['code'], 0, result)
                    await read_wallet(sockets[0])
                    await read_wallet(sockets[1])
                    await no_wallet(sockets[2])
                    snapshot = await database_sync_to_async(self.snapshot)()
                    self.assertEqual(snapshot['balance'], remaining)
                    self.assertEqual(snapshot['transactions'][0]['tx_type'], 'PAY')
                    replay = await database_sync_to_async(submit)(user, url, payload)
                    self.assertNotEqual(replay['code'], 0)
                    for socket in sockets:
                        await no_wallet(socket)
                failed = await database_sync_to_async(submit)(self.boss, '/api/orders/create/', {
                    **own_request, 'client_request_id': uuid4().hex,
                })
                self.assertNotEqual(failed['code'], 0)
                for socket in sockets:
                    await no_wallet(socket)
                snapshot = await database_sync_to_async(self.snapshot)()
                self.assertEqual(snapshot['balance'], 1000)
                self.assertEqual(sum(row['tx_type'] == 'PAY' for row in snapshot['transactions']), 2)
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()
