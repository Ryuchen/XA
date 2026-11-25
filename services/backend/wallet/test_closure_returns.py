"""Fail-closed cash provenance and source inventory for closure returns."""
import hashlib

from django.test import TestCase

from club_accounts.models import ClubAccount
from engagement.models import MemberProfile, PointFundingLot
from stores.context import resolve_store, store_context
from wallet.closure_returns import closure_return_list, closure_return_row, mask_external_reference
from wallet.models import RechargeGiftApproval, RechargeRecord, Transaction, Wallet


class ClosureReturnInventoryTests(TestCase):
    def setUp(self):
        self.context = store_context(resolve_store())
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.sequence = 0

    def account(self, pk=818001):
        account = ClubAccount.objects.create_account(
            id=pk, username=f'closure-inventory-{pk}', account_type='BOSS',
            account_no=f'XA{pk}', nickname='闭店清点老板',
        )
        MemberProfile.objects.create(account=account, points_balance=0)
        return account

    def verified_recharge(self, account, wallet, amount, *, gift=0):
        self.sequence += 1
        trade_no = f'closure-cash-{account.pk}-{self.sequence}'
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
            remark='闭店现金来源测试充值',
        )
        gift_tx = None
        if gift:
            gift_tx = Transaction.objects.create(
                wallet=wallet, tx_type='GIFT', amount=gift, status='SUCCESS',
                balance_before=before + amount, balance_after=before + amount + gift,
                remark='闭店现金来源测试赠送',
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

    def cash_account(self, pk=818001, amount=1000, *, gift=0):
        account = self.account(pk)
        wallet = Wallet.objects.create(account=account, balance=0)
        record, lot = self.verified_recharge(account, wallet, amount, gift=gift)
        return account, wallet, record, lot

    def test_real_recharge_successful_topup_cycle_and_receipt_allow_cash_ready(self):
        account, _wallet, _record, _lot = self.cash_account()
        row = closure_return_row(account)
        self.assertEqual(row['status'], 'READY_TO_PREPARE')
        self.assertEqual((row['principal_units'], row['gift_units'], row['refund_fen']), (1000, 0, 1000))

    def test_verified_mixed_recharge_shows_split_but_is_never_cash_ready(self):
        account, _wallet, _record, _lot = self.cash_account(amount=600, gift=400)
        row = closure_return_row(account)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertEqual((row['principal_units'], row['gift_units'], row['compensation_units']), (600, 400, 0))
        self.assertIsNone(row['refund_fen'])
        self.assertIn('NON_PRINCIPAL_BALANCE', row['blocking_reasons'])

    def test_null_gift_split_is_unknown_even_with_real_recharge_evidence(self):
        account, _wallet, _record, lot = self.cash_account()
        PointFundingLot.objects.filter(pk=lot.pk).update(gift_remaining=None)
        row = closure_return_row(account)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertIsNone(row['principal_units'])
        self.assertIsNone(row['gift_units'])
        self.assertIn('UNKNOWN_RECHARGE_SPLIT', row['blocking_reasons'])

    def test_missing_or_mismatched_recharge_evidence_fails_closed(self):
        mutations = (
            ('missing-record', lambda record, lot: record.delete()),
            ('missing-transaction', lambda record, lot: RechargeRecord.objects.filter(pk=record.pk).update(recharge_tx=None)),
            ('failed-transaction', lambda record, lot: Transaction.objects.filter(pk=record.recharge_tx_id).update(status='FAILED')),
            ('wrong-cycle', lambda record, lot: RechargeRecord.objects.filter(pk=record.pk).update(
                account_business_cycle=record.account_business_cycle + 1)),
            ('missing-proof', lambda record, lot: RechargeRecord.objects.filter(pk=record.pk).update(proof_image='')),
            ('receipt-mismatch', lambda record, lot: RechargeRecord.objects.filter(pk=record.pk).update(receipt_key='0' * 64)),
        )
        for index, (label, mutate) in enumerate(mutations):
            with self.subTest(label=label):
                account, _wallet, record, lot = self.cash_account(818100 + index)
                mutate(record, lot)
                row = closure_return_row(account)
                self.assertEqual(row['status'], 'REVIEW_REQUIRED')
                self.assertIsNone(row['principal_units'])
                self.assertIsNone(row['refund_fen'])
                self.assertTrue(any(reason.startswith('CASH_') for reason in row['blocking_reasons']), row)

    def test_classification_never_turns_referral_adjustment_or_prior_balance_into_cash(self):
        sources = (
            'referral-income:1', 'points-settlement-referral:1', 'wallet-adjust:1',
            'recharge:1:prior-balance', 'closure-return:principal', 'recharge:999999',
        )
        for index, source_key in enumerate(sources):
            with self.subTest(source_key=source_key):
                account = self.account(818200 + index)
                Wallet.objects.create(account=account, balance=1000)
                PointFundingLot.objects.create(
                    account=account, source_key=source_key,
                    kind='RECHARGE' if source_key == 'recharge:999999' else 'PREPAID',
                    remaining=1000, gift_remaining=0 if source_key == 'recharge:999999' else None,
                )
                row = closure_return_row(account)
                self.assertEqual(row['status'], 'REVIEW_REQUIRED')
                self.assertIsNone(row['principal_units'])
                self.assertIsNone(row['refund_fen'])
                self.assertIn('CASH_SOURCE_UNVERIFIED', row['blocking_reasons'])

    def test_pending_gift_approval_blocks_even_verified_principal(self):
        account, _wallet, record, lot = self.cash_account()
        PointFundingLot.objects.filter(pk=lot.pk).update(kind='PREPAID', gift_remaining=None)
        RechargeGiftApproval.objects.create(
            recharge=record, standard_amount=0, requested_amount=100, status='PENDING',
        )
        row = closure_return_row(account)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertEqual((row['principal_units'], row['gift_units']), (1000, 0))
        self.assertIsNone(row['refund_fen'])
        self.assertIn('PENDING_GIFT_APPROVAL', row['blocking_reasons'])

    def test_missing_wallet_points_and_unrecognized_source_are_unknown_not_zero(self):
        account = self.account()
        MemberProfile.objects.filter(account=account).delete()
        row = closure_return_row(account)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertIsNone(row['wallet_units'])
        self.assertIsNone(row['principal_units'])
        self.assertIsNone(row['points_hundredths'])
        self.assertIn('WALLET_UNAVAILABLE', row['blocking_reasons'])
        self.assertIn('POINTS_UNAVAILABLE', row['blocking_reasons'])
        unknown = self.account(818002)
        Wallet.objects.create(account=unknown, balance=100)
        lot = PointFundingLot.objects.create(account=unknown, source_key='closure:unknown-kind', remaining=100)
        PointFundingLot.objects.filter(pk=lot.pk).update(kind='UNRECOGNIZED')
        unknown_row = closure_return_row(unknown)
        self.assertEqual(unknown_row['status'], 'REVIEW_REQUIRED')
        self.assertIsNone(unknown_row['principal_units'])
        self.assertIn('UNRECOGNIZED_FUNDING_SOURCE', unknown_row['blocking_reasons'])

    def test_existing_freeze_and_zero_balance_do_not_offer_impossible_prepare(self):
        frozen, _wallet, _record, _lot = self.cash_account(818003, amount=100)
        Wallet.objects.filter(account=frozen).update(frozen_amount=50)
        frozen_row = closure_return_row(frozen)
        self.assertEqual(frozen_row['status'], 'REVIEW_REQUIRED')
        self.assertEqual(frozen_row['frozen_units'], 50)
        self.assertIn('EXISTING_FROZEN_BALANCE', frozen_row['blocking_reasons'])
        empty = self.account(818004)
        Wallet.objects.create(account=empty, balance=0)
        empty_row = closure_return_row(empty)
        self.assertEqual(empty_row['status'], 'REVIEW_REQUIRED')
        self.assertEqual((empty_row['wallet_units'], empty_row['principal_units']), (0, 0))
        self.assertIsNone(empty_row['refund_fen'])
        self.assertIn('ZERO_BALANCE', empty_row['blocking_reasons'])

    def test_two_pages_share_store_wide_summary_when_unknown_is_on_second_page(self):
        ready, _wallet, _record, _lot = self.cash_account(818020, amount=100)
        unknown, _wallet, _record, unknown_lot = self.cash_account(818021, amount=200)
        PointFundingLot.objects.filter(pk=unknown_lot.pk).update(gift_remaining=None)
        first = closure_return_list(page=1, page_size=1)
        second = closure_return_list(page=2, page_size=1)
        self.assertEqual(first['list'][0]['account_id'], ready.pk)
        self.assertEqual(second['list'][0]['account_id'], unknown.pk)
        self.assertEqual(first['summary'], second['summary'])
        self.assertFalse(first['summary']['complete'])
        self.assertIsNone(first['summary']['totals'])
        self.assertEqual(sum(first['summary']['counts'].values()), 2)

    def test_external_reference_is_masked_and_bank_details_are_rejected(self):
        masked = mask_external_reference('OFFLINE-RETURN-0001')
        self.assertNotEqual(masked, 'OFFLINE-RETURN-0001')
        self.assertNotIn('RETURN', masked)
        for value in ('6222020200112233456', '6222 0202 0011 2233 456', '', 'x' * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mask_external_reference(value)
