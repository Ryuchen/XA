import os
from datetime import timedelta
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message
from stores.context import resolve_store
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from wallet.models import SystemConfig, WithdrawRequest
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_WITHDRAW_THREE_ROLE_BROWSER') == '1', 'Requires isolated ASGI and three-end Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True)
class WithdrawalThreeRoleBrowserTest(LiveServerTestCase):
    def test_store_review_finance_poll_payout_and_provider_message(self):
        store = resolve_store('shenglang')
        store.initialized_at = timezone.now(); store.save(update_fields=['initialized_at'])
        finance = PlatformAccount(username='three-role-finance', phone='13900001112', is_phone_verified=True)
        finance.set_password('isolated-test-password'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug=store.slug)
        token = issue_tokens(finance, 'SMS', finance.phone)[0]
        provider = make_provider(); account = get_or_create_account_for_legacy_user(provider)
        wallet = get_wallet(account=account, user=provider); wallet.balance = 100000; wallet.save(update_fields=['balance'])
        staff = make_console_user(['withdraw:view', 'withdraw:audit'])
        admin = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, admin)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        for key, value in [('min_withdraw_amount','0'), ('withdraw_balance_threshold','0'), ('withdraw_tax_rate','5')]:
            SystemConfig.objects.update_or_create(key=key, defaults={'value':value})
        client = APIClient(HTTP_HOST='sl.esports.club')
        provider_token = issue_account_tokens(account)[0]
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        result = client.post('/api/wallet/withdraw/', {'amount':20000, 'payee_method':'WECHAT',
            'payee_name':'隔离三端提现', 'payee_account':'qa-only', 'client_request_id':'three-role-apply'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        pk = result.data['data']['request']['id']
        WithdrawRequest.objects.filter(pk=pk).update(created_at=timezone.now() - timedelta(minutes=1))
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        claim = staff_client.post(f'/api/admin/withdrawals/{pk}/claim/', {}, format='json')
        self.assertEqual(claim.data['code'], 0, claim.data)
        approved = staff_client.post(f'/api/admin/withdrawals/{pk}/approve/',
            {'expected_claimed_at': claim.data['data']['claimed_at']}, format='json')
        self.assertEqual(approved.data['code'], 0, approved.data)
        finance_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        final = finance_client.post(f'/api/admin/platform/withdrawals/{store.slug}/{pk}/approve',
            {'client_request_id': 'three-role-final'}, format='json')
        self.assertEqual(final.data['code'], 0, final.data)
        paid = finance_client.post(f'/api/admin/platform/withdrawals/{store.slug}/{pk}/paid',
            {'client_request_id': 'three-role-payout', 'payout_reference': 'QA-THREE-ROLE-PAYOUT',
             'paid_at': timezone.now().isoformat(),
             'paid_amount_fen': WithdrawRequest.objects.get(pk=pk).actual_amount}, format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        row = WithdrawRequest.objects.get(pk=pk); wallet.refresh_from_db()
        self.assertEqual((row.status, row.transaction.status, wallet.balance, wallet.frozen_amount), ('PAID','SUCCESS',80000,0))
        self.assertEqual(list(Message.objects.filter(recipient_account=account).order_by('id').values_list('title', flat=True)),
                         ['提现门店初审通过', '提现平台终审通过', '提现已登记打款'])
