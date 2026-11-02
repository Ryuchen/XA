"""Exercise fee configuration through the real mobile admin and isolated backend."""
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
from orders.models import PaymentFeePolicy, PaymentFeePolicyHistory
from orders.tests.factories import make_console_user


@skipUnless(os.environ.get('XA_PAYMENT_FEE_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PaymentFeeBrowserTest(LiveServerTestCase):
    def test_mobile_configuration_preview_and_history(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        user = make_console_user(['payment_fee:view', 'payment_fee:edit'])
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-payment-fee-mobile-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(account)[0]}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        row = PaymentFeePolicy.objects.get(pk=1)
        self.assertEqual((row.revision, row.rate_millionths, row.fee_basis, row.rounding),
                         (1, 6000, 'PAYMENT_TOTAL', 'UP'))
        self.assertEqual(PaymentFeePolicyHistory.objects.count(), 1)
