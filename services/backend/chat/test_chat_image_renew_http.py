"""Exercise real chat image delivery, pagination and expired-link renewal."""
import base64
import json
import time
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from django.core import signing
from django.test import LiveServerTestCase, override_settings

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from common.private_media import MAX_AGE, SALT
from console.models import AdminMembership
from orders.tests.factories import make_console_user


def expired_ticket(ticket):
    signer = signing.TimestampSigner(salt=SALT)
    value = signer.unsign(ticket)
    payload = value.rsplit(':', 1)[0]
    stamped = payload + ':' + signing.b62_encode(int(time.time()) - MAX_AGE - 20)
    return stamped + ':' + signer.signature(stamped)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatImageRenewHTTPTest(LiveServerTestCase):
    def test_owner_and_staff_can_reopen_expired_chat_image(self):
        boss = ClubAccount.objects.create_account(username='isolated-image-reader', account_type='BOSS')
        other = ClubAccount.objects.create_account(username='isolated-image-other', account_type='BOSS')
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        link_legacy_relations(staff_user, staff)
        AdminMembership.objects.filter(user=staff_user).update(account=staff)
        tokens = [issue_account_tokens(account)[0] for account in (boss, staff, other)]
        headers = [{'Authorization': 'Bearer ' + token} for token in tokens]
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGMUbulhYGBgYgADAA1VASfFeWKjAAAAAElFTkSuQmCC')

        def http(url, *, headers=None, data=None):
            try:
                with urlopen(Request(url, headers=headers or {}, data=data), timeout=10) as response:
                    return response.status, response.read()
            except HTTPError as error:
                return error.code, error.read()

        def api(url, account_index, payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            request_headers = {**headers[account_index], 'Content-Type': 'application/json'}
            status, body = http(url, headers=request_headers, data=data)
            return status, json.loads(body)

        with TemporaryDirectory(prefix='xa-chat-renew-') as media_root, override_settings(MEDIA_ROOT=media_root):
            boundary = 'isolated' + uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="chat.png"\r\n'
                    'Content-Type: image/png\r\n\r\n').encode() + image + f'\r\n--{boundary}--\r\n'.encode()
            status, raw = http(self.live_server_url + '/api/chat/send/', data=body,
                               headers={**headers[0], 'Content-Type': 'multipart/form-data; boundary=' + boundary})
            self.assertEqual(status, 200)
            result = json.loads(raw)
            self.assertEqual(result['code'], 0, result)
            message_id = result['data']['id']
            owner_url = result['data']['image_url']
            self.assertEqual(http(owner_url)[0], 200)

            # Place the actual image behind the 50-message history boundary.
            for index in range(51):
                status, sent = api(self.live_server_url + '/api/chat/send/', 0, {'content': f'隔离历史消息 {index}'})
                self.assertEqual((status, sent['code']), (200, 0))
            session = api(self.live_server_url + '/api/chat/session/', 0)[1]['data']
            self.assertTrue(session['has_more'])
            self.assertFalse(any(item['id'] == message_id for item in session['messages']))
            older = api(self.live_server_url + '/api/chat/session/?before_id=' + str(session['next_before_id']), 0)[1]['data']
            self.assertEqual(older['messages'][0]['id'], message_id)
            self.assertEqual(http(older['messages'][0]['image_url'])[0], 200)
            session_id = session['session']['id']
            recent = api(self.live_server_url + f'/api/admin/chat-sessions/{session_id}/messages/?cursor=1', 1)[1]['data']
            self.assertTrue(recent['has_more'])
            earlier = api(self.live_server_url + f'/api/admin/chat-sessions/{session_id}/messages/?cursor=1&before_id=' + str(recent['next_before_id']), 1)[1]['data']
            self.assertEqual(earlier['list'][0]['id'], message_id)
            staff_url = earlier['list'][0]['image_url']
            self.assertEqual(http(staff_url)[0], 200)
            self.assertNotEqual(owner_url, staff_url)

            for index, url in ((0, owner_url), (1, staff_url)):
                old_ticket = expired_ticket(urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1])
                expired = self.live_server_url + '/api/media/private/' + old_ticket + '/'
                self.assertEqual(http(expired)[0], 404)
                denied, _ = api(self.live_server_url + '/api/media/renew/', 2, {'ticket': old_ticket})
                self.assertEqual(denied, 404)
                status, renewed = api(self.live_server_url + '/api/media/renew/', index, {'ticket': old_ticket})
                self.assertEqual(status, 200, renewed)
                self.assertEqual(http(renewed['data']['url'])[0], 200)
