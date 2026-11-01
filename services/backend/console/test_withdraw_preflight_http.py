"""Exercise admin withdrawal preflight with real HTTP and an isolated database."""
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
from wallet.models import SystemConfig
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class WithdrawalPreflightHTTPTest(LiveServerTestCase):
    def test_second_staff_cannot_see_old_payout_prompt_after_rejection(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(user=provider, account=account)
        wallet.balance = 100000
        wallet.save(update_fields=['balance'])
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value':'2'})
        staff = make_console_user(['withdraw:view', 'withdraw:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        other_staff = make_console_user(['withdraw:view', 'withdraw:audit'])
        other_account = get_or_create_account_for_legacy_user(other_staff)
        other_staff.admin_membership.account = other_account
        other_staff.admin_membership.save(update_fields=['account'])
        pclient, aclient = APIClient(), APIClient()
        pclient.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        token = issue_account_tokens(staff_account)[0]
        aclient.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_account)[0])

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        payee = ok(pclient.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account':'test-account-1234','payee_name':'本地真实收款人',
            'expected_id':0,'expected_version':0,
        }, format='json'))
        ids = []
        for request_id in ('preflight-1','preflight-2','preflight-3','preflight-4'):
            result = ok(pclient.post('/api/wallet/withdraw/', {
                'amount':20000,'withdrawal_account_id':payee['id'],
                'expected_payee_version':payee['version'],'client_request_id':request_id,
            }, format='json'))
            ids.append(result['request']['id'])
        stale = ok(aclient.post(f'/api/admin/withdrawals/{ids[0]}/claim/', {}, format='json'))
        pending = ok(aclient.post(f'/api/admin/withdrawals/{ids[1]}/claim/', {}, format='json'))
        conflict = ok(aclient.get(f'/api/admin/withdrawals/{ids[2]}/'))
        unclaimed = ok(aclient.get(f'/api/admin/withdrawals/{ids[3]}/'))
        ok(other_client.post(f'/api/admin/withdrawals/{ids[2]}/claim/', {}, format='json'))
        # A second device belonging to the same claimant may complete the review.
        ok(aclient.post(f'/api/admin/withdrawals/{stale["id"]}/reject/', {'audit_remark':'隔离测试',
            'expected_claimed_at':stale['claimed_at']}, format='json'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-withdraw-preflight-live.cjs')],
            input=json.dumps({'base':self.live_server_url,'token':token,'stale':stale,'pending':pending,
                              'conflict':conflict,'unclaimed':unclaimed}),
            text=True, encoding='utf-8',capture_output=True,timeout=30,cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('PASS real HTTP', result.stdout)
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance,wallet.frozen_amount),(40000,60000))
