"""The provider message list refreshes real messages through the real HTTP API."""
import json
import subprocess
from pathlib import Path

from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class MessageFrontendHTTPTest(LiveServerTestCase):
    def test_return_to_older_messages_refresh_and_account_isolation(self):
        first = make_provider(display_name='消息验收一')
        second = make_provider(display_name='消息验收二')
        accounts = []
        for user in (first, second):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        Message.objects.bulk_create([
            Message(recipient=first, recipient_account=accounts[0], type='ORDER', title=f'旧消息 {n}')
            for n in range(65)
        ])
        oldest_id = Message.objects.get(recipient_account=accounts[0], title='旧消息 0').pk
        Message.objects.create(recipient=second, recipient_account=accounts[1], title='另一账号消息')
        staff = make_console_user(['message:view', 'message:push'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=staff_account)
        inputs = {'base': self.live_server_url, 'firstToken': issue_account_tokens(accounts[0])[0],
                  'secondToken': issue_account_tokens(accounts[1])[0], 'staffToken': issue_account_tokens(staff_account)[0],
                  'firstAccount': accounts[0].pk, 'oldest': oldest_id}
        script = Path(__file__).resolve().parents[3] / 'scripts' / 'provider-messages-live.cjs'
        completed = subprocess.run(['node', str(script)], input=json.dumps(inputs), text=True, encoding='utf-8',
                                   capture_output=True, timeout=45)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout.strip())
        self.assertTrue(result['accountIsolation'] and result['detailAccountIsolation'] and result['readAll'])
        self.assertTrue(result['liveRefresh'] and result['resumeRefresh'] and result['reconnectRefresh'])
        self.assertEqual(Message.objects.filter(recipient_account=accounts[0], is_read=False).count(), 0)
