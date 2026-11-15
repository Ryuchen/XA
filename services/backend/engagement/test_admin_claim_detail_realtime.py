"""A live admin dialog follows a boss's real delivery-details edit."""
import json
import os
from pathlib import Path
import subprocess
from datetime import timedelta
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import KookPointsConfig, RewardItem
from engagement.kook_points import process_event
from orders.tests.factories import make_console_user, make_user, make_provider
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@skipUnless(os.environ.get('XA_ADMIN_CLAIM_DETAIL_LIVE')=='1','Requires built admin and isolated Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AdminClaimDetailRealtimeTest(LiveServerTestCase):
    def test_provider_tag_edit_resets_review_confirmation_before_boss_sees_it(self):
        provider,boss,staff=make_provider(is_verified=True),make_user(),make_console_user(['dashboard:view','engagement:view','engagement:fulfill'])
        accounts=[get_or_create_account_for_legacy_user(user) for user in (provider,boss,staff)]
        provider.escort_profile.account=accounts[0];provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account=accounts[2];staff.admin_membership.save(update_fields=['account'])
        client=APIClient();client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(accounts[0])[0])
        now=timezone.now();KookPointsConfig.objects.create(pk=1,enabled=True,guild_id='tag-admin-live',checkin_channel_id='hall',voice_channel_ids=[],started_at=now-timedelta(days=1))
        binding=client.post('/api/club/kook-binding/',{},format='json').data;self.assertEqual(binding['code'],0,binding)
        for content,ident in [('绑定 '+binding['data']['code'],'tag-admin-bind'),('签到','tag-admin-checkin')]:
            event={'type':1,'channel_type':'GROUP','target_id':'hall','author_id':'provider','msg_id':ident,'msg_timestamp':int(now.timestamp()*1000),'content':content,'extra':{'guild_id':'tag-admin-live','author':{'username':'陪玩','bot':False}}}
            self.assertTrue(process_event(event,now))
        reward=RewardItem.objects.create(code='admin-live-tag',name='陪玩标签',kind='TAG',audience='PROVIDER',point_cost=1,duration_days=7,in_shop=True,is_active=True)
        result=client.post('/api/club/redeem/',{'reward_id':reward.pk,'details':{'tag':'原始标签'},'client_request_id':'admin-live-tag'},format='json');self.assertEqual(result.data['code'],0,result.data)
        with isolated_redis_asgi() as (base,ws_base):
            root=Path(settings.BASE_DIR).parent.parent
            result=subprocess.run(['node',str(root/'scripts/admin-claim-detail-realtime-live.cjs')],cwd=root,input=json.dumps({'base':base,'wsBase':ws_base,'root':os.environ['XA_ADMIN_VUE_ROOT'],'tag':True,'claim':result.data['data']['id'],'ownerId':accounts[0].pk,'ownerToken':issue_account_tokens(accounts[0])[0],'bossToken':issue_account_tokens(accounts[1])[0],'staffToken':issue_account_tokens(accounts[2])[0]}),encoding='utf-8',text=True,capture_output=True,timeout=35)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_open_dialog_refreshes_boss_details_and_preserves_staff_note(self):
        boss,staff=make_user(),make_console_user(['engagement:view','engagement:fulfill','engagement:points'])
        accounts=[get_or_create_account_for_legacy_user(user) for user in (boss,staff)]
        staff.admin_membership.account=accounts[1];staff.admin_membership.save(update_fields=['account'])
        clients=[APIClient(),APIClient()]
        for client,account in zip(clients,accounts):client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        credit=clients[1].post('/api/admin/external-points/',{'account_id':accounts[0].pk,'kind':'KOOK_CHECKIN','day':timezone.localdate().isoformat(),'source_id':'admin-live-claim'},format='json')
        self.assertEqual(credit.data['code'],0,credit.data)
        reward=RewardItem.objects.create(code='admin-live-claim',name='实时交付权益',kind='SERVICE',audience='ALL',point_cost=1,stock=1,in_shop=True,is_active=True)
        claim=clients[0].post('/api/club/redeem/',{'reward_id':reward.pk,'details':{'game_uid':'原游戏 UID'},'client_request_id':'admin-live-claim'},format='json')
        self.assertEqual(claim.data['code'],0,claim.data)
        with isolated_redis_asgi() as (base,ws_base):
            root=Path(settings.BASE_DIR).parent.parent
            result=subprocess.run(['node',str(root/'scripts/admin-claim-detail-realtime-live.cjs')],cwd=root,input=json.dumps({'base':base,'wsBase':ws_base,'root':os.environ['XA_ADMIN_VUE_ROOT'],'claim':claim.data['data']['id'],'bossToken':issue_account_tokens(accounts[0])[0],'staffToken':issue_account_tokens(accounts[1])[0]}),encoding='utf-8',text=True,capture_output=True,timeout=35)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
