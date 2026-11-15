"""Provider page follows actual redemption and cancellation via Redis/WebSocket."""
import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig, RewardItem
from orders.tests.factories import make_console_user, make_provider, make_user
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@skipUnless(os.environ.get('XA_PROVIDER_POINTS_LIVE') == '1', 'Requires built provider app and isolated Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderPointsRealtimeLiveTest(LiveServerTestCase):
    def test_provider_redeem_and_staff_cancel_refresh_open_page(self):
        provider, staff = make_provider(), make_console_user(['engagement:fulfill','engagement:edit','engagement:points'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        provider.escort_profile.account=accounts[0]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account=accounts[1]; staff.admin_membership.save(update_fields=['account'])
        client=APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(accounts[0])[0])
        boss=get_or_create_account_for_legacy_user(make_user())
        admin=APIClient(); admin.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(accounts[1])[0])
        credit=admin.post('/api/admin/external-points/',{'account_id':boss.pk,'kind':'KOOK_CHECKIN',
            'day':timezone.localdate().isoformat(),'source_id':'catalog-boss-credit'},format='json')
        self.assertEqual(credit.data['code'],0,credit.data)
        now=timezone.now(); KookPointsConfig.objects.create(pk=1,enabled=True,guild_id='points-live',
            checkin_channel_id='hall',voice_channel_ids=[],started_at=now-timedelta(days=1))
        binding=client.post('/api/club/kook-binding/',{},format='json').data
        self.assertEqual(binding['code'],0,binding)
        for content, ident in [('绑定 '+binding['data']['code'],'points-bind'),('签到','points-checkin')]:
            event={'type':1,'channel_type':'GROUP','target_id':'hall','author_id':'provider',
                'msg_id':ident,'msg_timestamp':int(now.timestamp()*1000),'content':content,
                'extra':{'guild_id':'points-live','author':{'username':'陪玩','bot':False}}}
            self.assertTrue(process_event(event,now))
        reward=RewardItem.objects.create(code='provider-points-live',name='实时交付权益',kind='SERVICE',
            audience='ALL',point_cost=1,stock=1,in_shop=True,is_active=True)
        balance_reward=RewardItem.objects.create(code='provider-balance-confirm',name='余额权益',kind='BALANCE',
            audience='ALL',point_cost=1,balance_amount=123,stock=1,in_shop=True,is_active=True)
        tag_reward=RewardItem.objects.create(code='provider-return-tag',name='退回修改标签',kind='TAG',
            audience='PROVIDER',point_cost=1,duration_days=7,stock=1,in_shop=True,is_active=True)
        with isolated_redis_asgi() as (base,ws_base):
            root=Path(settings.BASE_DIR).parent.parent
            result=subprocess.run(['node',str(root/'scripts/provider-points-realtime-live.cjs')],cwd=root,
                input=json.dumps({'base':base,'wsBase':ws_base,'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                    'account':accounts[0].pk,'token':issue_account_tokens(accounts[0])[0],
                    'staffToken':issue_account_tokens(accounts[1])[0],'reward':reward.pk,
                    'bossId':boss.pk,'bossToken':issue_account_tokens(boss)[0],'balanceReward':balance_reward.pk,
                    'tagReward':tag_reward.pk}),
                encoding='utf-8',text=True,capture_output=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(client.get('/api/club/points/').data['data']['points'],4)
