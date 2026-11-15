import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from chat.models import ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_CHAT_RECEPTION_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ReceptionBrowserTest(LiveServerTestCase):
    def test_claim_and_transfer_hide_messages_from_other_staff(self):
        permissions = ['chat:view', 'chat:reply']
        identities = []
        for _ in range(2):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            identities.append({'id': account.pk, 'token': issue_account_tokens(account)[0], 'permissions': permissions})
        boss = ClubAccount.objects.create_account(username='reception-browser-boss', account_type='BOSS')
        session = ChatSession.objects.create(account=boss, last_message='隔离咨询内容')
        ChatMessage.objects.create(session=session, sender_account=boss, content='隔离咨询内容')
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/chat-reception-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'a': identities[0], 'b': identities[1], 'session': session.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
