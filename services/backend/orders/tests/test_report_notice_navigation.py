"""Real report review notices carry a usable destination for the provider."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from orders.tests import test_provider_aftersale_status as flow
from site_messages.models import Message
from wallet.models import ProviderReport


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ReportNoticeNavigationTest(APITransactionTestCase):
    setUp = flow.ProviderAfterSaleStatusTest.setUp
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok

    def test_rejection_resubmission_and_approval_have_reachable_report(self):
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'report-notice-navigation',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))

        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/reject/', {'audit_remark': '请补充清晰截图'}, format='json'))
        message = Message.objects.get(recipient_account=self.accounts[self.provider.pk], title='报单审核未通过', related_order_id=order_id)
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/messages/{message.pk}/'))
        self.assertEqual(detail['action_url'], '/pages/report/index')
        self.assertEqual(detail['related_order_id'], order_id)
        self.assertIn('请补充清晰截图', detail['detail'])
        self.assertTrue(any(item['id'] == report.pk and item['status'] == 'REJECTED' and item['can_edit']
                            for item in self.ok(self.client.get('/api/wallet/reports/'))))
        node = shutil.which('node')
        if node:
            repo = Path(settings.BASE_DIR).parent.parent
            env = {**os.environ, 'XA_REPORT_NOTICE': json.dumps(detail, ensure_ascii=False)}
            result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/message-order-navigation.cjs')],
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=30, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        self.login(self.provider)
        approved = Message.objects.get(recipient_account=self.accounts[self.provider.pk], title='报单审核通过', related_order_id=order_id)
        detail = self.ok(self.client.get(f'/api/messages/{approved.pk}/'))
        self.assertEqual(detail['action_url'], '/pages/report/index')
        self.assertIn('审核通过', detail['title'])
