"""全店闭店义务只读扫描的真实模型与隔离测试。"""
from datetime import date, timedelta
import re
from unittest.mock import patch

from django.db import DatabaseError, connections
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from console.payroll_models import PayrollRun
from engagement.models import MemberBenefitGrant, RewardClaim, RewardItem, VipTier
from orders.models import (
    AfterSale, Order, OrderContinuation, OrderContinuationReward, OrderProvider,
    OrderRefund, Reservation,
)
from orders.tests.factories import make_boss_type, make_order, make_provider, make_service
from stores.closure_obligations import closure_obligation_readiness
from stores.context import resolve_store, store_context
from stores.models import Store
from wallet.models import (
    IncomeHold, ProviderReport, RechargeGiftApproval, RechargeRecord, Transaction,
    Wallet, WithdrawRequest,
)
from wallet.refund_deposit_models import RefundDepositDeficit, RefundDepositRecoveryPermit


CODES = (
    'AFTERSALES', 'DEPOSIT_DEFICITS', 'GIFT_APPROVALS', 'INCOME_HOLDS',
    'ORDERS', 'OTHER_RIGHTS', 'PAYROLL', 'RECOVERY_PERMITS', 'REPORTS',
    'RESERVATIONS', 'REWARD_CLAIMS', 'WITHDRAWALS',
)
RELIABLE_CODES = frozenset(CODES) - {'OTHER_RIGHTS', 'PAYROLL'}


class ClosureObligationReadinessTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.store = resolve_store()
        self.branch = Store.objects.create(
            slug='closure-branch-b', name='闭店扫描第二门店', database_alias='store_b',
            status=Store.Status.ACTIVE, initialized_at=timezone.now(),
        )
        self.sequence = 0

    def account(self, kind='BOSS', *, pk=None, username=None):
        self.sequence += 1
        fields = {
            'username': username or f'closure-obligation-{self.sequence}',
            'account_type': kind,
            'account_no': f'XA9{self.sequence:07d}' if pk is None else f'XA{pk}',
            'nickname': '隔离测试账号',
        }
        if pk is not None:
            fields['id'] = pk
        return ClubAccount.objects.create_account(**fields)

    def provider(self):
        user = make_provider()
        account = get_or_create_account_for_legacy_user(user)
        profile = user.escort_profile
        if profile.account_id != account.pk:
            profile.account = account
            profile.save(update_fields=['account'])
        return user, account, profile

    def order(self, *, account=None, provider=None, provider_account=None,
              status=Order.Status.PENDING, settled=False, amount=1000):
        return make_order(
            None, service=make_service(price=amount), provider=provider,
            customer_account=account or self.account(), provider_account=provider_account,
            status=status, amount=amount,
            settled_at=timezone.now() if settled else None,
        )

    def wallet(self, user, account):
        wallet, _ = Wallet.objects.update_or_create(
            user=user, defaults={'account': account, 'balance': 0, 'frozen_amount': 0},
        )
        return wallet

    def counts(self, result):
        return {row['code']: row['count'] for row in result['metrics']}

    def assert_contract(self, result):
        self.assertEqual(set(result), {'complete', 'status', 'metrics', 'blockers', 'facts_sha256'})
        self.assertEqual(tuple(row['code'] for row in result['metrics']), CODES)
        for row in result['metrics']:
            self.assertEqual(set(row), {'code', 'count'})
            self.assertTrue(row['count'] is None or type(row['count']) is int)
        self.assertEqual(result['blockers'], sorted(
            result['blockers'], key=lambda row: (row['code'], row['resource'], row['reference']),
        ))
        for row in result['blockers']:
            self.assertEqual(set(row), {'code', 'resource', 'reference'})
        self.assertIsNotNone(re.fullmatch(r'[0-9a-f]{64}', result['facts_sha256']))

    def test_empty_store_has_real_zeroes_and_explicit_unverifiable_categories(self):
        result = closure_obligation_readiness(self.store)

        self.assert_contract(result)
        counts = self.counts(result)
        for code in RELIABLE_CODES:
            self.assertEqual(counts[code], 0, code)
        self.assertIsNone(counts['PAYROLL'])
        self.assertIsNone(counts['OTHER_RIGHTS'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual([row['code'] for row in result['blockers']], [
            'OTHER_RIGHTS_UNVERIFIABLE', 'PAYROLL_UNVERIFIABLE',
        ])

    def test_order_reservation_aftersale_and_report_use_real_rows(self):
        boss = self.account()
        provider = self.account('PROVIDER')
        self.order(account=boss, status=Order.Status.PENDING)
        start = timezone.now() + timedelta(days=1)
        Reservation.objects.create(
            customer_account=boss, provider_account=provider, service=make_service(),
            start_time=start, end_time=start + timedelta(hours=1), duration_minutes=60,
            status=Reservation.Status.PENDING,
        )
        aftersale_order = self.order(
            account=boss, status=Order.Status.COMPLETED, settled=True,
        )
        AfterSale.objects.create(
            order=aftersale_order, account=boss, reason='不得输出的售后隐私', status='OPEN',
        )
        legacy_provider, provider_account, _profile = self.provider()
        ProviderReport.objects.create(
            provider=legacy_provider, provider_account=provider_account,
            game_name='隔离游戏', amount=100, status=ProviderReport.Status.PENDING,
        )

        result = closure_obligation_readiness(self.store)

        self.assert_contract(result)
        counts = self.counts(result)
        self.assertEqual(counts['ORDERS'], 1)
        self.assertEqual(counts['RESERVATIONS'], 1)
        self.assertEqual(counts['AFTERSALES'], 1)
        self.assertEqual(counts['REPORTS'], 1)
        self.assertNotIn('不得输出的售后隐私', str(result))

    def test_holds_deficits_permits_gifts_and_reward_claims_use_real_rows(self):
        legacy_provider, provider_account, profile = self.provider()
        wallet = self.wallet(legacy_provider, provider_account)
        income = Transaction.objects.create(
            wallet=wallet, tx_type='INCOME', amount=100,
            balance_before=0, balance_after=100,
        )
        IncomeHold.objects.create(
            wallet=wallet, income_transaction=income, amount=100,
            release_at=timezone.now() + timedelta(hours=1), recovered_amount=20,
        )
        order = self.order(
            account=self.account(), provider=legacy_provider, provider_account=provider_account,
            status=Order.Status.COMPLETED, settled=True,
        )
        refund = OrderRefund.objects.create(order=order, amount=100, reason='隔离退款夹具')
        source = Transaction.objects.create(
            wallet=wallet, order=order, tx_type='DEPOSIT_INCOME', amount=-100,
            balance_before=100, balance_after=0,
        )
        RefundDepositDeficit.objects.create(
            refund=refund, profile=profile, account=provider_account,
            amount=100, remaining=80, source_kind='DIRECT',
            source_store_transaction=source, direct_key=f'closure-test-{refund.pk}',
        )
        RefundDepositRecoveryPermit.objects.create(
            profile=profile, account=provider_account, order=order,
        )
        boss = self.account()
        recharge = RechargeRecord.objects.create(
            account=boss, amount=1000, gift_amount=100, trade_no='closure-gift-fixture',
        )
        RechargeGiftApproval.objects.create(
            recharge=recharge, standard_amount=0, requested_amount=100, status='PENDING',
        )
        reward = RewardItem.objects.create(
            code='closure-reward', name='闭店测试权益', kind='SERVICE',
        )
        RewardClaim.objects.create(
            account=boss, reward=reward, event_key='closure-reward-pending',
            name_snapshot=reward.name, kind_snapshot=reward.kind, status='PENDING',
        )

        result = closure_obligation_readiness(self.store)

        self.assert_contract(result)
        counts = self.counts(result)
        self.assertEqual(counts['INCOME_HOLDS'], 1)
        self.assertEqual(counts['DEPOSIT_DEFICITS'], 1)
        self.assertEqual(counts['RECOVERY_PERMITS'], 1)
        self.assertEqual(counts['GIFT_APPROVALS'], 1)
        self.assertEqual(counts['REWARD_CLAIMS'], 1)
        self.assertEqual(counts['ORDERS'], 0)

    def test_every_in_flight_withdrawal_status_counts_and_terminal_rows_do_not(self):
        account = self.account('PROVIDER')
        active = (
            'PENDING', 'APPROVED', 'STORE_APPROVED', 'PLATFORM_APPROVED', 'PAYMENT_UNKNOWN',
        )
        for index, status in enumerate(active, start=1):
            with self.subTest(status=status):
                WithdrawRequest.objects.create(
                    account=account, amount=100 + index, tax_amount=0,
                    payee_method='WECHAT', payee_account='绝密收款账号', payee_name='绝密收款人',
                    status=status,
                )
                result = closure_obligation_readiness(self.store)
                self.assertEqual(self.counts(result)['WITHDRAWALS'], index)
        for status in ('PAID', 'REJECTED', 'CANCELLED'):
            with self.subTest(status=status):
                WithdrawRequest.objects.create(
                    account=account, amount=100, tax_amount=0,
                    payee_method='WECHAT', payee_account='终态收款账号', payee_name='终态收款人',
                    status=status,
                )
                result = closure_obligation_readiness(self.store)
                self.assertEqual(self.counts(result)['WITHDRAWALS'], 5)
        self.assertNotIn('绝密收款', str(result))

    def test_completed_order_with_unsettled_provider_share_is_not_cleared_by_order_header(self):
        legacy_provider, provider_account, _profile = self.provider()
        order = self.order(
            provider=legacy_provider, provider_account=provider_account,
            status=Order.Status.COMPLETED, settled=True,
        )
        share = OrderProvider.objects.create(
            order=order, provider=legacy_provider, provider_account=provider_account,
            settlement_base=1000, provider_income=800,
        )

        blocked = closure_obligation_readiness(self.store)
        self.assertEqual(self.counts(blocked)['ORDERS'], 1)
        self.assertIn('order_provider', {row['resource'] for row in blocked['blockers']})

        OrderProvider.objects.filter(pk=share.pk).update(settled_at=timezone.now())
        cleared = closure_obligation_readiness(self.store)
        self.assertEqual(self.counts(cleared)['ORDERS'], 0)
        self.assertNotEqual(blocked['facts_sha256'], cleared['facts_sha256'])

    def test_continuation_requires_receipt_even_when_marked_paid(self):
        boss = self.account()
        legacy_provider, provider_account, _profile = self.provider()
        original = self.order(account=boss, status=Order.Status.COMPLETED, settled=True)
        current = self.order(
            account=boss, provider=legacy_provider, provider_account=provider_account,
            status=Order.Status.COMPLETED, settled=True, amount=20000,
        )
        OrderProvider.objects.create(
            order=current, provider=legacy_provider, provider_account=provider_account,
            settlement_base=20000, provider_income=16000, settled_at=timezone.now(),
        )
        claim = OrderContinuation.objects.create(
            original_order=original, continuation_order=current, customer=boss,
            business_cycle=boss.business_cycle, request_id='closure-continuation',
        )
        pending = closure_obligation_readiness(self.store)
        self.assertEqual(self.counts(pending)['REWARD_CLAIMS'], 1)

        participants = [{
            'provider_account_id': provider_account.pk,
            'provider_id': legacy_provider.pk,
            'provider_business_cycle': provider_account.business_cycle,
        }]
        OrderContinuation.objects.filter(pk=claim.pk).update(
            status='VERIFIED', verified_by=self.account('STAFF'), verified_at=timezone.now(),
            participants=participants, reward_state='PAID',
        )
        missing_receipt = closure_obligation_readiness(self.store)
        self.assertEqual(self.counts(missing_receipt)['REWARD_CLAIMS'], 1)
        self.assertNotEqual(pending['facts_sha256'], missing_receipt['facts_sha256'])

        wallet = self.wallet(legacy_provider, provider_account)
        payment = Transaction.objects.create(
            wallet=wallet, order=current, tx_type='REWARD', amount=500,
            balance_before=0, balance_after=500, status='SUCCESS',
        )
        OrderContinuationReward.objects.create(
            continuation=claim, provider=provider_account,
            provider_business_cycle=provider_account.business_cycle,
            transaction=payment, amount=500,
        )
        paid = closure_obligation_readiness(self.store)
        self.assertEqual(self.counts(paid)['REWARD_CLAIMS'], 0)
        self.assertNotEqual(missing_receipt['facts_sha256'], paid['facts_sha256'])

    def test_unverifiable_payroll_and_rights_still_contribute_safe_fact_changes(self):
        before = closure_obligation_readiness(self.store)
        boss = self.account()
        PayrollRun.objects.create(
            month=date(2026, 11, 1), fingerprint='a' * 64, confirmed_by=boss,
        )
        tier = VipTier.objects.create(
            boss_type=make_boss_type(), level=1, recharge_threshold=0,
        )
        grant = MemberBenefitGrant.objects.create(
            account=boss, tier=tier, event_key='closure-benefit', name='不得输出的权益名',
            kind='SERVICE', quantity=1, details={'private': '不得输出'},
        )
        created = closure_obligation_readiness(self.store)
        self.assertNotEqual(before['facts_sha256'], created['facts_sha256'])
        self.assertIsNone(self.counts(created)['PAYROLL'])
        self.assertIsNone(self.counts(created)['OTHER_RIGHTS'])
        self.assertNotIn('不得输出', str(created))

        MemberBenefitGrant.objects.filter(pk=grant.pk).update(quantity=2)
        changed = closure_obligation_readiness(self.store)
        self.assertNotEqual(created['facts_sha256'], changed['facts_sha256'])

    def test_same_numeric_ids_in_two_stores_never_mix_and_digest_binds_store(self):
        account_id, withdrawal_id = 880001, 770001
        first_account = self.account('PROVIDER', pk=account_id, username='shared-closure-provider')
        first_row = WithdrawRequest.objects.create(
            id=withdrawal_id, account=first_account, amount=100, tax_amount=0,
            payee_method='WECHAT', payee_account='甲店收款资料', payee_name='甲店收款人',
            status='PENDING',
        )
        with store_context(self.branch):
            second_account = self.account('PROVIDER', pk=account_id, username='shared-closure-provider')
            second_row = WithdrawRequest.objects.create(
                id=withdrawal_id, account=second_account, amount=100, tax_amount=0,
                payee_method='WECHAT', payee_account='乙店收款资料', payee_name='乙店收款人',
                status='PENDING',
            )
            second = closure_obligation_readiness(self.branch)
        first = closure_obligation_readiness(self.store)

        self.assertEqual(self.counts(first)['WITHDRAWALS'], 1)
        self.assertEqual(self.counts(second)['WITHDRAWALS'], 1)
        self.assertNotEqual(first['facts_sha256'], second['facts_sha256'])
        with store_context(self.branch):
            WithdrawRequest.objects.filter(pk=second_row.pk).update(status='PAYMENT_UNKNOWN')
            changed_second = closure_obligation_readiness(self.branch)
        self.assertNotEqual(second['facts_sha256'], changed_second['facts_sha256'])
        self.assertEqual(closure_obligation_readiness(self.store)['facts_sha256'], first['facts_sha256'])
        self.assertEqual(WithdrawRequest.objects.get(pk=first_row.pk).status, 'PENDING')

    def test_digest_is_repeatable_and_tracks_status_not_only_count(self):
        order = self.order(status=Order.Status.PENDING)
        first = closure_obligation_readiness(self.store)
        second = closure_obligation_readiness(self.store)
        self.assertEqual(first, second)

        Order.objects.filter(pk=order.pk).update(status=Order.Status.GRABBED)
        changed = closure_obligation_readiness(self.store)
        self.assertEqual(self.counts(changed)['ORDERS'], 1)
        self.assertNotEqual(first['facts_sha256'], changed['facts_sha256'])

    def test_one_failed_read_is_unknown_redacted_and_preserves_other_known_blockers(self):
        self.order(status=Order.Status.PENDING)
        secret = 'database-internal-password-and-sql'
        with patch.object(WithdrawRequest.objects, 'using', side_effect=DatabaseError(secret)):
            result = closure_obligation_readiness(self.store)

        self.assert_contract(result)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertFalse(result['complete'])
        self.assertEqual(self.counts(result)['ORDERS'], 1)
        self.assertIsNone(self.counts(result)['WITHDRAWALS'])
        codes = {row['code'] for row in result['blockers']}
        self.assertIn('ORDERS_OUTSTANDING', codes)
        self.assertIn('WITHDRAWALS_READ_FAILED', codes)
        self.assertNotIn(secret, str(result))

    def test_scan_has_no_writes_and_cannot_cross_store_context(self):
        order = self.order(status=Order.Status.PENDING)
        self.store.refresh_from_db()
        order.refresh_from_db()
        before = (self.store.status, self.store.updated_at, order.status, order.updated_at)
        with CaptureQueriesContext(connections[self.store.database_alias]) as captured:
            result = closure_obligation_readiness(self.store)
        writes = [
            row['sql'] for row in captured.captured_queries
            if row['sql'].lstrip().split(None, 1)[0].upper() in {
                'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP', 'REPLACE',
            }
        ]
        self.assertEqual(writes, [])
        self.assertEqual(self.counts(result)['ORDERS'], 1)
        self.store.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((self.store.status, self.store.updated_at, order.status, order.updated_at), before)

        with store_context(self.branch), self.assertRaisesMessage(ValueError, '门店上下文'):
            closure_obligation_readiness(self.store)
        with store_context(None), self.assertRaisesMessage(ValueError, '门店上下文'):
            closure_obligation_readiness(self.store)
