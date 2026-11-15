"""Finance UI reading real mixed pass refunds from an isolated store."""
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointLedger
from engagement.services import config
from orders.tests.factories import make_provider
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from users.models import ProviderPassPurchase, ProviderPassRefund
from wallet.models import get_platform_wallet
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_PASS_FINANCE_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(PLATFORM_IDENTITY_ENABLED=True, PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True,
                   POINTS_USAGE_SWITCHES_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PlatformPassPointsBrowserTest(LiveServerTestCase):
    def test_finance_distinguishes_returned_points_and_inconsistent_records(self):
        config()
        save_points_terms(group_point_hundredths=10000, group_coin_units=10,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        accounts = []
        for index in range(2):
            user = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(user); link_legacy_relations(user, account)
            profile = user.escort_profile; profile.account = account; profile.save(update_fields=['account'])
            wallet = get_wallet(account=account, user=user); wallet.balance = 1000; wallet.save(update_fields=['balance'])
            MemberProfile.objects.create(account=account, points_balance=30000)
            client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            quote = client.post('/api/users/provider-pass/points-quote/', {'tier': 'BRONZE',
                                'selected_point_hundredths': 20000}, format='json').data['data']
            bought = client.post('/api/users/provider-pass/', {'tier': 'BRONZE', 'days': 1,
                'points_quote_token': quote['quote_token'], 'client_request_id': f'finance-page-buy-{index}'}, format='json')
            self.assertEqual(bought.data['code'], 0, bought.data)
            purchase = ProviderPassPurchase.objects.get(account=account)
            now = timezone.now(); purchase.starts_at = now + timedelta(hours=1); purchase.expires_at = now + timedelta(hours=25)
            purchase.save(update_fields=['starts_at', 'expires_at'])
            profile.pass_tier, profile.pass_expires_at = purchase.tier, purchase.expires_at
            profile.save(update_fields=['pass_tier', 'pass_expires_at'])
            shop = get_platform_wallet(); shop.balance = 0; shop.save(update_fields=['balance'])
            refunded = client.post(f'/api/users/provider-pass/{purchase.pk}/refund/',
                {'client_request_id': f'finance-page-refund-{index}'}, format='json')
            self.assertEqual(refunded.data['code'], 0, refunded.data)
            refund = ProviderPassRefund.objects.get(purchase=purchase)
            if index == 1:
                PointLedger.objects.filter(pk=refund.points_return_ledger_id).update(amount=19999)
            accounts.append(account.account_no)
        finance = PlatformAccount(username='finance-points-browser', phone='13800001111', is_phone_verified=True)
        finance.set_password('Synthetic-Platform-Password-2026!'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='shenglang')
        token, refresh = issue_tokens(finance, 'SMS', finance.phone)
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/platform-pass-points-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'token': token, 'refresh': refresh, 'id': str(finance.pk),
                                  'username': finance.username, 'accounts': accounts}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
