import base64
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from django.core.files.base import ContentFile
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from chat.models import ChatAssignmentEvent, ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.private_media import may_read
from orders.tests.factories import make_console_user
from orders.consumers import current_chat_payload


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReceptionTest(APITestCase):
    def staff(self, permissions):
        legacy = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(legacy)
        legacy.admin_membership.account = account
        legacy.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return account, client

    def setUp(self):
        self.a, self.ac = self.staff(['chat:view', 'chat:reply'])
        self.b, self.bc = self.staff(['chat:view', 'chat:reply'])
        self.supervisor, self.sc = self.staff(['chat:view', 'chat:reply', 'chat:supervise'])
        self.boss = ClubAccount.objects.create_account(username='reception-boss', account_type='BOSS')
        self.session = ChatSession.objects.create(account=self.boss, last_message='私密咨询')
        self.message = ChatMessage.objects.create(session=self.session, sender_account=self.boss, content='私密咨询')
        self.path = f'/api/admin/chat-sessions/{self.session.pk}/'

    def change(self, client, action, version, **data):
        return client.post(self.path + action + '/', {'expected_version': version, **data}, format='json')

    def test_claim_transfer_supervision_and_media_access(self):
        item = self.ac.get(self.path).data['data']
        self.assertFalse(item['can_read'])
        self.assertEqual(item['last_message'], '')
        self.assertEqual(self.ac.get(self.path + 'messages/').status_code, 403)
        self.assertFalse(may_read(self.a, self.message))
        self.assertEqual(self.change(self.ac, 'claim', 0).status_code, 200)
        self.assertEqual(self.change(self.bc, 'claim', 0).status_code, 409)
        self.assertEqual(self.ac.get(self.path + 'messages/').data['data']['list'][0]['content'], '私密咨询')
        self.assertEqual(self.bc.get(self.path + 'messages/').status_code, 403)
        self.assertEqual(self.sc.post(self.path + 'reply/', {'content': '主管回复'}, format='json').status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.claimed_by_id, self.a.pk)
        self.assertEqual(self.change(self.ac, 'transfer', 1, account_id=self.b.pk).status_code, 200)
        self.assertEqual(self.ac.get(self.path + 'messages/').status_code, 403)
        self.session.refresh_from_db()
        self.message.session = self.session
        self.assertFalse(may_read(self.a, self.message))
        self.assertTrue(may_read(self.b, self.message))
        self.assertEqual(self.bc.get(self.path + 'messages/').status_code, 200)
        self.assertEqual(self.change(self.sc, 'release', 2, reason='主管结束本次接待').status_code, 200)
        self.assertEqual(self.bc.get(self.path + 'messages/').status_code, 403)
        self.assertEqual(list(ChatAssignmentEvent.objects.values_list('action', flat=True)), ['CLAIM', 'TRANSFER', 'RELEASE'])

    def test_status_and_read_do_not_disclose_unclaimed_content(self):
        self.assertEqual(self.ac.post(self.path + 'reply/', {'content': '不可发送'}, format='json').status_code, 403)
        self.assertEqual(self.ac.post(self.path + 'read/', {'message_ids': [self.message.pk]}, format='json').status_code, 403)
        self.assertEqual(self.ac.get(self.path + 'message-status/', {'client_message_id': 'abcdefgh'}).status_code, 403)
        self.assertEqual(self.change(self.bc, 'transfer', 0, account_id=self.b.pk).status_code, 403)
        self.assertEqual(self.change(self.sc, 'transfer', 0, account_id=self.b.pk).status_code, 200)
        self.assertEqual(self.bc.get(self.path + 'messages/').status_code, 200)

    def test_live_delivery_is_redacted_and_rechecked_after_transfer(self):
        event = {'session_id': self.session.pk, 'message': {'id': self.message.pk}}
        self.assertIsNone(current_chat_payload.func(self.a, event, message=True))
        session_event = current_chat_payload.func(self.a, event)['session']
        self.assertNotIn('last_message', session_event)
        self.assertNotIn('私密咨询', repr(session_event))
        self.assertEqual(self.change(self.ac, 'claim', 0).status_code, 200)
        self.assertEqual(current_chat_payload.func(self.a, event, message=True)['message']['content'], '私密咨询')
        self.assertIsNone(current_chat_payload.func(self.b, event, message=True))
        self.assertEqual(self.change(self.ac, 'transfer', 1, account_id=self.b.pk).status_code, 200)
        self.assertIsNone(current_chat_payload.func(self.a, event, message=True))
        self.assertEqual(current_chat_payload.func(self.b, event, message=True)['message']['content'], '私密咨询')

    def test_old_image_ticket_is_revoked_on_transfer(self):
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGMUbulhYGBgYgADAA1VASfFeWKjAAAAAElFTkSuQmCC')
        with TemporaryDirectory(prefix='xa-chat-reception-') as root, override_settings(MEDIA_ROOT=root):
            self.message.content_type = 'IMAGE'
            self.message.image.save('isolated.png', ContentFile(image), save=True)
            self.assertEqual(self.change(self.ac, 'claim', 0).status_code, 200)
            url = self.ac.get(self.path + 'messages/').data['data']['list'][0]['image_url']
            path = urlsplit(url).path
            response = self.ac.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()
            self.assertEqual(self.change(self.ac, 'transfer', 1, account_id=self.b.pk).status_code, 200)
            self.assertEqual(self.ac.get(path).status_code, 404)
            self.assertEqual(self.ac.post('/api/media/renew/', {'ticket': path.rstrip('/').rsplit('/', 1)[-1]}, format='json').status_code, 404)

    def test_supervisor_without_reply_permission_can_assign_but_cannot_reply(self):
        _, client = self.staff(['chat:view', 'chat:supervise'])
        self.assertEqual(client.get(self.path + 'messages/').status_code, 200)
        self.assertEqual(client.post(self.path + 'reply/', {'content': '不可回复'}, format='json').status_code, 403)
        self.assertEqual(self.change(client, 'transfer', 0, account_id=self.a.pk).status_code, 200)
        self.assertEqual(self.change(client, 'release', 1, reason='主管撤回接待分配').status_code, 200)

    def test_supervisor_reassignment_history_is_complete_and_not_public_to_other_agents(self):
        history_url = self.path + 'assignment-history/'
        self.assertEqual(self.change(self.ac, 'claim', 0).status_code, 200)
        bad = self.change(self.sc, 'transfer', 1, account_id=self.b.pk, reason=['bad'])
        self.assertEqual(bad.status_code, 400)
        transferred = self.change(self.sc, 'transfer', 1, account_id=self.b.pk, reason='原客服离岗，安排接手')
        self.assertEqual(transferred.status_code, 200, transferred.data)
        self.assertEqual(self.change(self.bc, 'release', 2, reason='接待结束').status_code, 200)
        self.assertEqual(self.ac.get(history_url).status_code, 403)
        self.assertEqual(self.bc.get(history_url).status_code, 403)
        response = self.sc.get(history_url)
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['data']['list']
        self.assertEqual([row['version'] for row in rows], [3, 2, 1])
        self.assertEqual(rows[1]['actor']['id'], self.supervisor.pk)
        self.assertEqual(rows[1]['previous']['id'], self.a.pk)
        self.assertEqual(rows[1]['current']['id'], self.b.pk)
        self.assertEqual(rows[1]['reason'], '原客服离岗，安排接手')
        self.assertTrue(rows[1]['acting_as_supervisor'])
        self.assertFalse(rows[0]['acting_as_supervisor'])
        self.assertEqual(rows[2]['reason'], '')
        self.assertEqual([row['version'] for row in self.sc.get(history_url, {'before_version': 3}).data['data']['list']], [2, 1])
        self.session.refresh_from_db()
        self.assertIsNone(self.session.claimed_by_id)
        self.assertEqual(self.session.claim_version, 3)

    def test_supervisor_reply_keeps_actual_author_in_history_and_live_delivery(self):
        self.a.nickname = '客服小浪'
        self.a.save(update_fields=['nickname'])
        self.supervisor.nickname = '主管小星'
        self.supervisor.save(update_fields=['nickname'])
        self.assertEqual(self.change(self.ac, 'claim', 0).status_code, 200)
        first = self.ac.post(self.path + 'reply/', {'content': '我先接待'}, format='json')
        second = self.sc.post(self.path + 'reply/', {'content': '主管补充说明'}, format='json')
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(second.data['data']['sender_name'], '主管小星')
        self.assertEqual(second.data['data']['sender_account'], self.supervisor.pk)
        self.session.refresh_from_db()
        self.assertEqual(self.session.claimed_by_id, self.a.pk)
        history = self.ac.get(self.path + 'messages/').data['data']['list']
        self.assertEqual([item['sender_name'] for item in history[-2:]], ['客服小浪', '主管小星'])
        self.assertEqual([item['sender_account'] for item in history[-2:]], [self.a.pk, self.supervisor.pk])

        boss_client = APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss)[0])
        boss_messages = boss_client.get('/api/chat/session/').data['data']['messages']
        self.assertEqual([item['sender_name'] for item in boss_messages[-2:]], ['客服小浪', '主管小星'])
        self.assertNotIn('sender_account', boss_messages[-1])
        event = {'session_id': self.session.pk, 'message': {'id': second.data['data']['id']},
                 'recipient_business_cycle': self.boss.business_cycle}
        boss_live = current_chat_payload.func(self.boss, event, message=True)['message']
        staff_live = current_chat_payload.func(self.a, event, message=True)['message']
        self.assertEqual(boss_live['sender_name'], '主管小星')
        self.assertNotIn('sender_account', boss_live)
        self.assertEqual(staff_live['sender_account'], self.supervisor.pk)
        self.supervisor.nickname = ''
        self.supervisor.save(update_fields=['nickname'])
        boss_again = boss_client.get('/api/chat/session/').data['data']['messages'][-1]
        self.assertEqual(boss_again['sender_name'], '客服')
        self.assertNotIn('sender_account', boss_again)
