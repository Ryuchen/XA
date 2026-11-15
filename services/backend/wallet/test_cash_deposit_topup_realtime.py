"""Real local HTTP and WebSocket notification for offline cash deposits."""
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
from wallet.models import CashDepositTopup
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_CASH_DEPOSIT_REALTIME') == '1', 'Requires isolated ASGI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CashDepositRealtimeTest(LiveServerTestCase):
    def test_cash_deposit_refreshes_only_the_provider_without_changing_wallet(self):
        provider = make_provider(deposit_mode='CASH', deposit_paid=20000)
        staff = make_console_user(['escort:view', 'escort:deposit_topup'])
        provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        link_legacy_relations(provider, provider_account)
        link_legacy_relations(staff, staff_account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        wallet = get_wallet(account=provider_account, user=provider)
        with single_asgi() as (base, ws_base):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/cash-deposit-realtime.cjs')], cwd=root,
                input=json.dumps({'base': base, 'wsBase': ws_base, 'profile': provider.escort_profile.pk,
                    'account': provider_account.pk, 'providerToken': issue_account_tokens(provider_account)[0],
                    'staffToken': issue_account_tokens(staff_account)[0]}),
                encoding='utf-8', text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        provider.escort_profile.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual((provider.escort_profile.deposit_paid, wallet.balance), (40000, 0))
        self.assertEqual(CashDepositTopup.objects.filter(profile=provider.escort_profile).count(), 1)
