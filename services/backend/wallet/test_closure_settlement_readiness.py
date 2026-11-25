"""E3 store-local, read-only funds settlement readiness."""
from datetime import datetime, timedelta, timezone as datetime_timezone
import hashlib
import json
from unittest.mock import patch

from django.db import DatabaseError, connections
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from engagement.models import MemberProfile, PointFundingLot
from stores.closure_clock import CLOCK_SOURCE, ControlClockEvidence
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from stores.tests_hosts import xinan_store
from wallet.closure_settlement_readiness import build_closure_funds_readiness
from wallet.models import ClosureReturnCase, ClosureReturnEvent, ClosureReturnOutbox, RechargeRecord, Transaction, Wallet


@override_settings(
    PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    STORE_HOST_BINDINGS={
        'sl.esports.club': 'shenglang', 'xa.esports.club': 'xinan', 'testserver': 'shenglang',
    },
)
class ClosureSettlementReadinessTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.actor = PlatformAccount.objects.create(
            username='readiness-platform', nickname='预检平台账号', phone='13800009901', is_phone_verified=True,
        )
        PlatformRoleGrant.objects.create(account=self.actor, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(self.actor, 'SMS', self.actor.phone)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.now = datetime(2026, 11, 1, 4, 0, tzinfo=datetime_timezone.utc)
        patcher = patch('stores.closure.read_control_clock', side_effect=lambda: ControlClockEvidence(
            self.now, CLOCK_SOURCE, 'sqlite-test:default',
        ))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sequence = 0

    def account(self, store, pk=910001, *, points=True, nickname=None):
        with store_context(store):
            account = ClubAccount.objects.create_account(
                id=pk, username=f'readiness-{pk}', account_type='BOSS', account_no=f'XA{pk}',
                nickname=nickname or f'老板隐私-{store.slug}-{pk}',
            )
            if points:
                MemberProfile.objects.create(account=account, points_balance=0)
            return account

    def verified_recharge(self, store, account, amount, *, gift=0):
        self.sequence += 1
        with store_context(store):
            wallet = Wallet.objects.get(account=account)
            trade_no = f'readiness-{store.slug}-{account.pk}-{self.sequence}'
            record = RechargeRecord.objects.create(
                account=account, account_business_cycle=account.business_cycle,
                amount=amount, gift_amount=gift, trade_no=trade_no,
                receipt_key=hashlib.sha256(trade_no.casefold().encode()).hexdigest(),
                proof_image='recharges/readiness-proof.jpg',
                bonus_snapshot={
                    'amount': amount, 'gift_amount': gift, 'remaining': amount,
                    'matches': [], 'version': '0' * 64, 'points_rule': 'VIP_AT_PAYMENT',
                },
            )
            before = wallet.balance
            recharge_tx = Transaction.objects.create(
                wallet=wallet, tx_type='TOPUP', amount=amount, status='SUCCESS',
                balance_before=before, balance_after=before + amount,
            )
            gift_tx = None
            if gift:
                gift_tx = Transaction.objects.create(
                    wallet=wallet, tx_type='GIFT', amount=gift, status='SUCCESS',
                    balance_before=before + amount, balance_after=before + amount + gift,
                )
            record.recharge_tx, record.gift_tx = recharge_tx, gift_tx
            record.save(update_fields=['recharge_tx', 'gift_tx'])
            wallet.balance += amount + gift
            wallet.total_recharge += amount
            wallet.total_gift += gift
            wallet.save(update_fields=['balance', 'total_recharge', 'total_gift'])
            lot = PointFundingLot.objects.create(
                account=account, source_key=f'recharge:{record.pk}', kind='RECHARGE',
                remaining=amount + gift, gift_remaining=gift,
            )
            return lot

    def funded_account(self, store, pk=910001, amount=100, **kwargs):
        account = self.account(store, pk, **kwargs)
        with store_context(store):
            Wallet.objects.create(account=account, balance=0)
        self.verified_recharge(store, account, amount)
        return account

    def enter_settlement(self, store):
        started = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure',
            {'reason_category': 'OPERATOR_REQUEST', 'reason': '预检测试闭店',
             'client_request_id': f'readiness-closure-{store.slug}'}, format='json',
        )
        self.assertEqual(started.status_code, 201, started.content)
        self.now += timedelta(days=31)
        confirmed = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure/confirm-expiry',
            {'reason': '公示到期', 'client_request_id': f'readiness-expiry-{store.slug}'}, format='json',
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.content)

    def prepare(self, store, account, amount, request_id):
        response = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure/returns/prepare',
            {'account_id': account.pk, 'business_cycle': account.business_cycle,
             'expected_wallet_units': amount, 'expected_principal_units': amount,
             'client_request_id': request_id}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.data['data']['case_id']

    def pay(self, store, account, case_id, amount, request_id, *, external=None):
        response = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure/returns/{case_id}/record-payment',
            {'amount_fen': amount, 'external_reference': external or f'OFFLINE-{store.slug}-{account.pk}',
             'proof_sha256': hashlib.sha256(b'readiness-payment-proof').hexdigest(),
             'occurred_at': '2026-11-25T02:00:00+00:00', 'client_request_id': request_id}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.data['data']

    def paid(self, store, account, amount, label, *, external=None):
        case_id = self.prepare(store, account, amount, f'{label}-prepare')
        self.pay(store, account, case_id, amount, f'{label}-pay', external=external)
        return case_id

    def release(self, store, case_id, request_id):
        response = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure/returns/{case_id}/release',
            {'reason': '核实未实付，解除原冻结', 'client_request_id': request_id}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.data['data']

    def readiness(self, store):
        return build_closure_funds_readiness(store)['funds']

    def codes(self, result):
        return [row['code'] for row in result['blockers']]

    def test_empty_account_list_is_an_explicit_blocker(self):
        result = self.readiness(self.first)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(result['complete'])
        self.assertEqual(result['counts']['accounts'], 0)
        self.assertEqual(result['counts']['completed'], 0)
        self.assertIn('EMPTY_ACCOUNT_LIST', self.codes(result))

    def test_legal_zero_balance_is_clear_and_distinct_from_review(self):
        account = self.account(self.first)
        with store_context(self.first):
            Wallet.objects.create(account=account, balance=0, frozen_amount=0)
        result = self.readiness(self.first)
        self.assertEqual(result['status'], 'CLEAR')
        self.assertTrue(result['complete'])
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['counts']['completed'], 1)
        self.assertEqual(result['counts']['zero_balance'], 1)
        self.assertEqual(result['totals']['wallet_units'], 0)
        self.assertEqual(set(result['totals']), {
            'wallet_units', 'principal_units', 'gift_units', 'compensation_units',
            'points_hundredths', 'frozen_units', 'refund_fen', 'paid_fen',
        })
        self.assertEqual(result['totals']['principal_units'], 0)
        self.assertEqual(result['totals']['refund_fen'], 0)

    def test_ready_gift_and_compensation_block_with_complete_known_totals(self):
        self.funded_account(self.first, 910001, 100)
        mixed = self.account(self.first, 910002)
        with store_context(self.first):
            Wallet.objects.create(account=mixed, balance=0)
        self.verified_recharge(self.first, mixed, 600, gift=400)
        compensation = self.account(self.first, 910003)
        with store_context(self.first):
            wallet = Wallet.objects.create(account=compensation, balance=50)
            PointFundingLot.objects.create(
                account=compensation, source_key='readiness-compensation', kind='COMPENSATION', remaining=50,
            )
        result = self.readiness(self.first)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(result['complete'])
        self.assertIn('READY_TO_PREPARE', self.codes(result))
        self.assertIn('NON_PRINCIPAL_BALANCE', self.codes(result))
        self.assertEqual(result['totals']['gift_units'], 400)
        self.assertEqual(result['totals']['compensation_units'], 50)

    def test_unknown_source_and_missing_points_never_become_zero_totals(self):
        unknown = self.funded_account(self.first, 910001, 100)
        with store_context(self.first):
            PointFundingLot.objects.filter(account=unknown).update(gift_remaining=None)
        no_points = self.account(self.first, 910002, points=False)
        with store_context(self.first):
            Wallet.objects.create(account=no_points, balance=0)
        result = self.readiness(self.first)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertFalse(result['complete'])
        self.assertIsNone(result['totals'])
        self.assertIn('UNKNOWN_RECHARGE_SPLIT', self.codes(result))
        self.assertIn('POINTS_UNAVAILABLE', self.codes(result))

    def test_real_paid_facts_are_clear_on_two_stores_with_same_numeric_account(self):
        left = self.funded_account(self.first, 910001, 100)
        right = self.funded_account(self.second, 910001, 200)
        self.enter_settlement(self.first)
        self.enter_settlement(self.second)
        self.paid(self.first, left, 100, 'left-paid', external='SHARED-CROSS-STORE-REFERENCE')
        self.paid(self.second, right, 200, 'right-paid', external='SHARED-CROSS-STORE-REFERENCE')
        first = self.readiness(self.first)
        second = self.readiness(self.second)
        self.assertEqual((first['status'], second['status']), ('CLEAR', 'CLEAR'))
        self.assertEqual((first['counts']['paid'], second['counts']['paid']), (1, 1))
        self.assertNotEqual(first['facts_sha256'], second['facts_sha256'])

    def test_paid_history_cannot_hide_new_balance(self):
        account = self.funded_account(self.first, 910001, 100)
        self.enter_settlement(self.first)
        case_id = self.paid(self.first, account, 100, 'paid-new')
        self.verified_recharge(self.first, account, 50)
        result = self.readiness(self.first)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('READY_TO_PREPARE', self.codes(result))
        self.assertIn('PAID_WITH_NEW_BALANCE', self.codes(result))
        with store_context(self.first):
            self.assertEqual(ClosureReturnCase.objects.get(pk=case_id).status, 'PAID')

    def test_release_with_balance_blocks_and_true_zero_requires_original_reversal(self):
        account = self.funded_account(self.first, 910001, 100)
        self.enter_settlement(self.first)
        case_id = self.prepare(self.first, account, 100, 'release-readiness-prepare')
        self.release(self.first, case_id, 'release-readiness-release')
        with_balance = self.readiness(self.first)
        self.assertEqual(with_balance['status'], 'BLOCKED')
        self.assertIn('RELEASED_WITH_BALANCE', self.codes(with_balance))
        from engagement.funding_wallet import allocate_locked_order_funding
        from orders.tests.factories import make_order, make_service, make_user
        from stores.db import transaction
        with store_context(self.first):
            order = make_order(
                make_user(), make_service(price=100), customer_account=account, is_gift_snapshot=True,
            )
            wallet = Wallet.objects.get(account=account)
            with transaction.atomic():
                allocate_locked_order_funding(order, wallet)
                wallet.balance -= 100
                wallet.save(update_fields=['balance'])
        zero = self.readiness(self.first)
        self.assertEqual(zero['status'], 'CLEAR')
        self.assertEqual(zero['counts']['zero_balance'], 1)
        with store_context(self.first):
            case = ClosureReturnCase.objects.get(pk=case_id)
            Transaction.objects.filter(pk=case.release_transaction_id).update(reversal_of=None)
        damaged = self.readiness(self.first)
        self.assertEqual(damaged['status'], 'UNKNOWN')
        self.assertIn('RELEASE_TRANSACTION_INVALID', self.codes(damaged))

    def test_historical_migration_review_is_checked_even_when_new_cycle_hides_case(self):
        account = self.funded_account(self.first, 910001, 100)
        self.enter_settlement(self.first)
        case_id = self.paid(self.first, account, 100, 'hidden-migration')
        with store_context(self.first):
            ClosureReturnCase.objects.filter(pk=case_id).update(
                migration_review_required=True, migration_review_reason='FROZEN_LOT_CHANGED',
            )
            ClubAccount.objects.filter(pk=account.pk).update(business_cycle=2)
        result = self.readiness(self.first)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertFalse(result['complete'])
        self.assertIn('MIGRATION_REVIEW_REQUIRED', self.codes(result))

    def test_paid_transaction_proof_outbox_and_audit_anomalies_block(self):
        account = self.funded_account(self.first, 910001, 100)
        self.enter_settlement(self.first)
        case_id = self.paid(self.first, account, 100, 'paid-evidence')
        with store_context(self.first):
            case = ClosureReturnCase.objects.get(pk=case_id)
            payment_id = case.payment_transaction_id
            Transaction.objects.filter(pk=payment_id).update(amount=1)
        self.assertIn('PAYMENT_TRANSACTION_INVALID', self.codes(self.readiness(self.first)))
        with store_context(self.first):
            Transaction.objects.filter(pk=payment_id).update(amount=0)
            ClosureReturnCase.objects.filter(pk=case_id).update(proof_sha256='')
        self.assertIn('PAYMENT_PROOF_INVALID', self.codes(self.readiness(self.first)))
        with store_context(self.first):
            ClosureReturnCase.objects.filter(pk=case_id).update(
                proof_sha256=hashlib.sha256(b'readiness-payment-proof').hexdigest(),
            )
            event = ClosureReturnEvent.objects.get(case_id=case_id, kind='PAYMENT_RECORDED')
            outbox = ClosureReturnOutbox.objects.get(event=event)
            audit_id = outbox.audit_id
            ClosureReturnOutbox.objects.filter(pk=outbox.pk).update(status='PENDING', audit_id=None)
        self.assertIn('RETURN_OUTBOX_PENDING', self.codes(self.readiness(self.first)))
        with store_context(self.first):
            ClosureReturnOutbox.objects.filter(event=event).update(status='RECORDED', audit_id=audit_id)
        audit = PlatformAuditEvent.objects.get(pk=audit_id)
        original_detail = audit.detail
        PlatformAuditEvent.objects.filter(pk=audit.pk).update(detail={'status': 'tampered'})
        self.assertIn('RETURN_AUDIT_MISMATCH', self.codes(self.readiness(self.first)))
        PlatformAuditEvent.objects.filter(pk=audit.pk).update(detail=original_detail)
        self.assertEqual(self.readiness(self.first)['status'], 'CLEAR')

    def test_duplicate_external_reference_blocks_both_without_leaking_secrets(self):
        left = self.funded_account(self.first, 910001, 100, nickname='隐私老板甲')
        right = self.funded_account(self.first, 910002, 200, nickname='隐私老板乙')
        self.enter_settlement(self.first)
        self.paid(self.first, left, 100, 'duplicate-left', external='OFFLINE-SHARED-SECRET')
        self.paid(self.first, right, 200, 'duplicate-right', external='OFFLINE-SHARED-SECRET')
        result = self.readiness(self.first)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(self.codes(result).count('DUPLICATE_EXTERNAL_REFERENCE'), 2)
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('OFFLINE-SHARED-SECRET', rendered)
        self.assertNotIn('隐私老板甲', rendered)
        self.assertNotIn('隐私老板乙', rendered)

    def test_database_failure_is_unavailable_while_invalid_facts_are_unknown(self):
        self.account(self.first)
        with patch('wallet.closure_settlement_readiness.closure_return_list', side_effect=DatabaseError('read offline')):
            with self.assertRaises(DatabaseError):
                self.readiness(self.first)
        with patch('wallet.closure_settlement_readiness.closure_return_list', side_effect=TypeError('invalid facts')):
            result = self.readiness(self.first)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertFalse(result['complete'])
        self.assertIsNone(result['totals'])
        self.assertEqual(self.codes(result), ['READ_INCOMPLETE'])

    def test_repeated_reads_are_stable_serializable_and_write_free(self):
        account = self.account(self.first)
        with store_context(self.first):
            Wallet.objects.create(account=account, balance=0)
        first = self.readiness(self.first)
        with CaptureQueriesContext(connections['default']) as default_queries, CaptureQueriesContext(
                connections[self.first.database_alias]) as tenant_queries:
            second = self.readiness(self.first)
        self.assertEqual(first, second)
        json.dumps(second, ensure_ascii=False)
        writes = []
        for queries in (default_queries, tenant_queries):
            writes.extend(row['sql'] for row in queries.captured_queries
                          if row['sql'].lstrip().split(None, 1)[0].upper() in {
                              'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP',
                          })
        self.assertEqual(writes, [])
