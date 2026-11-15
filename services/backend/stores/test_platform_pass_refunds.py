from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from stores.tests_hosts import xinan_store
from users.models import ProviderPassPurchase, ProviderPassRefund
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True)
class PlatformPassRefundTests(TestCase):
    databases = {'default', 'store_b'}
    url = '/api/admin/platform/pass-refund-shortfalls'

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.client = APIClient(HTTP_HOST='admin.esports.club')
        self.finance = PlatformAccount(username='shortfall-finance', phone='13800001111', is_phone_verified=True)
        self.finance.set_password('test-platform-password')
        self.finance.save()
        self.grant = PlatformRoleGrant.objects.create(account=self.finance, role='FINANCE', scope_slug='xinan')
        self.token = issue_tokens(self.finance, 'SMS', self.finance.phone)[0]
        for store, amount in ((self.first, 110), (self.second, 220)):
            with store_context(store):
                self.create_shortfall(amount)

    def create_shortfall(self, amount):
        provider = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(provider)
        wallet = get_wallet(account=account, user=provider)
        now = timezone.now()
        payment = Transaction.objects.create(wallet=wallet, tx_type='PASS_PURCHASE', amount=-amount,
                                              balance_before=amount, balance_after=0)
        purchase = ProviderPassPurchase.objects.create(provider=provider, account=account, tier='BRONZE',
            days=1, daily_price=amount, original_amount=amount, paid_amount=amount, transaction=payment,
            starts_at=now + timedelta(hours=1), expires_at=now + timedelta(hours=25))
        returned = Transaction.objects.create(wallet=wallet, tx_type='PASS_PURCHASE', amount=amount,
                                               balance_before=0, balance_after=amount, reversal_of=payment)
        return ProviderPassRefund.objects.create(purchase=purchase, account=account, amount=amount,
            funded_amount=0, shortfall_amount=amount, provider_transaction=returned,
            original_starts_at=purchase.starts_at, original_expires_at=purchase.expires_at)

    def get(self, url=None, token=None, **params):
        return self.client.get(url or self.url, params, HTTP_AUTHORIZATION='Bearer ' + (token or self.token))

    def test_scoped_finance_sees_only_authorized_store_and_audits_reads(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')
        data = response.data['data']
        self.assertTrue(data['complete'])
        self.assertEqual([row['slug'] for row in data['stores']], ['xinan'])
        self.assertEqual(data['stores'][0]['data'], {'count': 1, 'shortfall_amount': 220})
        self.assertNotIn('total', data)
        self.assertEqual(self.get(self.url + '/shenglang').status_code, 404)
        detail = self.get(self.url + '/xinan').data['data']
        self.assertEqual((detail['total'], detail['results'][0]['shortfall_amount']), (1, 220))
        self.assertEqual(detail['results'][0]['points_status'], 'NOT_USED')
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.finance, action='pass_refunds.view_summary').exists())
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.finance, action='pass_refunds.view_detail',
                                                           target_store_slug='xinan').exists())

    @override_settings(PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True)
    def test_actual_mixed_payment_refund_separates_coin_shortfall_and_points(self):
        from console.points_redemption_config import save_points_terms
        from engagement.models import MemberProfile, PointLedger
        from engagement.services import config
        from wallet.models import get_platform_wallet
        with store_context(self.second):
            provider = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(provider)
            profile = provider.escort_profile; profile.account = account; profile.save(update_fields=['account'])
            wallet = get_wallet(account=account, user=provider); wallet.balance = 1000; wallet.save(update_fields=['balance'])
            MemberProfile.objects.create(account=account, points_balance=30000)
            config()
            save_points_terms(group_point_hundredths=10000, group_coin_units=10,
                              max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
            client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            quote = client.post('/api/users/provider-pass/points-quote/', {'tier': 'BRONZE',
                                'selected_point_hundredths': 20000}, format='json').data['data']
            result = client.post('/api/users/provider-pass/', {'tier': 'BRONZE', 'days': 1,
                'points_quote_token': quote['quote_token'], 'client_request_id': 'finance-points-pass'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            purchase = ProviderPassPurchase.objects.get(account=account)
            now = timezone.now(); purchase.starts_at = now + timedelta(hours=1); purchase.expires_at = now + timedelta(hours=25)
            purchase.save(update_fields=['starts_at', 'expires_at'])
            profile.pass_tier, profile.pass_expires_at = purchase.tier, purchase.expires_at
            profile.save(update_fields=['pass_tier', 'pass_expires_at'])
            shop = get_platform_wallet(); shop.balance = 0; shop.save(update_fields=['balance'])
            response = client.post(f'/api/users/provider-pass/{purchase.pk}/refund/',
                                   {'client_request_id': 'finance-points-refund'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            refund = ProviderPassRefund.objects.get(purchase=purchase)
            refund_id, return_id = refund.pk, refund.points_return_ledger_id
        summary = self.get().data['data']['stores'][0]['data']
        self.assertEqual((summary['count'], summary['shortfall_amount']), (2, 400))
        data = self.get(self.url + '/xinan').data['data']
        row = next(row for row in data['results'] if row['refund_id'] == refund_id)
        self.assertEqual((row['refund_amount'], row['shortfall_amount'], row['point_discount_amount'],
                          row['points_redeemed'], row['points_returned'], row['points_status']),
                         (180, 180, 20, 20000, 20000, 'VERIFIED'))
        with store_context(self.second):
            PointLedger.objects.filter(pk=return_id).update(amount=19999)
        data = self.get(self.url + '/xinan').data['data']
        self.assertEqual(next(row for row in data['results'] if row['refund_id'] == refund_id)['points_status'], 'REVIEW')

    def test_super_admin_reads_both_currencies_without_combining_amounts(self):
        PlatformRoleGrant.objects.create(account=self.finance, role='SUPER_ADMIN', scope_slug='*')
        rows = self.get().data['data']['stores']
        self.assertEqual({row['slug']: row['data']['shortfall_amount'] for row in rows},
                         {'shenglang': 110, 'xinan': 220})
        self.assertEqual({row['slug']: row['currency_name'] for row in rows}['xinan'], '星辉')

    def test_unavailable_and_disabled_stores_are_not_misreported_as_zero(self):
        self.second.status = 'DISABLED'
        self.second.save(update_fields=['status'])
        self.assertEqual(self.get().data['data']['stores'][0]['data']['shortfall_amount'], 220)
        with patch('stores.platform_pass_refunds._store_summary', side_effect=DatabaseError('offline')):
            data = self.get().data['data']
        self.assertFalse(data['complete'])
        self.assertEqual((data['stores'][0]['status'], data['stores'][0]['data']), ('UNAVAILABLE', None))
        with patch('stores.platform_pass_refunds.detail', side_effect=DatabaseError('offline')):
            self.assertEqual(self.get(self.url + '/xinan').status_code, 503)

    def test_revoked_scope_and_store_tokens_cannot_access_platform_data(self):
        PlatformRoleGrant.objects.create(account=self.finance, role='FINANCE', scope_slug='shenglang')
        self.grant.is_active = False
        self.grant.save(update_fields=['is_active'])
        self.assertEqual([row['slug'] for row in self.get().data['data']['stores']], ['shenglang'])
        self.assertEqual(self.get(self.url + '/xinan').status_code, 404)
        with store_context(self.first):
            user = make_provider()
            account = get_or_create_account_for_legacy_user(user)
            token = issue_account_tokens(account)[0]
        self.assertEqual(self.get(token=token).status_code, 401)

    def test_invalid_page_is_rejected_and_details_are_paginated(self):
        self.assertEqual(self.get(self.url + '/xinan', page='0').status_code, 400)
        self.assertEqual(self.get(self.url + '/xinan', page='bad').status_code, 400)
        data = self.get(self.url + '/xinan', page='2').data['data']
        self.assertEqual((data['page'], data['total'], data['results']), (2, 1, []))
