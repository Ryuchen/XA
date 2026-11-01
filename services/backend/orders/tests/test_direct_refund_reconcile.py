"""Refund notification followed by a signed test-channel query and real state updates."""
import base64
import json
import time
from copy import deepcopy
from io import StringIO

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import AsyncClient, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from integrations.wechatpay_test_callback import TestCallbackConfig
from integrations.wechatpay_gateway import TransportResponse, WeChatPayGateway
from orders.direct_refund_inbox import ingest_verified_refund_notice
from orders.direct_refund_delivery_models import DirectRefundInbox
from orders.direct_refund_reconcile import reconcile_test_refund_notice
from orders.models import Order
from orders.tests.test_direct_refund_inbox import RefundInboxFixture


class SignedQueryTransport:
    def __init__(self, fixture, *, fail=False, during_query=None):
        self.fixture, self.fail, self.during_query, self.calls = fixture, fail, during_query, []

    def send(self, method, path, headers, body, **kwargs):
        assert not connection.in_atomic_block
        self.calls.append((method, path))
        if self.during_query:
            self.during_query()
        if self.fail:
            raise TimeoutError('simulated channel timeout')
        notice = self.fixture.payload()
        amount = dict(notice['amount'], settlement_total=10000, settlement_refund=10000,
                      discount_refund=0, currency='CNY')
        payload = {**notice, 'status': notice['refund_status'], 'amount': amount}
        response_body = json.dumps(payload, separators=(',', ':')).encode()
        timestamp, nonce = str(int(time.time())), 'test-nonce'
        signature = self.fixture.platform_key.sign((timestamp + '\n' + nonce + '\n').encode()
            + response_body + b'\n', padding.PKCS1v15(), hashes.SHA256())
        headers = {'Wechatpay-Serial': 'PUB_KEY_ID_test', 'Wechatpay-Timestamp': timestamp,
                   'Wechatpay-Nonce': nonce, 'Wechatpay-Signature': base64.b64encode(signature).decode()}
        return TransportResponse(200, headers, response_body)


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectRefundReconcileTest(RefundInboxFixture, TransactionTestCase):
    def ingest(self):
        return ingest_verified_refund_notice('mch-test', self.notice())

    def gateway(self, **kwargs):
        transport = SignedQueryTransport(self, **kwargs)
        return WeChatPayGateway(self.protocol, transport=transport), transport

    def test_query_confirms_same_refund_without_wallet_credit_and_is_idempotent(self):
        inbox = self.ingest()
        gateway, transport = self.gateway()
        self.assertEqual(reconcile_test_refund_notice(inbox.pk, gateway=gateway).status, 'DONE')
        self.assertEqual(reconcile_test_refund_notice(inbox.pk, gateway=gateway).status, 'DONE')
        self.assertEqual(transport.calls, [('GET', '/v3/refund/domestic/refunds/' + self.refund.merchant_refund_no)])
        self.refund.refresh_from_db()
        self.wallet.refresh_from_db()
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((self.refund.status, order.payment_status, self.wallet.balance), ('SUCCESS', 'REFUNDED', 50000))
        self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())
        client = APIClient()
        token, _ = issue_account_tokens(self.intent.account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.get(f'/api/orders/orders/{order.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['payment_status'], 'REFUNDED')

    def test_unknown_query_keeps_original_refund_and_later_query_resolves(self):
        inbox = self.ingest()
        gateway, _ = self.gateway(fail=True)
        row = reconcile_test_refund_notice(inbox.pk, gateway=gateway)
        self.assertEqual((row.status, row.last_error), ('PENDING', 'QUERY_UNCONFIRMED'))
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'RESERVED')
        gateway, transport = self.gateway()
        self.assertEqual(reconcile_test_refund_notice(inbox.pk, gateway=gateway).status, 'DONE')
        self.assertTrue(transport.calls[0][1].endswith(self.refund.merchant_refund_no))
        self.assertEqual(self.intent.channel_refunds.count(), 1)

    def test_owner_receives_committed_order_update_after_success(self):
        inbox = self.ingest()
        gateway, _ = self.gateway()
        token, _ = issue_account_tokens(self.intent.account)

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                row = await database_sync_to_async(reconcile_test_refund_notice)(inbox.pk, gateway=gateway)
                self.assertEqual(row.status, 'DONE')
                event = await socket.receive_json_from()
                self.assertEqual(event['type'], 'order_status_update')
                self.assertEqual(event['data']['payment_status'], 'REFUNDED')
                self.assertTrue(await socket.receive_nothing(timeout=0.2))
            finally:
                await socket.disconnect()
        async_to_sync(verify)()

    def test_callback_then_management_scan_reaches_boss_order(self):
        # The callback is a real ASGI request with encrypted, signed test data.
        payload = self.payload()
        nonce = '123456789012'
        ciphertext = AESGCM(self.api_key).encrypt(nonce.encode(), json.dumps(payload).encode(), b'refund')
        body = json.dumps({'id': 'command-notice', 'event_type': 'REFUND.SUCCESS',
            'resource_type': 'encrypt-resource', 'resource': {'original_type': 'refund',
            'algorithm': 'AEAD_AES_256_GCM', 'nonce': nonce, 'associated_data': 'refund',
            'ciphertext': base64.b64encode(ciphertext).decode()}}).encode()
        timestamp, header_nonce = str(int(time.time())), 'testnonce'
        signature = self.platform_key.sign((timestamp + '\n' + header_nonce + '\n').encode()
            + body + b'\n', padding.PKCS1v15(), hashes.SHA256())
        headers = {'wechatpay-serial': 'PUB_KEY_ID_test', 'wechatpay-timestamp': timestamp,
                   'wechatpay-nonce': header_nonce, 'wechatpay-signature': base64.b64encode(signature).decode()}
        gateway, transport = self.gateway()
        config = TestCallbackConfig(self.protocol, 'test-key', b'y' * 32)
        with override_settings(DEBUG=True, WECHAT_DIRECT_CALLBACK_TESTING=True,
                WECHAT_DIRECT_CALLBACK_TEST_CONFIG=config, WECHAT_DIRECT_REFUND_TEST_GATEWAY=gateway,
                ROOT_URLCONF='integrations.wechatpay_test_callback_urls'):
            response = async_to_sync(AsyncClient().post)('/isolated/wechatpay/callback', data=body,
                content_type='application/json', headers=headers)
            self.assertEqual(response.status_code, 204)
            self.assertEqual(DirectRefundInbox.objects.get(notification_id='command-notice').status, 'PENDING')
            output = StringIO()
            call_command('reconcile_test_refund_inbox', stdout=output)
            self.assertIn('DONE=1', output.getvalue())
            call_command('reconcile_test_refund_inbox', stdout=StringIO())
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(DirectRefundInbox.objects.get(notification_id='command-notice').status, 'DONE')
        client = APIClient()
        token, _ = issue_account_tokens(self.intent.account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(client.get(f'/api/orders/orders/{self.intent.order_id}/').data['data']['payment_status'], 'REFUNDED')

    def test_scan_requires_the_explicit_isolated_test_domain(self):
        self.ingest()
        gateway, transport = self.gateway()
        with override_settings(WECHAT_DIRECT_DOMAIN_TESTING=False, WECHAT_DIRECT_REFUND_TEST_GATEWAY=gateway):
            with self.assertRaises(CommandError):
                call_command('reconcile_test_refund_inbox', stdout=StringIO())
        self.assertEqual(transport.calls, [])

    def test_conflicting_notice_arriving_during_query_prevents_application(self):
        inbox = self.ingest()
        altered = deepcopy(self.payload())
        altered['amount']['payer_refund'] = 9000
        def conflict():
            ingest_verified_refund_notice('mch-test', self.notice(altered, nonce=b'123456789013'))
        gateway, _ = self.gateway(during_query=conflict)
        self.assertEqual(reconcile_test_refund_notice(inbox.pk, gateway=gateway).status, 'REVIEW')
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'REVIEW')
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
