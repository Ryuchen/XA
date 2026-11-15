"""Isolated real-API contention between provider closure and withdrawals."""
from concurrent.futures import ThreadPoolExecutor
import os
import re
from threading import Event
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.db.models import F
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts import provider_lifecycle
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.context import store_context
from stores.db import transaction
from stores import test_platform_withdraw_workflow as fixture
from wallet.models import Transaction, Wallet, WithdrawRequest


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, PLATFORM_IDENTITY_ENABLED=True,
    ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class WithdrawLifecycleMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True
    apply = fixture.PlatformWithdrawalWorkflowTest.apply
    initial = fixture.PlatformWithdrawalWorkflowTest.initial
    action = fixture.PlatformWithdrawalWorkflowTest.action

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
        fixture.PlatformWithdrawalWorkflowTest.setUp(self)

    def blocked_action(self, operation, *, change_membership=False, other_operation=None):
        entered = Event()
        actual = provider_lifecycle.lock_provider_operation

        def observe(account):
            if account._state.db == self.second.database_alias:
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
                    pending = pool.submit(invoke)
                    self.assertTrue(entered.wait(15), 'API did not reach the lifecycle barrier')
                    self.assertFalse(pending.done(), 'API should wait for the exclusive lifecycle lock')
                    if other_operation:
                        other_operation()
                    if change_membership:
                        ClubAccount.objects.filter(pk=self.account.pk).update(
                            license_membership_active=False, business_cycle=F('business_cycle') + 1)
                return pending.result(timeout=30)

    def test_submit_waits_and_checks_account_again_other_store_continues(self):
        with store_context(self.first):
            other = ClubAccount.objects.create_account(username='lifecycle-other-store', account_type='PROVIDER')
            self.assertEqual(other.pk, self.account.pk)
            wallet = Wallet.objects.create(account=other, balance=100000)
            token, _ = issue_account_tokens(other)
        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        payload = {'amount':20000, 'payee_method':'WECHAT', 'payee_name':'隔离测试',
                   'payee_account':'local-test', 'client_request_id':'lifecycle-apply'}

        def submit_other():
            result = client.post('/api/wallet/withdraw/', {**payload, 'client_request_id':'other-store-apply'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)

        result = self.blocked_action(lambda: self.p.post('/api/wallet/withdraw/', payload, format='json'),
                                     change_membership=True, other_operation=submit_other)
        self.assertEqual(result.status_code, 409)
        with store_context(self.second):
            self.wallet.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (100000, 0))
            self.assertFalse(WithdrawRequest.objects.filter(account=self.account).exists())
        with store_context(self.first):
            wallet.refresh_from_db()
            self.assertEqual((wallet.balance, wallet.frozen_amount), (80000, 20000))

    def test_owner_cancel_waits_then_returns_frozen_amount_once(self):
        pk = self.apply()
        data = {'client_request_id':'life-owner-cancel'}
        result = self.blocked_action(lambda: self.p.post(f'/api/wallet/withdraw/{pk}/cancel/', data, format='json'),
                                     change_membership=True)
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk)
            self.wallet.refresh_from_db()
            self.assertEqual((row.status, self.wallet.balance, self.wallet.frozen_amount), ('CANCELLED', 100000, 0))
            self.assertEqual(row.transaction.status, 'FAILED')
            self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='WITHDRAW').count(), 1)

    def test_staff_reject_continues_even_if_membership_changed(self):
        pk = self.apply()
        claim = self.s.post(f'/api/admin/withdrawals/{pk}/claim/').data
        self.assertEqual(claim['code'], 0, claim)
        data = {'audit_remark':'隔离测试驳回', 'expected_claimed_at':claim['data']['claimed_at']}
        result = self.blocked_action(lambda: self.s.post(f'/api/admin/withdrawals/{pk}/reject/', data, format='json'),
                                     change_membership=True)
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk)
            self.wallet.refresh_from_db()
            self.assertEqual((row.status, self.wallet.balance, self.wallet.frozen_amount), ('REJECTED', 100000, 0))
            self.assertEqual(row.transaction.status, 'FAILED')
            self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='WITHDRAW').count(), 1)

    def test_platform_payment_continues_even_if_membership_changed(self):
        pk = self.apply(); self.initial(pk)
        self.assertEqual(self.action(pk, 'approve', client_request_id='life-platform-approve').data['code'], 0)
        data = {'client_request_id':'life-platform-paid', 'payout_reference':'LIFECYCLE-TEST',
                'paid_at':timezone.now().isoformat()}
        result = self.blocked_action(lambda: self.action(pk, 'paid', **data), change_membership=True)
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertTrue(self.action(pk, 'paid', **data).data['data']['replayed'])
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk)
            self.wallet.refresh_from_db()
            self.assertEqual((row.status, self.wallet.balance, self.wallet.frozen_amount), ('PAID', 80000, 0))
            self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='WITHDRAW', status='SUCCESS').count(), 1)
