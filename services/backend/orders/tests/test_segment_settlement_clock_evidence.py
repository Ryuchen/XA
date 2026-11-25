"""Database-clock evidence gates real segment earnings and finalization."""
from datetime import timedelta
import json
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from engagement.confirmation import aftersale_deadline
from orders.models import (
    Order,
    OrderSegmentPersonSettlement,
    OrderSegmentReportSubmission,
    OrderSegmentSettlementManifest,
)
from orders.segment_settlement import settle_due_segment_people, verify_finalized
from orders.tests import test_segment_settlement_plan as fixture
from stores.db import transaction
from wallet.models import SettlementClockEvidence, Transaction
from wallet.settlement_clocks import ClockConflict, ClockUnavailable


@override_settings(
    ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True,
    SEGMENT_SETTLEMENT_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
)
class SegmentSettlementClockEvidenceTest(APITransactionTestCase):
    setUp = fixture.SegmentSettlementPlanTest.setUp
    login = fixture.SegmentSettlementPlanTest.login
    ok = fixture.SegmentSettlementPlanTest.ok
    recharge = fixture.SegmentSettlementPlanTest.recharge
    new_order = fixture.SegmentSettlementPlanTest.new_order
    replacement = fixture.SegmentSettlementPlanTest.replacement
    transfer = fixture.SegmentSettlementPlanTest.transfer
    prepare = fixture.SegmentSettlementPlanTest.prepare
    act = fixture.SegmentSettlementPlanTest.act
    review = fixture.SegmentSettlementPlanTest.review
    finished_order = fixture.SegmentSettlementPlanTest.finished_order
    submit_and_approve = fixture.SegmentSettlementPlanTest.submit_and_approve

    def finished_order(self):
        order, replacement, old, current = fixture.SegmentSettlementPlanTest.finished_order(self)
        # This clock slice does not exercise earnings-funded deposit cycles.
        # Keep the original income split and freeze clocks, while avoiding the
        # separately tracked provider deposit-cycle integration prerequisite.
        order.service_deposit_percent = 0
        order.save(update_fields=['service_deposit_percent'])
        return order, replacement, old, current

    def submit_and_approve_at(self, order, segment, user, at):
        with patch('django.utils.timezone.now', return_value=at):
            self.submit_and_approve(order, segment, user)

    def settle_at(self, order, at):
        with patch('django.utils.timezone.now', return_value=at):
            return self.settle(order)

    def settle(self, order):
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            return settle_due_segment_people(locked)

    def deadlines(self, order):
        order.refresh_from_db()
        submissions = OrderSegmentReportSubmission.objects.filter(
            report__segment__attribution__order=order, status='APPROVED')
        return [aftersale_deadline(order),
                *(row.submitted_at + timedelta(hours=order.service_freeze_hours) for row in submissions)]

    def deadline(self, order):
        return max(self.deadlines(order))

    def test_no_income_before_deadline_then_exact_boundary_posts_once_with_one_clock(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        deadline = self.deadline(order)

        self.assertEqual(self.settle_at(order, min(self.deadlines(order)) - timedelta(microseconds=1)), [])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())

        self.assertCountEqual(self.settle_at(order, deadline), [self.provider.pk, replacement.pk])
        people = list(OrderSegmentPersonSettlement.objects.filter(manifest__order=order))
        self.assertEqual(len(people), 2)
        self.assertEqual({row.settled_at for row in people}, {deadline})
        self.assertEqual(
            list(Transaction.objects.filter(order=order, tx_type='INCOME').order_by('pk').values_list('amount', flat=True)),
            [3200, 4800],
        )
        order.refresh_from_db()
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        self.assertEqual((order.provider_income, order.shop_income, order.settled_at), (8000, 2000, deadline))
        receipts = list(SettlementClockEvidence.objects.filter(
            order=order, operation__in=['PLAN', 'PERSON', 'FINALIZE']))
        self.assertEqual(len(receipts), 4)
        self.assertEqual({row.observed_at for row in receipts}, {deadline})
        self.assertEqual(len({json.dumps(row.clock_evidence, sort_keys=True) for row in receipts}), 1)

        with patch('django.utils.timezone.now', return_value=deadline + timedelta(seconds=1)):
            verify_finalized(order, manifest)
        with override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            self.assertEqual(self.settle(order), [])
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SHOP_INCOME').count(), 1)

        final = SettlementClockEvidence.objects.get(order=order, operation='FINALIZE')
        SettlementClockEvidence.objects.filter(pk=final.pk).update(
            clock_evidence={**final.clock_evidence, 'database_identity': 'another-store-database'})
        with patch('django.utils.timezone.now', return_value=deadline + timedelta(seconds=2)):
            with self.assertRaises(ClockConflict):
                verify_finalized(order, manifest)

    def test_clock_unavailable_posts_nothing_and_same_facts_can_retry(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        deadline = self.deadline(order)
        with override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            with self.assertRaises(ClockUnavailable):
                self.settle_at(order, deadline)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        self.assertFalse(SettlementClockEvidence.objects.filter(order=order, operation__in=['PLAN', 'PERSON']).exists())

        self.assertCountEqual(self.settle_at(order, deadline), [self.provider.pk, replacement.pk])
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)

    def test_tampered_submission_receipt_keeps_that_person_unpaid(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        submission = OrderSegmentReportSubmission.objects.get(report__segment=old, version=1)
        receipt = SettlementClockEvidence.objects.get(
            order=order, subject_type='SUBMISSION', subject_id=submission.pk, operation='SUBMIT')
        SettlementClockEvidence.objects.filter(pk=receipt.pk).update(
            clock_evidence={**receipt.clock_evidence, 'database_identity': 'another-store-database'})

        self.assertEqual(self.settle_at(order, self.deadline(order)), [])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        self.assertFalse(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists())
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())

    def test_tampered_original_person_receipt_blocks_remaining_credit(self):
        order, replacement, old, current = self.finished_order()
        first_submitted = timezone.now()
        self.submit_and_approve_at(order, old, self.provider, first_submitted)
        self.submit_and_approve_at(order, current, replacement, first_submitted + timedelta(hours=1))
        first_deadline = max(
            aftersale_deadline(order),
            OrderSegmentReportSubmission.objects.get(report__segment=old).submitted_at
            + timedelta(hours=order.service_freeze_hours),
        )
        second_deadline = self.deadline(order)
        self.assertGreater(second_deadline, first_deadline)
        self.assertEqual(self.settle_at(order, first_deadline), [self.provider.pk])
        paid = OrderSegmentPersonSettlement.objects.get(
            manifest__order=order, account=self.accounts[self.provider.pk])
        receipt = SettlementClockEvidence.objects.get(
            order=order, subject_type='PERSON', subject_id=paid.pk, operation='PERSON')
        SettlementClockEvidence.objects.filter(pk=receipt.pk).update(
            clock_evidence={**receipt.clock_evidence, 'database_identity': 'another-store-database'})

        with self.assertRaises(ClockConflict):
            self.settle_at(order, second_deadline)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
        self.assertEqual(paid.income_transaction.amount, 3200)
        self.assertFalse(OrderSegmentPersonSettlement.objects.filter(
            manifest__order=order, account=self.accounts[replacement.pk]).exists())
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)

    def test_person_receipt_failure_rolls_back_credit_and_retry_preserves_amount(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        deadline = self.deadline(order)
        from wallet.settlement_clocks import record as real_record

        def fail_person(*args):
            if args[3] == 'PERSON':
                raise ClockUnavailable('injected person receipt failure')
            return real_record(*args)

        with patch('orders.segment_settlement.record', side_effect=fail_person):
            with self.assertRaises(ClockUnavailable):
                self.settle_at(order, deadline)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        self.assertFalse(SettlementClockEvidence.objects.filter(order=order, operation='PLAN').exists())

        self.assertEqual(self.settle_at(order, deadline), [self.provider.pk])
        paid = OrderSegmentPersonSettlement.objects.get(manifest__order=order)
        self.assertEqual((paid.amount, paid.income_transaction.amount, paid.settled_at), (3200, 3200, deadline))
        self.assertEqual(self.settle_at(order, deadline + timedelta(seconds=1)), [])
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
