"""A committed reservation conversion with a lost reply remains reviewable."""
from datetime import timedelta
import os

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminReservationConvertUncertainHTTPTest(LiveServerTestCase):
    def exercise(self, rejected=False):
        root = os.environ.get('XA_RESERVATION_BUILD')
        if not root:
            self.skipTest('requires isolated admin build')
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['order:view', 'order:dispatch'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000); wallet.account = accounts[0]; wallet.save(update_fields=['account'])
        service = make_service(price=10000)
        clients = [APIClient(), APIClient(), APIClient()]
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        for client, token in zip(clients, tokens): client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        response = clients[0].post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk,
            'service_id':service.pk, 'start_time':start.isoformat(), 'end_time':(start + timedelta(hours=1)).isoformat()}, format='json').data
        self.assertEqual(response['code'], 0, response)
        reservation = response['data']; rid = reservation['id']
        self.assertEqual(clients[1].post(f'/api/orders/reservations/{rid}/confirm/', {}, format='json').data['code'], 0)
        if rejected:
            cancelled = clients[0].post(f'/api/orders/reservations/{rid}/cancel/',
                {'reason': '预约安排调整'}, format='json').data
            self.assertEqual(cancelled['code'], 0, cancelled)
            denied = clients[2].post(f'/api/orders/reservations/{rid}/convert/',
                {'client_request_id': 'uncertain-rejected-convert'}, format='json').data
            self.assertNotEqual(denied['code'], 0, denied)
            self.assertIn('仅已确认的预约可转订单', denied['msg'])
        else:
            quote = clients[2].get(f'/api/orders/reservations/{rid}/quote/').data
            self.assertEqual(quote['code'], 0, quote)
            converted = clients[2].post(f'/api/orders/reservations/{rid}/convert/',
                {'client_request_id': 'uncertain-committed-convert', 'expected_amount': quote['data']['amount'],
                 'expected_service': quote['data']['service']}, format='json').data
            self.assertEqual(converted['code'], 0, converted)
        for client in clients:
            booking = client.get(f'/api/orders/reservations/{rid}/').data['data']
            self.assertEqual(booking['status'], 'CANCELLED' if rejected else 'CONVERTED')
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 50000 if rejected else 40000)
        if rejected:
            self.assertFalse(Order.objects.filter(customer_account=accounts[0]).exists())
        else:
            order = Order.objects.get(customer_account=accounts[0])
            self.assertEqual((order.status, order.provider_account_id), ('GRABBED', accounts[1].pk))
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
            for client in clients:
                order_path = f'/api/admin/orders/{order.pk}/' if client == clients[2] else f'/api/orders/orders/{order.pk}/'
                self.assertEqual(client.get(order_path).data['data']['id'], order.pk)
            messages = clients[1].get('/api/messages/').data['data']['messages']
            self.assertEqual(len([m for m in messages if m['title']=='预约已转为订单' and m['related_order_id']==order.pk]), 1)

    def test_commit_response_lost_and_detail_unavailable(self):
        self.exercise()

    def test_actual_state_rejection_keeps_specific_error(self):
        self.exercise(rejected=True)