"""A cancelled order invalidates an open staff prompt without blocking later work."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import ProviderReport, Transaction, Wallet


@skipUnless(os.environ.get('XA_ORDER_PROMPT_BROWSER') == '1', 'Requires isolated ASGI and browser')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminCancelPromptLiveTest(APITransactionTestCase):
    def test_live_cancel_then_another_staff_cancel(self):
        database, node = os.environ.get('XA_ASGI_NETWORK_TEST_DB'), shutil.which('node')
        if not database or connection.settings_dict['NAME'] != database or not node:
            self.fail('An isolated file-backed database and Node are required')
        boss, staff, provider = make_user(), make_console_user(['dashboard:view', 'order:view', 'order:cancel', 'order:dispatch', 'order:settle', 'order:refund', 'escort:view']), make_provider()
        replacement = make_provider(display_name='隔离替补陪玩')
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, provider, replacement)]
        for user, account in zip((boss, staff, provider, replacement), accounts): link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        provider.escort_profile.account = accounts[2]; provider.escort_profile.save(update_fields=['account'])
        replacement.escort_profile.account = accounts[3]; replacement.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        orders = []
        for index in range(2):
            response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'STAFF',
                'client_request_id': f'order-prompt-live-{index}'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            orders.append(response.data['data'])
        response = client.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': accounts[2].pk,
            'client_request_id': 'order-prompt-service'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        service_order = response.data['data']
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None: self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                except OSError: time.sleep(.1)
            else: self.fail('isolated ASGI server did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/admin-order-cancel-prompt-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'staffToken': tokens[1],
                                  'orders': orders, 'serviceOrder': service_order,
                                  'providerId': accounts[2].pk, 'serviceId': service.pk}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Order.objects.filter(pk__in=[row['id'] for row in orders], status='CANCELLED').count(), 2)
            self.assertEqual(Transaction.objects.filter(order_id__in=[row['id'] for row in orders], tx_type='REFUND').count(), 2)
            self.assertEqual(Order.objects.get(pk=service_order['id']).status, 'COMPLETED')
            later = Order.objects.get(customer_account=accounts[0], status='IN_SERVICE')
            self.assertEqual(later.provider_account_id, accounts[3].pk)
            self.assertEqual(Wallet.objects.get(user=boss).balance, 30000)
            for index in (0, 2, 3):
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[index])
                response = client.get(f'/api/orders/orders/{later.pk}/')
                self.assertEqual(response.data['code'], 0, response.data)
                detail = response.data['data']
                self.assertEqual((detail['id'], detail['status']), (later.pk, 'IN_SERVICE'))
                self.assertEqual([person['id'] for person in detail['participants']], [accounts[3].pk])
                if index in (2, 3):
                    self.assertEqual(detail['my_assignment_released'], index == 2)
                    self.assertEqual(detail['expected_income'], 0 if index == 2 else 8000)
            result = subprocess.run([node, str(repo / 'scripts/provider-transfer-375-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'orderNo': later.order_no,
                                 'providers': [{'id': accounts[2].pk, 'token': tokens[2], 'released': True, 'income': '0币', 'canReport': False},
                                               {'id': accounts[3].pk, 'token': tokens[3], 'released': False, 'income': '800币', 'canReport': True}]}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(ProviderReport.objects.filter(order=later, provider_account=accounts[3]).exists())
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            completed = client.post(f'/api/admin/orders/{later.pk}/complete/', {}, format='json')
            self.assertEqual(completed.data['code'], 0, completed.data)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
            created = client.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': accounts[2].pk,
                'client_request_id': 'order-prompt-transfer-retained'}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            retained = created.data['data']
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            started = client.post(f"/api/admin/orders/{retained['id']}/start/", {}, format='json')
            self.assertEqual(started.data['code'], 0, started.data)
            transferred = client.post(f"/api/admin/orders/{retained['id']}/transfer/", {'transfers': [{
                'old_provider_id': accounts[2].pk, 'new_provider_id': accounts[3].pk,
                'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0,
            }]}, format='json')
            self.assertEqual(transferred.data['code'], 0, transferred.data)
            self.assertEqual(list(Transaction.objects.filter(order_id=retained['id'], wallet__account=accounts[2],
                tx_type='INCOME').values_list('amount', flat=True)), [3200])
            self.assertFalse(Transaction.objects.filter(order_id=retained['id'], wallet__account=accounts[3],
                tx_type='INCOME').exists())
            for index, expected in ((2, True), (3, False)):
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[index])
                detail = client.get(f"/api/orders/orders/{retained['id']}/").data['data']
                self.assertEqual(detail['my_assignment_settled'], expected)
                rows = client.get('/api/orders/orders/', {'status': 'in_progress'}).data['data']
                self.assertEqual(next(row for row in rows if row['id'] == retained['id'])['my_assignment_settled'], expected)
            result = subprocess.run([node, str(repo / 'scripts/provider-transfer-375-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'orderNo': retained['order_no'],
                                 'providers': [{'id': accounts[2].pk, 'token': tokens[2], 'released': True, 'income': '320币', 'canReport': True, 'settledNotice': True},
                                               {'id': accounts[3].pk, 'token': tokens[3], 'released': False, 'income': '480币', 'canReport': True,
                                                'waitingNotice': '服务完成时结算，实际到账及冻结以钱包流水为准'}]}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(ProviderReport.objects.filter(order_id=retained['id'], provider_account__in=accounts[2:4]).count(), 2)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            self.assertEqual(client.post(f"/api/admin/orders/{retained['id']}/complete/", {}, format='json').data['code'], 0)
            self.assertEqual(list(Transaction.objects.filter(order_id=retained['id'], wallet__account=accounts[2],
                tx_type='INCOME').values_list('amount', flat=True)), [3200])
            self.assertEqual(list(Transaction.objects.filter(order_id=retained['id'], wallet__account=accounts[3],
                tx_type='INCOME').values_list('amount', flat=True)), [4800])
            self.assertEqual(list(Transaction.objects.filter(order_id=retained['id'], tx_type='SHOP_INCOME')
                .values_list('amount', flat=True)), [2000])
            stats = client.get('/api/admin/dashboard/stats').data['data']
            self.assertEqual((stats['paid_amount_total'], stats['paid_order_total'], stats['daily_new_orders'][-1]['net_amount']), (30000, 3, 30000))
            recent = next(row for row in stats['recent_orders'] if row['id'] == retained['id'])
            self.assertEqual(recent['provider_name'], '隔离替补陪玩')
            result = subprocess.run([node, str(repo / 'scripts/native-boss-refund-realtime-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'bossId': accounts[0].pk,
                    'staffToken': tokens[1], 'orderId': retained['id'], 'before': 'PAID', 'after': 'PARTIALLY_REFUNDED',
                    'amount': 2500, 'requestId': 'retained-share-partial-refund', 'refunded': '250', 'remaining': '750', 'recordCount': 1,
                    'dashboard': {'before': '3000币', 'after': '2750币'}}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            stats = client.get('/api/admin/dashboard/stats').data['data']
            self.assertEqual((stats['paid_amount_total'], stats['paid_order_total'], stats['daily_new_orders'][-1]['net_amount']), (27500, 3, 27500))
            for index, expected_net in ((2, 2400), (3, 3600)):
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[index])
                detail = client.get(f"/api/orders/orders/{retained['id']}/").data['data']
                self.assertEqual(detail['payment_status'], 'PARTIALLY_REFUNDED')
                self.assertEqual(detail['refund_summary']['net_income'], expected_net)
            result = subprocess.run([node, str(repo / 'scripts/provider-transfer-375-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'orderNo': retained['order_no'],
                                 'completed': True, 'partialRefund': True,
                                 'providers': [{'id': accounts[2].pk, 'token': tokens[2], 'released': True, 'income': '320币', 'netIncome': '240'},
                                               {'id': accounts[3].pk, 'token': tokens[3], 'released': False, 'income': '480币', 'netIncome': '360'}]}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            result = subprocess.run([node, str(repo / 'scripts/native-boss-refund-realtime-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'bossId': accounts[0].pk,
                    'staffToken': tokens[1], 'orderId': retained['id'], 'before': 'PARTIALLY_REFUNDED', 'after': 'REFUNDED',
                    'requestId': 'retained-share-refund-remainder', 'refunded': '1000', 'remaining': '0', 'recordCount': 2}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            stats = client.get('/api/admin/dashboard/stats').data['data']
            self.assertEqual((stats['paid_amount_total'], stats['paid_order_total'], stats['daily_new_orders'][-1]['net_amount']), (20000, 2, 20000))
            for index in (2, 3):
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[index])
                detail = client.get(f"/api/orders/orders/{retained['id']}/").data['data']
                self.assertEqual((detail['payment_status'], detail['refund_summary']['net_income']), ('REFUNDED', 0))
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
            detail = client.get(f"/api/orders/orders/{retained['id']}/").data['data']
            self.assertEqual((detail['payment_status'], detail['refund_summary']['amount']), ('REFUNDED', 10000))
            self.assertEqual(Wallet.objects.get(user=boss).balance, 30000)
            result = subprocess.run([node, str(repo / 'scripts/provider-transfer-375-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'orderNo': retained['order_no'], 'fullRefund': True,
                                 'providers': [{'id': accounts[2].pk, 'token': tokens[2], 'released': True, 'income': '320币'},
                                               {'id': accounts[3].pk, 'token': tokens[3], 'released': False, 'income': '480币'}]}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
            owner_order = client.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': accounts[2].pk,
                'client_request_id': 'owner-transfer-realtime'}, format='json')
            self.assertEqual(owner_order.data['code'], 0, owner_order.data)
            owner_order = owner_order.data['data']
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            self.assertEqual(client.post(f"/api/admin/orders/{owner_order['id']}/start/", {}, format='json').data['code'], 0)
            result = subprocess.run([node, str(repo / 'scripts/native-boss-transfer-realtime-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'bossId': accounts[0].pk,
                    'staffToken': tokens[1], 'orderId': owner_order['id'], 'oldId': accounts[2].pk, 'newId': accounts[3].pk,
                    'oldToken': tokens[2], 'newToken': tokens[3],
                    'oldName': owner_order['provider_name_snapshot'], 'newName': '隔离替补陪玩'}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
