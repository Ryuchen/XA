"""Real gift API and lifecycle-lock races on two disposable MySQL stores."""
from concurrent.futures import ThreadPoolExecutor
import os
import re
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.db.models import F
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts import provider_lifecycle
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot
from orders import gift_lifecycle
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import IdempotencyKey, Transaction, Wallet
from wallet.services import get_wallet


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
    ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class GiftLifecycleMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True

    def setUp(self):
        run = os.environ.get('XA_FOLLOWUP_MYSQL_RUN_ID', '')
        host = f'xa-followup-mysql-{run}'
        if (os.environ.get('DJANGO_SETTINGS_MODULE') != 'config.ordinary_followup_mysql_test_settings'
                or os.environ.get('XA_FOLLOWUP_MYSQL_ISOLATED') != '1' or not re.fullmatch(r'[0-9a-f]{12}', run)):
            self.fail('Requires the disposable two-store MySQL runner')
        for alias, suffix in (('default', 'a'), ('store_b', 'b')):
            db = connections[alias]
            if (db.vendor != 'mysql' or db.settings_dict['HOST'] != host
                    or db.settings_dict['NAME'] != f'test_xa_test_followup_{run}_{suffix}'):
                self.fail('Unexpected MySQL test database')
            with db.cursor() as cursor:
                cursor.execute('SELECT @@hostname')
                self.assertEqual(cursor.fetchone()[0], host)
        self.a, self.b = resolve_store(), xinan_store()
        self.one = self._store(self.a, 'sl.esports.club')
        self.two = self._store(self.b, 'xa.esports.club')
        self.assertEqual(self.one['provider'].pk, self.two['provider'].pk)

    def _store(self, store, host):
        with store_context(store):
            boss = make_user()
            provider = make_provider(display_name='隔离收礼陪玩')
            account = get_or_create_account_for_legacy_user(boss)
            target = get_or_create_account_for_legacy_user(provider)
            profile = provider.escort_profile
            profile.account = target
            profile.save(update_fields=['account'])
            wallet = make_wallet(boss, balance=100000)
            wallet.account = account
            wallet.save(update_fields=['account'])
            provider_wallet = make_wallet(provider, balance=0)
            provider_wallet.account = target
            provider_wallet.save(update_fields=['account'])
            PointFundingLot.objects.create(account=account, source_key='gift-lifecycle-principal',
                                            kind='PREPAID', remaining=100000)
            category = ServiceCategory.objects.create(name='隔离礼物', is_gift=True)
            first = make_service(name='礼物甲', price=2100, service_category=category)
            second = make_service(name='礼物乙', price=5200, service_category=category, gift_naming_days=1)
            token, _ = issue_account_tokens(account)
            return {'boss':account, 'provider':target, 'wallet':wallet, 'first':first, 'second':second,
                    'client':APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+token)}

    @staticmethod
    def single(info, key):
        return {'gift_service_id':info['first'].pk, 'provider_id':info['provider'].pk,
                'quantity':1, 'expected_amount':2100, 'client_request_id':key}

    @staticmethod
    def basket(info, key, *, title='隔离测试冠名'):
        return {'items':[{'gift_service_id':info['first'].pk, 'quantity':1},
                         {'gift_service_id':info['second'].pk, 'quantity':1}],
                'provider_id':info['provider'].pk, 'naming_title':title,
                'expected_amount':7300, 'client_request_id':key}

    def blocked_gift(self, account, operation, *, change_membership=False, other_operation=None, check_wallet=None):
        entered = Event()
        actual = gift_lifecycle.lock_provider_operations

        def observe(accounts):
            if accounts and accounts[0]._state.db == self.a.database_alias:
                entered.set()
            return actual(accounts)

        def invoke():
            close_old_connections()
            try:
                return operation()
            finally:
                close_old_connections()

        with patch('orders.gift_lifecycle.lock_provider_operations', side_effect=observe):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with store_context(self.a), transaction.atomic():
                    provider_lifecycle.lock_provider_closure(account)
                    pending = pool.submit(invoke)
                    self.assertTrue(entered.wait(15), 'gift API did not reach lifecycle lock')
                    # Inspect only this guarded, dedicated test server and
                    # prove the waiter is at the barrier, not some later row.
                    deadline = monotonic() + 8
                    waiting = False
                    while monotonic() < deadline:
                        with connections['default'].cursor() as cursor:
                            cursor.execute('SHOW FULL PROCESSLIST')
                            waiting = any(row[3] == connections['default'].settings_dict['NAME']
                                and row[4] == 'Query' and row[7]
                                and 'CLUB_ACCOUNTS_PROVIDERLIFECYCLELOCK' in str(row[7]).upper()
                                and 'LOCK IN SHARE MODE' in str(row[7]).upper() for row in cursor.fetchall())
                        if waiting:
                            break
                        sleep(.05)
                    self.assertTrue(waiting, 'gift request was not waiting on the lifecycle row')
                    self.assertFalse(pending.done())
                    if check_wallet:
                        Wallet.objects.select_for_update(nowait=True).get(pk=check_wallet.pk)
                    if other_operation:
                        other_operation()
                    if change_membership:
                        ClubAccount.objects.filter(pk=account.pk).update(
                            license_membership_active=False, business_cycle=F('business_cycle')+1)
                return pending.result(timeout=30)

    def test_single_gift_rechecks_after_closure_other_store_can_pay(self):
        def other_gift():
            response = self.two['client'].post('/api/orders/gifts/send/', self.single(self.two, 'other-gift-1'), format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        result = self.blocked_gift(self.one['provider'], lambda:self.one['client'].post(
            '/api/orders/gifts/send/', self.single(self.one, 'single-close-1'), format='json'),
            change_membership=True, other_operation=other_gift, check_wallet=self.one['wallet'])
        self.assertEqual(result.status_code, 409)
        with store_context(self.a):
            self.one['wallet'].refresh_from_db()
            self.assertEqual(self.one['wallet'].balance, 100000)
            self.assertFalse(Order.objects.filter(customer_account=self.one['boss']).exists())
        with store_context(self.b):
            self.two['wallet'].refresh_from_db()
            self.assertEqual(self.two['wallet'].balance, 97900)
            self.assertEqual(Order.objects.filter(customer_account=self.two['boss']).count(), 1)

    def test_basket_locks_before_wallet_and_posts_both_gifts_once(self):
        data = self.basket(self.one, 'basket-lock-1')
        result = self.blocked_gift(self.one['provider'], lambda:self.one['client'].post(
            '/api/orders/gifts/send/', data, format='json'), check_wallet=self.one['wallet'])
        self.assertEqual(result.data['code'], 0, result.data)
        # A completed payment's receipt remains recoverable after a simulated
        # membership change; this is not an implementation of cancellation.
        with store_context(self.a), transaction.atomic():
            provider_lifecycle.lock_provider_closure(self.one['provider'])
            ClubAccount.objects.filter(pk=self.one['provider'].pk).update(
                license_membership_active=False, business_cycle=F('business_cycle')+1)
        replay = self.one['client'].post('/api/orders/gifts/send/', data, format='json')
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        with store_context(self.a):
            self.one['wallet'].refresh_from_db()
            self.assertEqual(self.one['wallet'].balance, 92700)
            self.assertEqual(Order.objects.filter(customer_account=self.one['boss']).count(), 2)

    def test_basket_late_failure_rolls_back_both_lines(self):
        result = self.one['client'].post('/api/orders/gifts/send/', self.basket(self.one, 'basket-failure-1', title=''), format='json')
        self.assertEqual(result.data['code'], 400, result.data)
        with store_context(self.a):
            self.one['wallet'].refresh_from_db()
            target_wallet = get_wallet(account=self.one['provider'])
            self.assertEqual((self.one['wallet'].balance, target_wallet.balance, target_wallet.frozen_amount), (100000, 0, 0))
            self.assertFalse(Order.objects.filter(customer_account=self.one['boss']).exists())
            self.assertFalse(Transaction.objects.filter(wallet=self.one['wallet']).exists())
            self.assertFalse(IdempotencyKey.objects.filter(account=self.one['boss']).exists())

    def test_provider_inviter_is_also_serialized_before_new_referral_credit(self):
        with store_context(self.a):
            user = make_provider(display_name='隔离推荐陪玩')
            inviter = get_or_create_account_for_legacy_user(user)
            profile = user.escort_profile
            profile.account = inviter
            profile.save(update_fields=['account'])
            ClubAccount.objects.filter(pk=self.one['boss'].pk).update(inviter=inviter, inviter_commission_rate=10)
        result = self.blocked_gift(inviter, lambda:self.one['client'].post(
            '/api/orders/gifts/send/', self.single(self.one, 'inviter-lock-1'), format='json'),
            check_wallet=self.one['wallet'])
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.a):
            wallet = get_wallet(account=inviter)
            self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='INCOME', amount=42).count(), 1)
