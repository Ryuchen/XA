"""Opt-in 375px browser review against isolated, real report APIs."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests import test_provider_aftersale_status as flow
from orders.tests.factories import make_console_user, make_provider
from wallet.models import ProviderReport


@skipUnless(os.environ.get('XA_ADMIN_REPORT_375') == '1', 'Requires local browser and isolated database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminReportMobile375Test(LiveServerTestCase):
    def setUp(self):
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok

    def test_staff_distinguishes_transferred_reports(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        incoming = make_provider(display_name='隔离接替陪玩')
        second_staff = make_console_user(['report:view', 'report:audit'])
        second_account = get_or_create_account_for_legacy_user(second_staff)
        second_staff.admin_membership.account = second_account
        second_staff.admin_membership.save(update_fields=['account'])
        second_token = issue_account_tokens(second_account)[0]
        account = get_or_create_account_for_legacy_user(incoming)
        incoming.escort_profile.account = account
        incoming.escort_profile.save(update_fields=['account'])
        self.accounts[incoming.pk] = account
        self.tokens[incoming.pk] = issue_account_tokens(account)[0]
        role = self.staff.admin_membership.roles.first()
        role.permissions = list(set(role.permissions) | {'order:dispatch'})
        role.save(update_fields=['permissions'])
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'client_request_id': 'admin-transfer-report-mobile'}, format='json'))
        order_id = order['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {'transfers': [{
            'old_provider_id': self.accounts[self.provider.pk].pk, 'new_provider_id': account.pk,
            'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0,
        }]}, format='json'))
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/complete/', {}, format='json'))
        entries = []
        for provider in (self.provider, incoming):
            self.login(provider)
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
            for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
                if kind == 'ENTRY' and provider == self.provider:
                    continue
                self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': kind, 'image': flow.image()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/", {}, format='json'))
            entries.append(report['id'])
        self.login(self.staff)
        rows = self.ok(self.client.get('/api/admin/reports/'))['list']
        by_id = {row['id']: row for row in rows}
        repo = Path(settings.BASE_DIR).parent.parent
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI server did not start')
            result = subprocess.run([node, str(repo / 'scripts/admin-report-transfer-375-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'token': self.tokens[self.staff.pk], 'secondToken': second_token, 'orderNo': order['order_no'],
                    'reports': [{'id': pk, 'name': by_id[pk]['provider_name'], 'income': f"{by_id[pk]['expected_income'] // 10} 币"} for pk in entries]}),
                text=True, encoding='utf-8', capture_output=True, timeout=120, cwd=repo)
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(ProviderReport.objects.filter(pk__in=entries).order_by('pk').values_list('status', flat=True)), ['APPROVED', 'PENDING'])

    def test_staff_reviews_and_rejects_real_report(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'admin-report-mobile-375',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-report-mobile-375-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': self.tokens[self.staff.pk], 'reportId': report.pk}),
            text=True, encoding='utf-8', capture_output=True, timeout=120, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report.refresh_from_db()
        self.assertEqual(report.status, ProviderReport.Status.REJECTED)
        self.assertEqual(report.audit_remark, '请补充清晰战绩截图')
        self.login(self.provider)
        reports = self.ok(self.client.get('/api/wallet/reports/'))
        provider_view = next(row for row in reports if row['id'] == report.pk)
        self.assertEqual((provider_view['status'], provider_view['audit_remark']),
                         ('REJECTED', '请补充清晰战绩截图'))
        self.login(self.boss)
        boss_order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertIsNone(boss_order['consumption_ready_at'])
        result = subprocess.run([node, str(repo / 'scripts/provider-report-time-375.cjs')],
            input=json.dumps({'base': self.live_server_url, 'providerToken': self.tokens[self.provider.pk],
                'providerId': str(self.accounts[self.provider.pk].pk), 'orderNo': boss_order['order_no'],
                'reportId': report.pk, 'resubmit': True}), text=True, encoding='utf-8',
            capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.login(self.staff)
        pending = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual((pending['status'], len(pending['result_image_urls']), pending['audit_remark']), ('PENDING', 2, ''))
        self.assertEqual(self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))['status'], 'APPROVED')
        self.login(self.boss)
        boss_order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertIsNotNone(boss_order['consumption_ready_at'])
