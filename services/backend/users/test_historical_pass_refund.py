from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from users.models import ProviderPassPurchase, ProviderPassRefund
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class HistoricalPassRefundTests(APITestCase):
    def setUp(self):
        self.provider = make_provider(deposit_mode='EARNINGS')
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.account
        self.profile.save(update_fields=['account'])
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.wallet.balance = 10000
        self.wallet.save(update_fields=['balance'])
        self.shop = get_platform_wallet()
        self.now = timezone.now()
        self.active = self.purchase(self.now - timedelta(hours=19), self.now + timedelta(hours=5))
        self.b = self.purchase(self.active.expires_at, self.active.expires_at + timedelta(days=1))
        self.c = self.purchase(self.b.expires_at, self.b.expires_at + timedelta(days=1))
        self.profile.pass_tier = 'BRONZE'
        self.profile.pass_expires_at = self.c.expires_at
        self.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])

    def purchase(self, start, end, amount=200):
        before = self.wallet.balance
        self.wallet.balance -= amount
        self.wallet.save(update_fields=['balance'])
        tx = Transaction.objects.create(wallet=self.wallet, amount=-amount,
            tx_type=Transaction.TxType.PASS_PURCHASE, balance_before=before, balance_after=self.wallet.balance)
        before = self.shop.balance
        self.shop.balance += amount
        self.shop.save(update_fields=['balance'])
        Transaction.objects.create(wallet=self.shop, amount=amount, tx_type=Transaction.TxType.PASS_INCOME,
            balance_before=before, balance_after=self.shop.balance, external_tx_id=tx.tx_no)
        return ProviderPassPurchase.objects.create(provider=self.provider, account=self.account,
            tier='BRONZE', days=1, daily_price=amount, original_amount=amount,
            paid_amount=amount, starts_at=start, expires_at=end, transaction=tx,
            business_cycle=self.account.business_cycle)

    def refund(self, item, request_id='refund-1'):
        return self.client.post(f'/api/users/provider-pass/{item.pk}/refund/',
                                {'client_request_id': request_id}, format='json')

    def test_refund_reverses_original_payment_and_moves_later_rights_forward_once(self):
        wallet_before, shop_before = self.wallet.balance, self.shop.balance
        response = self.refund(self.b)
        self.assertEqual(response.data['code'], 0, response.data)
        self.wallet.refresh_from_db(); self.shop.refresh_from_db()
        self.profile.refresh_from_db(); self.c.refresh_from_db(); self.b.refresh_from_db()
        refund = ProviderPassRefund.objects.get(purchase=self.b)
        self.assertEqual((self.wallet.balance, self.shop.balance), (wallet_before + 200, shop_before - 200))
        self.assertEqual((refund.amount, refund.funded_amount, refund.shortfall_amount), (200, 200, 0))
        self.assertEqual(refund.provider_transaction.reversal_of_id, self.b.transaction_id)
        self.assertEqual((self.c.starts_at, self.profile.pass_expires_at), (self.b.starts_at, self.b.expires_at))
        self.assertEqual(refund.schedule_changes[0]['purchase_id'], self.c.pk)
        replay = self.refund(self.b)
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(self.refund(self.b, 'new-key').data['code'], 404)
        self.assertEqual(ProviderPassRefund.objects.count(), 1)
        data = self.client.get('/api/users/provider-pass/').data['data']
        self.assertTrue(next(x for x in data['history'] if x['id'] == self.b.pk)['refunded'])
        self.assertTrue(next(x for x in data['history'] if x['id'] == self.c.pk)['refundable'])

    def test_insufficient_shop_funds_immediately_credit_full_amount_and_record_shortfall(self):
        self.shop.balance = 120
        self.shop.save(update_fields=['balance'])
        self.wallet.refresh_from_db(); before = self.wallet.balance
        response = self.refund(self.b)
        self.assertEqual(response.data['code'], 0, response.data)
        self.wallet.refresh_from_db(); self.shop.refresh_from_db()
        refund = ProviderPassRefund.objects.get(purchase=self.b)
        self.assertEqual(self.wallet.balance, before + 200)
        self.assertEqual(self.shop.balance, 0)
        self.assertEqual((refund.funded_amount, refund.shortfall_amount), (120, 80))
        self.assertEqual(refund.shop_transaction.amount, -120)

    def test_shop_deposit_pool_is_not_used_for_pass_refund(self):
        self.shop.balance = 200
        self.shop.save(update_fields=['balance'])
        Transaction.objects.create(wallet=self.shop, tx_type=Transaction.TxType.DEPOSIT_INCOME,
                                   amount=150, balance_before=0, balance_after=150)
        self.assertEqual(self.refund(self.b).data['code'], 0)
        self.shop.refresh_from_db()
        refund = ProviderPassRefund.objects.get(purchase=self.b)
        self.assertEqual((self.shop.balance, refund.funded_amount, refund.shortfall_amount), (150, 50, 150))

    def test_active_and_unowned_passes_cannot_be_refunded(self):
        self.assertEqual(self.refund(self.active).data['code'], 400)
        stranger = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(stranger)
        stranger.escort_profile.account = account
        stranger.escort_profile.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(self.refund(self.b, 'other-provider').data['code'], 404)
        self.assertFalse(ProviderPassRefund.objects.exists())

    def test_rollback_on_refund_record_failure(self):
        wallet_before, shop_before, start_before = self.wallet.balance, self.shop.balance, self.c.starts_at
        with patch('users.pass_refund.ProviderPassRefund.objects.create', side_effect=RuntimeError('test rollback')):
            with self.assertRaises(RuntimeError):
                self.refund(self.b)
        self.wallet.refresh_from_db(); self.shop.refresh_from_db(); self.c.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.shop.balance, self.c.starts_at),
                         (wallet_before, shop_before, start_before))
        self.assertFalse(ProviderPassRefund.objects.exists())

    def test_inconsistent_payment_does_not_move_money(self):
        Transaction.objects.filter(pk=self.b.transaction_id).update(amount=-1)
        self.assertEqual(self.refund(self.b).data['code'], 409)
        self.assertFalse(ProviderPassRefund.objects.exists())

    def test_refund_each_queued_purchase_keeps_active_pass_and_no_pending_ghost(self):
        self.assertEqual(self.refund(self.b).data['code'], 0)
        self.assertEqual(self.refund(self.c, 'refund-2').data['code'], 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.pass_expires_at, self.active.expires_at)
        self.assertEqual(self.profile.pass_tier, 'BRONZE')
        self.assertEqual(ProviderPassRefund.objects.count(), 2)
        data = self.client.get('/api/users/provider-pass/').data['data']
        self.assertTrue(next(x for x in data['history'] if x['id'] == self.c.pk)['refunded'])
        self.assertFalse(next(x for x in data['history'] if x['id'] == self.c.pk)['refundable'])

    def test_pending_pass_that_just_reached_start_is_not_refunded(self):
        with patch('users.pass_refund.timezone.now', return_value=self.b.starts_at):
            response = self.refund(self.b)
        self.assertEqual(response.data['code'], 400)
        self.assertFalse(ProviderPassRefund.objects.exists())

    def test_store_pass_report_displays_refund_and_unfunded_portion(self):
        self.shop.balance = 120
        self.shop.save(update_fields=['balance'])
        self.assertEqual(self.refund(self.b).data['code'], 0)
        staff = make_console_user(['escort:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        response = client.get('/api/admin/pass-purchases/')
        self.assertEqual(response.data['code'], 0, response.data)
        row = next(row for row in response.data['data']['list'] if row['id'] == self.b.pk)
        self.assertEqual((row['refund_amount'], row['refund_shortfall']), (200, 80))
        self.assertIsNotNone(row['refunded_at'])
