"""Real team applications reflected in the boss's native controllers."""
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
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class NativeApplicationProgressHTTPTest(LiveServerTestCase):
    def test_boss_progress_matches_real_team_readiness_and_staff_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, staff = make_user(), make_console_user(['order:view'])
        providers = [make_provider(deposit_mode='EARNINGS', pass_tier='BLACK', pass_expires_at=timezone.now() + timedelta(days=1)) for _ in range(2)]
        users = [boss, staff, *providers]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        for user, account in zip(providers, accounts[2:]):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000, billing_provider_count=2)
        for provider in providers:
            provider.escort_profile.service_items.add(service)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC', 'dispatch_mode': 'BOSS_SELECT',
            'client_request_id': 'native-application-progress'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        order_id = result['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-application-progress-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': order_id, 'staffToken': tokens[1],
                'applicantToken': tokens[2], 'partnerToken': tokens[3], 'partnerId': accounts[3].pk,
                'session': {'token': tokens[0], 'user': {'id': str(accounts[0].pk), 'role': 'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, 'GRABBED')
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
