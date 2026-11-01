import base64
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, current_thread
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.handlers.asgi import ASGIRequest
from django.db import DatabaseError, close_old_connections, transaction
from django.test import AsyncClient, RequestFactory, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import Resolver404, resolve

from club_accounts.services import get_or_create_account_for_legacy_user
from integrations.wechatpay_test_callback import TestCallbackConfig, isolated_callback
from integrations.wechatpay_v3 import VerifiedNotification, WeChatPayV3
from orders.direct_callback_models import (DirectCallbackEnvelope, DirectCallbackQuarantine,
                                            DirectCallbackQuarantineObservation)
from orders.direct_payment import DirectPaymentError
from orders.direct_payment import VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.direct_payment_delivery_models import DirectPaymentInbox, DirectPaymentOutbox
from orders.direct_payment_inbox import ingest_verified_payment_notice, process_payment_inbox
from orders.direct_refund_delivery_models import DirectRefundInbox
from orders.direct_refund import reserve_full_refund
from orders.direct_refund_inbox import ingest_verified_refund_notice
from orders.models import Order
from orders.tests.factories import make_service, make_user, make_wallet
from django.utils import timezone


@override_settings(DEBUG=True, WECHAT_DIRECT_DOMAIN_TESTING=True, WECHAT_DIRECT_CALLBACK_TESTING=True,
                   ROOT_URLCONF='integrations.wechatpay_test_callback_urls', WECOM_DISPATCH_NEW_ORDERS=False)
class IsolatedCallbackTest(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.api_key = b'x' * 32
        cls.quarantine_key = b'z' * 32
        cls.protocol = WeChatPayV3(
            app_id='wx-test', merchant_id='mch-test', merchant_serial='merchant-serial',
            merchant_private_key=cls.merchant.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            api_v3_key=cls.api_key, platform_keys={'PUB_KEY_ID_test': cls.platform.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)})

    def setUp(self):
        self.config_override = override_settings(WECHAT_DIRECT_CALLBACK_TEST_CONFIG=TestCallbackConfig(
            self.protocol, 'test-quarantine-v1', self.quarantine_key))
        self.config_override.enable()
        self.addCleanup(self.config_override.disable)
        self.clock = patch('integrations.wechatpay_v3.time.time', return_value=1700000000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        account.openid, account.wechat_appid, account.is_openid_bound = 'test-openid', 'wx-test', True
        account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=account, legacy_user=user,
            data={'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'callback-1'})
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        self.payload = {'appid': 'wx-test', 'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
            'transaction_id': 'tx-callback-1', 'trade_type': 'JSAPI', 'trade_state': 'SUCCESS',
            'success_time': self.intent.created_at.isoformat(), 'payer': {'openid': 'test-openid'},
            'amount': {'total': 10000, 'payer_total': 10000, 'currency': 'CNY', 'payer_currency': 'CNY'},
            'attach': 'private-not-retained'}

    def envelope(self, payload=None, event='TRANSACTION.SUCCESS', original='transaction', event_id='event-1',
                 encryption_nonce='123456789012'):
        plain = json.dumps(payload if payload is not None else self.payload).encode()
        ciphertext = AESGCM(self.api_key).encrypt(encryption_nonce.encode('ascii'), plain, b'associated')
        envelope = {'id': event_id, 'event_type': event, 'resource_type': 'encrypt-resource',
                    'resource': {'original_type': original, 'algorithm': 'AEAD_AES_256_GCM',
                                 'nonce': encryption_nonce, 'associated_data': 'associated',
                                 'ciphertext': base64.b64encode(ciphertext).decode()}}
        return json.dumps(envelope, separators=(',', ':')).encode()

    def headers(self, body, nonce='nonce'):
        message = b'1700000000\n' + nonce.encode() + b'\n' + body + b'\n'
        signature = self.platform.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return {'wechatpay-serial': 'PUB_KEY_ID_test', 'wechatpay-timestamp': '1700000000',
                'wechatpay-nonce': nonce, 'wechatpay-signature': base64.b64encode(signature).decode()}

    def request(self, body, headers=None, extra=()):
        headers = headers or self.headers(body)
        wire = [(b'host', b'testserver'), (b'content-type', b'application/json'),
                (b'content-length', str(len(body)).encode())]
        wire += [(name.encode(), value.encode()) for name, value in headers.items()]
        wire += list(extra)
        scope = {'type': 'http', 'method': 'POST', 'path': '/isolated/wechatpay/callback',
                 'query_string': b'', 'headers': wire, 'server': ('testserver', 80),
                 'client': ('127.0.0.1', 1234), 'scheme': 'http'}
        return ASGIRequest(scope, io.BytesIO(body))

    def test_http_ack_only_after_authenticated_inbox_commit(self):
        body = self.envelope()
        client = AsyncClient()
        response = async_to_sync(client.post)('/isolated/wechatpay/callback', data=body,
                                               content_type='application/json', headers=self.headers(body))
        self.assertEqual(response.status_code, 204)
        row = DirectPaymentInbox.objects.get()
        self.assertEqual((row.merchant_id, row.status), ('mch-test', 'PENDING'))
        self.assertNotIn('attach', row.controlled_payload)
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')
        self.assertFalse(DirectPaymentOutbox.objects.exists())
        self.assertEqual(isolated_callback(self.request(body)).status_code, 204)
        self.assertEqual(DirectPaymentInbox.objects.count(), 1)
        with patch('orders.direct_payment._notify_paid'):
            process_payment_inbox(row.pk)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.filter(intent=self.intent).count(), 1)

    def test_refund_notice_is_stored_but_never_marked_success_by_http(self):
        payload = {'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
                   'transaction_id': 'tx-1', 'out_refund_no': 'refund-1', 'refund_id': 'wx-refund-1',
                   'refund_status': 'SUCCESS', 'success_time': '2026-10-27T10:00:00+08:00',
                   'amount': {'total': 10000, 'refund': 10000, 'payer_total': 10000, 'payer_refund': 10000}}
        body = self.envelope(payload, event='REFUND.SUCCESS', original='refund', event_id='refund-event')
        self.assertEqual(isolated_callback(self.request(body)).status_code, 204)
        self.assertEqual(DirectRefundInbox.objects.get().status, 'REVIEW')
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')

    def test_disabled_wsgi_duplicate_and_bad_signature_fail_closed(self):
        body = self.envelope()
        with override_settings(WECHAT_DIRECT_CALLBACK_TESTING=False):
            self.assertEqual(isolated_callback(self.request(body)).status_code, 404)
        with patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            self.assertEqual(isolated_callback(self.request(body)).status_code, 404)
        request = RequestFactory().post('/isolated/wechatpay/callback', body, content_type='application/json')
        self.assertEqual(isolated_callback(request).status_code, 400)
        duplicate = self.request(body, extra=((b'WeChatPay-Serial', b'PUB_KEY_ID_test'),))
        self.assertEqual(isolated_callback(duplicate).status_code, 400)
        bad = self.headers(body)
        bad['wechatpay-signature'] = 'AAAA'
        self.assertEqual(isolated_callback(self.request(body, bad)).status_code, 400)
        self.assertFalse(DirectCallbackQuarantine.objects.exists())
        self.assertFalse(DirectPaymentInbox.objects.exists())
        with self.assertRaises(Resolver404):
            resolve('/isolated/wechatpay/callback', urlconf='config.urls')

    def test_oversize_and_ambiguous_length_are_rejected_before_accepting_evidence(self):
        body = self.envelope()
        duplicate = self.request(body, extra=((b'Content-Length', str(len(body)).encode()),))
        self.assertEqual(isolated_callback(duplicate).status_code, 400)
        oversize = b'x' * (2 * 1024 * 1024 + 1)
        self.assertEqual(isolated_callback(self.request(oversize)).status_code, 400)
        request = self.request(body)
        request.scope['headers'] = [(name, b'1' if name.lower() == b'content-length' else value)
                                    for name, value in request.scope['headers']]
        self.assertEqual(isolated_callback(request).status_code, 400)
        self.assertFalse(DirectCallbackQuarantine.objects.exists())
        self.assertFalse(DirectPaymentInbox.objects.exists())

    def test_authenticated_undecodable_is_encrypted_quarantined_and_never_acked(self):
        body = json.dumps({'id': 'event-1', 'event_type': 'TRANSACTION.UNKNOWN',
                           'resource_type': 'encrypt-resource', 'private': 'secret-marker'}).encode()
        self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        row = DirectCallbackQuarantine.objects.get()
        encrypted = bytes(row.encrypted_body)
        self.assertNotIn(b'secret-marker', encrypted)
        self.assertNotIn(body, encrypted)
        self.assertNotIn(self.api_key, encrypted)
        self.assertNotIn(self.quarantine_key, encrypted)
        self.assertEqual(row.body_hash, hashlib.sha256(body).hexdigest())
        key_der = self.platform.public_key().public_bytes(serialization.Encoding.DER,
                                                           serialization.PublicFormat.SubjectPublicKeyInfo)
        self.assertEqual(row.platform_key_hash, hashlib.sha256(key_der).hexdigest())
        aad = f'body\n{row.merchant_id}\n{row.body_hash}\n{row.platform_key_hash}\n{row.encryption_key_id}'.encode()
        self.assertEqual(AESGCM(self.quarantine_key).decrypt(bytes(row.body_nonce), encrypted, aad), body)
        observation = row.observations.get()
        aad = f'headers\n{row.pk}\n{observation.receipt_hash}\n{observation.encryption_key_id}'.encode()
        saved = AESGCM(self.quarantine_key).decrypt(bytes(observation.headers_nonce), bytes(observation.encrypted_headers), aad)
        self.assertEqual(json.loads(saved), self.headers(body))
        self.assertEqual(observation.reason, 'AUTHENTICATED_INVALID')
        self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        self.assertEqual(DirectCallbackQuarantine.objects.count(), 1)
        self.assertEqual(DirectCallbackQuarantineObservation.objects.count(), 1)
        self.assertEqual(bytes(DirectCallbackQuarantine.objects.get().encrypted_body), encrypted)
        self.assertEqual(isolated_callback(self.request(body, self.headers(body, nonce='another'))).status_code, 503)
        self.assertEqual(DirectCallbackQuarantine.objects.count(), 1)
        self.assertEqual(DirectCallbackQuarantineObservation.objects.count(), 2)
        nonces = list(DirectCallbackQuarantineObservation.objects.values_list('headers_nonce', flat=True))
        self.assertNotEqual(bytes(nonces[0]), bytes(nonces[1]))

    def test_valid_signature_inbox_rejection_is_quarantined_after_rollback(self):
        body = self.envelope()
        with patch('integrations.wechatpay_test_callback.ingest_verified_payment_notice', side_effect=DirectPaymentError('private')):
            self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        self.assertFalse(DirectPaymentInbox.objects.exists())
        observation = DirectCallbackQuarantineObservation.objects.get()
        self.assertEqual(observation.reason, 'AUTHENTICATED_INBOX_REJECTED')

    def test_large_authenticated_payload_rejected_by_inbox_is_quarantined(self):
        self.payload['attach'] = 'x' * (300 * 1024)
        body = self.envelope()
        self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        self.assertFalse(DirectPaymentInbox.objects.exists())
        self.assertEqual(DirectCallbackQuarantineObservation.objects.get().reason, 'AUTHENTICATED_INBOX_REJECTED')

    def test_inbox_rejection_after_paid_marks_conflict_without_undoing_payment(self):
        body = self.envelope()
        self.assertEqual(isolated_callback(self.request(body)).status_code, 204)
        with patch('orders.direct_payment._notify_paid'):
            process_payment_inbox(DirectPaymentInbox.objects.get().pk)
        self.payload['attach'] = 'x' * (300 * 1024)
        rejected = self.envelope()
        self.assertEqual(isolated_callback(self.request(rejected)).status_code, 503)
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'tx-callback-1'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')
        self.assertEqual(DirectCallbackQuarantineObservation.objects.get().reason, 'AUTHENTICATED_INBOX_REJECTED')

    def test_signed_ambiguous_json_is_quarantined_not_interpreted(self):
        outer = (b'{"id":"event-1","id":"event-2","event_type":"TRANSACTION.SUCCESS",'
                 b'"resource_type":"encrypt-resource","resource":{}}')
        cases = [outer]
        for raw in (b'{"appid":"wx-test","appid":"other"}', b'{"total":NaN}', b'{"total":1e999}'):
            ciphertext = AESGCM(self.api_key).encrypt(b'123456789012', raw, b'associated')
            envelope = {'id': 'event-1', 'event_type': 'TRANSACTION.SUCCESS', 'resource_type': 'encrypt-resource',
                        'resource': {'original_type': 'transaction', 'algorithm': 'AEAD_AES_256_GCM',
                                     'nonce': '123456789012', 'associated_data': 'associated',
                                     'ciphertext': base64.b64encode(ciphertext).decode()}}
            cases.append(json.dumps(envelope, separators=(',', ':')).encode())
        for body in cases:
            with self.subTest(body_hash=hashlib.sha256(body).hexdigest()):
                self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        self.assertEqual(DirectCallbackQuarantine.objects.count(), len(cases))
        self.assertFalse(DirectPaymentInbox.objects.exists())

    def test_same_body_retransmit_after_key_fix_can_recover(self):
        body = self.envelope()
        protocol = WeChatPayV3(
            app_id='wx-test', merchant_id='mch-test', merchant_serial='merchant-serial',
            merchant_private_key=self.merchant.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            api_v3_key=b'y' * 32, platform_keys={'PUB_KEY_ID_test': self.platform.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)})
        with override_settings(WECHAT_DIRECT_CALLBACK_TEST_CONFIG=TestCallbackConfig(protocol, 'test-quarantine-v1', self.quarantine_key)):
            self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        self.assertEqual(isolated_callback(self.request(body, self.headers(body, nonce='retry'))).status_code, 204)
        marker = DirectCallbackEnvelope.objects.get()
        self.assertFalse(marker.has_conflict)
        self.assertEqual((marker.bodies.count(), marker.bodies.get().accepted, marker.bodies.get().quarantined), (1, True, True))
        with patch('orders.direct_payment._notify_paid'):
            process_payment_inbox(DirectPaymentInbox.objects.get().pk)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'PAID')

    def test_previous_paid_event_then_bad_same_id_retains_paid_fact_and_holds_dispatch(self):
        good = self.envelope()
        self.assertEqual(isolated_callback(self.request(good)).status_code, 204)
        with patch('orders.direct_payment._notify_paid'):
            process_payment_inbox(DirectPaymentInbox.objects.get().pk)
        damaged = b'{"id":"event-1","event_type":"TRANSACTION.UNKNOWN","resource_type":"encrypt-resource","resource":{}}'
        self.assertEqual(isolated_callback(self.request(damaged)).status_code, 503)
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'tx-callback-1'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')
        self.assertTrue(DirectCallbackEnvelope.objects.get().has_conflict)

    def test_accepted_payment_and_refund_cannot_share_the_same_id(self):
        body = self.envelope()
        self.assertEqual(isolated_callback(self.request(body)).status_code, 204)
        with patch('orders.direct_payment._notify_paid'):
            process_payment_inbox(DirectPaymentInbox.objects.get().pk)
        payload = {'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
                   'transaction_id': 'tx-callback-1', 'out_refund_no': 'unknown-refund',
                   'refund_id': 'wx-refund-1', 'refund_status': 'SUCCESS',
                   'success_time': '2026-10-27T10:00:00+08:00',
                   'amount': {'total': 10000, 'refund': 10000, 'payer_total': 10000, 'payer_refund': 10000}}
        refund_body = self.envelope(payload, event='REFUND.SUCCESS', original='refund', event_id='event-1')
        self.assertEqual(isolated_callback(self.request(refund_body)).status_code, 503)
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'tx-callback-1'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')
        self.assertFalse(DirectRefundInbox.objects.exists())

    def test_accepted_refund_then_payment_same_id_requires_review(self):
        payload = {'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
                   'transaction_id': 'tx-unknown', 'out_refund_no': 'unknown-refund',
                   'refund_id': 'wx-refund-1', 'refund_status': 'SUCCESS',
                   'success_time': '2026-10-27T10:00:00+08:00',
                   'amount': {'total': 10000, 'refund': 10000, 'payer_total': 10000, 'payer_refund': 10000}}
        refund_body = self.envelope(payload, event='REFUND.SUCCESS', original='refund', event_id='event-1')
        self.assertEqual(isolated_callback(self.request(refund_body)).status_code, 204)
        payment_body = self.envelope()
        self.assertEqual(isolated_callback(self.request(payment_body)).status_code, 503)
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('AWAITING', None))
        self.assertIsNotNone(self.intent.evidence_risk_at)
        self.assertFalse(DirectPaymentInbox.objects.exists())

    def test_same_type_equivalent_reencrypted_notifications_remain_idempotent(self):
        first = self.envelope(encryption_nonce='123456789012')
        second = self.envelope(encryption_nonce='098765432109')
        self.assertNotEqual(first, second)
        self.assertEqual(isolated_callback(self.request(first)).status_code, 204)
        self.assertEqual(isolated_callback(self.request(second)).status_code, 204)
        marker = DirectCallbackEnvelope.objects.get()
        self.assertFalse(marker.has_conflict)
        self.assertEqual(marker.bodies.filter(accepted=True, event_kind='PAYMENT').count(), 2)
        self.assertEqual(DirectPaymentInbox.objects.count(), 1)
        self.assertFalse(DirectPaymentInbox.objects.get().has_conflict)

    def test_conflicts_do_not_cross_merchant_or_unrelated_ids(self):
        damaged = b'{"id":"event-1","event_type":"TRANSACTION.UNKNOWN","resource_type":"encrypt-resource","resource":{}}'
        wrong_merchant = WeChatPayV3(
            app_id='wx-test', merchant_id='other-mch', merchant_serial='merchant-serial',
            merchant_private_key=self.merchant.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            api_v3_key=self.api_key, platform_keys={'PUB_KEY_ID_test': self.platform.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)})
        with override_settings(WECHAT_DIRECT_CALLBACK_TEST_CONFIG=TestCallbackConfig(wrong_merchant, 'test-quarantine-v1', self.quarantine_key)):
            self.assertEqual(isolated_callback(self.request(damaged)).status_code, 503)
        other_id = damaged.replace(b'event-1', b'other-event')
        self.assertEqual(isolated_callback(self.request(other_id)).status_code, 503)
        good = self.envelope()
        self.assertEqual(isolated_callback(self.request(good)).status_code, 204)
        with patch('orders.direct_payment._notify_paid'):
            process_payment_inbox(DirectPaymentInbox.objects.get().pk)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'PAID')
        self.assertFalse(DirectCallbackEnvelope.objects.get(merchant_id='mch-test', notification_id='event-1').has_conflict)

    def test_ambiguous_or_forged_id_cannot_freeze_an_order(self):
        ambiguous = b'{"id":"event-1","id":"event-2","event_type":"UNKNOWN"}'
        self.assertEqual(isolated_callback(self.request(ambiguous)).status_code, 503)
        forged = b'{"id":"event-1","event_type":"UNKNOWN"}'
        bad_headers = self.headers(forged)
        bad_headers['wechatpay-signature'] = 'AAAA'
        self.assertEqual(isolated_callback(self.request(forged, bad_headers)).status_code, 400)
        good = self.envelope()
        self.assertEqual(isolated_callback(self.request(good)).status_code, 204)
        with patch('orders.direct_payment._notify_paid'):
            process_payment_inbox(DirectPaymentInbox.objects.get().pk)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'PAID')
        self.assertEqual(DirectCallbackQuarantine.objects.count(), 1)

    def test_bad_event_with_wrong_payer_does_not_freeze_another_order(self):
        damaged = b'{"id":"event-1","event_type":"UNKNOWN"}'
        self.assertEqual(isolated_callback(self.request(damaged)).status_code, 503)
        self.payload['payer']['openid'] = 'other-openid'
        good = self.envelope()
        self.assertEqual(isolated_callback(self.request(good)).status_code, 503)
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.evidence_risk_at), ('AWAITING', None))
        self.assertFalse(DirectPaymentInbox.objects.exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_quarantine_and_valid_notice_compete_without_losing_review(self):
        damaged = b'{"id":"event-1","event_type":"UNKNOWN"}'
        good = self.envelope()
        holding, attempted, release = Event(), Event(), Event()
        from integrations.wechatpay_test_callback import _envelope as original_envelope, _quarantine as original_quarantine

        def envelope(*args, **kwargs):
            if current_thread().name.startswith('valid'):
                attempted.set()
            return original_envelope(*args, **kwargs)

        def quarantine(*args, **kwargs):
            if current_thread().name.startswith('bad'):
                holding.set()
                if not release.wait(10):
                    raise RuntimeError('test barrier timed out')
            return original_quarantine(*args, **kwargs)

        def run(body, name):
            current_thread().name = name
            close_old_connections()
            try:
                return isolated_callback(self.request(body)).status_code
            finally:
                close_old_connections()

        with patch('integrations.wechatpay_test_callback._envelope', side_effect=envelope), \
             patch('integrations.wechatpay_test_callback._quarantine', side_effect=quarantine), \
             ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run, damaged, 'bad-receipt')
            self.assertTrue(holding.wait(10))
            second = executor.submit(run, good, 'valid-receipt')
            self.assertTrue(attempted.wait(10))
            release.set()
            self.assertEqual((first.result(timeout=20), second.result(timeout=20)), (503, 503))
        self.assertFalse(DirectPaymentInbox.objects.exists())
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('AWAITING', None))
        self.assertIsNotNone(self.intent.evidence_risk_at)
        self.assertFalse(DirectPaymentOutbox.objects.exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_valid_notice_then_concurrent_quarantine_prevents_later_dispatch(self):
        damaged = b'{"id":"event-1","event_type":"UNKNOWN"}'
        good = self.envelope()
        holding, attempted, release = Event(), Event(), Event()
        from integrations.wechatpay_test_callback import _envelope as original_envelope
        from orders.direct_payment_inbox import ingest_verified_payment_notice as original_ingest

        def envelope(*args, **kwargs):
            if current_thread().name.startswith('bad'):
                attempted.set()
            return original_envelope(*args, **kwargs)

        def ingest(*args, **kwargs):
            result = original_ingest(*args, **kwargs)
            holding.set()
            if not release.wait(10):
                raise RuntimeError('test barrier timed out')
            return result

        def run(body, name):
            current_thread().name = name
            close_old_connections()
            try:
                return isolated_callback(self.request(body)).status_code
            finally:
                close_old_connections()

        with patch('integrations.wechatpay_test_callback._envelope', side_effect=envelope), \
             patch('integrations.wechatpay_test_callback.ingest_verified_payment_notice', side_effect=ingest), \
             ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run, good, 'valid-receipt')
            self.assertTrue(holding.wait(10))
            second = executor.submit(run, damaged, 'bad-receipt')
            self.assertTrue(attempted.wait(10))
            release.set()
            self.assertEqual((first.result(timeout=20), second.result(timeout=20)), (204, 503))
        row = DirectPaymentInbox.objects.get()
        self.assertEqual(row.status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('AWAITING', None))
        self.assertIsNotNone(self.intent.evidence_risk_at)
        self.assertFalse(DirectPaymentOutbox.objects.exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_unidentifiable_signed_duplicate_receipts_are_idempotent_concurrently(self):
        body = b'{"id":"event-1","id":"event-2","event_type":"UNKNOWN"}'
        gate = Barrier(2, timeout=10)
        from integrations.wechatpay_test_callback import _quarantine as original_quarantine

        def quarantine(*args, **kwargs):
            gate.wait()
            return original_quarantine(*args, **kwargs)

        def run():
            close_old_connections()
            try:
                return isolated_callback(self.request(body)).status_code
            finally:
                close_old_connections()

        with patch('integrations.wechatpay_test_callback._quarantine', side_effect=quarantine), \
             ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.submit(run), executor.submit(run)
            self.assertEqual((first.result(timeout=20), second.result(timeout=20)), (503, 503))
        self.assertEqual(DirectCallbackQuarantine.objects.count(), 1)
        self.assertEqual(DirectCallbackQuarantineObservation.objects.count(), 1)
        self.assertFalse(DirectCallbackEnvelope.objects.exists())
        self.assertFalse(DirectPaymentInbox.objects.exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_cross_type_callbacks_lock_both_orders_in_one_sorted_pass(self):
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        account.openid, account.wechat_appid, account.is_openid_bound = 'other-openid', 'wx-test', True
        account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=account, legacy_user=user,
            data={'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'callback-2'})
        other = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        for index, intent in enumerate((self.intent, other)):
            evidence = VerifiedPayment(intent.merchant_order_no, f'tx-cross-{index}', intent.appid, intent.mchid,
                                       intent.openid, intent.amount_fen, 'CNY', timezone.now(),
                                       payer_total_fen=intent.amount_fen, payer_currency='CNY')
            with patch('orders.direct_payment._notify_paid'):
                confirm_verified_payment(intent.order_id, evidence)
            intent.refresh_from_db()
        first_refund = reserve_full_refund(self.intent.order_id, request_id='cross-refund-1', reason='test')
        second_refund = reserve_full_refund(other.order_id, request_id='cross-refund-2', reason='test')

        def seed_payment(event_id, intent, body_char):
            payload = deepcopy(self.payload)
            payload.update(out_trade_no=intent.merchant_order_no, transaction_id=intent.transaction_id)
            payload['payer']['openid'] = intent.openid
            ingest_verified_payment_notice('mch-test', VerifiedNotification(event_id, 'TRANSACTION.SUCCESS',
                'encrypt-resource', 'transaction', payload, body_char * 64))

        def seed_refund(event_id, intent, refund, body_char):
            payload = {'mchid': intent.mchid, 'out_trade_no': intent.merchant_order_no,
                       'transaction_id': intent.transaction_id, 'out_refund_no': refund.merchant_refund_no,
                       'refund_id': f'wx-{refund.pk}', 'refund_status': 'ABNORMAL',
                       'amount': {'total': intent.amount_fen, 'refund': intent.amount_fen,
                                  'payer_total': intent.amount_fen, 'payer_refund': intent.amount_fen}}
            ingest_verified_refund_notice('mch-test', VerifiedNotification(event_id, 'REFUND.ABNORMAL',
                'encrypt-resource', 'refund', payload, body_char * 64))

        seed_payment('cross-event-1', self.intent, 'a')
        seed_refund('cross-event-1', other, second_refund, 'b')
        seed_payment('cross-event-2', other, 'c')
        seed_refund('cross-event-2', self.intent, first_refund, 'd')
        gate = Barrier(2, timeout=10)
        from integrations.wechatpay_test_callback import _hold_known_inboxes as original_hold

        def hold(*args, **kwargs):
            gate.wait()
            return original_hold(*args, **kwargs)

        def run(event_id):
            body = json.dumps({'id': event_id, 'event_type': 'UNKNOWN'}).encode()
            close_old_connections()
            try:
                return isolated_callback(self.request(body)).status_code
            finally:
                close_old_connections()

        with patch('integrations.wechatpay_test_callback._hold_known_inboxes', side_effect=hold), \
             ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.submit(run, 'cross-event-1'), executor.submit(run, 'cross-event-2')
            self.assertEqual((first.result(timeout=20), second.result(timeout=20)), (503, 503))
        self.intent.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((self.intent.status, other.status), ('REVIEW', 'REVIEW'))
        self.assertEqual(set(DirectPaymentInbox.objects.values_list('status', flat=True)), {'REVIEW'})
        self.assertEqual(set(DirectRefundInbox.objects.values_list('status', flat=True)), {'REVIEW'})

    def test_storage_failure_or_uncommitted_transaction_never_ack(self):
        body = self.envelope()
        with patch('integrations.wechatpay_test_callback.ingest_verified_payment_notice', side_effect=DatabaseError('private')):
            self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        self.assertFalse(DirectPaymentInbox.objects.exists())
        with transaction.atomic():
            self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        malformed = b'{"secret":"private"}'
        with patch('integrations.wechatpay_test_callback._quarantine', side_effect=DatabaseError('private')):
            self.assertEqual(isolated_callback(self.request(malformed)).status_code, 503)
        self.assertFalse(DirectCallbackQuarantine.objects.exists())
