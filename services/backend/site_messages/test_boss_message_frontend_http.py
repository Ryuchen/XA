"""Boss native message page retains real older messages across new notices."""
import json
import subprocess
from pathlib import Path

from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_user
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class BossMessageFrontendHTTPTest(LiveServerTestCase):
    def test_boss_message_history_survives_new_notice_and_return(self):
        boss = make_user()
        other = make_user()
        accounts = []
        for user in (boss, other):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        Message.objects.bulk_create([
            Message(recipient=boss, recipient_account=accounts[0], type='ORDER', title=f'旧消息 {n}')
            for n in range(55)
        ])
        Message.objects.create(recipient=other, recipient_account=accounts[1], title='另一账号消息')
        # MySQL need not populate primary keys on bulk-created model instances.
        oldest = Message.objects.filter(recipient_account=accounts[0]).order_by('created_at', 'pk').values_list('pk', flat=True).first()
        staff = make_console_user(['message:view', 'message:push'])
        admin = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=admin)
        def session(account):
            token, refresh = issue_account_tokens(account)
            return {'token': token, 'refresh': refresh, 'user': {'id': str(account.pk), 'role': 'customer'}}
        inputs = {'base': self.live_server_url, 'first': session(accounts[0]), 'second': session(accounts[1]),
                  'staffToken': issue_account_tokens(admin)[0], 'firstAccount': accounts[0].pk,
                  'oldest': oldest}
        script = Path(__file__).resolve().parents[3] / 'scripts' / 'native-boss-messages-live.cjs'
        result = subprocess.run(['node', str(script)], input=json.dumps(inputs), text=True, encoding='utf-8',
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
