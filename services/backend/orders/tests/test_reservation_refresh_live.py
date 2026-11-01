"""Page event handling against committed reservations on the real API."""
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationRefreshLiveTest(LiveServerTestCase):
    def test_provider_stale_reject_dialog_after_boss_cancels(self):
        boss, provider = make_user(), make_provider()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        service = make_service(price=1000)
        boss_token, _ = issue_account_tokens(boss_account)
        provider_token, _ = issue_account_tokens(provider_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        response = client.post('/api/orders/reservations/', {
            'provider_account_id': provider_account.pk, 'service_id': service.pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
            'game_rounds': 1,
        }, format='json').data
        self.assertEqual(response['code'], 0, response)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        repo = Path(settings.BASE_DIR).parent.parent
        args = {'base': self.live_server_url, 'bossToken': boss_token, 'token': provider_token,
                'providerId': provider_account.pk, 'firstId': response['data']['id'], 'staleActionMode': True}
        result = subprocess.run([node, str(repo / 'scripts/provider-reservations-live.cjs')],
                                input=json.dumps(args), text=True, encoding='utf-8',
                                capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_counterparty_updates_while_pages_are_open(self):
        boss, provider = make_user(), make_provider()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        make_wallet(boss, balance=50000)
        Wallet.objects.filter(user=boss).update(account=boss_account)
        service = make_service(price=1000)
        boss_token, refresh = issue_account_tokens(boss_account)
        provider_token, _ = issue_account_tokens(provider_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        ids = []
        for day in (1, 2, 3, 4):
            start = (timezone.now() + timedelta(days=day)).replace(hour=10, minute=0, second=0, microsecond=0)
            response = client.post('/api/orders/reservations/', {
                'provider_account_id': provider_account.pk, 'service_id': service.pk,
                'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
                'game_rounds': 1,
            }, format='json').data
            self.assertEqual(response['code'], 0, response)
            ids.append(response['data']['id'])
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        repo = Path(settings.BASE_DIR).parent.parent
        common = {'base': self.live_server_url, 'bossToken': boss_token, 'token': provider_token,
                  'providerId': provider_account.pk}
        for script, args in (
            ('native-reservation-refresh-live.cjs', {**common, 'id': ids[0], 'listId': ids[2], 'account': boss_account.pk, 'refresh': refresh}),
            ('provider-reservations-live.cjs', {**common, 'firstId': ids[1], 'listId': ids[3], 'eventMode': True}),
        ):
            result = subprocess.run([node, str(repo / 'scripts' / script)], input=json.dumps(args), text=True,
                                    encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
