from datetime import timedelta

from django.test import override_settings

from orders.tests.factories import make_completed_order
from wallet.models import IncomeHold, RefundDepositIncomeApplication, Transaction, get_platform_wallet
from wallet.test_gift_income_clocks import GiftIncomeClocksTest, release_due_income
from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest


@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
class RefundDepositGiftTest(GiftIncomeClocksTest):
    def setUp(self):
        super().setUp()
        self.account = self.accounts[1]
        self.profile = self.provider.escort_profile
        self.profile.deposit_mode = 'EARNINGS'
        self.profile.deposit_paid = 10000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = self.provider_wallet
        self.platform = get_platform_wallet()
        self.platform.balance = 10000
        self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID')

    def test_zero_clock_gift_replenishes_deficit_in_real_send_flow(self):
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        self.configure(0, 0)
        gift = self.send('gift-refund-deficit-zero')
        deficit.refresh_from_db(); self.provider_wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((deficit.remaining, self.provider_wallet.balance, self.profile.deposit_paid), (0, 1400, 10000))
        source = gift.transactions.get(tx_type='GIFT', wallet=self.provider_wallet)
        self.assertEqual(RefundDepositIncomeApplication.objects.get(source_transaction=source).applied_amount, 200)

    def test_gift_replenishes_only_after_both_clocks(self):
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        self.configure(1, 2)
        gift = self.send('gift-refund-deficit-delayed')
        self.assertEqual(release_due_income(now=gift.completed_at + timedelta(hours=1)), 0)
        deficit.refresh_from_db(); self.assertEqual(deficit.remaining, 200)
        self.assertEqual(release_due_income(now=gift.completed_at + timedelta(hours=2)), 1)
        deficit.refresh_from_db(); self.provider_wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((deficit.remaining, self.provider_wallet.balance, self.profile.deposit_paid), (0, 1400, 10000))

    def test_legacy_hold_waiting_for_reconciliation_does_not_starve_due_gift(self):
        from django.utils import timezone
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        source = Transaction.objects.create(wallet=self.provider_wallet, order=self.order,
            tx_type='INCOME', amount=100, balance_before=0, balance_after=100, remark='订单收入：历史')
        self.provider_wallet.frozen_amount = 100
        self.provider_wallet.save(update_fields=['frozen_amount'])
        old = IncomeHold.objects.create(wallet=self.provider_wallet, income_transaction=source,
            amount=100, deposit_amount=50, release_at=timezone.now()-timedelta(hours=1))
        self.configure(1, 1)
        gift = self.send('gift-behind-unreconciled-hold')
        self.assertEqual(release_due_income(now=gift.completed_at+timedelta(hours=2), limit=1), 1)
        old.refresh_from_db(); deficit.refresh_from_db()
        self.assertIsNone(old.released_at)
        self.assertEqual(deficit.remaining, 0)

    @override_settings(REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_gift_full_repayment_closes_open_recovery_permit(self):
        from stores.db import transaction
        from orders.services import ensure_provider_dispatch_locks, lock_provider_dispatch
        from orders.tests.factories import make_order
        from wallet.refund_recovery_order import reserve
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        recovery = make_order(self.boss, provider=self.provider, provider_account=self.account,
                              status='GRABBED', payment_status='PAID')
        ensure_provider_dispatch_locks([self.account.pk])
        with transaction.atomic():
            lock_provider_dispatch([self.account.pk])
            permit = reserve(self.profile, recovery)
        self.configure(0, 0)
        gift = self.send('gift-closes-refund-permit')
        permit.refresh_from_db(); deficit.refresh_from_db()
        self.assertEqual((permit.close_reason, deficit.remaining), ('GAP_PAID', 0))
        self.assertEqual(permit.income_application.source_transaction.order_id, gift.pk)
