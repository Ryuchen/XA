"""A real review notice and new report keep their correct browser location."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from orders.tests import test_provider_aftersale_status as flow
from site_messages.models import Message
from wallet.models import ProviderReport


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueReportNavigationHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok

    def exercise(self, leave_early=''):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        self.login(self.boss)
        first = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'report-navigation-a'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{first['id']}/start/", {'entry_image':flow.image()}, format='multipart'))
        self.ok(self.client.post(f"/api/orders/orders/{first['id']}/complete/", {'completion_image':flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=first['id'], provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/reject/', {'audit_remark':'请补充清晰截图'}, format='json'))
        notice = Message.objects.get(recipient_account=self.accounts[self.provider.pk], related_order_id=first['id'], title='报单审核未通过')
        self.login(self.boss)
        second = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'report-navigation-b'}, format='json'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-report-navigation-375.cjs')],
            input=json.dumps({'root':root, 'base':self.live_server_url, 'first':first, 'second':second,
                'messageId':notice.pk, 'id':self.accounts[self.provider.pk].pk,
                'token':self.tokens[self.provider.pk], 'fixed':os.environ.get('XA_REPORT_ROUTE_FIXED') == '1',
                'leaveEarly':leave_early}), text=True, encoding='utf-8', capture_output=True, timeout=50, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        self.assertTrue(ProviderReport.objects.filter(order_id=second['id'], provider=self.provider, status='DRAFT').exists())
        report.refresh_from_db()
        self.assertEqual(report.status, 'REJECTED')
        for user in (self.boss, self.provider):
            self.login(user)
            self.assertEqual(self.ok(self.client.get(f"/api/orders/orders/{second['id']}/"))['status'], 'GRABBED')
        self.login(self.staff)
        self.assertEqual(self.ok(self.client.get(f"/api/admin/orders/{second['id']}/"))['status'], 'GRABBED')

    def test_notification_to_new_report_and_reload(self):
        self.exercise()

    def test_leaving_while_new_report_is_pending(self):
        if os.environ.get('XA_REPORT_ROUTE_FIXED') != '1':
            self.skipTest('requires updated report navigation')
        self.exercise(leave_early='leave')

    def test_changing_report_route_while_creation_is_pending(self):
        if os.environ.get('XA_REPORT_ROUTE_FIXED') != '1':
            self.skipTest('requires updated report navigation')
        self.exercise(leave_early='same')
