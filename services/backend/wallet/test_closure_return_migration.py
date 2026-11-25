"""Real 0066 -> 0067 data migration coverage on an isolated migration database."""
from types import SimpleNamespace
from unittest import skipIf
from uuid import uuid4

from django.apps import apps as global_apps
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin
from stores.context import resolve_store, store_context


OLD = [('wallet', '0066_closure_returns')]
NEW = [('wallet', '0067_closure_return_paid_terminal')]


def _frozen_case(apps, label, snapshot_amounts, current_amounts, *, wallet_balance=0):
    Account = apps.get_model('club_accounts', 'ClubAccount')
    Wallet = apps.get_model('wallet', 'Wallet')
    Transaction = apps.get_model('wallet', 'Transaction')
    FundingLot = apps.get_model('engagement', 'PointFundingLot')
    Case = apps.get_model('wallet', 'ClosureReturnCase')
    Snapshot = apps.get_model('wallet', 'ClosureReturnLot')
    Event = apps.get_model('wallet', 'ClosureReturnEvent')
    Outbox = apps.get_model('wallet', 'ClosureReturnOutbox')
    principal = sum(snapshot_amounts)
    account = Account.objects.create(
        username=f'migration-{label}', account_no=f'XA-{label}', account_type='BOSS', business_cycle=1,
    )
    wallet = Wallet.objects.create(account_id=account.pk, balance=wallet_balance, frozen_amount=principal)
    freeze = Transaction.objects.create(
        wallet_id=wallet.pk, tx_no=f'freeze-{label}'[:32], tx_type='CLOSURE_FREEZE', amount=-principal,
        status='SUCCESS', balance_before=principal, balance_after=0, remark='0066 old freeze',
    )
    actor = uuid4()
    case = Case.objects.create(
        account_id=account.pk, business_cycle=1, wallet_id=wallet.pk, status='FROZEN', active_key='ACTIVE',
        principal_units=principal, version=1, freeze_transaction_id=freeze.pk, prepared_by=actor,
    )
    lots, snapshots = [], []
    for index, (original, current) in enumerate(zip(snapshot_amounts, current_amounts, strict=True)):
        source_key = f'recharge:{label}:{index}'
        lot = FundingLot.objects.create(
            account_id=account.pk, source_key=source_key, kind='RECHARGE', remaining=current, gift_remaining=0,
        )
        snapshot = Snapshot.objects.create(
            case_id=case.pk, source_lot_id=lot.pk, source_key=source_key, source_kind='RECHARGE',
            remaining_units=original, principal_units=original, gift_units=0,
        )
        lots.append(lot)
        snapshots.append(snapshot)
    event = Event.objects.create(
        case_id=case.pk, kind='PREPARED', client_request_id=f'prepare-{label}',
        request_fingerprint='a' * 64, platform_actor_id=actor,
        from_status='READY_TO_PREPARE', to_status='FROZEN', response={},
        detail={
            'account_id': account.pk, 'business_cycle': 1, 'principal_units': principal,
            'refund_fen': principal, 'wallet_before': principal, 'wallet_after': 0,
            'frozen_before': 0, 'frozen_after': principal,
            'lot_ids': [lot.pk for lot in lots], 'freeze_transaction_id': freeze.pk,
        },
    )
    Outbox.objects.create(event_id=event.pk, status='PENDING')
    return {
        'account_id': account.pk, 'case_id': case.pk, 'wallet_id': wallet.pk,
        'freeze_id': freeze.pk, 'event_id': event.pk,
        'lot_ids': [lot.pk for lot in lots], 'snapshot_ids': [row.pk for row in snapshots],
    }


def _paid_case(apps, label):
    ids = _frozen_case(apps, label, [100], [100])
    Wallet = apps.get_model('wallet', 'Wallet')
    Transaction = apps.get_model('wallet', 'Transaction')
    FundingLot = apps.get_model('engagement', 'PointFundingLot')
    Case = apps.get_model('wallet', 'ClosureReturnCase')
    Event = apps.get_model('wallet', 'ClosureReturnEvent')
    Outbox = apps.get_model('wallet', 'ClosureReturnOutbox')
    Wallet.objects.filter(pk=ids['wallet_id']).update(frozen_amount=0)
    FundingLot.objects.filter(pk=ids['lot_ids'][0]).update(remaining=0)
    payment = Transaction.objects.create(
        wallet_id=ids['wallet_id'], tx_no=f'payment-{label}'[:32], tx_type='CLOSURE_PAYMENT', amount=0,
        status='SUCCESS', balance_before=0, balance_after=0, remark='0066 old payment',
    )
    now = timezone.now()
    actor = uuid4()
    Case.objects.filter(pk=ids['case_id']).update(
        status='PAID', active_key='ACTIVE', payment_transaction_id=payment.pk,
        paid_fen=100, paid_at=now, paid_by=actor, offline_occurred_at=now,
    )
    event = Event.objects.create(
        case_id=ids['case_id'], kind='PAYMENT_RECORDED', client_request_id=f'paid-{label}',
        request_fingerprint='b' * 64, platform_actor_id=actor,
        from_status='FROZEN', to_status='PAID', response={}, detail={},
    )
    Outbox.objects.create(event_id=event.pk, status='PENDING')
    ids['payment_id'] = payment.pk
    ids['payment_event_id'] = event.pk
    return ids


def _facts(model, **filters):
    return list(model.objects.filter(**filters).order_by('pk').values())


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class ClosureReturn0067MigrationTests(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_safe_conversion_changed_case_review_and_paid_cleanup(self):
        executor = MigrationExecutor(connection)
        executor.migrate(OLD)
        apps = executor.loader.project_state(OLD).apps
        safe = _frozen_case(apps, 'safe', [100], [100])
        changed = _frozen_case(apps, 'changed', [100], [700], wallet_balance=700)
        paid = _paid_case(apps, 'paid')
        Transaction = apps.get_model('wallet', 'Transaction')
        # 0066-era refund 1000, then old gift excess adjustment and payment 300.
        Transaction.objects.create(
            wallet_id=changed['wallet_id'], tx_no='changed-refund', tx_type='REFUND', amount=1000,
            status='SUCCESS', balance_before=0, balance_after=1000,
        )
        Transaction.objects.create(
            wallet_id=changed['wallet_id'], tx_no='changed-gift-pay', tx_type='PAY', amount=-300,
            status='SUCCESS', balance_before=1000, balance_after=700,
        )
        before = {
            key: _facts(Transaction, wallet_id=value['wallet_id'])
            for key, value in (('safe', safe), ('changed', changed), ('paid', paid))
        }

        executor = MigrationExecutor(connection)
        executor.migrate(NEW)
        apps = executor.loader.project_state(NEW).apps
        Case = apps.get_model('wallet', 'ClosureReturnCase')
        Wallet = apps.get_model('wallet', 'Wallet')
        FundingLot = apps.get_model('engagement', 'PointFundingLot')
        Snapshot = apps.get_model('wallet', 'ClosureReturnLot')
        Event = apps.get_model('wallet', 'ClosureReturnEvent')
        Outbox = apps.get_model('wallet', 'ClosureReturnOutbox')
        Transaction = apps.get_model('wallet', 'Transaction')

        changed_case = Case.objects.get(pk=changed['case_id'])
        self.assertEqual(FundingLot.objects.get(pk=changed['lot_ids'][0]).remaining, 700)
        self.assertTrue(changed_case.migration_review_required)
        self.assertTrue(changed_case.migration_review_reason)
        self.assertEqual(changed_case.active_key, 'ACTIVE')
        changed_wallet = Wallet.objects.get(pk=changed['wallet_id'])
        self.assertEqual((changed_wallet.balance, changed_wallet.frozen_amount), (700, 100))
        self.assertEqual(_facts(Transaction, wallet_id=changed['wallet_id']), before['changed'])
        self.assertEqual(Event.objects.filter(case_id=changed['case_id']).count(), 1)
        self.assertEqual(Outbox.objects.filter(event_id=changed['event_id']).count(), 1)
        self.assertEqual(Snapshot.objects.get(pk=changed['snapshot_ids'][0]).principal_units, 100)

        safe_case = Case.objects.get(pk=safe['case_id'])
        self.assertFalse(safe_case.migration_review_required)
        self.assertEqual(safe_case.migration_review_reason, '')
        self.assertEqual(FundingLot.objects.get(pk=safe['lot_ids'][0]).remaining, 0)
        safe_wallet = Wallet.objects.get(pk=safe['wallet_id'])
        self.assertEqual((safe_wallet.balance, safe_wallet.frozen_amount), (0, 100))
        self.assertEqual(_facts(Transaction, wallet_id=safe['wallet_id']), before['safe'])
        self.assertEqual(Event.objects.filter(case_id=safe['case_id']).count(), 1)
        self.assertEqual(Outbox.objects.filter(event_id=safe['event_id']).count(), 1)
        self.assertEqual(Snapshot.objects.get(pk=safe['snapshot_ids'][0]).principal_units, 100)

        paid_case = Case.objects.get(pk=paid['case_id'])
        self.assertEqual((paid_case.status, paid_case.active_key, paid_case.paid_fen), ('PAID', None, 100))
        self.assertFalse(paid_case.migration_review_required)
        self.assertEqual(FundingLot.objects.get(pk=paid['lot_ids'][0]).remaining, 0)
        paid_wallet = Wallet.objects.get(pk=paid['wallet_id'])
        self.assertEqual((paid_wallet.balance, paid_wallet.frozen_amount), (0, 0))
        self.assertEqual(_facts(Transaction, wallet_id=paid['wallet_id']), before['paid'])
        self.assertEqual(Event.objects.filter(case_id=paid['case_id']).count(), 2)
        self.assertEqual(Outbox.objects.filter(event__case_id=paid['case_id']).count(), 2)
        self.assertEqual(Snapshot.objects.get(pk=paid['snapshot_ids'][0]).principal_units, 100)

    def test_shortfall_rolls_back_partial_lot_changes_and_marks_review(self):
        executor = MigrationExecutor(connection)
        executor.migrate(OLD)
        apps = executor.loader.project_state(OLD).apps
        short = _frozen_case(apps, 'short', [60, 40], [60, 30])
        Transaction = apps.get_model('wallet', 'Transaction')
        before = _facts(Transaction, wallet_id=short['wallet_id'])

        executor = MigrationExecutor(connection)
        executor.migrate(NEW)
        apps = executor.loader.project_state(NEW).apps
        Case = apps.get_model('wallet', 'ClosureReturnCase')
        Wallet = apps.get_model('wallet', 'Wallet')
        FundingLot = apps.get_model('engagement', 'PointFundingLot')
        Snapshot = apps.get_model('wallet', 'ClosureReturnLot')
        Event = apps.get_model('wallet', 'ClosureReturnEvent')
        Outbox = apps.get_model('wallet', 'ClosureReturnOutbox')
        Transaction = apps.get_model('wallet', 'Transaction')
        case = Case.objects.get(pk=short['case_id'])
        self.assertTrue(case.migration_review_required)
        self.assertEqual(case.migration_review_reason, 'FROZEN_LOT_SHORTFALL')
        self.assertEqual(case.active_key, 'ACTIVE')
        self.assertEqual(
            list(FundingLot.objects.filter(pk__in=short['lot_ids']).order_by('pk').values_list('remaining', flat=True)),
            [60, 30],
        )
        wallet = Wallet.objects.get(pk=short['wallet_id'])
        self.assertEqual((wallet.balance, wallet.frozen_amount), (0, 100))
        self.assertEqual(_facts(Transaction, wallet_id=short['wallet_id']), before)
        self.assertEqual(
            list(Snapshot.objects.filter(case_id=short['case_id']).order_by('pk').values_list('principal_units', flat=True)),
            [60, 40],
        )
        self.assertEqual(Event.objects.filter(case_id=short['case_id']).count(), 1)
        self.assertEqual(Outbox.objects.filter(event_id=short['event_id']).count(), 1)


class ClosureReturnMigrationFunctionTests(TestCase):
    """Fast funds regression; executor tests above remain the migration gate."""

    def test_changed_frozen_case_is_never_subtracted_again(self):
        import importlib

        migration = importlib.import_module('wallet.migrations.0067_closure_return_paid_terminal')
        with store_context(resolve_store()):
            ids = _frozen_case(global_apps, 'direct', [100], [700], wallet_balance=700)
            migration.migrate_preexisting_returns(
                global_apps, SimpleNamespace(connection=connection),
            )
            lot = global_apps.get_model('engagement', 'PointFundingLot').objects.get(pk=ids['lot_ids'][0])
            self.assertEqual(lot.remaining, 700)
            case = global_apps.get_model('wallet', 'ClosureReturnCase').objects.get(pk=ids['case_id'])
            self.assertTrue(case.migration_review_required)
