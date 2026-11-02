from copy import deepcopy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from integrations.wechatpay_v3 import VerifiedNotification
from orders.direct_payment import DirectPaymentError, prepare_direct_order
from orders.direct_payment_delivery_models import DirectPaymentInbox, DirectPaymentOutbox
from orders.direct_payment_inbox import ingest_verified_payment_notice, process_payment_inbox, scan_payment_inbox
from orders.direct_payment_outbox import process_payment_outbox, scan_payment_outbox
from orders.dispatching import deliver_dispatch, eligibility_error
from orders.models import Order, WecomDispatchRecord
from orders.state_machine import IllegalTransitionError, transition
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True, WECOM_DISPATCH_NEW_ORDERS=False)
class DirectPaymentDeliveryTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.openid, self.account.wechat_appid, self.account.is_openid_bound = 'test-openid', 'wx-test', True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(self.user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=self.account, legacy_user=self.user,
            data={'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'delivery-1'})
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        self.payload = {'appid': 'wx-test', 'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
            'transaction_id': 'tx-delivery-1', 'trade_type': 'JSAPI', 'trade_state': 'SUCCESS',
            'success_time': self.intent.created_at.isoformat(), 'payer': {'openid': 'test-openid'},
            'amount': {'total': 10000, 'payer_total': 10000, 'currency': 'CNY', 'payer_currency': 'CNY'},
            'attach': 'discard-this-private-value'}

    def notice(self, payload=None, body='a'):
        return VerifiedNotification('event-1', 'TRANSACTION.SUCCESS', 'encrypt-resource', 'transaction',
                                    payload if payload is not None else self.payload, body * 64)

    def ingested(self):
        return ingest_verified_payment_notice('mch-test', self.notice())

    def process(self, row):
        with patch('orders.direct_payment._notify_paid'):
            return process_payment_inbox(row.pk)

    def test_authenticated_dedup_retains_hash_observations_and_redacts(self):
        row = self.ingested()
        same = ingest_verified_payment_notice('mch-test', self.notice(body='b'))
        self.assertEqual(row.pk, same.pk)
        self.assertEqual(row.observations.count(), 2)
        self.assertFalse(same.has_conflict)
        self.assertNotIn('attach', row.controlled_payload)
        with self.assertRaises(DirectPaymentError):
            ingest_verified_payment_notice('mch-test', {})
        self.assertEqual(DirectPaymentInbox.objects.count(), 1)

    def test_matched_bad_amount_before_good_notice_holds_legacy_intent_without_fabricating_payment(self):
        changed = deepcopy(self.payload)
        changed['amount']['total'] = 9999
        bad = ingest_verified_payment_notice('mch-test', self.notice(changed))
        self.assertEqual(self.process(bad).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('AWAITING', None))
        self.assertIsNotNone(self.intent.evidence_risk_at)
        good = ingest_verified_payment_notice('mch-test', replace(self.notice(), notification_id='event-2'))
        self.assertEqual(self.process(good).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'tx-delivery-1'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_parser_rejection_for_matched_identity_sets_risk_but_wrong_payer_does_not(self):
        for index, change in enumerate(('currency', 'trade_type', 'transaction')):
            payload = deepcopy(self.payload)
            if change == 'currency':
                payload['amount']['currency'] = 'USD'
            elif change == 'trade_type':
                payload['trade_type'] = 'NATIVE'
            else:
                payload['transaction_id'] = ' malformed '
            wrong_payer = deepcopy(payload)
            wrong_payer['payer']['openid'] = 'unrelated'
            bad = ingest_verified_payment_notice('mch-test', replace(self.notice(wrong_payer), notification_id=f'wrong-{index}'))
            self.assertEqual(self.process(bad).status, 'REVIEW')
            self.intent.refresh_from_db()
            self.assertIsNone(self.intent.evidence_risk_at)
            bad = ingest_verified_payment_notice('mch-test', replace(self.notice(payload), notification_id=f'bad-{index}'))
            self.assertEqual(self.process(bad).status, 'REVIEW')
            self.intent.refresh_from_db()
            self.assertIsNotNone(self.intent.evidence_risk_at)
            self.assertIsNone(self.intent.transaction_id)
            self.intent.evidence_risk_at = None
            self.intent.save(update_fields=['evidence_risk_at'])

    def test_bad_notice_after_paid_blocks_outbox_without_rolling_back_payment(self):
        self.assertEqual(self.process(self.ingested()).status, 'DONE')
        changed = deepcopy(self.payload)
        changed['amount']['currency'] = 'USD'
        bad = ingest_verified_payment_notice('mch-test', replace(self.notice(changed), notification_id='event-2'))
        self.assertEqual(self.process(bad).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'tx-delivery-1'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_conflict_before_processing_retains_evidence_and_never_dispatches(self):
        row = self.ingested()
        changed = deepcopy(self.payload)
        changed['amount']['total'] += 1
        ingest_verified_payment_notice('mch-test', self.notice(changed, 'b'))
        row = self.process(row)
        self.assertEqual(row.status, 'REVIEW')
        self.assertTrue(row.has_conflict)
        self.assertEqual(row.conflicts.count(), 1)
        self.assertFalse(DirectPaymentOutbox.objects.exists())
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')

    def test_same_id_conflict_before_processing_blocks_later_valid_new_id(self):
        first = self.ingested()
        changed = deepcopy(self.payload)
        changed['amount']['total'] = 9999
        ingest_verified_payment_notice('mch-test', self.notice(changed, 'b'))
        self.assertEqual(self.process(first).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('AWAITING', None))
        self.assertIsNotNone(self.intent.evidence_risk_at)
        good = ingest_verified_payment_notice('mch-test', replace(self.notice(), notification_id='new-event'))
        self.assertEqual(self.process(good).status, 'REVIEW')
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_same_id_conflict_does_not_freeze_an_order_with_unmatched_payer(self):
        other_user = make_user()
        other_account = get_or_create_account_for_legacy_user(other_user)
        other_account.openid, other_account.wechat_appid, other_account.is_openid_bound = 'other-openid', 'wx-test', True
        other_account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(other_user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=other_account, legacy_user=other_user,
            data={'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'other-order'})
        other = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        self.ingested()
        changed = deepcopy(self.payload)
        changed['out_trade_no'] = other.merchant_order_no
        changed['payer']['openid'] = 'not-the-other-openid'
        ingest_verified_payment_notice('mch-test', self.notice(changed, 'b'))
        self.intent.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNotNone(self.intent.evidence_risk_at)
        self.assertIsNone(other.evidence_risk_at)

    def test_confirmation_and_outbox_are_atomic_and_replay_does_not_reset_clock(self):
        row = self.ingested()
        with patch.object(DirectPaymentInbox, 'save', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.process(row)
        self.assertFalse(DirectPaymentOutbox.objects.exists())
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')
        self.assertEqual(self.process(row).status, 'DONE')
        order = Order.objects.get(pk=self.intent.order_id)
        clock = (order.pool_entered_at, order.auto_cancel_at)
        self.assertEqual(self.process(row).status, 'DONE')
        order.refresh_from_db()
        self.assertEqual(clock, (order.pool_entered_at, order.auto_cancel_at))
        self.assertEqual(DirectPaymentOutbox.objects.count(), 1)

    def test_conflict_after_published_stops_new_dispatch_preserving_money_and_service(self):
        row = self.ingested()
        self.process(row)
        event = DirectPaymentOutbox.objects.get()
        event.status = 'DONE'
        event.save(update_fields=['status'])
        changed = deepcopy(self.payload)
        changed['attach'] = 'different-sensitive-value'
        ingest_verified_payment_notice('mch-test', self.notice(changed, 'b'))
        self.intent.refresh_from_db()
        event.refresh_from_db()
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((self.intent.status, order.payment_status, order.status, event.status),
                         ('REVIEW', 'PAID', 'PENDING', 'REVIEW'))
        self.assertTrue(eligibility_error(order))
        with self.assertRaises(IllegalTransitionError):
            transition(order.pk, Order.Status.GRABBED)
        # A provider already accepted before the conflict must not be cancelled.
        Order.objects.filter(pk=order.pk).update(status='IN_SERVICE')
        ingest_verified_payment_notice('mch-test', self.notice(changed, 'c'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'IN_SERVICE')

    def test_conflict_removes_order_from_provider_pool(self):
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        row = self.ingested()
        self.process(row)
        client = APIClient()
        client.force_authenticate(provider)
        with patch.object(EscortProfile, 'order_visibility_delay_seconds', new_callable=lambda: property(lambda _: 0)):
            self.assertIn(self.intent.order_id, [item['id'] for item in client.get('/api/orders/orders/?status=pending').data['data']])
            changed = deepcopy(self.payload)
            changed['attach'] = 'changed'
            ingest_verified_payment_notice('mch-test', self.notice(changed, 'b'))
            self.assertNotIn(self.intent.order_id, [item['id'] for item in client.get('/api/orders/orders/?status=pending').data['data']])

    def test_different_event_id_conflicting_cash_stops_new_dispatch(self):
        self.process(self.ingested())
        changed = deepcopy(self.payload)
        changed['amount']['payer_total'] = 9000
        second = ingest_verified_payment_notice('mch-test', replace(self.notice(changed, 'b'), notification_id='event-2'))
        self.assertEqual(self.process(second).last_error, 'PAYMENT_EVIDENCE_CONFLICT')
        self.intent.refresh_from_db()
        order = Order.objects.get(pk=self.intent.order_id)
        event = DirectPaymentOutbox.objects.get(intent=self.intent)
        self.assertEqual((self.intent.status, order.payment_status, order.status, event.status),
                         ('REVIEW', 'PAID', 'PENDING', 'REVIEW'))
        self.assertTrue(eligibility_error(order))
        with self.assertRaises(IllegalTransitionError):
            transition(order.pk, Order.Status.GRABBED)
        self.assertEqual(DirectPaymentInbox.objects.filter(intent=self.intent).count(), 2)

    def test_different_event_id_transaction_conflict_preserves_service(self):
        self.process(self.ingested())
        order = Order.objects.get(pk=self.intent.order_id)
        Order.objects.filter(pk=order.pk).update(status='IN_SERVICE')
        changed = deepcopy(self.payload)
        changed['transaction_id'] = 'different-channel-transaction'
        second = ingest_verified_payment_notice('mch-test', replace(self.notice(changed, 'b'), notification_id='event-2'))
        self.assertEqual(self.process(second).status, 'REVIEW')
        self.intent.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((self.intent.status, order.status, order.payment_status), ('REVIEW', 'IN_SERVICE', 'PAID'))
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_different_event_id_total_conflict_stops_new_dispatch(self):
        self.process(self.ingested())
        changed = deepcopy(self.payload)
        changed['amount']['total'] += 1
        second = ingest_verified_payment_notice('mch-test', replace(self.notice(changed, 'b'), notification_id='event-2'))
        self.assertEqual(self.process(second).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'REVIEW')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_unrelated_notice_cannot_freeze_or_bind_the_order(self):
        self.process(self.ingested())
        for index, (field, value) in enumerate((('mchid', 'other'), ('appid', 'other'), ('openid', 'other'))):
            changed = deepcopy(self.payload)
            if field == 'openid':
                changed['payer']['openid'] = value
            else:
                changed[field] = value
            second = ingest_verified_payment_notice('mch-test', replace(self.notice(changed, 'b'), notification_id=f'unrelated-{index}'))
            second = self.process(second)
            self.assertEqual(second.status, 'REVIEW')
            self.assertIsNone(second.intent_id)
            self.intent.refresh_from_db()
            self.assertEqual(self.intent.status, 'PAID')
            self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'PENDING')

    def test_different_event_id_same_evidence_is_idempotent(self):
        self.process(self.ingested())
        second = ingest_verified_payment_notice('mch-test', replace(self.notice(body='b'), notification_id='event-2'))
        self.assertEqual(self.process(second).status, 'DONE')
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.filter(intent=self.intent).count(), 1)

    def test_missing_cash_evidence_is_saved_but_not_dispatched(self):
        del self.payload['amount']['payer_total']
        row = self.ingested()
        self.assertEqual(self.process(row).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'REVIEW')
        self.assertFalse(DirectPaymentOutbox.objects.exists())

    def test_malformed_payload_cannot_persist_nested_secret_or_unbounded_integer(self):
        self.payload['payer']['openid'] = {'secret': 'must-not-persist'}
        self.payload['amount']['payer_total'] = 2**100
        row = self.ingested()
        self.assertNotIn('must-not-persist', str(row.controlled_payload))
        self.assertEqual(row.controlled_payload['amount']['payer_total'], {'invalid_type': True})
        self.assertEqual(self.process(row).status, 'REVIEW')

    def test_inbox_scanner_retries_rolled_back_failure(self):
        row = self.ingested()
        with patch('orders.direct_payment_inbox.process_payment_inbox', side_effect=RuntimeError('secret')):
            self.assertEqual(scan_payment_inbox(), [(row.pk, 'retry')])
        row.refresh_from_db()
        self.assertEqual(row.status, 'PENDING')
        with patch('orders.direct_payment._notify_paid'):
            self.assertEqual(scan_payment_inbox(), [(row.pk, 'DONE')])
        self.assertEqual(scan_payment_inbox(), [])

    def test_outbox_retries_expired_lease_without_changing_order_time(self):
        row = self.ingested()
        self.process(row)
        event = DirectPaymentOutbox.objects.get()
        order = Order.objects.get(pk=self.intent.order_id)
        original = (order.pool_entered_at, order.auto_cancel_at)
        event.status, event.lease_until = 'CLAIMED', timezone.now() + timedelta(minutes=1)
        event.save(update_fields=['status', 'lease_until'])
        self.assertEqual(process_payment_outbox(event.pk), 'busy')
        event.lease_until = None
        event.save(update_fields=['lease_until'])
        with patch('orders.notifier.notify_order_update'), patch('orders.services.schedule_auto_cancel'), \
             patch('integrations.kook_config.get_kook_config', return_value=SimpleNamespace(auto_new_orders=False)):
            self.assertEqual(scan_payment_outbox(), [(event.pk, 'done')])
            self.assertEqual(process_payment_outbox(event.pk), 'done')
        order.refresh_from_db()
        self.assertEqual(original, (order.pool_entered_at, order.auto_cancel_at))

    @override_settings(WECOM_DISPATCH_ENABLED=True, WECOM_DISPATCH_NEW_ORDERS=True,
                       WECOM_DISPATCH_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=mock-test')
    def test_retry_keeps_one_group_record_and_delivery_cas(self):
        row = self.ingested()
        self.process(row)
        event = DirectPaymentOutbox.objects.get()
        with patch('integrations.kook_config.get_kook_config', return_value=SimpleNamespace(auto_new_orders=False)), \
             patch('orders.dispatching.send_task_safely'), \
             patch('orders.services.schedule_auto_cancel'), \
             patch('orders.notifier.notify_order_update', side_effect=[RuntimeError('test'), None]):
            self.assertEqual(process_payment_outbox(event.pk), 'retry')
            self.assertEqual(process_payment_outbox(event.pk), 'done')
        self.assertEqual(WecomDispatchRecord.objects.count(), 1)
        record = WecomDispatchRecord.objects.get()
        with patch('integrations.wecom.WecomClient.send_text') as send:
            self.assertTrue(deliver_dispatch('WECOM', record.pk).startswith('sent:'))
            self.assertTrue(deliver_dispatch('WECOM', record.pk).startswith('already_sent:'))
            send.assert_called_once()


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True, WECOM_DISPATCH_NEW_ORDERS=False)
@skipUnlessDBFeature('has_select_for_update')
class DirectPaymentDeliveryConcurrencyTest(TransactionTestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.openid, self.account.wechat_appid, self.account.is_openid_bound = 'test-openid', 'wx-test', True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(self.user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=self.account, legacy_user=self.user,
            data={'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'delivery-race'})
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        self.notice = VerifiedNotification('event-race', 'TRANSACTION.SUCCESS', 'encrypt-resource', 'transaction',
            {'appid': 'wx-test', 'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
             'transaction_id': 'tx-delivery-race', 'trade_type': 'JSAPI', 'trade_state': 'SUCCESS',
             'success_time': self.intent.created_at.isoformat(), 'payer': {'openid': 'test-openid'},
             'amount': {'total': 10000, 'payer_total': 10000, 'currency': 'CNY', 'payer_currency': 'CNY'}}, 'a' * 64)

    def test_concurrent_duplicate_inbox_is_one_record(self):
        barrier = Barrier(2)
        def ingest():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return ingest_verified_payment_notice('mch-test', self.notice).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(ingest), workers.submit(ingest)]
            ids = [future.result(timeout=20) for future in futures]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(DirectPaymentInbox.objects.count(), 1)
        self.assertEqual(DirectPaymentInbox.objects.get().observations.count(), 1)

    def test_bad_and_valid_notice_race_on_historical_intent_without_session(self):
        from orders.direct_payment_session_models import DirectPaymentSession
        self.assertFalse(DirectPaymentSession.objects.filter(intent=self.intent).exists())
        valid = ingest_verified_payment_notice('mch-test', self.notice)
        bad_payload = deepcopy(self.notice.payload)
        bad_payload['amount']['currency'] = 'USD'
        bad = ingest_verified_payment_notice('mch-test', replace(self.notice, notification_id='bad-race',
                    payload=bad_payload, signed_body_sha256='b' * 64))
        barrier = Barrier(2)
        def process(pk):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return process_payment_inbox(pk).status
            finally:
                close_old_connections()
        with patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(process, (valid.pk, bad.pk)))
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'tx-delivery-race'))
        self.assertIsNotNone(self.intent.evidence_risk_at)
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertIn(order.payment_status, ('PAID', 'UNPAID'))
        event = DirectPaymentOutbox.objects.filter(intent=self.intent).first()
        self.assertTrue(event is None or event.status == 'REVIEW')

    def test_two_conflicting_events_freeze_only_matching_orders_without_deadlock(self):
        second_user = make_user()
        second_account = get_or_create_account_for_legacy_user(second_user)
        second_account.openid, second_account.wechat_appid, second_account.is_openid_bound = 'openid-two', 'wx-test', True
        second_account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(second_user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=second_account, legacy_user=second_user,
            data={'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'other-race'})
        second = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        payload_two = deepcopy(self.notice.payload)
        payload_two['out_trade_no'] = second.merchant_order_no
        payload_two['payer']['openid'] = 'openid-two'
        first_event = replace(self.notice, notification_id='conflict-first')
        second_event = replace(self.notice, notification_id='conflict-second', payload=payload_two)
        ingest_verified_payment_notice('mch-test', first_event)
        ingest_verified_payment_notice('mch-test', second_event)
        barrier = Barrier(2)
        def conflict(notice):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return ingest_verified_payment_notice('mch-test', notice).status
            finally:
                close_old_connections()
        changes = (replace(first_event, payload=payload_two, signed_body_sha256='b' * 64),
                   replace(second_event, payload=self.notice.payload, signed_body_sha256='c' * 64))
        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(list(workers.map(conflict, changes)), ['REVIEW', 'REVIEW'])
        self.intent.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(self.intent.evidence_risk_at)
        self.assertIsNotNone(second.evidence_risk_at)
        self.assertEqual((self.intent.transaction_id, second.transaction_id), (None, None))

    def test_competing_outbox_workers_respect_claim(self):
        row = ingest_verified_payment_notice('mch-test', self.notice)
        with patch('orders.direct_payment._notify_paid'):
            process_payment_inbox(row.pk)
        event = DirectPaymentOutbox.objects.get()
        entered, release = Event(), Event()
        def notify(order):
            entered.set()
            if not release.wait(10):
                raise AssertionError('worker was not released')
        def worker():
            close_old_connections()
            try:
                return process_payment_outbox(event.pk)
            finally:
                close_old_connections()
        with patch('orders.notifier.notify_order_update', side_effect=notify), \
             patch('orders.services.schedule_auto_cancel'), \
             patch('integrations.kook_config.get_kook_config', return_value=SimpleNamespace(auto_new_orders=False)), \
             ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(worker)
            try:
                self.assertTrue(entered.wait(10))
                second = workers.submit(worker)
                self.assertEqual(second.result(timeout=10), 'busy')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), 'done')
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts), ('DONE', 1))

    def test_distinct_conflicting_events_concurrently_block_dispatch(self):
        baseline = ingest_verified_payment_notice('mch-test', self.notice)
        with patch('orders.direct_payment._notify_paid'):
            self.assertEqual(process_payment_inbox(baseline.pk).status, 'DONE')
        changed = deepcopy(self.notice.payload)
        changed['amount']['payer_total'] = 9000
        first = ingest_verified_payment_notice('mch-test', replace(self.notice, notification_id='event-other',
                                                                   payload=changed, signed_body_sha256='b'*64))
        other = deepcopy(self.notice.payload)
        other['transaction_id'] = 'other-channel-transaction'
        second = ingest_verified_payment_notice('mch-test', replace(self.notice, notification_id='event-third',
                                                                    payload=other, signed_body_sha256='c'*64))
        barrier = Barrier(2)
        def process(pk):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return process_payment_inbox(pk).status
            finally:
                close_old_connections()
        with patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(process, (first.pk, second.pk)))
        self.intent.refresh_from_db()
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual(self.intent.status, 'REVIEW')
        self.assertEqual((order.payment_status, order.status), ('PAID', 'PENDING'))
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')
        self.assertIn('REVIEW', results)
