"""测试夹具自检：确认 factories 能在 sqlite 测试库正确建数据。"""

from decimal import Decimal

from django.test import TestCase

from orders.models import Order
from orders.tests.factories import (
    User,
    make_completed_order,
    make_escort_profile,
    make_evaluation,
    make_provider,
    make_service,
    make_superuser,
    make_user,
    make_wallet,
)


class FactoriesTest(TestCase):
    def test_make_user_defaults_to_customer(self):
        user = make_user()
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertTrue(user.check_password('pass1234'))

    def test_usernames_are_unique(self):
        self.assertNotEqual(make_user().username, make_user().username)

    def test_make_superuser_is_superuser(self):
        admin = make_superuser()
        self.assertTrue(admin.is_superuser)

    def test_make_provider_has_profile(self):
        provider = make_provider()
        self.assertEqual(provider.role, User.Role.PROVIDER)
        self.assertEqual(provider.escort_profile.rating_count, 0)
        self.assertEqual(provider.escort_profile.rating_avg, Decimal('0'))

    def test_make_completed_order(self):
        customer = make_user()
        provider = make_provider()
        service = make_service(price=8800)
        order = make_completed_order(customer, provider, service=service)
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertEqual(order.amount, 8800)
        self.assertEqual(order.provider_id, provider.id)

    def test_make_wallet_and_evaluation(self):
        customer = make_user()
        provider = make_provider()
        wallet = make_wallet(provider, balance=5000)
        self.assertEqual(wallet.balance, 5000)
        order = make_completed_order(customer, provider)
        evaluation = make_evaluation(order, score=4, content='不错')
        self.assertEqual(evaluation.score, 4)
        self.assertEqual(evaluation.customer_id, customer.id)
        self.assertEqual(evaluation.provider_id, provider.id)
