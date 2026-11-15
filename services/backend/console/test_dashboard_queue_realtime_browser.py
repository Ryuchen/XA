"""The open workbench refreshes real order and withdrawal queue counts."""
import json
import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.models import Order
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, WithdrawRequest


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class DashboardQueueRealtimeBrowserTest(APITransactionTestCase):
    def test_new_order_and_withdrawal_update_open_workbench(self):
        node, database = shutil.which('node'), os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not node or not database or connection.settings_dict['NAME'] != database:
            self.skipTest('Requires isolated file database and ASGI')
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['dashboard:view', 'order:view', 'withdraw:view', 'report:view', 'report:audit', 'evaluation:view', 'evaluation:reply'])
        boss_account, provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        for user, account, balance in ((boss, boss_account, 50000), (provider, provider_account, 60000)):
            wallet = make_wallet(user, balance=balance)
            wallet.account = account
            wallet.save(update_fields=['account'])
        service = make_service(price=10000)
        provider.escort_profile.service_items.add(service)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        payee = client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': 'isolated-dashboard-account', 'payee_name': '本地测试',
            'expected_id': 0, 'expected_version': 0,
        }, format='json').data
        self.assertEqual(payee['code'], 0, payee)
        from orders.tests.test_provider_vue_team_progress_http import isolated_realtime
        temp = tempfile.TemporaryDirectory(prefix='xa-dashboard-realtime-')
        self.addCleanup(temp.cleanup)
        with override_settings(MEDIA_ROOT=temp.name), isolated_realtime(report_approval=True) as (api_base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/admin-dashboard-queue-realtime-live.cjs')],
                input=json.dumps({'api': api_base, 'ws': ws_base, 'staffToken': issue_account_tokens(staff_account)[0],
                    'bossToken': issue_account_tokens(boss_account)[0], 'providerToken': issue_account_tokens(provider_account)[0],
                    'providerId': provider_account.pk, 'evidence': base64.b64encode(screenshot().read()).decode('ascii'),
                    'serviceId': service.pk, 'withdrawPayload': {'amount': 20000,
                        'withdrawal_account_id': payee['data']['id'], 'expected_payee_version': payee['data']['version'],
                        'client_request_id': 'dashboard-live-withdraw'}}), cwd=repo,
                text=True, encoding='utf-8', capture_output=True, timeout=80)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Order.objects.filter(customer_account=boss_account, status='PENDING').count(), 1)
            self.assertEqual(WithdrawRequest.objects.filter(account=provider_account, status='PENDING').count(), 1)
            self.assertEqual(ProviderReport.objects.filter(provider_account=provider_account, status='APPROVED').count(), 1)
