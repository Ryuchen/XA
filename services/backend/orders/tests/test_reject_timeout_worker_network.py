"""A rejected staff order expires through an isolated real Celery worker."""
import os
import subprocess
import sys
import time
from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication, OrderStatusLog, OrderTimeoutConfig
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from site_messages.models import Message
from wallet.models import Transaction, Wallet


@skipUnless(os.environ.get('XA_ORDER_TIMEOUT_WORKER_TEST') == '1', 'Requires isolated Redis and a real worker')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class RejectTimeoutWorkerNetworkTest(APITransactionTestCase):
    def test_rejected_staff_order_expires_and_refunds_once(self):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not database or connection.settings_dict['NAME'] != database:
            self.fail('An explicitly isolated SQLite database is required')
        boss, provider, staff, partner = make_user(), make_provider(), make_console_user(['dashboard:view', 'order:view', 'order:dispatch']), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff, partner)]
        for user, account in zip((boss, provider, staff, partner), accounts):
            link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        OrderTimeoutConfig.objects.update_or_create(pk=1, defaults={'minutes': 1})
        service = make_service(price=10000)
        clients = []
        for account in accounts:
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]); clients.append(client)
        def ok(response):
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')
        order_id = ok(clients[0].post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'STAFF',
            'client_request_id': 'isolated-worker-reject-timeout'}, format='json'))['id']
        ok(clients[2].post(f'/api/admin/orders/{order_id}/assign/', {'provider_id': accounts[1].pk}, format='json'))
        self.assertEqual(Order.objects.get(pk=order_id).pending_timeout_minutes, 1)
        worker = subprocess.Popen([sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            returned = ok(clients[1].post(f'/api/orders/orders/{order_id}/reject/', {'reason': '隔离验收档期变化'}, format='json'))
            self.assertEqual((returned['status'], returned['dispatch_route']), ('PENDING', 'STAFF'))
            self.assertEqual(Wallet.objects.get(user=boss).balance, 90000)
            queue = ok(clients[2].get('/api/admin/orders/', {'actionable_staff': '1'}))['list']
            self.assertIn(order_id, [row['id'] for row in queue])
            team = make_service(price=10000, billing_provider_count=2)
            for user, account in ((provider, accounts[1]), (partner, accounts[3])):
                user.escort_profile.account = account
                user.escort_profile.pass_tier = 'BLACK'
                user.escort_profile.pass_expires_at = timezone.now() + timedelta(days=1)
                user.escort_profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
                user.escort_profile.service_items.add(team)
            team_id = ok(clients[0].post('/api/orders/create/', {'service_id': team.pk, 'dispatch_route': 'PUBLIC',
                'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'isolated-worker-team-timeout'}, format='json'))['id']
            Order.objects.filter(pk=team_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
            ok(clients[1].post(f'/api/orders/orders/{team_id}/grab/', {'team_size': 2, 'partner_id': accounts[3].pk}, format='json'))
            application = OrderApplication.objects.get(order_id=team_id, applicant=accounts[1])
            self.assertEqual(Wallet.objects.get(user=boss).balance, 70000)
            stats = ok(clients[2].get('/api/admin/dashboard/stats'))
            self.assertEqual((stats['pending_paid_total'], stats['pending_paid_routes'], stats['paid_amount_total']),
                             (2, {'staff': 1, 'public': 1}, 30000))
            deadline = time.monotonic() + 85
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    self.fail('isolated Celery worker exited')
                order = Order.objects.get(pk=order_id)
                team_order = Order.objects.get(pk=team_id)
                if order.status == Order.Status.CANCELLED and team_order.status == Order.Status.CANCELLED:
                    break
                time.sleep(.25)
            else:
                self.fail('worker did not cancel the rejected order after its real one-minute deadline')
            self.assertEqual(order.payment_status, Order.PaymentStatus.REFUNDED)
            self.assertEqual(Wallet.objects.get(user=boss).balance, 100000)
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)
            self.assertEqual(OrderStatusLog.objects.filter(order=order, action=OrderStatusLog.Action.AUTO_CANCEL).count(), 1)
            self.assertEqual(Message.objects.filter(recipient_account=accounts[0], related_order_id=order.pk,
                                                     title='订单超时已取消').count(), 1)
            self.assertEqual(Transaction.objects.filter(order=team_order, tx_type='REFUND').count(), 1)
            for account, client in ((accounts[1], clients[1]), (accounts[3], clients[3])):
                notice = Message.objects.get(recipient_account=account, related_order_id=team_id, title='报名订单已取消')
                self.assertEqual(notice.action_url, f'/pages/orders/index?application_id={application.pk}')
                detail = ok(client.get(f'/api/orders/orders/{team_id}/applications/{application.pk}/mine/'))
                self.assertEqual((detail['order']['status'], detail['can_confirm']), ('CANCELLED', False))
            self.assertEqual(ok(clients[0].get(f'/api/orders/orders/{order_id}/'))['status'], 'CANCELLED')
            self.assertNotIn(order_id, [row['id'] for row in ok(clients[2].get('/api/admin/orders/', {'actionable_staff': '1'}))['list']])
            stats = ok(clients[2].get('/api/admin/dashboard/stats'))
            self.assertEqual((stats['pending_paid_total'], stats['pending_paid_routes'], stats['paid_amount_total']),
                             (0, {'staff': 0, 'public': 0}, 0))
            today = next(row for row in stats['daily_new_orders'] if row['date'] == timezone.localdate().isoformat())
            self.assertEqual((today['count'], today['net_amount']), (2, 0))
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill(); worker.wait(timeout=10)
