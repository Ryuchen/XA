"""Real MySQL races for store configuration and legacy clock cutover."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from orders.models import LegacyClockCutover, Order
from orders.tests import test_legacy_clock_cutover as fixture
from wallet.models import Transaction


@skipUnless(connection.vendor == 'mysql', 'requires a disposable MySQL test database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False, ALLOWED_HOSTS=['*'])
class LegacyClockCutoverMySQLTest(APITransactionTestCase):
    account = fixture.LegacyClockCutoverTests.account
    client_for = fixture.LegacyClockCutoverTests.client_for
    setUp = fixture.LegacyClockCutoverTests.setUp
    preview = fixture.LegacyClockCutoverTests.preview

    def _client(self, token):
        return APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+token)

    def test_parallel_retries_do_not_duplicate_cutover(self):
        preview = self.preview()
        body = {'plan_fingerprint':preview['plan_fingerprint'], 'client_request_id':'mysql-clock-retry'}
        token, barrier = issue_account_tokens(self.cutter)[0], Barrier(2)
        def submit():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = self._client(token).post(self.url, body, format='json')
                return response.status_code, response.data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual([row[0] for row in results], [200, 200], results)
        self.assertEqual(LegacyClockCutover.objects.filter(order=self.order).count(), 1)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())

    def test_config_change_racing_cutover_never_uses_unreviewed_rules(self):
        from console.settlement_clocks import save_clocks
        preview = self.preview()
        body = {'plan_fingerprint':preview['plan_fingerprint'], 'client_request_id':'mysql-clock-race'}
        token, barrier = issue_account_tokens(self.cutter)[0], Barrier(2)
        def cutover():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = self._client(token).post(self.url, body, format='json')
                return response.status_code
            finally:
                close_old_connections()
        def change_config():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return save_clocks({'aftersale_hours':36, 'income_freeze_hours':36, 'expected_version':7})
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            cutover_future, config_future = pool.submit(cutover), pool.submit(change_config)
            status, new_rules = cutover_future.result(), config_future.result()
        self.assertEqual(new_rules['version'], 8)
        self.order.refresh_from_db()
        if status == 200:
            self.assertEqual((self.order.service_aftersale_hours, self.order.service_clock_version), (48, 7))
            self.assertEqual(LegacyClockCutover.objects.filter(order=self.order).count(), 1)
        else:
            self.assertEqual(status, 409)
            self.assertIsNone(self.order.service_clock_version)
            self.assertFalse(LegacyClockCutover.objects.filter(order=self.order).exists())
