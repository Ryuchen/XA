"""The seven-day deposit clock is based on provable business closure."""
from datetime import timedelta
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from console.deposit_clearance import clearance_evidence, refund_wait_state
from orders.models import AfterSale, Order, OrderProvider, OrderRefund
from orders.tests.factories import make_order, make_provider, make_user
from wallet.models import IncomeHold, Transaction
from wallet.services import get_wallet


class DepositClearanceEvidenceTest(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.provider = make_provider(deposit_paid=1000)
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.account
        self.profile.save(update_fields=['account'])
        self.wallet = get_wallet(user=self.provider, account=self.account)

    def order(self, days=10):
        timestamp = self.now - timedelta(days=days)
        order = make_order(self.boss, provider=self.provider, provider_account=self.account,
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID, completed_at=timestamp)
        share = OrderProvider.objects.create(order=order, provider=self.provider, provider_account=self.account,
            settlement_base=1000, provider_income=800, settled_at=timestamp)
        Order.objects.filter(pk=order.pk).update(created_at=timestamp-timedelta(hours=1))
        OrderProvider.objects.filter(pk=share.pk).update(created_at=timestamp-timedelta(minutes=30))
        before = self.wallet.balance
        self.wallet.balance += 800
        self.wallet.save(update_fields=['balance'])
        income = Transaction.objects.create(wallet=self.wallet, order=order, tx_type='INCOME', amount=800,
                                             balance_before=before, balance_after=self.wallet.balance)
        Transaction.objects.filter(pk=income.pk).update(created_at=timestamp)
        order.refresh_from_db(); share.refresh_from_db()
        return order, share

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '押金清算证据 needs_review=False 须订单正式结算凭证齐备＋收益已释放（18-盘点订单售后资金组合）；最终联调启用')
    def test_last_relevant_case_and_released_income_determine_closure(self):
        order, _ = self.order()
        settled = self.now - timedelta(days=8)
        case = AfterSale.objects.create(order=order, account=self.boss_account, status='RESOLVED',
            reason='协商售后', resolved_at=settled, affected_provider_account_ids=[self.account.pk])
        AfterSale.objects.filter(pk=case.pk).update(created_at=settled-timedelta(hours=1))
        income = Transaction.objects.get(wallet=self.wallet, order=order, tx_type='INCOME')
        last = self.now - timedelta(days=3)
        hold = IncomeHold.objects.create(wallet=self.wallet, income_transaction=income, amount=800,
                                         release_at=last, released_at=last)
        IncomeHold.objects.filter(pk=hold.pk).update(created_at=last-timedelta(hours=1))
        evidence = clearance_evidence(self.profile, self.wallet, now=self.now)
        self.assertFalse(evidence.needs_review)
        self.assertTrue(evidence.has_business)
        self.assertEqual(evidence.last_closed_at, last)

    def test_new_order_and_missing_terminal_evidence_cannot_be_backdated(self):
        self.order()
        new = make_order(self.boss, provider=self.provider, provider_account=self.account,
                         status=Order.Status.IN_SERVICE, payment_status=Order.PaymentStatus.PAID)
        self.assertTrue(clearance_evidence(self.profile, self.wallet, now=self.now).needs_review)
        new.status = Order.Status.COMPLETED
        new.save(update_fields=['status'])
        self.assertTrue(clearance_evidence(self.profile, self.wallet, now=self.now).needs_review)
        new.completed_at = self.now - timedelta(days=1)
        Order.objects.filter(pk=new.pk).update(completed_at=new.completed_at, created_at=new.completed_at-timedelta(hours=1))
        self.assertEqual(clearance_evidence(self.profile, self.wallet, now=self.now).last_closed_at,
                         new.completed_at)

    def test_other_players_scoped_aftersale_does_not_reset_this_clock(self):
        order, _ = self.order()
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        AfterSale.objects.create(order=order, account=self.boss_account, status='RESOLVED',
            reason='其他陪玩', resolved_at=self.now-timedelta(days=1),
            affected_provider_account_ids=[other_account.pk])
        self.assertEqual(clearance_evidence(self.profile, self.wallet, now=self.now).last_closed_at,
                         self.now-timedelta(days=10))

    def test_unknown_or_future_history_is_reviewed_not_treated_as_cleared(self):
        order, _ = self.order()
        case = AfterSale.objects.create(order=order, account=self.boss_account, status='RESOLVED',
            reason='历史记录', resolved_at=None)
        self.assertTrue(clearance_evidence(self.profile, self.wallet, now=self.now).needs_review)
        case.resolved_at = self.now + timedelta(seconds=1)
        case.save(update_fields=['resolved_at'])
        self.assertTrue(clearance_evidence(self.profile, self.wallet, now=self.now).needs_review)

    def test_marked_settled_without_matching_income_must_be_reviewed(self):
        order, _ = self.order()
        income = Transaction.objects.get(wallet=self.wallet, order=order, tx_type='INCOME')
        income.amount = 799
        income.save(update_fields=['amount'])
        self.assertTrue(clearance_evidence(self.profile, self.wallet, now=self.now).needs_review)

    def test_unscoped_historic_refund_restarts_the_wait(self):
        order, _ = self.order()
        refund_time = self.now-timedelta(days=1)
        OrderRefund.objects.create(order=order, amount=100, reason='历史退款', created_at=refund_time)
        self.assertEqual(refund_wait_state(self.profile, self.wallet, now=self.now)['cleared_at'], refund_time)
        self.assertEqual(refund_wait_state(self.profile, self.wallet, now=self.now)['status'], 'WAITING')

    def test_unverified_allocation_does_not_hide_a_refund(self):
        order, _ = self.order()
        other = make_provider()
        account = get_or_create_account_for_legacy_user(other)
        OrderProvider.objects.create(order=order, provider=other, provider_account=account,
                                     settlement_base=0, provider_income=0)
        OrderRefund.objects.create(order=order, amount=100, reason='另一名陪玩售后',
            created_at=self.now-timedelta(days=1), negotiated_allocation=[
                {'role':'PROVIDER', 'account_id':self.account.pk, 'amount':0},
                {'role':'PROVIDER', 'account_id':account.pk, 'amount':100},
            ])
        self.assertEqual(refund_wait_state(self.profile, self.wallet, now=self.now)['status'], 'WAITING')

    def test_cancelled_paid_order_without_settlement_or_refund_evidence_is_reviewed(self):
        cancelled = self.now-timedelta(days=10)
        order = make_order(self.boss, provider=self.provider, provider_account=self.account,
            status=Order.Status.CANCELLED, payment_status=Order.PaymentStatus.PAID,
            amount=1000, cancelled_at=cancelled)
        Order.objects.filter(pk=order.pk).update(created_at=cancelled-timedelta(hours=1))
        self.assertTrue(clearance_evidence(self.profile, self.wallet, now=self.now).needs_review)
        Order.objects.filter(pk=order.pk).update(payment_status=Order.PaymentStatus.REFUNDED)
        self.assertTrue(clearance_evidence(self.profile, self.wallet, now=self.now).needs_review)

    def test_no_business_returns_no_invented_closure_time(self):
        evidence = clearance_evidence(self.profile, self.wallet, now=self.now)
        self.assertFalse(evidence.has_business)
        self.assertFalse(evidence.needs_review)
        self.assertIsNone(evidence.last_closed_at)

    def test_seven_day_boundary_and_later_business_restarts_wait(self):
        self.order(days=7)
        self.assertEqual(refund_wait_state(self.profile, self.wallet, now=self.now)['status'], 'READY')
        self.assertEqual(refund_wait_state(self.profile, self.wallet,
            now=self.now-timedelta(microseconds=1))['status'], 'WAITING')
        new, share = self.order(days=1)
        state = refund_wait_state(self.profile, self.wallet, now=self.now)
        self.assertEqual((state['status'], state['cleared_at'], state['eligible_at']),
                         ('WAITING', share.settled_at, self.now+timedelta(days=6)))


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class DepositClearanceRefundApiTest(APITestCase):
    from wallet.test_deposit_refund_eligibility import DepositRefundEligibilityTest as _base

    def setUp(self):
        self._base.setUp(self)
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user
        staff = make_console_user(['escort:view', 'escort:deposit_refund'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def prepare_closed_order(self, days):
        self.profile = self.provider.escort_profile
        self.now = timezone.now()
        return DepositClearanceEvidenceTest.order(self, days=days)

    def post_refund(self, request_id):
        return self.client.post(self.url, {'amount': 1000, 'reason': '隔离退押金',
                                'client_request_id': request_id}, format='json')

    def test_completed_seven_days_can_refund_with_real_ledger(self):
        self.prepare_closed_order(7)
        self.assertEqual(self.client.get(self.url).data['data']['wait_status'], 'READY')
        response = self.post_refund('clearance-seven-days')
        self.assertEqual(response.data['code'], 0, response.data)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, self.wallet.balance), (2000, 2800))

    def test_new_business_restarts_wait_and_only_after_deadline_can_refund(self):
        self.prepare_closed_order(9)
        DepositClearanceEvidenceTest.order(self, days=1)
        initial = (self.profile.deposit_paid, self.wallet.balance, Transaction.objects.count())
        preview = self.client.get(self.url).data['data']
        self.assertEqual(preview['wait_status'], 'WAITING')
        self.assertEqual(self.post_refund('clearance-early').data['code'], 409)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, self.wallet.balance, Transaction.objects.count()), initial)
        with patch('django.utils.timezone.now', return_value=self.now+timedelta(days=6)):
            self.assertEqual(self.post_refund('clearance-due').data['code'], 0)

    def test_active_order_then_cancellation_starts_a_fresh_wait(self):
        self.prepare_closed_order(9)
        order = make_order(self.boss, provider=self.provider, provider_account=self.account,
            status=Order.Status.GRABBED, payment_status=Order.PaymentStatus.UNPAID, amount=0)
        self.assertEqual(self.client.get(self.url).data['data']['wait_status'], 'BLOCKED')
        self.assertEqual(self.post_refund('while-order-active').data['code'], 409)
        order.status = Order.Status.CANCELLED
        order.cancelled_at = timezone.now()
        order.save(update_fields=['status', 'cancelled_at'])
        preview = self.client.get(self.url).data['data']
        self.assertEqual(preview['wait_status'], 'WAITING')
        self.assertEqual(preview['cleared_at'], order.cancelled_at)
        self.assertEqual(self.post_refund('after-order-closed').data['code'], 409)
        with patch('django.utils.timezone.now', return_value=order.cancelled_at+timedelta(days=7)):
            self.assertEqual(self.post_refund('fresh-wait-complete').data['code'], 0)

    def test_no_business_with_unknown_deposit_payment_time_never_pays(self):
        before = (self.wallet.balance, Transaction.objects.count())
        preview = self.client.get(self.url).data['data']
        self.assertEqual(preview['wait_status'], 'REVIEW')
        self.assertEqual(self.post_refund('clearance-no-business').data['code'], 409)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, Transaction.objects.count()), before)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class CashDepositClearanceApiTest(APITestCase):
    from wallet.test_cash_deposit_topup import CashDepositTopupTest as _base
    setUp = _base.setUp
    topup = _base.post

    def refund_client(self):
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient
        staff = make_console_user(['escort:view', 'escort:deposit_refund'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        return client

    def test_cash_topup_starts_seven_days_and_partial_refunds_keep_original_start(self):
        from wallet.models import CashDepositTopup
        self.assertEqual(self.topup().data['code'], 0)
        row = CashDepositTopup.objects.get(profile=self.profile)
        client = self.refund_client()
        url = f'/api/admin/escorts/{self.profile.pk}/deposit-refund/'
        initial = client.get(url).data['data']
        self.assertEqual((initial['wait_status'], initial['cleared_at']), ('WAITING', row.created_at))
        self.assertEqual(client.post(url, {'amount':10000, 'reason':'押金退还',
            'client_request_id':'cash-early-request'}, format='json').data['code'], 409)
        with patch('django.utils.timezone.now', return_value=row.created_at+timedelta(days=7)):
            for index in (1, 2):
                response = client.post(url, {'amount':10000, 'reason':'押金退还',
                    'client_request_id':f'cash-due-request-{index}'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
            self.profile.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((self.profile.deposit_paid, self.wallet.balance), (20000, 21234))

    def test_cash_topup_without_matching_platform_ledger_requires_review(self):
        from wallet.models import CashDepositTopup
        self.assertEqual(self.topup().data['code'], 0)
        row = CashDepositTopup.objects.get(profile=self.profile)
        Transaction.objects.filter(pk=row.platform_transaction_id).update(amount=row.amount-1)
        client = self.refund_client()
        data = client.get(f'/api/admin/escorts/{self.profile.pk}/deposit-refund/').data['data']
        self.assertEqual(data['wait_status'], 'REVIEW')
        self.assertFalse(data['can_refund'])


class WalletDepositClearanceApiTest(APITestCase):
    from wallet.test_deposit_refund import DepositPayRefundIdentityTest as _base
    setUp = _base.setUp

    def test_wallet_deposit_uses_last_completion_snapshot_and_refunds_after_seven_days(self):
        import json
        from wallet.club_rules import DEPOSIT_TARGET_KEY
        from wallet.models import DepositPaymentSnapshot, SystemConfig
        SystemConfig.objects.create(key=DEPOSIT_TARGET_KEY, value=json.dumps(
            {'cash_target':3000, 'earnings_target':80000, 'version':1}))
        self.client.force_authenticate(self.provider)
        for amount in (1000, 2000):
            self.assertEqual(self.client.post('/api/wallet/deposit/', {'amount':amount,
                'client_request_id':f'wallet-deposit-{amount}'}).data['code'], 0)
        rows = list(DepositPaymentSnapshot.objects.filter(profile=self.provider.escort_profile).order_by('pk'))
        self.assertEqual([(row.paid_before, row.paid_after, row.target_snapshot) for row in rows],
                         [(0, 1000, 3000), (1000, 3000, 3000)])
        self.client.force_authenticate(self.admin)
        url = f'/api/admin/escorts/{self.provider.escort_profile.pk}/deposit-refund/'
        self.assertEqual(self.client.get(url).data['data']['wait_status'], 'WAITING')
        with patch('django.utils.timezone.now', return_value=rows[-1].created_at+timedelta(days=7)):
            for index, amount in enumerate((1000, 2000)):
                result = self.client.post(url, {'amount':amount, 'reason':'押金退还',
                    'client_request_id':f'wallet-due-request-{index}'}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.deposit_paid, 0)

    def test_missing_snapshot_schema_never_debits_wallet(self):
        self.client.force_authenticate(self.provider)
        wallet = get_wallet(account=self.account)
        before = (wallet.balance, Transaction.objects.count())
        with patch('wallet.deposit_payment.snapshot_storage_available', return_value=False):
            result = self.client.post('/api/wallet/deposit/', {'amount':1000, 'client_request_id':'deposit-no-schema'})
        self.assertEqual(result.status_code, 503)
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, Transaction.objects.count()), before)

    def test_snapshot_failure_rolls_back_wallet_and_both_ledgers(self):
        self.client.force_authenticate(self.provider)
        wallet = get_wallet(account=self.account)
        before = (wallet.balance, Transaction.objects.count())
        with patch('wallet.views.DepositPaymentSnapshot.objects.create', side_effect=RuntimeError('snapshot failure')):
            with self.assertRaisesMessage(RuntimeError, 'snapshot failure'):
                self.client.post('/api/wallet/deposit/', {'amount':1000, 'client_request_id':'deposit-snapshot-fail'})
        wallet.refresh_from_db(); self.provider.escort_profile.refresh_from_db()
        self.assertEqual((wallet.balance, Transaction.objects.count(), self.provider.escort_profile.deposit_paid),
                         (*before, 0))


@skipUnlessDBFeature('has_select_for_update')
class DepositClearanceRefundRaceTest(TransactionTestCase):
    def test_two_due_refunds_cannot_both_spend_same_deposit(self):
        from console.deposit_refund import refund_deposit
        from orders.tests.factories import make_superuser, make_wallet
        from wallet.test_deposit_refund import bind_account, fund_platform_deposit_pool, seed_closed_order
        admin = make_superuser()
        admin_account = get_or_create_account_for_legacy_user(admin)
        provider = make_provider(deposit_paid=3000)
        account = bind_account(provider)
        wallet = make_wallet(provider, balance=0)
        wallet.account = account; wallet.save(update_fields=['account'])
        fund_platform_deposit_pool(3000)
        seed_closed_order(provider, account)
        profile_id = provider.escort_profile.pk
        gate = Barrier(2)

        class Request:
            pass

        def attempt(number):
            close_old_connections()
            try:
                request = Request(); request.account = admin_account; request.legacy_user = admin
                gate.wait(timeout=10)
                return refund_deposit(request, profile_id, 2000, '并发隔离测试', f'concurrent-{number}')[0]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(attempt, (1, 2)))
        self.assertEqual(sorted(codes), [0, 400])
        wallet.refresh_from_db(); provider.escort_profile.refresh_from_db()
        self.assertEqual((wallet.balance, provider.escort_profile.deposit_paid), (2000, 1000))
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='DEPOSIT', amount=2000).count(), 1)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
class DepositClearanceStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_profile_id_uses_only_its_store_clearance_facts(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        profiles = []
        for store, days in ((resolve_store(), 10), (xinan_store(), 1)):
            with store_context(store):
                DepositClearanceEvidenceTest.setUp(self)
                DepositClearanceEvidenceTest.order(self, days=days)
                profiles.append((store, self.profile.pk, self.wallet.pk, self.now))
        self.assertEqual(profiles[0][1], profiles[1][1])
        for (store, profile_id, wallet_id, now), expected in zip(profiles, ('READY', 'WAITING')):
            with store_context(store):
                from users.models import EscortProfile
                from wallet.models import Wallet
                self.assertEqual(refund_wait_state(EscortProfile.objects.get(pk=profile_id),
                    Wallet.objects.get(pk=wallet_id), now=now)['status'], expected)

    def test_same_profile_id_uses_only_its_store_deposit_completion(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.models import DepositPaymentSnapshot, get_platform_wallet
        profiles = []
        for store, days in ((resolve_store(), 10), (xinan_store(), 1)):
            with store_context(store):
                DepositClearanceEvidenceTest.setUp(self)
                paid = self.now-timedelta(days=days)
                platform = get_platform_wallet()
                self.wallet.balance = 0; self.wallet.save(update_fields=['balance'])
                platform.balance = 1000; platform.save(update_fields=['balance'])
                debit = Transaction.objects.create(wallet=self.wallet, tx_type='DEPOSIT', amount=-1000,
                    balance_before=1000, balance_after=0)
                credit = Transaction.objects.create(wallet=platform, tx_type='DEPOSIT_INCOME', amount=1000,
                    balance_before=0, balance_after=1000)
                receipt = DepositPaymentSnapshot.objects.create(profile=self.profile, account=self.account,
                    provider_transaction=debit, platform_transaction=credit, amount=1000,
                    paid_before=0, paid_after=1000, target_snapshot=1000)
                Transaction.objects.filter(pk__in=[debit.pk, credit.pk]).update(created_at=paid)
                DepositPaymentSnapshot.objects.filter(pk=receipt.pk).update(created_at=paid)
                profiles.append((store, self.profile.pk, self.wallet.pk, self.now))
        self.assertEqual(profiles[0][1], profiles[1][1])
        for (store, profile_id, wallet_id, now), expected in zip(profiles, ('READY', 'WAITING')):
            with store_context(store):
                from users.models import EscortProfile
                from wallet.models import Wallet
                self.assertEqual(refund_wait_state(EscortProfile.objects.get(pk=profile_id),
                    Wallet.objects.get(pk=wallet_id), now=now)['status'], expected)
