"""A current boss account without a legacy user still receives claim progress."""
from datetime import timedelta
import json
from pathlib import Path
import subprocess

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig, RewardItem
from orders.tests.factories import make_console_user, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountOnlyClaimNoticeTest(LiveServerTestCase):
    def test_boss_receives_real_cancellation_and_refund_notice(self):
        boss = ClubAccount.objects.create_account(username='isolated-current-boss', account_type='BOSS', openid='isolated-current-openid')
        other = make_user()
        staff = make_console_user(['engagement:fulfill', 'message:view'])
        other_account = get_or_create_account_for_legacy_user(other)
        staff_account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(other, other_account)
        link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        clients = [APIClient() for _ in range(3)]
        for client, account in zip(clients, (boss, other_account, staff_account)):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

        def data(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        now = timezone.now()
        KookPointsConfig.objects.create(pk=1, enabled=True, guild_id='isolated-guild',
            checkin_channel_id='isolated-hall', voice_channel_ids=[], started_at=now-timedelta(days=1))
        code = data(clients[0].post('/api/club/kook-binding/', {}, format='json'))['code']
        def event(content, event_id):
            return {'type':1, 'channel_type':'GROUP', 'target_id':'isolated-hall', 'author_id':'isolated-kook-user',
                'msg_id':event_id, 'msg_timestamp':int(now.timestamp()*1000), 'content':content,
                'extra':{'guild_id':'isolated-guild', 'author':{'username':'隔离账号', 'bot':False}}}
        # Only the external KOOK event is simulated; binding and accrual are real.
        self.assertTrue(process_event(event('绑定 ' + code, 'isolated-bind'), now))
        self.assertTrue(process_event(event('签到', 'isolated-checkin'), now))
        reward = RewardItem.objects.create(code='account-only-claim', name='隔离权益', kind='SERVICE',
            audience='ALL', point_cost=1, stock=1, in_shop=True, is_active=True)
        claim = data(clients[0].post('/api/club/redeem/', {'reward_id':reward.pk,
            'client_request_id':'account-only-redeem'}, format='json'))
        data(clients[2].post(f"/api/admin/club-claims/{claim['id']}/process/",
            {'status':'CANCELLED', 'note':'隔离验收取消'}, format='json'))
        self.assertEqual(data(clients[0].get('/api/club/points/'))['points'], 5)
        self.assertEqual(data(clients[1].get('/api/club/rewards/'))[0]['stock'], 1)
        notices = data(clients[0].get('/api/messages/'))['messages']
        notice = next(row for row in notices if row['title'] == '权益交付进度更新')
        self.assertIn('积分已退回', notice['preview'])
        self.assertEqual(notice['action_url'], f"/pages/reward-claim/index?id={claim['id']}")
        self.assertFalse(data(clients[1].get('/api/messages/'))['messages'])
        self.assertEqual(clients[1].get(f"/api/messages/{notice['id']}/").data['code'], 404)
        admin_messages = data(clients[2].get('/api/admin/messages/'))['list']
        admin_notice = next(row for row in admin_messages if row['id'] == notice['id'])
        self.assertEqual(admin_notice['recipient_username'], boss.username)
        self.assertIsNone(admin_notice['recipient'])
        token, refresh = issue_account_tokens(boss)
        root = Path(settings.BASE_DIR).parent.parent
        native = subprocess.run(['node', str(root / 'scripts/native-reservation-notice-live.cjs')], cwd=root,
            input=json.dumps({'base':self.live_server_url, 'token':token, 'refresh':refresh, 'account':boss.pk,
                'message':notice['id'], 'expectedTitle':'权益交付进度更新', 'expectedPreview':'积分已退回',
                'expectedTarget':f"/pages/reward-claim/index?id={claim['id']}"}),
            encoding='utf-8', text=True, capture_output=True, timeout=30)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        self.assertEqual(data(clients[0].get('/api/messages/'))['unread'], 0)

        tag_reward = RewardItem.objects.create(code='account-only-tag', name='隔离标签', kind='TAG',
            audience='ALL', point_cost=1, stock=1, duration_days=30, in_shop=True, is_active=True)
        tag_claim = data(clients[0].post('/api/club/redeem/', {'reward_id':tag_reward.pk,
            'client_request_id':'account-only-tag', 'details':{'tag':'最初标签'}}, format='json'))
        base = f"/api/admin/club-claims/{tag_claim['id']}"
        issued = data(clients[2].post(base + '/process/', {'status':'COMPLETED', 'note':'审核并发放',
            'tag_reviewed':True}, format='json'))
        data(clients[2].post(base + '/suspend-tag/', {'expected_tag':'最初标签', 'reason':'需要修改'}, format='json'))
        data(clients[0].patch(f"/api/club/claims/{tag_claim['id']}/", {'details':{'tag':'修改后标签'}}, format='json'))
        reviewed = data(clients[2].post(base + '/review-tag/', {'tag_reviewed':True,
            'expected_tag':'修改后标签'}, format='json'))
        self.assertEqual(reviewed['expires_at'], issued['expires_at'])
        notices = data(clients[0].get('/api/messages/'))['messages']
        self.assertTrue(any(row['title'] == '标签暂停展示' and row['action_url'].endswith(str(tag_claim['id'])) for row in notices))
        self.assertTrue(any(row['title'] == '标签审核通过' and row['action_url'].endswith(str(tag_claim['id'])) for row in notices))
        self.assertFalse(data(clients[1].get('/api/messages/'))['messages'])
