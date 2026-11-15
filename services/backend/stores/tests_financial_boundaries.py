"""Reverse API matrix with deliberately identical IDs in two real databases.

Fixtures describe already-paid orders and existing balances; refund, recharge,
withdrawal, coupon claim and point redemption all execute their real API code.
No financial, permission or tenancy implementation is mocked.
"""
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole
from coupons.models import Coupon, UserCoupon
from engagement.models import MemberProfile, PointLedger, RewardClaim, RewardItem
from orders.models import Order, OrderRefund
from orders.tests.factories import make_order, make_service
from orders.tests.test_release_flow import screenshot
from wallet.models import RechargeRecord, Transaction, Wallet, WithdrawalAccount, WithdrawRequest

from .context import resolve_store, store_context
from .db import transaction
from .models import Store


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   REQUIRE_RECHARGE_PROOF=True, CELERY_TASK_ALWAYS_EAGER=False)
class StoreFinancialReverseMatrixTest(APITestCase):
    databases = {'default', 'store_b'}
    BOSS = 610001
    PROVIDER = 610002
    STAFF = 610003
    ROLE = 610004
    BOSS_WALLET = 610011
    PROVIDER_WALLET = 610012
    SERVICE = 610101
    ORDER = 610201
    PAYEE = 610301
    COUPON = 610401
    POINT_ENTRY = 610501
    PAYMENT = 610601
    REWARD = 610701
    FINANCE = {'salary:view', 'cost:view', 'roi:view'}
    BASE = {'wallet:view', 'wallet:recharge', 'order:view', 'order:refund',
            'withdraw:view', 'withdraw:audit', 'coupon:view'}

    def setUp(self):
        media = TemporaryDirectory(prefix='xa-store-financial-matrix-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.a = resolve_store()
        self.b = Store.objects.create(slug='financial-b', name='第二门店', database_alias='store_b',
                                      status=Store.Status.ACTIVE, initialized_at=timezone.now())
        self.stores = (self.a, self.b)
        self.accounts = {}
        self.amounts = {self.a.slug: 10000, self.b.slug: 16000}
        self.point_balances = {self.a.slug: 100000, self.b.slug: 200000}
        for store in self.stores:
            with store_context(store):
                boss = ClubAccount.objects.create_account(pk=self.BOSS, username='matrix-boss',
                    account_type='BOSS', nickname=store.name + '老板')
                provider = ClubAccount.objects.create_account(pk=self.PROVIDER, username='matrix-provider',
                    account_type='PROVIDER', nickname=store.name + '陪玩')
                staff_user = get_user_model().objects.create_user(pk=self.STAFF,
                    username='matrix-admin', role='ADMIN', password='matrix-admin-test-only')
                staff = ClubAccount.objects.create_account(pk=self.STAFF, username='matrix-admin',
                    account_type='STAFF', nickname=store.name + '管理员')
                LegacyAccountMap.objects.create(legacy_user_id=staff_user.pk, account=staff, legacy_role='ADMIN')
                role = AdminRole.objects.create(pk=self.ROLE, code='matrix-admin', name='矩阵管理员',
                    permissions=sorted(self.BASE | (self.FINANCE if store == self.a else set())))
                membership = AdminMembership.objects.create(user=staff_user, account=staff)
                membership.roles.add(role)
                amount = self.amounts[store.slug]
                wallet = Wallet.objects.create(pk=self.BOSS_WALLET, account=boss, balance=50000 - amount)
                Wallet.objects.create(pk=self.PROVIDER_WALLET, account=provider, balance=50000)
                service = make_service(pk=self.SERVICE, name=store.name + '服务', price=amount)
                order = make_order(None, pk=self.ORDER, customer_account=boss, service=service,
                    amount=amount, payment_status='PAID', status='PENDING', dispatch_route='STAFF')
                Transaction.objects.create(pk=self.PAYMENT, wallet=wallet, order=order,
                    tx_type='PAY', amount=-amount, balance_before=50000, balance_after=50000 - amount,
                    remark=store.name + '已支付订单')
                WithdrawalAccount.objects.create(pk=self.PAYEE, account=provider, payee_method='WECHAT',
                    payee_account=store.slug + '-synthetic-payee', payee_name=store.name + '测试收款人')
                Coupon.objects.create(pk=self.COUPON, name=store.name + '优惠券', amount=100,
                    discount_type='DIRECT', threshold=0, total_qty=10)
                points = self.point_balances[store.slug]
                MemberProfile.objects.create(account=boss, points_balance=points)
                PointLedger.objects.create(pk=self.POINT_ENTRY, account=boss,
                    event_key='same-store-local-points-key', kind='ADJUST', amount=points,
                    balance_after=points, remark=store.name + '积分期初')
                RewardItem.objects.create(pk=self.REWARD, code='matrix-reward', name=store.name + '测试权益',
                    kind='VIRTUAL', point_cost=100, stock=1, is_active=True)
                self.accounts[store.slug] = {'boss': boss, 'provider': provider, 'admin': staff}

    def client_for(self, store, role='boss', *, destination=None):
        with store_context(store):
            token, _ = issue_account_tokens(self.accounts[store.slug][role])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token,
                           HTTP_X_STORE_SLUG=(destination or store).slug)
        return client

    def ok(self, response):
        self.assertIn(response.status_code, (200, 201), getattr(response, 'data', response.content))
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def snapshot(self, store):
        with store_context(store):
            return {
                'wallets': list(Wallet.objects.order_by('pk').values_list('pk', 'account_id', 'balance', 'frozen_amount', 'total_recharge')),
                'transactions': list(Transaction.objects.order_by('pk').values_list('pk', 'wallet_id', 'order_id', 'amount', 'tx_type')),
                'orders': list(Order.objects.order_by('pk').values_list('pk', 'status', 'payment_status', 'amount')),
                'refunds': list(OrderRefund.objects.values_list('order_id', 'amount')),
                'withdrawals': list(WithdrawRequest.objects.values_list('account_id', 'amount', 'status')),
                'recharges': list(RechargeRecord.objects.values_list('account_id', 'amount')),
                'coupons': list(UserCoupon.objects.values_list('account_id', 'coupon_id', 'status')),
                'points': list(MemberProfile.objects.values_list('account_id', 'points_balance')),
                'claims': list(RewardClaim.objects.values_list('account_id', 'name_snapshot')),
            }

    def test_foreign_signed_roles_cannot_write_any_financial_surface_in_either_direction(self):
        writes = (
            ('boss', '/api/orders/create/', {'service_id': self.SERVICE}),
            ('admin', f'/api/admin/orders/{self.ORDER}/refund/', {'reason': '隔离边界验证'}),
            ('admin', '/api/admin/wallets/recharge/', {'account_id': self.BOSS, 'amount': 100}),
            ('provider', '/api/wallet/withdraw/', {'amount': 10000, 'withdrawal_account_id': self.PAYEE,
                                                'expected_payee_version': 1}),
            ('boss', f'/api/coupons/{self.COUPON}/claim/', {}),
            ('boss', '/api/club/redeem/', {'reward_id': self.REWARD, 'client_request_id': 'same-redeem'}),
            ('admin', '/api/admin/payroll/confirm/', {'month': '2026-11'}),
        )
        before = {store.slug: self.snapshot(store) for store in self.stores}
        for source, destination in ((self.a, self.b), (self.b, self.a)):
            for role, path, data in writes:
                with self.subTest(source=source.slug, destination=destination.slug, path=path):
                    response = self.client_for(source, role, destination=destination).post(path, data, format='json')
                    self.assertEqual(response.status_code, 401, getattr(response, 'data', response.content))
        for store in self.stores:
            self.assertEqual(self.snapshot(store), before[store.slug])

    def test_same_order_and_wallet_ids_refund_and_recharge_only_the_selected_store(self):
        for store, other in ((self.a, self.b), (self.b, self.a)):
            before_other = self.snapshot(other)
            owner = self.client_for(store)
            self.assertEqual(self.ok(owner.get(f'/api/orders/orders/{self.ORDER}/'))['amount'], self.amounts[store.slug])
            invoice = self.ok(owner.get('/api/wallet/transactions/'))
            self.assertIn(store.name + '已支付订单', str(invoice))
            self.assertNotIn(other.name + '已支付订单', str(invoice))
            admin = self.client_for(store, 'admin')
            self.ok(admin.post('/api/admin/wallets/recharge/', {
                'account_id': self.BOSS, 'amount': 1000, 'gift_amount': 0,
                'trade_no': 'same-receipt-in-separate-stores', 'proof_image': screenshot(),
                'client_request_id': 'same-recharge-key',
            }, format='multipart'))
            preview = self.ok(admin.get(f'/api/admin/orders/{self.ORDER}/refund-preview/'))
            self.ok(admin.post(f'/api/admin/orders/{self.ORDER}/refund/', {
                'reason': '本店隔离退款', 'plan_token': preview['plan_token'],
                'client_request_id': 'same-refund-key',
            }, format='json'))
            with store_context(store):
                self.assertEqual(Wallet.objects.get(pk=self.BOSS_WALLET).balance, 51000)
                self.assertEqual(OrderRefund.objects.get(order_id=self.ORDER).amount, self.amounts[store.slug])
                self.assertEqual(RechargeRecord.objects.get(account_id=self.BOSS).amount, 1000)
                self.assertEqual(Order.objects.get(pk=self.ORDER).payment_status, 'REFUNDED')
            self.assertEqual(self.snapshot(other), before_other)

    def test_identical_payee_ids_and_request_keys_freeze_only_the_local_provider_wallet(self):
        for store, other in ((self.a, self.b), (self.b, self.a)):
            before_other = self.snapshot(other)
            client = self.client_for(store, 'provider')
            payload = {
                'amount': 10000, 'withdrawal_account_id': self.PAYEE, 'expected_payee_version': 1,
                'client_request_id': 'same-withdraw-key',
            }
            created = self.ok(client.post('/api/wallet/withdraw/', payload, format='json'))
            with store_context(store):
                wallet = Wallet.objects.get(pk=self.PROVIDER_WALLET)
                self.assertEqual((wallet.balance, wallet.frozen_amount), (40000, 10000))
                withdrawal = WithdrawRequest.objects.get(account_id=self.PROVIDER)
                self.assertIsNone(withdrawal.user_id)
                self.assertEqual(withdrawal.payee_account, store.slug + '-synthetic-payee')
                self.assertEqual(withdrawal.amount, 10000)
                self.assertIn('matrix-provider', str(withdrawal))
            before_replay = self.snapshot(store)
            replay = client.post('/api/wallet/withdraw/', payload, format='json')
            self.assertEqual(replay.data['code'], 409, replay.data)
            self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
            self.assertEqual(replay.data['fundResult']['data']['request']['id'], created['request']['id'])
            self.assertEqual(self.snapshot(store), before_replay)
            admin = self.client_for(store, 'admin')
            listing = self.ok(admin.get('/api/admin/withdrawals/', {'keyword': 'matrix-provider'}))['list']
            self.assertEqual([row['id'] for row in listing], [withdrawal.pk])
            self.assertIsNone(listing[0]['user'])
            claimed = self.ok(admin.post(f'/api/admin/withdrawals/{withdrawal.pk}/claim/', {}, format='json'))
            rejection = {'audit_remark': '隔离测试驳回，不涉及真实打款', 'expected_claimed_at': claimed['claimed_at']}
            self.ok(admin.post(f'/api/admin/withdrawals/{withdrawal.pk}/reject/', rejection, format='json'))
            with store_context(store):
                wallet.refresh_from_db()
                withdrawal.refresh_from_db()
                self.assertEqual((wallet.balance, wallet.frozen_amount), (50000, 0))
                self.assertEqual(withdrawal.status, 'REJECTED')
                self.assertEqual(withdrawal.transaction.status, 'FAILED')
                self.assertEqual(WithdrawRequest.objects.filter(account_id=self.PROVIDER).count(), 1)
            restored = self.snapshot(store)
            self.assertNotEqual(admin.post(f'/api/admin/withdrawals/{withdrawal.pk}/reject/', rejection, format='json').data['code'], 0)
            self.assertEqual(self.snapshot(store), restored)
            self.assertEqual(self.snapshot(other), before_other)

    def test_account_only_approval_records_tax_and_cannot_approve_twice(self):
        from wallet.models import get_platform_wallet
        for store, other in ((self.a, self.b), (self.b, self.a)):
            before_other = self.snapshot(other)
            with store_context(store):
                tax_before = get_platform_wallet().balance
            created = self.ok(self.client_for(store, 'provider').post('/api/wallet/withdraw/', {
                'amount': 10000, 'withdrawal_account_id': self.PAYEE, 'expected_payee_version': 1,
                'client_request_id': 'same-native-approval-key',
            }, format='json'))
            pk = created['request']['id']
            admin = self.client_for(store, 'admin')
            claimed = self.ok(admin.post(f'/api/admin/withdrawals/{pk}/claim/', {}, format='json'))
            # This API records a synthetic manual payout reference; it does
            # not call a payment provider or move any real-world money.
            data = {'payout_reference': 'MATRIX-TEST-NO-EXTERNAL-TRANSFER',
                    'expected_claimed_at': claimed['claimed_at']}
            self.ok(admin.post(f'/api/admin/withdrawals/{pk}/approve/', data, format='json'))
            with store_context(store):
                wallet = Wallet.objects.get(pk=self.PROVIDER_WALLET)
                withdrawal = WithdrawRequest.objects.get(pk=pk)
                self.assertIsNone(withdrawal.user_id)
                self.assertEqual((wallet.balance, wallet.frozen_amount), (40000, 0))
                self.assertEqual(withdrawal.status, 'APPROVED')
                self.assertEqual(withdrawal.transaction.status, 'SUCCESS')
                self.assertEqual(get_platform_wallet().balance, tax_before + withdrawal.tax_amount)
                self.assertEqual(withdrawal.actual_amount + withdrawal.tax_amount, withdrawal.amount)
                self.assertEqual(Transaction.objects.filter(tx_type='WITHDRAW_TAX').count(), 1)
            final = self.snapshot(store)
            self.assertNotEqual(admin.post(f'/api/admin/withdrawals/{pk}/approve/', data, format='json').data['code'], 0)
            self.assertEqual(self.snapshot(store), final)
            self.assertEqual(self.snapshot(other), before_other)

    def test_withdrawal_cannot_exist_without_either_business_or_historical_owner(self):
        for store in self.stores:
            with store_context(store), self.assertRaises(IntegrityError), transaction.atomic():
                WithdrawRequest.objects.create(amount=10000, payee_method='WECHAT',
                    payee_account='synthetic-orphan', payee_name='隔离测试')

    def test_coupon_claim_and_point_redemption_use_the_local_same_id_catalog(self):
        for store, other in ((self.a, self.b), (self.b, self.a)):
            before_other = self.snapshot(other)
            client = self.client_for(store)
            self.ok(client.post(f'/api/coupons/{self.COUPON}/claim/', {}, format='json'))
            coupons = self.ok(client.get('/api/coupons/mine/'))
            self.assertIn(store.name + '优惠券', str(coupons))
            self.assertNotIn(other.name + '优惠券', str(coupons))
            points = self.ok(client.get('/api/club/points/'))
            self.assertEqual(points['points'], self.point_balances[store.slug] / 100)
            self.assertIn(store.name + '积分期初', str(points))
            self.assertNotIn(other.name + '积分期初', str(points))
            claim = self.ok(client.post('/api/club/redeem/', {
                'reward_id': self.REWARD, 'expected_point_cost': 100,
                'client_request_id': 'same-point-redemption-key',
            }, format='json'))
            self.assertIn(store.name + '测试权益', str(claim))
            with store_context(store):
                self.assertEqual(MemberProfile.objects.get(account_id=self.BOSS).points_balance,
                                 self.point_balances[store.slug] - 10000)
                self.assertEqual(RewardClaim.objects.filter(account_id=self.BOSS).count(), 1)
            self.assertEqual(self.snapshot(other), before_other)

    def test_same_staff_id_has_opposite_finance_permissions_and_live_changes_stay_local(self):
        paths = ('/api/admin/salary-plans/', '/api/admin/operating-costs/',
                 '/api/admin/payroll/preview/?month=2026-11',
                 '/api/admin/operating-roi/?start_date=2026-11-01&end_date=2026-11-04')
        for permitted, denied in ((self.a, self.b), (self.b, self.a)):
            for store, allowed in ((permitted, True), (denied, False)):
                with store_context(store):
                    AdminRole.objects.filter(pk=self.ROLE).update(
                        permissions=sorted(self.BASE | (self.FINANCE if allowed else set())))
            for store, allowed in ((permitted, True), (denied, False)):
                client = self.client_for(store, 'admin')
                for path in paths:
                    with self.subTest(store=store.slug, path=path, permitted=allowed):
                        response = client.get(path)
                        self.assertEqual(response.status_code, 200 if allowed else 403,
                                         getattr(response, 'data', response.content))
