"""A boss's real reservation details remain visible across participant roles."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeReservationFieldsHTTPTest(LiveServerTestCase):
    def test_actual_reservation_fields_are_available_to_each_party(self):
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['order:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts):
            link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        service = make_service(name='隔离预约服务', price=10000)
        clients = [APIClient() for _ in accounts]
        for client, account in zip(clients, accounts):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        fields = {'game_region':'隔离大区', 'game_nickname':'预约游戏昵称',
                  'game_uid':'LOCAL-UID-381', 'remark':'约定先联系再开局'}
        response = clients[0].post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk,
            'service_id': service.pk, 'start_time': start.isoformat(),
            'end_time': (start + timedelta(hours=1)).isoformat(), **fields}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        reservation_id = response.data['data']['id']
        for client in clients:
            response = client.get(f'/api/orders/reservations/{reservation_id}/')
            self.assertEqual(response.data['code'], 0, response.data)
            self.assertEqual({key:response.data['data'][key] for key in fields}, fields)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/reservation-fields-http.cjs')],
            input=json.dumps({'base':self.live_server_url, 'reservationId':reservation_id, 'fields':fields,
                'bossToken':issue_account_tokens(accounts[0])[0], 'bossId':accounts[0].pk,
                'providerToken':issue_account_tokens(accounts[1])[0], 'providerId':accounts[1].pk,
                'root':os.environ.get('XA_PROVIDER_VUE_ROOT'),
                'fixed':os.environ.get('XA_NATIVE_FIELDS_FIXED') == '1'}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        self.assertEqual(clients[1].post(f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json').data['code'], 0)
        quote = clients[0].get(f'/api/orders/reservations/{reservation_id}/quote/').data['data']
        converted = clients[0].post(f'/api/orders/reservations/{reservation_id}/convert/', {
            'client_request_id':'reservation-fields-conversion', 'expected_amount':quote['amount'],
            'expected_service':quote['service']}, format='json').data
        self.assertEqual(converted['code'], 0, converted)
        order_id = converted['data']['order']['id']
        for client, prefix in zip(clients, ('/api/orders/orders/', '/api/orders/orders/', '/api/admin/orders/')):
            response = client.get(f'{prefix}{order_id}/')
            self.assertEqual(response.data['code'], 0, response.data)
            self.assertEqual({key:response.data['data'][key] for key in fields}, fields)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 40000)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, order_id=order_id, tx_type='PAY').count(), 1)
