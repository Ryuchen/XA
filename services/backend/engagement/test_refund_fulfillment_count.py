from django.test import TestCase
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.refunds import reverse_refund_benefits
from orders.models import Order, OrderProvider, OrderRefund
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from rest_framework.test import APIClient
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


class RefundFulfillmentCountTests(TestCase):
    def test_full_refund_preserves_each_previously_counted_participant(self):
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        first, second = make_provider(completed_order_count=3), make_provider(completed_order_count=7)
        order = make_order(boss, provider=first, customer_account=account, amount=10000,
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
            in_service_at=timezone.now(), completed_at=timezone.now(), settled_at=timezone.now(),
            escort_mode=Order.EscortMode.DOUBLE)
        for provider in (first, second):
            OrderProvider.objects.create(order=order, provider=provider, settlement_base=5000, provider_income=4000)
        refund = OrderRefund.objects.create(order=order, amount=10000, reason='隔离全额退款')
        reverse_refund_benefits(order, refund, None)
        first.escort_profile.refresh_from_db()
        second.escort_profile.refresh_from_db()
        self.assertEqual((first.escort_profile.completed_order_count, second.escort_profile.completed_order_count), (3, 7))

    def test_authorized_full_refund_preserves_real_double_fulfillment_count(self):
        boss = make_user()
        first, second = make_provider(completed_order_count=3), make_provider(completed_order_count=7)
        staff = make_console_user(['order:view', 'order:refund'])
        accounts = {user.pk: get_or_create_account_for_legacy_user(user) for user in (boss, first, second, staff)}
        staff.admin_membership.account = accounts[staff.pk]
        staff.admin_membership.save(update_fields=['account'])
        for user in (first, second):
            user.escort_profile.account = accounts[user.pk]
            user.escort_profile.save(update_fields=['account'])
        order = make_order(boss, provider=first, customer_account=accounts[boss.pk],
            provider_account=accounts[first.pk], amount=10000, provider_income=8000, shop_income=2000,
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
            in_service_at=timezone.now(), completed_at=timezone.now(), settled_at=timezone.now(),
            escort_mode=Order.EscortMode.DOUBLE)
        customer_wallet = get_wallet(user=boss, account=accounts[boss.pk])
        Transaction.objects.create(wallet=customer_wallet, order=order, tx_type='PAY', amount=-10000,
                                   balance_before=10000, balance_after=0)
        for provider in (first, second):
            wallet = get_wallet(user=provider, account=accounts[provider.pk])
            wallet.balance = 4000
            wallet.save(update_fields=['balance'])
            OrderProvider.objects.create(order=order, provider=provider, provider_account=accounts[provider.pk],
                settlement_base=5000, provider_income=4000, settled_at=timezone.now())
            Transaction.objects.create(wallet=wallet, order=order, tx_type='INCOME', amount=4000,
                balance_before=0, balance_after=4000, remark=f'订单收入：{order.order_no}')
        shop = get_platform_wallet()
        shop.balance = 2000
        shop.save(update_fields=['balance'])
        Transaction.objects.create(wallet=shop, order=order, tx_type='SHOP_INCOME', amount=2000,
                                   balance_before=0, balance_after=2000)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[staff.pk])[0])
        preview = client.get(f'/api/admin/orders/{order.pk}/refund-preview/').data
        self.assertTrue(preview['data']['eligible'], preview)
        result = client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '隔离退款',
            'client_request_id': 'refund-fulfilled-double', 'plan_token': preview['data']['plan_token']}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order.refresh_from_db()
        first.escort_profile.refresh_from_db()
        second.escort_profile.refresh_from_db()
        self.assertEqual((order.payment_status, first.escort_profile.completed_order_count,
                          second.escort_profile.completed_order_count), ('REFUNDED', 3, 7))
