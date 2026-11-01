"""Actual provider page and reservation API on an isolated database."""
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

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderReservationPageTest(LiveServerTestCase):
    def test_real_reservation_confirm_and_reject_from_provider_page(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        make_wallet(boss, balance=50000)
        provider = make_provider()
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, provider_account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        service = make_service(price=10000)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        ids = []
        for days in (1, 2):
            start = (timezone.now() + timedelta(days=days)).replace(hour=10, minute=0, second=0, microsecond=0)
            response = client.post('/api/orders/reservations/', {
                'provider_account_id': provider_account.pk, 'service_id': service.pk,
                'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
                'game_rounds': 1, 'game_nickname': '隔离老板',
            }, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            ids.append(response.data['data']['id'])
        token, _ = issue_account_tokens(provider_account)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/provider-reservations-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'token': token, 'firstId': ids[0], 'providerId': provider_account.pk,
                              'firstStart': Reservation.objects.get(pk=ids[0]).start_time.isoformat()}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo,
            env={**os.environ, 'TZ': 'America/New_York'})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        first, second = [Reservation.objects.get(pk=pk) for pk in ids]
        self.assertEqual((first.status, first.cancel_reason, second.status, second.reject_reason),
                         ('CANCELLED', '临时有事', 'REJECTED', '时间不合适'))
