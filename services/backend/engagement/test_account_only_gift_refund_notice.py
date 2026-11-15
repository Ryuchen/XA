"""Gift rights remain owned by the order's explicit business account."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from django.test import LiveServerTestCase, override_settings
from django.conf import settings
from rest_framework.test import APIClient

from club_accounts.models import LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import GiftBenefitRule, RewardClaim, RewardItem
from orders.models import ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountOnlyGiftRefundNoticeTest(LiveServerTestCase):
    def test_full_gift_refund_preserves_rights_for_account_without_legacy_mapping(self):
        media = TemporaryDirectory(prefix='xa-gift-notice-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider, other = make_user(), make_provider(is_verified=True, deposit_mode='CASH'), make_user()
        staff = make_console_user(['wallet:recharge', 'order:view', 'order:refund'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, other, staff)]
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[3]
        staff.admin_membership.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='隔离退款礼物', is_gift=True)
        gift = make_service(name='隔离权益礼物', price=4000, commission_rate=20, service_category=category)
        reward = RewardItem.objects.create(code='account-only-refund-gift', name='隔离赠礼权益', kind='SERVICE', in_shop=False)
        GiftBenefitRule.objects.create(name='隔离赠礼权益规则', reward=reward, category=category,
            threshold=3000, inclusive=True, enabled=True)
        clients = [APIClient() for _ in accounts]
        for client, account in zip(clients, accounts):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        ok(clients[3].post('/api/admin/wallets/recharge/', {'account_id':accounts[0].pk,
            'amount':10000, 'gift_amount':0, 'trade_no':'account-gift-notice-recharge',
            'proof_image':screenshot(), 'client_request_id':'account-gift-notice-recharge'}, format='multipart'))
        order = ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id':gift.pk, 'quantity':1,
            'provider_id':accounts[1].pk, 'expected_amount':4000, 'client_request_id':'account-gift-notice-send'}, format='json'))
        claim = RewardClaim.objects.get(account=accounts[0], reward=reward)
        self.assertEqual(claim.status, 'PENDING')
        # Exercise a historical identity with an explicit order account but no legacy mapping.
        LegacyAccountMap.objects.filter(account=accounts[0]).delete()
        preview = ok(clients[3].get(f"/api/admin/orders/{order['id']}/refund-preview/"))
        ok(clients[3].post(f"/api/admin/orders/{order['id']}/refund/", {'reason':'隔离退款验收',
            'plan_token':preview['plan_token'], 'client_request_id':'account-gift-notice-refund'}, format='json'))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'PENDING')
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 10000)
        self.assertEqual(ok(clients[1].get('/api/wallet/provider-benefits/'))['gift_records'][0]['settlement_status'], 'REFUNDED')
        owner_order = ok(clients[0].get(f"/api/orders/orders/{order['id']}/"))
        self.assertTrue(owner_order['refund_summary']['records'][0]['gift_rights_preserved'])
        admin_order = ok(clients[3].get(f"/api/admin/orders/{order['id']}/"))
        self.assertTrue(admin_order['refund_summary']['records'][0]['benefit_effects']['gift_rights_preserved'])
        notices = [row for row in ok(clients[0].get('/api/messages/'))['messages'] if row['title'] == '权益状态已调整']
        self.assertEqual(notices, [])
        self.assertFalse([row for row in ok(clients[2].get('/api/messages/'))['messages'] if row['title'] == '权益状态已调整'])
        if os.environ.get('XA_GIFT_RIGHTS_BROWSER') == '1':
            from wallet.test_deposit_realtime_workflow import single_asgi
            with single_asgi() as (api, _):
                root = Path(settings.BASE_DIR).parent.parent
                result = subprocess.run(['node', str(root / 'scripts/gift-refund-rights-browser.cjs')], cwd=root,
                    input=json.dumps({'api':api, 'token':issue_account_tokens(accounts[0])[0],
                                      'account':accounts[0].pk, 'order':order['id']}),
                    text=True, encoding='utf-8', capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
