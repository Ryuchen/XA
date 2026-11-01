"""Native expired-reservation recovery against the actual isolated API."""
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
from orders.models import Reservation
from orders.tests.factories import make_provider, make_service, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NativeExpiredReservationHTTPTest(LiveServerTestCase):
    def test_expired_details_rebook_same_service_and_provider(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000)
        token, refresh = issue_account_tokens(boss_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = client.post('/api/orders/reservations/', {
            'provider_account_id': provider_account.pk, 'service_id': service.pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json').data
        self.assertEqual(created['code'], 0, created)
        original_id = created['data']['id']
        unavailable = make_service(name='隔离已下架服务', price=11000)
        other_start = start + timedelta(hours=3)
        old = client.post('/api/orders/reservations/', {
            'provider_account_id': provider_account.pk, 'service_id': unavailable.pk,
            'start_time': other_start.isoformat(), 'end_time': (other_start + timedelta(hours=1)).isoformat(),
        }, format='json').data
        self.assertEqual(old['code'], 0, old)
        unavailable.is_active = False
        unavailable.save(update_fields=['is_active'])
        now = timezone.now()
        Reservation.objects.filter(pk__in=[original_id, old['data']['id']]).update(start_time=now - timedelta(hours=2), end_time=now - timedelta(hours=1))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-expired-reservation-live.cjs')], input=json.dumps({
            'base': self.live_server_url, 'id': original_id, 'unavailableId': old['data']['id'],
            'unavailableServiceId': unavailable.pk, 'providerId': provider_account.pk, 'serviceId': service.pk,
            'session': {'token': token, 'refresh': refresh, 'user': {'id': str(boss_account.pk), 'role': 'customer'}},
        }), text=True, encoding='utf-8', capture_output=True, timeout=35, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(Reservation.objects.get(pk=original_id).status, 'EXPIRED')
        self.assertEqual(Reservation.objects.filter(customer_account=boss_account, provider_account=provider_account,
                                                    service=service, status='PENDING').count(), 1)
        provider_result = subprocess.run([node, str(repo / 'scripts/provider-reservations-live.cjs')], input=json.dumps({
            'base': self.live_server_url, 'token': issue_account_tokens(provider_account)[0],
            'firstId': original_id, 'providerId': provider_account.pk, 'expiredMode': True,
        }), text=True, encoding='utf-8', capture_output=True, timeout=35, cwd=repo)
        self.assertEqual(provider_result.returncode, 0, provider_result.stderr + provider_result.stdout)
