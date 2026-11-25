"""One trusted clock for zero-person settlement and subsidy readiness reads."""
from dataclasses import replace
from datetime import timedelta
import json
import re
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import (
    OrderPlatformSubsidyShare, OrderSegmentPersonSettlement, OrderSegmentSettlementManifest,
)
from orders.tests import test_safebox_segment_settlement_clocks as subsidy_clock_fixture
from orders.tests import test_safebox_settlement_clocks as subsidy_fixture
from orders.tests import test_segment_settlement_credit as segment_fixture
from orders.tests import test_segment_settlement_plan as segment_plan_fixture
from orders.tests.factories import make_completed_order
from wallet import test_refund_deposit_deficit as deficit_fixture
from wallet.models import (
    ProviderReport, RefundDepositRecoveryPermit, SettlementClockEvidence, Transaction,
    get_platform_wallet,
)
from wallet.reporting import report_platform_funding_insufficient
from wallet.services import get_wallet
from wallet.settlement_clocks import ClockUnavailable, trusted_test_clock


@override_settings(
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
    REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
)
class SegmentZeroSettlementClockTests(APITransactionTestCase):
    setUp = segment_plan_fixture.SegmentSettlementPlanTest.setUp
    login = segment_plan_fixture.SegmentSettlementPlanTest.login
    ok = segment_plan_fixture.SegmentSettlementPlanTest.ok
    recharge = segment_plan_fixture.SegmentSettlementPlanTest.recharge
    new_order = segment_plan_fixture.SegmentSettlementPlanTest.new_order
    replacement = segment_plan_fixture.SegmentSettlementPlanTest.replacement
    transfer = segment_plan_fixture.SegmentSettlementPlanTest.transfer
    prepare = segment_plan_fixture.SegmentSettlementPlanTest.prepare
    act = segment_plan_fixture.SegmentSettlementPlanTest.act
    review = segment_plan_fixture.SegmentSettlementPlanTest.review
    finished_order = segment_plan_fixture.SegmentSettlementPlanTest.finished_order
    submit_and_approve = segment_plan_fixture.SegmentSettlementPlanTest.submit_and_approve
    mature = segment_fixture.SegmentSettlementCreditTest.mature
    settle = segment_fixture.SegmentSettlementCreditTest.settle

    def test_zero_close_person_and_finalize_share_one_clock_and_retry_needs_none(self):
        self.service.commission_rate = 100
        self.service.save(update_fields=['commission_rate'])
        order, replacement, old, current = self.finished_order()
        self.account = self.accounts[self.provider.pk]
        self.profile = self.provider.escort_profile
        self.profile.deposit_mode = 'EARNINGS'
        self.profile.deposit_paid = 1000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.platform = get_platform_wallet()
        self.platform.balance = 10000
        self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(
            self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID',
        )
        deficit = deficit_fixture.RefundDepositDeficitTest.deficit(self, 200)
        permit = RefundDepositRecoveryPermit.objects.create(
            profile=self.profile, account=self.account, order=order,
        )
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        observed = self.settlement_time
        self.platform.refresh_from_db(); self.wallet.refresh_from_db()
        before = (self.platform.balance, self.wallet.balance, Transaction.objects.count())

        with patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('zero close clock disconnected'), create=True):
            with self.assertRaises(ClockUnavailable):
                self.settle(order)
        order.refresh_from_db(); permit.refresh_from_db(); deficit.refresh_from_db()
        self.platform.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertIsNone(permit.closed_at)
        self.assertEqual(deficit.remaining, 200)
        self.assertEqual((self.platform.balance, self.wallet.balance, Transaction.objects.count()), before)
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        self.assertFalse(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists())

        self.assertCountEqual(self.settle(order), [self.provider.pk, replacement.pk])
        order.refresh_from_db(); permit.refresh_from_db(); deficit.refresh_from_db()
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        records = list(OrderSegmentPersonSettlement.objects.filter(manifest=manifest))
        self.assertEqual((order.settled_at, manifest.finalized_at, permit.closed_at),
                         (observed, observed, observed))
        self.assertEqual({row.settled_at for row in records}, {observed})
        self.assertEqual({row.amount for row in records}, {0})
        self.assertEqual((permit.close_reason, permit.zero_income_settlement.account_id),
                         ('ZERO_INCOME_SETTLED', self.account.pk))
        self.assertEqual(deficit.remaining, 200)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        receipts = list(SettlementClockEvidence.objects.filter(
            order=order, operation__in=['PLAN', 'PERSON', 'FINALIZE'],
        ))
        self.assertEqual(len(receipts), 4)
        self.assertEqual({row.observed_at for row in receipts}, {observed})
        self.assertEqual(len({json.dumps(row.clock_evidence, sort_keys=True) for row in receipts}), 1)
        counts = (Transaction.objects.count(), len(receipts))
        with patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('completed retry needs no clock')), \
             patch('wallet.settlement_clocks.sample',
                   side_effect=ClockUnavailable('completed retry needs no sample')):
            self.assertEqual(self.settle(order), [])
        self.assertEqual((Transaction.objects.count(), SettlementClockEvidence.objects.filter(
            order=order, operation__in=['PLAN', 'PERSON', 'FINALIZE']).count()), counts)


@override_settings(
    VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False, SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
)
class SubsidyZeroSettlementClockTests(TestCase):
    setUp = subsidy_clock_fixture.SafeBoxSegmentClockEvidenceTest.setUp
    report_versions = subsidy_clock_fixture.SafeBoxSegmentClockEvidenceTest.report_versions
    verified_records = subsidy_clock_fixture.SafeBoxSegmentClockEvidenceTest.verified_records
    settle = subsidy_clock_fixture.SafeBoxSegmentClockEvidenceTest.settle
    assert_no_payout = subsidy_clock_fixture.SafeBoxSegmentClockEvidenceTest.assert_no_payout

    def test_zero_close_failure_rolls_back_prior_subsidy_then_retry_reconciles_amounts(self):
        second_segment = self.plan.segments[1]
        second_person = self.plan.persons[1]
        self.plan.segments = (
            self.plan.segments[0], replace(second_segment, fee=0, income=0),
        )
        self.plan.persons = (
            self.plan.persons[0], replace(second_person, income=0),
        )
        self.shares[1].amount = 0
        self.shares[1].save(update_fields=['amount'])
        permit = RefundDepositRecoveryPermit.objects.create(
            profile=self.users[1].escort_profile, account=self.accounts[1], order=self.order,
        )
        with patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('subsidy zero close disconnected'), create=True):
            with self.assertRaises(ClockUnavailable):
                self.settle(self.now)
        self.assert_no_payout()
        permit.refresh_from_db()
        self.assertIsNone(permit.closed_at)
        self.assertFalse(OrderSegmentPersonSettlement.objects.filter(manifest__order=self.order).exists())
        for share in self.shares:
            share.refresh_from_db()
            share.assignment.refresh_from_db()
            self.assertIsNone(share.paid_at)
            self.assertIsNone(share.income_transaction_id)
            self.assertIsNone(share.expense_transaction_id)
            self.assertIsNone(share.assignment.settled_at)

        self.assertCountEqual(self.settle(self.now), [user.pk for user in self.users])
        self.order.refresh_from_db(); self.platform.refresh_from_db(); permit.refresh_from_db()
        manifest = OrderSegmentSettlementManifest.objects.get(order=self.order)
        people = list(OrderSegmentPersonSettlement.objects.filter(manifest=manifest))
        zero = people[[row.account_id for row in people].index(self.accounts[1].pk)]
        self.assertEqual((self.order.settled_at, manifest.finalized_at, permit.closed_at),
                         (self.now, self.now, self.now))
        self.assertEqual((zero.amount, zero.income_transaction_id, permit.close_reason),
                         (0, None, 'ZERO_INCOME_SETTLED'))
        self.assertEqual(permit.zero_income_settlement_id, zero.pk)
        self.assertEqual(self.platform.balance, 4800)
        self.assertEqual(
            [get_wallet(user=user, account=account).balance
             for user, account in zip(self.users, self.accounts)], [3200, 0],
        )
        expenses = Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE')
        incomes = Transaction.objects.filter(order=self.order, tx_type='INCOME')
        self.assertEqual((expenses.get().amount, incomes.get().amount), (-3200, 3200))
        self.assertEqual(expenses.get().balance_after, expenses.get().balance_before - 3200)
        self.assertEqual(incomes.get().balance_after, incomes.get().balance_before + 3200)
        receipts = list(SettlementClockEvidence.objects.filter(
            order=self.order, operation__in=['PLAN', 'PERSON', 'FINALIZE'],
        ))
        self.assertEqual(len(receipts), 4)
        self.assertEqual({row.observed_at for row in receipts}, {self.now})
        counts = (Transaction.objects.count(), len(receipts))
        with patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('completed subsidy retry needs no clock')), \
             patch('wallet.settlement_clocks.sample',
                   side_effect=ClockUnavailable('completed subsidy retry needs no sample')):
            self.assertEqual(self.settle(self.now + timedelta(seconds=1)), [])
        self.assertEqual((Transaction.objects.count(), SettlementClockEvidence.objects.filter(
            order=self.order, operation__in=['PLAN', 'PERSON', 'FINALIZE']).count()), counts)


@override_settings(
    VIP_SAFE_BOX_SUBSIDY_ENABLED=True, REQUIRE_ORDER_EVIDENCE_IMAGES=False,
    ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
)
class SubsidyReportingClockTests(TestCase):
    setUp = subsidy_fixture.SafeBoxSettlementFixture.setUp

    def test_ready_unmature_and_application_time_cannot_override_trusted_clock_and_reads_never_write(self):
        self.platform.balance = 7999
        self.platform.save(update_fields=['balance'])
        first, second = list(ProviderReport.objects.filter(order=self.order).order_by('submitted_at'))
        early = trusted_test_clock(self.completed + timedelta(hours=71))
        with patch('wallet.reporting.require_clock', return_value=early, create=True), \
             patch('django.utils.timezone.now', return_value=self.completed + timedelta(hours=1000)):
            self.assertFalse(report_platform_funding_insufficient(first, {}))
        mature = trusted_test_clock(self.completed + timedelta(hours=72))
        with patch('wallet.reporting.require_clock', return_value=mature, create=True):
            self.assertTrue(report_platform_funding_insufficient(first, {}))
            self.assertFalse(report_platform_funding_insufficient(second, {}))
        before = (
            self.platform.balance, self.order.settled_at, Transaction.objects.count(),
            list(OrderPlatformSubsidyShare.objects.filter(subsidy=self.subsidy).values()),
        )
        with patch('wallet.reporting.require_clock', return_value=mature, create=True):
            with CaptureQueriesContext(connection) as captured:
                self.assertTrue(report_platform_funding_insufficient(first, {}))
        writes = [row['sql'] for row in captured.captured_queries if re.match(
            r'^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', row['sql'], re.I,
        )]
        self.assertEqual(writes, [])
        self.order.refresh_from_db(); self.platform.refresh_from_db()
        self.assertEqual(
            (self.platform.balance, self.order.settled_at, Transaction.objects.count(),
             list(OrderPlatformSubsidyShare.objects.filter(subsidy=self.subsidy).values())), before,
        )

    def test_clock_or_evidence_read_failure_is_review_not_ready_and_never_writes(self):
        self.platform.balance = 7999
        self.platform.save(update_fields=['balance'])
        report = ProviderReport.objects.filter(order=self.order).order_by('submitted_at').first()
        with patch('wallet.reporting.require_clock',
                   side_effect=ClockUnavailable('reporting clock disconnected'), create=True):
            self.assertIsNone(report_platform_funding_insufficient(report, {}))
        mature = trusted_test_clock(self.completed + timedelta(hours=72))
        with patch('wallet.reporting.require_clock', return_value=mature, create=True), \
             patch('orders.subsidies._share_ready', side_effect=ValueError('subsidy evidence incomplete')):
            self.assertIsNone(report_platform_funding_insufficient(report, {}))
        self.platform.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(self.platform.balance, 7999)
        self.assertIsNone(self.order.settled_at)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())
