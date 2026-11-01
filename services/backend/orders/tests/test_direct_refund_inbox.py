import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from integrations.wechatpay_v3 import WeChatPayProtocolError, WeChatPayV3
from orders.direct_payment import VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.direct_refund import VerifiedRefund, apply_verified_refund, reserve_full_refund
from orders.direct_refund_delivery_models import DirectRefundInbox
from orders.direct_refund_inbox import RefundInboxError, ingest_verified_refund_notice
from orders.models import Order
from orders.tests.factories import make_service, make_user, make_wallet


class RefundInboxFixture:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.api_key = b'x' * 32
        private = cls.merchant_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public = cls.platform_key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.protocol = WeChatPayV3(app_id='wx-test', merchant_id='mch-test', merchant_serial='merchant-serial',
                                   merchant_private_key=private, api_v3_key=cls.api_key,
                                   platform_keys={'PUB_KEY_ID_test': public})

    def setUp(self):
        super().setUp()
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        account.openid, account.wechat_appid, account.is_openid_bound = 'openid-test', 'wx-test', True
        account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        self.wallet = make_wallet(user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=account, legacy_user=user, data={
            'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'refund-inbox-payment',
            'dispatch_mode': 'AUTO_GRAB',
        })
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        evidence = VerifiedPayment(self.intent.merchant_order_no, 'wx-transaction', 'wx-test', 'mch-test',
            'openid-test', 10000, 'CNY', timezone.now(), payer_total_fen=10000, payer_currency='CNY')
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, evidence)
        self.intent.refresh_from_db()
        self.refund = reserve_full_refund(self.intent.order_id, request_id='refund-inbox-request', reason='客户取消')

    def payload(self, status='SUCCESS'):
        data = {'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
                'transaction_id': 'wx-transaction', 'out_refund_no': self.refund.merchant_refund_no,
                'refund_id': 'wx-refund', 'refund_status': status,
                'amount': {'total': 10000, 'refund': 10000, 'payer_total': 10000, 'payer_refund': 10000},
                'user_received_account': 'never-store-bank-info', 'attach': 'never-store-secret'}
        if status == 'SUCCESS':
            data['success_time'] = '2026-10-27T18:30:45+08:00'
        return data

    def notice(self, payload=None, *, event_id='notice-1', status='SUCCESS', nonce=b'123456789012'):
        data = payload if payload is not None else self.payload(status)
        ciphertext = AESGCM(self.api_key).encrypt(nonce, json.dumps(data).encode(), b'refund')
        body = json.dumps({'id': event_id, 'event_type': 'REFUND.' + status, 'resource_type': 'encrypt-resource',
            'resource': {'original_type': 'refund', 'algorithm': 'AEAD_AES_256_GCM', 'nonce': nonce.decode(),
                         'associated_data': 'refund', 'ciphertext': base64.b64encode(ciphertext).decode()}}).encode()
        timestamp = str(int(time.time()))
        sig = self.platform_key.sign((timestamp + '\nnonce\n').encode() + body + b'\n', padding.PKCS1v15(), hashes.SHA256())
        headers = {'Wechatpay-Serial': 'PUB_KEY_ID_test', 'Wechatpay-Timestamp': timestamp,
                   'Wechatpay-Nonce': 'nonce', 'Wechatpay-Signature': base64.b64encode(sig).decode()}
        return self.protocol.decrypt_notification(headers, body)


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectRefundInboxTest(RefundInboxFixture, TestCase):
    def ingest(self, payload=None, **kwargs):
        return ingest_verified_refund_notice('mch-test', self.notice(payload, **kwargs))

    def test_notification_is_retained_without_claiming_success_or_exposing_private_fields(self):
        row = self.ingest()
        self.assertEqual((row.status, row.refund_id), ('PENDING', self.refund.pk))
        self.assertEqual(row.controlled_payload['amount']['payer_refund'], 10000)
        self.assertNotIn('never-store', str(row.controlled_payload))
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'RESERVED')
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status, order.refunded_at), ('CANCELLED', 'PAID', None))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 50000)
        self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())

    def test_identical_event_reencrypted_is_idempotent(self):
        first = self.ingest()
        second = self.ingest(nonce=b'123456789013')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual((second.status, second.has_conflict, second.observations.count()), ('PENDING', False, 2))
        self.assertEqual(DirectRefundInbox.objects.count(), 1)

    def test_same_id_changed_amount_preserves_conflict_and_blocks_refund(self):
        first = self.ingest()
        changed = self.payload()
        changed['amount']['payer_refund'] = 9000
        result = self.ingest(changed, nonce=b'123456789013')
        self.assertEqual((result.pk, result.status, result.has_conflict, result.conflicts.count()),
                         (first.pk, 'REVIEW', True, 1))
        self.refund.refresh_from_db()
        self.intent.refresh_from_db()
        self.assertEqual((self.refund.status, self.intent.status), ('REVIEW', 'REVIEW'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')

    def test_different_events_allow_abnormal_to_success_in_either_arrival_order(self):
        self.assertEqual(self.ingest(status='SUCCESS').status, 'PENDING')
        self.assertEqual(self.ingest(event_id='notice-2', status='ABNORMAL').status, 'PENDING')
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'RESERVED')

    def test_different_events_conflicting_terminal_states_are_both_retained(self):
        first = self.ingest(status='SUCCESS')
        second = self.ingest(event_id='notice-2', status='CLOSED')
        self.assertEqual((first.status, second.status, second.has_conflict), ('PENDING', 'REVIEW', True))
        self.assertEqual(DirectRefundInbox.objects.filter(refund=self.refund).count(), 2)
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'REVIEW')

    def test_terminal_success_is_not_rewritten_by_conflicting_notice(self):
        paid_at = datetime.fromisoformat('2026-10-27T18:30:45+08:00')
        evidence = VerifiedRefund('mch-test', self.intent.merchant_order_no, 'wx-transaction',
            self.refund.merchant_refund_no, 'wx-refund', 10000, 10000, 'CNY', 10000, 10000,
            10000, 10000, 0, 'SUCCESS', paid_at)
        apply_verified_refund(self.intent.order_id, self.refund.pk, evidence)
        row = self.ingest(status='CLOSED')
        self.assertEqual(row.status, 'REVIEW')
        self.refund.refresh_from_db()
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((self.refund.status, self.refund.success_at, order.payment_status, order.refunded_at),
                         ('SUCCESS', paid_at, 'REFUNDED', paid_at))

    def test_unrelated_identity_cannot_freeze_a_refund(self):
        for index, (key, value) in enumerate((('mchid', 'other'), ('out_trade_no', 'other'),
                                               ('transaction_id', 'other'), ('out_refund_no', 'other'))):
            changed = self.payload()
            changed[key] = value
            row = self.ingest(changed, event_id=f'unrelated-{index}')
            self.assertEqual((row.status, row.refund_id), ('REVIEW', None))
            self.refund.refresh_from_db()
            self.assertEqual(self.refund.status, 'RESERVED')

    def test_same_id_identity_conflict_stops_only_the_original_bound_refund(self):
        row = self.ingest()
        changed = self.payload()
        changed['out_trade_no'] = 'unrelated-order'
        duplicate = self.ingest(changed, nonce=b'123456789013')
        self.assertEqual((duplicate.pk, duplicate.refund_id, duplicate.status, duplicate.has_conflict),
                         (row.pk, self.refund.pk, 'REVIEW', True))
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'REVIEW')

    def test_same_refund_with_changed_id_or_amount_is_a_conflict(self):
        self.ingest()
        changed = self.payload()
        changed['refund_id'] = 'different-channel-refund'
        row = self.ingest(changed, event_id='notice-2')
        self.assertEqual((row.status, row.refund_id, row.has_conflict), ('REVIEW', self.refund.pk, True))
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'REVIEW')

    def test_malformed_authenticated_payload_is_redacted_and_blocks_matching_refund(self):
        changed = self.payload()
        changed['amount']['payer_refund'] = {'secret': 'nested-private'}
        row = self.ingest(changed)
        self.assertEqual((row.status, row.refund_id), ('REVIEW', self.refund.pk))
        self.assertNotIn('nested-private', str(row.controlled_payload))
        self.assertEqual(row.controlled_payload['amount']['payer_refund'], {'invalid_type': True})
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'REVIEW')

    def test_submicrosecond_success_notice_is_retained_for_review_not_success(self):
        changed = self.payload()
        changed['success_time'] = '2026-10-27T18:30:45.1234561+08:00'
        row = self.ingest(changed)
        self.assertEqual((row.status, row.refund_id), ('REVIEW', self.refund.pk))
        self.assertEqual(row.controlled_payload['success_time'], changed['success_time'])
        self.refund.refresh_from_db()
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((self.refund.status, order.payment_status, order.refunded_at), ('REVIEW', 'PAID', None))

    def test_unsigned_or_disabled_domain_cannot_create_inbox(self):
        with self.assertRaises(RefundInboxError):
            ingest_verified_refund_notice('mch-test', {})
        with self.assertRaises(WeChatPayProtocolError):
            self.protocol.decrypt_notification({}, b'{}')
        with override_settings(WECHAT_DIRECT_DOMAIN_TESTING=False), self.assertRaises(ValueError):
            self.ingest()
        self.assertFalse(DirectRefundInbox.objects.exists())

    def test_unsupported_mysql_isolation_rejected_before_insert(self):
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, statement): self.statement = statement
            def fetchone(self): return ('REPEATABLE-READ',)
        fake_connection = SimpleNamespace(vendor='mysql', cursor=Cursor)
        with patch('orders.direct_refund_inbox.connection', fake_connection), self.assertRaises(RefundInboxError):
            self.ingest()
        self.assertFalse(DirectRefundInbox.objects.exists())


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
@skipUnlessDBFeature('has_select_for_update')
class DirectRefundInboxConcurrencyTest(RefundInboxFixture, TransactionTestCase):
    def test_concurrent_same_event_has_one_receipt(self):
        notice = self.notice()
        barrier = Barrier(2)
        def ingest():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return ingest_verified_refund_notice('mch-test', notice).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ingest), pool.submit(ingest)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(DirectRefundInbox.objects.count(), 1)
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'RESERVED')

    def test_concurrent_contradictory_events_stop_refund(self):
        notices = (self.notice(event_id='notice-1', status='SUCCESS'),
                   self.notice(event_id='notice-2', status='CLOSED', nonce=b'123456789013'))
        barrier = Barrier(2)
        from orders.direct_refund_inbox import _locked_match
        def after_both_inboxes_inserted(*args):
            # Both transactions have inserted their distinct inbox rows before
            # either contends for the order/intent/refund row locks.
            barrier.wait(timeout=10)
            return _locked_match(*args)
        def ingest(notice):
            close_old_connections()
            try:
                return ingest_verified_refund_notice('mch-test', notice).status
            finally:
                close_old_connections()
        with patch('orders.direct_refund_inbox._locked_match', side_effect=after_both_inboxes_inserted), \
             ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(ingest, notices))
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'REVIEW')
        self.assertEqual(DirectRefundInbox.objects.filter(refund=self.refund).count(), 2)
        self.assertIn('REVIEW', results)
