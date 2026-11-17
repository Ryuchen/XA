import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_provider
from stores.context import resolve_store
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from wallet.models import Transaction, WithdrawRequest
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_LEGACY_WITHDRAW_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True)
class PlatformLegacyWithdrawalBrowserTest(LiveServerTestCase):
    def test_finance_can_inspect_real_legacy_record_without_changing_money(self):
        store = resolve_store('shenglang')
        store.initialized_at = timezone.now()
        store.save(update_fields=['initialized_at'])
        finance = PlatformAccount(username='isolated-finance', phone='13900001111', is_phone_verified=True)
        finance.set_password('isolated-test-password'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug=store.slug)
        token = issue_tokens(finance, 'SMS', finance.phone)[0]
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        wallet = get_wallet(account=account, user=provider)
        wallet.balance, wallet.frozen_amount = 80000, 20000
        wallet.save(update_fields=['balance', 'frozen_amount'])
        tx = Transaction.objects.create(wallet=wallet, tx_type='WITHDRAW', status='PENDING', amount=-20000,
                                        balance_before=100000, balance_after=80000)
        row = WithdrawRequest.objects.create(user=provider, account=account, amount=20000, tax_amount=1000,
            payee_method='WECHAT', payee_name='隔离历史申请', payee_account='qa-only',
            workflow_version=1, transaction=tx)
        finance_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        stores_view = finance_client.get('/api/admin/platform/withdrawals')
        self.assertEqual(stores_view.data['code'], 0, stores_view.data)
        entry = next(item for item in stores_view.data['data']['stores'] if item['slug'] == store.slug)
        self.assertEqual(entry['legacy_pending'], 1)
        legacy = finance_client.get(f'/api/admin/platform/withdrawals/{store.slug}', {'scope': 'legacy'})
        self.assertEqual(legacy.data['code'], 0, legacy.data)
        record = next(item for item in legacy.data['data']['results'] if item['id'] == row.pk)
        self.assertEqual(record['workflow_version'], 1)
        self.assertEqual(record['legacy_ledger']['transaction_status'], 'PENDING')
        self.assertEqual(record['legacy_ledger']['wallet_frozen_total'], 20000)
        self.assertTrue(record['legacy_ledger']['owner_match'])
        blocked = finance_client.post(f'/api/admin/platform/withdrawals/{store.slug}/{row.pk}/approve',
            {'client_request_id': 'legacy-inspect-approve'}, format='json')
        self.assertEqual(blocked.status_code, 404, blocked.data)
        row.refresh_from_db(); wallet.refresh_from_db(); tx.refresh_from_db()
        self.assertEqual((row.workflow_version, row.status, wallet.balance, wallet.frozen_amount, tx.status),
                         (1, 'PENDING', 80000, 20000, 'PENDING'))
