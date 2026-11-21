from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.reservation_deadlines import save_deadlines
from orders.models import Reservation
from orders.tests.factories import make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.models import EscortProfile


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False,
                   ALLOWED_HOSTS=['*'], CELERY_TASK_ALWAYS_EAGER=False)
class TeamReservationStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_ids_and_confirmation_remain_store_scoped(self):
        start = (timezone.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                save_deadlines(30, 60, 0, lambda *_: None)
                boss = make_user()
                boss_account = get_or_create_account_for_legacy_user(boss)
                link_legacy_relations(boss, boss_account)
                providers = []
                for _ in range(2):
                    user = make_provider(deposit_mode='EARNINGS')
                    account = get_or_create_account_for_legacy_user(user)
                    link_legacy_relations(user, account)
                    EscortProfile.objects.filter(user=user).update(account=account)
                    providers.append(account)
                service = make_service(price=1000, billing_provider_count=2, fulfillment_provider_count=2, price_unit='份')
                boss_token = issue_account_tokens(boss_account)[0]
                provider_token = issue_account_tokens(providers[0])[0]
                ids = [account.pk for account in providers]
                service_id = service.pk
            client = APIClient(HTTP_AUTHORIZATION='Bearer ' + boss_token)
            response = client.post('/api/orders/reservations/', {
                'provider_account_ids': ids, 'service_id': service_id,
                'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
            }, HTTP_HOST=host, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            rows.append((store, host, boss_token, provider_token, response.data['data']['id']))
        self.assertEqual(rows[0][4], rows[1][4])
        first, second = rows
        path = f'/api/orders/reservations/{first[4]}/confirm/'
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + first[3])
        self.assertNotEqual(client.post(path, {}, HTTP_HOST=second[1], format='json').status_code, 200)
        self.assertEqual(client.post(path, {}, HTTP_HOST=first[1], format='json').data['code'], 0)
        for entry, expected in ((first, 'CONFIRMED'), (second, 'PENDING')):
            with store_context(entry[0]):
                booking = Reservation.objects.get(pk=entry[4])
                self.assertEqual(booking.seats.get(number=1).status, expected)
