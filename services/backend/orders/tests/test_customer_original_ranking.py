from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderRefund
from orders.tests.factories import make_order, make_service, make_service_category, make_user


class CustomerOriginalRankingTests(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.account = get_or_create_account_for_legacy_user(self.customer)
        token, _ = issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.now = timezone.now()

    def order(self, *, original, paid, status='PAID', gift=False, account_only=False):
        service = make_service(service_category=make_service_category(is_gift=gift))
        return make_order(None if account_only else self.customer, service=service,
            customer_account=self.account, original_amount=original, amount=paid,
            status=Order.Status.COMPLETED, payment_status=status, completed_at=self.now)

    def ranking(self):
        response = self.client.get('/api/orders/rankings/?period=month')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def test_original_price_less_actual_refund_includes_gift_and_account_only(self):
        order = self.order(original=2500, paid=1700, status='PARTIALLY_REFUNDED')
        OrderRefund.objects.create(order=order, amount=500, reason='测试')
        self.order(original=400, paid=400, gift=True, account_only=True)
        data = self.ranking()
        self.assertTrue(data['consume_rank_complete'])
        self.assertEqual(len(data['consume_rank']), 1)
        row = data['consume_rank'][0]
        self.assertEqual((row['customer_account_id'], row['user_id'], row['total_amount'], row['order_count']),
                         (self.account.pk, self.customer.pk, 2400, 2))

    def test_full_refund_zeroes_remaining_original_and_old_period_is_excluded(self):
        refunded = self.order(original=2500, paid=1700, status='REFUNDED')
        OrderRefund.objects.create(order=refunded, amount=1700, reason='测试')
        previous = self.order(original=900, paid=900)
        Order.objects.filter(pk=previous.pk).update(completed_at=self.now - timedelta(days=40))
        self.assertEqual(self.ranking()['consume_rank'][0]['total_amount'], 0)

    def test_missing_original_never_appears_as_zero_or_a_certified_rank(self):
        self.order(original=0, paid=300)
        data = self.ranking()
        self.assertFalse(data['consume_rank_complete'])
        self.assertEqual((data['consume_rank'][0]['rank'], data['consume_rank'][0]['total_amount']), (None, None))

    def test_refund_state_without_a_refund_record_is_not_assumed_zero(self):
        self.order(original=2500, paid=1700, status='REFUNDED')
        data = self.ranking()
        self.assertFalse(data['consume_rank_complete'])
        self.assertIsNone(data['consume_rank'][0]['total_amount'])
