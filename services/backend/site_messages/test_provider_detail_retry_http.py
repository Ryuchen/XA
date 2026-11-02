"""Staff sends a real isolated notification; provider recovers from a dropped read."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderDetailRetryHTTPTest(LiveServerTestCase):
    def test_staff_push_and_provider_retries_detail(self):
        root = os.environ.get('XA_PROVIDER_MESSAGE_H5_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires the current provider H5 build and Node')
        provider, staff = make_provider(), make_console_user(['message:view', 'message:push'])
        account, operator = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        for user, linked in ((provider, account), (staff, operator)):
            link_legacy_relations(user, linked)
        AdminMembership.objects.filter(user=staff).update(account=operator)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(operator)[0])
        response = client.post('/api/admin/messages/push/', {'audience': 'CUSTOM', 'recipient_account_ids': [account.pk],
            'title': '隔离通知验收', 'preview': '请查看消息'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        message = Message.objects.get(recipient_account=account, title='隔离通知验收')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-message-detail-retry-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'token': issue_account_tokens(account)[0],
                              'id': account.pk, 'messageId': message.pk}), text=True, encoding='utf-8',
            capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        message.refresh_from_db()
        self.assertTrue(message.is_read)
