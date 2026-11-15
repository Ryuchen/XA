from django.test import TestCase
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderRefund
from orders.refunds import RefundError, build_refund_plan
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from rest_framework.test import APIClient
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


class RefundReferralGuardTests(TestCase):
    def setUp(self):
        self.boss, self.provider = make_user(), make_provider()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        for user, account in ((self.boss, self.boss_account), (self.provider, self.provider_account)):
            link_legacy_relations(user, account)
        self.customer_wallet = get_wallet(user=self.boss, account=self.boss_account)
        self.provider_wallet = get_wallet(user=self.provider, account=self.provider_account)
        self.platform = get_platform_wallet()
        self.order = make_order(self.boss, provider=self.provider, customer_account=self.boss_account,
            provider_account=self.provider_account, inviter=self.provider, inviter_account=self.provider_account,
            amount=10000, provider_income=8000, inviter_commission=500, shop_income=1500,
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
            completed_at=timezone.now(), settled_at=timezone.now())
        self.provider_wallet.balance = 8500
        self.provider_wallet.save(update_fields=['balance'])
        self.platform.balance = 1500
        self.platform.save(update_fields=['balance'])
        Transaction.objects.create(order=self.order, wallet=self.customer_wallet, tx_type='PAY', amount=-10000,
                                   balance_before=10000, balance_after=0)
        Transaction.objects.create(order=self.order, wallet=self.provider_wallet, tx_type='INCOME', amount=8000,
                                   balance_before=0, balance_after=8000, remark=f'订单收入：{self.order.order_no}')
        self.referral = Transaction.objects.create(order=self.order, wallet=self.provider_wallet, tx_type='INCOME', amount=500,
                                   balance_before=8000, balance_after=8500, remark=f'推荐分佣：{self.order.order_no}')
        Transaction.objects.create(order=self.order, wallet=self.platform, tx_type='SHOP_INCOME', amount=1500,
                                   balance_before=0, balance_after=1500)

    def test_referral_is_never_recovered_even_when_same_wallet_also_contains_service_income(self):
        before = list(Transaction.objects.filter(order=self.order).values_list('pk', 'amount', 'reversal_of_id'))
        with self.assertRaises(RefundError) as error:
            build_refund_plan(self.order, refund_amount=5000)
        self.assertEqual(error.exception.code, 'REFUND_REFERRAL_STORE_FUNDING_REQUIRED')
        self.provider_wallet.refresh_from_db()
        self.assertEqual(self.provider_wallet.balance, 8500)
        self.assertEqual(list(Transaction.objects.filter(order=self.order).values_list('pk', 'amount', 'reversal_of_id')), before)
        self.assertFalse(self.referral.reversals.exists())

    def test_missing_referral_evidence_is_not_guessed_from_wallet_identity(self):
        self.referral.remark = '历史用途未注明'
        self.referral.save(update_fields=['remark'])
        with self.assertRaises(RefundError) as error:
            build_refund_plan(self.order, refund_amount=5000)
        self.assertEqual(error.exception.code, 'REFUND_REFERRAL_EVIDENCE_REQUIRED')
        self.assertFalse(self.referral.reversals.exists())

    def test_admin_preview_and_submit_leave_no_refund_or_wallet_mutation(self):
        staff = make_console_user(['order:view', 'order:refund'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        preview = client.get(f'/api/admin/orders/{self.order.pk}/refund-preview/', {'refund_amount': 5000})
        self.assertEqual(preview.data['errorCode'], 'REFUND_REFERRAL_STORE_FUNDING_REQUIRED')
        response = client.post(f'/api/admin/orders/{self.order.pk}/refund/', {
            'refund_amount': 5000, 'reason': '隔离退款', 'client_request_id': 'guard-refund',
            'plan_token': 'unusable',
        }, format='json')
        self.assertEqual(response.data['errorCode'], 'REFUND_REFERRAL_STORE_FUNDING_REQUIRED')
        self.assertFalse(OrderRefund.objects.filter(order=self.order).exists())
        self.order.refresh_from_db()
        self.provider_wallet.refresh_from_db()
        self.platform.refresh_from_db()
        self.assertEqual((self.order.payment_status, self.provider_wallet.balance, self.platform.balance), ('PAID', 8500, 1500))
        self.assertFalse(self.referral.reversals.exists())
