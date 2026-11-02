"""Exercise an already-running old team order through actual report APIs."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.test import override_settings
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class LegacyTeamRunningWorkflowTest(APITransactionTestCase):
    def test_running_old_team_can_complete_both_reports_without_early_income(self):
        media = tempfile.TemporaryDirectory(); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, staff = make_user(), make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        providers = [make_provider(), make_provider()]
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, *providers)]
        for user, account in zip((boss, staff, *providers), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        for provider, account in zip(providers, accounts[2:]):
            provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        service = make_service(price=10000, billing_provider_count=2)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        def login(index):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[index])
        def ok(response):
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')
        login(1)
        order_id = ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': accounts[0].pk, 'service_id': service.pk, 'escort_mode': 'DOUBLE',
            'providers': [{'provider_id': account.pk} for account in accounts[2:]],
            'client_request_id': 'legacy-running-team',
        }, format='json'))['id']
        # Persist the marker used by orders created before the workflow fix.
        Order.objects.filter(pk=order_id).update(requires_boss_selection=False)
        login(2)
        # A provider may already have opened the report before staff checks the
        # historical order. Do not switch workflows over an existing draft.
        draft = ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
        login(1)
        denied = self.client.post(f'/api/admin/orders/{order_id}/enable-team-evidence/', {'confirmed': True}, format='json').data
        self.assertEqual(denied['code'], 409, denied)
        self.assertIn('按原流程', denied['msg'])
        self.assertIn('双方分别补齐凭证', denied['msg'])
        self.assertFalse(Order.objects.get(pk=order_id).requires_boss_selection)
        login(2)
        ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order_id).status, 'IN_SERVICE')
        detail = ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        node = shutil.which('node')
        if not node:
            self.fail('Node runtime required for provider presentation')
        repo = Path(settings.BASE_DIR).parent.parent
        presented = subprocess.run([node, '--test', '--test-name-pattern=real running legacy team response',
                                    str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
            env={**os.environ, 'XA_REAL_LEGACY_TEAM_ORDER': json.dumps(detail, cls=DjangoJSONEncoder)},
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(presented.returncode, 0, presented.stdout + presented.stderr)
        login(0)
        boss_detail = ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        native = subprocess.run([node, str(repo / 'scripts/native-legacy-team-progress.cjs')],
            input=json.dumps(boss_detail, cls=DjangoJSONEncoder), text=True, encoding='utf-8',
            capture_output=True, timeout=20, cwd=repo)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        login(1)
        denied = self.client.post(f'/api/admin/orders/{order_id}/enable-team-evidence/', {'confirmed': True}, format='json').data
        self.assertEqual(denied['code'], 409, denied)
        login(2)
        ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order_id).status, 'COMPLETED')
        for index, provider in enumerate(providers):
            login(index + 2)
            report = ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
            if index == 0:
                self.assertEqual(report['id'], draft['id'])
            for kind in (('RESULT',) if index == 0 else ('ENTRY', 'COMPLETION', 'RESULT')):
                ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': kind, 'image': screenshot()}, format='multipart'))
            ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/", {}, format='json'))
            login(1)
            ok(self.client.post(f"/api/admin/reports/{report['id']}/approve/", {}, format='json'))
        self.assertEqual(ProviderReport.objects.filter(order_id=order_id, status='APPROVED').count(), 2)
        self.assertIsNone(Order.objects.get(pk=order_id).settled_at)
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())
