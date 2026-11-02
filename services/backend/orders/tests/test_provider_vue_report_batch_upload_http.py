"""Real multi-image uploads report limits and partial failures accurately."""
import json
import os
import subprocess
from pathlib import Path

from PIL import Image
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from wallet.models import ProviderReport


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_RECHARGE_PROOF=True, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueReportBatchUploadHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order

    def exercise(self, scenario):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        pictures = []
        for index in range(10):
            name = Path(self.media.name) / f'result-{index}.png'
            Image.new('RGB', (8, 8), (index * 20, 10, 20)).save(name)
            pictures.append(str(name))
        invalid = Path(self.media.name) / 'invalid.png'
        invalid.write_bytes(b'not an image')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-report-batch-upload-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'orderId': order.pk,
                'orderNo': order.order_no, 'reportId': report.pk, 'pictures': pictures, 'invalid': str(invalid),
                'id': self.accounts[self.provider.pk].pk, 'token': issue_account_tokens(self.accounts[self.provider.pk])[0],
                'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
                'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
                'scenario': scenario, 'fixed': os.environ.get('XA_BATCH_UPLOAD_FIXED') == '1'}),
            text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        self.assertEqual(report.result_images.count(), 9 if scenario == 'limit' else 3 if scenario in ('lost', 'partial') else 2 if os.environ.get('XA_BATCH_UPLOAD_FIXED') == '1' else 1)

    def test_ten_selected_images_respect_nine_image_limit(self):
        self.exercise('limit')

    def test_partial_invalid_file_can_be_corrected(self):
        self.exercise('invalid')

    def test_lost_response_is_not_treated_as_definite_failure(self):
        if os.environ.get('XA_BATCH_UPLOAD_FIXED') != '1':
            self.skipTest('requires updated upload feedback')
        self.exercise('lost')

    def test_truncated_response_remains_uncertain(self):
        if os.environ.get('XA_BATCH_UPLOAD_FIXED') != '1':
            self.skipTest('requires updated upload feedback')
        self.exercise('partial')
