"""旧普通服务结算与实销必须共用可信店库时钟。"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings

from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation, PointLedger
from engagement.services import completed_consumption
from engagement import test_confirmation_realtime as confirmation_fixture
from orders.models import Order
from orders.settlement import SettlementError
from wallet.models import ProviderReport, Transaction, get_platform_wallet
from orders.service_settlement import legacy_report_next_subject_id
from wallet.settlement_clocks import ClockUnavailable, record, trusted_test_clock


@override_settings(
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=False,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
)
class LegacyServiceTrustedClockTest(TestCase):
    setUp = confirmation_fixture.ConfirmationRealtimeTest.setUp
    order = confirmation_fixture.ConfirmationRealtimeTest.order

    def modern(self, order):
        Order.objects.filter(pk=order.pk).update(
            service_clock_version=1, service_aftersale_hours=72,
            service_freeze_hours=72, service_deposit_percent=0,
        )
        order.refresh_from_db()
        return order

    def attest_report(self, order):
        report = order.provider_reports.get()
        report.commission_rate = order.commission_rate
        report.audited_at = report.submitted_at
        report.auditor = self.provider
        report.save(update_fields=['commission_rate', 'audited_at', 'auditor'])
        clock = trusted_test_clock(report.submitted_at)
        subject_id = legacy_report_next_subject_id(order, report.pk)
        record(order, 'SUBMISSION', subject_id, 'SUBMIT', clock)
        record(order, 'SUBMISSION', subject_id, 'REVIEW', clock)

    def process_at(self, order, at):
        with patch('django.utils.timezone.now', return_value=at):
            return process_order(order.pk)

    def assert_no_money_or_confirmation(self, order):
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertEqual(self.wallet.balance, 0)
        self.assertFalse(Transaction.objects.filter(
            order=order, tx_type__in=['INCOME', 'SHOP_INCOME'],
        ).exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())

    def test_store_clock_outage_cannot_settle_or_confirm_with_application_time(self):
        order = self.modern(self.order())
        self.attest_report(order)
        with override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            with self.assertRaises(ClockUnavailable):
                process_order(order.pk)
        self.assert_no_money_or_confirmation(order)

    def test_aftersale_and_report_boundaries_then_exact_money_membership_and_retry(self):
        order = self.modern(self.order(report_age=1))
        submitted = self.now + timedelta(hours=10)
        aftersale_deadline = self.now + timedelta(hours=72)
        report_deadline = submitted + timedelta(hours=72)
        Order.objects.filter(pk=order.pk).update(
            completed_at=self.now, report_ready_at=submitted, consumption_ready_at=report_deadline,
        )
        ProviderReport.objects.filter(order=order).update(submitted_at=submitted)
        order.refresh_from_db()
        self.attest_report(order)

        self.assertFalse(self.process_at(order, aftersale_deadline - timedelta(microseconds=1)))
        self.assert_no_money_or_confirmation(order)
        # 售后等待已结束，但报单冻结尚未成熟，仍不得结算或实销。
        self.assertFalse(self.process_at(order, aftersale_deadline))
        self.assert_no_money_or_confirmation(order)

        platform = get_platform_wallet()
        platform.refresh_from_db()
        platform_before = platform.balance
        self.assertTrue(self.process_at(order, report_deadline))
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        platform.refresh_from_db()
        self.assertEqual(self.wallet.balance, 800)
        self.assertEqual(platform.balance, platform_before + 200)
        self.assertEqual(
            (order.settled_at, order.consumption_confirmed_at, order.funding_benefits_resolved_at),
            (report_deadline, report_deadline, report_deadline),
        )
        income = Transaction.objects.get(order=order, tx_type='INCOME')
        shop = Transaction.objects.get(order=order, tx_type='SHOP_INCOME')
        self.assertEqual((income.amount, income.balance_before, income.balance_after), (800, 0, 800))
        self.assertEqual((shop.amount, shop.balance_before, shop.balance_after),
                         (200, platform_before, platform_before + 200))
        self.assertEqual(sum(order.transactions.values_list('amount', flat=True)), 0)
        confirmation = ConsumptionConfirmation.objects.get(order=order)
        self.assertEqual((confirmation.account_id, confirmation.business_cycle, confirmation.amount,
                          confirmation.refunded_amount, confirmation.confirmed_at),
                         (self.boss_account.pk, order.business_cycle, 1000, 0, report_deadline))
        self.assertEqual(completed_consumption(self.boss_account), 1000)
        points_before = list(PointLedger.objects.filter(order=order).order_by('pk').values_list(
            'pk', 'kind', 'amount', 'balance_after',
        ))

        # 后续可信时间只允许读取原结果，不增加收益、平台收入、积分或实销。
        self.assertFalse(self.process_at(order, report_deadline + timedelta(seconds=1)))
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        platform.refresh_from_db()
        self.assertEqual((self.wallet.balance, platform.balance), (800, platform_before + 200))
        self.assertEqual(order.settled_at, report_deadline)
        self.assertEqual(order.consumption_confirmed_at, report_deadline)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SHOP_INCOME').count(), 1)
        self.assertEqual(ConsumptionConfirmation.objects.filter(order=order).count(), 1)
        self.assertEqual(list(PointLedger.objects.filter(order=order).order_by('pk').values_list(
            'pk', 'kind', 'amount', 'balance_after',
        )), points_before)
        self.assertEqual(completed_consumption(self.boss_account), 1000)

    def test_tampered_report_evidence_never_settles_or_confirms(self):
        order = self.modern(self.order())
        self.attest_report(order)
        ProviderReport.objects.filter(order=order).update(completion_image='')
        with self.assertRaisesMessage(SettlementError, '凭证'):
            process_order(order.pk)
        self.assert_no_money_or_confirmation(order)

    def test_tampered_existing_confirmation_is_not_backfilled_as_success(self):
        order = self.modern(self.order())
        self.attest_report(order)
        original_time = self.now - timedelta(hours=1)
        Order.objects.filter(pk=order.pk).update(settled_at=self.now)
        confirmation = ConsumptionConfirmation.objects.create(
            order=order, account=self.boss_account, business_cycle=order.business_cycle,
            amount=1001, confirmed_at=original_time,
        )

        with self.assertRaisesMessage(SettlementError, '原实销'):
            process_order(order.pk)
        order.refresh_from_db()
        confirmation.refresh_from_db()
        self.assertEqual(order.settled_at, self.now)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertEqual((confirmation.amount, confirmation.confirmed_at), (1001, original_time))
        self.assertEqual(ConsumptionConfirmation.objects.filter(order=order).count(), 1)
        self.assertFalse(Transaction.objects.filter(
            order=order, tx_type__in=['INCOME', 'SHOP_INCOME'],
        ).exists())
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
