"""A provider pass purchase follows real staff recharges over the isolated API."""
import os
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from orders.tests.test_release_flow import screenshot
from users.models import ProviderPassPurchase
from wallet.models import Wallet


@skipUnless(os.environ.get('XA_PASS_PAGE_ROOT'), 'Requires isolated provider H5 build')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class PassPageRechargeRealtimeTest(LiveServerTestCase):
    def test_recharge_with_confirmation_open_then_buy(self):
        media = TemporaryDirectory(prefix='xa-pass-page-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        provider, staff = make_provider(), make_console_user(['wallet:recharge'])
        account, admin = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        link_legacy_relations(provider, account); link_legacy_relations(staff, admin)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        for amount, request_id in ((100000, 'pass-page-recharge'), (50000, 'pass-page-recharge-during-purchase')):
            recharge = staff_client.post('/api/admin/wallets/recharge/', {'account_id': account.pk, 'amount': amount,
                'gift_amount': 0, 'trade_no': request_id, 'client_request_id': request_id,
                'proof_image': screenshot()}, format='multipart')
            self.assertEqual(recharge.data['code'], 0, recharge.data)
        provider_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        bought = provider_client.post('/api/users/provider-pass/', {'tier': 'BRONZE', 'days': 1,
            'client_request_id': 'pass-page-recharge-buy'}, format='json')
        self.assertEqual(bought.data['code'], 0, bought.data)
        purchase = ProviderPassPurchase.objects.get(account=account)
        self.assertEqual(Wallet.objects.get(account=account).balance, 150000 - purchase.paid_amount)
