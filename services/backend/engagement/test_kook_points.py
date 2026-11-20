from datetime import datetime, timedelta
import os
from unittest import skipUnless
from django.test import TestCase, override_settings
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
        from club_accounts.models import ClubAccount
        ClubAccount.objects.filter(pk=self.account.pk).update(license_membership_active=True)
        self.account.refresh_from_db()
        self.cfg=KookPointsConfig.objects.create(enabled=True,guild_id='guild',checkin_channel_id='hall',voice_channel_ids=['voice','sleep'],started_at=self.now-timedelta(seconds=1))
        from engagement.models import ClubOperationConfig
        ClubOperationConfig.objects.get_or_create(pk=1)
    def msg(self,content='签到',user='kook-user',id='m1',at=None):
        return {'type':1,'channel_type':'GROUP','target_id':'hall','author_id':user,'msg_id':id,
            'msg_timestamp':int((at or self.now).timestamp()*1000),'content':content,'extra':{'guild_id':'guild','author':{'username':'昵称','bot':False}}}
    def voice(self,kind='joined_channel',id='v1',at=None,channel='voice'):
        e=self.msg(id=id,at=at);e.update(type=255,target_id='guild',extra={'type':kind,'body':{'user_id':'kook-user','channel_id':channel}});return e
    def bind(self):return KookBinding.objects.create(account=self.account,user_id='kook-user',business_cycle=self.account.business_cycle)
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
    @skipUnless(os.environ.get('XA_KOOK_POINTS_FORMAL') == '1',
                'KOOK 语音在线积分须可核对的在线规则凭证与 verified presence（18-盘点 KOOK 积分待联调）；最终联调启用')
    def test_cumulative_remainder_survives_two_sessions_and_retry(self):
        binding=self.bind();process_event(self.voice(),self.now)
        for n in range(1,181):checkpoint(self.now+timedelta(seconds=n*30))
        at=self.now+timedelta(seconds=5401);process_event(self.voice('exited_channel','exit',at),at)
        at=self.now+timedelta(seconds=5500);process_event(self.voice('joined_channel','rejoin',at,'sleep'),at)
        for n in range(1,61):checkpoint(at+timedelta(seconds=n*30))
        checkpoint(at+timedelta(seconds=1800))
        self.assertEqual(self.points(),100);binding.refresh_from_db()
        self.assertEqual(binding.awarded_points,1);self.assertGreaterEqual(binding.online_milliseconds,7200000)
    @skipUnless(os.environ.get('XA_KOOK_POINTS_FORMAL') == '1',
                'KOOK 语音在线积分须可核对的在线规则凭证与 verified presence（18-盘点 KOOK 积分待联调）；最终联调启用')
    def test_disconnect_gap_does_not_generate_points(self):
        self.bind();reset_presence({'kook-user':'voice'},self.now)
        checkpoint(self.now+timedelta(hours=3))
        binding=KookBinding.objects.get();self.assertEqual(binding.online_milliseconds,0)
        reset_presence({'kook-user':'voice'},self.now+timedelta(hours=4));checkpoint(self.now+timedelta(hours=4,seconds=30))
        binding.refresh_from_db();self.assertEqual(binding.online_milliseconds,30000)
    @skipUnless(os.environ.get('XA_KOOK_POINTS_FORMAL') == '1',
                'KOOK 语音在线积分须可核对的在线规则凭证与 verified presence（18-盘点 KOOK 积分待联调）；最终联调启用')
    def test_newly_readable_channel_starts_now_without_backfilling_missing_permission_time(self):
        binding=self.bind();binding.accounted_at=self.now-timedelta(hours=3);binding.save()
        with patch('engagement.management.commands.run_kook_points.timezone.now',return_value=self.now):
            attach_verified_presence({'kook-user':'sleep'}, self.cfg.listener_revision)
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


@override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=True)
class KookCertificateTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        from stores.context import store_context
        from stores.models import PlatformAccount
        from stores.tests_hosts import xinan_store
        from engagement.models import ClubOperationConfig, KookOnlineRuleVersion
        from stores.business_certificates import CERTIFICATE_TIMEZONE

        self.store = xinan_store()
        self.owner = PlatformAccount.objects.create(username='kook-certificate-reviewer')
        self.midnight = datetime(2026, 11, 22, tzinfo=CERTIFICATE_TIMEZONE)
        with store_context(self.store), override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=False):
            KookOnlineRuleVersion.objects.get_or_create(pk=0, defaults={'revision': 0, 'window_ms': 7200000, 'points': 1})
            self.account = mapped(make_user())
            self.binding = KookBinding.objects.create(account=self.account, user_id='certificate-member',
                business_cycle=self.account.business_cycle, channel_id='voice',
                accounted_at=self.midnight - timedelta(seconds=20), online_milliseconds=7160000)
            KookPointsConfig.objects.create(enabled=True, guild_id='guild', checkin_channel_id='hall',
                voice_channel_ids=['voice'], started_at=self.midnight - timedelta(hours=2))
            operation, _ = ClubOperationConfig.objects.get_or_create(pk=1)
            ClubOperationConfig.objects.filter(pk=operation.pk).update(updated_at=self.midnight - timedelta(hours=2))

    def certificate(self, registered_at, expires_on=None, supersedes=None):
        from uuid import uuid4
        from stores.models import StoreBusinessCertificate

        with patch('django.utils.timezone.now', return_value=registered_at):
            return StoreBusinessCertificate.objects.create(store=self.store, confirmed_by=self.owner,
                expires_on=expires_on, document=b'fixture', content_type='image/png',
                client_request_id=uuid4(), supersedes=supersedes)

    @staticmethod
    def fail_certificates(execute, sql, params, many, context):
        from django.db import DatabaseError
        if 'stores_storebusinesscertificate' in sql:
            raise DatabaseError('fixture certificate query unavailable')
        return execute(sql, params, many, context)

    def test_midnight_counts_only_the_eligible_part_and_renewal_does_not_restore_the_gap(self):
        from engagement.models import KookOnlineInterval
        from stores.certificate_admission import interval_admission
        from stores.context import store_context
        from stores.models import Store

        signed = self.certificate(self.midnight - timedelta(days=2), self.midnight.date() - timedelta(days=1))
        other = Store.objects.get(slug='shenglang')
        with store_context(other):
            _, _, outcome, facts = interval_admission(self.midnight - timedelta(seconds=20), self.midnight)[0]
            self.assertEqual((outcome, facts['store_id'], facts['status']), ('DENY', other.pk, 'MISSING'))
        with store_context(self.store):
            checkpoint(self.midnight + timedelta(seconds=20))
            self.binding.refresh_from_db()
            self.assertEqual((self.binding.online_milliseconds, self.binding.awarded_points), (7180000, 0))
            self.assertEqual(list(KookOnlineInterval.objects.order_by('started_at')
                .values_list('ended_at', 'elapsed_ms', 'interval_outcome')), [
                    (self.midnight, 20000, 'ALLOW'), (self.midnight + timedelta(seconds=20), 20000, 'DENY')])
        self.certificate(self.midnight + timedelta(seconds=30), supersedes=signed)
        at = self.midnight + timedelta(seconds=50)
        with store_context(self.store), patch('django.utils.timezone.now', return_value=at):
            leave = {'type': 255, 'target_id': 'guild', 'msg_id': 'certificate-voice-exit',
                'msg_timestamp': int(at.timestamp() * 1000), 'extra': {'type': 'exited_channel',
                    'body': {'user_id': self.binding.user_id, 'channel_id': 'voice'}}}
            self.assertTrue(process_event(leave, at))
            self.assertFalse(process_event(leave, at))
            self.binding.refresh_from_db()
            self.assertEqual((self.binding.online_milliseconds, self.binding.awarded_points), (7200000, 1))
            self.assertEqual(self.binding.channel_id, '')
            self.assertEqual(list(PointLedger.objects.filter(kind='KOOK_ONLINE').values_list('amount', flat=True)), [100])

    def test_review_after_control_outage_counts_only_originally_eligible_time(self):
        from uuid import uuid4
        from django.db import connections
        from console.models import AdminAuditLog
        from engagement.kook_license_review import resolve_locked
        from engagement.models import KookOnlineInterval
        from stores.context import store_context
        from stores.db import transaction

        original = self.certificate(self.midnight - timedelta(days=2), self.midnight.date() - timedelta(days=1))
        with store_context(self.store):
            with connections['default'].execute_wrapper(self.fail_certificates):
                checkpoint(self.midnight + timedelta(seconds=20))
            checkpoint(self.midnight + timedelta(seconds=30))
            event = AdminAuditLog.objects.get(resource='kook-license-points')
            self.assertEqual(event.request_body['business_certificate_interval']['status'], 'CONTROL_UNAVAILABLE')
            self.assertEqual(event.request_body['evidence_version'], 3)
            self.assertEqual(KookOnlineInterval.objects.filter(processing_status='WAITING').count(), 2)
            self.assertFalse(PointLedger.objects.exists())

        self.certificate(self.midnight + timedelta(seconds=30), supersedes=original)
        with store_context(self.store), transaction.atomic():
            request_id = uuid4()
            kwargs = dict(request_id=request_id, actor_id=self.owner.pk, outcome='ALLOW', reason='复核隔离证照历史')
            review = resolve_locked(event.pk, **kwargs)
            self.assertEqual((review.credited_milliseconds, review.awarded_points), (20000, 0))
            self.assertEqual(resolve_locked(event.pk, **kwargs).pk, review.pk)
            self.binding.refresh_from_db()
            self.assertEqual(self.binding.online_milliseconds, 7180000)
            self.assertFalse(KookOnlineInterval.objects.filter(processing_status='WAITING').exists())
            self.assertFalse(PointLedger.objects.exists())

    def test_versioned_review_keeps_original_rule_and_replays_only_definite_waiting_time(self):
        from uuid import uuid4
        from django.db import connections
        from console.models import AdminAuditLog
        from engagement.kook_license_review import resolve_locked
        from engagement.models import KookOnlineCursor, KookOnlineRuleVersion
        from stores.context import store_context
        from stores.db import transaction

        original = self.certificate(self.midnight - timedelta(days=2), self.midnight.date() - timedelta(days=1))
        with store_context(self.store):
            KookOnlineRuleVersion.objects.create(revision=1, window_ms=20000, points=3,
                effective_at=self.midnight - timedelta(hours=1))
            KookBinding.objects.filter(pk=self.binding.pk).update(online_milliseconds=0)
            with connections['default'].execute_wrapper(self.fail_certificates):
                checkpoint(self.midnight + timedelta(seconds=20))
            event = AdminAuditLog.objects.get(resource='kook-license-points')
            self.assertEqual(event.request_body['rule_revision'], 1)

        self.certificate(self.midnight + timedelta(seconds=20), supersedes=original)
        with store_context(self.store), transaction.atomic():
            checkpoint(self.midnight + timedelta(seconds=30))
            self.assertFalse(PointLedger.objects.exists())
            kwargs = dict(request_id=uuid4(), actor_id=self.owner.pk, outcome='ALLOW', reason='原规则时段核对')
            review = resolve_locked(event.pk, **kwargs)
            self.assertEqual((review.credited_milliseconds, review.awarded_points), (20000, 300))
            self.assertEqual(resolve_locked(event.pk, **kwargs).pk, review.pk)
            self.binding.refresh_from_db()
            cursor = KookOnlineCursor.objects.get(account=self.account)
            self.assertEqual((self.binding.online_milliseconds, self.binding.awarded_points, cursor.remainder_ms),
                (30000, 3, 10000))
            self.assertEqual(PointLedger.objects.count(), 1)

    def test_later_registration_cannot_approve_a_wholly_unlicensed_interval(self):
        from uuid import uuid4
        from django.db import connections
        from console.models import AdminAuditLog
        from engagement.kook_license_review import resolve_locked
        from stores.context import store_context
        from stores.db import transaction

        with store_context(self.store), connections['default'].execute_wrapper(self.fail_certificates):
            checkpoint(self.midnight + timedelta(seconds=20))
            event = AdminAuditLog.objects.get(resource='kook-license-points')
        self.certificate(self.midnight + timedelta(seconds=20))
        with store_context(self.store), transaction.atomic(), override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=False):
            kwargs = dict(request_id=uuid4(), actor_id=self.owner.pk, reason='已核对原时段')
            with self.assertRaisesMessage(ValidationError, '原在线时段缺少有效的本店证照'):
                resolve_locked(event.pk, outcome='ALLOW', **kwargs)
            review = resolve_locked(event.pk, outcome='DENY', **kwargs)
            self.assertEqual((review.credited_milliseconds, review.awarded_points), (0, 0))
            self.assertFalse(PointLedger.objects.exists())

    def test_originally_valid_certificate_does_not_increase_the_recorded_milliseconds(self):
        from uuid import uuid4
        from console.models import AdminAuditLog
        from engagement.kook_license_review import resolve_locked
        from engagement.models import KookOnlineInterval
        from stores.context import store_context
        from stores.db import transaction

        self.certificate(self.midnight - timedelta(days=2))
        with store_context(self.store), transaction.atomic():
            KookBinding.objects.filter(pk=self.binding.pk).update(online_milliseconds=7198999)
            with patch('stores.license_benefits.check_license_benefits',
                       return_value=('UNKNOWN', {'status': 'CONTROL_UNAVAILABLE'})):
                checkpoint(self.binding.accounted_at + timedelta(milliseconds=1001))
            event = AdminAuditLog.objects.get(resource='kook-license-points')
            interval = KookOnlineInterval.objects.get(decision_source=event)
            review = resolve_locked(event.pk, request_id=uuid4(), actor_id=self.owner.pk, outcome='ALLOW',
                reason='核对原在线毫秒和证照')
            self.assertEqual((review.credited_milliseconds, review.awarded_points), (interval.elapsed_ms, 0))
            self.assertFalse(PointLedger.objects.exists())

    def test_manual_queue_rejects_interval_with_an_ineligible_replacement_in_the_middle(self):
        from engagement.models import KookManualOnlineReview, KookOnlineCursor
        from stores.context import store_context

        first = self.certificate(self.midnight - timedelta(days=2))
        expired = self.certificate(self.midnight - timedelta(minutes=30),
            self.midnight.date() - timedelta(days=2), supersedes=first)
        self.certificate(self.midnight - timedelta(minutes=10), supersedes=expired)
        start, end = self.midnight - timedelta(hours=1), self.midnight - timedelta(minutes=5)
        with (store_context(self.store), patch('django.utils.timezone.now', return_value=self.midnight),
              self.assertRaisesMessage(ValidationError, '原在线时段缺少有效的本店证照')):
            KookPointsConfig.objects.update(enabled=False)
            KookOnlineCursor.objects.create(account=self.account, business_cycle=self.account.business_cycle,
                binding=self.binding, blocked=True)
            record_external_points(self.account, kind='KOOK_ONLINE', source_id='certificate-history',
                day=start.date(), units=3300, operator=self.account, observed_start=start, observed_end=end)
        with store_context(self.store):
            self.assertFalse(KookManualOnlineReview.objects.exists())
            self.assertFalse(PointLedger.objects.exists())


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
