"""Provider TAG moderation drives the real boss and provider display."""
import os
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig, MemberTag, RewardClaim, RewardItem
from orders.tests.factories import make_console_user, make_provider, make_user


@skipUnless(os.environ.get('XA_TAG_NATIVE_REALTIME') == '1', 'Optional TAG moderation display scenario')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderTagBossRealtimeTest(LiveServerTestCase):
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
        boss_client = APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[1])[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        def titles():
            public = [tag['title'] for tag in ok(boss_client.get(f'/api/users/escorts/{accounts[0].pk}/'))['display_tags']]
            own = [tag['title'] for tag in ok(provider_client.get('/api/users/escorts/me/'))['display_tags']]
            self.assertEqual(own, public)
            return public
        self.assertEqual(titles(), ['标签丁','标签丙','标签乙'])
        ok(staff_client.post(f'/api/admin/club-claims/{ids[-1]}/suspend-tag/',
            {'reason':'内容需重新核实','expected_tag':'标签丁'},format='json'))
        self.assertEqual(titles(), ['标签丙','标签乙','标签甲'])
        ok(staff_client.post(f'/api/admin/club-claims/{ids[-1]}/review-tag/',
            {'tag_reviewed':True,'expected_tag':'标签丁'},format='json'))
        self.assertEqual(titles(), ['标签丁','标签丙','标签乙'])
        expired_at = timezone.now() - timedelta(seconds=1)
        RewardClaim.objects.filter(pk=ids[-1]).update(expires_at=expired_at)
        MemberTag.objects.filter(event_key=f'claim:{ids[-1]}:tag').update(expires_at=expired_at)
        self.assertEqual(titles(), ['标签丙','标签乙','标签甲'])

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
                     'extra':{'guild_id':'isolated-tag', 'author':{'username':'陪玩','bot':False}}}
            self.assertTrue(process_event(event, now))
        reward = RewardItem.objects.create(code='tag-native-live', name='陪玩标签', kind='TAG', audience='PROVIDER',
            point_cost=1, duration_days=7, in_shop=True, is_active=True)
        claim = client.post('/api/club/redeem/', {'reward_id':reward.pk, 'details':{'tag':'海洋搭子'},
            'client_request_id':'native-tag-live'}, format='json').data
        self.assertEqual(claim['code'], 0, claim)
        claim_id = claim['data']['id']
        boss_client, staff_client = APIClient(), APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[1])[0])
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        def titles():
            public = [tag['title'] for tag in ok(boss_client.get(f'/api/users/escorts/{accounts[0].pk}/'))['display_tags']]
            own = [tag['title'] for tag in ok(client.get('/api/users/escorts/me/'))['display_tags']]
            self.assertEqual(own, public)
            return public
        url = f'/api/club/claims/{claim_id}/'
        self.assertEqual(titles(), [])
        ok(staff_client.post(f'/api/admin/club-claims/{claim_id}/process/',
            {'status':'COMPLETED','note':'已审核','tag_reviewed':True},format='json'))
        self.assertEqual(titles(), ['海洋搭子'])
        ok(staff_client.post(f'/api/admin/club-claims/{claim_id}/suspend-tag/',
            {'reason':'需重新核实','expected_tag':'海洋搭子'},format='json'))
        self.assertEqual(titles(), [])
        ok(client.patch(url, {'details':{'tag':'海岸搭子'}}, format='json'))
        self.assertEqual(titles(), [])
        ok(staff_client.post(f'/api/admin/club-claims/{claim_id}/review-tag/',
            {'tag_reviewed':True,'expected_tag':'海岸搭子'},format='json'))
        self.assertEqual(titles(), ['海岸搭子'])
        expired_at = timezone.now() - timedelta(seconds=1)
        RewardClaim.objects.filter(pk=claim_id).update(expires_at=expired_at)
        MemberTag.objects.filter(event_key=f'claim:{claim_id}:tag').update(expires_at=expired_at)
        self.assertEqual(titles(), [])