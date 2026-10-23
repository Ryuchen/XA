"""The periodic confirmation path announces each actual committed order change."""
from datetime import timedelta
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation
from orders.models import AfterSale, Order
from orders.tests.factories import make_order, make_provider, make_service, make_user
from wallet.models import ProviderReport, Transaction
from wallet.services import get_wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=False)
class ConfirmationRealtimeTest(TransactionTestCase):
    def setUp(self):
        self.boss, self.provider = make_user(), make_provider(deposit_mode='CASH')
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        link_legacy_relations(self.boss, self.boss_account)
        link_legacy_relations(self.provider, self.provider_account)
        self.wallet = get_wallet(user=self.provider, account=self.provider_account)
        self.customer_wallet = get_wallet(user=self.boss, account=self.boss_account)
        self.service = make_service(price=1000)
        self.now = timezone.now()

    def order(self, *, report_age=74):
        order = make_order(
            self.boss, self.service, self.provider, customer_account=self.boss_account,
            provider_account=self.provider_account, amount=1000, provider_income=800,
            shop_income=200, commission_rate=20, status='COMPLETED', payment_status='PAID',
            completed_at=self.now-timedelta(hours=73), settlement_mode='REPORT_APPROVAL',
        )
        Transaction.objects.create(wallet=self.customer_wallet, order=order, tx_type='PAY', amount=-1000)
        ProviderReport.objects.create(provider=self.provider, provider_account=self.provider_account,
                                      order=order, amount=1000, game_name='本地确认样例', status='APPROVED',
                                      submitted_at=self.now-timedelta(hours=report_age))
        return order

    def test_settlement_then_later_confirmation_each_publish_once_after_commit(self):
        order = self.order(report_age=1)
        with patch('engagement.confirmation.notify_order_update') as notify:
            with transaction.atomic():
                self.assertFalse(process_order(order.pk))
                notify.assert_not_called()
            notify.assert_called_once()
            order.refresh_from_db()
            self.assertIsNotNone(order.settled_at)
            self.assertIsNone(order.consumption_confirmed_at)
            self.assertFalse(process_order(order.pk))
            notify.assert_called_once()
            with patch('django.utils.timezone.now', return_value=self.now+timedelta(hours=73)):
                self.assertTrue(process_order(order.pk))
                self.assertFalse(process_order(order.pk))
            self.assertEqual(notify.call_count, 2)
        self.assertEqual(order.transactions.filter(tx_type='INCOME').count(), 1)

    def test_both_changes_share_one_final_committed_order_event(self):
        order = self.order()
        def publish(changed):
            self.assertFalse(connection.in_atomic_block)
            saved = Order.objects.get(pk=changed.pk)
            self.assertIsNotNone(saved.settled_at)
            self.assertIsNotNone(saved.consumption_confirmed_at)
        with patch('engagement.confirmation.notify_order_update', side_effect=publish) as notify:
            self.assertTrue(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
            notify.assert_called_once()

    def test_gift_settlement_notifies_even_when_confirmation_is_skipped(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(is_gift_snapshot=True)
        with patch('engagement.confirmation.notify_order_update') as notify:
            self.assertFalse(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
            notify.assert_called_once()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)

    def test_existing_confirmation_row_with_missing_order_flag_still_notifies_once(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(settled_at=self.now-timedelta(hours=1))
        row = ConsumptionConfirmation.objects.create(order=order, account=self.boss_account,
                                                     business_cycle=order.business_cycle, amount=order.amount)
        with patch('engagement.confirmation.notify_order_update') as notify:
            self.assertFalse(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
            notify.assert_called_once()
        order.refresh_from_db()
        self.assertEqual(order.consumption_confirmed_at, row.confirmed_at)
        self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())

    def test_blocked_states_publish_nothing(self):
        for blocker in ('status', 'payment', 'account', 'cycle', 'report', 'aftersale', 'window'):
            with self.subTest(blocker=blocker):
                order = self.order()
                fields = {
                    'status': {'status': 'CANCELLED'}, 'payment': {'payment_status': 'REFUNDED'},
                    'account': {'customer_account': None}, 'cycle': {'business_cycle': order.business_cycle+1},
                    'window': {'completed_at': self.now-timedelta(hours=1)},
                }.get(blocker)
                if fields:
                    Order.objects.filter(pk=order.pk).update(**fields)
                elif blocker == 'report':
                    order.provider_reports.update(status='PENDING')
                else:
                    AfterSale.objects.create(order=order, account=self.boss_account, reason='本地未结客诉')
                with patch('engagement.confirmation.notify_order_update') as notify:
                    self.assertFalse(process_order(order.pk))
                    notify.assert_not_called()
                self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())

    def test_outer_rollback_discards_order_wallet_and_confirmation_changes(self):
        order = self.order()
        with patch('engagement.confirmation.notify_order_update') as notify:
            with self.assertRaisesRegex(RuntimeError, 'outer rollback'):
                with transaction.atomic():
                    self.assertTrue(process_order(order.pk))
                    notify.assert_not_called()
                    raise RuntimeError('outer rollback')
            notify.assert_not_called()
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (0, 0))
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())

    def test_late_benefit_failure_rolls_back_without_order_event(self):
        order = self.order()
        with patch('engagement.confirmation.notify_order_update') as notify:
            with patch('coupons.benefits.grant_periodic', side_effect=RuntimeError('benefit failure')):
                with self.assertRaisesRegex(RuntimeError, 'benefit failure'):
                    process_order(order.pk)
            notify.assert_not_called()
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())

    def test_channel_failure_does_not_fail_committed_processing(self):
        order = self.order()
        with patch('orders.notifier.get_channel_layer', side_effect=RuntimeError('channel offline')):
            with self.assertLogs('orders.notifier', level='WARNING'):
                self.assertTrue(process_order(order.pk))
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertIsNotNone(order.consumption_confirmed_at)
