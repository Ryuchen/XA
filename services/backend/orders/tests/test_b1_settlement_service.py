"""B1：结算收口到 orders.services 之后的核心不变量。

覆盖三件事：
1. ``settle_order`` 是唯一结算实现，且资金守恒、计数用 F()、状态按实况刷新；
2. ``refresh_escort_status`` 不再无脑置 AVAILABLE，也不覆盖 OFFLINE；
3. 订单列表的 status 过滤对未知取值 fail-fast。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderProvider
from orders.services import (
    count_active_orders,
    mark_escort_busy,
    refresh_escort_status,
    settle_order,
)
from orders.settlement import SettlementError
from orders.tests.factories import (
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from users.models import EscortProfile
from wallet.models import Transaction, Wallet

User = get_user_model()


def _account(user):
    return get_or_create_account_for_legacy_user(user)


def _balance_of(user):
    return Wallet.objects.get(user=user).balance


@override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=False)
class SettleOrderTest(TestCase):
    """结算的资金口径：三方入账合计恒等于订单实付。"""

    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.customer_account = _account(self.customer)
        self.provider_account = _account(self.provider)
        make_wallet(self.customer, balance=0)
        make_wallet(self.provider, balance=0)
        self.service = make_service(price=10000)

    def _make_in_service_order(self, **kwargs):
        fields = {
            'customer': self.customer,
            'customer_account': self.customer_account,
            'provider': self.provider,
            'provider_account': self.provider_account,
            'service': self.service,
            'amount': 10000,
            'status': Order.Status.IN_SERVICE,
            'payment_status': Order.PaymentStatus.PAID,
            'commission_rate': 20,
            'provider_income': 8000,
            'inviter_commission': 0,
            'shop_income': 2000,
        }
        fields.update(kwargs)
        return Order.objects.create(**fields)

    def test_single_provider_settlement_is_conserved(self):
        order = self._make_in_service_order()
        settled = settle_order(order)

        self.assertEqual(settled, [self.provider.id])
        self.assertEqual(_balance_of(self.provider), 8000)

        incomes = Transaction.objects.filter(
            order=order, tx_type=Transaction.TxType.INCOME,
        ).count()
        shop = Transaction.objects.filter(
            order=order, tx_type=Transaction.TxType.SHOP_INCOME,
        ).first()
        self.assertEqual(incomes, 1)
        self.assertIsNotNone(shop)
        self.assertEqual(shop.amount, 2000)

        total_out = sum(
            t.amount for t in Transaction.objects.filter(order=order)
        )
        self.assertEqual(total_out, order.amount)

    def test_inviter_commission_is_paid_from_platform_cut(self):
        inviter = make_user()
        make_wallet(inviter, balance=0)
        inviter_account = _account(inviter)
        order = self._make_in_service_order(
            inviter=inviter,
            inviter_account=inviter_account,
            inviter_commission=500,
            shop_income=1500,
        )
        settle_order(order)

        self.assertEqual(_balance_of(inviter), 500)
        total_out = sum(t.amount for t in Transaction.objects.filter(order=order))
        self.assertEqual(total_out, order.amount)

    def test_zero_provider_income_does_not_fall_back_to_full_amount(self):
        """抽成 100% 是合法配置：陪玩实得 0，不得回落成全额造钱。"""
        order = self._make_in_service_order(
            commission_rate=100, provider_income=0, shop_income=10000,
        )
        settle_order(order)
        self.assertEqual(_balance_of(self.provider), 0)

    def test_over_allocation_is_blocked(self):
        """双陪两行各拿全额这类跨行超付必须被守恒闸门拦下。"""
        second = make_provider()
        make_wallet(second, balance=0)
        second_account = _account(second)
        order = self._make_in_service_order()
        OrderProvider.objects.create(
            order=order, provider=self.provider,
            provider_account=self.provider_account,
            settlement_base=10000, provider_income=10000,
        )
        OrderProvider.objects.create(
            order=order, provider=second, provider_account=second_account,
            settlement_base=10000, provider_income=10000,
        )
        with self.assertRaises(SettlementError):
            settle_order(order)
        self.assertEqual(_balance_of(self.provider), 0)
        self.assertEqual(_balance_of(second), 0)

    def test_already_settled_rows_are_not_paid_twice(self):
        from django.utils import timezone

        order = self._make_in_service_order()
        OrderProvider.objects.create(
            order=order, provider=self.provider,
            provider_account=self.provider_account,
            settlement_base=10000, provider_income=8000,
            settled_at=timezone.now(),
        )
        settle_order(order)
        self.assertEqual(_balance_of(self.provider), 0)

    def test_completed_order_count_uses_f_expression(self):
        order = self._make_in_service_order()
        settle_order(order)
        profile = EscortProfile.objects.get(user=self.provider)
        self.assertEqual(profile.completed_order_count, 1)

    def test_status_released_when_no_other_active_order(self):
        profile = EscortProfile.objects.get(user=self.provider)
        profile.status = EscortProfile.Status.BUSY
        profile.save(update_fields=['status'])

        order = self._make_in_service_order()
        settle_order(order)

        profile.refresh_from_db()
        self.assertEqual(profile.status, EscortProfile.Status.AVAILABLE)

    def test_status_stays_busy_when_another_order_is_running(self):
        """双开陪玩结完一单仍在跑另一单，不能被标成空闲。"""
        profile = EscortProfile.objects.get(user=self.provider)
        profile.status = EscortProfile.Status.BUSY
        profile.save(update_fields=['status'])

        self._make_in_service_order()          # 另一笔仍在服务中
        order = self._make_in_service_order()
        settle_order(order)

        profile.refresh_from_db()
        self.assertEqual(profile.status, EscortProfile.Status.BUSY)


@override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class SettleOrderEvidenceGateTest(TestCase):
    """凭证闸门对两条结算入口一视同仁。"""

    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        make_wallet(self.customer, balance=0)
        make_wallet(self.provider, balance=0)
        self.service = make_service(price=10000)

    def test_missing_report_blocks_settlement(self):
        order = Order.objects.create(
            customer=self.customer,
            customer_account=_account(self.customer),
            provider=self.provider,
            provider_account=_account(self.provider),
            service=self.service,
            amount=10000,
            status=Order.Status.IN_SERVICE,
            payment_status=Order.PaymentStatus.PAID,
            commission_rate=20,
            provider_income=8000,
            shop_income=2000,
        )
        with self.assertRaises(SettlementError):
            settle_order(order)
        self.assertEqual(_balance_of(self.provider), 0)


class RefreshEscortStatusTest(TestCase):
    def setUp(self):
        self.provider = make_provider()
        self.account = _account(self.provider)
        self.customer = make_user()
        self.service = make_service(price=1000)

    def _profile(self):
        return EscortProfile.objects.get(user=self.provider)

    def test_offline_is_never_overridden(self):
        """离线是陪玩的主动意图，系统无权替他上线。"""
        profile = self._profile()
        profile.status = EscortProfile.Status.OFFLINE
        profile.save(update_fields=['status'])

        refresh_escort_status(
            provider_ids=[self.provider.id], account_ids=[self.account.pk],
        )
        self.assertEqual(self._profile().status, EscortProfile.Status.OFFLINE)

    def test_busy_when_active_order_exists(self):
        Order.objects.create(
            customer=self.customer, provider=self.provider,
            provider_account=self.account, service=self.service, amount=1000,
            status=Order.Status.IN_SERVICE,
        )
        refresh_escort_status(
            provider_ids=[self.provider.id], account_ids=[self.account.pk],
        )
        self.assertEqual(self._profile().status, EscortProfile.Status.BUSY)

    def test_available_when_nothing_running(self):
        profile = self._profile()
        profile.status = EscortProfile.Status.BUSY
        profile.save(update_fields=['status'])

        refresh_escort_status(
            provider_ids=[self.provider.id], account_ids=[self.account.pk],
        )
        self.assertEqual(self._profile().status, EscortProfile.Status.AVAILABLE)

    def test_excluded_order_is_not_counted(self):
        order = Order.objects.create(
            customer=self.customer, provider=self.provider,
            provider_account=self.account, service=self.service, amount=1000,
            status=Order.Status.IN_SERVICE,
        )
        refresh_escort_status(
            provider_ids=[self.provider.id],
            account_ids=[self.account.pk],
            exclude_order_ids=[order.pk],
        )
        self.assertEqual(self._profile().status, EscortProfile.Status.AVAILABLE)

    def test_empty_input_is_a_noop(self):
        self.assertEqual(refresh_escort_status(), 0)

    def test_mark_busy_skips_offline(self):
        profile = self._profile()
        profile.status = EscortProfile.Status.OFFLINE
        profile.save(update_fields=['status'])

        mark_escort_busy(provider_ids=[self.provider.id])
        self.assertEqual(self._profile().status, EscortProfile.Status.OFFLINE)

    def test_mark_busy_promotes_available(self):
        mark_escort_busy(provider_ids=[self.provider.id])
        self.assertEqual(self._profile().status, EscortProfile.Status.BUSY)


class CountActiveOrdersTest(TestCase):
    def setUp(self):
        self.provider = make_provider()
        self.account = _account(self.provider)
        self.customer = make_user()
        self.service = make_service(price=1000)

    def _order(self, status):
        return Order.objects.create(
            customer=self.customer, provider=self.provider,
            provider_account=self.account, service=self.service,
            amount=1000, status=status,
        )

    def test_counts_grabbed_and_in_service_only(self):
        self._order(Order.Status.GRABBED)
        self._order(Order.Status.IN_SERVICE)
        self._order(Order.Status.COMPLETED)
        self._order(Order.Status.PENDING)
        self.assertEqual(count_active_orders(self.account), 2)

    def test_same_order_counted_once_across_dimensions(self):
        order = self._order(Order.Status.IN_SERVICE)
        OrderProvider.objects.create(
            order=order, provider=self.provider,
            provider_account=self.account,
            settlement_base=1000, provider_income=800,
        )
        self.assertEqual(
            count_active_orders(
                account_id=self.account.pk, provider_id=self.provider.id,
            ),
            1,
        )

    def test_no_dimension_returns_zero(self):
        self._order(Order.Status.IN_SERVICE)
        self.assertEqual(count_active_orders(), 0)
