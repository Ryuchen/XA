"""Dashboard claim counts drill into real gift entitlement work queues."""
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import GiftBenefitRule, RewardClaim, RewardItem
from orders.models import ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class DashboardClaimWorkflowTest(LiveServerTestCase):
    def test_counts_and_drilldown_follow_actual_delivery_state(self):
        media = TemporaryDirectory(prefix='xa-dashboard-claim-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider = make_user(), make_provider(is_verified=True, deposit_mode='CASH')
        staff = make_console_user(['wallet:recharge', 'dashboard:view', 'engagement:fulfill'])
        viewer = make_console_user(['dashboard:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff, viewer)]
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        for user, account in ((staff, accounts[2]), (viewer, accounts[3])):
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='工作台赠礼', is_gift=True)
        gift = make_service(name='工作台礼物', price=2100, service_category=category)
        reward = RewardItem.objects.create(code='dashboard-claim', name='工作台赠礼权益', kind='SERVICE', in_shop=False)
        GiftBenefitRule.objects.create(name='工作台赠礼规则', reward=reward, category=category,
            threshold=2100, inclusive=True, enabled=True)
        clients = []
        for account in accounts:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        ok(clients[2].post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk, 'amount': 10000,
            'gift_amount': 0, 'trade_no': 'dashboard-claim-recharge', 'proof_image': screenshot(),
            'client_request_id': 'dashboard-claim-recharge'}, format='multipart'))
        ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'quantity': 1,
            'provider_id': accounts[1].pk, 'expected_amount': 2100, 'client_request_id': 'dashboard-claim-gift'}, format='json'))
        claim = RewardClaim.objects.get(account=accounts[0], reward=reward)
        def check(status, pending, fulfilling):
            snapshot = ok(clients[2].get('/api/admin/dashboard/stats'))
            self.assertEqual((snapshot['pending_claim_total'], snapshot['fulfilling_claim_total']), (pending, fulfilling))
            listing = ok(clients[2].get('/api/admin/club-claims/', {'status': status}))
            self.assertEqual([row['id'] for row in listing['list']], [claim.pk])
            if os.environ.get('XA_DASHBOARD_CLAIM_BROWSER') == '1':
                repo = Path(settings.BASE_DIR).parent.parent
                browser = subprocess.run(['node', str(repo / 'scripts/dashboard-claim-375.cjs')], cwd=repo,
                    input=json.dumps({'base': self.live_server_url, 'root': os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                        'token': issue_account_tokens(accounts[2])[0], 'status': status, 'claimId': claim.pk}),
                    text=True, encoding='utf-8', capture_output=True, timeout=60)
                self.assertEqual(browser.returncode, 0, browser.stdout + browser.stderr)
        check('PENDING', 1, 0)
        unauthorized = ok(clients[3].get('/api/admin/dashboard/stats'))
        self.assertIsNone(unauthorized['pending_claim_total'])
        ok(clients[2].post(f'/api/admin/club-claims/{claim.pk}/process/', {'status': 'FULFILLING', 'note': '正在安排'}, format='json'))
        check('FULFILLING', 0, 1)
        ok(clients[2].post(f'/api/admin/club-claims/{claim.pk}/process/', {'status': 'COMPLETED', 'note': '已交付'}, format='json'))
        final = ok(clients[2].get('/api/admin/dashboard/stats'))
        self.assertEqual((final['pending_claim_total'], final['fulfilling_claim_total']), (0, 0))
