"""Real gifts produce more entitlement records than the old list limit."""
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
class ClaimPaginationJourneyTest(LiveServerTestCase):
    host, port = '127.0.0.1', 19210
    def test_old_gift_entitlements_remain_reachable_during_new_gifts(self):
        media = TemporaryDirectory(prefix='xa-claim-pages-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, other, provider = make_user(), make_user(), make_provider(is_verified=True, deposit_mode='CASH')
        staff = make_console_user(['wallet:recharge'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, other, provider, staff)]
        provider.escort_profile.account = accounts[2]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[3]
        staff.admin_membership.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='分页礼物', is_gift=True)
        gifts = [make_service(name=f'分页赠礼 {index}', price=2100, service_category=category) for index in range(102)]
        reward = RewardItem.objects.create(code='claim-pagination-gift', name='赠礼权益', kind='SERVICE', in_shop=False)
        GiftBenefitRule.objects.create(name='分页权益', reward=reward, category=category,
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
        ok(clients[3].post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk, 'amount': 250000,
            'gift_amount': 0, 'trade_no': 'claim-pages-recharge', 'proof_image': screenshot(),
            'client_request_id': 'claim-pages-recharge'}, format='multipart'))
        def send(index, items):
            return ok(clients[0].post('/api/orders/gifts/send/', {'items': [{'gift_service_id': gift.pk, 'quantity': 1} for gift in items],
                'provider_id': accounts[2].pk, 'expected_amount': 2100 * len(items),
                'client_request_id': f'claim-pages-gift-{index:03}'}, format='json'))
        for index, start in enumerate(range(0, 101, 30)):
            send(index, gifts[start:min(start + 30, 101)])
        original = set(RewardClaim.objects.filter(account=accounts[0]).values_list('id', flat=True))
        self.assertEqual(len(original), 101)
        self.assertEqual(len(ok(clients[0].get('/api/club/claims/'))), 100)
        first = ok(clients[0].get('/api/club/claims/', {'page_size': 20}))
        seen = [row['id'] for row in first['items']]
        cursor = first['next']
        send(101, gifts[101:])
        while cursor:
            page = ok(clients[0].get('/api/club/claims/', {'page_size': 20, 'before': cursor}))
            seen.extend(row['id'] for row in page['items'])
            cursor = page['next']
        self.assertEqual(set(seen), original)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(ok(clients[1].get('/api/club/claims/', {'page_size': 20}))['items'], [])
        self.assertEqual(ok(clients[0].get(f'/api/club/claims/{min(original)}/'))['name_snapshot'], '赠礼权益')
        if os.environ.get('XA_NATIVE_CLAIM_PAGES') == '1':
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / '.tools/native-recharge-checkout-375-20261102/native-claim-pages-375.cjs')],
                cwd=repo, input=json.dumps({'oldest': min(original), 'total': 102,
                    'session': {'token': issue_account_tokens(accounts[0])[0], 'user': {'id': str(accounts[0].pk), 'role': 'customer'}}}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
