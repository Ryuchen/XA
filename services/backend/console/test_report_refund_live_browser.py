"""Provider's open report page follows real refund and audit events."""
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
from django.db import connection
from django.test import override_settings
from rest_framework.test import APITransactionTestCase
from club_accounts.tokens import issue_account_tokens
from console.test_report_order_block import RefundedReportWorkQueueTest


@skipUnless(os.environ.get('XA_REPORT_REFUND_LIVE_BROWSER') == '1', 'Requires isolated ASGI and H5 build')
@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ReportRefundLiveBrowserTest(APITransactionTestCase):
    setUp = RefundedReportWorkQueueTest.setUp
    login = RefundedReportWorkQueueTest.login
    ok = RefundedReportWorkQueueTest.ok
    recharge = RefundedReportWorkQueueTest.recharge
    new_order = RefundedReportWorkQueueTest.new_order
    finish = RefundedReportWorkQueueTest.finish
    prepare = RefundedReportWorkQueueTest.prepare

    def test_open_review_closes_after_refund_and_can_reject(self):
        database, node = os.environ.get('XA_ASGI_NETWORK_TEST_DB'), shutil.which('node')
        if not database or connection.settings_dict['NAME'] != database or not node:
            self.fail('An isolated file-backed database and Node are required')
        self.prepare()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None: self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                except OSError: time.sleep(.1)
            else: self.fail('isolated ASGI server did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/admin-report-refund-review-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
                                  'orderId': self.order.pk, 'reportId': self.report.pk, 'manualId': self.manual.pk}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.report.refresh_from_db()
            self.assertEqual(self.report.status, 'REJECTED')
            self.manual.refresh_from_db()
            self.assertEqual(self.manual.status, 'APPROVED')
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)

    def test_open_provider_report_tracks_refund_and_rejection(self):
        database, node = os.environ.get('XA_ASGI_NETWORK_TEST_DB'), shutil.which('node')
        if not database or connection.settings_dict['NAME'] != database or not node:
            self.fail('An isolated file-backed database and Node are required')
        self.prepare()
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', '19166', 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', 19166), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI server did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/provider-report-refund-live-375.cjs')],
                input=json.dumps({'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'providerToken': issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'bossId': str(self.accounts[self.boss.pk].pk),
                    'providerId': str(self.accounts[self.provider.pk].pk), 'orderId': self.order.pk,
                    'orderNo': self.order.order_no, 'reportId': self.report.pk}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
