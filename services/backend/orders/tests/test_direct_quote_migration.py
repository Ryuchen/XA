from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import IrreversibleError
from django.test import TransactionTestCase
from django.utils import timezone


from .migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class DirectQuoteMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_unknown_v1_and_missing_snapshot_preserve_payment_but_stop_dispatch(self):
        old = [('orders', '0061_directpaymentquote')]
        new = [('orders', '0062_directpaymentquote_conversion_rounding_and_more')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            user = apps.get_model('users', 'CustomUser').objects.create(username='fee-migration-user')
            account = apps.get_model('club_accounts', 'ClubAccount').objects.create(
                username='fee-migration-account', account_no='fee-migration-account', account_type='BOSS')
            service = apps.get_model('orders', 'ServiceItem').objects.create(name='fee-migration', price=10000)
            order_model = apps.get_model('orders', 'Order')
            intent_model = apps.get_model('orders', 'DirectPaymentIntent')
            quote_model = apps.get_model('orders', 'DirectPaymentQuote')
            outbox_model = apps.get_model('orders', 'DirectPaymentOutbox')
            ids = []
            for index, (quote_version, paid, service_started) in enumerate(((None, True, False), ('FEE_QUOTE_V1', True, True),
                    (None, False, False), ('FEE_QUOTE_V1', False, False), ('FEE_QUOTE_V2', True, False))):
                order = order_model.objects.create(customer_id=user.pk, customer_account_id=account.pk,
                    service_id=service.pk, amount=10000, order_no=f'fee-migration-{index}',
                    status='IN_SERVICE' if service_started else 'PENDING', payment_status='PAID' if paid else 'UNPAID')
                intent = intent_model.objects.create(order_id=order.pk, account_id=account.pk,
                    client_request_id=f'fee-migration-{index}', request_fingerprint='a'*64,
                    merchant_order_no=f'fee-migration-{index}', appid='wx', mchid='mch', openid='open',
                    amount_fen=10000, status='PAID' if paid else 'AWAITING',
                    transaction_id=f'transaction-{index}' if paid else None, paid_at=timezone.now() if paid else None,
                    payer_total_fen=10000 if paid else None, payer_currency='CNY' if paid else None)
                if quote_version:
                    quote_model.objects.create(intent_id=intent.pk, contract_version=quote_version, policy_id='old', service_fen=10000,
                        fee_fen=0, total_fen=10000, rate_numerator=0, rate_denominator=1,
                        fee_basis='SERVICE_AMOUNT', rounding='DOWN', quote_hash='b'*64)
                if paid:
                    outbox_model.objects.create(intent_id=intent.pk, pool_entered_at=timezone.now(), status='DONE')
                ids.append((intent.pk, order.pk))
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            apps = executor.loader.project_state(new).apps
            intents = apps.get_model('orders', 'DirectPaymentIntent')
            orders = apps.get_model('orders', 'Order')
            outboxes = apps.get_model('orders', 'DirectPaymentOutbox')
            for index, (intent_id, order_id) in enumerate(ids):
                intent = intents.objects.get(pk=intent_id)
                order = orders.objects.get(pk=order_id)
                if index < 2 or index == 4:
                    self.assertEqual((intent.status, intent.review_reason), ('REVIEW', 'QUOTE_CONVERSION_UNVERIFIED'))
                    self.assertEqual(order.payment_status, 'PAID')
                    self.assertEqual(order.status, 'IN_SERVICE' if index == 1 else 'PENDING')
                    self.assertEqual(outboxes.objects.get(intent_id=intent_id).status, 'REVIEW')
                else:
                    self.assertEqual((intent.status, order.payment_status), ('AWAITING', 'UNPAID'))
            v1 = apps.get_model('orders', 'DirectPaymentQuote').objects.get(intent_id=ids[1][0])
            self.assertEqual(v1.contract_version, 'FEE_QUOTE_V1')
            self.assertIsNone(v1.service_units)
            self.assertEqual(v1.conversion_version, '')
            self.assertEqual(apps.get_model('orders', 'DirectPaymentQuote').objects.get(intent_id=ids[4][0]).contract_version,
                             'PRE62_V2_UNKNOWN')
            quote = apps.get_model('orders', 'DirectPaymentQuote').objects.create(intent_id=ids[2][0],
                contract_version='FEE_QUOTE_V2', policy_id='verified', service_units=10000,
                conversion_version='TEST_CURRENT_100_V1', units_per_yuan=100, fen_per_yuan=100,
                conversion_rounding='EXACT', service_fen=10000, fee_fen=0, total_fen=10000,
                rate_numerator=0, rate_denominator=1, fee_basis='SERVICE_AMOUNT', rounding='DOWN', quote_hash='c'*64)
            with self.assertRaises(IrreversibleError):
                MigrationExecutor(connection).migrate(old)
            retained = apps.get_model('orders', 'DirectPaymentQuote').objects.get(pk=quote.pk)
            self.assertEqual((retained.service_units, retained.conversion_version, retained.units_per_yuan),
                             (10000, 'TEST_CURRENT_100_V1', 100))
        finally:
            MigrationExecutor(connection).migrate(new)
