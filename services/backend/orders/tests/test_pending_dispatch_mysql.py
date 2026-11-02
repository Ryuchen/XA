"""Exercise dispatch snapshot migrations against real MySQL row locks."""
from importlib import import_module
import json
from unittest import skipUnless

from django.apps import apps
from django.db import connection, transaction
from django.test import TransactionTestCase

from console.models import KookDispatchConfig
from orders.dispatch_templates import render_template
from orders.kook_dispatch import build_card
from orders.models import KookDispatchRecord, WecomDispatchRecord
from orders.tests.factories import make_order, make_service, make_user


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL')
class PendingDispatchMySQLTest(TransactionTestCase):
    def test_pending_snapshots_change_and_sent_history_stays_intact(self):
        kook = import_module('orders.migrations.0080_fix_pending_default_kook_text')
        wecom = import_module('orders.migrations.0079_fix_pending_wecom_settlement_text')
        old_template = import_module('console.migrations.0017_update_default_dispatch_settlement_text').OLD
        KookDispatchConfig.objects.create(pk=1, routes=[{'id': 'default', 'template': kook.DEFAULT_TEMPLATE}])
        boss = make_user()
        service = make_service(price=10000)
        for sequence, status in enumerate(('PENDING', 'SENT')):
            order = make_order(boss, service=service, amount=10000)
            text = render_template(order, 'NEW_ORDER', old_template)
            card = build_card(text, role_ids=['123'], provider_order_url='https://example.test/provider')
            KookDispatchRecord.objects.create(order=order, trigger='NEW_ORDER', channel_id='123',
                route_key='default', status=status, content=text, card_content=card)
            WecomDispatchRecord.objects.create(order=order, trigger='NEW_ORDER', channel_id='fingerprint',
                status=status, content='订单内容' + wecom.OLD)
        with transaction.atomic():
            wecom.update_pending_wecom(apps, None)
            kook.repair(apps, None)
        pending = KookDispatchRecord.objects.get(status='PENDING')
        sent = KookDispatchRecord.objects.get(status='SENT')
        self.assertIn(kook.NEW_SENTENCE, pending.content)
        self.assertIn(kook.OLD_SENTENCE, sent.content)
        modules = json.loads(pending.card_content)[0]['modules']
        self.assertEqual(next(module['text']['content'] for module in modules
                              if module.get('text', {}).get('type') == 'plain-text'), pending.content)
        self.assertEqual(modules[-1]['elements'][0]['value'], 'https://example.test/provider')
        self.assertTrue(WecomDispatchRecord.objects.get(status='PENDING').content.endswith(wecom.NEW))
        self.assertTrue(WecomDispatchRecord.objects.get(status='SENT').content.endswith(wecom.OLD))
