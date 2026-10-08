"""跨菜单资金闭环与后台任务的过时状态回归。"""
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.permissions import ALL_PERMISSIONS
from orders.models import Order
from orders.tasks import auto_cancel_pending_order
from orders.state_machine import transition
from orders.tests.factories import make_console_user, make_provider, make_user, make_order, make_wallet
from users.models import ProviderPassPurchase, Achievement
from wallet.models import Transaction, RechargeRecord, get_platform_wallet, SystemConfig
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=True)
class BusinessClosureTest(APITestCase):
    def setUp(self):
        self.staff = make_console_user(ALL_PERMISSIONS)
        self.provider = make_provider(deposit_required=1000)
        self.boss = make_user()
        self.accounts = {}
        for user in (self.staff, self.provider, self.boss):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            self.accounts[user.pk] = account
        membership = self.staff.admin_membership
        membership.account = self.accounts[self.staff.pk]
        membership.save(update_fields=['account'])
        profile = self.provider.escort_profile
        profile.account = self.accounts[self.provider.pk]
        profile.save(update_fields=['account'])
        self.provider_client = self.login(self.provider)
        self.client = self.login(self.staff)
        self.media = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media.cleanup)
        self.addCleanup(self.media_override.disable)

    def login(self, user):
        client = APIClient()
        token, _ = issue_account_tokens(self.accounts[user.pk])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        return client

    def ok(self, result):
        self.assertIn(result.status_code, (200, 201), result.data)
        self.assertEqual(result.data['code'], 0, result.data)
        return result.data.get('data')

    def recharge_payload(self, user=None):
        content = BytesIO()
        Image.new('RGB', (10, 10), 'green').save(content, format='PNG')
        return {
            'account_id': self.accounts[(user or self.provider).pk].pk,
            'amount': 10000, 'trade_no': 'CLOSURE-RECEIPT-001',
            'client_request_id': 'closure-topup',
            'proof_image': SimpleUploadedFile('receipt.png', content.getvalue(), content_type='image/png'),
        }

    @override_settings(REQUIRE_CLIENT_REQUEST_ID=True, REQUIRE_RECHARGE_PROOF=True)
    def test_provider_recharge_deposit_pass_and_admin_records_close(self):
        self.ok(self.client.post('/api/admin/wallets/recharge/', self.recharge_payload(), format='multipart'))
        rows = self.ok(self.client.get('/api/admin/wallets/', {'account_type': 'PROVIDER'}))['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['boss_no'], self.accounts[self.provider.pk].account_no)
        self.assertEqual(rows[0]['balance'], 10000)
        self.assertNotEqual(self.client.post('/api/admin/wallets/recharge/', self.recharge_payload(), format='multipart').data['code'], 0)
        platform_before = get_platform_wallet().balance
        self.ok(self.provider_client.post('/api/wallet/deposit/', {'amount': 1000, 'client_request_id': 'closure-deposit'}, format='json'))
        payload = {'tier': 'BRONZE', 'days': 1, 'client_request_id': 'closure-pass'}
        self.ok(self.provider_client.post('/api/users/provider-pass/', payload, format='json'))
        self.assertEqual(self.provider_client.post('/api/users/provider-pass/', payload, format='json').data['code'], 409)
        wallet = get_wallet(account=self.accounts[self.provider.pk])
        self.assertEqual(wallet.balance, 8800)
        self.assertEqual(get_platform_wallet().balance - platform_before, 1200)
        purchase = ProviderPassPurchase.objects.get(provider=self.provider)
        platform_tx = Transaction.objects.get(tx_type='PASS_INCOME', external_tx_id=purchase.transaction.tx_no)
        self.assertEqual(platform_tx.amount + purchase.transaction.amount, 0)
        rows = self.ok(self.client.get('/api/admin/pass-purchases/', {'keyword': self.accounts[self.provider.pk].account_no}))['list']
        self.assertEqual(rows[0]['tx_no'], purchase.transaction.tx_no)
        self.assertEqual(RechargeRecord.objects.filter(user=self.provider).count(), 1)

    def test_recharge_rejects_staff_and_missing_proof(self):
        result = self.client.post('/api/admin/wallets/recharge/', self.recharge_payload(self.staff), format='multipart')
        self.assertEqual(result.status_code, 403)
        self.assertEqual(RechargeRecord.objects.count(), 0)
        payload = self.recharge_payload()
        payload.pop('proof_image')
        self.assertEqual(self.client.post('/api/admin/wallets/recharge/', payload).data['code'], 400)

    def test_provider_wallet_is_not_exposed_as_boss_and_cannot_be_adjusted(self):
        rows = self.ok(self.client.get('/api/admin/wallets/'))['list']
        self.assertFalse(any(row['user'] == self.provider.pk for row in rows))
        self.assertEqual(self.client.get('/api/admin/wallets/?account_type=STAFF').status_code, 400)

    def test_pass_explicit_renewal_and_failed_attempt_can_retry(self):
        payload = {'tier': 'BLACK', 'days': 1, 'client_request_id': 'insufficient-first'}
        self.assertEqual(self.provider_client.post('/api/users/provider-pass/', payload).data['code'], 400)
        make_wallet(self.provider, balance=20000)
        self.ok(self.provider_client.post('/api/users/provider-pass/', payload))
        first = ProviderPassPurchase.objects.get(provider=self.provider)
        self.ok(self.provider_client.post('/api/users/provider-pass/', {**payload, 'client_request_id': 'intentional-renewal'}))
        second = ProviderPassPurchase.objects.filter(provider=self.provider).first()
        self.assertEqual(second.starts_at, first.expires_at)
        self.assertEqual((second.expires_at - first.expires_at).total_seconds(), 86400)

    def test_pass_old_client_double_click_is_blocked(self):
        make_wallet(self.provider, balance=20000)
        payload = {'tier': 'BLACK', 'days': 1}
        self.ok(self.provider_client.post('/api/users/provider-pass/', payload))
        self.assertEqual(self.provider_client.post('/api/users/provider-pass/', payload).data['code'], 409)
        self.assertEqual(ProviderPassPurchase.objects.filter(provider=self.provider).count(), 1)

    def test_pass_rejects_fractional_days_and_changed_price(self):
        make_wallet(self.provider, balance=20000)
        for extra in ({'days': 1.9}, {'days': True}, {'expected_daily_price': 1}):
            response = self.provider_client.post('/api/users/provider-pass/', {'tier': 'BLACK', **extra}, format='json')
            self.assertEqual(response.data['code'], 400)
        self.assertFalse(ProviderPassPurchase.objects.exists())

    def test_pass_config_validation_permission_and_purchase_snapshot(self):
        url = '/api/admin/config/provider-pass'
        config = self.ok(self.client.get(url))
        config['products'][0]['daily_price'] = 1200
        self.ok(self.client.put(url, config, format='json'))
        make_wallet(self.provider, balance=5000)
        self.ok(self.provider_client.post('/api/users/provider-pass/', {'tier': 'BLACK', 'client_request_id': 'priced-pass'}))
        config['products'][0]['daily_price'] = 1500
        self.ok(self.client.put(url, config, format='json'))
        self.assertEqual(ProviderPassPurchase.objects.get(provider=self.provider).paid_amount, 1200)
        self.assertEqual(self.ok(self.provider_client.get('/api/users/provider-pass/'))['products'][0]['daily_price'], 1500)
        config['products'][0]['delay_seconds'] = 500
        self.assertEqual(self.client.put(url, config, format='json').status_code, 400)
        self.assertEqual(SystemConfig.objects.get(key='provider_pass_delay_seconds_BLACK').value, '0')
        role = self.staff.admin_membership.roles.get()
        role.permissions = ['escort:view']
        role.save()
        self.ok(self.client.get(url))
        self.assertEqual(self.client.put(url, config, format='json').status_code, 403)
        self.assertEqual(self.provider_client.get(url).status_code, 403)

    def test_pass_failure_rolls_back_both_wallets_and_purchase(self):
        make_wallet(self.provider, balance=10000)
        before = get_platform_wallet().balance
        with patch('users.views.ProviderPassPurchase.objects.create', side_effect=RuntimeError('simulate storage failure')):
            with self.assertRaises(RuntimeError):
                self.provider_client.post('/api/users/provider-pass/', {'tier': 'BLACK', 'client_request_id': 'rollback'})
        self.assertEqual(get_wallet(account=self.accounts[self.provider.pk]).balance, 10000)
        self.assertEqual(get_platform_wallet().balance, before)
        self.assertFalse(Transaction.objects.filter(tx_type__in=['PASS_INCOME', 'PASS_PURCHASE']).exists())
        self.ok(self.provider_client.post('/api/users/provider-pass/', {'tier': 'BLACK', 'client_request_id': 'rollback'}))

    def expired_order(self, **kwargs):
        return make_order(self.boss, customer_account=self.accounts[self.boss.pk],
                          auto_cancel_at=timezone.now() - timedelta(minutes=1), **kwargs)

    def test_timeout_cancels_and_refunds_only_once(self):
        order = self.expired_order(payment_status='PAID')
        before = get_wallet(account=self.accounts[self.boss.pk]).balance
        self.assertEqual(auto_cancel_pending_order(order.pk), 'cancelled')
        auto_cancel_pending_order(order.pk)
        self.assertEqual(get_wallet(account=self.accounts[self.boss.pk]).balance, before + order.amount)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)

    def test_unpaid_timeout_never_credits_customer(self):
        order = self.expired_order(payment_status='UNPAID')
        self.assertEqual(auto_cancel_pending_order(order.pk), 'cancelled')
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())

    def test_timeout_rechecks_locked_order_after_scan(self):
        for update in ({'status': 'GRABBED', 'auto_cancel_at': None},
                       {'auto_cancel_at': timezone.now() + timedelta(minutes=30)}):
            with self.subTest(update=update):
                order = self.expired_order(payment_status='PAID')
                def changed_before_lock(*args, **kwargs):
                    Order.objects.filter(pk=order.pk).update(**update)
                    return transition(*args, **kwargs)
                with patch('orders.state_machine.transition', side_effect=changed_before_lock):
                    self.assertEqual(auto_cancel_pending_order(order.pk), 'skip')
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())
                order.refresh_from_db()
                self.assertNotEqual(order.status, 'CANCELLED')

    def test_missing_timeout_deadline_is_not_cancelled(self):
        order = make_order(self.boss, auto_cancel_at=None)
        self.assertEqual(auto_cancel_pending_order(order.pk), 'skip')
        order.refresh_from_db()
        self.assertEqual(order.status, 'PENDING')

    def test_achievement_provides_metric_for_correct_frontend_units(self):
        Achievement.objects.create(code='closure-amount', title='消费成就', metric='amount', target=10000)
        rows = self.ok(self.login(self.boss).get('/api/users/achievements/'))
        item = next(row for row in rows if row['code'] == 'closure-amount')
        self.assertEqual(item['metric'], 'amount')
        self.assertEqual(item['target'], 10000)

    def test_wallet_income_covers_history_and_pagination_has_no_missing_rows(self):
        wallet = get_wallet(account=self.accounts[self.provider.pk])
        for index in range(55):
            Transaction.objects.create(wallet=wallet, amount=100, tx_type='INCOME',
                                       balance_before=index * 100, balance_after=(index + 1) * 100)
        Transaction.objects.create(wallet=wallet, amount=999, tx_type='INCOME', status='FAILED')
        Transaction.objects.create(wallet=wallet, amount=888, tx_type='TOPUP')
        first = self.ok(self.provider_client.get('/api/wallet/transactions/?page_size=50'))
        second = self.ok(self.provider_client.get('/api/wallet/transactions/?page_size=50&page=2'))
        self.assertEqual(first['total_income'], 5500)
        self.assertEqual(second['total_income'], 5500)
        ids = [item['id'] for item in first['transactions'] + second['transactions']]
        self.assertEqual(len(set(ids)), 57)
        self.assertEqual(len(ids), 57)

    def test_provider_support_card_has_own_account_reference(self):
        from support.models import SupportContactCard
        SupportContactCard.objects.create(name='充值客服', wechat_id='club-support', sort_order=0)
        data = self.ok(self.provider_client.get('/api/support/contact-card/'))
        self.assertEqual(data['customer_reference'], self.accounts[self.provider.pk].account_no)
        self.assertEqual(data['wechat_id'], 'club-support')
