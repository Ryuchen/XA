"""The native order consultation uses the real order number in chat."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ChatOrderContextFrontendHTTPTest(LiveServerTestCase):
    def test_order_number_reaches_customer_service_and_context_is_account_scoped(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, other = make_user(), make_user()
        staff = make_console_user(['chat:view', 'chat:reply'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, other, staff)]
        for user, account in zip((boss, other, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        wallet.account = accounts[0]
        wallet.save(update_fields=['account'])
        service = make_service(price=10000)
        tokens = [issue_account_tokens(account) for account in accounts]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0][0])
        created = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'AUTO_GRAB', 'client_request_id': 'chat-order-context-real'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = created.data['data']
        second = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'AUTO_GRAB', 'client_request_id': 'chat-order-context-second'}, format='json')
        self.assertEqual(second.data['code'], 0, second.data)
        second_order = second.data['data']
        repo = Path(settings.BASE_DIR).parent.parent
        with tempfile.TemporaryDirectory(prefix='xa-chat-order-media-') as media_dir, override_settings(MEDIA_ROOT=media_dir):
            result = subprocess.run([node, str(repo / 'scripts/native-chat-order-context-live.cjs')],
                input=json.dumps({'base': self.live_server_url, 'orderId': order['id'], 'orderNo': order['order_no'],
                    'secondOrderId': second_order['id'], 'secondOrderNo': second_order['order_no'],
                    'sessions': [{'token': token, 'refresh': refresh, 'user': {'id': str(account.pk), 'role': 'customer'}}
                                 for account, (token, refresh) in zip(accounts[:2], tokens[:2])],
                    'staffToken': tokens[2][0]}), text=True, encoding='utf-8', capture_output=True,
                timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('real order context in customer support', result.stdout)
