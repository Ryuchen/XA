"""A stale provider page consumes real report APIs after another device resubmits."""
import base64
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from orders.models import Order
from orders.tests import test_provider_aftersale_status as flow
from wallet.models import ProviderReport, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ReportStaleFrontendHTTPTest(LiveServerTestCase):
    def setUp(self):
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok

    def test_report_creation_submission_and_image_deletion_lost_responses(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        self.login(self.boss)
        second = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'report-actions-lost-2',
        }, format='json'))
        self.login(self.provider)
        second_id = second['id']
        self.ok(self.client.post(f'/api/orders/orders/{second_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{second_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=second_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.login(self.boss)
        first = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'report-actions-lost-1',
        }, format='json'))
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_REPORT_ACTIONS_LOST_HTTP': json.dumps({
            'base': self.live_server_url, 'token': self.tokens[self.provider.pk],
            'firstOrderId': first['id'], 'firstOrderNo': first['order_no'],
            'secondOrderId': second_id, 'image': base64.b64encode(flow.image().read()).decode('ascii'),
        })}
        result = subprocess.run([node, '--test', '--test-name-pattern=real report actions recover',
                                 str(repo / 'apps/provider-miniapp/tests/report-flow.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([node, '--test', '--test-name-pattern=real order report entry opens report after a lost response',
                                 str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first_report = ProviderReport.objects.get(order_id=first['id'], provider=self.provider)
        self.assertEqual(first_report.result_images.count(), 0)
        report.refresh_from_db()
        self.assertEqual(report.status, ProviderReport.Status.PENDING)
        self.login(self.staff)
        pending = self.ok(self.client.get('/api/admin/reports/', {'status': 'PENDING'}))
        self.assertIn(report.pk, [row['id'] for row in pending['list']])
        self.login(self.boss)
        boss_order = self.ok(self.client.get(f'/api/orders/orders/{second_id}/'))
        self.assertIsNotNone(boss_order['report_ready_at'])
        self.assertIsNotNone(boss_order['consumption_ready_at'])
        self.login(self.provider)
        provider_reports = self.ok(self.client.get('/api/wallet/reports/'))
        self.assertEqual(next(row for row in provider_reports if row['id'] == report.pk)['status'], 'PENDING')
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/reject/', {'audit_remark': '请补充清晰战绩截图'}, format='json'))
        self.login(self.boss)
        self.assertIsNone(self.ok(self.client.get(f'/api/orders/orders/{second_id}/'))['consumption_ready_at'])
        self.login(self.provider)
        rejected = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)
        self.assertEqual((rejected['status'], rejected['audit_remark']), ('REJECTED', '请补充清晰战绩截图'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        self.login(self.provider)
        approved = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)
        self.assertEqual((approved['status'], approved['settlement_status']), ('APPROVED', 'WAITING'))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.login(self.boss)
        self.assertIsNotNone(self.ok(self.client.get(f'/api/orders/orders/{second_id}/'))['consumption_ready_at'])

    def test_real_multi_order_report_focus_and_account_change(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_provider

        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        orders = []
        for number in (1, 2):
            self.login(self.boss)
            order = self.ok(self.client.post('/api/orders/create/', {
                'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
                'client_request_id': f'report-focus-real-{number}',
            }, format='json'))
            orders.append(order)
            self.login(self.provider)
            self.ok(self.client.post('/api/wallet/reports/', {'order_id': order['id']}, format='json'))
            if number == 1:
                self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.image()}, format='multipart'))
                self.ok(self.client.post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image': flow.image()}, format='multipart'))
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        other.escort_profile.account = other_account
        other.escort_profile.save(update_fields=['account'])
        other_token, _ = issue_account_tokens(other_account)
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_REPORT_FOCUS_HTTP': json.dumps({
            'base': self.live_server_url, 'token': self.tokens[self.provider.pk], 'otherToken': other_token,
            'firstOrderId': orders[0]['id'], 'firstOrderNo': orders[0]['order_no'],
            'secondOrderId': orders[1]['id'], 'secondOrderNo': orders[1]['order_no'],
            'missingOrderId': max(order['id'] for order in orders) + 1000,
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/report-flow.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_successful_result_image_with_lost_response_is_reloaded(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'report-image-response-lost',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_REPORT_UPLOAD_LOST_HTTP': json.dumps({
            'base': self.live_server_url, 'token': self.tokens[self.provider.pk], 'reportId': report.pk,
            'image': base64.b64encode(flow.image().read()).decode('ascii'),
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/report-flow.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report.result_images.count(), 9)

    def test_order_selector_old_account_response_after_account_switch(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_provider

        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'report-account-switch',
        }, format='json'))
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        other.escort_profile.account = other_account
        other.escort_profile.save(update_fields=['account'])
        other_token, _ = issue_account_tokens(other_account)
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_REPORT_ACCOUNT_SWITCH_HTTP': json.dumps({
            'base': self.live_server_url, 'token': self.tokens[self.provider.pk], 'otherToken': other_token,
            'orderId': created['id'], 'orderNo': created['order_no'],
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/report-flow.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ProviderReport.objects.filter(order_id=created['id'], provider=self.provider).count(), 1)
        self.assertFalse(ProviderReport.objects.filter(order_id=created['id'], provider=other).exists())

    def test_reject_resubmit_stale_second_device_and_settlement(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'report-stale-page',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/reject/', {'audit_remark': '请补充截图'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_REPORT_STALE_HTTP': json.dumps({
            'base': self.live_server_url, 'token': self.tokens[self.provider.pk], 'reportId': report.pk,
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/report-flow.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report.refresh_from_db()
        self.assertEqual(report.status, ProviderReport.Status.PENDING)
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        from engagement.confirmation import process_order
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
        self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
