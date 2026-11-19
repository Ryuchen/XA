from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.exceptions import ValidationError
import os
from unittest import skipUnless
from unittest.mock import patch

from club_accounts.services import get_or_create_account_for_legacy_user
from engagement.funding_wallet import allocate_locked_order_funding, refund_locked_order_funding
from engagement.models import OrderFundingAllocation, PointFundingLot
from engagement.services import (_decrease_funding_lot, _restore_legacy_funding_lot,
                                 allocate_order_points, release_order_allocations, wallet_funding_parts)
from orders.tests.factories import make_order, make_service, make_user
from wallet.models import RechargeRecord
from wallet.services import get_wallet


class FundingWalletTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.wallet = get_wallet(account=self.account, user=self.user)
        self.wallet.balance = 1700; self.wallet.save(update_fields=['balance'])
        recharge = RechargeRecord.objects.create(account=self.account, amount=1000, gift_amount=300)
        self.mixed = PointFundingLot.objects.create(account=self.account, source_key=f'recharge:{recharge.pk}',
                                                    kind='RECHARGE', remaining=1300)
        self.gift = PointFundingLot.objects.create(account=self.account, source_key='test-wallet-gift',
                                                   kind='GIFT', remaining=200)
        self.comp = PointFundingLot.objects.create(account=self.account, source_key='test-wallet-comp',
                                                   kind='COMPENSATION', remaining=200)
        self.order = make_order(self.user, make_service(price=1000), customer_account=self.account)

    def pay(self):
        with transaction.atomic():
            allocate_locked_order_funding(self.order, self.wallet, gift=200, compensation=100)
            self.wallet.balance -= 1000; self.wallet.save(update_fields=['balance'])

    def refund(self, amount):
        with transaction.atomic():
            result = refund_locked_order_funding(self.order, self.wallet, amount)
            self.wallet.balance += amount; self.wallet.save(update_fields=['balance'])
            return result

    def test_mixed_recharge_and_partial_refund_restore_exact_sources(self):
        self.pay()
        self.mixed.refresh_from_db(); self.comp.refresh_from_db()
        self.assertEqual((self.mixed.remaining, self.mixed.gift_remaining, self.comp.remaining), (400, 100, 100))
        self.assertEqual(wallet_funding_parts(self.wallet),
                         {'prepaid_balance': 300, 'gift_balance': 300, 'compensation_balance': 100})
        self.refund(500)
        self.assertEqual(wallet_funding_parts(self.wallet),
                         {'prepaid_balance': 800, 'gift_balance': 300, 'compensation_balance': 100})
        self.refund(500)
        self.mixed.refresh_from_db(); self.comp.refresh_from_db()
        self.assertEqual((self.mixed.remaining, self.mixed.gift_remaining, self.comp.remaining), (1300, 300, 200))
        self.assertEqual(wallet_funding_parts(self.wallet),
                         {'prepaid_balance': 1000, 'gift_balance': 500, 'compensation_balance': 200})
        self.assertEqual(sum(OrderFundingAllocation.objects.filter(order=self.order)
                             .values_list('refunded_amount', flat=True)), 1000)
        with self.assertRaises(ValidationError):
            self.refund(10)

    def test_rejected_plan_rolls_back_untracked_legacy_principal(self):
        self.wallet.balance += 200; self.wallet.save(update_fields=['balance'])
        with transaction.atomic(), self.assertRaises(ValidationError):
            allocate_locked_order_funding(self.order, self.wallet, gift=600)
        self.assertFalse(PointFundingLot.objects.filter(source_key__startswith='funding:order:').exists())
        self.assertFalse(OrderFundingAllocation.objects.filter(order=self.order).exists())
        self.mixed.refresh_from_db()
        self.assertIsNone(self.mixed.gift_remaining)

    def test_initialized_mixed_lot_legacy_fifo_and_unknown_refund(self):
        self.mixed.gift_remaining = 300; self.mixed.save(update_fields=['gift_remaining'])
        with transaction.atomic():
            lot = PointFundingLot.objects.select_for_update().get(pk=self.mixed.pk)
            _decrease_funding_lot(lot, 1100)
            self.assertEqual((lot.remaining, lot.gift_remaining), (200, 200))
            _restore_legacy_funding_lot(lot, 100)
            self.assertEqual((lot.remaining, lot.gift_remaining), (300, 200))

    @skipUnless(os.environ.get('XA_LEGACY_GIFT_FIFO_FORMAL') == '1',
                '普通单 FIFO 抵扣赠送属旧资金旅程：默认服务支付已改为仅消费本金（18-盘点 消费积分补充），本金不足不动赠送／赔付；最终联调启用')
    def test_legacy_order_after_mixed_snapshot_preserves_its_known_gift_on_refund(self):
        self.pay()
        legacy = make_order(self.user, make_service(price=200), customer_account=self.account)
        with transaction.atomic():
            allocate_order_points(legacy, self.wallet, self.wallet.balance)
            allocation = legacy.point_allocations.get(lot=self.mixed)
            self.assertEqual((allocation.amount, allocation.source_gift_amount), (200, 0))
            # The initialized lot currently has 300 principal; the next 200
            # consumes 100 principal and 100 gift.
            second = make_order(self.user, make_service(price=200), customer_account=self.account)
            self.wallet.balance -= 200; self.wallet.save(update_fields=['balance'])
            allocate_order_points(second, self.wallet, self.wallet.balance)
            allocation2 = second.point_allocations.get(lot=self.mixed)
            self.assertEqual((allocation2.amount, allocation2.source_gift_amount), (200, 100))
            release_order_allocations(second)
        self.mixed.refresh_from_db()
        self.assertEqual((self.mixed.remaining, self.mixed.gift_remaining), (200, 100))

    def test_constraint_rejects_more_gift_than_balance_and_nonmixed_gift_snapshot(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PointFundingLot.objects.filter(pk=self.mixed.pk).update(gift_remaining=1301)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PointFundingLot.objects.filter(pk=self.gift.pk).update(gift_remaining=1)

    def test_order_must_belong_to_locked_wallet(self):
        other = get_or_create_account_for_legacy_user(make_user())
        other_wallet = get_wallet(account=other)
        with transaction.atomic(), self.assertRaises(ValidationError):
            allocate_locked_order_funding(self.order, other_wallet)
        self.assertFalse(OrderFundingAllocation.objects.filter(order=self.order).exists())

    def test_allocation_failure_rolls_back_every_source(self):
        with patch.object(OrderFundingAllocation.objects, 'bulk_create', side_effect=RuntimeError('test rollback')):
            with self.assertRaises(RuntimeError), transaction.atomic():
                allocate_locked_order_funding(self.order, self.wallet, gift=200, compensation=100)
        self.mixed.refresh_from_db(); self.comp.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.mixed.remaining, self.mixed.gift_remaining, self.comp.remaining, self.wallet.balance),
                         (1300, None, 200, 1700))
        self.assertFalse(OrderFundingAllocation.objects.filter(order=self.order).exists())

    def test_refund_rejects_changed_unused_source_and_rolls_back_wallet_credit(self):
        self.pay()
        # The first refund goes to principal, but a later compensation source
        # must still match the original order before anything is credited.
        self.comp.kind = 'GIFT'; self.comp.save(update_fields=['kind'])
        before = self.wallet.balance
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                self.wallet.balance += 500; self.wallet.save(update_fields=['balance'])
                refund_locked_order_funding(self.order, self.wallet, 500)
        self.wallet.refresh_from_db(); self.mixed.refresh_from_db()
        self.assertEqual(self.wallet.balance, before)
        self.assertEqual((self.mixed.remaining, self.mixed.gift_remaining), (400, 100))
        self.assertFalse(self.order.funding_allocations.filter(refunded_amount__gt=0).exists())

    def test_refund_rejects_compensation_allocation_against_mixed_recharge(self):
        self.pay()
        self.order.funding_allocations.filter(lot=self.mixed, kind='PREPAID').update(kind='COMPENSATION')
        before = self.wallet.balance
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                self.wallet.balance += 1000; self.wallet.save(update_fields=['balance'])
                refund_locked_order_funding(self.order, self.wallet, 1000)
        self.wallet.refresh_from_db(); self.mixed.refresh_from_db()
        self.assertEqual(self.wallet.balance, before)
        self.assertEqual((self.mixed.remaining, self.mixed.gift_remaining), (400, 100))
        self.assertFalse(self.order.funding_allocations.filter(refunded_amount__gt=0).exists())


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class FundingStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_numeric_ids_do_not_mix_store_sources_or_refunds(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        first, second = resolve_store(), xinan_store()
        for store in (first, second):
            with store_context(store):
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                wallet = get_wallet(account=account, user=user)
                wallet.balance = 1000; wallet.save(update_fields=['balance'])
                PointFundingLot.objects.create(account=account, source_key='same-store-key',
                                               kind='PREPAID', remaining=1000)
                order = make_order(user, make_service(price=500), customer_account=account)
                with transaction.atomic():
                    allocate_locked_order_funding(order, wallet)
                    wallet.balance -= 500; wallet.save(update_fields=['balance'])
        with store_context(second):
            lot = PointFundingLot.objects.get(source_key='same-store-key')
            lot.kind = 'GIFT'; lot.save(update_fields=['kind'])
        with store_context(first):
            with transaction.atomic():
                order = OrderFundingAllocation.objects.get().order
                wallet = get_wallet(account=order.customer_account)
                refund_locked_order_funding(order, wallet, 500)
                wallet.balance += 500; wallet.save(update_fields=['balance'])
            self.assertEqual(PointFundingLot.objects.get(source_key='same-store-key').remaining, 1000)
        with store_context(second):
            self.assertEqual(PointFundingLot.objects.get(source_key='same-store-key').remaining, 500)
            self.assertEqual(OrderFundingAllocation.objects.get().refunded_amount, 0)
            order = OrderFundingAllocation.objects.get().order
            wallet = get_wallet(account=order.customer_account)
            with self.assertRaises(ValidationError), transaction.atomic():
                refund_locked_order_funding(order, wallet, 500)
            self.assertEqual(OrderFundingAllocation.objects.get().refunded_amount, 0)
            wallet.refresh_from_db(); self.assertEqual(wallet.balance, 500)
