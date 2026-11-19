import os
from unittest import skipUnless

from django.test import SimpleTestCase, TestCase

from engagement.vip_recalculation_plan import BossFact, TierRule, plan_changes, plan_unknown_baseline, read_store_snapshot


class VipRecalculationPlanTest(SimpleTestCase):
    def setUp(self):
        self.low = TierRule(1, 11, 1, 100)
        self.high = TierRule(3, 33, 3, 900)

    def test_threshold_changes_use_confirmed_net_and_do_not_duplicate_rewards(self):
        lower = TierRule(3, 33, 3, 500)
        bosses = [BossFact(1, 1, 11, 499), BossFact(2, 1, 11, 500),
                  BossFact(3, 1, 11, 700, frozenset({3})), BossFact(4, 1, 33, 1000)]
        changes = plan_changes([self.low, self.high], [self.low, lower], bosses)
        self.assertEqual([(x.account_id, x.new_boss_type_id, x.newly_eligible_tier_ids) for x in changes],
                         [(2, 33, (3,)), (3, 33, ())])

    def test_new_intermediate_level_can_grant_without_changing_current_level(self):
        middle = TierRule(2, 22, 2, 300)
        boss = BossFact(1, 2, 33, 1000, frozenset({1, 3}))
        change = plan_changes([self.low, self.high], [self.low, middle, self.high], [boss])[0]
        self.assertEqual((change.old_boss_type_id, change.new_boss_type_id, change.newly_eligible_tier_ids),
                         (33, 33, (2,)))

    def test_deferred_changes_compare_with_rules_before_vip_was_disabled(self):
        dormant = TierRule(2, 22, 2, 300, active=False)
        enabled = TierRule(2, 22, 2, 300, active=True)
        boss = BossFact(1, 1, 11, 500, frozenset({1}))
        changes = plan_changes([self.low, dormant, self.high], [self.low, enabled, self.high], [boss])
        self.assertEqual((changes[0].new_boss_type_id, changes[0].newly_eligible_tier_ids), (22, (2,)))

    def test_deactivation_and_non_vip_grades_remain_distinct(self):
        inactive = TierRule(3, 33, 3, 900, active=False)
        bosses = [BossFact(1, 1, 33, 1000), BossFact(2, 1, 99, 50), BossFact(3, 1, 33, 50)]
        changes = plan_changes([self.low, self.high], [self.low, inactive], bosses)
        self.assertEqual([(x.account_id, x.new_boss_type_id) for x in changes], [(1, 11), (3, None)])

    def test_changed_level_of_same_tier_is_an_impact(self):
        moved = TierRule(1, 11, 2, 100)
        change = plan_changes([self.low], [moved], [BossFact(1, 1, 11, 500, frozenset({1}))])[0]
        self.assertEqual((change.old_boss_type_id, change.new_boss_type_id,
                          change.old_level, change.new_level), (11, 11, 1, 2))
        self.assertEqual(change.newly_eligible_tier_ids, ())

    def test_reactivating_the_tier_already_held_is_not_a_new_upgrade(self):
        inactive = TierRule(3, 33, 3, 900, active=False)
        boss = BossFact(1, 1, 33, 1000)
        self.assertEqual(plan_changes([self.low, inactive], [self.low, self.high], [boss]), [])

    def test_unknown_baseline_recalculates_and_surfaces_missing_issuance_for_review(self):
        bosses = [BossFact(1, 1, 33, 500, frozenset({1})), BossFact(2, 1, 33, 1000, frozenset({1}))]
        plan = plan_unknown_baseline([self.low, self.high], bosses)
        self.assertEqual([(item.account_id, item.new_boss_type_id, item.newly_eligible_tier_ids) for item in plan],
                         [(1, 11, ()), (2, 33, (3,))])

    def test_creating_vip_for_an_existing_non_vip_type_is_a_new_upgrade(self):
        new = TierRule(-1, 99, 2, 200)
        boss = BossFact(1, 1, 99, 500)
        change = plan_changes([self.low], [self.low, new], [boss])[0]
        self.assertEqual((change.old_boss_type_id, change.new_boss_type_id,
                          change.old_level, change.new_level, change.newly_eligible_tier_ids), (99, 99, 0, 2, (1, -1)))

    def test_invalid_or_duplicate_inputs_do_not_produce_a_plan(self):
        with self.assertRaises(ValueError):
            plan_changes([self.low, self.low], [self.high], [])
        with self.assertRaises(ValueError):
            plan_changes([self.low], [self.high], [BossFact(1, 1, 11, -1)])


class VipRecalculationSnapshotTest(TestCase):
    def test_only_current_cycle_confirmed_paid_net_is_counted(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from engagement.models import ConsumptionConfirmation, MemberBenefitGrant, VipTier
        from orders.tests.factories import make_boss_type, make_order, make_user

        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        tier = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100)
        account.boss_type = tier.boss_type; account.save(update_fields=['boss_type'])
        for cycle, paid, amount, refunded in [(1, 'PAID', 300, 0), (1, 'PARTIALLY_REFUNDED', 500, 100),
                                               (1, 'REFUNDED', 400, 0), (2, 'PAID', 900, 0)]:
            order = make_order(user, customer_account=account, amount=amount, payment_status=paid)
            ConsumptionConfirmation.objects.create(order=order, account=account, business_cycle=cycle,
                                                   amount=amount, refunded_amount=refunded)
        MemberBenefitGrant.objects.create(account=account, tier=tier, kind='UPGRADE', name='已有权益',
                                           event_key=f'vip:{account.pk}:1:1:upgrade')
        tiers, bosses = read_store_snapshot()
        self.assertEqual(len(tiers), 1)
        self.assertEqual((bosses[0].net_consumption, bosses[0].issued_tier_ids), (700, frozenset({tier.pk})))


class VipRecalculationPreviewTest(TestCase):
    def setUp(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from engagement.models import ConsumptionConfirmation, VipTier
        from engagement.services import config
        from orders.tests.factories import make_boss_type, make_console_user, make_order, make_user
        from rest_framework.test import APIClient

        user = make_user(); self.account = get_or_create_account_for_legacy_user(user)
        self.low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100)
        self.high = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=900)
        self.account.boss_type = self.low.boss_type; self.account.save(update_fields=['boss_type'])
        order = make_order(user, customer_account=self.account, amount=500, payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=self.account,
            business_cycle=self.account.business_cycle, amount=500)
        config()
        staff = make_console_user(['engagement:view', 'engagement:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.url = f'/api/admin/vip-tiers/{self.high.pk}/recalculation-preview/'

    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                '重算预览 executable 受 VIP 月度切换门禁属正式契约深水区（18-盘点 VIP 月券及券条款保护待联调）；最终联调启用')
    def test_preview_is_read_only_and_reports_candidate_upgrades(self):
        response = self.client.post(self.url, {'consumption_threshold': 500}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual((data['account_count'], data['affected_count'], data['upgrade_count'],
                          data['candidate_upgrade_rewards'], data['executable']), (1, 1, 1, 1, False))
        self.high.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual((self.high.recharge_threshold, self.account.boss_type_id), (900, self.low.boss_type_id))

    def test_disabled_vip_is_marked_deferred_and_unknown_fields_rejected(self):
        from engagement.services import config
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        response = self.client.post(self.url, {'consumption_threshold': 500}, format='json')
        self.assertTrue(response.data['data']['vip_disabled'])
        denied = self.client.post(self.url, {'benefits': {'伪造': '不允许'}}, format='json')
        self.assertEqual(denied.status_code, 400, denied.data)
        denied = self.client.post(self.url, ['invalid'], format='json')
        self.assertEqual(denied.status_code, 400, denied.data)

    def test_preview_requires_edit_permission(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient
        staff = make_console_user(['engagement:view'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        viewer = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(viewer.post(self.url, {'consumption_threshold': 500}, format='json').status_code, 403)
