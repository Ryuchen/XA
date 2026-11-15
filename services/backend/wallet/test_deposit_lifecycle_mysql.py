"""Actual deposit APIs serialize with a provider lifecycle closure lock."""
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
from club_accounts.tokens import issue_account_tokens
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from users.models import EscortProfile
from wallet import test_cash_deposit_topup as cash_fixture
from wallet import test_deposit_clearance as refund_fixture
from wallet.models import CashDepositTopup, DepositPaymentSnapshot, Transaction, Wallet


def require_isolated(test):
    run = os.environ.get('XA_FOLLOWUP_MYSQL_RUN_ID', '')
    host = f'xa-followup-mysql-{run}'
    if (os.environ.get('DJANGO_SETTINGS_MODULE') != 'config.ordinary_followup_mysql_test_settings'
            or os.environ.get('XA_FOLLOWUP_MYSQL_ISOLATED') != '1' or not re.fullmatch(r'[0-9a-f]{12}', run)):
        test.fail('Requires the disposable two-store MySQL runner')
    for alias, suffix in (('default', 'a'), ('store_b', 'b')):
        db = connections[alias]
        if (db.vendor != 'mysql' or db.settings_dict['HOST'] != host
                or db.settings_dict['NAME'] != f'test_xa_test_followup_{run}_{suffix}'):
            test.fail('Unexpected MySQL test database')
        with db.cursor() as cursor:
            cursor.execute('SELECT @@hostname')
            test.assertEqual(cursor.fetchone()[0], host)


class LifecycleCompetition:
    def blocked_action(self, operation, *, change_membership=False):
        entered = Event()
        actual = provider_lifecycle.lock_provider_operation

        def observe(account):
            entered.set()
            return actual(account)

        def invoke():
            close_old_connections()
            try:
                return operation()
            finally:
                close_old_connections()

        with patch('club_accounts.provider_lifecycle.lock_provider_operation', side_effect=observe):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with store_context(self.second), transaction.atomic():
                    provider_lifecycle.lock_provider_closure(self.account)
                    future = pool.submit(invoke)
                    self.assertTrue(entered.wait(15), 'deposit API did not reach lifecycle lock')
                    db = connections['store_b']
                    deadline = monotonic() + 8
                    waiting = False
                    while monotonic() < deadline:
                        with db.cursor() as cursor:
                            cursor.execute('SHOW FULL PROCESSLIST')
                            waiting = any(row[3] == db.settings_dict['NAME'] and row[4] == 'Query' and row[7]
                                and 'CLUB_ACCOUNTS_PROVIDERLIFECYCLELOCK' in str(row[7]).upper()
                                and 'LOCK IN SHARE MODE' in str(row[7]).upper() for row in cursor.fetchall())
                        if waiting:
                            break
                        sleep(.05)
                    self.assertTrue(waiting, 'deposit API did not wait on the lifecycle row')
                    self.assertFalse(future.done())
                    # The request must be blocked before it takes either row.
                    EscortProfile.objects.select_for_update(nowait=True).get(pk=self.profile.pk)
                    Wallet.objects.select_for_update(nowait=True).get(pk=self.wallet.pk)
                    if change_membership:
                        ClubAccount.objects.filter(pk=self.account.pk).update(
                            license_membership_active=False, business_cycle=F('business_cycle')+1)
                return future.result(timeout=30)


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class DepositAndTopupLifecycleMySQLTest(LifecycleCompetition, TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True

    def setUp(self):
        require_isolated(self)
        self.first, self.second = resolve_store(), xinan_store()
        with store_context(self.second):
            cash_fixture.CashDepositTopupTest.setUp(self)
            self.client.defaults['HTTP_HOST'] = 'admin.esports.club'
            token, _ = issue_account_tokens(self.account)
        self.owner = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+token)

    def test_new_wallet_deposit_rechecks_member_after_wait(self):
        result = self.blocked_action(lambda:self.owner.post('/api/wallet/deposit/',
            {'amount':1000, 'client_request_id':'life-deposit-pay'}, format='json'), change_membership=True)
        self.assertEqual(result.status_code, 409)
        with store_context(self.second):
            self.profile.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((self.profile.deposit_paid, self.wallet.balance), (20000, 1234))
            self.assertFalse(DepositPaymentSnapshot.objects.filter(account=self.account).exists())
            self.assertFalse(Transaction.objects.filter(wallet=self.wallet).exists())

    def test_cash_topup_continues_after_member_change_without_wallet_credit(self):
        data = {'amount':1000, 'reason':'隔离线下收款核对', 'reference':'life-cash-001',
                'client_request_id':'life-cash-topup'}
        result = self.blocked_action(lambda:self.client.post(self.url, data, format='json'), change_membership=True)
        self.assertEqual(result.data['code'], 0, result.data)
        repeated = self.client.post(self.url, data, format='json')
        self.assertEqual(repeated.status_code, 409)
        with store_context(self.second):
            self.profile.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((self.profile.deposit_paid, self.wallet.balance), (21000, 1234))
            row = CashDepositTopup.objects.get(account=self.account)
            self.assertEqual((row.amount, row.platform_transaction.amount), (1000, 1000))


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class DepositRefundLifecycleMySQLTest(LifecycleCompetition, TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True
    _base = refund_fixture.DepositClearanceRefundApiTest._base

    def setUp(self):
        require_isolated(self)
        self.first, self.second = resolve_store(), xinan_store()
        self.client = APIClient(HTTP_HOST='admin.esports.club')
        with store_context(self.second):
            refund_fixture.DepositClearanceRefundApiTest.setUp(self)
            refund_fixture.DepositClearanceRefundApiTest.prepare_closed_order(self, 7)

    def test_existing_refund_waits_then_posts_after_member_change(self):
        data = {'amount':1000, 'reason':'隔离退还押金', 'client_request_id':'life-deposit-refund'}
        result = self.blocked_action(lambda:self.client.post(self.url, data, format='json'), change_membership=True)
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.second):
            self.profile.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((self.profile.deposit_paid, self.wallet.balance), (2000, 2800))
            self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='DEPOSIT', amount=1000).count(), 1)
