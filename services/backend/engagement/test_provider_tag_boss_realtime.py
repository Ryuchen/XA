"""Open native companion screens follow actual provider TAG moderation."""
import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig, MemberTag, RewardClaim, RewardItem
from orders.tests.factories import make_console_user, make_provider, make_user
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@skipUnless(os.environ.get('XA_TAG_NATIVE_REALTIME') == '1', 'Requires isolated Redis and ASGI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderTagBossRealtimeTest(LiveServerTestCase):
    @skipUnless(os.environ.get('XA_PROVIDER_MINE_TAG_LIVE') == '1', 'Requires built provider app and browser')
    def test_four_tags_fall_back_across_open_boss_and_provider_pages(self):
        provider, boss = make_provider(display_name='标签陪玩', is_verified=True), make_user()
        staff = make_console_user(['engagement:fulfill'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (provider, boss, staff)]
        provider.escort_profile.account = accounts[0]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        provider_client, staff_client = APIClient(), APIClient()
        provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        now = timezone.now()
        KookPointsConfig.objects.create(pk=1, enabled=True, guild_id='fallback', checkin_channel_id='hall', voice_channel_ids=[], started_at=now-timedelta(days=1))
        binding = provider_client.post('/api/club/kook-binding/', {}, format='json').data
        self.assertEqual(binding['code'], 0, binding)
        for content, ident in [('绑定 ' + binding['data']['code'], 'fallback-bind'), ('签到', 'fallback-checkin')]:
            self.assertTrue(process_event({'type':1,'channel_type':'GROUP','target_id':'hall','author_id':'provider',
                'msg_id':ident,'msg_timestamp':int(now.timestamp()*1000),'content':content,
                'extra':{'guild_id':'fallback','author':{'username':'陪玩','bot':False}}}, now))
        reward = RewardItem.objects.create(code='tag-fallback', name='陪玩标签', kind='TAG', audience='PROVIDER',
            point_cost=1, duration_days=7, in_shop=True, is_active=True)
        ids = []
        for title in ['标签甲','标签乙','标签丙','标签丁']:
            claim = provider_client.post('/api/club/redeem/', {'reward_id':reward.pk,'details':{'tag':title},
                'client_request_id':'fallback-'+title},format='json').data
            self.assertEqual(claim['code'],0,claim); ids.append(claim['data']['id'])
            approved = staff_client.post(f"/api/admin/club-claims/{ids[-1]}/process/",
                {'status':'COMPLETED','note':'已审核','tag_reviewed':True},format='json').data
            self.assertEqual(approved['code'],0,approved)
        with isolated_redis_asgi() as (base, ws_base):
            expiry = timezone.now() + timedelta(seconds=28)
            RewardClaim.objects.filter(pk=ids[-1]).update(expires_at=expiry)
            MemberTag.objects.filter(event_key=f'claim:{ids[-1]}:tag').update(expires_at=expiry)
            repo = Path(settings.BASE_DIR).parent.parent
            sync = TemporaryDirectory(prefix='xa-tag-observers-'); self.addCleanup(sync.cleanup)
            common = {'base':base,'wsBase':ws_base,'fallbackOnly':True,'claimId':ids[-1],'syncDir':sync.name,
                'providerId':accounts[0].pk,'providerToken':issue_account_tokens(accounts[0])[0],
                'bossId':accounts[1].pk,'bossToken':issue_account_tokens(accounts[1])[0],
                'staffToken':issue_account_tokens(accounts[2])[0]}
            mine = subprocess.Popen(['node',str(repo/'scripts/provider-mine-tag-live.cjs')],cwd=repo,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
            try:
                mine.stdin.write(json.dumps({**common,'root':os.environ['XA_PROVIDER_VUE_ROOT']})); mine.stdin.close()
                self.assertEqual(mine.stdout.readline().strip(),'READY')
                native = subprocess.run(['node',str(repo/'scripts/native-provider-tags-realtime.cjs')],cwd=repo,
                    input=json.dumps(common),text=True,encoding='utf-8',capture_output=True,timeout=60)
                self.assertEqual(native.returncode,0,native.stdout+native.stderr)
                mine.wait(timeout=20)
                self.assertEqual(mine.returncode,0,mine.stdout.read()+mine.stderr.read())
            finally:
                if mine.poll() is None: mine.kill(); mine.wait()

    def test_open_list_and_detail_follow_review_suspend_and_revision(self):
        media = TemporaryDirectory(prefix='xa-tag-native-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        provider, boss = make_provider(display_name='标签陪玩', is_verified=True), make_user()
        staff = make_console_user(['engagement:fulfill'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (provider,boss,staff)]
        provider.escort_profile.account = accounts[0]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        now = timezone.now()
        KookPointsConfig.objects.create(pk=1, enabled=True, guild_id='isolated-tag', checkin_channel_id='hall',
            voice_channel_ids=[], started_at=now-timedelta(days=1))
        binding = client.post('/api/club/kook-binding/', {}, format='json').data
        self.assertEqual(binding['code'], 0, binding)
        for content, ident in [('绑定 ' + binding['data']['code'], 'binding'), ('签到', 'checkin')]:
            event = {'type':1, 'channel_type':'GROUP', 'target_id':'hall', 'author_id':'provider',
                     'msg_id':ident, 'msg_timestamp':int(now.timestamp()*1000), 'content':content,
                     'extra':{'guild_id':'isolated-tag', 'author':{'username':'陪玩', 'bot':False}}}
            self.assertTrue(process_event(event, now))
        reward = RewardItem.objects.create(code='tag-native-live', name='陪玩标签', kind='TAG', audience='PROVIDER',
            point_cost=1, duration_days=7, in_shop=True, is_active=True)
        claim = client.post('/api/club/redeem/', {'reward_id':reward.pk, 'details':{'tag':'海洋搭子'},
            'client_request_id':'native-tag-live'}, format='json').data
        self.assertEqual(claim['code'], 0, claim)
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo/'scripts/native-provider-tags-realtime.cjs')], cwd=repo,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'claimId':claim['data']['id'],
                    'providerId':accounts[0].pk, 'providerToken':issue_account_tokens(accounts[0])[0],
                    'bossId':accounts[1].pk, 'bossToken':issue_account_tokens(accounts[1])[0],
                    'staffToken':issue_account_tokens(accounts[2])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if os.environ.get('XA_PROVIDER_MINE_TAG_LIVE') == '1':
                next_claim = client.post('/api/club/redeem/', {'reward_id':reward.pk, 'details':{'tag':'海蓝搭子'},
                    'client_request_id':'provider-mine-tag-live'}, format='json').data
                self.assertEqual(next_claim['code'], 0, next_claim)
                result = subprocess.run(['node', str(repo/'scripts/provider-mine-tag-live.cjs')], cwd=repo,
                    input=json.dumps({'base':base,'wsBase':ws_base,'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                        'claimId':next_claim['data']['id'], 'providerId':accounts[0].pk,
                        'providerToken':issue_account_tokens(accounts[0])[0], 'staffToken':issue_account_tokens(accounts[2])[0]}),
                    text=True, encoding='utf-8', capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                mine_expiry = timezone.now() + timedelta(seconds=7)
                RewardClaim.objects.filter(pk=next_claim['data']['id']).update(expires_at=mine_expiry)
                MemberTag.objects.filter(event_key=f"claim:{next_claim['data']['id']}:tag").update(expires_at=mine_expiry)
                result = subprocess.run(['node', str(repo/'scripts/provider-mine-tag-live.cjs')], cwd=repo,
                    input=json.dumps({'base':base,'wsBase':ws_base,'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                        'expiryOnly':True,'providerId':accounts[0].pk,'providerToken':issue_account_tokens(accounts[0])[0]}),
                    text=True, encoding='utf-8', capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            # Make the real issued TAG expire shortly while both native controllers stay open.
            expiry = timezone.now() + timedelta(seconds=6)
            RewardClaim.objects.filter(pk=claim['data']['id']).update(expires_at=expiry)
            MemberTag.objects.filter(event_key=f"claim:{claim['data']['id']}:tag").update(expires_at=expiry)
            result = subprocess.run(['node', str(repo/'scripts/native-provider-tags-realtime.cjs')], cwd=repo,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'expiryOnly':True,
                    'providerId':accounts[0].pk, 'bossId':accounts[1].pk,
                    'bossToken':issue_account_tokens(accounts[1])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
