from common.fronted_test_support import fronted_root
"""Real payout registration when only its HTTP response is lost."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.tokens import issue_account_tokens
from wallet.models import WithdrawRequest
from wallet import test_withdraw_feedback_real as fixtures


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class WithdrawalLostResponseBrowserTest(LiveServerTestCase):
    setUp = fixtures.WithdrawalFeedbackRealTest.setUp
    client_for = staticmethod(fixtures.WithdrawalFeedbackRealTest.client_for)
    ok = fixtures.WithdrawalFeedbackRealTest.ok
    bind = fixtures.WithdrawalFeedbackRealTest.bind
    payload = fixtures.WithdrawalFeedbackRealTest.payload

    def test_recorded_payout_is_reconciled_after_response_loss_and_reload(self):
        root = fronted_root('admin')
        if not root:
            self.skipTest('requires an isolated current admin build')
        payee = self.bind('isolated-lost-payout-1234')
        response = self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, 'lost-payout-request'), format='json'))
        withdraw_id = response['data']['request']['id']
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{withdraw_id}/claim/'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/scenarios/admin-withdraw-lost-response-live.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'id': withdraw_id,
                              'token': issue_account_tokens(self.admin.admin_membership.account)[0]}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = WithdrawRequest.objects.get(pk=withdraw_id)
        self.assertEqual((record.status, record.payout_reference), ('APPROVED', 'ISOLATED-LOST-RESPONSE'))
        rows = self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']['requests']
        receipt = next(row for row in rows if row['id'] == withdraw_id)
        self.assertEqual((receipt['status'], receipt['payout_reference']), ('APPROVED', 'ISOLATED-LOST-RESPONSE'))
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 0))
