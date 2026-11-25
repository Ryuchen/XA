"""非逐段安全箱直接份额必须使用可信店库时钟及不可变回执。"""
from datetime import timedelta
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from orders.settlement import SettlementError
from orders.subsidies import settle_platform_subsidy_share
from orders.tests.test_safebox_settlement_clocks import SafeBoxSettlementFixture
from wallet.models import ProviderReport, SettlementClockEvidence, Transaction
from wallet.services import get_wallet
from wallet.settlement_clocks import ClockConflict, ClockUnavailable


@override_settings(
    VIP_SAFE_BOX_SUBSIDY_ENABLED=True,
    REQUIRE_ORDER_EVIDENCE_IMAGES=False,
    ORIGINAL_ATTRIBUTION_ENABLED=False,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
)
class DirectSubsidyClockEvidenceTest(SafeBoxSettlementFixture, TestCase):
    def align_reports(self):
        ProviderReport.objects.filter(order=self.order).update(
            submitted_at=self.completed, audited_at=self.completed,
        )

    def assert_no_payout(self):
        self.platform.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.platform.balance, 8000)
        self.assertEqual([get_wallet(account=account).balance for account in self.accounts], [0, 0])
        self.assertFalse(Transaction.objects.filter(
            order=self.order, tx_type__in=['INCOME', 'SUBSIDY_EXPENSE'],
        ).exists())
        self.assertFalse(SettlementClockEvidence.objects.filter(order=self.order).exists())
        self.assertIsNone(self.order.settled_at)
        for share in self.shares:
            share.refresh_from_db()
            self.assertIsNone(share.paid_at)

    def test_disconnected_store_clock_cannot_fall_back_to_application_time(self):
        deadline = self.completed + timedelta(hours=82)
        with override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            with self.assertRaises(ClockUnavailable):
                self.settle(deadline)
        self.assert_no_payout()

    def test_exact_deadline_posts_3200_and_4800_with_plan_person_finalize_receipts(self):
        self.align_reports()
        deadline = self.completed + timedelta(hours=72)
        self.assertEqual(self.settle(deadline - timedelta(microseconds=1)), [])
        self.assert_no_payout()

        # Raw IDs used by segment-style receipts must not collide with the
        # namespaced direct subsidy/share subjects on another order.
        from orders.tests.factories import make_order, make_user
        from wallet.settlement_clocks import record as record_clock, trusted_test_clock
        other_order = make_order(make_user())
        raw_clock = trusted_test_clock(deadline)
        record_clock(other_order, 'MANIFEST', self.subsidy.pk, 'PLAN', raw_clock)
        record_clock(other_order, 'PERSON', self.shares[0].pk, 'PERSON', raw_clock)

        self.assertCountEqual(self.settle(deadline), [user.pk for user in self.players])
        self.platform.refresh_from_db()
        self.order.refresh_from_db()
        for share in self.shares:
            share.refresh_from_db()
            share.assignment.refresh_from_db()
            self.assertEqual(share.paid_at, deadline)
            self.assertEqual(share.assignment.settled_at, deadline)
        self.assertEqual(self.order.settled_at, deadline)
        self.assertEqual(self.platform.balance, 0)
        self.assertEqual([get_wallet(account=account).balance for account in self.accounts], [3200, 4800])

        expenses = list(Transaction.objects.filter(
            order=self.order, tx_type='SUBSIDY_EXPENSE',
        ).order_by('pk'))
        incomes = list(Transaction.objects.filter(order=self.order, tx_type='INCOME').order_by('pk'))
        self.assertEqual([row.amount for row in expenses], [-3200, -4800])
        self.assertEqual([row.balance_before for row in expenses], [8000, 4800])
        self.assertEqual([row.balance_after for row in expenses], [4800, 0])
        self.assertEqual([row.amount for row in incomes], [3200, 4800])
        self.assertEqual([row.balance_before for row in incomes], [0, 0])
        self.assertEqual([row.balance_after for row in incomes], [3200, 4800])
        self.assertEqual([row.wallet_id for row in expenses], [self.platform.pk, self.platform.pk])
        self.assertEqual(
            [row.wallet.account_id for row in incomes], [account.pk for account in self.accounts],
        )
        self.assertEqual(sum(row.amount for row in incomes), -sum(row.amount for row in expenses))

        receipts = list(SettlementClockEvidence.objects.filter(order=self.order))
        self.assertEqual(len(receipts), 4)
        self.assertEqual({row.observed_at for row in receipts}, {deadline})
        self.assertEqual(len({json.dumps(row.clock_evidence, sort_keys=True) for row in receipts}), 1)
        plan = SettlementClockEvidence.objects.get(order=self.order, operation='PLAN')
        final = SettlementClockEvidence.objects.get(order=self.order, operation='FINALIZE')
        self.assertEqual((plan.subject_type, plan.subject_id), ('MANIFEST', (1 << 60) + self.subsidy.pk))
        self.assertEqual((final.subject_type, final.subject_id), ('MANIFEST', (1 << 60) + self.subsidy.pk))
        self.assertCountEqual(
            SettlementClockEvidence.objects.filter(order=self.order, operation='PERSON').values_list(
                'subject_id', flat=True,
            ),
            [(1 << 60) + share.pk for share in self.shares],
        )
        self.assertEqual(SettlementClockEvidence.objects.filter(order=other_order).count(), 2)

        later = deadline + timedelta(seconds=1)
        self.assertEqual(self.settle(later), [])
        with patch('django.utils.timezone.now', return_value=later):
            self.assertTrue(settle_platform_subsidy_share(self.shares[0].pk))
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME').count(), 2)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)
        self.assertEqual(SettlementClockEvidence.objects.filter(order=self.order).count(), 4)

    def test_person_receipt_failure_rolls_back_half_payment_then_original_facts_retry(self):
        self.align_reports()
        deadline = self.completed + timedelta(hours=72)
        from wallet.settlement_clocks import record as real_record

        def fail_first_person(*args, **kwargs):
            if args[3] == 'PERSON' and args[2] == (1 << 60) + self.shares[0].pk:
                raise ClockUnavailable('injected direct person receipt failure')
            return real_record(*args, **kwargs)

        with patch('orders.subsidies.record_clock', side_effect=fail_first_person):
            with self.assertRaises(ClockUnavailable):
                self.settle(deadline)
        self.assert_no_payout()

        self.assertCountEqual(self.settle(deadline), [user.pk for user in self.players])
        self.platform.refresh_from_db()
        self.assertEqual(self.platform.balance, 0)
        self.assertEqual([get_wallet(account=account).balance for account in self.accounts], [3200, 4800])
        self.assertEqual(SettlementClockEvidence.objects.filter(order=self.order).count(), 4)

    def test_tampered_original_receipts_block_remaining_share_and_retry_keeps_original_times(self):
        first_deadline = self.completed + timedelta(hours=72)
        second_deadline = self.completed + timedelta(hours=82)
        self.assertEqual(self.settle(first_deadline), [self.players[0].pk])
        person = SettlementClockEvidence.objects.get(
            order=self.order, subject_type='PERSON', subject_id=(1 << 60) + self.shares[0].pk, operation='PERSON',
        )
        original_person = dict(person.clock_evidence)
        SettlementClockEvidence.objects.filter(pk=person.pk).update(
            clock_evidence={**original_person, 'database_identity': 'another-store-database'},
        )
        with self.assertRaises(ClockConflict):
            self.settle(second_deadline)
        self.platform.refresh_from_db()
        self.assertEqual(self.platform.balance, 4800)
        self.assertEqual([get_wallet(account=account).balance for account in self.accounts], [3200, 0])
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME').count(), 1)
        self.assertFalse(SettlementClockEvidence.objects.filter(order=self.order, operation='FINALIZE').exists())

        SettlementClockEvidence.objects.filter(pk=person.pk).update(clock_evidence=original_person)
        plan = SettlementClockEvidence.objects.get(order=self.order, operation='PLAN')
        original_plan = dict(plan.clock_evidence)
        SettlementClockEvidence.objects.filter(pk=plan.pk).update(
            clock_evidence={**original_plan, 'database_identity': 'another-store-database'},
        )
        with self.assertRaises(ClockConflict):
            self.settle(second_deadline)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME').count(), 1)

        SettlementClockEvidence.objects.filter(pk=plan.pk).update(clock_evidence=original_plan)
        self.assertEqual(self.settle(second_deadline), [self.players[1].pk])
        self.platform.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.platform.balance, 0)
        self.assertEqual([get_wallet(account=account).balance for account in self.accounts], [3200, 4800])
        self.assertEqual(self.order.settled_at, second_deadline)
        plan.refresh_from_db()
        person.refresh_from_db()
        second_person = SettlementClockEvidence.objects.get(
            order=self.order, subject_type='PERSON', subject_id=(1 << 60) + self.shares[1].pk, operation='PERSON',
        )
        final = SettlementClockEvidence.objects.get(order=self.order, operation='FINALIZE')
        self.assertEqual((plan.observed_at, person.observed_at), (first_deadline, first_deadline))
        self.assertEqual((second_person.observed_at, final.observed_at), (second_deadline, second_deadline))
        self.assertEqual(SettlementClockEvidence.objects.filter(order=self.order).count(), 4)

    def test_missing_original_payment_fact_blocks_even_a_ready_share(self):
        Transaction.objects.filter(order=self.order, tx_type='PAY').delete()
        with self.assertRaisesMessage(SettlementError, '原付款'):
            self.settle(self.completed + timedelta(hours=72))
        self.assert_no_payout()
