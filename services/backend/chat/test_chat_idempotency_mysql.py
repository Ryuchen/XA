"""Use MySQL row locking and real API requests to exercise chat retries."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_user


@skipUnless(connection.vendor == 'mysql', 'Requires MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatIdempotencyMySQLTest(TransactionTestCase):
    def test_concurrent_text_and_image_retries_create_one_message_each(self):
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, account)
        token, _ = issue_account_tokens(account)
        repo = Path(settings.BASE_DIR).parent.parent
        image = (repo / 'apps/boss-miniapp/native/assets/icons/chat.png').read_bytes()

        def send(kind):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                payload = {'client_message_id': 'mysql-chat-' + kind, 'content': '并发' + kind}
                if kind == 'image':
                    payload['image'] = SimpleUploadedFile('chat.png', image, content_type='image/png')
                response = client.post('/api/chat/send/', payload, format='multipart' if kind == 'image' else 'json')
                return response.data
            finally:
                close_old_connections()

        with TemporaryDirectory(prefix='xa-chat-mysql-media-') as media, override_settings(MEDIA_ROOT=media):
            for kind in ('text', 'image'):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(send, [kind, kind]))
                self.assertEqual([result['code'] for result in results], [0, 0], results)
                self.assertEqual(results[0]['data']['id'], results[1]['data']['id'])
            session = ChatSession.objects.get(account=account)
            self.assertEqual(session.unread_support, 2)
            self.assertEqual(ChatMessage.objects.filter(session=session).count(), 2)
