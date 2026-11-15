from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from engagement.funding_wallet import allocate_locked_order_funding, snapshot_service_funding_terms
from engagement.models import MemberProfile, PointFundingLot, PointLedger
from engagement.services import config
from orders.models import Order, OrderPointsRefund, OrderRefund
from orders.points_service_payment import quote, record_locked
from orders.points_service_refund import refund_assets_locked
from orders.tests.factories import make_service, make_user
from stores.db import transaction
from wallet.models import Transaction
from wallet.services import get_wallet
from rest_framework.test import APIClient


@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True)
class ServicePointsRefundTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.wallet = get_wallet(user=self.user, account=self.account)
        self.wallet.balance = 2000; self.wallet.save(update_fields=['balance'])
        self.profile, _ = MemberProfile.objects.update_or_create(account=self.account,
                                                                 defaults={'points_balance': 15000})
        for kind, amount in (('PREPAID', 1800), ('GIFT', 100), ('COMPENSATION', 100)):
            PointFundingLot.objects.create(account=self.account, source_key=f'test-{kind}', kind=kind, remaining=amount)
        self.service = make_service(price=1000)
        config()
        save_points_terms(group_point_hundredths=10000, group_coin_units=30,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        price = {'original_amount': 1000, 'boss_discount': 0, 'promo_discount': 0,
                 'coupon_discount': 0, 'promotion_id': None, 'game_rounds': 1}
        _, token = quote(self.account, self.service, price, 10000, gift=100, compensation=100)
        with transaction.atomic():
            self.order = Order.objects.create(customer=self.user, customer_account=self.account, service=self.service,
                amount=970, original_amount=1000, business_cycle=self.account.business_cycle)
            allocate_locked_order_funding(self.order, self.wallet, gift=100, compensation=100)
            self.wallet.balance -= 970; self.wallet.save(update_fields=['balance'])
            pay = Transaction.objects.create(wallet=self.wallet, order=self.order, amount=-970,
                tx_type=Transaction.TxType.PAY, balance_before=2000, balance_after=1030)
            snapshot_service_funding_terms(self.order, paid_at=pay.created_at)
            record_locked(order_id=self.order.pk, payment_id=pay.pk, token=token)

    def refund(self, equivalent, coin):
        with transaction.atomic():
            refund = OrderRefund.objects.create(order=self.order, amount=coin, reason='隔离测试退款')
            return refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=equivalent)

    def test_partial_then_final_return_original_assets_and_sources(self):
        first = self.refund(500, 480)
        self.assertEqual((first.coin_units, first.points_hundredths, first.prepaid_units,
                          first.gift_units, first.compensation_units, first.is_final), (480, 5000, 480, 0, 0, False))
        operation = config(); operation.points_enabled = False; operation.save(update_fields=['points_enabled'])
        final = self.refund(500, 490)
        self.assertEqual((final.coin_units, final.points_hundredths, final.prepaid_units,
                          final.gift_units, final.compensation_units, final.is_final), (490, 5000, 290, 100, 100, True))
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (2000, 15000))
        self.assertEqual(list(PointFundingLot.objects.order_by('kind').values_list('remaining', flat=True)),
                         [100, 100, 1800])

    def test_small_partial_can_return_only_points_then_final_remainder(self):
        first = self.refund(10, 0)
        self.assertEqual((first.coin_units, first.points_hundredths, first.coin_transaction_id), (0, 100, None))
        final = self.refund(990, 970)
        self.assertEqual((final.coin_units, final.points_hundredths, final.is_final), (970, 9900, True))
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (2000, 15000))

    def test_failure_rolls_back_refund_sources_wallet_and_points(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                refund = OrderRefund.objects.create(order=self.order, amount=480, reason='隔离测试')
                with patch.object(OrderPointsRefund.objects, 'create', side_effect=RuntimeError('failure')):
                    refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1030, 5000))
        self.assertFalse(OrderRefund.objects.exists())
        self.assertFalse(OrderPointsRefund.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type=Transaction.TxType.REFUND).exists())
        self.assertFalse(PointLedger.objects.filter(kind='ORDER_POINTS_RETURN').exists())
        self.assertEqual(sum(self.order.funding_allocations.values_list('refunded_amount', flat=True)), 0)

    def test_wrong_amount_replay_or_unrecorded_history_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.refund(500, 490)
        first = self.refund(500, 480)
        with self.assertRaises(ValidationError), transaction.atomic():
            refund_assets_locked(order_id=self.order.pk, refund_id=first.refund_id, equivalent_units=500)
        with self.assertRaises(ValidationError), transaction.atomic():
            OrderRefund.objects.create(order=self.order, amount=10, reason='未经核对历史')
            refund = OrderRefund.objects.create(order=self.order, amount=490, reason='隔离测试')
            refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1510, 10000))
        self.assertEqual(OrderPointsRefund.objects.count(), 1)

    def test_modified_previous_ledger_or_sources_block_following_refund(self):
        first = self.refund(500, 480)
        for bad in ('ledger', 'sources'):
            with self.subTest(bad=bad), transaction.atomic():
                if bad == 'ledger':
                    PointLedger.objects.filter(pk=first.points_ledger_id).update(amount=4999)
                else:
                    self.order.funding_allocations.filter(kind='PREPAID').update(refunded_amount=470)
                refund = OrderRefund.objects.create(order=self.order, amount=490, reason='隔离测试')
                with self.assertRaises(ValidationError):
                    refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
                transaction.set_rollback(True)

    def test_original_payment_evidence_must_be_consistent_before_any_return(self):
        receipt = self.order.points_redemption
        for bad in ('payment_balance', 'payment_reversal', 'redemption_key', 'extra_payment'):
            with self.subTest(bad=bad), transaction.atomic():
                if bad == 'payment_balance':
                    Transaction.objects.filter(pk=receipt.payment_id).update(balance_after=1020)
                elif bad == 'payment_reversal':
                    Transaction.objects.create(wallet=self.wallet, order=self.order, amount=970,
                        tx_type=Transaction.TxType.REFUND, balance_before=1030, balance_after=2000,
                        reversal_of=receipt.payment)
                elif bad == 'redemption_key':
                    PointLedger.objects.filter(pk=receipt.ledger_id).update(event_key='wrong-redemption')
                else:
                    Transaction.objects.create(wallet=self.wallet, order=self.order, amount=-10,
                        tx_type=Transaction.TxType.PAY, balance_before=1030, balance_after=1020)
                refund = OrderRefund.objects.create(order=self.order, amount=480, reason='隔离测试')
                with self.assertRaisesMessage(ValidationError, '原积分付款凭据不一致'):
                    refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
                transaction.set_rollback(True)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1030, 5000))
        self.assertFalse(OrderPointsRefund.objects.exists())

    def test_refund_rejects_missing_identity_mapping_and_orphan_redemption_debit(self):
        from club_accounts.models import LegacyAccountMap
        for fault in ('identity', 'customer', 'orphan_redemption'):
            with self.subTest(fault=fault), transaction.atomic():
                if fault == 'identity':
                    LegacyAccountMap.objects.filter(account=self.account).delete()
                elif fault == 'customer':
                    self.order.customer = make_user()
                    self.order.save(update_fields=['customer'])
                else:
                    PointLedger.objects.create(account=self.account, order=self.order, event_key='orphan-redeem',
                        kind='ORDER_POINTS_REDEEM', amount=-100, balance_after=4900, remark='隔离异常流水')
                refund = OrderRefund.objects.create(order=self.order, amount=480, reason='隔离退款测试')
                with self.assertRaises(ValidationError):
                    refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
                transaction.set_rollback(True)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1030, 5000))
        self.assertFalse(OrderPointsRefund.objects.exists())

    def test_tampered_previous_credit_or_points_key_blocks_further_return(self):
        first = self.refund(500, 480)
        for bad in ('credit_balance', 'credit_reversal', 'points_key'):
            with self.subTest(bad=bad), transaction.atomic():
                if bad == 'credit_balance':
                    Transaction.objects.filter(pk=first.coin_transaction_id).update(balance_after=999)
                elif bad == 'credit_reversal':
                    Transaction.objects.create(wallet=self.wallet, order=self.order, amount=-480,
                        tx_type=Transaction.TxType.REFUND, balance_before=1510, balance_after=1030,
                        reversal_of=first.coin_transaction)
                else:
                    PointLedger.objects.filter(pk=first.points_ledger_id).update(event_key='wrong-return')
                refund = OrderRefund.objects.create(order=self.order, amount=490, reason='隔离测试')
                with self.assertRaises(ValidationError):
                    refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
                transaction.set_rollback(True)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1510, 10000))
        self.assertEqual(OrderPointsRefund.objects.count(), 1)

    def test_refund_rejects_wrong_kind_even_when_current_refund_uses_other_source(self):
        from engagement.models import PointFundingLot
        with transaction.atomic():
            lot = self.order.funding_allocations.get(kind='COMPENSATION').lot
            lot.kind = PointFundingLot.Kind.GIFT
            lot.save(update_fields=['kind'])
            with self.assertRaises(ValidationError):
                self.refund(500, 480)
            transaction.set_rollback(True)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1030, 5000))
        self.assertFalse(OrderRefund.objects.exists())

    def test_orphan_customer_refund_or_point_return_blocks_further_credit(self):
        self.refund(500, 480)
        for bad in ('coin', 'points'):
            with self.subTest(bad=bad), transaction.atomic():
                if bad == 'coin':
                    Transaction.objects.create(wallet=self.wallet, order=self.order, amount=10,
                        tx_type=Transaction.TxType.REFUND, balance_before=0, balance_after=10)
                else:
                    PointLedger.objects.create(account=self.account, order=self.order, event_key='orphan',
                        kind='ORDER_POINTS_RETURN', amount=1, balance_after=1, remark='隔离测试')
                refund = OrderRefund.objects.create(order=self.order, amount=490, reason='隔离测试')
                with self.assertRaises(ValidationError):
                    refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
                transaction.set_rollback(True)

    def test_refund_refuses_inconsistent_receipt_marker(self):
        with self.assertRaises(ValidationError), transaction.atomic():
            self.order.refresh_from_db()
            self.order.funding_terms['points_redemption_v1'] = {'receipt_id': 999999}
            self.order.save(update_fields=['funding_terms'])
            refund = OrderRefund.objects.create(order=self.order, amount=480, reason='隔离测试')
            refund_assets_locked(order_id=self.order.pk, refund_id=refund.pk, equivalent_units=500)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1030, 5000))
        self.assertFalse(OrderRefund.objects.exists())

    def test_disabled_flag_and_stale_order_cannot_use_old_refund_or_settlement(self):
        from engagement.confirmation import process_order
        from orders.refunds import RefundError, build_refund_plan
        from orders.services import refund_to_customer, settle_order
        from orders.settlement import SettlementError

        # setUp's original Order object predates receipt creation and therefore
        # does not contain the marker now saved in the database.
        self.assertNotIn('points_redemption_v1', self.order.funding_terms)
        with override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=False):
            with self.assertRaises(RefundError) as error:
                build_refund_plan(self.order)
            self.assertEqual(error.exception.code, 'POINTS_REFUND_UNAVAILABLE')
            with self.assertRaises(RefundError), transaction.atomic():
                refund_to_customer(self.order)
            client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            response = client.post(f'/api/orders/orders/{self.order.pk}/cancel/', {}, format='json')
            self.assertEqual(response.data['code'], 400, response.data)
            self.order.refresh_from_db()
            self.assertEqual((self.order.status, self.order.payment_status), ('PENDING', 'PAID'))
            self.assertFalse(OrderRefund.objects.exists())
            self.order.status = 'COMPLETED'; self.order.save(update_fields=['status'])
            self.assertFalse(process_order(self.order.pk))
            with self.assertRaises(SettlementError), transaction.atomic():
                settle_order(self.order)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1030, 5000))
        self.assertFalse(Transaction.objects.filter(tx_type=Transaction.TxType.REFUND).exists())
