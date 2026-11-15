from django.test import TestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from orders.referral_evidence import ReferralEvidenceError, paid_referral_credits
from orders.tests.factories import make_order, make_provider, make_user
from wallet.models import Transaction
from wallet.services import get_wallet


class ReferralEvidenceTest(TestCase):
    def setUp(self):
        self.boss = make_user()
        self.player = make_provider()
        self.account = get_or_create_account_for_legacy_user(self.player)
        link_legacy_relations(self.player, self.account)
        self.wallet = get_wallet(user=self.player, account=self.account)
        self.order = make_order(self.boss, provider=self.player, provider_account=self.account,
                                inviter=self.player, inviter_account=self.account, inviter_commission=500)

    def credit(self, amount, remark, wallet=None):
        return Transaction.objects.create(order=self.order, wallet=wallet or self.wallet,
                                          tx_type='INCOME', amount=amount, remark=remark)

    def test_referral_is_separate_when_provider_uses_same_wallet(self):
        income = self.credit(7500, f'订单收入：{self.order.order_no}')
        referral = self.credit(500, f'推荐分佣：{self.order.order_no}')
        self.assertEqual(paid_referral_credits(self.order, [income, referral], expected_amount=500), [referral])

    def test_missing_or_conflicting_referral_evidence_is_rejected(self):
        income = self.credit(8000, f'订单收入：{self.order.order_no}')
        with self.assertRaises(ReferralEvidenceError):
            paid_referral_credits(self.order, [income], expected_amount=500)
        stranger = make_user()
        other_wallet = get_wallet(user=stranger, account=get_or_create_account_for_legacy_user(stranger))
        referral = self.credit(500, f'推荐分佣：{self.order.order_no}', wallet=other_wallet)
        with self.assertRaises(ReferralEvidenceError):
            paid_referral_credits(self.order, [referral], expected_amount=500)

    def test_legacy_wallet_mapping_does_not_override_explicit_order_account(self):
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(other, other_account)
        other_wallet = get_wallet(user=other, account=other_account)
        other_wallet.account = None
        other_wallet.save(update_fields=['account'])
        referral = self.credit(500, f'礼物推荐分佣：{self.order.order_no}', wallet=other_wallet)
        self.order.inviter_id = other.pk  # Conflicting legacy identity cannot override explicit account.
        with self.assertRaises(ReferralEvidenceError):
            paid_referral_credits(self.order, [referral], expected_amount=500)
