"""An open boss order page rereads the deadline from the real API."""
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_service, make_user, make_wallet
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class PendingDeadlineFrontendHTTPTest(LiveServerTestCase):
    def test_open_page_rechecks_deadline_without_cancelling_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native controller')
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        make_wallet(boss, balance=20000)
        service = make_service(price=10000)
        token, refresh = issue_account_tokens(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'open-page-deadline'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order_id = response.data['data']['id']
        # Shorten the real persisted deadline to exercise the waiting screen.
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() + timedelta(seconds=5))
        repo = Path(settings.BASE_DIR).parent.parent
        payload = {'base': self.live_server_url, 'orderId': order_id, 'session': {
            'token': token, 'refresh': refresh,
            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '隔离老板'},
        }}
        result = subprocess.run([node, str(repo / 'scripts/native-pending-deadline-live.cjs')],
            input=json.dumps(payload), text=True, encoding='utf-8', capture_output=True, timeout=20, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.status, order.payment_status), ('PENDING', 'PAID'))
        self.assertEqual(Wallet.objects.get(user=boss).balance, 10000)
