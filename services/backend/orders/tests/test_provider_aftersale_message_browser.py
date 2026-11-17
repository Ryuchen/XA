"""A provider's resolved aftersale notice links to its order and report."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from orders.models import Order
from orders.tests import test_provider_aftersale_status as flow


@skipUnless(os.environ.get('XA_AFTERSALE_PROVIDER_BROWSER') == '1', 'Requires isolated H5 build and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ProviderAfterSaleMessageBrowserTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    open_aftersale = flow.ProviderAfterSaleStatusTest.open_aftersale
    resolve = flow.ProviderAfterSaleStatusTest.resolve

    def test_notice_opens_resolved_order_at_375(self):
        order_id, report_id = self.prepare()
        ticket = self.open_aftersale(order_id)
        self.resolve(ticket)
        self.login(self.provider)
        inbox = self.ok(self.client.get('/api/messages/'))['messages']
        notice = next(row for row in inbox if row['related_order_id'] == order_id and row['title'] == '订单售后已处理')
        self.assertTrue(notice['id'])
        order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(order['order_no'], Order.objects.get(pk=order_id).order_no)
        self.assertEqual(order['aftersale_status'], 'RESOLVED')
        report = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report_id)
        self.assertEqual(report['aftersale_status'], 'RESOLVED')
