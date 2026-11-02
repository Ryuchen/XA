"""Real staff claim notices and redemption keep their selected record in Vue."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig, RewardClaim, RewardItem
from orders.tests.factories import make_console_user, make_provider, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueClaimNavigationHTTPTest(LiveServerTestCase):
    def exercise(self, lost=False):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root: self.skipTest('requires isolated provider Vue build')
        boss, provider, staff = make_user(), make_provider(), make_console_user(['engagement:points', 'engagement:fulfill'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts): link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        clients = [APIClient(), APIClient(), APIClient()]; tokens = [issue_account_tokens(account)[0] for account in accounts]
        for client, token in zip(clients, tokens): client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        ok(clients[2].post('/api/admin/external-points/', {'account_id':accounts[0].pk, 'kind':'KOOK_CHECKIN',
            'day':timezone.localdate().isoformat(), 'source_id':'claim-route-boss'}, format='json'))
        now = timezone.now()
        KookPointsConfig.objects.create(pk=1, enabled=True, guild_id='isolated-guild',
            checkin_channel_id='isolated-hall', voice_channel_ids=[], started_at=now-timedelta(days=1))
        code = ok(clients[1].post('/api/club/kook-binding/', {}, format='json'))['code']
        def event(content, event_id):
            return {'type':1, 'channel_type':'GROUP', 'target_id':'isolated-hall', 'author_id':'isolated-provider',
                'msg_id':event_id, 'msg_timestamp':int(now.timestamp()*1000), 'content':content,
                'extra':{'guild_id':'isolated-guild', 'author':{'username':'隔离陪玩', 'bot':False}}}
        self.assertTrue(process_event(event('绑定 ' + code, 'bind'), now))
        self.assertTrue(process_event(event('签到', 'checkin'), now))
        a = RewardItem.objects.create(code='provider-claim-route-a', name='已交付权益甲', kind='SERVICE', audience='PROVIDER', point_cost=1, in_shop=True, is_active=True)
        b = RewardItem.objects.create(code='provider-claim-route-b', name='待兑换权益乙', kind='SERVICE', audience='PROVIDER', point_cost=1, in_shop=True, is_active=True)
        boss_reward = RewardItem.objects.create(code='boss-claim-route', name='老板专属权益', kind='SERVICE', audience='BOSS', point_cost=1, in_shop=True, is_active=True)
        old = ok(clients[1].post('/api/club/redeem/', {'reward_id':a.pk, 'client_request_id':'claim-route-a'}, format='json'))
        boss_claim = ok(clients[0].post('/api/club/redeem/', {'reward_id':boss_reward.pk, 'client_request_id':'claim-route-boss'}, format='json'))
        ok(clients[2].post(f"/api/admin/club-claims/{old['id']}/process/", {'status':'COMPLETED','note':'甲权益已交付'}, format='json'))
        notices = ok(clients[1].get('/api/messages/'))['messages']
        notice = next(row for row in notices if row['title']=='权益交付进度更新')
        self.assertEqual(notice['action_url'], f"/pages/reward-claim/index?id={old['id']}")
        fixed = os.environ.get('XA_CLAIM_ROUTE_FIXED') == '1'
        repo = Path(settings.BASE_DIR).parent.parent
        try:
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-claim-navigation-375.cjs')],
                input=json.dumps({'root':root, 'base':self.live_server_url, 'token':tokens[1], 'id':accounts[1].pk,
                    'oldId':old['id'], 'fixed':fixed, 'lost':lost}), text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        except subprocess.TimeoutExpired as error:
            self.fail(f'Browser timed out; stdout={error.stdout!r}; stderr={error.stderr!r}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        new = RewardClaim.objects.get(account=accounts[1], reward=b)
        self.assertEqual(new.status, 'PENDING')
        self.assertEqual(ok(clients[1].get(f'/api/club/claims/{new.pk}/'))['name_snapshot'], '待兑换权益乙')
        self.assertEqual(ok(clients[2].get(f'/api/admin/club-claims/{new.pk}/'))['account_type'], 'PROVIDER')
        self.assertEqual(ok(clients[0].get(f"/api/club/claims/{boss_claim['id']}/"))['name_snapshot'], '老板专属权益')
        self.assertEqual(clients[0].get(f'/api/club/claims/{new.pk}/').status_code, 404)
        self.assertEqual(ok(clients[1].get('/api/club/points/'))['points'], 3)

    def test_notice_all_records_and_new_redemption(self):
        self.exercise()

    def test_lost_redemption_response_replays_same_record(self):
        if os.environ.get('XA_CLAIM_ROUTE_FIXED') != '1':
            self.skipTest('requires updated route')
        self.exercise(lost=True)
