"""Real provider redemption and staff cancellation while its Vue page stays open."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig, RewardItem
from orders.tests.factories import make_console_user, make_provider, make_user
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueClaimCancelRealtimeTest(APITransactionTestCase):
    def test_staff_cancellation_refreshes_points_stock_and_loaded_claims_without_losing_draft(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['engagement:fulfill'])
        users = (boss, provider, staff)
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        clients = [APIClient() for _ in users]
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        for client, token in zip(clients, tokens):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        now = timezone.now()
        KookPointsConfig.objects.create(pk=1, enabled=True, guild_id='isolated-claim-guild',
            checkin_channel_id='isolated-claim-hall', voice_channel_ids=[], started_at=now-timedelta(days=8))
        code = ok(clients[1].post('/api/club/kook-binding/', {}, format='json'))['code']
        def event(content, event_id, at):
            return {'type':1, 'channel_type':'GROUP', 'target_id':'isolated-claim-hall', 'author_id':'isolated-claim-provider',
                'msg_id':event_id, 'msg_timestamp':int(at.timestamp()*1000), 'content':content,
                'extra':{'guild_id':'isolated-claim-guild', 'author':{'username':'隔离陪玩', 'bot':False}}}
        self.assertTrue(process_event(event('绑定 ' + code, 'claim-bind', now), now))
        for day in range(5):
            self.assertTrue(process_event(event('签到', f'claim-checkin-{day}', now-timedelta(days=day)), now))
        target = RewardItem.objects.create(code='isolated-claim-cancel', name='限定库存权益', kind='SERVICE',
            audience='ALL', point_cost=1, stock=1, in_shop=True, is_active=True)
        old = []
        for index in range(21):
            historical = RewardItem.objects.create(code=f'isolated-claim-history-{index:02}', name=f'历史兑换 {index:02}',
                kind='SERVICE', audience='PROVIDER', point_cost=1, in_shop=True, is_active=True)
            old.append(ok(clients[1].post('/api/club/redeem/', {'reward_id':historical.pk,
                'client_request_id':f'claim-history-{index:03}'}, format='json')))
        claim = ok(clients[1].post('/api/club/redeem/', {'reward_id':target.pk,
            'client_request_id':'claim-cancel-target'}, format='json'))
        self.assertEqual(ok(clients[1].get('/api/club/points/'))['points'], 3)
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_realtime() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-claim-cancel-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'claimId':claim['id'],
                    'oldId':old[0]['id'], 'targetId':target.pk, 'id':accounts[1].pk, 'token':tokens[1],
                    'bossToken':tokens[0], 'staffToken':tokens[2],
                    'fixed':os.environ.get('XA_CLAIM_CANCEL_FIXED') == '1'}),
                text=True, encoding='utf-8', capture_output=True, cwd=repo, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
