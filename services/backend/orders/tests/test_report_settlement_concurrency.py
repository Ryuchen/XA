from datetime import timedelta
from django.utils import timezone
"""Use real row locks to arbitrate final report approval against duplicate approval/refund."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import tempfile
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, ProviderReportImage, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
@skipUnlessDBFeature('has_select_for_update')
class ReportSettlementConcurrencyTest(TransactionTestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.enterContext(patch('console.views.notify_order_update'))
        self.boss, self.provider = make_user(), make_provider()
        admin = make_console_user(['order:refund', 'report:audit'])
        accounts = {user.pk: get_or_create_account_for_legacy_user(user) for user in (self.boss, self.provider, admin)}
        membership = admin.admin_membership
        membership.account = accounts[admin.pk]
        membership.save(update_fields=['account'])
        profile = self.provider.escort_profile
        profile.account = accounts[self.provider.pk]
        profile.save(update_fields=['account'])
        self.token, _ = issue_account_tokens(accounts[admin.pk])
        make_wallet(self.boss, balance=90000)
        self.order = make_order(
            self.boss, service=make_service(price=10000), provider=self.provider,
            customer_account=accounts[self.boss.pk], provider_account=accounts[self.provider.pk],
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID, completed_at=timezone.now(),
            amount=10000, commission_rate=20, provider_income=8000, shop_income=2000,
        )
        OrderProvider.objects.create(
            order=self.order, provider=self.provider, provider_account=accounts[self.provider.pk],
            settlement_base=10000, commission_rate=20, provider_income=8000,
        )
        self.report = ProviderReport.objects.create(
            order=self.order, provider=self.provider, provider_account=accounts[self.provider.pk],
            game_name='并发结算验收', amount=10000, status=ProviderReport.Status.PENDING, submitted_at=timezone.now(),
            entry_image=screenshot(), completion_image=screenshot(),
        )
        ProviderReportImage.objects.create(report=self.report, image=screenshot())

    def race(self, requests, *, both_success_allowed=False):
        barrier = Barrier(2)

        def send(item):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.token)
                barrier.wait(timeout=5)
                return client.post(item[0], item[1], format='json').data
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(send, requests, timeout=20))
        codes = sorted(row['code'] for row in results)
        self.assertIn(codes, ([0,0],[0,400]) if both_success_allowed else ([0,400],), results)
        self.order.refresh_from_db()

    def test_simultaneous_approvals_only_credit_income_once(self):
        request = (f'/api/admin/reports/{self.report.pk}/approve/', {})
        self.race([request, request])
        self.assertIsNone(self.order.settled_at)
        from engagement.confirmation import process_order
        with patch('django.utils.timezone.now',return_value=timezone.now()+timedelta(hours=73)):
            process_order(self.order.pk)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME').count(), 1)

    def test_refund_and_final_approval_cannot_both_move_money(self):
        self.race([
            (f'/api/admin/reports/{self.report.pk}/approve/', {}),
            (f'/api/admin/orders/{self.order.pk}/refund/', {'reason': '并发售后验收'}),
        ], both_success_allowed=True)
        if self.order.settled_at:
            self.assertEqual(self.order.status, Order.Status.COMPLETED)
            self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
            self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)
            self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='REFUND').exists())
        else:
            self.assertEqual(self.order.payment_status, Order.PaymentStatus.REFUNDED)
            self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
            self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
            self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())
