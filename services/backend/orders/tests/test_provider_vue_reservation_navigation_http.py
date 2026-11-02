"""Navigation between real reservations preserves the selected route."""
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
from orders.models import Reservation
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueReservationNavigationHTTPTest(LiveServerTestCase):
    def test_order_to_reservation_list_and_another_reservation(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        boss, provider, outsider = make_user(), make_provider(), make_provider()
        staff = make_console_user(['order:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff, outsider)]
        for user, account in zip((boss, provider, staff, outsider), accounts):
            link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        outsider.escort_profile.account = accounts[3]
        outsider.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(name='隔离预约导航服务', price=10000)
        clients = [APIClient() for _ in accounts]
        for client, account in zip(clients, accounts):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def ok(response):
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        def book(at, provider_id=None, remark=''):
            return ok(clients[0].post('/api/orders/reservations/', {'provider_account_id':provider_id or accounts[1].pk,
                'service_id':service.pk, 'start_time':at.isoformat(), 'end_time':(at+timedelta(hours=1)).isoformat(), 'remark':remark}, format='json'))
        first, second = book(start), book(start+timedelta(hours=3))
        expired = book(start+timedelta(hours=5))
        hidden = book(start+timedelta(hours=7), accounts[3].pk, '仅指定陪玩可见的预约备注')
        now = timezone.now()
        Reservation.objects.filter(pk=expired['id']).update(start_time=now-timedelta(hours=2), end_time=now-timedelta(hours=1))
        self.assertEqual(ok(clients[0].get(f"/api/orders/reservations/{expired['id']}/"))['status'], 'EXPIRED')
        self.assertEqual(clients[1].get(f"/api/orders/reservations/{hidden['id']}/").data['code'], 404)
        ok(clients[1].post(f"/api/orders/reservations/{first['id']}/confirm/", {}, format='json'))
        quote = ok(clients[0].get(f"/api/orders/reservations/{first['id']}/quote/"))
        converted = ok(clients[0].post(f"/api/orders/reservations/{first['id']}/convert/", {
            'client_request_id':'navigation-first', 'expected_amount':quote['amount'], 'expected_service':quote['service']}, format='json'))
        for client in clients[:3]:
            self.assertEqual(ok(client.get(f"/api/orders/reservations/{first['id']}/"))['status'], 'CONVERTED')
            self.assertEqual(ok(client.get(f"/api/orders/reservations/{second['id']}/"))['status'], 'PENDING')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-reservation-navigation-375.cjs')],
            input=json.dumps({'root':root, 'base':self.live_server_url, 'orderId':converted['order']['id'],
                'orderNo':converted['order']['order_no'], 'first':first, 'second':second, 'expired':expired, 'hidden':hidden,
                'id':accounts[1].pk, 'token':issue_account_tokens(accounts[1])[0],
                'fixed':os.environ.get('XA_RESERVATION_ROUTE_FIXED') == '1'}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        if os.environ.get('XA_RESERVATION_ROUTE_FIXED') == '1':
            for client in clients[:3]:
                self.assertEqual(ok(client.get(f"/api/orders/reservations/{second['id']}/"))['status'], 'CONFIRMED')
