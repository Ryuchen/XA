"""Real reservation pagination as new bookings arrive while lists are open."""
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


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ReservationPaginationLiveTest(LiveServerTestCase):
    def setUp(self):
        self.node = shutil.which('node')
        if not self.node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider()
        self.boss = get_or_create_account_for_legacy_user(boss)
        self.provider = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(boss, self.boss)
        link_legacy_relations(provider, self.provider)
        EscortProfile.objects.filter(user=provider).update(account=self.provider)
        make_wallet(boss, balance=50000)
        self.service = make_service(price=10000)
        self.boss_token, refresh = issue_account_tokens(self.boss)
        self.provider_token, _ = issue_account_tokens(self.provider)
        self.session = {'token': self.boss_token, 'refresh': refresh,
                        'user': {'id': str(self.boss.pk), 'role': 'customer', 'nickname': '隔离老板'}}
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.boss_token)
        self.initial_ids = []
        for day in range(1, 24):
            response = self.client.post('/api/orders/reservations/', self.payload(day), format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            self.initial_ids.append(response.data['data']['id'])

    def payload(self, day):
        start = (timezone.now() + timedelta(days=day)).replace(hour=10, minute=0, second=0, microsecond=0)
        return {'provider_account_id': self.provider.pk, 'service_id': self.service.pk,
                'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat()}

    def run_page(self, script, extra):
        repo = Path(settings.BASE_DIR).parent.parent
        data = {'base': self.live_server_url, 'listMode': True, 'session': self.session,
                'bossToken': self.boss_token, 'token': self.provider_token,
                'providerId': self.provider.pk, 'initialIds': self.initial_ids,
                'newReservation': self.payload(25), **extra}
        result = subprocess.run([self.node, str(repo / 'scripts' / script)], input=json.dumps(data),
                                text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_boss_list_keeps_loaded_pages_when_new_reservation_arrives(self):
        self.run_page('native-reservation-live.cjs', {})

    def test_provider_list_keeps_loaded_pages_when_new_reservation_arrives(self):
        self.run_page('provider-reservations-live.cjs', {})

    def test_cursor_disambiguates_identical_start_times_and_keeps_legacy_pages(self):
        all_ids = set(self.initial_ids)
        for _ in range(2):
            provider = make_provider()
            account = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, account)
            EscortProfile.objects.filter(user=provider).update(account=account)
            payload = self.payload(8)
            payload['provider_account_id'] = account.pk
            response = self.client.post('/api/orders/reservations/', payload, format='json').data
            self.assertEqual(response['code'], 0, response)
            all_ids.add(response['data']['id'])
        legacy = self.client.get('/api/orders/reservations/', {'page': 2, 'page_size': 20}).data['data']
        self.assertEqual((legacy['total'], len(legacy['items'])), (25, 5))
        cursor, retrieved = None, []
        while True:
            params = {'cursor': '1', 'page_size': 1}
            if cursor:
                params.update(before_time=cursor['start_time'], before_id=cursor['id'])
            response = self.client.get('/api/orders/reservations/', params).data
            self.assertEqual(response['code'], 0, response)
            retrieved.extend(row['id'] for row in response['data']['items'])
            cursor = response['data']['next_cursor']
            if not cursor:
                break
        self.assertEqual((len(retrieved), set(retrieved)), (25, all_ids))
