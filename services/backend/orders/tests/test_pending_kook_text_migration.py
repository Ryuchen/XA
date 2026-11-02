"""Repair only pending KOOK cards that match the default template."""
from importlib import import_module
import json
from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class PendingKookTextMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_pending_default_card_changes_but_custom_and_sent_cards_remain(self):
        old = [('orders', '0079_fix_pending_wecom_settlement_text'), ('console', '0017_update_default_dispatch_settlement_text')]
        new = [('orders', '0080_fix_pending_default_kook_text')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        User = apps.get_model('users', 'CustomUser')
        Service = apps.get_model('orders', 'ServiceItem')
        Order = apps.get_model('orders', 'Order')
        Record = apps.get_model('orders', 'KookDispatchRecord')
        Config = apps.get_model('console', 'KookDispatchConfig')
        migration = import_module('orders.migrations.0080_fix_pending_default_kook_text')
        old_template = import_module('console.migrations.0017_update_default_dispatch_settlement_text').OLD
        from orders.dispatch_templates import render_template
        from orders.kook_dispatch import build_card
        from orders.models import Order as CurrentOrder
        Config.objects.create(id=1, routes=[{'id': 'default', 'template': migration.DEFAULT_TEMPLATE},
                                            {'id': 'custom', 'template': '自定义'}])
        boss = User.objects.create(username='kook-migration-boss', boss_no='KOOK-HIST')
        service = Service.objects.create(name='测试服务', price=10000)
        ids = []
        for index, (status, route, custom) in enumerate((('PENDING', 'default', False), ('SENT', 'default', False),
                                                         ('PENDING', 'custom', False), ('PENDING', 'default', True))):
            order = Order.objects.create(customer_id=boss.pk, service_id=service.pk,
                                         order_no=f'kook-migration-{index}', amount=10000)
            current_order = CurrentOrder.objects.select_related('service').get(pk=order.pk)
            text = render_template(current_order, 'NEW_ORDER', old_template)
            body = text + ('\n自定义' if custom else '')
            card = build_card(body, role_ids=['123'], provider_order_url='https://example.test/provider',
                              order_no=current_order.order_no)
            ids.append(Record.objects.create(order_id=order.pk, trigger='NEW_ORDER', channel_id='123', status=status,
                                              route_key=route, content=body, card_content=card).pk)
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        Record = executor.loader.project_state(new).apps.get_model('orders', 'KookDispatchRecord')
        updated = Record.objects.get(pk=ids[0])
        self.assertIn(migration.NEW_SENTENCE, updated.content)
        modules = json.loads(updated.card_content)[0]['modules']
        self.assertEqual(next(module['text']['content'] for module in modules
                              if module.get('text', {}).get('type') == 'plain-text'), updated.content)
        self.assertEqual(modules[0]['text']['content'], '(rol)123(rol)')
        self.assertEqual(modules[-1]['elements'][0]['value'], 'https://example.test/provider')
        for pk in ids[1:]:
            self.assertIn(migration.OLD_SENTENCE, Record.objects.get(pk=pk).content)
