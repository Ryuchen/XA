"""Correct unsent generated dispatch text while preserving delivery history."""
from importlib import import_module
from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class PendingWecomTextMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_only_pending_generated_messages_change(self):
        old = [('orders', '0078_alter_evaluation_order_and_more')]
        new = [('orders', '0079_fix_pending_wecom_settlement_text')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        User = apps.get_model('users', 'CustomUser')
        Service = apps.get_model('orders', 'ServiceItem')
        Order = apps.get_model('orders', 'Order')
        Record = apps.get_model('orders', 'WecomDispatchRecord')
        migration = import_module('orders.migrations.0079_fix_pending_wecom_settlement_text')
        boss = User.objects.create(username='wecom-migration-boss', boss_no='WECOM-HIST')
        service = Service.objects.create(name='测试服务', price=10000)
        records = []
        for index, status in enumerate(('PENDING', 'SENT', 'SENDING', 'UNKNOWN')):
            order = Order.objects.create(customer_id=boss.pk, service_id=service.pk,
                                         order_no=f'wecom-migration-{index}', amount=10000)
            records.append(Record.objects.create(order_id=order.pk, trigger='NEW_ORDER', channel_id='test',
                                                 status=status, content='订单内容' + migration.OLD).pk)
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        Record = executor.loader.project_state(new).apps.get_model('orders', 'WecomDispatchRecord')
        for index, pk in enumerate(records):
            self.assertEqual(Record.objects.get(pk=pk).content,
                             '订单内容' + (migration.NEW if index == 0 else migration.OLD))
