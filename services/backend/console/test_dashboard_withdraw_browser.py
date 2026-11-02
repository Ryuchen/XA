"""Exercise the withdrawal dashboard drilldown using actual isolated applications."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from wallet.test_withdraw_feedback_real import WithdrawalFeedbackRealTest as fixtures
from wallet.models import WithdrawRequest


@skipUnless(os.environ.get('XA_ADMIN_WITHDRAW_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class DashboardWithdrawalBrowserTest(LiveServerTestCase):
    setUp = fixtures.setUp
    client_for = staticmethod(fixtures.client_for)
    ok = fixtures.ok
    bind = fixtures.bind
    payload = fixtures.payload

    def test_other_staff_claim_drilldown_on_mobile(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        self.account.nickname = '陪玩测试昵称' * 7
        self.account.save(update_fields=['nickname'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = ['dashboard:view', 'withdraw:view', 'withdraw:release']
        role.save(update_fields=['permissions'])
        other = make_console_user(['withdraw:view', 'withdraw:audit'])
        other_account = get_or_create_account_for_legacy_user(other)
        other.admin_membership.account = other_account
        other.admin_membership.save(update_fields=['account'])
        reclaimer = make_console_user(['withdraw:view', 'withdraw:audit'])
        reclaimer_account = get_or_create_account_for_legacy_user(reclaimer)
        reclaimer.admin_membership.account = reclaimer_account
        reclaimer.admin_membership.save(update_fields=['account'])
        payee = self.bind('browser-test-1234')
        ids = [self.ok(self.provider_client.post('/api/wallet/withdraw/', self.payload(payee, f'mobile-withdraw-{n}'), format='json'))['data']['request']['id'] for n in range(2)]
        self.ok(self.client_for(other_account).post(f'/api/admin/withdrawals/{ids[0]}/claim/'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-dashboard-withdraw-mobile-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(self.admin.admin_membership.account)[0], 'reclaimerToken': issue_account_tokens(reclaimer_account)[0], 'claimedId': ids[0], 'unclaimedId': ids[1]}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = WithdrawRequest.objects.get(pk=ids[0])
        self.assertEqual((record.claimed_by_account_id, record.status, record.payout_reference),
                         (reclaimer_account.pk, 'APPROVED', 'ISOLATED-QA-TRANSFER'))
        history = self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']['requests']
        receipt = next(row for row in history if row['id'] == ids[0])
        self.assertEqual((receipt['status'], receipt['actual_amount'], receipt['payout_reference']),
                         ('APPROVED', 19600, 'ISOLATED-QA-TRANSFER'))
        messages = self.ok(self.provider_client.get('/api/messages/'))['data']['messages']
        self.assertTrue(any(message['title'] == '提现审核通过' and '¥196.00' in message['preview'] for message in messages), messages)
        self.ok(self.client_for(other_account).post(f'/api/admin/withdrawals/{ids[1]}/claim/'))
        self.ok(self.provider_client.post('/api/wallet/withdraw/', self.payload(payee, 'mobile-withdraw-unclaimed'), format='json'))
        provider = subprocess.run([node, str(repo / 'scripts/provider-withdraw-mobile-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'token': issue_account_tokens(self.account)[0], 'id': self.account.pk,
                              'pendingProgress': True}),
            text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
        self.assertEqual(provider.returncode, 0, provider.stdout + provider.stderr)
