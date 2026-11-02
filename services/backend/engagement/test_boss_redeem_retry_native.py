"""A lost redemption response must remain recoverable after stock sells out."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import RewardClaim, RewardItem
from orders.tests.factories import make_console_user, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class BossRedemptionRetryNativeTest(LiveServerTestCase):
    host, port = '127.0.0.1', 19210

    def test_response_loss_after_sold_out_redemption(self):
        if os.environ.get('XA_NATIVE_REDEEM_RETRY') != '1':
            self.skipTest('requires isolated WeChat developer tools')
        boss, staff = make_user(), make_console_user(['engagement:points', 'engagement:fulfill', 'user:edit'])
        account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, staff)]
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        staff_client = APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        credited = staff_client.post('/api/admin/external-points/', {'account_id': account.pk,
            'kind': 'KOOK_CHECKIN', 'day': timezone.localdate().isoformat(), 'source_id': 'native-retry-checkin'}, format='json')
        self.assertEqual(credited.data['code'], 0, credited.data)
        reward = RewardItem.objects.create(code='native-retry-reward', name='最后一份权益', kind='SERVICE',
            audience='BOSS', point_cost=5, in_shop=True, is_active=True, stock=1)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/native-boss-redeem-retry-375.cjs')], cwd=repo,
            input=json.dumps({'base': self.live_server_url, 'rewardId': reward.pk, 'staffToken': issue_account_tokens(staff_account)[0],
                'session': {'token': issue_account_tokens(account)[0], 'user': {'id': str(account.pk), 'role': 'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reward.refresh_from_db()
        self.assertEqual((reward.stock, reward.redeemed_count), (0, 1))
        self.assertEqual(RewardClaim.objects.filter(account=account, reward=reward).count(), 2)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(client.get('/api/club/points/').data['data']['points'], 0)
        claims = list(RewardClaim.objects.filter(account=account, reward=reward).order_by('id'))
        self.assertTrue(client.get(f'/api/club/claims/{claims[0].pk}/').data['data']['points_returned'])
        self.assertFalse(client.get(f'/api/club/claims/{claims[1].pk}/').data['data']['points_returned'])
        rows = client.get('/api/club/claims/?page_size=20').data['data']['items']
        self.assertEqual({row['id']: row['points_returned'] for row in rows},
                         {claims[0].pk: True, claims[1].pk: False})
        messages = client.get('/api/messages/').data['data']['messages']
        self.assertTrue(any('5 积分已退回' in item['preview'] for item in messages))
