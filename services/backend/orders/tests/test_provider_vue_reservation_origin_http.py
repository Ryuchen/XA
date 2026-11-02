"""A converted appointment remains identifiable on the provider order."""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueReservationOriginHTTPTest(LiveServerTestCase):
    def test_boss_reservation_conversion_and_provider_handoff(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires current Vue build and Node')
        boss, provider, incoming = make_user(), make_provider(), make_provider()
        staff = make_console_user(['order:view', 'order:dispatch'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, incoming, staff)]
        for user, account in zip((provider, incoming), accounts[1:3]):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[3]
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(name='隔离预约服务', price=10000)
        clients = [APIClient() for _ in accounts]
        for client, account in zip(clients, accounts):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def ok(response):
            data = response.data
            self.assertEqual(data['code'], 0, data)
            return data['data']
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        reservation = ok(clients[0].post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk,
            'service_id': service.pk, 'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat()}, format='json'))
        rid = reservation['id']
        ok(clients[1].post(f'/api/orders/reservations/{rid}/confirm/', {}, format='json'))
        quote = ok(clients[0].get(f'/api/orders/reservations/{rid}/quote/'))
        result = ok(clients[0].post(f'/api/orders/reservations/{rid}/convert/', {'client_request_id':'vue-origin',
            'expected_amount':quote['amount'], 'expected_service':quote['service']}, format='json'))
        oid = result['order']['id']
        for client in clients[:2]:
            origin = ok(client.get(f'/api/orders/orders/{oid}/'))['reservation_origin']
            self.assertEqual((origin['id'], origin['can_open']), (rid, True))
        admin = ok(clients[3].get(f'/api/admin/orders/{oid}/'))
        self.assertEqual(admin['reservation_origin']['id'], rid)

        repo = Path(settings.BASE_DIR).parent.parent
        def browser(account, can_open):
            run = subprocess.run(['node', str(repo / 'scripts/provider-vue-reservation-origin-375.cjs')],
                input=json.dumps({'root':root, 'base':self.live_server_url, 'orderId':oid,
                    'orderNo':result['order']['order_no'], 'reservationId':rid,
                    'token':issue_account_tokens(account)[0], 'id':account.pk, 'canOpen':can_open,
                    'start':start.isoformat(), 'end':(start + timedelta(hours=1)).isoformat()}),
                text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        browser(accounts[1], True)
        if os.environ.get('XA_RESERVATION_ORIGIN_NATIVE') == '1':
            native = subprocess.run(['node', str(repo / 'scripts/native-reservation-origin-iphone12.cjs')],
                input=json.dumps({'base':self.live_server_url, 'token':issue_account_tokens(accounts[0])[0],
                    'bossId':accounts[0].pk, 'orderId':oid, 'reservationId':rid}),
                text=True, encoding='utf-8', capture_output=True, timeout=50, cwd=repo)
            self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        ok(clients[1].post(f'/api/orders/orders/{oid}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        ok(clients[3].post(f'/api/admin/orders/{oid}/transfer/', {'transfers':[
            {'old_provider_id':accounts[1].pk, 'new_provider_id':accounts[2].pk,
             'split_type':'PERCENT', 'split_value':40, 'penalty_amount':0}], 'reason':'隔离转单'}, format='json'))
        origin = ok(clients[2].get(f'/api/orders/orders/{oid}/'))['reservation_origin']
        self.assertEqual((origin['id'], origin['can_open']), (rid, False))
        browser(accounts[2], False)
        previous = ok(clients[1].get(f'/api/orders/orders/{oid}/'))['reservation_origin']
        self.assertEqual((previous['id'], previous['can_open']), (rid, True))
        browser(accounts[1], True)

        # Unconverted cancelled or expired appointments must not produce an order.
        def book(at):
            return ok(clients[0].post('/api/orders/reservations/', {'provider_account_id':accounts[1].pk,
                'service_id':service.pk, 'start_time':at.isoformat(),
                'end_time':(at + timedelta(hours=1)).isoformat()}, format='json'))
        cancelled = book(start + timedelta(hours=3))
        ok(clients[0].post(f"/api/orders/reservations/{cancelled['id']}/cancel/", {}, format='json'))
        self.assertEqual(ok(clients[1].get(f"/api/orders/reservations/{cancelled['id']}/"))['status'], 'CANCELLED')
        expired = book(start + timedelta(hours=6))
        now = timezone.now()
        Reservation.objects.filter(pk=expired['id']).update(start_time=now-timedelta(hours=2), end_time=now-timedelta(hours=1))
        self.assertEqual(ok(clients[1].get(f"/api/orders/reservations/{expired['id']}/"))['status'], 'EXPIRED')
        self.assertFalse(Reservation.objects.filter(pk__in=[cancelled['id'], expired['id']], order__isnull=False).exists())
