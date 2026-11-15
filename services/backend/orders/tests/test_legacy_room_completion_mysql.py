"""The same old-site confirmation cannot finish an order twice under MySQL."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from orders.models import LegacyRoomCompletion, Order
from orders.tests import test_negotiation_termination as fixture
from wallet.models import ProviderReport, Transaction


@skipUnless(connection.vendor == 'mysql', 'requires a disposable MySQL test database')
@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False, ALLOWED_HOSTS=['*'])
class LegacyRoomCompletionMySQLTest(APITransactionTestCase):
    account = fixture.NegotiationTerminationTests.account
    client_for = fixture.NegotiationTerminationTests.client_for

    def setUp(self):
        fixture.NegotiationTerminationTests.setUp(self)
        Order.objects.filter(pk=self.order.pk).update(room_required_snapshot=None)

    def test_parallel_retries_have_one_attestation_and_no_income(self):
        barrier = Barrier(2)
        token = issue_account_tokens(self.staff)[0]
        body = {'customer_account_id': self.boss_account.pk,
                'provider_account_ids': [self.a_account.pk, self.b_account.pk],
                'confirmed_at': timezone.now().isoformat(), 'evidence': '客服核实旧站老板本人确认',
                'client_request_id': 'mysql-legacy-confirm'}
        path = f'/api/admin/orders/{self.order.pk}/legacy-room-completion/'

        def submit():
            close_old_connections()
            try:
                client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+token)
                barrier.wait(timeout=10)
                response = client.post(path, body, format='json')
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual([row[0] for row in results], [200, 200], results)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertEqual(LegacyRoomCompletion.objects.filter(order=self.order).count(), 1)
        self.assertEqual(ProviderReport.objects.filter(order=self.order).count(), 2)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())
