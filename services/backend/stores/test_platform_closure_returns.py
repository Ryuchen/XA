"""SA02 boss-principal closure returns: scoped APIs, conservation and recovery."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, close_old_connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from engagement.models import MemberProfile, PointFundingLot, PointLedger
from wallet.models import ClosureReturnCase, ClosureReturnEvent, ClosureReturnOutbox, RechargeRecord, Transaction, Wallet

from .closure_clock import CLOCK_SOURCE, ControlClockEvidence
from .context import resolve_store, store_context
from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store
from .platform_auth import issue_tokens
from .tests_hosts import xinan_store


ROW_FIELDS = {
    'case_id', 'account_id', 'business_cycle', 'account_label', 'status',
    'wallet_units', 'principal_units', 'gift_units', 'compensation_units',
    'points_hundredths', 'frozen_units', 'refund_fen', 'paid_fen', 'version',
    'blocking_reasons', 'updated_at',
}


@override_settings(
    PLATFORM_IDENTITY_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'],
    STORE_HOST_BINDINGS={
        'sl.esports.club': 'shenglang', 'xa.esports.club': 'xinan',
        'testserver': 'shenglang',
    },
)
class PlatformClosureReturnTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.first.status = Store.Status.ACTIVE
        self.first.save(update_fields=['status', 'updated_at'])
        self.second = xinan_store()
        self.super = self.actor('return-super', 'SUPER_ADMIN', '*', '13800009001')
        self.second_super = self.actor('return-second-super', 'SUPER_ADMIN', self.second.slug, '13800009002')
        self.finance = self.actor('return-finance', 'FINANCE', '*', '13800009003')
        self.viewer = self.actor('return-viewer', 'ORDER_VIEWER', '*', '13800009004')
        self.client = self.platform_client(self.super)
        self.second_client = self.platform_client(self.second_super)
        self.finance_client = self.platform_client(self.finance)
        self.viewer_client = self.platform_client(self.viewer)
        self.accounts = {}
        self.wallets = {}
        self.lots = {}
        self.recharge_sequence = 0
        for store, amount in ((self.first, 12500), (self.second, 7700)):
            with store_context(store):
                account = self.boss(807101, 'return-principal', store.slug + '-principal')
                wallet = Wallet.objects.create(account=account, balance=0)
                _record, lot = self.verified_recharge(store, account, wallet, amount)
                self.points(account, 321)
                self.accounts[store.slug] = account
                self.wallets[store.slug] = wallet
                self.lots[store.slug] = lot
        with store_context(self.first):
            self.gift = self.boss(807102, 'return-gift', '赠送待核')
            gift_wallet = Wallet.objects.create(account=self.gift, balance=0)
            self.verified_recharge(self.first, self.gift, gift_wallet, 600, gift=400)
            self.points(self.gift, 0)
            self.unknown = self.boss(807103, 'return-unknown', '混合快照待核')
            unknown_wallet = Wallet.objects.create(account=self.unknown, balance=0)
            _record, unknown_lot = self.verified_recharge(self.first, self.unknown, unknown_wallet, 1000)
            PointFundingLot.objects.filter(pk=unknown_lot.pk).update(gift_remaining=None)
            self.points(self.unknown, 0)
            self.compensation = self.boss(807104, 'return-compensation', '赔付待核')
            Wallet.objects.create(account=self.compensation, balance=900)
            PointFundingLot.objects.create(
                account=self.compensation, source_key='closure:compensation', remaining=900,
                kind='COMPENSATION',
            )
            self.points(self.compensation, 0)
            self.mismatch = self.boss(807105, 'return-mismatch', '钱包批次不一致')
            Wallet.objects.create(account=self.mismatch, balance=500)
            PointFundingLot.objects.create(
                account=self.mismatch, source_key='closure:mismatch', remaining=400, kind='PREPAID',
            )
            self.points(self.mismatch, 0)
            self.releasable = self.boss(807106, 'return-releasable', '可解除本金')
            releasable_wallet = Wallet.objects.create(account=self.releasable, balance=0)
            self.verified_recharge(self.first, self.releasable, releasable_wallet, 2300)
            self.points(self.releasable, 0)
            self.retirement_operator = ClubAccount.objects.create_account(
                id=807199, username='return-retirement-operator', account_type='STAFF', account_no='XA807199',
            )
        self.now = datetime(2026, 11, 1, 4, 0, tzinfo=datetime_timezone.utc)
        patcher = patch('stores.closure.read_control_clock', side_effect=lambda: ControlClockEvidence(
            self.now, CLOCK_SOURCE, 'sqlite-test:default',
        ))
        patcher.start()
        self.addCleanup(patcher.stop)

    def actor(self, username, role, scope, phone):
        actor = PlatformAccount.objects.create(
            username=username, nickname=username, phone=phone, is_phone_verified=True,
        )
        PlatformRoleGrant.objects.create(account=actor, role=role, scope_slug=scope)
        return actor

    def platform_client(self, actor, host='admin.esports.club'):
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def boss(self, pk, username, nickname):
        return ClubAccount.objects.create_account(
            id=pk, username=username, account_type='BOSS', account_no=f'XA{pk}', nickname=nickname,
        )

    def points(self, account, amount):
        MemberProfile.objects.create(account=account, points_balance=amount)
        if amount:
            PointLedger.objects.create(
                account=account, event_key=f'closure-return:points:{account.pk}', kind='TEST',
                amount=amount, balance_after=amount, remark='闭店返还测试积分',
            )

    def verified_recharge(self, store, account, wallet, amount, *, gift=0):
        self.recharge_sequence += 1
        trade_no = f'{store.slug}-closure-cash-{account.pk}-{self.recharge_sequence}'
        record = RechargeRecord.objects.create(
            account=account, account_business_cycle=account.business_cycle,
            amount=amount, gift_amount=gift, trade_no=trade_no,
            receipt_key=hashlib.sha256(trade_no.casefold().encode()).hexdigest(),
            proof_image='recharges/closure-return-proof.jpg',
            bonus_snapshot={
                'amount': amount, 'gift_amount': gift, 'remaining': amount,
                'matches': [], 'version': '0' * 64, 'points_rule': 'VIP_AT_PAYMENT',
            },
        )
        before = wallet.balance
        recharge_tx = Transaction.objects.create(
            wallet=wallet, tx_type='TOPUP', amount=amount, status='SUCCESS',
            balance_before=before, balance_after=before + amount,
            remark='闭店返还真实充值夹具',
        )
        gift_tx = None
        if gift:
            gift_tx = Transaction.objects.create(
                wallet=wallet, tx_type='GIFT', amount=gift, status='SUCCESS',
                balance_before=before + amount, balance_after=before + amount + gift,
                remark='闭店返还真实赠送夹具',
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
        return record, lot

    def url(self, store, suffix=''):
        return f'/api/admin/platform/store-management/{store.pk}/closure/returns{suffix}'

    def enter_settlement(self, store):
        started = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure',
            {'reason_category': 'OPERATOR_REQUEST', 'reason': '闭店返还测试', 'client_request_id': f'closure-{store.slug}'},
            format='json',
        )
        self.assertEqual(started.status_code, 201, started.content)
        self.now += timedelta(days=31)
        confirmed = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure/confirm-expiry',
            {'reason': '公示已满三十天', 'client_request_id': f'expiry-{store.slug}'}, format='json',
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.content)

    def prepare(self, store, account=None, *, client=None, request_id='return-prepare-1', **overrides):
        account = account or self.accounts[store.slug]
        expected = 12500 if store == self.first else 7700
        if account == self.releasable:
            expected = 2300
        body = {
            'account_id': account.pk, 'business_cycle': account.business_cycle,
            'expected_wallet_units': expected, 'expected_principal_units': expected,
            'client_request_id': request_id,
        }
        body.update(overrides)
        return (client or self.client).post(self.url(store, '/prepare'), body, format='json')

    def payment(self, store, case_id, *, client=None, request_id='return-payment-1', **overrides):
        body = {
            'amount_fen': 12500, 'external_reference': 'OFFLINE-RETURN-0001',
            'proof_sha256': hashlib.sha256(b'closure-return-proof').hexdigest(),
            'occurred_at': '2026-11-25T02:00:00+00:00', 'client_request_id': request_id,
        }
        body.update(overrides)
        return (client or self.client).post(self.url(store, f'/{case_id}/record-payment'), body, format='json')

    def release(self, store, case_id, *, client=None, request_id='return-release-1', **overrides):
        body = {'reason': '核实尚未线下实付，解除本次冻结', 'client_request_id': request_id}
        body.update(overrides)
        return (client or self.client).post(self.url(store, f'/{case_id}/release'), body, format='json')

    def listed(self, store, account_id, *, client=None):
        response = (client or self.finance_client).get(self.url(store), {'page': 1, 'page_size': 100})
        self.assertEqual(response.status_code, 200, response.content)
        return response, next(row for row in response.data['data']['list'] if row['account_id'] == account_id)

    def test_permissions_scope_host_status_and_no_store(self):
        self.assertEqual(APIClient(HTTP_HOST='admin.esports.club').get(self.url(self.first)).status_code, 401)
        self.assertEqual(self.finance_client.get(self.url(self.first)).status_code, 409)
        self.enter_settlement(self.first)
        self.assertEqual(self.viewer_client.get(self.url(self.first)).status_code, 403)
        self.assertEqual(self.second_client.get(self.url(self.first)).status_code, 403)
        self.assertEqual(self.prepare(self.first, client=self.finance_client).status_code, 403)
        self.assertEqual(self.prepare(self.first, client=self.second_client).status_code, 403)
        wrong_host = self.platform_client(self.super, host='sl.esports.club')
        self.assertEqual(wrong_host.get(self.url(self.first)).status_code, 403)
        response = self.finance_client.get(self.url(self.first))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(set(response.data['data']), {'list', 'total', 'page', 'page_size', 'has_more', 'summary'})

    def test_two_stores_same_numeric_account_and_request_id_are_independent(self):
        self.enter_settlement(self.first)
        self.enter_settlement(self.second)
        self.assertEqual(self.accounts[self.first.slug].pk, self.accounts[self.second.slug].pk)
        first = self.prepare(self.first, request_id='shared-return-request')
        second = self.prepare(self.second, request_id='shared-return-request')
        self.assertEqual((first.status_code, second.status_code), (201, 201), (first.content, second.content))
        self.assertNotEqual(first.data['data']['case_id'], second.data['data']['case_id'])
        self.assertEqual((first.data['data']['wallet_balance'], first.data['data']['wallet_frozen_units']), (0, 12500))
        self.assertEqual((second.data['data']['wallet_balance'], second.data['data']['wallet_frozen_units']), (0, 7700))
        with store_context(self.first):
            self.assertEqual((Wallet.objects.get(account=self.accounts[self.first.slug]).balance,
                              Wallet.objects.get(account=self.accounts[self.first.slug]).frozen_amount), (0, 12500))
            self.assertEqual(PointFundingLot.objects.get(pk=self.lots[self.first.slug].pk).remaining, 0)
        with store_context(self.second):
            self.assertEqual((Wallet.objects.get(account=self.accounts[self.second.slug]).balance,
                              Wallet.objects.get(account=self.accounts[self.second.slug]).frozen_amount), (0, 7700))
            self.assertEqual(ClosureReturnCase.objects.count(), 1)

    def test_inventory_is_fail_closed_and_summary_never_turns_unknown_into_zero(self):
        self.enter_settlement(self.first)
        self.enter_settlement(self.second)
        second_response, ready = self.listed(self.second, 807101)
        self.assertEqual(set(ready), ROW_FIELDS)
        self.assertEqual(ready['status'], 'READY_TO_PREPARE')
        self.assertEqual((ready['wallet_units'], ready['principal_units'], ready['refund_fen']), (7700, 7700, 7700))
        self.assertEqual((ready['gift_units'], ready['compensation_units'], ready['points_hundredths']), (0, 0, 321))
        self.assertTrue(second_response.data['data']['summary']['complete'])
        self.assertEqual(second_response.data['data']['summary']['totals']['principal_units'], 7700)
        response = self.finance_client.get(self.url(self.first), {'page_size': 100})
        data = response.data['data']
        rows = {row['account_id']: row for row in data['list']}
        self.assertFalse(data['summary']['complete'])
        self.assertIsNone(data['summary']['totals'])
        self.assertEqual(rows[self.gift.pk]['status'], 'REVIEW_REQUIRED')
        self.assertEqual((rows[self.gift.pk]['principal_units'], rows[self.gift.pk]['gift_units']), (600, 400))
        self.assertIsNone(rows[self.gift.pk]['refund_fen'])
        self.assertEqual(rows[self.unknown.pk]['status'], 'REVIEW_REQUIRED')
        self.assertIsNone(rows[self.unknown.pk]['principal_units'])
        self.assertIsNone(rows[self.unknown.pk]['gift_units'])
        self.assertIn('UNKNOWN_RECHARGE_SPLIT', rows[self.unknown.pk]['blocking_reasons'])
        self.assertEqual(rows[self.compensation.pk]['compensation_units'], 900)
        self.assertIsNone(rows[self.compensation.pk]['refund_fen'])
        self.assertEqual(rows[self.mismatch.pk]['status'], 'REVIEW_REQUIRED')
        for field in ('principal_units', 'gift_units', 'compensation_units', 'refund_fen'):
            self.assertIsNone(rows[self.mismatch.pk][field])
        self.assertIn('WALLET_LOT_MISMATCH', rows[self.mismatch.pk]['blocking_reasons'])

    def test_prepare_validates_expected_amount_cycle_and_request_fingerprint(self):
        self.enter_settlement(self.first)
        wrong = self.prepare(self.first, expected_wallet_units=12499)
        self.assertEqual(wrong.status_code, 409)
        wrong_cycle = self.prepare(self.first, request_id='return-cycle-wrong', business_cycle=2)
        self.assertEqual(wrong_cycle.status_code, 409)
        with store_context(self.first):
            self.assertFalse(ClosureReturnCase.objects.exists())
            self.assertEqual(Wallet.objects.get(account=self.accounts[self.first.slug]).balance, 12500)
        created = self.prepare(self.first)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertFalse(created.data['data']['replayed'])
        self.assertEqual(set(created.data['data']), ROW_FIELDS | {
            'replayed', 'wallet_balance', 'wallet_frozen_units',
        })
        replay = self.prepare(self.first)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.data['data']['replayed'])
        self.assertEqual(replay.data['data']['case_id'], created.data['data']['case_id'])
        conflict = self.prepare(self.first, expected_principal_units=12499)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['errorCode'], 'REQUEST_ID_CONFLICT')
        other = self.prepare(self.first, request_id='return-prepare-other')
        self.assertEqual(other.status_code, 409)
        with store_context(self.first):
            self.assertEqual(ClosureReturnCase.objects.count(), 1)
            self.assertEqual(ClosureReturnEvent.objects.count(), 1)
            self.assertEqual(Transaction.objects.filter(tx_type='CLOSURE_FREEZE').count(), 1)
            self.assertEqual(ClosureReturnOutbox.objects.count(), 1)

    def test_nonprincipal_unknown_and_mismatch_can_never_prepare(self):
        self.enter_settlement(self.first)
        for account, amount in ((self.gift, 1000), (self.unknown, 1000), (self.compensation, 900), (self.mismatch, 500)):
            with self.subTest(account=account.pk):
                response = self.prepare(
                    self.first, account, request_id=f'review-prepare-{account.pk}',
                    expected_wallet_units=amount, expected_principal_units=amount,
                )
                self.assertEqual(response.status_code, 409, response.content)
                with store_context(self.first):
                    self.assertFalse(ClosureReturnCase.objects.filter(account=account).exists())
                    self.assertEqual(Wallet.objects.get(account=account).balance, amount)
        with store_context(self.first):
            self.assertFalse(Transaction.objects.filter(tx_type__startswith='CLOSURE_').exists())

    def test_full_payment_conserves_wallet_lots_and_immutable_transactions(self):
        self.enter_settlement(self.first)
        case_id = self.prepare(self.first).data['data']['case_id']
        with store_context(self.first):
            case = ClosureReturnCase.objects.get(pk=case_id)
            original = case.freeze_transaction
            original_facts = (original.status, original.amount, original.balance_before, original.balance_after, original.remark)
        partial = self.payment(self.first, case_id, request_id='return-partial', amount_fen=12499)
        self.assertEqual(partial.status_code, 409)
        paid = self.payment(self.first, case_id)
        self.assertEqual(paid.status_code, 200, paid.content)
        self.assertEqual(paid.data['data']['channel_status'], 'NOT_REQUESTED')
        self.assertEqual((paid.data['data']['status'], paid.data['data']['paid_fen']), ('PAID', 12500))
        self.assertEqual((paid.data['data']['wallet_balance'], paid.data['data']['wallet_frozen_units']), (0, 0))
        replay = self.payment(self.first, case_id)
        self.assertTrue(replay.data['data']['replayed'])
        conflict = self.payment(self.first, case_id, proof_sha256=hashlib.sha256(b'different').hexdigest())
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['errorCode'], 'REQUEST_ID_CONFLICT')
        self.assertEqual(self.release(self.first, case_id).status_code, 409)
        with store_context(self.first):
            case = ClosureReturnCase.objects.select_related('freeze_transaction', 'payment_transaction').get(pk=case_id)
            self.assertEqual(case.status, 'PAID')
            self.assertEqual(case.paid_fen, case.principal_units)
            self.assertEqual((case.freeze_transaction.status, case.freeze_transaction.amount,
                              case.freeze_transaction.balance_before, case.freeze_transaction.balance_after,
                              case.freeze_transaction.remark), original_facts)
            self.assertEqual(case.payment_transaction.amount, 0)
            self.assertEqual(PointFundingLot.objects.get(pk=self.lots[self.first.slug].pk).remaining, 0)
            wallet = Wallet.objects.get(account=self.accounts[self.first.slug])
            self.assertEqual((wallet.balance, wallet.frozen_amount), (0, 0))
            self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type__startswith='CLOSURE_').count(), 2)
        detail = self.finance_client.get(self.url(self.first, f'/{case_id}'))
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail['Cache-Control'], 'no-store')
        self.assertEqual(detail.data['data']['channel_status'], 'NOT_REQUESTED')
        self.assertEqual(len(detail.data['data']['events']), 2)
        rendered = str(detail.data)
        self.assertNotIn('OFFLINE-RETURN-0001', rendered)
        self.assertNotIn('closure-return-proof', rendered)

    def test_release_restores_exact_freeze_and_can_prepare_a_new_case(self):
        self.enter_settlement(self.first)
        created = self.prepare(self.first, self.releasable)
        case_id = created.data['data']['case_id']
        with store_context(self.first):
            case = ClosureReturnCase.objects.get(pk=case_id)
            freeze_id = case.freeze_transaction_id
            self.assertEqual(PointFundingLot.objects.get(account=self.releasable).remaining, 0)
        released = self.release(self.first, case_id)
        self.assertEqual(released.status_code, 200, released.content)
        self.assertEqual(released.data['data']['status'], 'RELEASED')
        self.assertEqual((released.data['data']['wallet_balance'], released.data['data']['wallet_frozen_units']), (2300, 0))
        replay = self.release(self.first, case_id)
        self.assertTrue(replay.data['data']['replayed'])
        self.assertEqual(self.release(self.first, case_id, reason='同号异内容').data['errorCode'], 'REQUEST_ID_CONFLICT')
        self.assertEqual(self.payment(self.first, case_id, amount_fen=2300).status_code, 409)
        with store_context(self.first):
            case = ClosureReturnCase.objects.select_related('release_transaction').get(pk=case_id)
            wallet = Wallet.objects.get(account=self.releasable)
            self.assertEqual((case.status, wallet.balance, wallet.frozen_amount), ('RELEASED', 2300, 0))
            self.assertEqual(case.release_transaction.amount, 2300)
            self.assertEqual(case.release_transaction.reversal_of_id, freeze_id)
            self.assertEqual(PointFundingLot.objects.get(account=self.releasable).remaining, 2300)
            self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type__startswith='CLOSURE_').count(), 2)
        again = self.prepare(self.first, self.releasable, request_id='return-prepare-after-release')
        self.assertEqual(again.status_code, 201, again.content)
        self.assertNotEqual(again.data['data']['case_id'], case_id)

    def test_changed_original_lot_identity_blocks_payment_and_release_without_moving_money(self):
        self.enter_settlement(self.first)
        for action in ('payment', 'release'):
            with self.subTest(action=action):
                account = self.accounts[self.first.slug] if action == 'payment' else self.releasable
                case_id = self.prepare(self.first, account, request_id=f'lot-guard-prepare-{action}').data['data']['case_id']
                with store_context(self.first):
                    lot = PointFundingLot.objects.get(account=account)
                    PointFundingLot.objects.filter(pk=lot.pk).update(source_key=f'changed-source-identity-{action}')
                if action == 'payment':
                    response = self.payment(self.first, case_id)
                else:
                    response = self.release(self.first, case_id)
                self.assertEqual(response.status_code, 409, response.content)
                with store_context(self.first):
                    wallet = Wallet.objects.get(account=account)
                    expected = 12500 if action == 'payment' else 2300
                    self.assertEqual((wallet.balance, wallet.frozen_amount), (0, expected))
                    self.assertEqual(ClosureReturnCase.objects.get(pk=case_id).status, 'FROZEN')
                    self.assertEqual(ClosureReturnEvent.objects.filter(case_id=case_id).count(), 1)

    def test_outbox_failure_rolls_back_every_store_write(self):
        self.enter_settlement(self.first)
        with patch('wallet.closure_returns.ClosureReturnOutbox.objects.create', side_effect=DatabaseError('outbox offline')):
            response = self.prepare(self.first)
        self.assertEqual(response.status_code, 503)
        with store_context(self.first):
            self.assertFalse(ClosureReturnCase.objects.exists())
            self.assertFalse(ClosureReturnEvent.objects.exists())
            self.assertFalse(Transaction.objects.filter(tx_type__startswith='CLOSURE_').exists())
            wallet = Wallet.objects.get(account=self.accounts[self.first.slug])
            self.assertEqual((wallet.balance, wallet.frozen_amount), (12500, 0))
        self.assertEqual(self.prepare(self.first).status_code, 201)

    def test_audit_failure_recovers_by_original_request_without_double_money(self):
        self.enter_settlement(self.first)
        with patch('wallet.closure_return_audit.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            uncertain = self.prepare(self.first)
        self.assertEqual(uncertain.status_code, 503, uncertain.content)
        with store_context(self.first):
            case = ClosureReturnCase.objects.get()
            event = ClosureReturnEvent.objects.get()
            self.assertEqual((case.status, event.kind, event.outbox.status), ('FROZEN', 'PREPARED', 'PENDING'))
            self.assertEqual(Wallet.objects.get(account=self.accounts[self.first.slug]).frozen_amount, 12500)
            case_id = case.pk
        recovered = self.prepare(self.first)
        self.assertEqual(recovered.status_code, 200, recovered.content)
        self.assertTrue(recovered.data['data']['replayed'])
        with store_context(self.first):
            self.assertEqual(ClosureReturnCase.objects.count(), 1)
            self.assertEqual(ClosureReturnEvent.objects.count(), 1)
            self.assertEqual(Transaction.objects.filter(tx_type='CLOSURE_FREEZE').count(), 1)
            self.assertEqual(ClosureReturnOutbox.objects.get().status, 'RECORDED')
        audits = PlatformAuditEvent.objects.filter(
            action='closure_return.prepared', target_store_slug=self.first.slug,
        )
        self.assertEqual(audits.count(), 1)
        with patch('wallet.closure_return_audit.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            payment_uncertain = self.payment(self.first, case_id)
        self.assertEqual(payment_uncertain.status_code, 503)
        with store_context(self.first):
            self.assertEqual(ClosureReturnCase.objects.get(pk=case_id).status, 'PAID')
            self.assertEqual(Wallet.objects.get(account=self.accounts[self.first.slug]).frozen_amount, 0)
        self.assertTrue(self.payment(self.first, case_id).data['data']['replayed'])
        self.assertEqual(PlatformAuditEvent.objects.filter(
            action='closure_return.payment_recorded', target_store_slug=self.first.slug,
        ).count(), 1)

    def test_release_audit_failure_also_recovers_without_second_release(self):
        self.enter_settlement(self.first)
        case_id = self.prepare(self.first, self.releasable).data['data']['case_id']
        with patch('wallet.closure_return_audit.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            response = self.release(self.first, case_id)
        self.assertEqual(response.status_code, 503)
        with store_context(self.first):
            self.assertEqual(Wallet.objects.get(account=self.releasable).balance, 2300)
            self.assertEqual(ClosureReturnEvent.objects.filter(case_id=case_id).count(), 2)
        self.assertTrue(self.release(self.first, case_id).data['data']['replayed'])
        with store_context(self.first):
            wallet = Wallet.objects.get(account=self.releasable)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (2300, 0))
            self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='CLOSURE_RELEASE').count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(
            action='closure_return.released', target_store_slug=self.first.slug,
        ).count(), 1)

    def test_reads_do_not_write_and_events_are_append_only(self):
        self.enter_settlement(self.first)
        case_id = self.prepare(self.first).data['data']['case_id']
        with store_context(self.first):
            event = ClosureReturnEvent.objects.get(case_id=case_id)
            original = event.detail.copy()
        before_audits = PlatformAuditEvent.objects.count()
        self.assertEqual(self.finance_client.get(self.url(self.first)).status_code, 200)
        self.assertEqual(self.finance_client.get(self.url(self.first, f'/{case_id}')).status_code, 200)
        self.assertEqual(PlatformAuditEvent.objects.count(), before_audits)
        with store_context(self.first):
            self.assertEqual(ClosureReturnEvent.objects.count(), 1)
            self.assertEqual(ClosureReturnOutbox.objects.count(), 1)
            event.refresh_from_db()
            self.assertEqual(event.detail, original)
            event.detail = {'tampered': True}
            with self.assertRaises(DjangoValidationError):
                event.save()
            with self.assertRaises(DjangoValidationError):
                event.delete()
            with self.assertRaises(DjangoValidationError):
                ClosureReturnEvent.objects.filter(pk=event.pk).update(detail={'tampered': True})
            with self.assertRaises(DjangoValidationError):
                ClosureReturnEvent.objects.filter(pk=event.pk).delete()

    def test_strict_payload_and_sensitive_reference_validation(self):
        self.enter_settlement(self.first)
        extra = self.prepare(self.first, proof='not-in-contract')
        self.assertEqual(extra.status_code, 400)
        case_id = self.prepare(self.first).data['data']['case_id']
        bank_reference = self.payment(self.first, case_id, external_reference='6222 0202 0011 2233 456')
        self.assertEqual(bank_reference.status_code, 400)
        bad_hash = self.payment(self.first, case_id, proof_sha256='not-a-sha')
        self.assertEqual(bad_hash.status_code, 400)
        naive_time = self.payment(self.first, case_id, occurred_at='2026-11-25T10:00:00')
        self.assertEqual(naive_time.status_code, 400)
        with store_context(self.first):
            self.assertEqual(ClosureReturnCase.objects.get(pk=case_id).status, 'FROZEN')
            self.assertEqual(ClosureReturnEvent.objects.filter(case_id=case_id).count(), 1)


    def test_prepare_competition_commits_only_one_freeze(self):
        self.enter_settlement(self.first)
        barrier = Barrier(2)
        clients = [self.platform_client(self.super), self.platform_client(self.super)]

        def invoke(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.prepare(
                    self.first, client=clients[index], request_id=f'prepare-race-{index}',
                ).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke, index) for index in range(2)]
            statuses = [future.result(timeout=30) for future in futures]
        self.assertTrue(all(status in (201, 409, 503) for status in statuses), statuses)
        with store_context(self.first):
            committed = ClosureReturnCase.objects.filter(account=self.accounts[self.first.slug]).exists()
        if not committed:
            # SQLite may reject both simultaneous writers with database-locked.
            # Recovery with one original key must then commit exactly one freeze.
            recovered = self.prepare(self.first, request_id='prepare-race-0')
            self.assertEqual(recovered.status_code, 201, recovered.content)
        with store_context(self.first):
            account = self.accounts[self.first.slug]
            wallet = Wallet.objects.get(account=account)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (0, 12500))
            self.assertEqual(ClosureReturnCase.objects.filter(account=account, status='FROZEN').count(), 1)
            self.assertEqual(ClosureReturnEvent.objects.filter(kind='PREPARED').count(), 1)
            self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='CLOSURE_FREEZE').count(), 1)
            self.assertEqual(PointFundingLot.objects.get(pk=self.lots[self.first.slug].pk).remaining, 0)

    def test_payment_and_release_competition_has_exactly_one_terminal_fact(self):
        self.enter_settlement(self.first)
        case_id = self.prepare(self.first).data['data']['case_id']
        barrier = Barrier(2)
        clients = [self.platform_client(self.super), self.platform_client(self.super)]

        def invoke(kind):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if kind == 'payment':
                    response = self.payment(
                        self.first, case_id, client=clients[0], request_id='terminal-race-payment',
                    )
                else:
                    response = self.release(
                        self.first, case_id, client=clients[1], request_id='terminal-race-release',
                    )
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke, kind) for kind in ('payment', 'release')]
            statuses = [future.result(timeout=30) for future in futures]
        self.assertTrue(all(status in (200, 409, 503) for status in statuses), statuses)
        with store_context(self.first):
            unresolved = ClosureReturnCase.objects.get(pk=case_id).status == 'FROZEN'
        if unresolved:
            recovered = self.payment(self.first, case_id, request_id='terminal-race-payment')
            self.assertEqual(recovered.status_code, 200, recovered.content)
        with store_context(self.first):
            case = ClosureReturnCase.objects.get(pk=case_id)
            account = self.accounts[self.first.slug]
            wallet = Wallet.objects.get(account=account)
            lot = PointFundingLot.objects.get(pk=self.lots[self.first.slug].pk)
            self.assertIn(case.status, ('PAID', 'RELEASED'))
            if case.status == 'PAID':
                self.assertEqual((wallet.balance, wallet.frozen_amount, lot.remaining), (0, 0, 0))
                self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='CLOSURE_PAYMENT').count(), 1)
                self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='CLOSURE_RELEASE').count(), 0)
                self.assertEqual(ClosureReturnEvent.objects.filter(case=case, kind='PAYMENT_RECORDED').count(), 1)
            else:
                self.assertEqual((wallet.balance, wallet.frozen_amount, lot.remaining), (12500, 0, 12500))
                self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='CLOSURE_PAYMENT').count(), 0)
                self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='CLOSURE_RELEASE').count(), 1)
                self.assertEqual(ClosureReturnEvent.objects.filter(case=case, kind='RELEASED').count(), 1)
            self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type__startswith='CLOSURE_').count(), 2)
            self.assertEqual(ClosureReturnEvent.objects.filter(case=case).count(), 2)


    def test_cycle_change_after_freeze_blocks_payment_and_release(self):
        self.enter_settlement(self.first)
        paid_case = self.prepare(self.first, request_id='cycle-payment-prepare').data['data']['case_id']
        released_case = self.prepare(
            self.first, self.releasable, request_id='cycle-release-prepare',
        ).data['data']['case_id']
        with store_context(self.first):
            for account in (self.accounts[self.first.slug], self.releasable):
                ClubAccount.objects.filter(pk=account.pk).update(business_cycle=account.business_cycle + 1)
        payment = self.payment(self.first, paid_case, request_id='cycle-payment-blocked')
        release = self.release(self.first, released_case, request_id='cycle-release-blocked')
        self.assertEqual((payment.status_code, release.status_code), (409, 409))
        with store_context(self.first):
            principal_wallet = Wallet.objects.get(account_id=self.accounts[self.first.slug].pk)
            released_wallet = Wallet.objects.get(account_id=self.releasable.pk)
            self.assertEqual((principal_wallet.balance, principal_wallet.frozen_amount), (0, 12500))
            self.assertEqual((released_wallet.balance, released_wallet.frozen_amount), (0, 2300))
            self.assertEqual(ClosureReturnCase.objects.get(pk=paid_case).status, 'FROZEN')
            self.assertEqual(ClosureReturnCase.objects.get(pk=released_case).status, 'FROZEN')
            self.assertEqual(ClosureReturnEvent.objects.filter(case_id=paid_case).count(), 1)
            self.assertEqual(ClosureReturnEvent.objects.filter(case_id=released_case).count(), 1)

    def test_audit_committed_but_receipt_update_failed_recovers_without_duplicate(self):
        self.enter_settlement(self.first)
        with patch('stores.platform_closure_returns.mark_recorded', side_effect=DatabaseError('receipt offline')):
            uncertain = self.prepare(self.first)
        self.assertEqual(uncertain.status_code, 503)
        with store_context(self.first):
            case = ClosureReturnCase.objects.get()
            self.assertEqual(ClosureReturnOutbox.objects.get().status, 'PENDING')
            case_id = case.pk
        self.assertEqual(PlatformAuditEvent.objects.filter(
            action='closure_return.prepared', target_store_slug=self.first.slug,
        ).count(), 1)
        recovered = self.prepare(self.first)
        self.assertEqual(recovered.status_code, 200, recovered.content)
        self.assertTrue(recovered.data['data']['replayed'])
        self.assertEqual(PlatformAuditEvent.objects.filter(
            action='closure_return.prepared', target_store_slug=self.first.slug,
        ).count(), 1)
        with store_context(self.first):
            self.assertEqual(ClosureReturnCase.objects.filter(pk=case_id).count(), 1)
            self.assertEqual(ClosureReturnOutbox.objects.get().status, 'RECORDED')
            self.assertEqual(Transaction.objects.filter(tx_type='CLOSURE_FREEZE').count(), 1)

    def test_paginated_rows_still_use_store_wide_counts_and_unknown_totals(self):
        self.enter_settlement(self.first)
        response = self.finance_client.get(self.url(self.first), {'page': 1, 'page_size': 1})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.data['data']
        self.assertEqual(len(data['list']), 1)
        self.assertEqual(data['total'], 6)
        self.assertTrue(data['has_more'])
        self.assertEqual(sum(data['summary']['counts'].values()), 6)
        self.assertFalse(data['summary']['complete'])
        self.assertIsNone(data['summary']['totals'])


    def test_isolated_second_store_recovers_audit_without_control_transaction(self):
        from django.db import connections

        self.enter_settlement(self.second)
        from wallet import closure_returns as return_service

        original = return_service._revalidate_control
        observations = []

        def observe(actor, store):
            observations.append(connections['default'].in_atomic_block)
            self.assertFalse(connections['default'].in_atomic_block)
            return original(actor, store)

        with patch('wallet.closure_returns._revalidate_control', side_effect=observe):
            with patch('wallet.closure_return_audit.PlatformAuditEvent.objects.create',
                       side_effect=DatabaseError('cross-db audit offline')):
                uncertain = self.prepare(self.second, request_id='isolated-audit-recovery')
            self.assertEqual(uncertain.status_code, 503)
            recovered = self.prepare(self.second, request_id='isolated-audit-recovery')
        self.assertEqual(recovered.status_code, 200, recovered.content)
        self.assertTrue(recovered.data['data']['replayed'])
        self.assertEqual(observations, [False, False])
        with store_context(self.second):
            account = self.accounts[self.second.slug]
            wallet = Wallet.objects.get(account=account)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (0, 7700))
            self.assertEqual(ClosureReturnCase.objects.count(), 1)
            self.assertEqual(ClosureReturnEvent.objects.count(), 1)
            self.assertEqual(Transaction.objects.filter(tx_type='CLOSURE_FREEZE').count(), 1)
            self.assertEqual(ClosureReturnOutbox.objects.get().status, 'RECORDED')
        self.assertEqual(PlatformAuditEvent.objects.filter(
            action='closure_return.prepared', target_store_slug=self.second.slug,
        ).count(), 1)


    def _freeze_refund_and_existing_gift(self, request_id):
        """12500 frozen; a later 400 refund and 300 gift use only new funds."""
        from engagement.funding_wallet import allocate_locked_order_funding, refund_locked_order_funding
        from orders.tests.factories import make_order, make_service, make_user
        from stores.db import transaction

        account = self.accounts[self.first.slug]
        with store_context(self.first):
            user = make_user()
            original_order = make_order(
                user, make_service(price=400), customer_account=account,
            )
            wallet = Wallet.objects.get(account=account)
            with transaction.atomic():
                allocate_locked_order_funding(original_order, wallet)
                wallet.balance -= 400
                wallet.save(update_fields=['balance'])
            self.verified_recharge(self.first, account, wallet, 400)
            wallet.refresh_from_db()
            self.assertEqual(wallet.balance, 12500)
        self.enter_settlement(self.first)
        created = self.prepare(self.first, request_id=request_id)
        self.assertEqual(created.status_code, 201, created.content)
        case_id = created.data['data']['case_id']
        with store_context(self.first):
            # The original available lots moved into independent frozen shares.
            self.assertEqual(sum(PointFundingLot.objects.filter(
                account=account).values_list('remaining', flat=True)), 0)
            self.assertEqual(sum(row.principal_units for row in ClosureReturnCase.objects.get(
                pk=case_id).lots.all()), 12500)
            wallet = Wallet.objects.get(account=account)
            with transaction.atomic():
                refund_locked_order_funding(original_order, wallet, 400)
                wallet.balance += 400
                wallet.save(update_fields=['balance'])
            wallet.refresh_from_db()
            gift_order = make_order(
                user, make_service(price=300), customer_account=account, is_gift_snapshot=True,
            )
            with transaction.atomic():
                allocate_locked_order_funding(gift_order, wallet)
                wallet.balance -= 300
                wallet.save(update_fields=['balance'])
            wallet.refresh_from_db()
            current_lots = sum(PointFundingLot.objects.filter(
                account=account).values_list('remaining', flat=True))
            case = ClosureReturnCase.objects.get(pk=case_id)
            self.assertEqual((wallet.balance, wallet.frozen_amount, current_lots), (100, 12500, 100))
            self.assertEqual(wallet.balance + wallet.frozen_amount,
                             current_lots + case.principal_units)
            self.assertEqual(sum(row.refunded_amount for row in original_order.funding_allocations.all()), 400)
            self.assertEqual(sum(row.amount for row in gift_order.funding_allocations.all()), 300)
        return case_id

    def test_frozen_shares_survive_original_refund_and_existing_gift_before_payment(self):
        case_id = self._freeze_refund_and_existing_gift('independent-share-payment')
        _response, row = self.listed(self.first, self.accounts[self.first.slug].pk)
        self.assertEqual(row['status'], 'FROZEN')
        self.assertEqual((row['wallet_units'], row['principal_units'], row['frozen_units']), (100, 12600, 12500))
        self.assertIn('NEW_AVAILABLE_BALANCE', row['blocking_reasons'])
        detail = self.finance_client.get(self.url(self.first, f'/{case_id}'))
        self.assertEqual(detail.data['data']['status'], 'FROZEN')
        self.assertEqual(detail.data['data']['wallet_units'], 100)
        paid = self.payment(self.first, case_id)
        self.assertEqual(paid.status_code, 200, paid.content)
        with store_context(self.first):
            account = self.accounts[self.first.slug]
            wallet = Wallet.objects.get(account=account)
            case = ClosureReturnCase.objects.get(pk=case_id)
            current_lots = sum(PointFundingLot.objects.filter(
                account=account).values_list('remaining', flat=True))
            self.assertEqual(case.status, 'PAID')
            self.assertEqual((wallet.balance, wallet.frozen_amount, current_lots), (100, 0, 100))
            self.assertEqual(sum(row.principal_units for row in case.lots.all()), 12500)
            self.assertEqual(Transaction.objects.filter(
                wallet=wallet, tx_type__startswith='CLOSURE_').count(), 2)

    def test_frozen_shares_survive_original_refund_and_existing_gift_before_release(self):
        case_id = self._freeze_refund_and_existing_gift('independent-share-release')
        released = self.release(self.first, case_id)
        self.assertEqual(released.status_code, 200, released.content)
        self.assertEqual(released.data['data']['status'], 'RELEASED')
        with store_context(self.first):
            account = self.accounts[self.first.slug]
            wallet = Wallet.objects.get(account=account)
            case = ClosureReturnCase.objects.get(pk=case_id)
            current_lots = sum(PointFundingLot.objects.filter(
                account=account).values_list('remaining', flat=True))
            self.assertEqual(case.status, 'RELEASED')
            self.assertEqual((wallet.balance, wallet.frozen_amount, current_lots), (12600, 0, 12600))
            self.assertEqual(sum(row.principal_units for row in case.lots.all()), 12500)
            self.assertEqual(case.release_transaction.reversal_of_id, case.freeze_transaction_id)

    def test_original_receipts_replay_before_cycle_guard_after_real_retirement(self):
        from engagement.retirement import preview_retirement, retire

        self.enter_settlement(self.first)
        account = self.accounts[self.first.slug]
        prepare_body = {
            'account_id': account.pk, 'business_cycle': 1,
            'expected_wallet_units': 12500, 'expected_principal_units': 12500,
            'client_request_id': 'cycle-one-original-prepare',
        }
        prepared = self.client.post(self.url(self.first, '/prepare'), prepare_body, format='json')
        self.assertEqual(prepared.status_code, 201, prepared.content)
        case_id = prepared.data['data']['case_id']
        payment_body = {
            'amount_fen': 12500, 'external_reference': 'OFFLINE-RETURN-0001',
            'proof_sha256': hashlib.sha256(b'closure-return-proof').hexdigest(),
            'occurred_at': '2026-11-25T02:00:00+00:00',
            'client_request_id': 'cycle-one-original-payment',
        }
        paid = self.client.post(
            self.url(self.first, f'/{case_id}/record-payment'), payment_body, format='json',
        )
        self.assertEqual(paid.status_code, 200, paid.content)
        with store_context(self.first):
            account.refresh_from_db()
            preview = preview_retirement(account, self.retirement_operator)
            retired = retire(
                account, self.retirement_operator, '核对周期一历史回执', preview['confirmation_token'],
            )
            self.assertEqual(retired.business_cycle, 2)
            self.assertEqual(Wallet.objects.get(account=account).balance, 0)
        prepare_replay = self.client.post(self.url(self.first, '/prepare'), prepare_body, format='json')
        payment_replay = self.client.post(
            self.url(self.first, f'/{case_id}/record-payment'), payment_body, format='json',
        )
        self.assertEqual(prepare_replay.status_code, 200, prepare_replay.content)
        self.assertEqual(payment_replay.status_code, 200, payment_replay.content)
        self.assertTrue(prepare_replay.data['data']['replayed'])
        self.assertTrue(payment_replay.data['data']['replayed'])
        self.assertEqual(prepare_replay.data['data']['business_cycle'], 1)
        self.assertEqual(payment_replay.data['data']['business_cycle'], 1)
        with store_context(self.first):
            self.assertEqual(ClosureReturnEvent.objects.filter(case_id=case_id).count(), 2)
            self.assertEqual(Transaction.objects.filter(
                wallet__account=account, tx_type__startswith='CLOSURE_').count(), 2)
        detail = self.finance_client.get(self.url(self.first, f'/{case_id}'))
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.data['data']['business_cycle'], 1)
        self.assertEqual(detail.data['data']['status'], 'PAID')
        self.assertEqual((detail.data['data']['wallet_units'], detail.data['data']['principal_units'],
                          detail.data['data']['paid_fen']), (0, 12500, 12500))

    def test_frozen_source_identity_damage_is_visible_in_list_detail_and_summary(self):
        self.enter_settlement(self.first)
        case_id = self.prepare(self.first).data['data']['case_id']
        with store_context(self.first):
            PointFundingLot.objects.filter(pk=self.lots[self.first.slug].pk).update(source_key='damaged-source')
        response, row = self.listed(self.first, self.accounts[self.first.slug].pk)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertIn('FROZEN_SOURCE_MISMATCH', row['blocking_reasons'])
        self.assertFalse(response.data['data']['summary']['complete'])
        self.assertIsNone(response.data['data']['summary']['totals'])
        detail = self.finance_client.get(self.url(self.first, f'/{case_id}'))
        self.assertEqual(detail.data['data']['status'], 'REVIEW_REQUIRED')
        self.assertIn('FROZEN_SOURCE_MISMATCH', detail.data['data']['blocking_reasons'])

    def test_new_refund_after_paid_is_visible_and_can_be_prepared_separately(self):
        old_case_id = self._freeze_refund_and_existing_gift('paid-terminal-new-funds')
        paid = self.payment(self.first, old_case_id)
        self.assertEqual(paid.status_code, 200, paid.content)
        response, row = self.listed(self.first, self.accounts[self.first.slug].pk)
        self.assertEqual(row['status'], 'READY_TO_PREPARE')
        self.assertEqual((row['wallet_units'], row['principal_units'], row['refund_fen']), (100, 100, 100))
        self.assertEqual(row['paid_fen'], 0)
        detail = self.finance_client.get(self.url(self.first, f'/{old_case_id}'))
        self.assertEqual(detail.data['data']['status'], 'PAID')
        self.assertEqual(detail.data['data']['wallet_units'], paid.data['data']['wallet_balance'])
        self.assertEqual((detail.data['data']['principal_units'], detail.data['data']['refund_fen'],
                          detail.data['data']['paid_fen']), (12500, 12500, 12500))
        created = self.prepare(
            self.first, request_id='paid-terminal-new-case',
            expected_wallet_units=100, expected_principal_units=100,
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertNotEqual(created.data['data']['case_id'], old_case_id)
        old_detail = self.finance_client.get(self.url(self.first, f'/{old_case_id}'))
        self.assertEqual(old_detail.data['data']['status'], 'PAID')
        with store_context(self.first):
            self.assertEqual(ClosureReturnEvent.objects.filter(case_id=old_case_id).count(), 2)

    def test_prepare_payment_release_use_wallet_then_account_lock_order(self):
        from django.db.models import QuerySet

        original = QuerySet.select_for_update
        operations = []

        def observe(queryset, *args, **kwargs):
            if queryset.model in (Wallet, ClubAccount):
                operations.append('WALLET' if queryset.model is Wallet else 'ACCOUNT')
            return original(queryset, *args, **kwargs)

        self.enter_settlement(self.first)
        with patch.object(QuerySet, 'select_for_update', new=observe):
            prepared = self.prepare(self.first)
        self.assertEqual(prepared.status_code, 201, prepared.content)
        self.assertEqual(operations[:2], ['WALLET', 'ACCOUNT'], operations)
        case_id = prepared.data['data']['case_id']
        operations.clear()
        with patch.object(QuerySet, 'select_for_update', new=observe):
            paid = self.payment(self.first, case_id)
        self.assertEqual(paid.status_code, 200, paid.content)
        self.assertEqual(operations[:2], ['WALLET', 'ACCOUNT'], operations)
        released_case = self.prepare(
            self.first, self.releasable, request_id='lock-order-release-prepare',
        ).data['data']['case_id']
        operations.clear()
        with patch.object(QuerySet, 'select_for_update', new=observe):
            released = self.release(self.first, released_case)
        self.assertEqual(released.status_code, 200, released.content)
        self.assertEqual(operations[:2], ['WALLET', 'ACCOUNT'], operations)


    def test_migration_review_required_blocks_payment_and_release_without_money_changes(self):
        self.enter_settlement(self.first)
        case_id = self.prepare(self.first).data['data']['case_id']
        with store_context(self.first):
            ClosureReturnCase.objects.filter(pk=case_id).update(
                migration_review_required=True, migration_review_reason='FROZEN_LOT_CHANGED',
            )
        response, row = self.listed(self.first, self.accounts[self.first.slug].pk)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertIn('MIGRATION_REVIEW_REQUIRED', row['blocking_reasons'])
        self.assertFalse(response.data['data']['summary']['complete'])
        detail = self.finance_client.get(self.url(self.first, f'/{case_id}'))
        self.assertEqual(detail.data['data']['status'], 'REVIEW_REQUIRED')
        self.assertIn('MIGRATION_REVIEW_REQUIRED', detail.data['data']['blocking_reasons'])
        paid = self.payment(self.first, case_id)
        released = self.release(self.first, case_id)
        self.assertEqual((paid.status_code, released.status_code), (409, 409))
        self.assertEqual((paid.data['errorCode'], released.data['errorCode']),
                         ('MIGRATION_REVIEW_REQUIRED', 'MIGRATION_REVIEW_REQUIRED'))
        with store_context(self.first):
            case = ClosureReturnCase.objects.get(pk=case_id)
            wallet = Wallet.objects.get(account=self.accounts[self.first.slug])
            self.assertEqual((case.status, case.migration_review_required), ('FROZEN', True))
            self.assertEqual((wallet.balance, wallet.frozen_amount), (0, 12500))
            self.assertEqual(PointFundingLot.objects.get(pk=self.lots[self.first.slug].pk).remaining, 0)
            self.assertEqual(ClosureReturnEvent.objects.filter(case=case).count(), 1)
            self.assertEqual(ClosureReturnOutbox.objects.filter(event__case=case).count(), 1)
            self.assertEqual(Transaction.objects.filter(
                wallet=wallet, tx_type__startswith='CLOSURE_').count(), 1)
