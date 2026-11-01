"""Exercise the release UI against real isolated withdrawal APIs."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class WithdrawReleaseFrontendHTTPTest(LiveServerTestCase):
    def test_admin_releases_only_after_confirmation_and_rechecks_claim_version(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(user=provider, account=account)
        wallet.balance = 60000
        wallet.save(update_fields=['balance'])
        users = [make_console_user(['withdraw:view', 'withdraw:audit']),
                 make_console_user(['withdraw:view', 'withdraw:release'])]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, staff_account in zip(users, accounts):
            user.admin_membership.account = staff_account
            user.admin_membership.save(update_fields=['account'])
        tokens = [issue_account_tokens(item)[0] for item in accounts]
        def client(token):
            obj = APIClient()
            obj.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            return obj
        provider_client, staff, admin = client(issue_account_tokens(account)[0]), client(tokens[0]), client(tokens[1])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        payee = ok(provider_client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account':'isolated-account-1234', 'payee_name':'隔离收款人',
            'expected_id':0, 'expected_version':0,
        }, format='json'))
        rows = []
        for request_id in ('release-ui-1', 'release-ui-2'):
            created = ok(provider_client.post('/api/wallet/withdraw/', {
                'amount':20000, 'withdrawal_account_id':payee['id'],
                'expected_payee_version':payee['version'], 'client_request_id':request_id,
            }, format='json'))
            row_id = created['request']['id']
            ok(staff.post(f'/api/admin/withdrawals/{row_id}/claim/'))
            rows.append(ok(admin.get(f'/api/admin/withdrawals/{row_id}/')))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-withdraw-release-live.cjs')],
            input=json.dumps({'base':self.live_server_url, 'staffToken':tokens[0], 'adminToken':tokens[1], 'ownerId':account.pk, 'rows':rows}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('PASS real HTTP', result.stdout)
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.frozen_amount), (20000, 40000))
