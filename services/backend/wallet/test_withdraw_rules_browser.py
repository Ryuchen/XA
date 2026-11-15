import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from wallet.models import SystemConfig, WithdrawalAccount, WithdrawRequest
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_WITHDRAW_RULES_BROWSER') == '1', 'Requires isolated ASGI and provider Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class WithdrawalRulesBrowserTest(LiveServerTestCase):
    def test_zero_payout_then_updated_rule_on_provider_page(self):
        for key, value in [('min_withdraw_amount', '0'), ('withdraw_balance_threshold', '0'), ('withdraw_tax_rate', '100')]:
            SystemConfig.objects.update_or_create(key=key, defaults={'value':value})
        provider, staff = make_provider(), make_console_user(['withdraw:view', 'withdraw:config'])
        account, admin = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        link_legacy_relations(provider, account); link_legacy_relations(staff, admin)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = get_wallet(account=account, user=provider)
        wallet.balance = 10000
        wallet.save(update_fields=['balance'])
        payee = WithdrawalAccount.objects.create(account=account, payee_method='WECHAT',
                                                  payee_account='qa-only', payee_name='隔离测试')
        with single_asgi() as (base, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/withdraw-rule-browser-live.cjs')], cwd=root,
                input=json.dumps({'api':base, 'account':account.pk, 'payee':payee.pk,
                    'token':issue_account_tokens(account)[0], 'staffToken':issue_account_tokens(admin)[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        wallet.refresh_from_db()
        request = WithdrawRequest.objects.get(account=account)
        self.assertEqual((request.amount, request.tax_rate, request.actual_amount), (1000, 2, 980))
        self.assertEqual((wallet.balance, wallet.frozen_amount), (9000, 1000))
