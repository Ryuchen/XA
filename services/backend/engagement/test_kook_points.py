from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from orders.tests.factories import make_user,make_provider
from engagement.tests import mapped
from engagement.models import KookBinding,KookPointsConfig,MemberProfile,PointLedger,RewardItem,RewardClaim
from engagement.kook_points import challenge,process_event,checkpoint,reset_presence
from engagement.services import redeem,record_external_points
from rest_framework.exceptions import ValidationError
from django.test import TransactionTestCase,skipUnlessDBFeature
from django.test import SimpleTestCase
from unittest.mock import patch
from engagement.management.commands.run_kook_points import KookAPIError,seed_presence,connection_status,attach_verified_presence


class KookGatewayPermissionTests(SimpleTestCase):
    def test_denied_channel_does_not_block_readable_channels_or_fabricate_presence(self):
        def read(token,path,**params):
            if params['channel_id']=='private':raise KookAPIError(path,40300)
            return [{'id':'member','bot':False},{'id':'bot','bot':True}]
        with patch('engagement.management.commands.run_kook_points.read_api',side_effect=read):
            presence,unreadable=seed_presence('test-token',['normal','private'])
        self.assertEqual(presence,{'member':'normal'})
        self.assertEqual(unreadable,['private'])
        self.assertIn('1/2',connection_status(['normal','private'],unreadable))
        self.assertEqual(connection_status(['normal','private'],[]),'已连接，自动计分中')

    def test_api_outage_is_not_misreported_as_empty_or_permission_denied(self):
        with patch('engagement.management.commands.run_kook_points.read_api',side_effect=KookAPIError('channel/user-list',42900)):
            with self.assertRaises(KookAPIError):seed_presence('test-token',['normal'])


class KookPointsTests(TestCase):
    def setUp(self):
        self.now=timezone.now()
        self.account=mapped(make_user())
        self.cfg=KookPointsConfig.objects.create(enabled=True,guild_id='guild',checkin_channel_id='hall',voice_channel_ids=['voice','sleep'],started_at=self.now-timedelta(seconds=1))
    def msg(self,content='签到',user='kook-user',id='m1',at=None):
        return {'type':1,'channel_type':'GROUP','target_id':'hall','author_id':user,'msg_id':id,
            'msg_timestamp':int((at or self.now).timestamp()*1000),'content':content,'extra':{'guild_id':'guild','author':{'username':'昵称','bot':False}}}
    def voice(self,kind='joined_channel',id='v1',at=None,channel='voice'):
        e=self.msg(id=id,at=at);e.update(type=255,target_id='guild',extra={'type':kind,'body':{'user_id':'kook-user','channel_id':channel}});return e
    def bind(self):return KookBinding.objects.create(account=self.account,user_id='kook-user')
    def points(self):return MemberProfile.objects.get(account=self.account).points_balance
    def test_only_real_owner_message_consumes_challenge(self):
        token=challenge(self.account);e=self.msg('绑定 '+token);e['extra']['author']['bot']=True
        self.assertFalse(process_event(e,self.now));self.assertFalse(KookBinding.objects.exists())
        e['extra']['author']['bot']=False;e['msg_id']='m2'
        self.assertTrue(process_event(e,self.now));self.assertTrue(KookBinding.objects.filter(account=self.account,user_id='kook-user').exists())
    def test_expired_proof_and_wrong_server_rejected(self):
        token=challenge(self.account)
        self.assertFalse(process_event(self.msg('绑定 '+token),self.now+timedelta(minutes=11)))
        e=self.msg('绑定 '+token,id='wrong');e['extra']['guild_id']='foreign'
        self.assertFalse(process_event(e,self.now));self.assertFalse(KookBinding.objects.exists())
    def test_same_kook_cannot_bind_two_accounts(self):
        self.bind();other=mapped(make_user());token=challenge(other)
        self.assertFalse(process_event(self.msg('绑定 '+token),self.now));self.assertEqual(KookBinding.objects.count(),1)
    def test_checkin_retry_and_multiple_messages_only_award_once(self):
        self.bind();e=self.msg();self.assertTrue(process_event(e,self.now));self.assertFalse(process_event(e,self.now))
        process_event(self.msg(id='m2'),self.now)
        self.assertEqual(self.points(),500);self.assertEqual(PointLedger.objects.count(),1)
    def test_checkin_next_day_uses_china_local_day(self):
        self.bind();process_event(self.msg(),self.now)
        tomorrow=self.now+timedelta(days=1);process_event(self.msg(id='m2',at=tomorrow),tomorrow)
        self.assertEqual(self.points(),1000)
    def test_cumulative_remainder_survives_two_sessions_and_retry(self):
        binding=self.bind();process_event(self.voice(),self.now)
        for n in range(1,181):checkpoint(self.now+timedelta(seconds=n*30))
        at=self.now+timedelta(seconds=5401);process_event(self.voice('exited_channel','exit',at),at)
        at=self.now+timedelta(seconds=5500);process_event(self.voice('joined_channel','rejoin',at,'sleep'),at)
        for n in range(1,61):checkpoint(at+timedelta(seconds=n*30))
        checkpoint(at+timedelta(seconds=1800))
        self.assertEqual(self.points(),100);binding.refresh_from_db()
        self.assertEqual(binding.awarded_points,1);self.assertGreaterEqual(binding.online_milliseconds,7200000)
    def test_disconnect_gap_does_not_generate_points(self):
        self.bind();reset_presence({'kook-user':'voice'},self.now)
        checkpoint(self.now+timedelta(hours=3))
        binding=KookBinding.objects.get();self.assertEqual(binding.online_milliseconds,0)
        reset_presence({'kook-user':'voice'},self.now+timedelta(hours=4));checkpoint(self.now+timedelta(hours=4,seconds=30))
        binding.refresh_from_db();self.assertEqual(binding.online_milliseconds,30000)
    def test_newly_readable_channel_starts_now_without_backfilling_missing_permission_time(self):
        binding=self.bind();binding.accounted_at=self.now-timedelta(hours=3);binding.save()
        with patch('engagement.management.commands.run_kook_points.timezone.now',return_value=self.now):
            attach_verified_presence({'kook-user':'sleep'})
        checkpoint(self.now+timedelta(seconds=30));binding.refresh_from_db()
        self.assertEqual(binding.channel_id,'sleep')
        self.assertEqual(binding.online_milliseconds,30000)
        self.assertEqual(binding.awarded_points,0)
    def test_wrong_channel_and_out_of_order_leave_do_not_change_presence(self):
        self.bind();process_event(self.voice(at=self.now+timedelta(seconds=2)),self.now+timedelta(seconds=2))
        process_event(self.voice('exited_channel','late',self.now),self.now+timedelta(seconds=3))
        self.assertEqual(KookBinding.objects.get().channel_id,'voice')
        self.assertFalse(process_event(self.voice(id='x',channel='other'),self.now))
    def test_inactive_member_cannot_get_points(self):
        self.bind();self.account.is_active=False;self.account.save()
        self.assertFalse(process_event(self.msg(),self.now));self.assertFalse(PointLedger.objects.exists())
    def test_manual_points_disabled_when_automatic_enabled(self):
        with self.assertRaises(ValidationError):record_external_points(self.account,kind='KOOK_ONLINE',source_id='x',day=timezone.localdate(),units=7200,operator=self.account)
    def test_provider_rewards_are_isolated_and_redeem_with_idempotency(self):
        provider=mapped(make_provider());MemberProfile.objects.create(account=provider,points_balance=4000000)
        reward=RewardItem.objects.create(code='provider-account',audience='PROVIDER',kind='SERVICE',name='账号补贴',point_cost=30000,is_active=True)
        claim=redeem(provider,reward.id,'provider-request-001')
        self.assertEqual(redeem(provider,reward.id,'provider-request-001').id,claim.id)
        self.assertEqual(MemberProfile.objects.get(account=provider).points_balance,1000000)
        self.assertEqual(RewardClaim.objects.count(),1)
        with self.assertRaises(ValidationError):redeem(self.account,reward.id,'boss-request-001')
    def test_provider_can_read_points_but_not_boss_only_rewards(self):
        user=make_provider();provider=mapped(user);client=APIClient();client.force_authenticate(user)
        RewardItem.objects.create(code='boss',kind='SERVICE',name='老板奖励',point_cost=100,is_active=True)
        RewardItem.objects.create(code='provider',audience='PROVIDER',kind='SERVICE',name='账号补贴',point_cost=30000,is_active=True)
        self.assertEqual(client.get('/api/club/points/').status_code,200)
        data=client.get('/api/club/rewards/').data['data'];self.assertEqual([r['code'] for r in data],['provider'])


@skipUnlessDBFeature('has_select_for_update')
class KookConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.now=timezone.now();self.account=mapped(make_user())
        KookPointsConfig.objects.create(enabled=True,guild_id='guild',checkin_channel_id='hall',voice_channel_ids=['voice'],started_at=self.now-timedelta(seconds=1))
        MemberProfile.objects.create(account=self.account)
        self.binding=KookBinding.objects.create(account=self.account,user_id='kook-user',channel_id='voice',accounted_at=self.now,online_milliseconds=7190000)
    def race(self,function):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections,connections
        barrier=Barrier(2)
        def worker(i):
            close_old_connections()
            try:barrier.wait(timeout=10);return function(i)
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(worker,range(2)))
    def test_same_checkpoint_awards_exactly_once(self):
        self.race(lambda i:checkpoint(self.now+timedelta(seconds=10)))
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.online_milliseconds,7200000)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance,100)
        self.assertEqual(PointLedger.objects.count(),1)
    def test_two_messages_same_day_cannot_duplicate_checkin(self):
        def send(i):
            return process_event({'type':1,'channel_type':'GROUP','target_id':'hall','author_id':'kook-user',
                'msg_id':'check-'+str(i),'msg_timestamp':int(self.now.timestamp()*1000),'content':'签到',
                'extra':{'guild_id':'guild','author':{'bot':False}}},self.now)
        self.race(send)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance,500)
        self.assertEqual(PointLedger.objects.count(),1)
