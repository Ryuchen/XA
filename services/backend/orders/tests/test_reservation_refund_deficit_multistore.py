from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.reservation_deadlines import save_deadlines
from orders.models import Reservation
from orders.tests.factories import make_completed_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import RefundDepositDeficit, Transaction, get_platform_wallet
from wallet.services import get_wallet


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False,
                   ALLOWED_HOSTS=['*'], CELERY_TASK_ALWAYS_EAGER=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False)
class ReservationConfirmedBeforeDeficitTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_paid_existing_single_and_team_reservations_in_both_stores(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                save_deadlines(30, 60, 0, lambda *_: None)
                boss_user = make_user()
                boss = get_or_create_account_for_legacy_user(boss_user)
                link_legacy_relations(boss_user, boss)
                customer_wallet = get_wallet(user=boss_user, account=boss)
                customer_wallet.balance = 10000; customer_wallet.save(update_fields=['balance'])
                self.platform = get_platform_wallet()
                self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
                providers = []
                for _ in range(3):
                    user = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
                    account = get_or_create_account_for_legacy_user(user)
                    link_legacy_relations(user, account)
                    user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
                    providers.append((user, account))
                start = (timezone.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
                client = lambda account: APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                owner = client(boss)
                for index, selected in enumerate((providers[:1], providers[1:])):
                    service = make_service(price=1000, billing_provider_count=len(selected), price_unit='份')
                    payload = {'service_id': service.pk, 'start_time': start.isoformat(),
                               'end_time': (start + timedelta(hours=1)).isoformat(),
                               'provider_account_ids': [account.pk for _, account in selected]}
                    booking = owner.post('/api/orders/reservations/', payload, format='json')
                    self.assertEqual(booking.data['code'], 0, booking.data)
                    rid = booking.data['data']['id']
                    for _, account in selected:
                        result = client(account).post(f'/api/orders/reservations/{rid}/confirm/', {}, format='json')
                        self.assertEqual(result.data['code'], 0, result.data)
                    self.provider, self.account = selected[0]
                    self.boss = boss_user
                    self.profile = self.provider.escort_profile
                    self.wallet = get_wallet(user=self.provider, account=self.account)
                    self.order = make_completed_order(self.boss, self.provider, amount=10000,
                        provider_account=self.account, payment_status='PAID')
                    deficit = RefundDepositDeficitTest.deficit(self, 200)
                    before = customer_wallet.balance
                    # An unrelated new order is still subject to the deposit gate.
                    fresh = owner.post('/api/orders/create/', {'service_id': service.pk,
                        'provider_ids': [a.pk for _, a in selected], 'client_request_id': f'new-{index}'}, format='json')
                    self.assertNotEqual(fresh.data.get('code'), 0, fresh.data)
                    quote = owner.get(f'/api/orders/reservations/{rid}/quote/')
                    self.assertEqual(quote.data['code'], 0, quote.data)
                    # A persisted gap preceding confirmation is not an eligible promise.
                    confirmed_at = (Reservation.objects.get(pk=rid).confirmed_at if len(selected) == 1
                                    else Reservation.objects.get(pk=rid).seats.get(provider_account=self.account).confirmed_at)
                    RefundDepositDeficit.objects.filter(pk=deficit.pk).update(created_at=confirmed_at - timedelta(seconds=1))
                    rejected = owner.post(f'/api/orders/reservations/{rid}/convert/',
                        {'client_request_id': f'convert-{index}', 'expected_amount': quote.data['data']['amount'],
                         'expected_service': quote.data['data']['service']}, format='json')
                    self.assertNotEqual(rejected.data.get('code'), 0, rejected.data)
                    customer_wallet.refresh_from_db(); self.assertEqual(customer_wallet.balance, before)
                    RefundDepositDeficit.objects.filter(pk=deficit.pk).update(created_at=confirmed_at + timedelta(microseconds=1))
                    result = owner.post(f'/api/orders/reservations/{rid}/convert/',
                        {'client_request_id': f'convert-{index}', 'expected_amount': quote.data['data']['amount'],
                         'expected_service': quote.data['data']['service']}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    oid = result.data['data']['order']['id']
                    customer_wallet.refresh_from_db()
                    self.assertEqual(customer_wallet.balance, before - quote.data['data']['amount'])
                    self.assertTrue(Transaction.objects.filter(order_id=oid, wallet=customer_wallet, tx_type='PAY', status='SUCCESS').exists())
