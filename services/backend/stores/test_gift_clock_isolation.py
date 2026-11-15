"""Store-local clock configuration is snapshotted independently on gifts."""
import json

from django.test import TestCase

from orders.tests.factories import make_order, make_service, make_service_category, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig
from wallet.settlement_clocks import CONFIG_KEY, current_rules


class GiftClockIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_store_configuration_and_order_snapshots_do_not_cross(self):
        stores = (resolve_store(), xinan_store())
        rules = ((12, 24), (48, 72))
        identifiers = []
        for store, (aftersale, freeze) in zip(stores, rules):
            with store_context(store):
                SystemConfig.objects.create(key=CONFIG_KEY, value=json.dumps({
                    'aftersale_hours': aftersale, 'income_freeze_hours': freeze, 'version': 1,
                }))
                service = make_service(service_category=make_service_category(is_gift=True))
                order = make_order(make_user(), service=service)
                identifiers.append(order.pk)
                self.assertEqual((order.gift_aftersale_hours, order.gift_freeze_hours), (aftersale, freeze))
        self.assertEqual(identifiers[0], identifiers[1])
        for store, expected in zip(stores, rules):
            with store_context(store):
                config = current_rules()
                self.assertEqual((config['aftersale_hours'], config['income_freeze_hours']), expected)
