"""Matured order through the real task, role APIs and wallet ledger."""
from datetime import timedelta
from unittest.mock import call, patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation
from engagement.tasks import confirm_due_consumption
from orders.models import Order
from orders.tests import test_release_flow as flow
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class MaturedSettlementRealTest(APITestCase):
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def setUp(self):
        flow.ReleaseFlowTest.setUp(self)
        # This suite exercises settlement, not the cash-deposit entry gate.
        # Its historical default profile is now below the store's cash target.
        profile = self.provider.escort_profile
        profile.deposit_paid = profile.effective_deposit_required
        profile.save(update_fields=['deposit_paid'])

    @staticmethod
    def make_historical(order, report):
        completed = timezone.now() - timedelta(hours=74)
        submitted = completed + timedelta(minutes=10)
        Order.objects.filter(pk=order.pk).update(completed_at=completed, report_ready_at=submitted,
                                                  consumption_ready_at=submitted + timedelta(hours=72))
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=submitted)
        report.refresh_from_db()
        from engagement.test_confirmation_realtime import attest_legacy_report
        attest_legacy_report(order, report, submitted_at=submitted)

    def test_order_matures_once_and_all_roles_see_settlement(self):
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)

        # The order and report were produced by the real APIs above. In this
        # isolated test database, date them as a coherent historical order;
        # the real task and real current clock decide whether it is due.
        self.make_historical(order, report)
        self.assertEqual(confirm_due_consumption(), 1)
        self.assertEqual(confirm_due_consumption(), 0)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertIsNotNone(order.consumption_confirmed_at)
        self.assertEqual(ConsumptionConfirmation.objects.filter(order=order).count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', amount__gt=0).count(), 1)
        self.assertEqual(sum(order.transactions.values_list('amount', flat=True)), 0)

        for user in (self.boss, self.provider):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.accounts[user.pk])[0])
            detail = self.ok(client.get(f'/api/orders/orders/{order.pk}/'))
            self.assertIsNotNone(detail['settled_at'])
            self.assertIsNotNone(detail['consumption_confirmed_at'])
            self.assertEqual(detail['settlement_status'], 'SETTLED')
        self.login(self.admin)
        admin_detail = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))
        self.assertEqual(admin_detail['settlement_status'], 'SETTLED')
        self.assertIsNotNone(admin_detail['consumption_confirmed_at'])
        self.assertTrue(Transaction.objects.filter(order=order, tx_type='INCOME', amount=8000).exists())

    def test_open_aftersale_pauses_due_settlement_until_staff_resolves_it(self):
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        self.login(self.boss)
        opened = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/',
                                           {'reason': '服务问题请核对'}, format='json'))
        self.make_historical(order, report)
        self.assertEqual(confirm_due_consumption(), 0)
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.login(self.admin)
        self.ok(self.client.post(f"/api/admin/aftersales/{opened['id']}/resolve/",
                                 {'resolution': '已与双方沟通确认'}, format='json'))
        self.assertEqual(confirm_due_consumption(), 1)
        self.assertEqual(confirm_due_consumption(), 0)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual((detail['aftersale_status'], detail['settlement_status']), ('RESOLVED', 'SETTLED'))

    def test_scan_skips_finalized_benefits_without_a_vip_confirmation(self):
        from orders.tests.factories import make_order

        stamp = timezone.now() - timedelta(days=4)
        terms = dict(status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
                     settlement_mode=Order.SettlementMode.COMPLETION,
                     customer_account=self.accounts[self.boss.pk], completed_at=stamp, report_ready_at=stamp)
        finalized = make_order(self.boss, settled_at=stamp, funding_benefits_resolved_at=stamp, **terms)
        awaiting_benefits = make_order(self.boss, settled_at=stamp, **terms)
        awaiting_income = make_order(self.boss, funding_benefits_resolved_at=stamp, **terms)
        Order.objects.filter(pk__in=[finalized.pk, awaiting_benefits.pk, awaiting_income.pk]).update(service_clock_version=None)

        with patch('engagement.confirmation.process_order', return_value=False) as processor:
            confirm_due_consumption()

        self.assertEqual(processor.call_args_list, [call(awaiting_benefits.pk), call(awaiting_income.pk)])

    def test_service_settlement_waits_for_both_overlapping_clocks(self):
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))

        # The service finished over 72 hours ago, but the valid report was
        # submitted recently. Income and consumption both still wait for the
        # report freeze to end, rather than starting a second freeze later.
        Order.objects.filter(pk=order.pk).update(completed_at=timezone.now() - timedelta(hours=74))
        self.assertEqual(confirm_due_consumption(), 0)
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertNotEqual(detail['settlement_status'], 'SETTLED')
        self.assertIsNone(detail['consumption_confirmed_at'])
        self.assertIsNotNone(detail['consumption_ready_at'])
        self.login(self.provider)
        provider_detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertNotEqual(provider_detail['settlement_status'], 'SETTLED')
        self.assertIsNone(provider_detail['consumption_confirmed_at'])
        self.login(self.admin)
        detail = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))
        self.assertNotEqual(detail['settlement_status'], 'SETTLED')
        self.assertIsNone(detail['consumption_confirmed_at'])

        # Later both clocks are due; the scan settles and confirms once.
        historical = timezone.now() - timedelta(hours=73)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=historical)
        report.refresh_from_db()
        from engagement.test_confirmation_realtime import attest_legacy_report
        attest_legacy_report(order, report, submitted_at=historical)
        self.assertEqual(confirm_due_consumption(), 1)
        self.assertEqual(confirm_due_consumption(), 0)
        order.refresh_from_db()
        self.assertIsNotNone(order.consumption_confirmed_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', amount=8000).count(), 1)
        self.assertEqual(ConsumptionConfirmation.objects.filter(order=order).count(), 1)
        self.login(self.boss)
        self.assertIsNotNone(self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['consumption_confirmed_at'])
        self.login(self.admin)
        self.assertIsNotNone(self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))['consumption_confirmed_at'])
