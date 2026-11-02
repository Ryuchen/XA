"""Concurrent customer clients submitting different aftersale descriptions."""
import json
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.test import skipUnlessDBFeature
from rest_framework.test import APIClient, APITransactionTestCase

from orders.models import AfterSale
from orders.tests import test_provider_aftersale_status as flow


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeAfterSaleConcurrentHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare

    def test_two_customer_clients_submit_different_descriptions(self):
        order_id, _ = self.prepare()
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/native-aftersale-concurrent-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': order_id,
                'staffToken': self.tokens[self.staff.pk],
                'session': {'token': self.tokens[self.boss.pk], 'user': {
                    'id': str(self.accounts[self.boss.pk].pk), 'role': 'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sale = AfterSale.objects.get(order_id=order_id)
        self.assertEqual((sale.status, sale.reason), ('OPEN', '第二设备填写的新说明'))
        self.assertEqual([item['status'] for item in sale.history], ['OPEN', 'RESOLVED', 'OPEN'])
        self.login(self.staff)
        data = self.ok(self.client.get('/api/admin/aftersales/', {'order': order_id}))
        self.assertEqual(data['list'][0]['reason'], '第二设备填写的新说明')
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(detail['aftersale_status'], 'OPEN')

    def test_same_request_replays_only_the_same_description(self):
        order_id, _ = self.prepare()
        self.login(self.boss)
        url = f'/api/orders/orders/{order_id}/aftersale/'
        first = {'reason': '第一笔说明', 'client_request_id': 'same-case-intent'}
        opened = self.ok(self.client.post(url, first, format='json'))
        self.assertEqual(self.ok(self.client.post(url, first, format='json'))['id'], opened['id'])
        changed = self.client.post(url, dict(first, reason='不同的说明'), format='json')
        self.assertEqual((changed.status_code, changed.data['errorCode']), (409, 'AFTERSALE_REQUEST_CONFLICT'))
        concurrent = self.client.post(url, {'reason': '另一设备的新说明', 'client_request_id': 'other-case-intent'}, format='json')
        self.assertEqual((concurrent.status_code, concurrent.data['errorCode']), (409, 'AFTERSALE_ALREADY_OPEN'))
        sale = AfterSale.objects.get(order_id=order_id)
        self.assertEqual((sale.status, sale.reason, len(sale.history)), ('OPEN', '第一笔说明', 1))
        self.login(self.staff)
        self.ok(self.client.post(f"/api/admin/aftersales/{opened['id']}/resolve/", {'resolution': '已处理第一笔'}, format='json'))
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.post(url, first, format='json'))['status'], 'RESOLVED')
        again = self.ok(self.client.post(url, {'reason': '另一设备的新说明', 'client_request_id': 'other-case-intent'}, format='json'))
        self.assertEqual((again['status'], again['reason']), ('OPEN', '另一设备的新说明'))


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AfterSaleConcurrentLockTest(APITransactionTestCase):
    setUp = flow.ProviderAfterSaleStatusTest.setUp
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_different_intents_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        order_id, _ = self.prepare()
        barrier = Barrier(2)
        token = self.tokens[self.boss.pk]
        def post(index):
            close_old_connections()
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            barrier.wait(timeout=10)
            response = client.post(f'/api/orders/orders/{order_id}/aftersale/', {
                'reason': f'隔离并发说明{index}', 'client_request_id': f'parallel-aftersale-{index}'}, format='json')
            result = (response.status_code, response.data.get('code'), response.data.get('errorCode'))
            close_old_connections()
            return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(post, (1, 2)))
        self.assertEqual(sorted(result[0] for result in results), [200, 409], results)
        self.assertEqual([result[2] for result in results if result[0] == 409], ['AFTERSALE_ALREADY_OPEN'])
        sale = AfterSale.objects.get(order_id=order_id)
        self.assertIn(sale.reason, ('隔离并发说明1', '隔离并发说明2'))
        self.assertEqual(len(sale.history), 1)
        self.login(self.staff)
        data = self.ok(self.client.get('/api/admin/aftersales/', {'order': order_id}))
        self.assertEqual(data['list'][0]['reason'], sale.reason)
        self.login(self.provider)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['aftersale_status'], 'OPEN')
