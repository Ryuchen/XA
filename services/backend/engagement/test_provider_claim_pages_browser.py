"""Provider earns isolated KOOK points and redeems real rewards across pages."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig, RewardClaim, RewardItem
from orders.tests.factories import make_console_user, make_provider, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderClaimPagesBrowserTest(LiveServerTestCase):
    def test_provider_can_load_and_edit_old_reward_claim(self):
        provider = make_provider(display_name='分页陪玩', is_verified=True, deposit_mode='CASH')
        staff = make_console_user(['engagement:fulfill', 'engagement:points'])
        account = get_or_create_account_for_legacy_user(provider)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        client = APIClient()
        token = issue_account_tokens(account)[0]
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        now = timezone.now()
        KookPointsConfig.objects.create(pk=1, enabled=True, guild_id='isolated-guild',
            checkin_channel_id='isolated-hall', voice_channel_ids=[], started_at=now-timedelta(days=7))
        code = ok(client.post('/api/club/kook-binding/', {}, format='json'))['code']
        def incoming(content, event_id, timestamp):
            return {'type': 1, 'channel_type': 'GROUP', 'target_id': 'isolated-hall',
                'author_id': 'isolated-provider', 'msg_id': event_id, 'msg_timestamp': int(timestamp.timestamp()*1000),
                'content': content, 'extra': {'guild_id': 'isolated-guild', 'author': {'username': '隔离用户', 'bot': False}}}
        # The external KOOK event is simulated; binding, scoring, ledger and redemption run real code.
        self.assertTrue(process_event(incoming('绑定 ' + code, 'bind', now), now))
        for index in range(5):
            at = now-timedelta(days=4-index)
            self.assertTrue(process_event(incoming('签到', f'checkin-{index}', at), now))
        reward = RewardItem.objects.create(code='provider-pages', name='陪玩兑换权益', kind='SERVICE',
            audience='PROVIDER', point_cost=1, in_shop=True, is_active=True)
        physical = RewardItem.objects.create(code='provider-physical', name='陪玩实物奖品', kind='PHYSICAL',
            audience='PROVIDER', point_cost=1, in_shop=True, is_active=True)
        for index in range(21):
            ok(client.post('/api/club/redeem/', {'reward_id': reward.pk, 'details': {},
                'client_request_id': f'provider-pages-{index:03}'}, format='json'))
        ids = list(RewardClaim.objects.filter(account=account).order_by('id').values_list('id', flat=True))
        self.assertEqual(len(ids), 21)
        boss = get_or_create_account_for_legacy_user(make_user())
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        boss_client = APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
        boss_code = ok(boss_client.post('/api/club/kook-binding/', {}, format='json'))['code']
        for content, event_id in [('绑定 ' + boss_code, 'boss-bind'), ('签到', 'boss-checkin')]:
            event = incoming(content, event_id, now)
            event['author_id'] = 'isolated-boss'
            self.assertTrue(process_event(event, now))
        boss_reward = RewardItem.objects.create(code='boss-identity', name='老板兑换权益', kind='SERVICE',
            audience='BOSS', point_cost=1, in_shop=True, is_active=True)
        boss_claim = ok(boss_client.post('/api/club/redeem/', {'reward_id': boss_reward.pk,
            'client_request_id': 'boss-identity-redeem'}, format='json'))
        for identity, expected in [('BOSS', {boss_claim['id']}), ('PROVIDER', set(ids))]:
            result = ok(admin.get('/api/admin/club-claims/', {'account_type': identity, 'page_size': 100}))
            self.assertEqual({row['id'] for row in result['list']}, expected)
            self.assertTrue(all(row['account_type'] == identity for row in result['list']))
        if os.environ.get('XA_ADMIN_CLAIM_IDENTITIES') == '1':
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/admin-claim-identities-375.cjs')], cwd=repo,
                input=json.dumps({'base': self.live_server_url, 'root': os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                    'token': issue_account_tokens(staff_account)[0], 'bossId': boss_claim['id'], 'providerId': ids[-1]}),
                text=True, encoding='utf-8', capture_output=True, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if os.environ.get('XA_PROVIDER_CLAIM_BROWSER') == '1':
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/provider-claim-pages-375.cjs')], cwd=repo,
                input=json.dumps({'base': self.live_server_url, 'root': os.environ['XA_TEAM_GIFT_PROVIDER_ROOT'],
                    'token': token, 'staffToken': issue_account_tokens(staff_account)[0],
                    'accountId': account.pk, 'oldest': ids[0], 'secondOldest': ids[1],
                    'dropRedeemResponse': os.environ.get('XA_DROP_REDEEM_RESPONSE') == '1'}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(ok(client.get(f'/api/club/claims/{ids[0]}/'))['details']['game_uid'], 'provider-375')
            self.assertEqual(ok(client.get(f'/api/club/claims/{ids[1]}/'))['details']['game_uid'], '未保存的UID')
            self.assertEqual(ok(client.get(f'/api/club/claims/{ids[1]}/'))['status'], 'CANCELLED')
            self.assertEqual(ok(client.get('/api/club/points/'))['points'], 3)
            new_claim = RewardClaim.objects.filter(account=account,reward=reward).exclude(pk__in=ids).get()
            self.assertEqual(new_claim.details['game_uid'], '新兑换UID')
            self.assertEqual(new_claim.details['note'], '新兑换说明')
            physical_claim = RewardClaim.objects.get(account=account,reward=physical)
            self.assertEqual({key:physical_claim.details[key] for key in ('recipient','phone','address')},
                {'recipient':'收件陪玩','phone':'13800138000','address':'测试收货地址'})
            self.assertEqual(ok(admin.get(f'/api/admin/club-claims/{physical_claim.pk}/'))['details']['address'], '测试收货地址')
