"""Exercise the real admin recharge and gift approval APIs on isolated data."""
import os
import tempfile
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import RechargePackage
from orders.tests.factories import make_console_user
from orders.tests.test_release_flow import screenshot
from wallet.models import RechargeRecord, Wallet
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_RECHARGE_PRICING_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class RechargePricingBrowserTest(LiveServerTestCase):
    def test_staff_recharge_and_manager_approval(self):
        staff = make_console_user(['wallet:view', 'wallet:recharge', 'recharge:view', 'recharge:gift_approve'])
        actor = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = actor; staff.admin_membership.save(update_fields=['account'])
        boss = ClubAccount.objects.create_account(username='synthetic-recharge-browser', account_type='BOSS')
        get_wallet(account=boss)
        RechargePackage.objects.create(amount=300000, gift_amount=10000, active=True)
        RechargePackage.objects.create(amount=600000, gift_amount=25000, active=True)
        media = tempfile.TemporaryDirectory(prefix='xa-recharge-media-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(actor)[0])
        quote = client.get('/api/admin/wallets/recharge-quote/', {'amount': 900000})
        self.assertEqual(quote.data['code'], 0, quote.data)
        self.assertEqual(quote.data['data']['gift_amount'], 35000)
        recharged = client.post('/api/admin/wallets/recharge/', {'account_id': boss.pk, 'amount': 900000,
            'gift_amount': 40000, 'trade_no': 'synthetic-browser-receipt',
            'client_request_id': 'synthetic-browser-receipt', 'bonus_version': quote.data['data']['version'],
            'proof_image': screenshot()}, format='multipart')
        self.assertEqual(recharged.data['code'], 0, recharged.data)
        record = RechargeRecord.objects.get(account=boss)
        decided = client.post(f'/api/admin/recharge-records/{record.pk}/gift-decision/',
            {'decision': 'APPROVED', 'reason': '隔离审批通过'}, format='json')
        self.assertEqual(decided.data['code'], 0, decided.data)
        record = RechargeRecord.objects.get(account=boss)
        self.assertEqual((record.amount, record.gift_amount, record.gift_approval.status), (900000, 40000, 'APPROVED'))
        self.assertEqual(Wallet.objects.get(account=boss).balance, 940000)