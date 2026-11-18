"""线下补差按本店押金记账，不经过陪玩钱包。"""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import CashDepositTopup, IdempotencyKey, Transaction, get_platform_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class CashDepositTopupTest(APITestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=self.folder.name))
        self.provider = make_provider(deposit_mode='CASH', deposit_paid=20000, deposit_required=20000)
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.account
        self.profile.deposit_business_cycle = self.account.business_cycle
        self.profile.save(update_fields=['account', 'deposit_business_cycle'])
        self.wallet = make_wallet(self.provider, balance=1234)
        self.wallet.account = self.account
        self.wallet.save(update_fields=['account'])
        self.staff = make_console_user(['escort:view', 'escort:deposit_topup'])
        self.staff_account = get_or_create_account_for_legacy_user(self.staff)
        self.staff.admin_membership.account = self.staff_account
        self.staff.admin_membership.save(update_fields=['account'])
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff_account)[0])
        self.url = f'/api/admin/escorts/{self.profile.pk}/deposit-topups/'

    def post(self, **overrides):
        data = {'amount': 20000, 'reason': '核验线下补差', 'reference': 'BANK-2026-1',
                'client_request_id': 'cash-topup-test-1'}
        data.update(overrides)
        return self.client.post(self.url, data, format='multipart')

    def test_offline_topup_is_atomic_and_does_not_touch_provider_wallet(self):
        platform_before = get_platform_wallet().balance
        self.assertEqual(self.profile.order_acceptance_error(), '陪玩押金未缴齐')
        response = self.post(proof_image=screenshot())
        self.assertEqual(response.data['code'], 0, response.data)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, self.wallet.balance, self.wallet.total_recharge), (40000, 1234, 0))
        self.assertNotEqual(self.profile.order_acceptance_error(), '陪玩押金未缴齐')
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet).exists())
        row = CashDepositTopup.objects.get(profile=self.profile)
        self.assertEqual((row.amount, row.paid_before, row.paid_after, row.target_snapshot), (20000, 20000, 40000, 40000))
        self.assertEqual((row.operator_id, row.reference, row.reason), (self.staff_account.pk, 'BANK-2026-1', '核验线下补差'))
        self.assertEqual(row.platform_transaction.tx_type, 'DEPOSIT_INCOME')
        self.assertEqual(get_platform_wallet().balance, platform_before + 20000)
        self.assertEqual(self.client.get(self.url).data['data'][0]['id'], row.pk)
        proof_url = self.client.get(self.url).data['data'][0]['proof_image_url']
        proof = self.client.get(proof_url)
        self.assertEqual(proof.status_code, 200)
        if proof.streaming: b''.join(proof.streaming_content)
        self.assertEqual(self.post().status_code, 409)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.deposit_paid, 40000)
        self.assertEqual(CashDepositTopup.objects.count(), 1)

    def test_duplicate_reference_is_rejected_before_new_funds(self):
        self.assertEqual(self.post(amount=5000).data['code'], 0)
        repeated = self.post(amount=5000, reference=' bank-2026-1 ', client_request_id='other-topup-key')
        self.assertEqual(repeated.status_code, 409)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.deposit_paid, 25000)
        self.assertFalse(IdempotencyKey.objects.filter(request_id='other-topup-key').exists())

    def test_unreferenced_image_cannot_be_reused_but_distinct_references_can_share_image(self):
        self.assertEqual(self.post(amount=3000, reference='', proof_image=screenshot()).data['code'], 0)
        duplicate = self.post(amount=3000, reference='another-ref', proof_image=screenshot(),
                              client_request_id='other-image-key')
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(CashDepositTopup.objects.count(), 1)

    def test_two_distinct_receipts_can_use_a_shared_aggregate_image(self):
        self.assertEqual(self.post(amount=3000, reference='receipt-one', proof_image=screenshot()).data['code'], 0)
        second = self.post(amount=4000, reference='receipt-two', proof_image=screenshot(),
                           client_request_id='other-image-key')
        self.assertEqual(second.data['code'], 0, second.data)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.deposit_paid, 27000)

    def test_invalid_amount_mode_and_missing_evidence_never_write(self):
        before = get_platform_wallet().balance
        for changes in ({'amount': 0}, {'amount': 20001}, {'reason': ''}, {'reference': ''}, {'amount': 1.5}):
            self.assertEqual(self.post(**changes).status_code, 400)
        self.profile.deposit_mode = 'EARNINGS'
        self.profile.save(update_fields=['deposit_mode'])
        self.assertEqual(self.post().status_code, 400)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, self.wallet.balance, get_platform_wallet().balance), (20000, 1234, before))
        self.assertFalse(CashDepositTopup.objects.exists())

    def test_permission_and_rollback(self):
        limited = make_console_user(['escort:view'])
        account = get_or_create_account_for_legacy_user(limited)
        limited.admin_membership.account = account
        limited.admin_membership.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(self.post().status_code, 403)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff_account)[0])
        before = get_platform_wallet().balance
        with patch('wallet.cash_deposit_models.CashDepositTopup.save', side_effect=RuntimeError('rollback test')):
            with self.assertRaises(RuntimeError):
                self.post()
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, get_platform_wallet().balance), (20000, before))
        self.assertFalse(IdempotencyKey.objects.filter(request_id='cash-topup-test-1').exists())
        self.assertEqual(self.post().data['code'], 0)

    def test_proof_access_is_private_and_revoked_with_permission(self):
        self.assertEqual(self.post(proof_image=screenshot()).data['code'], 0)
        row = self.client.get(self.url).data['data'][0]
        self.assertTrue(urlsplit(row['proof_image_url']).path.startswith('/api/media/private/'))
        receipt = CashDepositTopup.objects.get(pk=row['id'])
        self.assertEqual(self.client.get('/media/' + receipt.proof_image.name).status_code, 404)
        role = self.staff.admin_membership.roles.get()
        role.permissions = ['escort:view']
        role.save(update_fields=['permissions'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(row['proof_image_url']).status_code, 404)

    def test_failure_after_proof_save_rolls_back_money_and_removes_new_file(self):
        before = get_platform_wallet().balance
        with patch('console.cash_deposit.row_data', side_effect=RuntimeError('test after file save')):
            with self.assertRaises(RuntimeError):
                self.post(proof_image=screenshot())
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, self.wallet.balance, get_platform_wallet().balance), (20000, 1234, before))
        self.assertFalse(CashDepositTopup.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(request_id='cash-topup-test-1').exists())
        self.assertFalse(any(p.is_file() for p in Path(self.folder.name).rglob('*')))

    def test_readonly_reconciliation_includes_external_cash(self):
        # The prior deposit is represented by its historical provider and pool ledgers.
        Transaction.objects.create(wallet=self.wallet, tx_type='DEPOSIT', amount=-20000,
            balance_before=20000, balance_after=0, remark='历史押金')
        platform = get_platform_wallet()
        platform.balance = 20000
        platform.save(update_fields=['balance'])
        Transaction.objects.create(wallet=platform, tx_type='DEPOSIT_INCOME', amount=20000,
            balance_before=0, balance_after=20000, remark='历史押金')
        self.assertEqual(self.post().data['code'], 0)
        output = StringIO()
        call_command('backfill_deposit_platform_income', stdout=output)
        self.assertIn('线下现金押金登记：1 笔，合计 20000', output.getvalue())
        self.assertIn('差额 [1]+[1a]-[2]：0', output.getvalue())
        self.assertIn('待回补金额 [1]+[1a]-[4]：0', output.getvalue())
