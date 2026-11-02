"""A committed reservation conversion with a lost reply remains reviewable."""
from datetime import timedelta
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
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
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/admin-reservation-convert-uncertain-375.cjs')],
            input=json.dumps({'api':self.live_server_url, 'root':root, 'token':tokens[2], 'bossToken':tokens[0],
                'id':rid, 'reservationNo':reservation['reservation_no'], 'fixed':os.environ.get('XA_RESERVATION_UNCERTAIN_FIXED') == '1', 'rejected':rejected}),
            text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
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
        if os.environ.get('XA_RESERVATION_UNCERTAIN_FIXED') != '1':
            self.skipTest('only run rejection regression against updated page')
        self.exercise(rejected=True)
