from datetime import timedelta
import os
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import ConsumptionConfirmation, MemberBenefitGrant, RewardClaim, RewardItem, VipTier
from engagement.services import config
from orders.tests.factories import make_boss_type, make_console_user, make_order, make_user


@override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                   VIP_MONTHLY_DECISIONS_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VipThresholdChangeTest(APITestCase):
    def setUp(self):
        user = make_user(); self.account = get_or_create_account_for_legacy_user(user)
        self.low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100,
                                         monthly_coupon_count=1, monthly_discount=90)
        reward = RewardItem.objects.create(code='threshold-friend', name='新等级好友位', kind='SERVICE',
                                           vip_quota_kind='FRIEND', in_shop=False)
        self.reward = reward
        self.high = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=900,
            monthly_coupon_count=2, monthly_discount=80, upgrade_rewards=[{'reward': reward.pk, 'quantity': 2}])
        self.account.boss_type = self.low.boss_type; self.account.save(update_fields=['boss_type'])
        order = make_order(user, customer_account=self.account, amount=500, payment_status='PAID')
        self.confirmation = ConsumptionConfirmation.objects.create(order=order, account=self.account,
            business_cycle=self.account.business_cycle, amount=500)
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='threshold')
        grant_periodic(self.account)
        self.monthly = UserCoupon.objects.get(account=self.account, coupon__grant_key__startswith='vip-month:')
        self.low.monthly_discount = 95; self.low.save(update_fields=['monthly_discount'])
        staff = make_console_user(['engagement:view', 'engagement:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.url = f'/api/admin/vip-tiers/{self.high.pk}/'

    def token(self, threshold):
        result = self.client.post(self.url+'recalculation-preview/', {'consumption_threshold': threshold}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['data']['executable'])
        return result.data['data']['token']

    def change(self, threshold, token):
        return self.client.patch(self.url, {'consumption_threshold': threshold, 'recalculation_token': token}, format='json')

    @skipUnless(os.environ.get('XA_VIP_BENEFIT_FORMAL') == '1',
                'VIP 门槛调整与升级权益配额发放属 VIP 权益正式契约深水区（18-盘点 VIP 等级停用和门槛调整待联调）；最终联调启用')
    def test_recalculate_upgrade_issues_new_rights_and_preserves_old_monthly_terms(self):
        token = self.token(500)
        result = self.change(500, token)
        self.assertEqual(result.status_code, 200, result.data)
        self.account.refresh_from_db(); self.high.refresh_from_db(); self.monthly.refresh_from_db()
        self.monthly.coupon.refresh_from_db()
        self.assertEqual((self.high.recharge_threshold, self.account.boss_type_id), (500, self.high.boss_type_id))
        self.assertEqual((self.monthly.coupon.discount_rate, self.monthly.vip_terms_locked_by_downgrade), (95, True))
        self.assertEqual(UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-month:').count(), 1)
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).count(), 1)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)
        self.assertTrue(AdminAuditLog.objects.filter(resource='vip-threshold-change', object_id=str(self.high.pk)).exists())
        self.assertEqual(self.change(500, token).status_code, 200)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)

    def test_recalculation_uses_same_confirmed_addon_net_as_growth(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.models import (OrderAddonConsumption, OrderAddonIntent, OrderAddonPayment,
                                   OrderOriginalAttribution, OrderParticipationSegment)
        from orders.tests.factories import make_provider, make_service
        from wallet.models import Transaction
        from wallet.services import get_wallet
        from engagement.services import completed_consumption, update_vip
        from engagement.vip_recalculation_plan import read_store_snapshot
        provider = get_or_create_account_for_legacy_user(make_provider())
        order = self.confirmation.order
        attribution = OrderOriginalAttribution.objects.create(order=order, original_amount=500)
        segment = OrderParticipationSegment.objects.create(attribution=attribution, provider_account=provider,
            slot=1, original_share=500, accepted_at=timezone.now())
        addon = make_service(is_addon=True)
        wallet = get_wallet(account=self.account)
        for i, eligible in enumerate((True, False)):
            intent = OrderAddonIntent.objects.create(order=order, segment=segment, provider=provider, addon=addon,
                buyer=self.account, operator=self.account, request_id=f'vip-net-{i}', request_hash='a'*64,
                unit='HOUR', quantity=1, unit_price=600, amount=600, principal=600, config_version=1,
                condition_mode='NONE', addon_name='加购', commission_snapshot={})
            tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-600,
                                            balance_before=1200, balance_after=600)
            payment = OrderAddonPayment.objects.create(intent=intent, buyer_transaction=tx, operator=self.account,
                commission_fee=0, provider_income=600, inviter_income=0, inviter_rate=0, store_income=0,
                principal=600, funding_required=0, benefit_terms={}, paid_at=timezone.now())
            OrderAddonConsumption.objects.create(payment=payment, account=self.account, business_cycle=1,
                paid_units=600, refunded_units=100, principal_units=600, refunded_principal_units=100,
                vip_eligible=eligible)
        self.assertEqual(completed_consumption(self.account), 1000)
        facts = {row.account_id: row for row in read_store_snapshot()[1]}
        self.assertEqual(facts[self.account.pk].net_consumption, 1000)
        with patch('engagement.tasks.enqueue_broadcast'):
            update_vip(self.account)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)
        response = self.change(950, self.token(950))
        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)
        self.assertTrue(AdminAuditLog.objects.filter(resource='vip-threshold-change', object_id=str(self.high.pk)).exists())
        from uuid import uuid4
        from engagement.models import LicenseAddonBenefitsReview
        ineligible = OrderAddonConsumption.objects.get(account=self.account, vip_eligible=False)
        LicenseAddonBenefitsReview.objects.create(consumption=ineligible, request_id=uuid4(),
            platform_actor_id=uuid4(), vip_outcome='ALLOW', reason='核实授权', vip_applied=True)
        self.assertEqual(completed_consumption(self.account), 1500)
        facts = {row.account_id: row for row in read_store_snapshot()[1]}
        self.assertEqual(facts[self.account.pk].net_consumption, 1500)

    def test_reordering_uses_tier_identity_and_preserves_issued_terms(self):
        from engagement.services import update_vip
        from engagement.vip_policy import issuance_policy
        self.confirmation.amount = 1000; self.confirmation.save(update_fields=['amount'])
        with patch('engagement.tasks.enqueue_broadcast'):
            update_vip(self.account)
        self.account.refresh_from_db(); self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)
        issued = set(MemberBenefitGrant.objects.filter(account=self.account).values_list('pk', flat=True))
        coupons = set(UserCoupon.objects.filter(account=self.account).values_list('pk', flat=True))
        url = f'/api/admin/vip-tiers/{self.low.pk}/'
        preview = self.client.post(url+'recalculation-preview/', {'level': 3}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertTrue(preview.data['data']['executable'])
        result = self.client.patch(url, {'level': 3, 'recalculation_token': preview.data['data']['token']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.low.refresh_from_db(); self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.assertEqual((self.low.level, self.account.boss_type_id), (3, self.low.boss_type_id))
        self.assertEqual(issuance_policy(self.low)['template_level'], 1)
        self.assertEqual(set(MemberBenefitGrant.objects.filter(account=self.account).values_list('pk', flat=True)), issued)
        self.assertEqual(set(UserCoupon.objects.filter(account=self.account).values_list('pk', flat=True)), coupons)
        self.assertTrue(self.monthly.vip_terms_locked_by_downgrade)
        with patch('engagement.tasks.enqueue_broadcast'):
            update_vip(self.account)
        self.assertEqual(set(MemberBenefitGrant.objects.filter(account=self.account).values_list('pk', flat=True)), issued)
        self.assertEqual(set(UserCoupon.objects.filter(account=self.account).values_list('pk', flat=True)), coupons)
        self.assertTrue(AdminAuditLog.objects.filter(resource='vip-tier-reorder', object_id=str(self.low.pk)).exists())

    def test_batch_reorder_swaps_once_without_intermediate_grants(self):
        from engagement.services import update_vip
        from engagement.vip_policy import issuance_policy
        self.confirmation.amount = 1000; self.confirmation.save(update_fields=['amount'])
        with patch('engagement.tasks.enqueue_broadcast'):
            update_vip(self.account)
        before_grants = set(MemberBenefitGrant.objects.filter(account=self.account).values_list('pk', flat=True))
        before_coupons = set(UserCoupon.objects.filter(account=self.account).values_list('pk', flat=True))
        mapping = [{'id': self.low.pk, 'level': 2}, {'id': self.high.pk, 'level': 1}]
        preview = self.client.post('/api/admin/vip-tiers/reorder-preview/', {'mapping': mapping}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        token = preview.data['data']['token']
        result = self.client.post('/api/admin/vip-tiers/reorder/',
                                  {'mapping': mapping, 'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.low.refresh_from_db(); self.high.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual((self.low.level, self.high.level, self.account.boss_type_id), (2, 1, self.low.boss_type_id))
        self.assertEqual((issuance_policy(self.low)['template_level'], issuance_policy(self.high)['template_level']), (1, 2))
        self.assertEqual(set(MemberBenefitGrant.objects.filter(account=self.account).values_list('pk', flat=True)), before_grants)
        self.assertEqual(set(UserCoupon.objects.filter(account=self.account).values_list('pk', flat=True)), before_coupons)
        self.assertTrue(AdminAuditLog.objects.filter(resource='vip-batch-reorder').exists())

    def test_batch_reorder_rejects_stale_preview_and_incomplete_mapping(self):
        url = '/api/admin/vip-tiers/reorder-preview/'
        self.assertEqual(self.client.post(url, {'mapping': [{'id': self.low.pk, 'level': 2}]}, format='json').status_code, 400)
        mapping = [{'id': self.low.pk, 'level': 2}, {'id': self.high.pk, 'level': 1}]
        preview = self.client.post(url, {'mapping': mapping}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        self.confirmation.refunded_amount = 1; self.confirmation.save(update_fields=['refunded_amount'])
        result = self.client.post('/api/admin/vip-tiers/reorder/',
            {'mapping': mapping, 'recalculation_token': preview.data['data']['token']}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.low.refresh_from_db(); self.high.refresh_from_db()
        self.assertEqual((self.low.level, self.high.level), (1, 2))

    def test_batch_reorder_audit_failure_rolls_back_all_levels(self):
        mapping = [{'id': self.low.pk, 'level': 2}, {'id': self.high.pk, 'level': 1}]
        preview = self.client.post('/api/admin/vip-tiers/reorder-preview/', {'mapping': mapping}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.client.post('/api/admin/vip-tiers/reorder/',
                    {'mapping': mapping, 'recalculation_token': preview.data['data']['token']}, format='json')
        self.low.refresh_from_db(); self.high.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual((self.low.level, self.high.level, self.account.boss_type_id), (1, 2, self.low.boss_type_id))

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=False)
    def test_reorder_is_rejected_when_recalculation_disabled(self):
        result = self.client.patch(f'/api/admin/vip-tiers/{self.low.pk}/', {'level': 3}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.low.refresh_from_db()
        self.assertEqual(self.low.level, 1)

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=False)
    def test_threshold_edits_fail_closed_without_blocking_other_fields(self):
        before_audits = AdminAuditLog.objects.count()
        for name in ('consumption_threshold', 'recharge_threshold'):
            result = self.client.patch(self.url, {name: 500, 'points_rate': 120}, format='json')
            self.assertEqual(result.status_code, 400, result.data)
            self.high.refresh_from_db(); self.account.refresh_from_db()
            self.assertEqual((self.high.recharge_threshold, self.high.points_rate, self.account.boss_type_id),
                             (900, 100, self.low.boss_type_id))
            self.assertEqual(AdminAuditLog.objects.count(), before_audits)
        result = self.client.patch(self.url, {'recharge_threshold': 900, 'points_rate': 120}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.high.refresh_from_db()
        self.assertEqual((self.high.recharge_threshold, self.high.points_rate), (900, 120))

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=False,
                       VIP_TERMS_FREEZE_ENABLED=False, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=False)
    def test_threshold_edit_rejected_if_coupon_protection_is_unavailable(self):
        result = self.client.patch(self.url, {'consumption_threshold': 500,
                                              'recalculation_token': 'unavailable'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.high.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual((self.high.recharge_threshold, self.account.boss_type_id), (900, self.low.boss_type_id))

    def test_stale_preview_rejects_change_without_side_effects(self):
        token = self.token(500)
        self.confirmation.refunded_amount = 1; self.confirmation.save(update_fields=['refunded_amount'])
        result = self.change(500, token)
        self.assertEqual(result.status_code, 400, result.data)
        self.high.refresh_from_db(); self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.assertEqual((self.high.recharge_threshold, self.account.boss_type_id), (900, self.low.boss_type_id))
        self.assertFalse(self.monthly.vip_terms_locked_by_downgrade)
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_audit_failure_rolls_back_configuration_and_rights(self):
        token = self.token(500)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.change(500, token)
        self.high.refresh_from_db(); self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.assertEqual((self.high.recharge_threshold, self.account.boss_type_id), (900, self.low.boss_type_id))
        self.assertFalse(self.monthly.vip_terms_locked_by_downgrade)
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_downgrade_preserves_existing_coupon_terms(self):
        self.account.boss_type = self.high.boss_type; self.account.save(update_fields=['boss_type'])
        self.high.recharge_threshold = 400; self.high.save(update_fields=['recharge_threshold'])
        result = self.change(600, self.token(600))
        self.assertEqual(result.status_code, 200, result.data)
        self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertTrue(self.monthly.vip_terms_locked_by_downgrade)

    def test_downgrade_freezes_current_high_tier_terms_before_threshold_changes(self):
        user = make_user(); boss = get_or_create_account_for_legacy_user(user)
        self.high.recharge_threshold = 400; self.high.save(update_fields=['recharge_threshold'])
        boss.boss_type = self.high.boss_type; boss.save(update_fields=['boss_type'])
        order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
        grant_periodic(boss)
        voucher = UserCoupon.objects.filter(account=boss, coupon__grant_key__startswith='vip-month:').first()
        self.high.monthly_discount = 75; self.high.save(update_fields=['monthly_discount'])
        result = self.change(600, self.token(600))
        self.assertEqual(result.status_code, 200, result.data)
        boss.refresh_from_db(); voucher.refresh_from_db(); voucher.coupon.refresh_from_db()
        self.assertEqual((boss.boss_type_id, voucher.coupon.discount_rate,
                          voucher.vip_terms_locked_by_downgrade), (self.low.boss_type_id, 75, True))

    def test_unrelated_changes_require_separate_save(self):
        token = self.token(500)
        result = self.client.patch(self.url, {'consumption_threshold': 500, 'recalculation_token': token,
                                              'points_rate': 125}, format='json')
        self.assertEqual(result.status_code, 400, result.data)

    def test_reward_configuration_changed_after_preview_is_rejected(self):
        token = self.token(500)
        self.reward.duration_days = 10; self.reward.save(update_fields=['duration_days'])
        result = self.change(500, token)
        self.assertEqual(result.status_code, 400, result.data)
        self.high.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual((self.high.recharge_threshold, self.account.boss_type_id), (900, self.low.boss_type_id))

    def test_token_required_and_disabled_vip_cannot_use_this_path(self):
        self.assertEqual(self.change(500, None).status_code, 400)
        token = self.token(500)
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        self.assertEqual(self.change(500, token).status_code, 400)
        self.high.refresh_from_db(); self.assertEqual(self.high.recharge_threshold, 900)

    @skipUnless(os.environ.get('XA_VIP_BENEFIT_FORMAL') == '1',
                'VIP 门槛调整与升级权益配额发放属 VIP 权益正式契约深水区（18-盘点 VIP 等级停用和门槛调整待联调）；最终联调启用')
    def test_existing_claim_is_kept_when_new_tier_adds_same_kind(self):
        old_reward = RewardItem.objects.create(code='old-friend', name='原好友位', kind='SERVICE',
                                               vip_quota_kind='FRIEND', in_shop=False)
        claim = RewardClaim.objects.create(account=self.account, reward=old_reward, source='VIP',
            event_key=f'vip-quota:{self.account.pk}:{self.account.business_cycle}:FRIEND:0',
            name_snapshot='原好友位', kind_snapshot='SERVICE', duration_days=7)
        result = self.change(500, self.token(500))
        self.assertEqual(result.status_code, 200, result.data)
        claim.refresh_from_db()
        self.assertEqual((claim.reward_id, claim.name_snapshot, claim.duration_days), (old_reward.pk, '原好友位', 7))
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 1)

    def test_shared_private_coupon_stops_change_and_rolls_back(self):
        other = get_or_create_account_for_legacy_user(make_user())
        UserCoupon.objects.create(account=other, coupon=self.monthly.coupon, business_cycle=other.business_cycle)
        result = self.change(500, self.token(500))
        self.assertEqual(result.status_code, 400, result.data)
        self.high.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual((self.high.recharge_threshold, self.account.boss_type_id), (900, self.low.boss_type_id))
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_reactivation_and_creation_require_previews(self):
        self.high.active = False; self.high.recharge_threshold = 400
        self.high.save(update_fields=['active', 'recharge_threshold'])
        response = self.client.patch(self.url, {'active': True}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.high.refresh_from_db(); self.assertFalse(self.high.active)
        kind = make_boss_type()
        response = self.client.post('/api/admin/vip-tiers/', {'boss_type': kind.pk, 'level': 3,
                                    'recharge_threshold': 400}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(VipTier.objects.filter(boss_type=kind).exists())

    def creation_payload(self, **overrides):
        kind = make_boss_type()
        return {'boss_type': kind.pk, 'level': 3, 'recharge_threshold': 400,
                'upgrade_rewards': [{'reward': self.reward.pk, 'quantity': 2}], **overrides}

    def creation_token(self, payload):
        result = self.client.post('/api/admin/vip-tiers/creation-preview/', payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['data']['executable'])
        return result.data['data']['token']

    @skipUnless(os.environ.get('XA_VIP_BENEFIT_FORMAL') == '1',
                'VIP 门槛调整与升级权益配额发放属 VIP 权益正式契约深水区（18-盘点 VIP 等级停用和门槛调整待联调）；最终联调启用')
    def test_creation_recalculates_and_preserves_existing_monthly_coupon(self):
        payload = self.creation_payload()
        token = self.creation_token(payload)
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        tier = VipTier.objects.get(boss_type_id=payload['boss_type'])
        self.account.refresh_from_db(); self.monthly.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, tier.boss_type_id)
        self.assertEqual((self.monthly.coupon.discount_rate, self.monthly.vip_terms_locked_by_downgrade), (95, True))
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier=tier).count(), 1)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)
        audit = AdminAuditLog.objects.get(resource='vip-tier-creation', object_id=str(tier.pk))
        self.assertEqual(audit.request_body['configuration']['boss_type_id'], payload['boss_type'])
        self.assertEqual(audit.request_body['configuration']['upgrade_rewards'], payload['upgrade_rewards'])

    def test_seventh_tier_configured_rights_monthly_freeze_and_refund(self):
        from engagement.refunds import reverse_refund_benefits
        from coupons.monthly import issue_monthly_coupons
        from orders.models import OrderRefund
        from stores.db import transaction
        self.confirmation.amount = 1200
        self.confirmation.save(update_fields=['amount'])
        self.confirmation.order.amount = 1200
        self.confirmation.order.save(update_fields=['amount'])
        payload = self.creation_payload(level=7, recharge_threshold=1000,
            monthly_coupon_count=3, monthly_discount=70, season_discount=60,
            benefits={'issuance': {'companion_quantity': 3, 'companion_variant': '自选陪玩',
                                   'safety_box': True, 'monthly_max_discount': 1700}})
        token = self.creation_token(payload)
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        tier = VipTier.objects.get(boss_type_id=payload['boss_type'])
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, tier.boss_type_id)
        upgrades = UserCoupon.objects.filter(account=self.account,
            coupon__grant_key__startswith=f'vip-upgrade:{self.account.pk}:1:{tier.pk}:companion:')
        self.assertEqual(upgrades.count(), 3)
        self.assertEqual(set(upgrades.values_list('coupon__scope_variant', flat=True)), {'自选陪玩'})
        grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(account=self.account,
            coupon__grant_key__startswith='vip-month:').count(), 1)
        later = (timezone.now().replace(day=28) + timedelta(days=4)).replace(day=1)
        grant = issue_monthly_coupons(self.account, tier.pk, now=later, allow_new_period=True)
        self.assertEqual((grant.issued_count, grant.issued_discount), (3, 70))
        next_monthly = UserCoupon.objects.filter(account=self.account,
            coupon__grant_key__startswith=f'vip-month:{self.account.pk}:1:{later:%Y-%m}:')
        self.assertEqual(set(next_monthly.values_list('coupon__max_discount', flat=True)), {1700})
        order = self.confirmation.order
        with transaction.atomic():
            refund = OrderRefund.objects.create(order=order, amount=500, reason='测试退款')
            order.payment_status = 'PARTIALLY_REFUNDED'; order.save(update_fields=['payment_status'])
            reverse_refund_benefits(order, refund, None)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertEqual(upgrades.filter(status='UNUSED', vip_terms_locked_by_downgrade=True).count(), 3)
        self.assertEqual(next_monthly.filter(vip_terms_locked_by_downgrade=True,
                                             coupon__max_discount=1700).count(), 3)
        replacement = make_order(self.confirmation.order.customer, customer_account=self.account, amount=500,
                                 status='COMPLETED', payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=replacement, account=self.account,
            business_cycle=self.account.business_cycle, amount=500)
        from engagement.services import update_vip
        with patch('engagement.tasks.enqueue_broadcast'):
            update_vip(self.account)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, tier.boss_type_id)
        self.assertEqual(upgrades.count(), 3)

    def test_creation_preview_binds_proposed_configuration_and_current_data(self):
        payload = self.creation_payload()
        token = self.creation_token(payload)
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recharge_threshold': 300,
                                   'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(VipTier.objects.filter(boss_type_id=payload['boss_type']).exists())
        self.confirmation.refunded_amount = 1; self.confirmation.save(update_fields=['refunded_amount'])
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.account.refresh_from_db(); self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)

    def test_creation_audit_failure_rolls_back_new_tier_and_rewards(self):
        payload = self.creation_payload(); token = self.creation_token(payload)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': token}, format='json')
        self.assertFalse(VipTier.objects.filter(boss_type_id=payload['boss_type']).exists())
        self.account.refresh_from_db(); self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_creation_for_existing_non_vip_type_issues_new_upgrade(self):
        payload = self.creation_payload()
        self.account.boss_type_id = payload['boss_type']; self.account.save(update_fields=['boss_type'])
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': self.creation_token(payload)}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        tier = VipTier.objects.get(boss_type_id=payload['boss_type'])
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, tier.boss_type_id)
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier=tier).count(), 1)

    def test_creation_reward_change_requires_new_preview(self):
        payload = self.creation_payload(); token = self.creation_token(payload)
        self.reward.duration_days = 8; self.reward.save(update_fields=['duration_days'])
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(VipTier.objects.filter(boss_type_id=payload['boss_type']).exists())

    def test_creation_birthday_reward_change_requires_new_preview(self):
        reward = RewardItem.objects.create(code='birthday-preview', name='原生日奖励', kind='SERVICE',
            vip_quota_kind='BIRTHDAY_GIFT', in_shop=False, is_active=True)
        payload = self.creation_payload(level=7, recharge_threshold=1000,
            benefits={'issuance': {'birthday_reward_id': reward.pk, 'birthday_reward_quantity': 1}})
        token = self.creation_token(payload)
        reward.name = '调整后生日奖励'; reward.save(update_fields=['name'])
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(VipTier.objects.filter(boss_type_id=payload['boss_type']).exists())

    def test_inactive_creation_needs_no_recalculation_and_awards_nothing(self):
        payload = self.creation_payload(active=False)
        result = self.client.post('/api/admin/vip-tiers/', payload, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        tier = VipTier.objects.get(boss_type_id=payload['boss_type'])
        self.assertFalse(tier.active)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=tier).exists())

    def test_creation_preview_uses_public_consumption_threshold_alias(self):
        payload = self.creation_payload()
        payload['consumption_threshold'] = payload.pop('recharge_threshold')
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': self.creation_token(payload)}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(VipTier.objects.get(boss_type_id=payload['boss_type']).recharge_threshold, 400)

    @skipUnless(os.environ.get('XA_VIP_BENEFIT_FORMAL') == '1',
                'VIP 门槛调整与升级权益配额发放属 VIP 权益正式契约深水区（18-盘点 VIP 等级停用和门槛调整待联调）；最终联调启用')
    def test_new_intermediate_tier_awards_eligible_boss_without_downgrade(self):
        self.high.level = 3; self.high.recharge_threshold = 300
        self.high.save(update_fields=['level', 'recharge_threshold'])
        self.account.boss_type = self.high.boss_type; self.account.save(update_fields=['boss_type'])
        payload = self.creation_payload(level=2)
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': self.creation_token(payload)}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        tier = VipTier.objects.get(boss_type_id=payload['boss_type'])
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier=tier).count(), 1)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)

    def reactivate_token(self):
        preview = self.client.post(self.url+'recalculation-preview/', {'active': True}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertTrue(preview.data['data']['executable'])
        return preview.data['data']['token']

    @skipUnless(os.environ.get('XA_VIP_BENEFIT_FORMAL') == '1',
                'VIP 门槛调整与升级权益配额发放属 VIP 权益正式契约深水区（18-盘点 VIP 等级停用和门槛调整待联调）；最终联调启用')
    def test_reactivation_recalculates_and_awards_new_tier(self):
        self.high.active = False; self.high.recharge_threshold = 400
        self.high.save(update_fields=['active', 'recharge_threshold'])
        result = self.client.patch(self.url, {'active': True, 'recalculation_token': self.reactivate_token()}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.high.refresh_from_db(); self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.monthly.coupon.refresh_from_db()
        self.assertEqual((self.high.active, self.account.boss_type_id), (True, self.high.boss_type_id))
        self.assertEqual((self.monthly.coupon.discount_rate, self.monthly.vip_terms_locked_by_downgrade), (95, True))
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)
        self.assertTrue(AdminAuditLog.objects.filter(resource='vip-tier-reactivation', object_id=str(self.high.pk)).exists())
        repeated = self.client.patch(self.url, {'active': True}, format='json')
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)

    def test_reactivation_stale_or_wrong_action_token_rolls_back(self):
        threshold_token = self.token(500)
        self.high.active = False; self.high.recharge_threshold = 400
        self.high.save(update_fields=['active', 'recharge_threshold'])
        token = self.reactivate_token()
        result = self.client.patch(self.url, {'active': True, 'recalculation_token': threshold_token}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.confirmation.refunded_amount = 1; self.confirmation.save(update_fields=['refunded_amount'])
        result = self.client.patch(self.url, {'active': True, 'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.high.refresh_from_db(); self.account.refresh_from_db()
        self.assertFalse(self.high.active)
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_reactivation_audit_failure_rolls_back_rights(self):
        self.high.active = False; self.high.recharge_threshold = 400
        self.high.save(update_fields=['active', 'recharge_threshold'])
        token = self.reactivate_token()
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.client.patch(self.url, {'active': True, 'recalculation_token': token}, format='json')
        self.high.refresh_from_db(); self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.assertFalse(self.high.active)
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertFalse(self.monthly.vip_terms_locked_by_downgrade)
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_legacy_holder_of_disabled_tier_is_not_granted_the_same_upgrade_again(self):
        self.high.active = False; self.high.recharge_threshold = 400
        self.high.save(update_fields=['active', 'recharge_threshold'])
        self.account.boss_type = self.high.boss_type; self.account.save(update_fields=['boss_type'])
        token = self.reactivate_token()
        result = self.client.patch(self.url, {'active': True, 'recalculation_token': token}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)
        self.assertTrue(self.monthly.vip_terms_locked_by_downgrade)
        self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).exists())
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    @skipUnless(os.environ.get('XA_VIP_BENEFIT_FORMAL') == '1',
                'VIP 门槛调整与升级权益配额发放属 VIP 权益正式契约深水区（18-盘点 VIP 等级停用和门槛调整待联调）；最终联调启用')
    def test_reactivated_intermediate_tier_grants_rights_without_lowering_current_grade(self):
        top = VipTier.objects.create(level=3, boss_type=make_boss_type(), recharge_threshold=300)
        self.high.active = False; self.high.recharge_threshold = 400
        self.high.save(update_fields=['active', 'recharge_threshold'])
        self.account.boss_type = top.boss_type; self.account.save(update_fields=['boss_type'])
        result = self.client.patch(self.url, {'active': True, 'recalculation_token': self.reactivate_token()}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, top.boss_type_id)
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).count(), 1)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)
