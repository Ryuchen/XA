"""Safety-box segment settlement shares the trusted settlement clock receipts."""
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import (
    Order,
    OrderPlatformSubsidy,
    OrderPlatformSubsidyShare,
    OrderProvider,
    OrderSegmentPersonSettlement,
    OrderSegmentSettlementManifest,
)
from orders.segment_settlement_plan import PersonAmount, SegmentAmount
from orders.subsidy_segment_settlement import settle_locked
from orders.tests.factories import make_order, make_provider, make_user
from stores.db import transaction
from wallet.models import SettlementClockEvidence, Transaction, get_platform_wallet
from wallet.services import get_wallet
from wallet.settlement_clocks import (ClockConflict, ClockUnavailable, receipt,
                                      require_clock, validate_receipt, verify)


@override_settings(
    VIP_SAFE_BOX_SUBSIDY_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
)
class SafeBoxSegmentClockEvidenceTest(TestCase):
    def setUp(self):
        self.boss = make_user()
        self.customer = get_or_create_account_for_legacy_user(self.boss)
        self.users = [make_provider(deposit_mode='CASH', deposit_paid=40000) for _ in range(2)]
        self.accounts = [get_or_create_account_for_legacy_user(user) for user in self.users]
        for user, account in zip(self.users, self.accounts):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
            get_wallet(user=user, account=account)
        self.now = timezone.now()
        self.completed = self.now - timedelta(hours=100)
        self.order = make_order(
            self.boss, provider=self.users[1], amount=0, status='COMPLETED', payment_status='PAID',
            settlement_mode='REPORT_APPROVAL', completed_at=self.completed,
            customer_account=self.customer, provider_account=self.accounts[1],
            provider_income=0, shop_income=0, inviter_commission=0,
            service_clock_version=1, service_aftersale_hours=72,
            service_freeze_hours=72, service_deposit_percent=60,
        )
        Transaction.objects.create(
            wallet=get_wallet(user=self.boss, account=self.customer), order=self.order,
            tx_type='PAY', amount=0, balance_before=0, balance_after=0)
        self.subsidy = OrderPlatformSubsidy.objects.create(
            order=self.order, coupon_id_snapshot=1, basis=10000)
        self.assignments = [
            OrderProvider.objects.create(
                order=self.order, provider=user, provider_account=account,
                settlement_base=0, provider_income=0,
                released_at=self.completed if index == 0 else None)
            for index, (user, account) in enumerate(zip(self.users, self.accounts))
        ]
        self.shares = [
            OrderPlatformSubsidyShare.objects.create(
                subsidy=self.subsidy, assignment=assignment, amount=amount)
            for assignment, amount in zip(self.assignments, (3200, 4800))
        ]
        freeze_until = self.completed + timedelta(hours=73)
        self.plan = SimpleNamespace(
            deal_amount=10000,
            segments=(
                SegmentAmount(101, self.accounts[0].pk, 4000, 800, 3200),
                SegmentAmount(102, self.accounts[1].pk, 6000, 1200, 4800),
            ),
            persons=(
                PersonAmount(self.accounts[0].pk, (101,), 3200, freeze_until, ()),
                PersonAmount(self.accounts[1].pk, (102,), 4800, freeze_until, ()),
            ),
        )
        self.platform = get_platform_wallet()
        self.platform.balance = 8000
        self.platform.save(update_fields=['balance'])

    def report_versions(self, person, *, clock=None):
        return [{
            'segment_id': person.segment_ids[0], 'version': 1,
            'submitted_at': (self.completed + timedelta(hours=1)).isoformat(),
            'reviewed_at': (self.completed + timedelta(hours=2)).isoformat(),
            'reviewer_id': 1, 'evidence_hash': '0' * 64,
        }]

    def verified_records(self, order, shares, manifest, plan):
        clock = require_clock()
        plan_clock = receipt(order, 'MANIFEST', manifest.pk, 'PLAN')
        if plan_clock is None:
            raise ClockConflict('安全箱清单缺少时钟回执')
        validate_receipt(plan_clock.clock_evidence, plan_clock.observed_at, clock)
        rows = {row.account_id: row for row in manifest.people.all()}
        for row in rows.values():
            verify(order, 'PERSON', row.pk, 'PERSON', row.settled_at, clock)
        return rows

    def settle(self, at):
        with (patch('orders.subsidies._locked_subsidy_shares', return_value=self.shares),
              patch('orders.subsidy_segment_settlement._plan', return_value=self.plan),
              patch('orders.subsidy_segment_settlement._verified_records', side_effect=self.verified_records),
              patch('orders.segment_settlement._report_versions', side_effect=self.report_versions),
              patch('orders.services.refresh_escort_status'),
              patch('django.utils.timezone.now', return_value=at),
              transaction.atomic()):
            locked = Order.objects.select_for_update().get(pk=self.order.pk)
            return settle_locked(locked)

    def assert_no_payout(self):
        self.platform.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.platform.balance, 8000)
        self.assertEqual(
            [get_wallet(user=user, account=account).balance for user, account in zip(self.users, self.accounts)],
            [0, 0],
        )
        self.assertFalse(Transaction.objects.filter(
            order=self.order, tx_type__in=['INCOME', 'SUBSIDY_EXPENSE']).exists())
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=self.order).exists())
        self.assertIsNone(self.order.settled_at)

    def test_settlement_persists_plan_person_and_finalize_receipts_with_exact_funds(self):
        self.assertCountEqual(self.settle(self.now), [user.pk for user in self.users])
        self.order.refresh_from_db()
        self.platform.refresh_from_db()
        self.assertEqual(self.platform.balance, 0)
        self.assertEqual(
            [get_wallet(user=user, account=account).balance for user, account in zip(self.users, self.accounts)],
            [3200, 4800],
        )
        self.assertEqual(
            sorted(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').values_list('amount', flat=True)),
            [-4800, -3200],
        )
        self.assertEqual(
            sorted(Transaction.objects.filter(order=self.order, tx_type='INCOME').values_list('amount', flat=True)),
            [3200, 4800],
        )
        people = list(OrderSegmentPersonSettlement.objects.filter(manifest__order=self.order))
        self.assertEqual({row.settled_at for row in people}, {self.now})
        self.assertEqual(self.order.settled_at, self.now)
        manifest = OrderSegmentSettlementManifest.objects.get(order=self.order)
        self.assertEqual(manifest.finalized_at, self.now)
        receipts = list(SettlementClockEvidence.objects.filter(
            order=self.order, operation__in=['PLAN', 'PERSON', 'FINALIZE']))
        self.assertEqual(len(receipts), 4)
        self.assertEqual({row.observed_at for row in receipts}, {self.now})
        self.assertEqual(len({json.dumps(row.clock_evidence, sort_keys=True) for row in receipts}), 1)

        self.assertEqual(self.settle(self.now + timedelta(seconds=1)), [])
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME').count(), 2)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)


    def test_person_receipt_failure_rolls_back_subsidy_and_retry_preserves_funds(self):
        from wallet.settlement_clocks import record as real_record

        def fail_person(*args, **kwargs):
            if args[3] == 'PERSON':
                raise ClockUnavailable('injected safety-box person receipt failure')
            return real_record(*args, **kwargs)

        with patch('orders.subsidy_segment_settlement.record_clock', side_effect=fail_person):
            with self.assertRaises(ClockUnavailable):
                self.settle(self.now)
        self.assert_no_payout()
        self.assertFalse(SettlementClockEvidence.objects.filter(order=self.order, operation='PLAN').exists())

        self.assertCountEqual(self.settle(self.now), [user.pk for user in self.users])
        self.platform.refresh_from_db()
        self.assertEqual(self.platform.balance, 0)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME').count(), 2)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)

    def test_tampered_paid_person_receipt_blocks_remaining_subsidy(self):
        second_ready = self.plan.persons[1]
        self.plan.persons = (
            self.plan.persons[0],
            PersonAmount(second_ready.account_id, second_ready.segment_ids, second_ready.income,
                         second_ready.freeze_until, ('本人报单尚未到期',)),
        )
        self.assertEqual(self.settle(self.now), [self.users[0].pk])
        self.assertIsNone(Order.objects.get(pk=self.order.pk).settled_at)
        paid = OrderSegmentPersonSettlement.objects.get(
            manifest__order=self.order, account=self.accounts[0])
        original = SettlementClockEvidence.objects.get(
            order=self.order, subject_type='PERSON', subject_id=paid.pk, operation='PERSON')
        SettlementClockEvidence.objects.filter(pk=original.pk).update(
            clock_evidence={**original.clock_evidence, 'database_identity': 'another-store-database'})
        self.plan.persons = (self.plan.persons[0], second_ready)

        with self.assertRaises(ClockConflict):
            self.settle(self.now + timedelta(seconds=1))
        self.platform.refresh_from_db()
        self.assertEqual(self.platform.balance, 4800)
        self.assertEqual(get_wallet(user=self.users[0], account=self.accounts[0]).balance, 3200)
        self.assertEqual(get_wallet(user=self.users[1], account=self.accounts[1]).balance, 0)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 1)
        self.assertFalse(OrderSegmentPersonSettlement.objects.filter(
            manifest__order=self.order, account=self.accounts[1]).exists())
