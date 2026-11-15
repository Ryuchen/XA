from datetime import timedelta
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_provider
from stores.context import store_context
from users.models import PassRefundRepaymentAllocation, ProviderPassPurchase, ProviderPassRefund
from users.pass_repayment import allocate, register_receipt, verify_receipt
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


class PassRefundRepaymentTests(TestCase):
    def setUp(self):
        self.store = SimpleNamespace(database_alias='default', slug='shenglang')
        self.provider = make_provider(deposit_mode='EARNINGS')
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.shop = get_platform_wallet()
        self.now = timezone.now()

    def refund(self, amount):
        payment = Transaction.objects.create(wallet=self.wallet, tx_type='PASS_PURCHASE', amount=-amount,
            balance_before=amount, balance_after=0)
        purchase = ProviderPassPurchase.objects.create(provider=self.provider, account=self.account, tier='BRONZE', days=1,
            daily_price=amount, original_amount=amount, paid_amount=amount, transaction=payment,
            starts_at=self.now + timedelta(hours=1), expires_at=self.now + timedelta(hours=25))
        returned = Transaction.objects.create(wallet=self.wallet, tx_type='PASS_PURCHASE', amount=amount,
            balance_before=0, balance_after=amount, reversal_of=payment)
        return ProviderPassRefund.objects.create(purchase=purchase, account=self.account, amount=amount,
            funded_amount=0, shortfall_amount=amount, provider_transaction=returned,
            original_starts_at=purchase.starts_at, original_expires_at=purchase.expires_at)

    def receipt(self, key='r1', reference='bank-1', amount=1000):
        return register_receipt(actor='finance-1', request_key=key, external_reference=reference,
            amount=amount, received_at=self.now, evidence='银行到账凭证')

    def test_verified_receipt_can_partially_cover_several_refunds_without_wallet_change(self):
        with store_context(self.store):
            first, second = self.refund(700), self.refund(800)
            before = (self.wallet.balance, self.shop.balance, Transaction.objects.count())
            receipt = self.receipt()
            with self.assertRaises(ValidationError):
                allocate(actor='finance-1', request_key='a0', receipt_id=receipt.pk, refund_id=first.pk, amount=1)
            verified = verify_receipt(actor='finance-2', receipt_id=receipt.pk)
            self.assertEqual(verified.verified_by, 'finance-2')
            a = allocate(actor='finance-1', request_key='a1', receipt_id=receipt.pk, refund_id=first.pk, amount=300)
            allocate(actor='finance-1', request_key='a2', receipt_id=receipt.pk, refund_id=second.pk, amount=400)
            self.assertEqual(allocate(actor='finance-1', request_key='a1', receipt_id=receipt.pk,
                                      refund_id=first.pk, amount=300).pk, a.pk)
            self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 2)
            self.wallet.refresh_from_db(); self.shop.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.shop.balance, Transaction.objects.count()), before)
            first.refresh_from_db(); self.assertEqual(first.shortfall_amount, 700)

    def test_cannot_overallocate_receipt_or_refund(self):
        with store_context(self.store):
            refund = self.refund(200)
            receipt = self.receipt(amount=300)
            verify_receipt(actor='finance-1', receipt_id=receipt.pk)
            allocate(actor='finance-1', request_key='a1', receipt_id=receipt.pk, refund_id=refund.pk, amount=150)
            with self.assertRaises(ValidationError):
                allocate(actor='finance-1', request_key='a2', receipt_id=receipt.pk, refund_id=refund.pk, amount=51)
            other = self.refund(200)
            with self.assertRaises(ValidationError):
                allocate(actor='finance-1', request_key='a3', receipt_id=receipt.pk, refund_id=other.pk, amount=151)
            self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 1)

    def test_duplicate_request_and_external_reference_are_not_new_receipts(self):
        with store_context(self.store):
            receipt = self.receipt()
            self.assertEqual(self.receipt().pk, receipt.pk)
            with self.assertRaises(ValidationError): self.receipt(amount=999)
            with self.assertRaises(ValidationError): self.receipt(key='r2')

    def test_multiple_receipts_cannot_together_exceed_one_refund(self):
        with store_context(self.store):
            refund = self.refund(200)
            first = self.receipt(amount=150)
            second = self.receipt(key='r2', reference='bank-2', amount=150)
            for receipt in (first, second):
                verify_receipt(actor='finance-2', receipt_id=receipt.pk)
            allocate(actor='finance-1', request_key='a1', receipt_id=first.pk, refund_id=refund.pk, amount=150)
            with self.assertRaises(ValidationError):
                allocate(actor='finance-1', request_key='a2', receipt_id=second.pk, refund_id=refund.pk, amount=51)
            allocate(actor='finance-1', request_key='a2', receipt_id=second.pk, refund_id=refund.pk, amount=50)
            self.assertEqual(refund.repayment_allocations.count(), 2)

    def test_rejects_missing_context_or_invalid_values(self):
        with self.assertRaises(ValidationError): self.receipt()
        with store_context(self.store):
            for amount in (0, -1, True, 1.5):
                with self.assertRaises(ValidationError): self.receipt(amount=amount)
