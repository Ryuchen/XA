"""An open provider reservation dialog follows another participant's action."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@skipUnless(os.environ.get('XA_RESERVATION_DIALOG_BROWSER') == '1', 'Requires isolated ASGI and H5 build')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationDialogLiveBrowserTest(APITransactionTestCase):
    def test_boss_cancellation_closes_open_provider_rejection(self):
        database, node = os.environ.get('XA_ASGI_NETWORK_TEST_DB'), shutil.which('node')
        if not database or connection.settings_dict['NAME'] != database or not node:
            self.fail('An isolated file-backed database and Node are required')
        boss, provider = make_user(), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider)]
        for user, account in zip((boss, provider), accounts): link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff = make_console_user(['order:view', 'order:dispatch'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        rid = response.data['data']['id']
        other_start = start + timedelta(hours=2)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': other_start.isoformat(), 'end_time': (other_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        other_id = response.data['data']['id']
        third_start = start + timedelta(hours=4)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': third_start.isoformat(), 'end_time': (third_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        third_id = response.data['data']['id']
        provider_client = APIClient(); provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        self.assertEqual(provider_client.post(f'/api/orders/reservations/{third_id}/confirm/', {}, format='json').data['code'], 0)
        fourth_start = start + timedelta(hours=6)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': fourth_start.isoformat(), 'end_time': (fourth_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        fourth_id = response.data['data']['id']
        self.assertEqual(provider_client.post(f'/api/orders/reservations/{fourth_id}/confirm/', {}, format='json').data['code'], 0)
        fifth_start = start + timedelta(hours=8)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': fifth_start.isoformat(), 'end_time': (fifth_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        fifth_id = response.data['data']['id']
        sixth_start = start + timedelta(hours=10)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': sixth_start.isoformat(), 'end_time': (sixth_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        sixth_id = response.data['data']['id']
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
            result = subprocess.run([node, str(repo / 'scripts/provider-reservation-dialog-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'providerToken': tokens[1],
                    'providerId': str(accounts[1].pk), 'reservationId': rid, 'otherId': other_id}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([node, str(repo / 'scripts/admin-reservation-convert-conflict-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'reservationId': sixth_id,
                                  'staffToken': issue_account_tokens(staff_account)[0], 'reject': True, 'normalReject': True}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            detail = client.get(f'/api/orders/reservations/{sixth_id}/').data['data']
            self.assertEqual((detail['status'], detail['reject_reason']), ('REJECTED', '填写中的原因'))
            result = subprocess.run([node, str(repo / 'scripts/admin-reservation-convert-conflict-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'reservationId': fifth_id,
                                  'staffToken': issue_account_tokens(staff_account)[0], 'reject': True}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([node, str(repo / 'scripts/admin-reservation-convert-conflict-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'reservationId': fourth_id,
                                  'staffToken': issue_account_tokens(staff_account)[0], 'reconnect': True}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([node, str(repo / 'scripts/admin-reservation-convert-conflict-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': tokens[0], 'reservationId': third_id,
                                  'staffToken': issue_account_tokens(staff_account)[0]}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
