"""A recipient who referred the boss sees gift income separate from commission."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.db.models import Sum
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider, ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GiftReferralRealTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.provider = make_provider(deposit_mode='CASH')
        self.staff = make_console_user(['order:view', 'order:refund'])
        self.accounts = {}
        for user in (self.boss, self.provider, self.staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            self.accounts[user.pk] = account
        self.provider.escort_profile.account = self.accounts[self.provider.pk]
        self.provider.escort_profile.save(update_fields=['account'])
        self.staff.admin_membership.account = self.accounts[self.staff.pk]
        self.staff.admin_membership.save(update_fields=['account'])
        self.boss.inviter = self.provider
        self.boss.inviter_commission_rate = 50
        self.boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        wallet = get_wallet(user=self.boss, account=self.accounts[self.boss.pk])
        wallet.balance = 50000
        wallet.save(update_fields=['balance'])
        category = ServiceCategory.objects.create(name='隔离礼物', is_gift=True)
        self.gift = make_service(name='隔离赠礼', price=10000, commission_rate=20, service_category=category)

    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.accounts[user.pk])[0])
        return client

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def test_provider_gift_history_uses_current_boss_identity(self):
        boss, provider = (self.client_for(user) for user in (self.boss, self.provider))
        created = self.ok(boss.post('/api/orders/gifts/send/', {
            'gift_service_id': self.gift.pk, 'quantity': 1, 'provider_id': self.accounts[self.provider.pk].pk,
            'expected_amount': 10000, 'client_request_id': 'gift-current-boss-identity',
        }, format='json'))
        self.boss.nickname = '历史老板昵称'
        self.boss.save(update_fields=['nickname'])
        boss_account = self.accounts[self.boss.pk]
        boss_account.nickname = '现行老板昵称'
        boss_account.save(update_fields=['nickname'])
        benefits = self.ok(provider.get('/api/wallet/provider-benefits/'))
        row = next(row for row in benefits['gift_records'] if row['id'] == created['id'])
        self.assertEqual(row['customer_name'], '现行老板昵称')
        Order.objects.filter(pk=created['id']).update(customer_account=None)
        benefits = self.ok(provider.get('/api/wallet/provider-benefits/'))
        row = next(row for row in benefits['gift_records'] if row['id'] == created['id'])
        self.assertEqual(row['customer_name'], '历史老板昵称')

    def test_historical_gift_follows_bound_wallet_after_identity_remapping(self):
        from club_accounts.models import ClubAccount, LegacyAccountMap
        boss, provider = (self.client_for(user) for user in (self.boss, self.provider))
        created = self.ok(boss.post('/api/orders/gifts/send/', {
            'gift_service_id': self.gift.pk, 'quantity': 1, 'provider_id': self.accounts[self.provider.pk].pk,
            'expected_amount': 10000, 'client_request_id': 'gift-historical-provider-identity',
        }, format='json'))
        Order.objects.filter(pk=created['id']).update(provider_account=None)
        OrderProvider.objects.filter(order_id=created['id']).update(provider_account=None)
        other = ClubAccount.objects.create(username='gift-remapped-other-provider', account_type=ClubAccount.AccountType.PROVIDER)
        LegacyAccountMap.objects.filter(legacy_user_id=self.provider.pk).update(account=other)
        own = self.ok(provider.get('/api/wallet/provider-benefits/'))
        row = next(row for row in own['gift_records'] if row['id'] == created['id'])
        self.assertEqual((row['received_income'], row['settlement_status']), (8000, 'SETTLED'))
        stranger = APIClient()
        stranger.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
        self.assertFalse(self.ok(stranger.get('/api/wallet/provider-benefits/'))['gift_records'])

    def test_real_gift_and_refunds_keep_referral_commission_separate(self):
        boss, provider, staff = (self.client_for(user) for user in (self.boss, self.provider, self.staff))
        created = self.ok(boss.post('/api/orders/gifts/send/', {
            'gift_service_id': self.gift.pk, 'quantity': 1, 'provider_id': self.accounts[self.provider.pk].pk,
            'expected_amount': 10000, 'client_request_id': 'isolated-gift-referral',
        }, format='json'))
        order = Order.objects.get(pk=created['id'])
        self.assertGreater(order.inviter_commission, 0)
        self.assertGreater(order.provider_income, 0)
        original = self.ok(provider.get('/api/wallet/provider-benefits/'))
        row = next(row for row in original['gift_records'] if row['id'] == order.pk)
        self.assertEqual(row['received_income'], order.provider_income)
        self.assertEqual(row['settlement_status'], 'SETTLED')
        self.assertEqual(original['summary']['total_gift_income'], order.provider_income)
        snapshots = [original]

        for i, amount in enumerate((2500, 2500), 1):
            preview = self.ok(staff.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': amount}))
            self.ok(staff.post(f'/api/admin/orders/{order.pk}/refund/', {
                'refund_amount': amount, 'reason': '隔离分次退款', 'resolution': '隔离测试',
                'client_request_id': f'isolated-gift-refund-{i}', 'plan_token': preview['plan_token'],
            }, format='json'))
            service_tx = Transaction.objects.filter(order=order, wallet__account=self.accounts[self.provider.pk],
                tx_type='GIFT', status='SUCCESS').aggregate(total=Sum('amount'))['total']
            data = self.ok(provider.get('/api/wallet/provider-benefits/'))
            row = next(row for row in data['gift_records'] if row['id'] == order.pk)
            self.assertEqual(row['received_income'], service_tx)
            self.assertEqual(row['settlement_status'], 'PARTIALLY_REFUNDED')
            self.assertEqual(data['summary']['total_gift_income'], service_tx)
        snapshots.append(data)

        preview = self.ok(staff.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        self.ok(staff.post(f'/api/admin/orders/{order.pk}/refund/', {
            'reason': '隔离退款余款', 'resolution': '隔离测试',
            'client_request_id': 'isolated-gift-refund-final', 'plan_token': preview['plan_token'],
        }, format='json'))
        data = self.ok(provider.get('/api/wallet/provider-benefits/'))
        row = next(row for row in data['gift_records'] if row['id'] == order.pk)
        self.assertEqual((row['settlement_status'], row['received_income'], data['summary']['total_gift_income']),
                         ('REFUNDED', 0, 0))
        transactions = self.ok(provider.get('/api/wallet/transactions/'))['transactions']
        referral = [item for item in transactions if item['is_referral_income']]
        self.assertEqual(sum(item['amount'] for item in referral), 0)
        snapshots.append(data)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for provider wallet rendering')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, '--test', '--test-name-pattern=real gift benefit data',
                                 str(repo / 'apps/provider-miniapp/tests/wallet-realtime.cjs')],
                                env={**os.environ, 'XA_REAL_GIFT_BENEFITS': json.dumps(snapshots)},
                                text=True, encoding='utf-8', capture_output=True, timeout=20, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
