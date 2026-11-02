"""A lost HTTP response must not duplicate a customer-support message."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from chat.models import ChatMessage, ChatSession
from orders.tests.factories import make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NativeChatSendRecoveryHTTPTest(LiveServerTestCase):
    def test_lost_text_and_image_responses_recover_without_duplicate_messages(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        users = [make_user(), make_user()]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        sessions = []
        for account in accounts:
            token, refresh = issue_account_tokens(account)
            sessions.append({'token': token, 'refresh': refresh,
                             'user': {'id': str(account.pk), 'role': 'customer'}})
        repo = Path(settings.BASE_DIR).parent.parent
        with tempfile.TemporaryDirectory(prefix='xa-chat-send-recovery-') as media, override_settings(MEDIA_ROOT=media):
            result = subprocess.run([node, str(repo / 'scripts/native-chat-send-recovery-live.cjs')],
                input=json.dumps({'base': self.live_server_url, 'sessions': sessions}),
                text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            session = ChatSession.objects.get(account=accounts[0])
            self.assertEqual(session.messages.count(), 6)
            self.assertEqual(session.unread_support, 6)
            self.assertEqual(ChatMessage.objects.filter(session=session, content='首次消息').count(), 1)
            self.assertEqual(ChatMessage.objects.filter(session=session, content='重试消息').count(), 1)
            self.assertEqual(ChatMessage.objects.filter(session=session, content_type='IMAGE').count(), 2)
