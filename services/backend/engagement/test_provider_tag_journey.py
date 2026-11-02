"""A provider earns points, redeems a TAG, and receives real public display."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig, MemberTag, RewardClaim, RewardItem
from orders.tests.factories import make_console_user, make_provider, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderTagJourneyTest(LiveServerTestCase):
    host, port = '127.0.0.1', 19210

    def test_tag_redemption_appears_until_expiry(self):
        provider, boss = make_provider(display_name='标签陪玩', is_verified=True, deposit_mode='CASH'), make_user()
        staff = make_console_user(['engagement:fulfill'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (provider, boss, staff)]
        provider.escort_profile.account = accounts[0]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        clients = []
        for account in accounts:
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0]); clients.append(client)
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        now = timezone.now()
        KookPointsConfig.objects.create(pk=1, enabled=True, guild_id='tag-guild', checkin_channel_id='tag-hall',
            voice_channel_ids=[], started_at=now-timedelta(days=1))
        code = ok(clients[0].post('/api/club/kook-binding/', {}, format='json'))['code']
        for content, ident in [('绑定 '+code, 'tag-bind'), ('签到', 'tag-checkin')]:
            # Only the external KOOK event is simulated.
            event = {'type':1,'channel_type':'GROUP','target_id':'tag-hall','author_id':'tag-provider',
                'msg_id':ident,'msg_timestamp':int(now.timestamp()*1000),'content':content,
                'extra':{'guild_id':'tag-guild','author':{'username':'标签陪玩','bot':False}}}
            self.assertTrue(process_event(event, now))
        reward = RewardItem.objects.create(code='provider-tag-journey', name='陪玩专属TAG', kind='TAG',
            audience='PROVIDER', point_cost=1, duration_days=1, highlighted=True, in_shop=True, is_active=True)
        if os.environ.get('XA_PROVIDER_TAG_UI') == '1':
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo/'scripts/provider-tag-journey-375.cjs')], cwd=repo,
                input=json.dumps({'base':self.live_server_url, 'root':os.environ['XA_TEAM_GIFT_PROVIDER_ROOT'],
                    'adminRoot':os.environ['XA_TEAM_GIFT_ADMIN_ROOT'], 'providerId':accounts[0].pk,'providerToken':issue_account_tokens(accounts[0])[0],
                    'bossToken':issue_account_tokens(accounts[1])[0], 'bossId':str(accounts[1].pk),
                    'staffToken':issue_account_tokens(accounts[2])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        else:
            role = staff.admin_membership.roles.first()
            role.permissions = [*role.permissions, 'dashboard:view']
            role.save(update_fields=['permissions'])
            claim = ok(clients[0].post('/api/club/redeem/', {'reward_id':reward.pk,'details':{'tag':''},
                'client_request_id':'provider-tag-journey'}, format='json'))
            url = f"/api/club/claims/{claim['id']}/"
            process = f"/api/admin/club-claims/{claim['id']}/process/"
            def check_ready(count):
                self.assertEqual(ok(clients[2].get('/api/admin/dashboard/stats'))['ready_tag_review_total'],count)
                queue = ok(clients[2].get('/api/admin/club-claims/', {'tag_ready_for_review':'1'}))
                self.assertEqual(queue['total'],count)
            check_ready(0)
            ok(clients[0].patch(url, {'details':{'tag':'原标签'}}, format='json'))
            check_ready(1)
            if os.environ.get('XA_PROVIDER_TAG_QUEUE_UI') == '1':
                repo = Path(settings.BASE_DIR).parent.parent
                dashboard = subprocess.run(['node',str(repo/'scripts/dashboard-tag-review-375.cjs')],cwd=repo,
                    input=json.dumps({'base':self.live_server_url,'root':os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                        'staffToken':issue_account_tokens(accounts[2])[0], 'claimId':claim['id'], 'ready':True}),
                    text=True,encoding='utf-8',capture_output=True,timeout=45)
                self.assertEqual(dashboard.returncode,0,dashboard.stdout+dashboard.stderr)
            ok(clients[2].post(process, {'status':'FULFILLING','note':'正在核对'}, format='json'))
            check_ready(1)
            returned = ok(clients[2].post(process, {'status':'PENDING','note':'请修改标签'}, format='json'))
            self.assertTrue(returned['tag_revision_required'])
            check_ready(0)
            locked = clients[2].post(process, {'status':'FULFILLING','note':'开始处理'}, format='json')
            self.assertEqual(locked.status_code, 400)
            unchanged = ok(clients[0].patch(url, {'details':{'tag':'原标签','note':'更新备注'}}, format='json'))
            self.assertTrue(unchanged['tag_revision_required'])
            check_ready(0)
            revised = ok(clients[0].patch(url, {'details':{'tag':'海洋搭子'}}, format='json'))
            self.assertFalse(revised['tag_revision_required'])
            check_ready(1)
            ok(clients[2].post(process, {'status':'COMPLETED','note':'已发放','tag_reviewed':True}, format='json'))
            check_ready(0)
        claim = RewardClaim.objects.get(account=accounts[0],reward=reward)
        self.assertEqual((claim.status, claim.details['tag']), ('COMPLETED', '海洋搭子'))
        matches = ok(clients[2].get('/api/admin/club-claims/', {'search':'海洋搭子','kind':'TAG'}))
        self.assertEqual([row['id'] for row in matches['list']], [claim.pk])
        self.assertEqual(ok(clients[2].get('/api/admin/club-claims/', {'search':'海洋搭子','kind':'SERVICE'}))['total'],0)
        # An old delivered TAG predating recorded moderation must be reviewed again.
        MemberTag.objects.filter(event_key=f'claim:{claim.pk}:tag').update(reviewed_at=None,reviewed_by=None)
        self.assertEqual(ok(clients[1].get(f'/api/users/escorts/{accounts[0].pk}/'))['display_tags'], [])
        queue = ok(clients[2].get('/api/admin/club-claims/', {'needs_tag_review':'1'}))
        self.assertEqual([row['id'] for row in queue['list']], [claim.pk])
        unconfirmed = clients[2].post(f'/api/admin/club-claims/{claim.pk}/review-tag/',
            {'expected_tag':'海洋搭子'}, format='json')
        self.assertEqual(unconfirmed.status_code,400)
        stale = clients[2].post(f'/api/admin/club-claims/{claim.pk}/review-tag/',
            {'tag_reviewed':True,'expected_tag':'旧文字'}, format='json')
        self.assertEqual(stale.status_code,409)
        if os.environ.get('XA_PROVIDER_TAG_UI') == '1':
            repo = Path(settings.BASE_DIR).parent.parent
            review = subprocess.run(['node',str(repo/'scripts/admin-historical-tag-review-375.cjs')],cwd=repo,
                input=json.dumps({'base':self.live_server_url,'root':os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                    'staffToken':issue_account_tokens(accounts[2])[0], 'claimId':claim.pk}),
                text=True,encoding='utf-8',capture_output=True,timeout=60)
            self.assertEqual(review.returncode,0,review.stdout+review.stderr)
        else:
            ok(clients[2].post(f'/api/admin/club-claims/{claim.pk}/review-tag/',
                {'tag_reviewed':True,'expected_tag':'海洋搭子'},format='json'))
        tag = MemberTag.objects.get(event_key=f'claim:{claim.pk}:tag')
        self.assertEqual(tag.reviewed_by_id,accounts[2].pk)
        self.assertIsNotNone(tag.reviewed_at)
        reviewed_at = tag.reviewed_at
        ok(clients[2].post(f'/api/admin/club-claims/{claim.pk}/review-tag/',
            {'tag_reviewed':True,'expected_tag':'海洋搭子'},format='json'))
        tag.refresh_from_db()
        self.assertEqual(tag.reviewed_at, reviewed_at)
        notices = ok(clients[0].get('/api/messages/'))['messages']
        self.assertEqual(len([item for item in notices if item['title']=='标签审核通过']), 1)
        self.assertEqual(ok(clients[2].get('/api/admin/club-claims/', {'needs_tag_review':'1'}))['total'],0)
        own = ok(clients[0].get('/api/users/escorts/me/'))['display_tags']
        public = ok(clients[1].get(f'/api/users/escorts/{accounts[0].pk}/'))['display_tags']
        self.assertEqual(own, public)
        self.assertEqual([(tag['title'],tag['highlighted']) for tag in public], [('海洋搭子',True)])
        role = staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'dashboard:view']
        role.save(update_fields=['permissions'])
        for index in range(3):
            more = ok(clients[0].post('/api/club/redeem/', {'reward_id':reward.pk,'details':{'tag':f'新标签{index}'},
                'client_request_id':f'provider-tag-extra-{index}'}, format='json'))
            ok(clients[2].post(f"/api/admin/club-claims/{more['id']}/process/",
                {'status':'COMPLETED','note':'审核通过','tag_reviewed':True}, format='json'))
        visible = ok(clients[1].get(f'/api/users/escorts/{accounts[0].pk}/'))['display_tags']
        self.assertEqual([tag['title'] for tag in visible], ['新标签2','新标签1','新标签0'])
        self.assertEqual(len(visible),3)
        self.assertEqual(ok(clients[0].get('/api/users/escorts/me/'))['display_tags'],visible)
        self.assertEqual(RewardClaim.objects.filter(account=accounts[0],reward=reward).count(),4)
        newest = RewardClaim.objects.filter(account=accounts[0],reward=reward).order_by('-id').first()
        expiry_before = MemberTag.objects.get(event_key=f'claim:{newest.pk}:tag').expires_at
        if os.environ.get('XA_PROVIDER_TAG_UI') == '1':
            repo = Path(settings.BASE_DIR).parent.parent
            suspend = subprocess.run(['node',str(repo/'scripts/admin-tag-suspend-375.cjs')],cwd=repo,
                input=json.dumps({'base':self.live_server_url,'root':os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                    'staffToken':issue_account_tokens(accounts[2])[0], 'claimId':newest.pk}),
                text=True,encoding='utf-8',capture_output=True,timeout=60)
            self.assertEqual(suspend.returncode,0,suspend.stdout+suspend.stderr)
        else:
            ok(clients[2].post(f'/api/admin/club-claims/{newest.pk}/suspend-tag/',
                {'reason':'内容需要重新核实','expected_tag':'新标签2'},format='json'))
        ok(clients[2].post(f'/api/admin/club-claims/{newest.pk}/suspend-tag/',
            {'reason':'内容需要重新核实','expected_tag':'新标签2'},format='json'))
        changed = clients[2].post(f'/api/admin/club-claims/{newest.pk}/suspend-tag/',
            {'reason':'另一个原因','expected_tag':'新标签2'},format='json')
        self.assertEqual(changed.status_code,409)
        current = ok(clients[0].get(f'/api/club/claims/{newest.pk}/'))
        self.assertFalse(current['tag_reviewed'])
        self.assertFalse(current['tag_revision_submitted'])
        self.assertEqual(current['tag_suspension_reason'],'内容需要重新核实')
        if os.environ.get('XA_PROVIDER_TAG_UI') == '1':
            view = subprocess.run(['node',str(repo/'scripts/provider-tag-suspended-375.cjs')],cwd=repo,
                input=json.dumps({'base':self.live_server_url,'root':os.environ['XA_TEAM_GIFT_PROVIDER_ROOT'],
                    'token':issue_account_tokens(accounts[0])[0],'id':accounts[0].pk,'claimId':newest.pk}),
                text=True,encoding='utf-8',capture_output=True,timeout=45)
            self.assertEqual(view.returncode,0,view.stdout+view.stderr)
        else:
            edited = ok(clients[0].patch(f'/api/club/claims/{newest.pk}/',
                {'details':{'tag':'清海搭子'}}, format='json'))
            self.assertTrue(edited['tag_editable'])
        revised_claim = ok(clients[0].get(f'/api/club/claims/{newest.pk}/'))
        self.assertEqual(revised_claim['details']['tag'],'清海搭子')
        self.assertTrue(revised_claim['tag_revision_submitted'])
        self.assertTrue(ok(clients[2].get(f'/api/admin/club-claims/{newest.pk}/'))['tag_revision_submitted'])
        stored = MemberTag.objects.get(event_key=f'claim:{newest.pk}:tag')
        self.assertEqual((stored.title, stored.expires_at), ('新标签2', expiry_before))
        stale = clients[2].post(f'/api/admin/club-claims/{newest.pk}/review-tag/',
            {'tag_reviewed':True,'expected_tag':'新标签2'},format='json')
        self.assertEqual(stale.status_code,409)
        visible = ok(clients[1].get(f'/api/users/escorts/{accounts[0].pk}/'))['display_tags']
        self.assertEqual([tag['title'] for tag in visible], ['新标签1','新标签0','海洋搭子'])
        self.assertEqual(ok(clients[2].get('/api/admin/club-claims/', {'needs_tag_review':'1'}))['total'],1)
        self.assertEqual(ok(clients[2].get('/api/admin/dashboard/stats'))['pending_tag_review_total'],1)
        if os.environ.get('XA_PROVIDER_TAG_UI') == '1':
            dashboard = subprocess.run(['node',str(repo/'scripts/dashboard-tag-review-375.cjs')],cwd=repo,
                input=json.dumps({'base':self.live_server_url,'root':os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                    'staffToken':issue_account_tokens(accounts[2])[0], 'claimId':newest.pk}),
                text=True,encoding='utf-8',capture_output=True,timeout=45)
            self.assertEqual(dashboard.returncode,0,dashboard.stdout+dashboard.stderr)
        notices = ok(clients[0].get('/api/messages/'))['messages']
        self.assertEqual(len([item for item in notices if item['title']=='标签暂停展示']),1)
        if os.environ.get('XA_PROVIDER_TAG_UI') == '1':
            review = subprocess.run(['node',str(repo/'scripts/admin-historical-tag-review-375.cjs')],cwd=repo,
                input=json.dumps({'base':self.live_server_url,'root':os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                    'staffToken':issue_account_tokens(accounts[2])[0], 'claimId':newest.pk, 'suspended':True}),
                text=True,encoding='utf-8',capture_output=True,timeout=60)
            self.assertEqual(review.returncode,0,review.stdout+review.stderr)
        else:
            ok(clients[2].post(f'/api/admin/club-claims/{newest.pk}/review-tag/',
                {'tag_reviewed':True,'expected_tag':'清海搭子'},format='json'))
        self.assertFalse(ok(clients[0].get(f'/api/club/claims/{newest.pk}/'))['tag_revision_submitted'])
        newest_tag = MemberTag.objects.get(event_key=f'claim:{newest.pk}:tag')
        self.assertEqual((newest_tag.title,newest_tag.expires_at),('清海搭子',expiry_before))
        self.assertEqual([tag['title'] for tag in ok(clients[1].get(f'/api/users/escorts/{accounts[0].pk}/'))['display_tags']],
            ['清海搭子','新标签1','新标签0'])
        self.assertEqual(ok(clients[2].get('/api/admin/dashboard/stats'))['pending_tag_review_total'],0)
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(days=2)):
            self.assertEqual(ok(clients[0].get('/api/users/escorts/me/'))['display_tags'], [])
            self.assertEqual(ok(clients[1].get(f'/api/users/escorts/{accounts[0].pk}/'))['display_tags'], [])
