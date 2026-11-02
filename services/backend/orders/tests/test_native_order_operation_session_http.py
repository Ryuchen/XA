"""Account changes while an actual order operation is being confirmed."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_service, make_user, make_wallet
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class NativeOrderOperationSessionTest(LiveServerTestCase):
    def test_switch_accounts_while_cancel_confirmation_is_open(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        users = [make_user(), make_user()]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        make_wallet(users[0], balance=50000)
        service = make_service(price=10000)
        sessions = []
        for account in accounts:
            token, refresh = issue_account_tokens(account)
            sessions.append({'token': token, 'refresh': refresh, 'user': {'id': str(account.pk), 'role': 'customer'}})
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + sessions[0]['token'])
        response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'AUTO_GRAB', 'client_request_id': 'native-cancel-session-qa'}, format='json').data
        self.assertEqual(response['code'], 0, response)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-order-operation-session-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': response['data']['id'],
                              'first': sessions[0], 'second': sessions[1]}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(pk=response['data']['id'])
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(user=users[0]).balance, 50000)
