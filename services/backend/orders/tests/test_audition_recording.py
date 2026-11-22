"""Real paid-order audition linkage with private audio in isolated storage."""
import os
from datetime import timedelta
from unittest import skipUnless
from urllib.parse import urlsplit
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import OrderApplication
from orders.tests import test_release_flow as release_flow
from orders.tests.factories import make_provider, make_user
from users.test_voice_recordings import audio_file


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AuditionRecordingTests(APITransactionTestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge

    def prepare(self, user):
        account = get_or_create_account_for_legacy_user(user)
        account.openid = f'audition-voice-{account.pk}'
        account.is_openid_bound = True
        account.save(update_fields=['openid', 'is_openid_bound'])
        profile = user.escort_profile
        profile.account = account
        profile.pass_tier = 'BLACK'
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.accounts[user.pk] = account
        return account

    def upload(self, user, title):
        self.login(user)
        return self.ok(self.client.post('/api/users/recordings/', {'audio': audio_file(), 'title': title, 'client_request_id': uuid4().hex}, format='multipart'))

    def order(self, count=1):
        self.service.billing_provider_count = count
        self.service.fulfillment_provider_count = count
        self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        return self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'dispatch_mode': 'BOSS_SELECT', 'support_contact_id': self.card.pk,
            'client_request_id': f'voice-order-{count}'}, format='json'))

    def test_single_application_requires_owned_recording_and_boss_can_listen(self):
        self.prepare(self.provider)
        own = self.upload(self.provider, '本人试音')
        other = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        self.prepare(other)
        foreign = self.upload(other, '他人录音')
        order = self.order()
        from types import SimpleNamespace
        from common.private_media import private_media_url
        from users.models import VoiceRecording
        self.assertEqual(private_media_url(SimpleNamespace(account=self.accounts[self.boss.pk]),
            VoiceRecording.objects.get(pk=own['id']).audio), '')
        self.login(self.provider)
        path = f"/api/orders/orders/{order['id']}/grab/"
        self.assertNotEqual(self.client.post(path, {}, format='json').data['code'], 0)
        self.assertNotEqual(self.client.post(path, {'recording_id': foreign['id']}, format='json').data['code'], 0)
        self.assertFalse(OrderApplication.objects.filter(order_id=order['id']).exists())
        self.ok(self.client.post(path, {'recording_id': own['id']}, format='json'))
        self.assertEqual(self.client.delete(f"/api/users/recordings/{own['id']}/").data['code'], 0)
        self.login(self.boss)
        current = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))
        application = current['applications'][0]
        self.assertTrue(application['ready'])
        recorded = application['members'][0]['recording']
        self.assertEqual(recorded['title'], '本人试音')
        self.assertEqual(private_media_url(SimpleNamespace(account=self.accounts[self.boss.pk]),
            VoiceRecording.objects.get(pk=foreign['id']).audio), '')
        url = urlsplit(recorded['audio_url'])
        response = self.client.get(url.path + ('?' + url.query if url.query else ''))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'RIFF'))
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/applications/{application['id']}/select/", {}, format='json'))
        stranger = make_user()
        self.accounts[stranger.pk] = get_or_create_account_for_legacy_user(stranger)
        self.login(stranger)
        self.assertNotEqual(self.client.get(f"/api/orders/orders/{order['id']}/").data['code'], 0)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '双陪试音抢单池入池与逐人录音确认属正式契约深水区（18-盘点 试音/抢单池待联调）；最终联调启用')
    def test_double_team_each_member_confirms_own_recording(self):
        self.prepare(self.provider)
        partner = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        partner_account = self.prepare(partner)
        first = self.upload(self.provider, '甲试音')
        second = self.upload(partner, '乙试音')
        order = self.order(2)
        self.login(self.provider)
        pool = self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))
        pool_by_id = {item['id']: item for item in pool}
        self.assertIn(order['id'], pool_by_id)
        self.assertIn('GRAB', pool_by_id[order['id']]['allowed_actions'])
        detail = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))
        self.assertEqual(detail['escort_mode'], 'DOUBLE')
        self.assertEqual(detail['required_team_size'], 2)
        self.assertTrue(detail['audition_recordings_required'])
        self.assertIn('GRAB', detail['allowed_actions'])
        grab = f"/api/orders/orders/{order['id']}/grab/"
        team = {'team_size': 2, 'partner_id': partner_account.pk}
        self.assertNotEqual(self.client.post(grab, team, format='json').data['code'], 0)
        self.assertFalse(OrderApplication.objects.filter(order_id=order['id']).exists())
        self.ok(self.client.post(grab, {**team, 'recording_id': first['id']}, format='json'))
        application = OrderApplication.objects.get(order_id=order['id'])
        self.login(partner)
        pool = self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))
        item = next(item for item in pool if item['id'] == order['id'])
        self.assertTrue(any(row['id'] == application.pk and row['is_partner'] for row in item['my_applications']))
        endpoint = f"/api/orders/orders/{order['id']}/applications/{application.pk}/action/"
        self.assertNotEqual(self.client.post(endpoint, {'action': 'SUBMIT_VOICE', 'recording_id': second['id']}, format='json').data['code'], 0)
        application.refresh_from_db()
        self.assertFalse(application.partner_confirmed)
        self.assertNotEqual(self.client.post(endpoint, {'action': 'CONFIRM_TEAM', 'recording_id': first['id']}, format='json').data['code'], 0)
        self.ok(self.client.post(endpoint, {'action': 'CONFIRM_TEAM', 'recording_id': second['id']}, format='json'))
        self.login(self.boss)
        item = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['applications'][0]
        self.assertTrue(item['ready'])
        self.assertEqual([member['recording']['title'] for member in item['members']], ['甲试音', '乙试音'])

    def test_unavailable_recording_cannot_be_submitted_or_selected(self):
        from pathlib import Path
        from users.models import VoiceRecording
        self.prepare(self.provider)
        recording = self.upload(self.provider, '本人试音')
        file_path = Path(VoiceRecording.objects.get(pk=recording['id']).audio.path)
        content = file_path.read_bytes()
        order = self.order()
        grab = f"/api/orders/orders/{order['id']}/grab/"
        self.login(self.provider)
        file_path.write_bytes(content[:20])
        listing = self.ok(self.client.get('/api/users/recordings/'))['list']
        self.assertFalse(next(row for row in listing if row['id'] == recording['id'])['available'])
        self.assertNotEqual(self.client.post(grab, {'recording_id': recording['id']}, format='json').data['code'], 0)
        self.assertFalse(OrderApplication.objects.filter(order_id=order['id']).exists())
        file_path.write_bytes(content)
        self.ok(self.client.post(grab, {'recording_id': recording['id']}, format='json'))
        application = OrderApplication.objects.get(order_id=order['id'])
        self.login(self.boss)
        listing = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['applications'][0]
        self.assertTrue(listing['ready'])
        file_path.unlink()
        listing = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['applications'][0]
        self.assertFalse(listing['ready'])
        self.assertIn('录音暂不可用', listing['not_ready_reason'])
        select = f"/api/orders/orders/{order['id']}/applications/{application.pk}/select/"
        self.assertNotEqual(self.client.post(select, {}, format='json').data['code'], 0)
        application.refresh_from_db()
        self.assertEqual(application.status, 'PENDING')
        self.login(self.provider)
        detail = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/applications/{application.pk}/mine/"))
        self.assertTrue(detail['application']['my_recording_unavailable'])
        file_path.write_bytes(content)
        self.login(self.boss)
        self.ok(self.client.post(select, {}, format='json'))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '双陪试音抢单池入池与逐人录音确认属正式契约深水区（18-盘点 试音/抢单池待联调）；最终联调启用')
    def test_existing_confirmed_team_can_supplement_each_own_recording(self):
        applicant_account = self.prepare(self.provider)
        partner = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        partner_account = self.prepare(partner)
        first = self.upload(self.provider, '历史甲录音')
        second = self.upload(partner, '历史乙录音')
        order = self.order(2)
        # Existing confirmed applications can predate per-member recordings.
        application = OrderApplication.objects.create(
            order_id=order['id'], applicant=applicant_account, partner=partner_account,
            applicant_business_cycle=applicant_account.business_cycle,
            partner_business_cycle=partner_account.business_cycle, partner_confirmed=True,
        )
        endpoint = f"/api/orders/orders/{order['id']}/applications/{application.pk}/action/"
        select = f"/api/orders/orders/{order['id']}/applications/{application.pk}/select/"
        self.login(self.boss)
        listing = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['applications'][0]
        self.assertFalse(listing['ready'])
        self.assertNotEqual(self.client.post(select, {}, format='json').data['code'], 0)
        self.login(self.provider)
        detail = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/applications/{application.pk}/mine/"))
        self.assertFalse(detail['application']['my_recording_submitted'])
        self.assertNotEqual(self.client.post(endpoint, {'action': 'SUBMIT_VOICE', 'recording_id': second['id']}, format='json').data['code'], 0)
        self.ok(self.client.post(endpoint, {'action': 'SUBMIT_VOICE', 'recording_id': first['id']}, format='json'))
        self.ok(self.client.post(endpoint, {'action': 'SUBMIT_VOICE', 'recording_id': first['id']}, format='json'))
        detail = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/applications/{application.pk}/mine/"))
        self.assertTrue(detail['application']['my_recording_submitted'])
        self.login(self.boss)
        self.assertNotEqual(self.client.post(select, {}, format='json').data['code'], 0)
        self.login(partner)
        self.ok(self.client.post(endpoint, {'action': 'SUBMIT_VOICE', 'recording_id': second['id']}, format='json'))
        self.login(self.boss)
        listing = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['applications'][0]
        self.assertTrue(listing['ready'])
        self.assertEqual([m['recording']['title'] for m in listing['members']], ['历史甲录音', '历史乙录音'])
        self.assertEqual(application.voices.count(), 2)
        self.ok(self.client.post(select, {}, format='json'))
