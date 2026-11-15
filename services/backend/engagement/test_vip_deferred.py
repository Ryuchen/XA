from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from rest_framework.test import APITestCase

from engagement.models import MemberBenefitGrant, RewardClaim, VipDeferredRecalculation, VipTier, VipUpgradeReview
from engagement.services import config
from engagement import test_vip_threshold_change as threshold_fixture


@override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                   VIP_MONTHLY_DECISIONS_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VipDeferredTest(APITestCase):
    setUp = threshold_fixture.VipThresholdChangeTest.setUp
    token = threshold_fixture.VipThresholdChangeTest.token
    change = threshold_fixture.VipThresholdChangeTest.change
    creation_payload = threshold_fixture.VipThresholdChangeTest.creation_payload
    creation_token = threshold_fixture.VipThresholdChangeTest.creation_token

    def disable(self):
        result = self.client.put('/api/admin/club-config/', {'vip_enabled': False}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def test_disabled_changes_keep_original_baseline_and_defer_effects(self):
        self.disable()
        baseline = VipDeferredRecalculation.objects.get(pk=1)
        original = baseline.baseline_rules
        self.assertTrue(baseline.is_open)
        self.assertFalse(baseline.pending)
        result = self.change(400, self.token(400))
        self.assertEqual(result.status_code, 200, result.data)
        payload = self.creation_payload()
        result = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': self.creation_token(payload)}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        baseline.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual(baseline.baseline_rules, original)
        self.assertTrue(baseline.pending)
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).exists())
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_preexisting_disabled_store_is_explicitly_unknown(self):
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        result = self.change(400, self.token(400))
        self.assertEqual(result.status_code, 200, result.data)
        baseline = VipDeferredRecalculation.objects.get(pk=1)
        self.assertTrue(baseline.pending)
        self.assertIsNone(baseline.baseline_rules)

    def test_audit_failure_rolls_back_deferred_configuration(self):
        self.disable()
        token = self.token(400)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.change(400, token)
        self.high.refresh_from_db(); baseline = VipDeferredRecalculation.objects.get(pk=1)
        self.assertEqual(self.high.recharge_threshold, 900)
        self.assertFalse(baseline.pending)

    def test_no_resume_without_reconciliation(self):
        self.disable()
        result = self.client.put('/api/admin/club-config/', {'vip_enabled': True}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(config().vip_enabled)

    def resume_preview(self):
        response = self.client.get('/api/admin/club-config/vip-resume-preview/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def resume(self, token):
        return self.client.put('/api/admin/club-config/', {'vip_enabled': True, 'vip_resume_token': token}, format='json')

    def test_known_baseline_resume_recalculates_and_awards_only_new_tiers(self):
        self.disable()
        self.assertEqual(self.change(400, self.token(400)).status_code, 200)
        payload = self.creation_payload()
        created = self.client.post('/api/admin/vip-tiers/', {**payload, 'recalculation_token': self.creation_token(payload)}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        tier = VipTier.objects.get(boss_type_id=payload['boss_type'])
        preview = self.resume_preview()
        self.assertTrue(preview['baseline_known'])
        self.assertEqual((preview['upgrade_count'], preview['manual_review_count']), (1, 0))
        response = self.resume(preview['token'])
        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db(); self.monthly.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, tier.boss_type_id)
        self.assertEqual((self.monthly.coupon.discount_rate, self.monthly.vip_terms_locked_by_downgrade), (95, True))
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier__in=[self.high, tier]).count(), 2)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)
        self.assertFalse(VipUpgradeReview.objects.exists())
        self.assertFalse(VipDeferredRecalculation.objects.get(pk=1).is_open)
        self.assertTrue(config().vip_enabled)
        self.assertEqual(self.client.put('/api/admin/club-config/', {'vip_enabled': True}, format='json').status_code, 200)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)

    def test_unknown_baseline_resumes_grade_and_queues_unverified_rewards(self):
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        self.assertEqual(self.change(400, self.token(400)).status_code, 200)
        preview = self.resume_preview()
        self.assertFalse(preview['baseline_known'])
        self.assertGreaterEqual(preview['manual_review_count'], 1)
        response = self.resume(preview['token'])
        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)
        self.assertTrue(self.monthly.vip_terms_locked_by_downgrade)
        self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).exists())
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())
        self.assertTrue(VipUpgradeReview.objects.filter(account=self.account, tier=self.high, status='PENDING').exists())
        self.assertTrue(config().vip_enabled)

    def test_resume_rejects_stale_preview_and_rolls_back_audit_failure(self):
        self.disable()
        self.assertEqual(self.change(400, self.token(400)).status_code, 200)
        preview = self.resume_preview()
        self.confirmation.refunded_amount = 1; self.confirmation.save(update_fields=['refunded_amount'])
        self.assertEqual(self.resume(preview['token']).status_code, 400)
        fresh = self.resume_preview()
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.resume(fresh['token'])
        self.account.refresh_from_db()
        self.assertFalse(config().vip_enabled)
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertTrue(VipDeferredRecalculation.objects.get(pk=1).is_open)
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_retirement_while_disabled_is_deferred(self):
        self.disable()
        preview = self.client.get(f'/api/admin/vip-tiers/{self.low.pk}/retirement-preview/')
        self.assertEqual(preview.status_code, 200, preview.data)
        result = self.client.patch(f'/api/admin/vip-tiers/{self.low.pk}/',
            {'active':False, 'retirement_token':preview.data['data']['token']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.account.refresh_from_db(); self.low.refresh_from_db()
        self.assertFalse(self.low.active)
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        preview = self.resume_preview()
        response = self.resume(preview['token'])
        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.boss_type_id)

    def unknown_resume(self):
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        self.assertEqual(self.change(400, self.token(400)).status_code, 200)
        response = self.resume(self.resume_preview()['token'])
        self.assertEqual(response.status_code, 200, response.data)
        return VipUpgradeReview.objects.get(account=self.account, tier=self.high, status='PENDING')

    def test_manual_already_issued_records_evidence_without_granting(self):
        from engagement.vip_recalculation_plan import read_store_snapshot
        review = self.unknown_resume()
        listing = self.client.get('/api/admin/vip-upgrade-reviews/', {'status':'PENDING'})
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertTrue(any(row['id'] == review.pk for row in listing.data['data']['list']))
        result = self.client.post(f'/api/admin/vip-upgrade-reviews/{review.pk}/resolve/',
                                  {'decision':'ALREADY_ISSUED', 'note':'核对历史已发'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        review.refresh_from_db()
        self.assertEqual((review.status, review.resolution), ('RESOLVED', 'ALREADY_ISSUED'))
        self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).exists())
        _, bosses = read_store_snapshot()
        self.assertIn(self.high.pk, next(boss for boss in bosses if boss.account_id == self.account.pk).issued_tier_ids)

    def test_normal_vip_update_cannot_bypass_pending_or_verified_history(self):
        from engagement.services import update_vip
        from coupons.models import UserCoupon
        self.reward.kind = 'TAG'; self.reward.save(update_fields=['kind'])
        review = self.unknown_resume()
        # Remove the preexisting low-tier safe box to simulate missing history.
        UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-safe:').delete()
        update_vip(self.account)
        self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).exists())
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())
        self.assertFalse(UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-safe:').exists())
        response = self.client.post(f'/api/admin/vip-upgrade-reviews/{review.pk}/resolve/',
                                    {'decision':'ALREADY_ISSUED', 'note':'核对历史已发'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        update_vip(self.account)
        self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).exists())
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())
        self.assertFalse(UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-safe:').exists())

    def test_manual_issue_is_idempotent_and_audited(self):
        from console.models import AdminAuditLog
        review = self.unknown_resume()
        path = f'/api/admin/vip-upgrade-reviews/{review.pk}/resolve/'
        body = {'decision':'ISSUE', 'note':'确认历史未发'}
        result = self.client.post(path, body, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.client.post(path, body, format='json').status_code, 200)
        self.assertEqual(RewardClaim.objects.filter(account=self.account, reward=self.reward).count(), 2)
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(resource='vip-upgrade-review', object_id=str(review.pk)).count(), 1)
        self.assertEqual(self.client.post(path, {'decision':'ALREADY_ISSUED', 'note':'再次核对'}, format='json').status_code, 400)

    def test_manual_issue_failure_rolls_back_review_and_grant(self):
        review = self.unknown_resume()
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.client.post(f'/api/admin/vip-upgrade-reviews/{review.pk}/resolve/',
                                 {'decision':'ISSUE', 'note':'确认历史未发'}, format='json')
        review.refresh_from_db()
        self.assertEqual(review.status, 'PENDING')
        self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).exists())
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_manual_review_requires_edit_permission_and_verified_note(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient
        review = self.unknown_resume()
        staff = make_console_user(['engagement:view'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        viewer = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        path = f'/api/admin/vip-upgrade-reviews/{review.pk}/resolve/'
        self.assertEqual(viewer.get('/api/admin/vip-upgrade-reviews/').status_code, 200)
        self.assertEqual(viewer.post(path, {'decision':'ISSUE', 'note':'已核对'}, format='json').status_code, 403)
        self.assertEqual(self.client.post(path, {'decision':'ISSUE', 'note':'x'}, format='json').status_code, 400)
        review.refresh_from_db(); self.assertEqual(review.status, 'PENDING')

    def test_manual_issue_does_not_cross_business_cycles_or_vip_off(self):
        review = self.unknown_resume()
        path = f'/api/admin/vip-upgrade-reviews/{review.pk}/resolve/'
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        self.assertEqual(self.client.post(path, {'decision':'ISSUE', 'note':'确认未发'}, format='json').status_code, 400)
        operation.vip_enabled = True; operation.save(update_fields=['vip_enabled'])
        self.account.business_cycle += 1; self.account.save(update_fields=['business_cycle'])
        self.assertEqual(self.client.post(path, {'decision':'ISSUE', 'note':'确认未发'}, format='json').status_code, 400)
        self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    def test_resume_preview_requires_edit_permission(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient
        self.disable()
        staff = make_console_user(['engagement:view'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        viewer = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        self.assertEqual(viewer.get('/api/admin/club-config/vip-resume-preview/').status_code, 403)

    def test_gift_paid_while_vip_disabled_does_not_grow_vip_after_resume(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from engagement.models import ConsumptionConfirmation
        from orders.models import Order
        from orders.tests.factories import make_provider, make_service, make_service_category
        from rest_framework.test import APIClient
        from wallet.services import get_wallet
        self.disable()
        self.assertEqual(self.change(700, self.token(700)).status_code, 200)
        provider = make_provider(is_verified=True)
        recipient = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
        gift = make_service(price=2100, service_category=make_service_category(is_gift=True))
        wallet = get_wallet(account=self.account); wallet.balance = 3000; wallet.save(update_fields=['balance'])
        buyer = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.account)[0])
        result = buyer.post('/api/orders/gifts/send/', {'gift_service_id':gift.pk, 'provider_id':recipient.pk,
            'quantity':1, 'expected_amount':2100, 'client_request_id':'vip-disabled-gift'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        response = self.resume(self.resume_preview()['token'])
        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
