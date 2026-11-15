"""An open boss order page rereads the deadline from the real API."""
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class PendingDeadlineFrontendHTTPTest(LiveServerTestCase):
    def test_open_page_rechecks_deadline_without_cancelling_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for current React HTTP controller')
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        make_wallet(boss, balance=20000)
        service = make_service(price=10000)
        provider = make_provider(deposit_mode='EARNINGS')
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, provider_account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        token, refresh = issue_account_tokens(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'open-page-deadline'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order_id = response.data['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        repo = Path(settings.BASE_DIR).parent.parent
        payload = {'base': self.live_server_url, 'orderId': order_id,
            'providerToken': issue_account_tokens(provider_account)[0], 'session': {
            'token': token, 'refresh': refresh,
            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '隔离老板'},
        }}
        # Starting Node/importing the actual React controllers must not consume
        # the five-second business window. Arm the persisted deadline only once
        # both real initial HTTP reads have completed in the ready process.
        with subprocess.Popen([node, str(repo / 'scripts/scenarios/native-pending-deadline-live.cjs')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', cwd=repo) as process:
            try:
                process.stdin.write(json.dumps(payload) + '\n')
                process.stdin.flush()
                ready = Queue()
                Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
                try:
                    message = ready.get(timeout=15)
                except Empty:
                    process.kill()
                    output, errors = process.communicate(timeout=5)
                    self.fail('React deadline controller did not become ready: ' + errors + output)
                if not message:
                    output, errors = process.communicate(timeout=5)
                    self.fail('React deadline controller exited before readiness: ' + errors + output)
                self.assertEqual(json.loads(message), {'ready_for_deadline': True}, message)
                deadline = timezone.now() + timedelta(seconds=5)
                Order.objects.filter(pk=order_id).update(auto_cancel_at=deadline)
                process.stdin.write(json.dumps({'deadline': deadline.isoformat()}) + '\n')
                process.stdin.flush()
                output, errors = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, errors + output)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        order = Order.objects.get(pk=order_id)
        self.assertLessEqual(order.auto_cancel_at, timezone.now())
        self.assertEqual((order.status, order.payment_status), ('PENDING', 'PAID'))
        self.assertEqual(Wallet.objects.get(user=boss).balance, 10000)
        self.assertEqual(Wallet.objects.get(user=provider).balance, 0)
