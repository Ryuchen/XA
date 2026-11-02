"""Actual audit notices open the provider's withdrawal records."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider
from wallet.test_provider_vue_withdraw_receipt_http import ProviderVueWithdrawReceiptHTTPTest


class ProviderVueWithdrawNoticeHTTPTest(ProviderVueWithdrawReceiptHTTPTest):
    def run_browser(self, root):
        empty_provider = make_provider()
        empty_account = get_or_create_account_for_legacy_user(empty_provider)
        empty_provider.escort_profile.account = empty_account
        empty_provider.escort_profile.save(update_fields=['account'])
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-withdraw-notice-375.cjs')],
            input=json.dumps({'root':root, 'base':self.live_server_url, 'token':self.tokens[self.provider.pk],
                'id':self.accounts[self.provider.pk].pk, 'emptyToken':issue_account_tokens(empty_account)[0],
                'emptyId':empty_account.pk, 'fixed':os.environ.get('XA_WITHDRAW_NOTICE_FIXED') == '1'}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
