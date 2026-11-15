from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointLedger
from engagement.services import config
from orders.commission import fixed_specs
from orders.models import Order
from orders.points_funding_plan import PointsFundingPlanError, plan_points_funding
from orders.points_redemption_preview import preview_service_points
from orders.serializers import CreateOrderSerializer
from orders.settlement import ProviderShare, build_provider_shares
from orders.tests.factories import make_service, make_user
from wallet.models import Transaction
from wallet.services import get_wallet


class PointsFundingPlanTests(SimpleTestCase):
    def test_gift_and_compensation_need_store_funding_without_reducing_referral_base(self):
        shares = build_provider_shares(2000, [{'commission_rate': 20}])
        plan = plan_points_funding(deal_units=2000, point_discount_units=300, shares=shares, inviter_rate=10,
                                   principal_units=1100, gift_units=400, compensation_units=200)
        self.assertEqual((plan.customer_coin_units, plan.principal_units, plan.gift_units, plan.compensation_units),
                         (1700, 1100, 400, 200))
        self.assertEqual((plan.provider_income_units, plan.inviter_base_units, plan.inviter_income_units,
                          plan.additional_store_funding_units, plan.store_income_units), (1600, 1700, 170, 670, 0))
        self.assertEqual(plan.principal_units + plan.additional_store_funding_units,
                         plan.provider_income_units + plan.inviter_income_units + plan.store_income_units)

    def test_inviter_commission_uses_paid_amount_and_store_funds_shortfall(self):
        shares = build_provider_shares(2000, [{'commission_rate': 20}])
        plan = plan_points_funding(deal_units=2000, point_discount_units=300, shares=shares, inviter_rate=10)
        self.assertEqual((plan.customer_coin_units, plan.provider_income_units, plan.gross_store_margin_units),
                         (1700, 1600, 400))
        self.assertEqual((plan.net_store_margin_units, plan.inviter_base_units, plan.inviter_income_units,
                          plan.store_income_units, plan.additional_store_funding_units), (100, 1700, 170, 0, 70))

    def test_discount_exceeds_margin_creating_funding_need_without_negative_commission(self):
        shares = build_provider_shares(2000, [{'commission_rate': 20}])
        plan = plan_points_funding(deal_units=2000, point_discount_units=500, shares=shares, inviter_rate=10)
        self.assertEqual((plan.customer_coin_units, plan.provider_income_units, plan.net_store_margin_units),
                         (1500, 1600, -100))
        self.assertEqual((plan.inviter_base_units, plan.inviter_income_units, plan.store_income_units,
                          plan.additional_store_funding_units), (1500, 150, 0, 250))

    def test_two_providers_keep_their_individual_deal_based_shares(self):
        shares = build_provider_shares(2530, [{'commission_rate': 20}, {'commission_rate': 30}])
        plan = plan_points_funding(deal_units=2530, point_discount_units=300, shares=shares, inviter_rate=10)
        self.assertEqual([share.provider_income for share in shares], [1012, 886])
        self.assertEqual((plan.customer_coin_units, plan.provider_income_units, plan.inviter_income_units,
                          plan.store_income_units, plan.additional_store_funding_units), (2230, 1898, 223, 109, 0))

    def test_fixed_fee_uses_preallocated_seat_snapshots_not_full_fee_per_person(self):
        shares = build_provider_shares(1000, fixed_specs(1000, 2, 200))
        self.assertEqual([share.commission_fixed for share in shares], [100, 100])
        plan = plan_points_funding(deal_units=1000, point_discount_units=300, shares=shares, inviter_rate=10)
        self.assertEqual((plan.provider_income_units, plan.customer_coin_units,
                          plan.additional_store_funding_units, plan.inviter_income_units), (800, 700, 170, 70))

    def test_rejects_invalid_amounts_unverified_shares_and_mismatched_snapshots(self):
        shares = build_provider_shares(1000, [{'commission_rate': 20}])
        invalid = [
            {'deal_units': 0}, {'point_discount_units': 1000}, {'point_discount_units': 11},
            {'point_discount_units': True}, {'inviter_rate': 101}, {'inviter_rate': 1.5},
            {'shares': []}, {'shares': [object()]},
            {'shares': [ProviderShare(999, 'PERCENT', 20, 0, 800)]},
            {'shares': [ProviderShare(1000, 'PERCENT', 20, 0, 801)]},
            {'shares': [ProviderShare(1000, 'FIXED', 0, 1001, 0)]},
            {'principal_units': 800, 'gift_units': 101},
            {'principal_units': 800, 'gift_units': 200},
            {'principal_units': True},
        ]
        for changes in invalid:
            args = {'deal_units': 1000, 'point_discount_units': 100, 'shares': shares, 'inviter_rate': 10}
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(PointsFundingPlanError):
                plan_points_funding(**args)

    def test_funding_conservation_and_nonnegative_recipient_amounts_across_boundaries(self):
        for deal in (10, 100, 1010, 10000):
            for rate_a in (0, 20, 50, 100):
                for rate_b in (0, 30, 100):
                    for discount in (0, (deal // 20) * 10, deal - 10):
                        for referral_rate in (0, 10, 100):
                            shares = build_provider_shares(deal, [{'commission_rate': rate_a}, {'commission_rate': rate_b}])
                            plan = plan_points_funding(deal_units=deal, point_discount_units=discount,
                                                       shares=shares, inviter_rate=referral_rate)
                            self.assertEqual(plan.customer_coin_units + plan.additional_store_funding_units,
                                plan.provider_income_units + plan.inviter_income_units + plan.store_income_units)
                            self.assertGreaterEqual(min(plan.inviter_income_units, plan.store_income_units,
                                                        plan.additional_store_funding_units), 0)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, POINTS_USAGE_SWITCHES_ENABLED=True)
class PointsFundingQuoteIntegrationTests(TestCase):
    def test_real_service_price_and_points_rules_produce_the_confirmed_plan_without_writes(self):
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 3000; wallet.save(update_fields=['balance'])
        profile = MemberProfile.objects.create(account=account, points_balance=10000)
        config()
        service = make_service(price=2000, commission_rate=20)
        save_points_terms(group_point_hundredths=10000, group_coin_units=300,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        request = SimpleNamespace(account=account, legacy_user=user)
        serializer = CreateOrderSerializer(data={'service_id': service.pk}, context={'request': request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        values = serializer.validated_data
        before = (wallet.balance, profile.points_balance, Order.objects.count(),
                  Transaction.objects.count(), PointLedger.objects.count())
        quote = preview_service_points(account=account, service=service, deal_amount=values['amount'], selected_points=10000)
        self.assertTrue(quote['applicable'])
        self.assertFalse(quote['payment_available'])
        shares = build_provider_shares(values['amount'], [values['commission_spec']])
        plan = plan_points_funding(deal_units=values['amount'], point_discount_units=quote['discount_units'],
                                   shares=shares, inviter_rate=10)
        self.assertEqual((plan.deal_units, plan.customer_coin_units, plan.provider_income_units,
                          plan.inviter_income_units, plan.store_income_units), (2000, 1700, 1600, 170, 0))
        wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, profile.points_balance, Order.objects.count(),
                          Transaction.objects.count(), PointLedger.objects.count()), before)
