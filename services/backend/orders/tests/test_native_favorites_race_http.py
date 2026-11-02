"""Exercise favorite mutations with real isolated accounts, HTTP and native page controller."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import ServiceFavorite
from orders.tests.factories import make_service, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class NativeFavoritesRaceTest(LiveServerTestCase):
    def test_service_detail_and_list_reconcile_real_mutations(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        account = get_or_create_account_for_legacy_user(make_user())
        service = make_service(name='隔离收藏服务', price=1000)
        token, refresh = issue_account_tokens(account)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-service-favorite-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk,
                'session': {'token': token, 'refresh': refresh, 'user': {'id': str(account.pk), 'role': 'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(ServiceFavorite.objects.filter(account=account, service=service).exists())

    def test_refresh_and_account_switch_after_real_removal(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        accounts = [get_or_create_account_for_legacy_user(make_user()) for _ in range(2)]
        service = make_service(name='隔离收藏服务', price=1000)
        sessions = []
        for account in accounts:
            token, refresh = issue_account_tokens(account)
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            result = client.post('/api/orders/favorites/toggle/', {'service_id': service.pk}, format='json')
            self.assertEqual(result.data['code'], 0)
            sessions.append({'token': token, 'refresh': refresh, 'user': {'id': str(account.pk), 'role': 'customer'}})
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-favorites-race-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'first': sessions[0], 'second': sessions[1]}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(ServiceFavorite.objects.filter(account=accounts[0], service=service).exists())
        self.assertTrue(ServiceFavorite.objects.filter(account=accounts[1], service=service).exists())
