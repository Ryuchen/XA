"""Historical pending withdrawals survive claim migration and remain actionable."""
from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class WithdrawClaimMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_old_pending_is_preserved_unclaimed_then_claimed_and_reviewed(self):
        old = [('wallet', '0031_incomehold_recovered_amount_and_more')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        user = apps.get_model('users', 'CustomUser').objects.create(
            username='migration-withdraw-provider', role='PROVIDER')
        account = apps.get_model('club_accounts', 'ClubAccount').objects.create(
            username='migration-withdraw-account', account_no='migration-withdraw', account_type='PROVIDER')
        apps.get_model('club_accounts', 'LegacyAccountMap').objects.create(
            legacy_user_id=user.pk, account_id=account.pk, legacy_role='PROVIDER')
        wallet = apps.get_model('wallet', 'Wallet').objects.create(
            user_id=user.pk, account_id=account.pk, balance=40000, frozen_amount=20000)
        tx = apps.get_model('wallet', 'Transaction').objects.create(
            wallet_id=wallet.pk, tx_no='migration-withdraw-tx', amount=-20000,
            tx_type='WITHDRAW', status='PENDING', balance_before=60000, balance_after=40000)
        withdraw = apps.get_model('wallet', 'WithdrawRequest').objects.create(
            user_id=user.pk, account_id=account.pk, transaction_id=tx.pk, amount=20000,
            tax_rate=0, tax_amount=0, actual_amount=20000,
            payee_method='WECHAT', payee_account='isolated-old-account', payee_name='隔离旧资料',
            status='PENDING', remark='历史待审保留')

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from wallet.models import Transaction, Wallet, WithdrawRequest
        current = WithdrawRequest.objects.get(pk=withdraw.pk)
        self.assertEqual((current.status, current.amount, current.actual_amount, current.payee_account,
                          current.remark, current.transaction_id),
                         ('PENDING', 20000, 20000, 'isolated-old-account', '历史待审保留', tx.pk))
        self.assertIsNone(current.claimed_by_account_id)
        self.assertIsNone(current.claimed_at)
        self.assertEqual((Wallet.objects.get(pk=wallet.pk).balance, Wallet.objects.get(pk=wallet.pk).frozen_amount),
                         (40000, 20000))

        staff = make_console_user(['withdraw:view', 'withdraw:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        url = f'/api/admin/withdrawals/{withdraw.pk}/'
        claimed = client.post(url + 'claim/')
        self.assertEqual((claimed.status_code, claimed.data['code']), (200, 0), claimed.data)
        self.assertTrue(claimed.data['data']['claim_is_mine'])
        approved = client.post(url + 'approve/', {'payout_reference': 'ISOLATED-MIGRATION-TEST',
                                                   'expected_claimed_at': claimed.data['data']['claimed_at']}, format='json')
        self.assertEqual((approved.status_code, approved.data['code']), (200, 0), approved.data)
        self.assertEqual(WithdrawRequest.objects.get(pk=withdraw.pk).status, 'APPROVED')
        self.assertEqual(Transaction.objects.get(pk=tx.pk).status, 'SUCCESS')
        current_wallet = Wallet.objects.get(pk=wallet.pk)
        self.assertEqual((current_wallet.balance, current_wallet.frozen_amount), (40000, 0))
