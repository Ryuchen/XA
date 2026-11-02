"""A real staff reply reaches the provider message-center entry and chat."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueChatNoticeTest(TransactionTestCase):
    def test_staff_reply_is_visible_outside_conversation_and_clears_on_read(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        provider = make_provider(display_name='隔离咨询陪玩')
        staff = make_console_user(['chat:view', 'chat:reply', 'message:push'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        for user, account in zip((provider, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]
        staff.admin_membership.save(update_fields=['account'])
        clients = [APIClient(), APIClient()]
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        for client, token in zip(clients, tokens):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        session = clients[0].get('/api/chat/session/').data['data']['session']
        pushed = clients[1].post('/api/admin/messages/push/', {'recipient_account_ids':[accounts[0].pk],
            'title':'隔离站内通知', 'preview':'隔离通知内容'}, format='json')
        self.assertEqual(pushed.data['code'], 0, pushed.data)
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_realtime() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-chat-notice-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'token':tokens[0],
                    'id':accounts[0].pk, 'staffToken':tokens[1], 'sessionId':session['id'],
                    'fixed':os.environ.get('XA_CHAT_NOTICE_FIXED') == '1'}),
                text=True, encoding='utf-8', capture_output=True, cwd=repo, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
