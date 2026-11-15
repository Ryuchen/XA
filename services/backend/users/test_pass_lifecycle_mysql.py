"""Real pass APIs serialize with a provider lifecycle closure lock."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import re
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.db.models import F
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts import provider_lifecycle
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointLedger
from engagement.services import config
from orders.tests.factories import make_provider
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from users import test_pass_points_payment as fixture
from users.models import EscortProfile, ProviderPassPurchase, ProviderPassRefund
from users.pass_rules import lock_pass_config
from wallet.models import SystemConfig, Transaction, Wallet
from wallet.services import get_wallet


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True,
    POINTS_USAGE_SWITCHES_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PassLifecycleMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True
    quote = fixture.PassPointsPaymentTests.quote
    buy = fixture.PassPointsPaymentTests.buy

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
        self.first, self.second = resolve_store(), xinan_store()
        self.client = APIClient(HTTP_HOST='xa.esports.club')
        with store_context(self.second):
            fixture.PassPointsPaymentTests.setUp(self)
            with transaction.atomic():
                lock_pass_config()

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
                    self.assertTrue(entered.wait(15), 'pass API did not reach lifecycle lock')
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
                    self.assertTrue(waiting, 'pass API did not wait on the lifecycle row')
                    self.assertFalse(pending.done())
                    SystemConfig.objects.select_for_update(nowait=True).get(key='provider_pass_config_lock')
                    EscortProfile.objects.select_for_update(nowait=True).get(pk=self.profile.pk)
                    Wallet.objects.select_for_update(nowait=True).get(pk=self.wallet.pk)
                    if other_operation:
                        other_operation()
                    if change_membership:
                        ClubAccount.objects.filter(pk=self.account.pk).update(
                            license_membership_active=False, business_cycle=F('business_cycle') + 1)
                return pending.result(timeout=30)

    def test_points_purchase_rechecks_member_and_other_store_continues(self):
        quote = self.quote()
        with store_context(self.first):
            other_user = make_provider(deposit_mode='EARNINGS')
            other = get_or_create_account_for_legacy_user(other_user)
            self.assertEqual(other.pk, self.account.pk)
            profile = other_user.escort_profile; profile.account = other; profile.save(update_fields=['account'])
            wallet = get_wallet(account=other, user=other_user); wallet.balance = 1000; wallet.save(update_fields=['balance'])
            points = MemberProfile.objects.create(account=other, points_balance=30000)
            config()
            save_points_terms(group_point_hundredths=10000, group_coin_units=10,
                              max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
            token, _ = issue_account_tokens(other)
        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+token)
        quoted = client.post('/api/users/provider-pass/points-quote/', {'tier':'BRONZE',
            'selected_point_hundredths':20000}, format='json')
        self.assertEqual(quoted.data['code'], 0, quoted.data)

        def buy_other():
            q = quoted.data['data']
            result = client.post('/api/users/provider-pass/', {'tier':'BRONZE', 'days':1,
                'expected_daily_price':q['daily_price'], 'points_quote_token':q['quote_token'],
                'client_request_id':'life-other-pass'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)

        result = self.blocked_action(lambda:self.buy(quote, 'life-points-pass'),
                                     change_membership=True, other_operation=buy_other)
        self.assertEqual(result.status_code, 409)
        with store_context(self.second):
            self.wallet.refresh_from_db(); self.points.refresh_from_db(); self.profile.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.points.points_balance, self.profile.pass_tier), (1000, 30000, ''))
            self.assertFalse(ProviderPassPurchase.objects.filter(account=self.account).exists())
        with store_context(self.first):
            wallet.refresh_from_db(); points.refresh_from_db()
            self.assertEqual((wallet.balance, points.points_balance), (820, 10000))
            self.assertEqual(ProviderPassPurchase.objects.filter(account=other).count(), 1)

    def test_cash_purchase_waits_before_config_and_replays_once(self):
        data = {'tier':'BRONZE', 'days':1, 'client_request_id':'life-pass-cash'}
        result = self.blocked_action(lambda:self.client.post(self.url, data, format='json'))
        self.assertEqual(result.data['code'], 0, result.data)
        replay = self.client.post(self.url, data, format='json')
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        with store_context(self.second):
            self.wallet.refresh_from_db(); self.profile.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.profile.pass_tier), (800, 'BRONZE'))
            self.assertEqual(ProviderPassPurchase.objects.filter(account=self.account).count(), 1)
            self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='PASS_PURCHASE', amount__lt=0).count(), 1)

    def test_inflight_pending_pass_refund_restores_coin_and_points_after_member_change(self):
        quote = self.quote()
        self.assertEqual(self.buy(quote, 'life-pass-pre-refund').data['code'], 0)
        with store_context(self.second):
            purchase = ProviderPassPurchase.objects.get(account=self.account)
            # Synthetic future-dated purchase to exercise the existing refund
            # handler; first-release purchases activate immediately.
            start = timezone.now() + timedelta(hours=2)
            purchase.starts_at, purchase.expires_at = start, start + timedelta(days=1)
            purchase.save(update_fields=['starts_at', 'expires_at'])
            self.profile.pass_tier, self.profile.pass_expires_at = purchase.tier, purchase.expires_at
            self.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        data = {'client_request_id':'life-pass-refund'}
        result = self.blocked_action(lambda:self.client.post(f'{self.url}{purchase.pk}/refund/', data, format='json'),
                                     change_membership=True)
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.second):
            self.wallet.refresh_from_db(); self.points.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.points.points_balance), (1000, 30000))
            refund = ProviderPassRefund.objects.get(purchase_id=purchase.pk)
            self.assertEqual((refund.amount, refund.points_returned), (180, 20000))
            self.assertEqual(PointLedger.objects.filter(account=self.account, kind='PASS_POINTS_RETURN').count(), 1)
