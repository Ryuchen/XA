from dataclasses import replace
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, current_thread
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.direct_payment import DirectPaymentError, VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.models import DirectPaymentIntent, Order
from orders.services import refund_to_customer, settle_order
from orders.settlement import SettlementError
from orders.state_machine import IllegalTransitionError, transition
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction, Wallet
from wallet.services import get_wallet


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectPaymentDomainTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.openid = 'test-openid'
        self.account.wechat_appid = 'wx-test'
        self.account.is_openid_bound = True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        self.wallet = make_wallet(self.user, balance=50000)
        self.service = make_service(price=10000)

    def request(self, **overrides):
        data = {'service_id': self.service.pk, 'expected_amount': 10000,
                'client_request_id': 'direct-req-1', 'dispatch_mode': 'AUTO_GRAB'}
        data.update(overrides)
        return SimpleNamespace(account=self.account, legacy_user=self.user, data=data)

    def prepare(self, **overrides):
        return prepare_direct_order(self.request(**overrides), appid='wx-test', mchid='mch-test')

    def evidence(self, intent, **overrides):
        base = VerifiedPayment(intent.merchant_order_no, 'wx-transaction-1', intent.appid,
                               intent.mchid, intent.openid, intent.amount_fen, 'CNY', timezone.now(),
                               payer_total_fen=intent.amount_fen, payer_currency='CNY')
        return replace(base, **overrides)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_staged_direct_payment_does_not_pollute_operational_dashboard(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        order = client.post('/api/orders/create/', {'service_id': self.service.pk, 'dispatch_route': 'STAFF',
            'client_request_id': 'dashboard-balance-order'}, format='json')
        self.assertEqual(order.data['code'], 0, order.data)
        staff = make_console_user(['dashboard:view', 'player_dashboard:view', 'order:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        before = client.get('/api/admin/dashboard/stats').data['data']
        player_before = client.get('/api/admin/dashboard/player').data['data']['kpi']
        intent = self.prepare()
        confirm_verified_payment(intent.order_id, self.evidence(intent))
        intent.order.refresh_from_db()
        self.assertEqual(intent.order.payment_status, 'PAID')
        after = client.get('/api/admin/dashboard/stats').data['data']
        player_after = client.get('/api/admin/dashboard/player').data['data']['kpi']
        for key in ('order_total', 'today_order_total', 'paid_order_total', 'paid_amount_total',
                    'pending_paid_total', 'order_status', 'daily_new_orders'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(player_after, player_before)
        self.assertNotIn(intent.order_id, [row['id'] for row in after['recent_orders']])
        all_orders = client.get('/api/admin/orders/', {'status': 'PENDING', 'payment_status': 'PAID'}).data['data']
        scoped = client.get('/api/admin/orders/', {'status': 'PENDING', 'payment_status': 'PAID', 'exclude_direct_test': '1'}).data['data']
        self.assertEqual((all_orders['total'], scoped['total']), (2, after['pending_paid_total']))
        self.assertTrue(scoped['exclude_direct_test_applied'])
        self.assertNotIn(intent.order_id, [row['id'] for row in scoped['list']])
        daily = client.get('/api/admin/orders/', {'created_day': timezone.localdate().isoformat(), 'exclude_direct_test': '1'}).data['data']
        self.assertEqual(daily['total'], after['today_order_total'])

    def test_buyer_display_never_treats_staging_evidence_as_real_payment(self):
        from orders.serializers import OrderSerializer
        intent = self.prepare()
        buyer = SimpleNamespace(account=self.account, legacy_user=self.user)
        other_user = make_user()
        other_account = get_or_create_account_for_legacy_user(other_user)
        for status in ('AWAITING', 'PAID', 'REVIEW'):
            with self.subTest(status=status):
                # The view projection must remain conservative even if a test
                # has populated a paid order or review evidence.
                intent.status = status
                intent.order.direct_payment = intent
                data = OrderSerializer(intent.order, context={'request': buyer}).data
                self.assertEqual(data['payment_display'], {
                    'kind': 'DIRECT_TEST', 'state': 'REVIEW' if status == 'REVIEW' else 'UNCONFIRMED',
                })
                self.assertIsNone(OrderSerializer(intent.order, context={'request': SimpleNamespace(account=other_account, legacy_user=other_user)}).data['payment_display'])
                self.assertIsNone(OrderSerializer(intent.order).data['payment_display'])

        client = APIClient()
        client.force_authenticate(self.user)
        detail = client.get(f'/api/orders/orders/{intent.order_id}/').data['data']
        listing = client.get('/api/orders/orders/').data['data']
        self.assertEqual(detail['payment_display']['kind'], 'DIRECT_TEST')
        self.assertEqual(next(row for row in listing if row['id'] == intent.order_id)['payment_display']['kind'], 'DIRECT_TEST')

    def test_unpaid_does_not_debit_wallet_or_enter_dispatch(self):
        intent = self.prepare()
        order = intent.order
        self.assertEqual((order.status, order.payment_status), ('PENDING', 'UNPAID'))
        self.assertIsNone(order.pool_entered_at)
        self.assertIsNone(order.auto_cancel_at)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)
        self.assertFalse(Transaction.objects.filter(order=order).exists())
        with self.assertRaises(IllegalTransitionError):
            transition(order.pk, Order.Status.GRABBED)
        self.assertEqual(self.prepare().pk, intent.pk)
        with self.assertRaises(DirectPaymentError):
            self.prepare(remark='different')
        self.assertEqual(Order.objects.count(), 1)

    def test_unpaid_buyer_and_staff_cancel_are_blocked_without_hiding_order(self):
        intent = self.prepare()
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(f'/api/orders/orders/{intent.order_id}/cancel/', {}, format='json')
        self.assertEqual((response.data['code'], response.data['errorCode']),
                         (409, 'DIRECT_PAYMENT_CANCELLATION_UNCONFIRMED'))
        self.assertIn('订单尚未取消', response.data['msg'])
        detail = client.get(f'/api/orders/orders/{intent.order_id}/')
        self.assertEqual(detail.data['code'], 0)
        staff = make_console_user(['order:view', 'order:cancel'])
        client.force_authenticate(staff)
        response = client.post(f'/api/admin/orders/{intent.order_id}/cancel/', {'reason': 'test'}, format='json')
        self.assertEqual((response.data['code'], response.data['errorCode']),
                         (409, 'DIRECT_PAYMENT_CANCELLATION_UNCONFIRMED'))
        self.assertIn('订单尚未取消', response.data['msg'])
        intent.order.refresh_from_db()
        self.assertEqual((intent.order.status, intent.order.payment_status), ('PENDING', 'UNPAID'))
        self.assertFalse(Transaction.objects.filter(order_id=intent.order_id).exists())
        # Internal reconciliation remains available for explicit test scenarios;
        # this local transition is not evidence of a channel close.
        self.assertEqual(transition(intent.order_id, Order.Status.CANCELLED).status, 'CANCELLED')

    def test_missing_cash_evidence_is_not_defaulted_and_goes_to_review(self):
        intent = self.prepare()
        evidence = self.evidence(intent, payer_total_fen=None, payer_currency=None, promotion_detail=None)
        with patch('orders.direct_payment._notify_paid') as notify, self.captureOnCommitCallbacks(execute=True):
            result = confirm_verified_payment(intent.order_id, evidence)
        self.assertEqual(result.status, 'REVIEW')
        self.assertIsNone(result.payer_total_fen)
        self.assertIsNone(result.payer_currency)
        self.assertIsNone(result.promotion_detail)
        self.assertTrue(result.evidence_issue)
        self.assertEqual(Order.objects.get(pk=intent.order_id).payment_status, 'UNPAID')
        notify.assert_not_called()

    def test_channel_coupon_and_zero_cash_are_preserved_for_review(self):
        for index, cash in enumerate((8000, 0)):
            intent = self.prepare(client_request_id=f'direct-coupon-{index}')
            details = [{'coupon_id': 'coupon-1', 'type': 'NOCASH', 'amount': 10000-cash,
                        'merchant_contribute': 10000-cash}]
            evidence = self.evidence(intent, transaction_id=f'coupon-tx-{index}', payer_total_fen=cash,
                                     promotion_detail=details)
            result = confirm_verified_payment(intent.order_id, evidence)
            self.assertEqual(result.status, 'REVIEW')
            self.assertEqual(result.payer_total_fen, cash)
            self.assertEqual(result.promotion_detail, details)
            details[0]['amount'] = -1
            result.refresh_from_db()
            self.assertNotEqual(result.promotion_detail, details)
            self.assertEqual(Order.objects.get(pk=intent.order_id).payment_status, 'UNPAID')

    def test_invalid_payer_values_and_unknown_promotions_are_reviewed(self):
        cases = [(-1, 'CNY', None), (10001, 'CNY', None), (10000, 'USD', None),
                 (8000, 'CNY', None), (10000, 'CNY', {'not': 'a list'})]
        for index, (cash, currency, details) in enumerate(cases):
            intent = self.prepare(client_request_id=f'invalid-cash-{index}')
            evidence = self.evidence(intent, transaction_id=f'invalid-tx-{index}',
                                     payer_total_fen=cash, payer_currency=currency, promotion_detail=details)
            result = confirm_verified_payment(intent.order_id, evidence)
            self.assertEqual(result.status, 'REVIEW')
            self.assertTrue(result.evidence_issue)
            self.assertEqual(Order.objects.get(pk=intent.order_id).payment_status, 'UNPAID')

    def test_no_discount_cash_is_recorded_without_inventing_promotion_details(self):
        intent = self.prepare()
        evidence = self.evidence(intent, promotion_detail=None)
        result = confirm_verified_payment(intent.order_id, evidence)
        self.assertEqual((result.status, result.payer_total_fen, result.payer_currency), ('PAID', 10000, 'CNY'))
        self.assertIsNone(result.promotion_detail)
        with self.assertRaises(DirectPaymentError):
            confirm_verified_payment(intent.order_id, replace(evidence, payer_total_fen=9999))
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    DirectPaymentIntent.objects.filter(pk=intent.pk).update(payer_total_fen=-1)

    def test_staging_cannot_be_enabled_by_default(self):
        with override_settings(WECHAT_DIRECT_DOMAIN_TESTING=False):
            with self.assertRaises(DirectPaymentError):
                self.prepare()
        self.assertFalse(DirectPaymentIntent.objects.exists())

    def test_simulated_payment_is_rejected_for_nonisolated_config_or_database(self):
        from orders.direct_payment import _require_test_domain

        database = connections['default'].settings_dict
        with patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            with self.assertRaises(DirectPaymentError):
                self.prepare()
        # Simulate a production DB and an ordinary persistent SQLite DB
        # without opening connections or writing to either one.
        unsafe = (
            {'ENGINE': 'django.db.backends.mysql', 'NAME': 'xa_db',
             'HOST': '127.0.0.1', 'USER': 'xa_test'},
            {'ENGINE': 'django.db.backends.mysql', 'NAME': 'test_xa_test_demo',
             'HOST': 'production.example', 'USER': 'xa_test'},
            {'ENGINE': 'django.db.backends.mysql', 'NAME': 'test_xa_test_demo',
             'HOST': '127.0.0.1', 'USER': 'root'},
            {'ENGINE': 'django.db.backends.sqlite3', 'NAME': 'business.sqlite3'},
        )
        for candidate in unsafe:
            with self.subTest(database=candidate), patch.dict(database, candidate):
                with self.assertRaises(DirectPaymentError):
                    _require_test_domain()
        self.assertFalse(DirectPaymentIntent.objects.exists())
        self.assertFalse(Order.objects.exists())

    def test_paid_simulation_cannot_authorize_dispatch_outside_test_environment(self):
        from orders.applications import ApplicationError, public_order
        from orders.direct_payment_quote import quote_matches_intent
        from orders.staff_dispatch_readiness import dispatch_assignment_allowed

        intent = self.prepare()
        confirm_verified_payment(intent.order_id, self.evidence(intent))
        intent.refresh_from_db()
        order = Order.objects.get(pk=intent.order_id)
        self.assertEqual((intent.status, order.payment_status), ('PAID', 'PAID'))
        self.assertTrue(quote_matches_intent(intent, order))
        with patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            self.assertFalse(quote_matches_intent(intent, order))
            self.assertFalse(dispatch_assignment_allowed(order))
            with self.assertRaises(ApplicationError):
                public_order(order)
            with self.assertRaises(IllegalTransitionError):
                transition(order.pk, Order.Status.GRABBED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)

    def test_preparation_rechecks_fresh_account_status_under_lock(self):
        for field in ('is_active', 'can_login'):
            type(self.account).objects.filter(pk=self.account.pk).update(**{field: False})
            with self.subTest(field=field), self.assertRaises(DirectPaymentError):
                self.prepare()
            type(self.account).objects.filter(pk=self.account.pk).update(**{field: True})
        self.assertFalse(DirectPaymentIntent.objects.exists())
        self.assertFalse(Order.objects.exists())

    def test_only_matching_authenticated_evidence_activates_once(self):
        intent = self.prepare()
        evidence = self.evidence(intent)
        for field, value in [('amount_fen', 9999), ('mchid', 'other'), ('appid', 'other'),
                             ('openid', 'other'), ('merchant_order_no', 'other'), ('currency', 'USD')]:
            with self.subTest(field=field), self.assertRaises(DirectPaymentError):
                confirm_verified_payment(intent.order_id, replace(evidence, **{field: value}))
        intent.order.refresh_from_db()
        self.assertEqual(intent.order.payment_status, 'UNPAID')
        with patch('orders.direct_payment._notify_paid') as notify, self.captureOnCommitCallbacks(execute=True):
            result = confirm_verified_payment(intent.order_id, evidence)
            first = Order.objects.get(pk=intent.order_id)
            self.assertEqual((result.status, first.payment_status), ('PAID', 'PAID'))
            self.assertIsNotNone(first.pool_entered_at)
            self.assertEqual(first.auto_cancel_at - first.pool_entered_at, timedelta(minutes=30))
            confirm_verified_payment(intent.order_id, evidence)
        notify.assert_called_once_with(intent.order_id)
        first.refresh_from_db()
        self.assertEqual(first.pool_entered_at, result.order.pool_entered_at)
        self.assertFalse(Transaction.objects.filter(order=first).exists())
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)

    def test_same_channel_transaction_cannot_pay_another_order(self):
        first = self.prepare()
        second = self.prepare(client_request_id='direct-req-2')
        paid = self.evidence(first)
        confirm_verified_payment(first.order_id, paid)
        with self.assertRaises(DirectPaymentError):
            confirm_verified_payment(second.order_id, self.evidence(second, transaction_id=paid.transaction_id))
        second.refresh_from_db()
        second.order.refresh_from_db()
        self.assertEqual((second.status, second.order.payment_status), ('AWAITING', 'UNPAID'))

    def test_other_integrity_errors_are_not_misreported_as_duplicate_transaction(self):
        intent = self.prepare()
        with patch('orders.direct_payment_models.DirectPaymentIntent.save', side_effect=IntegrityError('database constraint failure')):
            with self.assertRaisesRegex(IntegrityError, 'database constraint failure'):
                confirm_verified_payment(intent.order_id, self.evidence(intent))
        intent.order.refresh_from_db()
        self.assertEqual(intent.order.payment_status, 'UNPAID')

    def test_late_success_after_cancellation_is_recorded_for_review(self):
        intent = self.prepare()
        transition(intent.order_id, Order.Status.CANCELLED)
        evidence = self.evidence(intent)
        with patch('orders.direct_payment._notify_paid') as notify, self.captureOnCommitCallbacks(execute=True):
            updated = confirm_verified_payment(intent.order_id, evidence)
            self.assertEqual(updated.status, 'REVIEW')
            self.assertEqual(confirm_verified_payment(intent.order_id, evidence).pk, updated.pk)
        notify.assert_not_called()
        intent.order.refresh_from_db()
        self.assertEqual((intent.order.status, intent.order.payment_status), ('CANCELLED', 'UNPAID'))
        self.assertFalse(Transaction.objects.filter(order=intent.order).exists())

    def test_changed_account_cycle_is_recorded_for_review_without_publication(self):
        intent = self.prepare()
        type(self.account).objects.filter(pk=self.account.pk).update(business_cycle=self.account.business_cycle + 1)
        with patch('orders.direct_payment._notify_paid') as notify, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(confirm_verified_payment(intent.order_id, self.evidence(intent)).status, 'REVIEW')
        notify.assert_not_called()
        order = Order.objects.get(pk=intent.order_id)
        self.assertEqual(order.payment_status, 'UNPAID')
        self.assertIsNone(order.pool_entered_at)

    def test_changed_wechat_binding_cannot_resume_or_publish_old_payment(self):
        intent = self.prepare()
        type(self.account).objects.filter(pk=self.account.pk).update(openid='changed-openid')
        with self.assertRaises(DirectPaymentError):
            self.prepare()
        with patch('orders.direct_payment._notify_paid') as notify, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(confirm_verified_payment(intent.order_id, self.evidence(intent)).status, 'REVIEW')
        notify.assert_not_called()
        order = Order.objects.get(pk=intent.order_id)
        self.assertEqual(order.payment_status, 'UNPAID')
        self.assertIsNone(order.pool_entered_at)

    def test_selected_provider_and_coupon_are_rejected_without_creating_order(self):
        from orders.tests.factories import make_coupon, make_user_coupon
        provider = make_provider()
        provider_account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        coupon = make_user_coupon(self.user, coupon=make_coupon(threshold=10000, amount=1000), account=self.account)
        with self.assertRaises(DirectPaymentError):
            self.prepare(provider_id=provider_account.pk)
        with self.assertRaises(DirectPaymentError):
            self.prepare(user_coupon_id=coupon.pk, expected_amount=9000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(DirectPaymentIntent.objects.exists())

    def test_existing_balance_refund_path_refuses_direct_payment(self):
        intent = self.prepare()
        confirm_verified_payment(intent.order_id, self.evidence(intent))
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=intent.order_id)
            with self.assertRaises(SettlementError):
                refund_to_customer(order)
            with self.assertRaises(SettlementError):
                settle_order(order)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)
        self.assertFalse(Transaction.objects.filter(order_id=intent.order_id, tx_type='REFUND').exists())

    def test_balance_refund_preview_and_transfer_refuse_direct_payment(self):
        from orders.refunds import RefundError, build_refund_plan
        intent = self.prepare()
        confirm_verified_payment(intent.order_id, self.evidence(intent))
        order = Order.objects.get(pk=intent.order_id)
        with self.assertRaises(RefundError) as error:
            build_refund_plan(order)
        self.assertEqual(error.exception.code, 'REFUND_CHANNEL_UNSUPPORTED')
        Order.objects.filter(pk=order.pk).update(status=Order.Status.IN_SERVICE, settlement_mode=Order.SettlementMode.COMPLETION)
        staff = make_console_user(['order:view', 'order:dispatch'])
        client = APIClient()
        client.force_authenticate(staff)
        response = client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers': []}, format='json')
        self.assertEqual(response.data['code'], 400)
        self.assertIn('微信支付', response.data['msg'])
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_unpaid_notification_never_broadcasts_to_provider_pool(self):
        from orders.notifier import notify_order_update
        intent = self.prepare()
        layer = SimpleNamespace(group_send=AsyncMock())
        with patch('orders.notifier.get_channel_layer', return_value=layer):
            notify_order_update(intent.order)
        groups = [call.args[0] for call in layer.group_send.call_args_list]
        self.assertNotIn('providers', groups)

    def test_visibility_delay_starts_when_payment_enters_pool(self):
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        intent = self.prepare()
        Order.objects.filter(pk=intent.order_id).update(created_at=timezone.now() - timedelta(hours=1))
        confirm_verified_payment(intent.order_id, self.evidence(intent))
        client = APIClient()
        client.force_authenticate(provider)
        with patch.object(EscortProfile, 'order_visibility_delay_seconds', new_callable=lambda: property(lambda _: 60)):
            listing = client.get('/api/orders/orders/?status=pending')
            self.assertNotIn(intent.order_id, [item['id'] for item in listing.data['data']])
            self.assertNotEqual(client.post(f'/api/orders/orders/{intent.order_id}/grab/').data['code'], 0)
            Order.objects.filter(pk=intent.order_id).update(pool_entered_at=timezone.now() - timedelta(seconds=61))
            listing = client.get('/api/orders/orders/?status=pending')
            self.assertIn(intent.order_id, [item['id'] for item in listing.data['data']])
            self.assertEqual(client.post(f'/api/orders/orders/{intent.order_id}/grab/').data['code'], 0)


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class DirectPaymentConcurrencyTest(TransactionTestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.openid, self.account.wechat_appid, self.account.is_openid_bound = 'test-openid', 'wx-test', True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        self.wallet = make_wallet(self.user, balance=50000)
        self.service = make_service(price=10000)
        request = SimpleNamespace(account=self.account, legacy_user=self.user,
            data={'service_id': self.service.pk, 'expected_amount': 10000, 'client_request_id': 'direct-race-1'})
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        self.evidence = VerifiedPayment(self.intent.merchant_order_no, 'wx-race-transaction', 'wx-test',
                                        'mch-test', 'test-openid', 10000, 'CNY', timezone.now(),
                                        payer_total_fen=10000, payer_currency='CNY')

    def test_duplicate_verified_callbacks_capture_one_transaction(self):
        barrier = Barrier(2)
        def callback():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return confirm_verified_payment(self.intent.order_id, self.evidence).status
            finally:
                close_old_connections()
        with patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as workers:
            results = [worker.result(timeout=20) for worker in (workers.submit(callback), workers.submit(callback))]
        self.assertEqual(results, ['PAID', 'PAID'])
        self.assertEqual(DirectPaymentIntent.objects.filter(transaction_id=self.evidence.transaction_id).count(), 1)
        self.assertFalse(Transaction.objects.filter(order_id=self.intent.order_id).exists())

    def test_cancel_and_payment_compete_without_creating_wallet_refund(self):
        barrier = Barrier(2)
        token, _ = issue_account_tokens(self.account)
        def callback():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return confirm_verified_payment(self.intent.order_id, self.evidence).status
            finally:
                close_old_connections()
        def cancel():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post(f'/api/orders/orders/{self.intent.order_id}/cancel/', {}, format='json').data['code']
            finally:
                close_old_connections()
        with patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as workers:
            captured, cancelled = workers.submit(callback), workers.submit(cancel)
            captured_status, cancel_code = captured.result(timeout=20), cancelled.result(timeout=20)
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((captured_status, order.status, order.payment_status), ('PAID', 'PENDING', 'PAID'))
        self.assertNotEqual(cancel_code, 0)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_payment_waits_for_account_ban_and_records_review(self):
        from club_accounts.models import ClubAccount
        from console.ban_utils import active_ban_for, apply_ban
        locked, release, attempted = Event(), Event(), Event()
        account_select = ClubAccount.objects.select_for_update

        def pause_ban(*args, **kwargs):
            result = active_ban_for(*args, **kwargs)
            locked.set()
            if not release.wait(10):
                raise AssertionError('ban was not released')
            return result

        def account_lock(*args, **kwargs):
            if current_thread().name == 'payment':
                attempted.set()
            return account_select(*args, **kwargs)

        def ban():
            close_old_connections()
            try:
                return apply_ban(ClubAccount.objects.get(pk=self.account.pk), 'test ban')
            finally:
                close_old_connections()

        def callback():
            current_thread().name = 'payment'
            close_old_connections()
            try:
                return confirm_verified_payment(self.intent.order_id, self.evidence).status
            finally:
                close_old_connections()

        with patch('console.ban_utils.active_ban_for', side_effect=pause_ban), \
             patch.object(ClubAccount.objects, 'select_for_update', side_effect=account_lock), \
             patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as workers:
            ban_result = workers.submit(ban)
            try:
                self.assertTrue(locked.wait(10))
                paid_result = workers.submit(callback)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(paid_result.done())
            finally:
                release.set()
            ban_result.result(timeout=20)
            self.assertEqual(paid_result.result(timeout=20), 'REVIEW')
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual(order.payment_status, 'UNPAID')
        self.assertIsNone(order.pool_entered_at)

    def test_preparation_waits_for_account_ban_and_rejects(self):
        from club_accounts.models import ClubAccount
        from console.ban_utils import active_ban_for, apply_ban
        locked, release, attempted = Event(), Event(), Event()
        account_select = ClubAccount.objects.select_for_update

        def pause_ban(*args, **kwargs):
            result = active_ban_for(*args, **kwargs)
            locked.set()
            if not release.wait(10):
                raise AssertionError('ban was not released')
            return result

        def account_lock(*args, **kwargs):
            if current_thread().name == 'preparation':
                attempted.set()
            return account_select(*args, **kwargs)

        def ban():
            close_old_connections()
            try:
                return apply_ban(ClubAccount.objects.get(pk=self.account.pk), 'test ban')
            finally:
                close_old_connections()

        def prepare():
            current_thread().name = 'preparation'
            close_old_connections()
            try:
                request = SimpleNamespace(account=ClubAccount.objects.get(pk=self.account.pk), legacy_user=self.user,
                    data={'service_id': self.service.pk, 'expected_amount': 10000, 'client_request_id': 'direct-race-2'})
                with self.assertRaises(DirectPaymentError):
                    prepare_direct_order(request, appid='wx-test', mchid='mch-test')
            finally:
                close_old_connections()

        with patch('console.ban_utils.active_ban_for', side_effect=pause_ban), \
             patch.object(ClubAccount.objects, 'select_for_update', side_effect=account_lock), \
             ThreadPoolExecutor(max_workers=2) as workers:
            ban_result = workers.submit(ban)
            try:
                self.assertTrue(locked.wait(10))
                prepare_result = workers.submit(prepare)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(prepare_result.done())
            finally:
                release.set()
            ban_result.result(timeout=20)
            prepare_result.result(timeout=20)
        self.assertEqual(DirectPaymentIntent.objects.count(), 1)

    def test_payment_waits_for_retirement_wallet_lock_without_deadlock(self):
        from club_accounts.models import ClubAccount
        from engagement.retirement import retire
        transition(self.intent.order_id, Order.Status.CANCELLED)
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=0)
        locked, release, attempted = Event(), Event(), Event()

        def retirement_wallet(*args, **kwargs):
            result = get_wallet(*args, **kwargs)
            locked.set()
            if not release.wait(10):
                raise AssertionError('retirement was not released')
            return result

        def payment_wallet(*args, **kwargs):
            attempted.set()
            return get_wallet(*args, **kwargs)

        def retirement():
            close_old_connections()
            try:
                account = ClubAccount.objects.get(pk=self.account.pk)
                return retire(account, account, '测试退店').business_cycle
            finally:
                close_old_connections()

        def callback():
            close_old_connections()
            try:
                return confirm_verified_payment(self.intent.order_id, self.evidence).status
            finally:
                close_old_connections()

        with patch('engagement.retirement.get_wallet', side_effect=retirement_wallet), \
             patch('orders.direct_payment.get_wallet', side_effect=payment_wallet), \
             patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as workers:
            retirement_result = workers.submit(retirement)
            try:
                self.assertTrue(locked.wait(10))
                paid_result = workers.submit(callback)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(paid_result.done())
            finally:
                release.set()
            self.assertEqual(retirement_result.result(timeout=20), self.account.business_cycle + 1)
            self.assertEqual(paid_result.result(timeout=20), 'REVIEW')
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'UNPAID'))
        self.assertFalse(Transaction.objects.filter(order=order).exists())
