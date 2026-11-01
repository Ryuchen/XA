"""Cross-module acceptance in an isolated database; no real merchant or network.

The buyer's route is passed to the internal test-domain order preparation
function: there is no production mini-program prepay endpoint in this test.
Keys and channel notices below are freshly generated synthetic test data.
The scanner is invoked explicitly; no production scheduler is exercised.
"""
import base64
import json
import secrets
import time
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import AsyncClient, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation, PointLedger
from integrations.wechatpay_test_callback import TestCallbackConfig
from integrations.wechatpay_v3 import WeChatPayV3
from orders.direct_clearing import post_test_clearing
from orders.direct_payment import prepare_direct_order
from orders.direct_payment_delivery_models import DirectPaymentInbox, DirectPaymentOutbox
from orders.direct_payment_inbox import scan_payment_inbox
from orders.direct_payment_outbox import scan_payment_outbox
from orders.direct_payment_quote import quote_direct_payment
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import IncomeHold, ProviderReport, SystemConfig, Transaction, Wallet


FEE = {'policy_id': 'acceptance-rate', 'rate_numerator': 6, 'rate_denominator': 1000,
       'fee_basis': 'PAYMENT_TOTAL', 'rounding': 'UP'}


@override_settings(DEBUG=True, WECHAT_DIRECT_DOMAIN_TESTING=True, WECHAT_DIRECT_CALLBACK_TESTING=True,
                   WECHAT_DIRECT_TEST_FEE_POLICY=FEE, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, WECOM_DISPATCH_NEW_ORDERS=False)
class DirectPaymentIsolatedAcceptance(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.aes_key = secrets.token_bytes(32)
        protocol = WeChatPayV3(app_id='wx-acceptance', merchant_id='mch-acceptance',
            merchant_serial='temporary-merchant', merchant_private_key=merchant.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            api_v3_key=cls.aes_key, platform_keys={'PUB_KEY_ID_test': cls.platform.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)})
        cls.config = TestCallbackConfig(protocol, 'temporary-quarantine', secrets.token_bytes(32))

    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        config = override_settings(MEDIA_ROOT=media.name, WECHAT_DIRECT_CALLBACK_TEST_CONFIG=self.config)
        config.enable()
        self.addCleanup(config.disable)
        self.boss, self.provider = make_user(), make_provider()
        self.staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        self.staff_account = get_or_create_account_for_legacy_user(self.staff)
        self.boss_account.openid, self.boss_account.wechat_appid = 'synthetic-openid', 'wx-acceptance'
        self.boss_account.is_openid_bound = True
        self.boss_account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        EscortProfile.objects.filter(user=self.provider).update(account=self.provider_account)
        self.staff.admin_membership.account = self.staff_account
        self.staff.admin_membership.save(update_fields=['account'])
        make_wallet(self.boss, balance=50000)
        make_wallet(self.provider)
        self.service = make_service(price=10000)
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_NONE', defaults={'value': '0'})
        self.tokens = {account.pk: issue_account_tokens(account)[0]
                       for account in (self.boss_account, self.provider_account, self.staff_account)}
        self.client = APIClient()

    def auth(self, account):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.tokens[account.pk])

    def upload(self):
        data = BytesIO()
        Image.new('RGB', (4, 4), 'white').save(data, format='PNG')
        return SimpleUploadedFile('evidence.png', data.getvalue(), content_type='image/png')

    def prepare(self, route):
        quote = quote_direct_payment(service_units=10000, appid='wx-acceptance', mchid='mch-acceptance')
        return prepare_direct_order(SimpleNamespace(account=self.boss_account, legacy_user=self.boss,
            data={'service_id': self.service.pk, 'expected_amount': 10000, 'dispatch_route': route,
                  'dispatch_mode': 'AUTO_GRAB' if route == 'PUBLIC' else 'BOSS_SELECT',
                  'client_request_id': 'acceptance-' + route, 'expected_payment_total': quote.total_fen,
                  'expected_fee_quote_hash': quote.quote_hash}), appid='wx-acceptance', mchid='mch-acceptance')

    def test_unpaid_dashboard_count_matches_order_filter(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'dashboard:view']
        role.save(update_fields=['permissions'])
        intent = self.prepare('STAFF')
        self.auth(self.staff_account)
        stats = self.client.get('/api/admin/dashboard/stats').data['data']
        result = self.client.get('/api/admin/orders/', {'status': 'PENDING', 'payment_status': 'UNPAID'}).data['data']
        self.assertEqual(stats['pending_unpaid_total'], 1)
        self.assertEqual(result['total'], 1)
        self.assertEqual([row['id'] for row in result['list']], [intent.order_id])
        self.assertEqual(self.client.get('/api/admin/orders/', {'status': 'PENDING', 'payment_status': 'PAID'}).data['data']['total'], 0)

    def callback(self, intent, *, amount=None, event_id='evt-acceptance'):
        total = intent.amount_fen if amount is None else amount
        payload = {'appid': intent.appid, 'mchid': intent.mchid, 'out_trade_no': intent.merchant_order_no,
                   'transaction_id': 'synthetic-tx-' + event_id, 'trade_type': 'JSAPI', 'trade_state': 'SUCCESS',
                   'success_time': timezone.now().isoformat(), 'payer': {'openid': intent.openid},
                   'amount': {'total': total, 'payer_total': total, 'currency': 'CNY', 'payer_currency': 'CNY'}}
        nonce = secrets.token_hex(6)
        ciphertext = AESGCM(self.aes_key).encrypt(nonce.encode(), json.dumps(payload).encode(), b'acceptance')
        body = json.dumps({'id': event_id, 'event_type': 'TRANSACTION.SUCCESS', 'resource_type': 'encrypt-resource',
            'resource': {'original_type': 'transaction', 'algorithm': 'AEAD_AES_256_GCM', 'nonce': nonce,
                         'associated_data': 'acceptance', 'ciphertext': base64.b64encode(ciphertext).decode()}},
            separators=(',', ':')).encode()
        timestamp, header_nonce = str(int(time.time())), secrets.token_hex(12)
        message = timestamp.encode() + b'\n' + header_nonce.encode() + b'\n' + body + b'\n'
        signature = self.platform.sign(message, padding.PKCS1v15(), hashes.SHA256())
        headers = {'wechatpay-serial': 'PUB_KEY_ID_test', 'wechatpay-timestamp': timestamp,
                   'wechatpay-nonce': header_nonce, 'wechatpay-signature': base64.b64encode(signature).decode()}
        with override_settings(ROOT_URLCONF='integrations.wechatpay_test_callback_urls'):
            response = async_to_sync(AsyncClient().post)('/isolated/wechatpay/callback', data=body,
                content_type='application/json', headers=headers)
            replay = async_to_sync(AsyncClient().post)('/isolated/wechatpay/callback', data=body,
                content_type='application/json', headers=headers)
        self.assertEqual((response.status_code, replay.status_code), (204, 204))
        self.assertEqual(DirectPaymentInbox.objects.filter(notification_id=event_id).count(), 1)

    def scan(self):
        # Notifications and task scheduling are not delivered externally.
        with patch('orders.direct_payment._notify_paid'), patch('orders.notifier.notify_order_update'), \
             patch('orders.services.schedule_auto_cancel'), \
             patch('integrations.kook_config.get_kook_config', return_value=SimpleNamespace(auto_new_orders=False)):
            scan_payment_inbox()
            scan_payment_outbox()

    def run_route(self, route):
        old = timezone.now() - timedelta(hours=80)
        before = list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        with patch('django.utils.timezone.now', return_value=old):
            intent = self.prepare(route)
            self.callback(intent, event_id='evt-' + route)
            self.assertEqual(Order.objects.get(pk=intent.order_id).payment_status, 'UNPAID')
            self.scan()
            order = Order.objects.get(pk=intent.order_id)
            self.assertEqual((order.payment_status, order.dispatch_route), ('PAID', route))
            self.assertEqual(DirectPaymentOutbox.objects.filter(intent=intent).count(), 1)
            self.assertEqual(DirectPaymentOutbox.objects.get(intent=intent).status, 'DONE')
            self.auth(self.provider_account)
            listing = self.client.get('/api/orders/orders/?status=pending').data['data']
            if route == 'STAFF':
                self.assertNotIn(order.pk, [row['id'] for row in listing])
                self.assertEqual(self.client.get(f'/api/orders/orders/{order.pk}/').status_code, 404)
                self.assertEqual(self.client.get('/api/admin/orders/').status_code, 403)
                self.assertNotEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json').data['code'], 0)
                self.auth(self.staff_account)
                queue = self.client.get('/api/admin/orders/?dispatch_route=STAFF&status=PENDING&payment_status=PAID').data['data']['list']
                self.assertIn(order.pk, [row['id'] for row in queue])
                response = self.client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': self.provider_account.pk}, format='json')
            else:
                self.assertIn(order.pk, [row['id'] for row in listing])
                response = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            self.auth(self.provider_account)
            for action, name in (('start', 'entry_image'), ('complete', 'completion_image')):
                response = self.client.post(f'/api/orders/orders/{order.pk}/{action}/', {name: self.upload()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            report = ProviderReport.objects.get(order=order, provider=self.provider)
            response = self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': self.upload()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            response = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            self.auth(self.staff_account)
            response = self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        batch = post_test_clearing(order.pk)
        for book in ('FEN', 'UNIT'):
            lines = list(batch.lines.filter(book=book))
            self.assertEqual(sum(x.amount for x in lines if x.side == 'DEBIT'),
                             sum(x.amount for x in lines if x.side == 'CREDIT'))
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')), before)
        self.assertFalse(Transaction.objects.filter(order=order).exists())
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=order).exists())
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertEqual(post_test_clearing(order.pk).pk, batch.pk)

    def test_public_buyer_choice_full_test_flow(self):
        self.run_route('PUBLIC')

    def test_staff_buyer_choice_private_full_test_flow(self):
        self.run_route('STAFF')

    def test_valid_signature_wrong_amount_never_dispatches(self):
        intent = self.prepare('PUBLIC')
        self.callback(intent, amount=intent.amount_fen - 1, event_id='evt-wrong')
        self.scan()
        intent.refresh_from_db()
        self.assertNotEqual(intent.status, 'PAID')
        self.assertEqual(Order.objects.get(pk=intent.order_id).payment_status, 'UNPAID')
        self.assertEqual(DirectPaymentInbox.objects.get(notification_id='evt-wrong').status, 'REVIEW')
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=intent).exists())
        self.auth(self.provider_account)
        listing = self.client.get('/api/orders/orders/?status=pending').data['data']
        self.assertNotIn(intent.order_id, [row['id'] for row in listing])
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{intent.order_id}/grab/', {}, format='json').data['code'], 0)
