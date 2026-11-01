"""A catalog import cannot reclassify a completed service or gift order."""
import io
import json
import subprocess
from pathlib import Path

from django.core.management import call_command
from django.test import override_settings, tag
from rest_framework.test import APITestCase

from orders.management.commands.seed_club_catalog import CATALOG
from orders.models import Order
from orders.tests.factories import make_service, make_service_category
from orders.tests import test_release_flow as release_flow


@override_settings(DEBUG=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False)
class ReportSaleTypeWorkflowTest(APITestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge
    new_order = release_flow.ReleaseFlowTest.new_order
    finish = release_flow.ReleaseFlowTest.finish
    settle_after_window = release_flow.ReleaseFlowTest.settle_after_window

    @tag('catalog_verify')
    def test_report_and_gift_history_use_sale_type_after_catalog_reimport(self):
        catalog = json.loads(CATALOG.read_text(encoding='utf-8'))['items']
        self.service.catalog_key = next(item['catalog_key'] for item in catalog if item['is_gift'])
        self.service.save(update_fields=['catalog_key'])
        gift = make_service(name='本地礼物', price=2100,
                            service_category=make_service_category(is_gift=True),
                            catalog_key=next(item['catalog_key'] for item in catalog if not item['is_gift']))
        order = self.new_order()
        self.login(self.boss)
        gift_result = self.ok(self.client.post('/api/orders/gifts/send/', {
            'gift_service_id': gift.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'quantity': 1, 'expected_amount': 2100, 'client_request_id': 'report-snapshot-gift',
        }, format='json'))
        gift_order = Order.objects.get(pk=gift_result['id'])
        self.assertFalse(order.is_gift_snapshot)
        self.assertTrue(gift_order.is_gift_snapshot)

        call_command('seed_club_catalog', apply=True, stdout=io.StringIO())
        self.service.refresh_from_db()
        gift.refresh_from_db()
        self.assertTrue(self.service.service_category.is_gift)
        self.assertFalse(gift.service_category.is_gift)

        # Use the actual boss order responses and native presentation function:
        # the directory's new type must not rewrite either completed purchase.
        self.login(self.boss)
        boss_service = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        boss_gift = self.ok(self.client.get(f'/api/orders/orders/{gift_order.pk}/'))
        native = Path(__file__).resolve().parents[4] / 'apps' / 'boss-miniapp' / 'native' / 'core' / 'orders.js'
        result = subprocess.run(['node', '-e',
            "const {present}=require(process.argv[1]); let s=''; process.stdin.on('data',x=>s+=x); "
            "process.stdin.on('end',()=>console.log(JSON.stringify(JSON.parse(s).map(x=>{const y=present(x);return {gift:y.isGift,status:y.statusText}}))));",
            str(native)], input=json.dumps([boss_service, boss_gift], default=str), text=True,
            capture_output=True, encoding='utf-8', timeout=15, check=True)
        display = json.loads(result.stdout)
        self.assertEqual(display, [{'gift': False, 'status': '已接单'}, {'gift': True, 'status': '礼物已送达'}])

        self.login(self.provider)
        service_detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        gift_detail = self.ok(self.client.get(f'/api/orders/orders/{gift_order.pk}/'))
        self.assertFalse(service_detail['is_gift_snapshot'])
        self.assertTrue(service_detail['service']['is_gift'])
        self.assertEqual(service_detail['report_entry'], {'available': True, 'reason': ''})
        self.assertTrue(gift_detail['is_gift_snapshot'])
        self.assertFalse(gift_detail['service']['is_gift'])
        self.assertEqual(gift_detail['report_entry']['available'], False)
        self.assertEqual(self.client.post('/api/wallet/reports/', {'order_id': gift_order.pk}).data['code'], 400)
        draft = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}))

        report = self.finish(order)
        self.assertEqual(report.pk, draft['id'])
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}))
        self.settle_after_window()
        self.login(self.provider)
        history = self.ok(self.client.get('/api/wallet/provider-benefits/'))
        self.assertEqual(history['summary']['gift_count'], 1)
        self.assertEqual([item['id'] for item in history['gift_records']], [gift_order.pk])
        self.assertEqual(history['summary']['total_gift_amount'], 2100)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
