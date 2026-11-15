import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile
from engagement.services import config
from orders.tests.factories import make_provider
from users.models import ProviderPassPointRedemption, ProviderPassPurchase
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_PASS_POINTS_BROWSER') == '1', 'Requires isolated ASGI and provider Vite')
@override_settings(PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False)
class PassPointsBrowserTest(LiveServerTestCase):
    def test_provider_quotes_and_pays_from_actual_page(self):
        user = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(user); link_legacy_relations(user, account)
        profile = user.escort_profile; profile.account = account; profile.save(update_fields=['account'])
        wallet = get_wallet(account=account, user=user); wallet.balance = 180; wallet.save(update_fields=['balance'])
        MemberProfile.objects.create(account=account, points_balance=30000)
        config()
        save_points_terms(group_point_hundredths=10000, group_coin_units=10,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/pass-points-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'account': account.pk, 'token': issue_account_tokens(account)[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        wallet.refresh_from_db()
        purchase = ProviderPassPurchase.objects.get(account=account)
        self.assertEqual((wallet.balance, purchase.paid_amount, purchase.point_discount_amount), (0, 180, 20))
        self.assertEqual(ProviderPassPointRedemption.objects.get(purchase=purchase).points_hundredths, 20000)
        self.assertEqual(MemberProfile.objects.get(account=account).points_balance, 10000)
