"""Refunds share one financial policy and publish only committed state."""
from datetime import timedelta
from unittest.mock import patch

from django.db import connection, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from engagement.models import MemberProfile, PointFundingLot, PointLedger
from engagement.services import allocate_order_points, award_order_points
from orders.models import AfterSale, Order, OrderProvider
from orders.serializers import OrderSerializer
from orders.services import refund_to_customer
from orders.settlement import SettlementError
from orders.tests.factories import (
    make_console_user, make_order, make_provider, make_service, make_user, make_user_coupon,
)
from wallet.models import IncomeHold, ProviderReport, Transaction, WithdrawRequest
from wallet.services import WalletAddressingError, get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class AfterSaleRefundRealtimeTest(APITransactionTestCase):
    def setUp(self):
        self.boss, self.provider = make_user(), make_provider(deposit_mode='CASH')
        self.admin = make_console_user(['order:view', 'order:refund', 'order:cancel', 'order:settle'])
        self.accounts = {}
        for user in (self.boss, self.provider, self.admin):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            self.accounts[user.pk] = account
        membership = self.admin.admin_membership
        membership.account = self.accounts[self.admin.pk]
        membership.save(update_fields=['account'])
        self.customer_wallet = get_wallet(user=self.boss, account=self.accounts[self.boss.pk])
        self.customer_wallet.balance = 90000
        self.customer_wallet.save(update_fields=['balance'])
        self.provider_wallet = get_wallet(user=self.provider, account=self.accounts[self.provider.pk])
        self.service = make_service(price=1000)
        self.customer = self.client_for(self.accounts[self.boss.pk])
        self.staff = self.client_for(self.accounts[self.admin.pk])

    def client_for(self, account):
        client = APIClient()
        token, _ = issue_account_tokens(account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        return client

    def order(self, *, age=1, status='COMPLETED', **kwargs):
        return make_order(
            self.boss, self.service, self.provider, status=status,
            customer_account=self.accounts[self.boss.pk], provider_account=self.accounts[self.provider.pk],
            payment_status='PAID', completed_at=timezone.now() - timedelta(hours=age),
            provider_income=800, shop_income=200, commission_rate=20, **kwargs,
        )

    def aftersale(self, order):
        return AfterSale.objects.create(order=order, account=self.accounts[self.boss.pk], reason='本地售后验证')

    def post(self, order, action, row=None):
        if action == 'create':
            return self.customer.post(f'/api/orders/orders/{order.pk}/aftersale/', {'reason': '本地售后验证'}, format='json')
        if action in ('resolve', 'aftersale-refund'):
            endpoint = 'refund' if action == 'aftersale-refund' else action
            return self.staff.post(f'/api/admin/aftersales/{row.pk}/{endpoint}/', {'resolution': '本地客服处理结论'}, format='json')
        if action == 'legacy-refund':
            return self.staff.post(f'/api/orders/orders/{order.pk}/refund/', {'reason': '本地客服退款'}, format='json')
        return self.staff.post(f'/api/admin/orders/{order.pk}/{action}/', {'reason': '本地客服退款'}, format='json')

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def rejected(self, response):
        self.assertIn(response.status_code, (200, 400), response.data)
        self.assertNotEqual(response.data.get('code', 400), 0, response.data)

    def test_create_and_replay_publish_only_one_committed_open_state(self):
        order = self.order()
        def publish(saved):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(OrderSerializer(Order.objects.get(pk=saved.pk)).data['aftersale_status'], 'OPEN')
        with patch('orders.aftersale.notify_order_update', side_effect=publish) as notify:
            with transaction.atomic():
                self.ok(self.post(order, 'create'))
                notify.assert_not_called()
            notify.assert_called_once()
            self.ok(self.post(order, 'create'))
            notify.assert_called_once()
        self.assertEqual(order.aftersales.count(), 1)

    def test_resolve_publishes_final_state_without_refund_or_repeat(self):
        order = self.order(age=73)
        row = self.aftersale(order)
        def publish(saved):
            self.assertFalse(connection.in_atomic_block)
            data = OrderSerializer(Order.objects.get(pk=saved.pk)).data
            self.assertEqual((data['aftersale_status'], data['status'], data['payment_status']), ('RESOLVED', 'COMPLETED', 'PAID'))
        with patch('orders.aftersale.notify_order_update', side_effect=publish) as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
            with transaction.atomic():
                self.ok(self.post(order, 'resolve', row))
                notify.assert_not_called()
            notify.assert_called_once()
            self.rejected(self.post(order, 'resolve', row))
            notify.assert_called_once()
            wallet_notify.assert_not_called()
        self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())

    def test_open_case_can_refund_after_window_and_notifies_final_states_once(self):
        order = self.order(age=73)
        row = self.aftersale(order)
        def publish(saved):
            self.assertFalse(connection.in_atomic_block)
            data = OrderSerializer(Order.objects.get(pk=saved.pk)).data
            self.assertEqual((data['aftersale_status'], data['status'], data['payment_status']), ('REFUNDED', 'CANCELLED', 'REFUNDED'))
        with patch('orders.aftersale.notify_order_update', side_effect=publish) as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
            with transaction.atomic():
                self.ok(self.post(order, 'aftersale-refund', row))
                notify.assert_not_called()
                wallet_notify.assert_not_called()
            notify.assert_called_once()
            wallet_notify.assert_called_once()
            self.rejected(self.post(order, 'aftersale-refund', row))
            notify.assert_called_once()
            wallet_notify.assert_called_once()
        self.customer_wallet.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.customer_wallet.balance, 91000)
        self.assertEqual(order.cancel_reason, '本地客服处理结论')
        refund = order.transactions.get(tx_type='REFUND')
        self.assertEqual((refund.amount, refund.balance_before, refund.balance_after), (1000, 90000, 91000))
        self.assertEqual((refund.operator_id, refund.operator_account_id), (self.admin.pk, self.accounts[self.admin.pk].pk))

    def test_invalid_create_or_processing_does_not_publish(self):
        order = self.order(age=73)
        with patch('orders.aftersale.notify_order_update') as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
            self.rejected(self.post(order, 'create'))
            fresh = self.order()
            response = self.customer.post(f'/api/orders/orders/{fresh.pk}/aftersale/', {'reason': '短'}, format='json')
            self.rejected(response)
            row = self.aftersale(fresh)
            response = self.staff.post(f'/api/admin/aftersales/{row.pk}/refund/', {'resolution': ''}, format='json')
            self.rejected(response)
            notify.assert_not_called()
            wallet_notify.assert_not_called()
        self.assertFalse(Transaction.objects.filter(tx_type='REFUND').exists())

    def test_outer_rollback_discards_each_aftersale_change_and_notification(self):
        for action in ('create', 'resolve', 'aftersale-refund'):
            with self.subTest(action=action):
                order = self.order()
                row = None if action == 'create' else self.aftersale(order)
                with patch('orders.aftersale.notify_order_update') as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
                    with self.assertRaisesRegex(RuntimeError, 'outer rollback'):
                        with transaction.atomic():
                            self.ok(self.post(order, action, row))
                            notify.assert_not_called()
                            wallet_notify.assert_not_called()
                            raise RuntimeError('outer rollback')
                    notify.assert_not_called()
                    wallet_notify.assert_not_called()
                if row:
                    row.refresh_from_db()
                    self.assertEqual(row.status, 'OPEN')
                else:
                    self.assertFalse(order.aftersales.exists())
                order.refresh_from_db()
                self.customer_wallet.refresh_from_db()
                self.assertEqual((order.status, order.payment_status, self.customer_wallet.balance), ('COMPLETED', 'PAID', 90000))
                self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())

    def test_aftersale_save_failure_rolls_back_refund_and_registered_callback(self):
        order = self.order()
        row = self.aftersale(order)
        with patch('orders.aftersale.notify_order_update') as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
            with patch('orders.aftersale.AfterSale.save', side_effect=RuntimeError('case save failed')):
                with self.assertRaisesRegex(RuntimeError, 'case save failed'):
                    self.post(order, 'aftersale-refund', row)
            notify.assert_not_called()
            wallet_notify.assert_not_called()
        row.refresh_from_db()
        order.refresh_from_db()
        self.customer_wallet.refresh_from_db()
        self.assertEqual((row.status, order.status, order.payment_status, self.customer_wallet.balance), ('OPEN', 'COMPLETED', 'PAID', 90000))
        self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())

    def test_notification_failure_preserves_committed_refund(self):
        order = self.order()
        row = self.aftersale(order)
        with patch('orders.notifier.get_channel_layer', side_effect=RuntimeError('offline')), patch('wallet.notifier.get_channel_layer', side_effect=RuntimeError('offline')):
            with self.assertLogs('orders.notifier', level='WARNING'), self.assertLogs('wallet.notifier', level='WARNING'):
                self.ok(self.post(order, 'aftersale-refund', row))
        self.customer_wallet.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual((self.customer_wallet.balance, row.status), (91000, 'REFUNDED'))

    def assert_settled_refund_blocked(self, disposition):
        order = self.order(age=73, settled_at=timezone.now())
        row = self.aftersale(order)
        OrderProvider.objects.create(order=order, provider=self.provider, provider_account=self.accounts[self.provider.pk],
                                     settlement_base=1000, provider_income=800, settled_at=timezone.now())
        income = Transaction.objects.create(wallet=self.provider_wallet, order=order, tx_type='INCOME', amount=800)
        self.provider_wallet.balance = 800 if disposition == 'available' else 0
        self.provider_wallet.frozen_amount = 800 if disposition == 'frozen' else 0
        self.provider_wallet.save(update_fields=['balance', 'frozen_amount'])
        if disposition == 'frozen':
            IncomeHold.objects.create(wallet=self.provider_wallet, income_transaction=income, amount=800, release_at=timezone.now()+timedelta(hours=72))
        if disposition == 'withdrawn':
            Transaction.objects.create(wallet=self.provider_wallet, tx_type='WITHDRAW', amount=-800)
            WithdrawRequest.objects.create(user=self.provider, account=self.accounts[self.provider.pk], amount=800,
                                           status='APPROVED', payee_method='WECHAT', payee_account='LOCAL_TEST_ONLY', payee_name='本地样例')
        tx_count, hold_count = Transaction.objects.count(), IncomeHold.objects.count()
        with patch('orders.aftersale.notify_order_update') as after_notify, patch('console.views.notify_order_update') as console_notify, patch('orders.services.notify_wallet_update') as wallet_notify:
            for action in ('refund', 'aftersale-refund'):
                self.rejected(self.post(order, action, row))
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                with self.assertRaises(SettlementError):
                    refund_to_customer(locked)
            after_notify.assert_not_called()
            console_notify.assert_not_called()
            wallet_notify.assert_not_called()
        row.refresh_from_db()
        order.refresh_from_db()
        self.customer_wallet.refresh_from_db()
        self.provider_wallet.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, row.status), ('COMPLETED', 'PAID', 'OPEN'))
        self.assertEqual(self.customer_wallet.balance, 90000)
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount),
                         (800 if disposition == 'available' else 0, 800 if disposition == 'frozen' else 0))
        self.assertEqual((Transaction.objects.count(), IncomeHold.objects.count()), (tx_count, hold_count))

    def test_settled_available_income_is_protected(self):
        self.assert_settled_refund_blocked('available')

    def test_settled_frozen_income_is_protected(self):
        self.assert_settled_refund_blocked('frozen')

    def test_settled_withdrawn_insufficient_income_is_protected(self):
        self.assert_settled_refund_blocked('withdrawn')

    def test_partial_settlement_and_historical_income_without_markers_are_protected(self):
        for marker in ('share', 'ledger'):
            with self.subTest(marker=marker):
                order = self.order()
                row = self.aftersale(order)
                if marker == 'share':
                    OrderProvider.objects.create(order=order, provider=self.provider, settlement_base=1000,
                                                 provider_income=800, settled_at=timezone.now())
                else:
                    Transaction.objects.create(wallet=self.provider_wallet, order=order, tx_type='INCOME', amount=800)
                with patch('orders.aftersale.notify_order_update') as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
                    self.rejected(self.post(order, 'aftersale-refund', row))
                    self.rejected(self.post(order, 'refund'))
                    order.status = 'GRABBED'
                    order.save(update_fields=['status'])
                    self.rejected(self.post(order, 'legacy-refund'))
                    notify.assert_not_called()
                    wallet_notify.assert_not_called()
                self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())

    def test_console_enforces_cutoff_and_consumption_confirmation_as_business_errors(self):
        now = timezone.now()
        for fields in ({'completed_at': now-timedelta(hours=72)}, {'consumption_confirmed_at': now}):
            with self.subTest(fields=fields):
                order = self.order()
                Order.objects.filter(pk=order.pk).update(**fields)
                with patch('django.utils.timezone.now', return_value=now), patch('console.views.notify_order_update') as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
                    self.rejected(self.post(order, 'refund'))
                    notify.assert_not_called()
                    wallet_notify.assert_not_called()
                order.refresh_from_db()
                self.assertEqual((order.status, order.payment_status), ('COMPLETED', 'PAID'))
                self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())

    def test_console_refund_reverses_points_allocations_and_coupon_once(self):
        order = self.order()
        with transaction.atomic():
            allocate_order_points(order, self.customer_wallet, 91000)
            award_order_points(order)
        self.assertEqual(MemberProfile.objects.get(account=self.accounts[self.boss.pk]).points_balance, 1000)
        lot = PointFundingLot.objects.get(account=self.accounts[self.boss.pk])
        self.assertEqual(lot.remaining, 90000)
        coupon = make_user_coupon(self.boss, status='USED', order=order)
        with patch('console.views.notify_order_update') as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
            self.ok(self.post(order, 'refund'))
            self.rejected(self.post(order, 'refund'))
            notify.assert_called_once()
            wallet_notify.assert_called_once()
        lot.refresh_from_db()
        coupon.refresh_from_db()
        self.assertEqual(lot.remaining, 91000)
        self.assertEqual(MemberProfile.objects.get(account=self.accounts[self.boss.pk]).points_balance, 0)
        self.assertEqual(PointLedger.objects.filter(order=order, kind='REFUND').count(), 1)
        self.assertFalse(order.point_allocations.filter(released=False).exists())
        self.assertEqual(coupon.status, 'UNUSED')
        refund = order.transactions.get(tx_type='REFUND')
        self.assertEqual((refund.operator_id, refund.operator_account_id), (self.admin.pk, self.accounts[self.admin.pk].pk))

    def test_open_case_blocks_settlement_then_resolution_allows_single_credit(self):
        order = self.order(age=73)
        row = self.aftersale(order)
        Transaction.objects.create(wallet=self.customer_wallet, order=order, tx_type='PAY', amount=-1000)
        ProviderReport.objects.create(provider=self.provider, provider_account=self.accounts[self.provider.pk], order=order,
                                      game_name='本地样例', amount=1000, status='APPROVED', submitted_at=timezone.now()-timedelta(hours=74))
        with patch('orders.services.notify_wallet_update'):
            self.assertFalse(process_order(order.pk))
            self.provider_wallet.refresh_from_db()
            self.assertEqual(self.provider_wallet.balance, 0)
            self.ok(self.post(order, 'resolve', row))
            self.assertTrue(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
        self.provider_wallet.refresh_from_db()
        self.assertEqual(self.provider_wallet.balance, 800)
        self.assertEqual(order.transactions.filter(tx_type='INCOME').count(), 1)

    def test_unsettled_refund_prevents_all_later_settlement(self):
        order = self.order(age=73)
        row = self.aftersale(order)
        ProviderReport.objects.create(provider=self.provider, provider_account=self.accounts[self.provider.pk], order=order,
                                      game_name='本地样例', amount=1000, status='APPROVED', submitted_at=timezone.now()-timedelta(hours=74))
        self.ok(self.post(order, 'aftersale-refund', row))
        self.assertFalse(process_order(order.pk))
        self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())

    def test_all_refund_and_cancel_entrypoints_defer_one_wallet_and_order_update(self):
        from orders.tasks import auto_cancel_pending_order
        for action in ('refund', 'cancel', 'legacy-refund', 'customer-cancel', 'timeout'):
            with self.subTest(action=action):
                pending = action in ('cancel', 'customer-cancel', 'timeout')
                order = self.order(status='PENDING' if pending else 'GRABBED')
                self.customer_wallet.refresh_from_db()
                before = self.customer_wallet.balance
                if action == 'timeout':
                    Order.objects.filter(pk=order.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
                target = ('console.views.notify_order_update' if action in ('refund', 'cancel') else
                          'orders.notifier.notify_order_update' if action == 'timeout' else 'orders.views.notify_order_update')
                with patch(target) as notify, patch('orders.services.notify_wallet_update') as wallet_notify:
                    with transaction.atomic():
                        if action == 'timeout':
                            self.assertEqual(auto_cancel_pending_order.run(order.pk), 'cancelled')
                        elif action == 'customer-cancel':
                            self.ok(self.customer.post(f'/api/orders/orders/{order.pk}/cancel/', {'reason': '本地客户取消'}, format='json'))
                        else:
                            self.ok(self.post(order, action))
                        notify.assert_not_called()
                        wallet_notify.assert_not_called()
                    notify.assert_called_once()
                    wallet_notify.assert_called_once()
                self.customer_wallet.refresh_from_db()
                self.assertEqual(self.customer_wallet.balance, before+order.amount)
                self.assertEqual(order.transactions.filter(tx_type='REFUND').count(), 1)

    def test_wallet_addressing_failure_is_business_error_and_rolls_back(self):
        for action in ('refund', 'aftersale-refund', 'legacy-refund'):
            with self.subTest(action=action):
                order = self.order(status='GRABBED' if action == 'legacy-refund' else 'COMPLETED')
                row = self.aftersale(order)
                with patch('orders.services.get_wallet', side_effect=WalletAddressingError('本地异常钱包')):
                    self.rejected(self.post(order, action, row))
                order.refresh_from_db()
                row.refresh_from_db()
                self.assertEqual(order.payment_status, 'PAID')
                self.assertEqual(row.status, 'OPEN')
                self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())

    def test_one_microsecond_before_cutoff_remains_refundable(self):
        now = timezone.now()
        order = self.order()
        Order.objects.filter(pk=order.pk).update(completed_at=now-timedelta(hours=72)+timedelta(microseconds=1))
        with patch('django.utils.timezone.now', return_value=now):
            self.ok(self.post(order, 'refund'))
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
