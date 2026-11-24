"""A cancelled order invalidates an open staff prompt without blocking later work."""
import os
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import ProviderReport, Transaction, Wallet


@skipUnless(os.environ.get('XA_ORDER_PROMPT_BROWSER') == '1', 'Requires isolated ASGI and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminCancelPromptLiveTest(APITransactionTestCase):
    def test_live_cancel_then_another_staff_cancel(self):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not database or connection.settings_dict['NAME'] != database:
            self.fail('An isolated file-backed database is required')
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

        def post(url, index, payload):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[index])
            response = client.post(url, payload, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        def get(url, index, params=None):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[index])
            response = client.get(url, params or {})
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        def cancel_and_replace():
            post(f"/api/orders/orders/{orders[0]['id']}/cancel/", 0, {})
            post(f"/api/admin/orders/{orders[1]['id']}/cancel/", 1, {'reason': '客服核实后取消'})
            post(f"/api/admin/orders/{service_order['id']}/start/", 1, {})
            post(f"/api/admin/orders/{service_order['id']}/complete/", 1, {})
            later = post('/api/orders/create/', 0, {'service_id': service.pk, 'provider_id': accounts[2].pk,
                'client_request_id': 'order-prompt-transfer-normal'})
            post(f"/api/admin/orders/{later['id']}/start/", 1, {})
            post(f"/api/admin/orders/{later['id']}/transfer/", 1, {'transfers': [{
                'old_provider_id': accounts[2].pk, 'new_provider_id': accounts[3].pk,
                'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0}]})
            return later

        def check_cancelled_and_later(later):
            self.assertEqual(Order.objects.filter(pk__in=[row['id'] for row in orders], status='CANCELLED').count(), 2)
            self.assertEqual(Transaction.objects.filter(order_id__in=[row['id'] for row in orders], tx_type='REFUND').count(), 2)
            self.assertEqual(Order.objects.get(pk=service_order['id']).status, 'COMPLETED')
            later_row = Order.objects.get(customer_account=accounts[0], status='IN_SERVICE')
            self.assertEqual(later_row.pk, later['id'])
            self.assertEqual(later_row.provider_account_id, accounts[3].pk)
            self.assertEqual(Wallet.objects.get(user=boss).balance, 30000)
            for index in (0, 2, 3):
                detail = get(f'/api/orders/orders/{later_row.pk}/', index)
                self.assertEqual((detail['id'], detail['status']), (later_row.pk, 'IN_SERVICE'))
                self.assertEqual([person['id'] for person in detail['participants']], [accounts[3].pk])
                if index in (2, 3):
                    self.assertEqual(detail['my_assignment_released'], index == 2)
                    self.assertEqual(detail['expected_income'], 0 if index == 2 else 8000)
            return later_row

        def open_later_reports(later_row):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
            refused = client.post('/api/wallet/reports/', {'order_id': later_row.pk}, format='json')
            self.assertIn('无需提交报单', refused.data['msg'])
            post('/api/wallet/reports/', 3, {'order_id': later_row.pk})
            self.assertTrue(ProviderReport.objects.filter(order=later_row, provider_account=accounts[3]).exists())

        def complete_later_and_retained(later_row):
            post(f'/api/admin/orders/{later_row.pk}/complete/', 1, {})
            retained = post('/api/orders/create/', 0, {'service_id': service.pk, 'provider_id': accounts[2].pk,
                'client_request_id': 'order-prompt-transfer-retained'})
            post(f"/api/admin/orders/{retained['id']}/start/", 1, {})
            post(f"/api/admin/orders/{retained['id']}/transfer/", 1, {'transfers': [{
                'old_provider_id': accounts[2].pk, 'new_provider_id': accounts[3].pk,
                'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}]})
            self.assertEqual(list(Transaction.objects.filter(order_id=retained['id'], wallet__account=accounts[2],
                tx_type='INCOME').values_list('amount', flat=True)), [3200])
            self.assertFalse(Transaction.objects.filter(order_id=retained['id'], wallet__account=accounts[3],
                tx_type='INCOME').exists())
            for index, expected in ((2, True), (3, False)):
                detail = get(f"/api/orders/orders/{retained['id']}/", index)
                self.assertEqual(detail['my_assignment_settled'], expected)
                rows = get('/api/orders/orders/', index, {'status': 'in_progress'})
                self.assertEqual(next(row for row in rows if row['id'] == retained['id'])['my_assignment_settled'], expected)
            return retained

        def open_retained_reports(retained):
            for index in (2, 3):
                post('/api/wallet/reports/', index, {'order_id': retained['id']})
            self.assertEqual(ProviderReport.objects.filter(order_id=retained['id'], provider_account__in=accounts[2:4]).count(), 2)

        def complete_retained(retained):
            post(f"/api/admin/orders/{retained['id']}/complete/", 1, {})
            self.assertEqual(list(Transaction.objects.filter(order_id=retained['id'], wallet__account=accounts[2],
                tx_type='INCOME').values_list('amount', flat=True)), [3200])
            self.assertEqual(list(Transaction.objects.filter(order_id=retained['id'], wallet__account=accounts[3],
                tx_type='INCOME').values_list('amount', flat=True)), [4800])
            self.assertEqual(list(Transaction.objects.filter(order_id=retained['id'], tx_type='SHOP_INCOME')
                .values_list('amount', flat=True)), [2000])
            stats = get('/api/admin/dashboard/stats', 1)
            self.assertEqual((stats['paid_amount_total'], stats['paid_order_total'], stats['daily_new_orders'][-1]['net_amount']), (30000, 3, 30000))
            recent = next(row for row in stats['recent_orders'] if row['id'] == retained['id'])
            self.assertEqual(recent['provider_name'], '隔离替补陪玩')

        def refund_part(retained):
            preview = get(f"/api/admin/orders/{retained['id']}/refund-preview/", 1, {'refund_amount': 2500})
            post(f"/api/admin/orders/{retained['id']}/refund/", 1, {'reason': '隔离退款实时验收',
                'client_request_id': 'retained-share-partial-refund', 'plan_token': preview['plan_token'],
                'refund_amount': 2500})

        def check_partial(retained):
            stats = get('/api/admin/dashboard/stats', 1)
            self.assertEqual((stats['paid_amount_total'], stats['paid_order_total'], stats['daily_new_orders'][-1]['net_amount']), (27500, 3, 27500))
            for index, expected_net in ((2, 2400), (3, 3600)):
                detail = get(f"/api/orders/orders/{retained['id']}/", index)
                self.assertEqual(detail['payment_status'], 'PARTIALLY_REFUNDED')
                self.assertEqual(detail['refund_summary']['net_income'], expected_net)
            boss_detail = get(f"/api/orders/orders/{retained['id']}/", 0)
            self.assertEqual((boss_detail['refund_summary']['amount'], boss_detail['refund_summary']['remaining_amount'],
                              boss_detail['refund_summary']['refund_count']), (2500, 7500, 1))

        def refund_remainder(retained):
            preview = get(f"/api/admin/orders/{retained['id']}/refund-preview/", 1)
            post(f"/api/admin/orders/{retained['id']}/refund/", 1, {'reason': '隔离退款实时验收',
                'client_request_id': 'retained-share-refund-remainder', 'plan_token': preview['plan_token']})

        def check_full(retained):
            stats = get('/api/admin/dashboard/stats', 1)
            self.assertEqual((stats['paid_amount_total'], stats['paid_order_total'], stats['daily_new_orders'][-1]['net_amount']), (20000, 2, 20000))
            for index in (2, 3):
                detail = get(f"/api/orders/orders/{retained['id']}/", index)
                self.assertEqual((detail['payment_status'], detail['refund_summary']['net_income']), ('REFUNDED', 0))
            detail = get(f"/api/orders/orders/{retained['id']}/", 0)
            self.assertEqual((detail['payment_status'], detail['refund_summary']['amount']), ('REFUNDED', 10000))
            self.assertEqual(Wallet.objects.get(user=boss).balance, 30000)

        def transfer_owner():
            owner_order = post('/api/orders/create/', 0, {'service_id': service.pk, 'provider_id': accounts[2].pk,
                'client_request_id': 'owner-transfer-realtime'})
            post(f"/api/admin/orders/{owner_order['id']}/start/", 1, {})
            post(f"/api/admin/orders/{owner_order['id']}/transfer/", 1, {'transfers': [{
                'old_provider_id': accounts[2].pk, 'new_provider_id': accounts[3].pk,
                'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}]})
            return owner_order

        def check_owner(owner_order):
            detail = get(f"/api/orders/orders/{owner_order['id']}/", 0)
            self.assertEqual(detail['status'], 'IN_SERVICE')
            self.assertEqual(detail['provider_name_snapshot'], '隔离替补陪玩')
            self.assertEqual([person['id'] for person in detail['participants']], [accounts[3].pk])
            self.assertIsNone(detail['my_assignment_settled'])

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={tokens[index]}') for index in (1, 0, 2, 3)]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])
            staff_socket, boss_socket, old_socket, new_socket = sockets

            async def wait_event(socket, predicate):
                for _ in range(20):
                    event = await socket.receive_json_from(timeout=5)
                    if predicate(event):
                        return event
                self.fail('实时事件未送达')

            try:
                later = await database_sync_to_async(cancel_and_replace)()
                await wait_event(staff_socket, lambda event: event['type'] == 'order_status_update'
                                 and event['data'].get('id') == orders[0]['id'] and event['data'].get('status') == 'CANCELLED')
                later_row = await database_sync_to_async(check_cancelled_and_later)(later)
                await database_sync_to_async(open_later_reports)(later_row)
                retained = await database_sync_to_async(complete_later_and_retained)(later_row)
                await database_sync_to_async(open_retained_reports)(retained)
                await database_sync_to_async(complete_retained)(retained)
                await database_sync_to_async(refund_part)(retained)
                await wait_event(boss_socket, lambda event: event['type'] == 'order_status_update'
                                 and event['data'].get('id') == retained['id']
                                 and event['data'].get('payment_status') == 'PARTIALLY_REFUNDED')
                await database_sync_to_async(check_partial)(retained)
                await database_sync_to_async(refund_remainder)(retained)
                await wait_event(boss_socket, lambda event: event['type'] == 'order_status_update'
                                 and event['data'].get('id') == retained['id']
                                 and event['data'].get('payment_status') == 'REFUNDED')
                await database_sync_to_async(check_full)(retained)
                owner_order = await database_sync_to_async(transfer_owner)()
                for socket, expected in ((old_socket, True), (new_socket, False)):
                    event = await wait_event(socket, lambda event: event['type'] == 'order_status_update'
                                             and event['data'].get('id') == owner_order['id'])
                    self.assertEqual(event['data']['my_assignment_settled'], expected)
                await database_sync_to_async(check_owner)(owner_order)
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()