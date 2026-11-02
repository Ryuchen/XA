"""Real paid orders and their mobile workbench drilldown."""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@skipUnless(os.environ.get('XA_DASHBOARD_OVERDUE_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class DashboardOverdueBrowserTest(LiveServerTestCase):
    def test_overdue_count_and_filter_at_375(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, staff = make_user(), make_console_user(['dashboard:view', 'order:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff)]
        for user, account in zip((boss, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        rows = []
        for name in ('expired', 'future', 'oldest'):
            result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC' if name == 'oldest' else 'STAFF',
                'client_request_id': 'dashboard-overdue-' + name}, format='json').data
            self.assertEqual(result['code'], 0, result); rows.append(result['data'])
        Order.objects.filter(pk=rows[0]['id']).update(auto_cancel_at=timezone.now() - timedelta(minutes=1))
        Order.objects.filter(pk=rows[2]['id']).update(auto_cancel_at=timezone.now() - timedelta(minutes=5))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-dashboard-overdue-mobile-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(accounts[1])[0],
                              'expiredNo': rows[0]['order_no'], 'futureNo': rows[1]['order_no'], 'oldestNo': rows[2]['order_no']}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        from orders.tasks import auto_cancel_pending_order
        from wallet.models import Transaction, Wallet
        self.assertEqual(auto_cancel_pending_order.run(rows[2]['id']), 'cancelled')
        boss_view = client.get(f"/api/orders/orders/{rows[2]['id']}/").data['data']
        self.assertEqual((boss_view['status'], boss_view['payment_status']), ('CANCELLED', 'REFUNDED'))
        notices = client.get('/api/messages/', {'type': 'ORDER'}).data['data']['messages']
        self.assertTrue(any(item['related_order_id'] == rows[2]['id'] and item['title'] == '订单超时已取消' for item in notices))
        self.assertEqual(Transaction.objects.filter(order_id=rows[2]['id'], tx_type='REFUND').count(), 1)
        self.assertEqual(Wallet.objects.get(user=boss).balance, 30000)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[1])[0])
        self.assertEqual(client.get('/api/admin/dashboard/stats').data['data']['pending_paid_overdue'], 1)
