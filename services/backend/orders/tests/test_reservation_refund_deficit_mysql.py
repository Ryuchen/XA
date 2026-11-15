from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.reservation_deadlines import save_deadlines
from orders.tests.factories import make_completed_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@skipUnless(connection.vendor == 'mysql', 'requires an isolated MySQL server')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False,
                   ALLOWED_HOSTS=['*'], CELERY_TASK_ALWAYS_EAGER=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False)
class ReservationRefundDeficitMySQLTest(TransactionTestCase):
    databases = {'default'}

    def test_refund_deficit_commit_serializes_before_reservation_payment(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        db = connection.settings_dict
        if db['HOST'] != '127.0.0.1' or not db['NAME'].startswith('test_xa_test_d200_'):
            self.fail('仅允许独立临时测试库')
        store = resolve_store()
        with store_context(store):
            save_deadlines(30, 60, 0, lambda *_: None)
            self.boss = make_user()
            boss = get_or_create_account_for_legacy_user(self.boss)
            link_legacy_relations(self.boss, boss)
            self.provider = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
            self.account = get_or_create_account_for_legacy_user(self.provider)
            link_legacy_relations(self.provider, self.account)
            self.profile = self.provider.escort_profile
            self.profile.account = self.account; self.profile.save(update_fields=['account'])
            self.wallet = get_wallet(user=self.provider, account=self.account)
            self.platform = get_platform_wallet()
            self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
            self.order = make_completed_order(self.boss, self.provider, amount=10000,
                provider_account=self.account, payment_status='PAID')
            buyer_wallet = get_wallet(user=self.boss, account=boss)
            buyer_wallet.balance = 10000; buyer_wallet.save(update_fields=['balance'])
            service = make_service(price=1000, billing_provider_count=1, price_unit='份')
            token = issue_account_tokens(boss)[0]
            owner = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
            worker = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            start = (timezone.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
            response = owner.post('/api/orders/reservations/', {'service_id': service.pk,
                'provider_account_id': self.account.pk, 'start_time': start.isoformat(),
                'end_time': (start + timedelta(hours=1)).isoformat()}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            rid = response.data['data']['id']
            response = worker.post(f'/api/orders/reservations/{rid}/confirm/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            quote = owner.get(f'/api/orders/reservations/{rid}/quote/').data['data']
            ready = Event()
            def pay():
                close_old_connections()
                try:
                    with store_context(store):
                        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                        ready.set()
                        result = client.post(f'/api/orders/reservations/{rid}/convert/',
                            {'client_request_id': 'mysql-d200', 'expected_amount': quote['amount'],
                             'expected_service': quote['service']}, format='json')
                        return result.data
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    EscortProfile.objects.select_for_update().get(pk=self.profile.pk)
                    RefundDepositDeficitTest.deficit(self, 200)
                    future = pool.submit(pay)
                    self.assertTrue(ready.wait(5))
                    deadline = monotonic() + 10
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute('SHOW FULL PROCESSLIST')
                            if any(row[3] == db['NAME'] and row[4] == 'Query'
                                   and 'USERS_ESCORTPROFILE' in str(row[7] or '').upper()
                                   and 'FOR UPDATE' in str(row[7] or '').upper() for row in cursor.fetchall()):
                                break
                        if future.done():
                            self.fail(f'付款未等待押金档案锁: {future.result()}')
                        sleep(0.05)
                    else:
                        self.fail('未观察到付款等待押金档案锁')
                result = future.result(timeout=15)
            self.assertEqual(result['code'], 0, result)
            oid = result['data']['order']['id']
            buyer_wallet.refresh_from_db()
            self.assertEqual(buyer_wallet.balance, 9000)
            self.assertTrue(Transaction.objects.filter(order_id=oid, wallet=buyer_wallet, tx_type='PAY', status='SUCCESS').exists())
