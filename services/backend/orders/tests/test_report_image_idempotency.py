"""Real image upload retries are distinct from deliberate repeated uploads."""
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from wallet.models import ProviderReport


def picture(color='white'):
    data = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(data, format='PNG')
    return SimpleUploadedFile('result.png', data.getvalue(), content_type='image/png')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_RECHARGE_PROOF=True, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ReportImageIdempotencyTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order

    def prepare(self):
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': flow.screenshot()}, format='multipart'))
        return ProviderReport.objects.get(order=order, provider=self.provider)

    def test_replay_conflict_delete_and_legacy_clients(self):
        report = self.prepare()
        url = f'/api/wallet/reports/{report.pk}/images/'
        def upload(key=None, color='white'):
            data = {'kind': 'RESULT', 'image': picture(color)}
            if key is not None:
                data['client_request_id'] = key
            return self.client.post(url, data, format='multipart')
        first = self.ok(upload('image-one'))
        first_id = first['result_images'][0]['id']
        self.ok(upload('image-one'))
        self.assertEqual(report.result_images.count(), 1)
        conflict = upload('image-one', 'red')
        self.assertEqual(conflict.status_code, 409)
        self.ok(upload('image-two'))
        self.assertEqual(report.result_images.count(), 2, 'a different request may intentionally upload identical pixels')
        self.ok(self.client.delete(url + f'{first_id}/'))
        deleted = upload('image-one')
        self.assertEqual(deleted.status_code, 409)
        self.assertIn('已删除', deleted.data['msg'])
        self.assertEqual(report.result_images.count(), 1)
        self.ok(upload())
        self.ok(upload())
        self.assertEqual(report.result_images.count(), 3, 'legacy clients retain their previous behavior')
        for index in range(6):
            self.ok(upload(f'image-fill-{index}'))
        self.ok(upload('image-two'))
        self.assertEqual(report.result_images.count(), 9, 'replay must succeed even when the report is full')
        self.assertNotEqual(upload('image-over-limit').data['code'], 0)

    def test_concurrent_same_request(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL row locks')
        report = self.prepare()
        token = issue_account_tokens(self.accounts[self.provider.pk])[0]
        barrier = Barrier(2)
        def send():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                response = client.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind': 'RESULT', 'image': picture(), 'client_request_id': 'image-concurrent'}, format='multipart')
                return response.status_code, response.data['code']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual(results, [(200, 0), (200, 0)])
        self.assertEqual(report.result_images.count(), 1)
