from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from engagement.models import PointFundingLot
from engagement.services import allocate_order_points
from orders.continuations import award_one
from orders.models import OrderContinuation, OrderContinuationReward, OrderProvider
from orders.tasks import award_continuations
from orders.tests.factories import make_order, make_provider, make_user
from orders.tests.test_addon_configuration import staff_client
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(ORDER_CONTINUATION_REWARD_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=False, ALLOWED_HOSTS=['*'])
class RoiContinuationReceiptTest(TestCase):
    def setUp(self):
        buyer = make_user()
        provider = make_provider()
        buyer_account = get_or_create_account_for_legacy_user(buyer)
        provider_account = get_or_create_account_for_legacy_user(provider)
        self.provider_account_id = provider_account.pk
        settled = timezone.now()
        original = make_order(buyer, customer_account=buyer_account)
        self.order = make_order(buyer, provider=provider, customer_account=buyer_account,
                                provider_account=provider_account, status='COMPLETED',
                                payment_status='PAID', amount=20000, provider_income=20000,
                                shop_income=0, completed_at=settled, settled_at=settled)
        OrderProvider.objects.create(order=self.order, provider=provider, provider_account=provider_account,
            provider_business_cycle=provider_account.business_cycle, settlement_base=20000,
            provider_income=20000, settled_at=settled)
        buyer_wallet = get_wallet(account=buyer_account, user=buyer, for_update=True)
        PointFundingLot.objects.create(account=buyer_account, source_key='continuation-roi-principal',
                                       kind='PREPAID', remaining=20000)
        allocate_order_points(self.order, buyer_wallet, 20000)
        self.provider_wallet = get_wallet(account=provider_account, user=provider)
        self.provider_wallet.balance = 20000
        self.provider_wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=buyer_wallet, order=self.order, tx_type='PAY', amount=-20000,
                                   balance_before=20000, balance_after=0)
        Transaction.objects.create(wallet=self.provider_wallet, order=self.order, tx_type='INCOME', amount=20000,
                                   balance_before=0, balance_after=20000, remark=f'订单收入：{self.order.order_no}')
        self.staff = staff_client(['order:view', 'order:refund', 'support:edit', 'roi:view'])
        submitted = self.staff.post(f'/api/admin/orders/{self.order.pk}/continuation/',
            {'original_order_id': original.pk, 'client_request_id': 'continuation-roi-claim'}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.claim_id = submitted.data['data']['id']
        self.claim_url = f'/api/admin/continuations/{self.claim_id}/'
        reviewed = self.staff.post(self.claim_url,
            {'decision': 'VERIFIED', 'note': '核对属于同一场服务', 'client_request_id': 'continuation-roi-review'},
            format='json')
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(reviewed.data['data']['status'], 'VERIFIED')

    def pay_reward(self):
        self.assertEqual(award_continuations(), 1)
        self.reward = OrderContinuationReward.objects.get(continuation_id=self.claim_id)

    def refund_order(self):
        url = f'/api/admin/orders/{self.order.pk}/'
        preview = self.staff.get(url + 'refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refund = self.staff.post(url + 'refund/', {'reason': '隔离续单全退', 'client_request_id': 'continuation-roi-refund',
                                'plan_token': preview.data['data']['plan_token']}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        self.order.refresh_from_db()
        self.assertEqual((self.order.payment_status, self.order.refund_records.get().amount), ('REFUNDED', 20000))

    def roi(self):
        day = timezone.localdate(self.reward.transaction.created_at).isoformat()
        response = self.staff.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def test_keeps_valid_payout_once_after_full_refund(self):
        self.pay_reward()
        self.assertEqual(award_continuations(), 0)
        self.provider_wallet.refresh_from_db()
        self.assertEqual(self.provider_wallet.balance, 20500)
        self.assertEqual(OrderContinuationReward.objects.filter(continuation_id=self.claim_id).count(), 1)
        self.reward.refresh_from_db()
        self.assertIsNotNone(self.reward.notice_message_id)
        self.refund_order()
        self.provider_wallet.refresh_from_db()
        self.assertEqual(self.provider_wallet.balance, 500)
        self.assertFalse(self.reward.transaction.reversals.exists())
        ClubAccount.objects.filter(pk=self.provider_account_id).update(license_membership_active=False)
        result = self.roi()
        self.assertEqual(result['operating']['net_consumption_fen'], 0)
        self.assertEqual(result['operating']['continuation_reward_cost_fen'], 500)
        self.assertEqual(result['provisional']['continuation_reward_cost_fen'], 500)
        self.assertEqual(result['operating']['invalid_continuation_reward_ids'], [])

    def test_roi_requires_same_payout_evidence_as_order_detail(self):
        self.pay_reward()
        claim = OrderContinuation.objects.get(pk=self.claim_id)
        parts = claim.participants
        self.provider_wallet.user = None
        self.provider_wallet.save(update_fields=['user'])
        for participants in ([], [{**parts[0], 'provider_id': None}]):
            with self.subTest(participants=participants):
                OrderContinuation.objects.filter(pk=claim.pk).update(participants=participants)
                self.assert_roi_requires_review()
        self.provider_wallet.user_id = parts[0]['provider_id']
        self.provider_wallet.save(update_fields=['user'])
        for participants in ([{**parts[0], 'provider_business_cycle': parts[0]['provider_business_cycle'] + 1}],
                             [{**parts[0], 'provider_business_cycle': True}],
                             [{**parts[0], 'provider_id': parts[0]['provider_id'] + 1}]):
            with self.subTest(participants=participants):
                OrderContinuation.objects.filter(pk=claim.pk).update(participants=participants)
                self.assert_roi_requires_review()

    def assert_roi_requires_review(self):
        detail = self.staff.get(self.claim_url)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['data']['reward_state'], 'REVIEW')
        result = self.roi()
        self.assertIsNone(result['operating']['continuation_reward_cost_fen'])
        self.assertIsNone(result['provisional']['continuation_reward_cost_fen'])
        self.assertEqual(result['operating']['invalid_continuation_reward_ids'], [self.reward.pk])

    def test_roi_rejects_unverified_claim_and_nonstandard_reward_amount(self):
        self.pay_reward()
        OrderContinuation.objects.filter(pk=self.claim_id).update(status='REJECTED')
        self.assert_roi_requires_review()
        claim = OrderContinuation.objects.get(pk=self.claim_id)
        OrderContinuation.objects.filter(pk=claim.pk).update(status='VERIFIED', verified_at=None)
        self.assert_roi_requires_review()
        OrderContinuation.objects.filter(pk=claim.pk).update(verified_at=claim.verified_at)
        OrderContinuationReward.objects.filter(pk=self.reward.pk).update(amount=499)
        Transaction.objects.filter(pk=self.reward.transaction_id).update(amount=499, balance_after=20499)
        self.assert_roi_requires_review()

    def test_full_refund_before_payout_disqualifies_reward(self):
        self.refund_order()
        self.assertEqual(award_continuations(), 0)
        self.assertEqual(OrderContinuation.objects.get(pk=self.claim_id).reward_state, 'INELIGIBLE')
        self.assertFalse(OrderContinuationReward.objects.filter(continuation_id=self.claim_id).exists())

    def test_missing_reviewer_prevents_payout(self):
        OrderContinuation.objects.filter(pk=self.claim_id).update(verified_by=None)
        with self.assertRaisesMessage(ValueError, '续单缺少客服核实凭证'):
            award_one(self.claim_id)
        self.provider_wallet.refresh_from_db()
        self.assertEqual(self.provider_wallet.balance, 20000)
        self.assertFalse(OrderContinuationReward.objects.filter(continuation_id=self.claim_id).exists())
