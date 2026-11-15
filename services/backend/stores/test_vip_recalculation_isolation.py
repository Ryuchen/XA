from dataclasses import replace

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from engagement.models import ConsumptionConfirmation, VipTier
from engagement.vip_recalculation_plan import plan_changes, read_store_snapshot
from orders.tests.factories import make_boss_type, make_order, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


class VipRecalculationIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    @override_settings(VIP_MONTHLY_DECISIONS_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                       LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
    def test_coupon_configuration_is_store_scoped(self):
        from club_accounts.tokens import issue_account_tokens
        from coupons.models import VipMonthlyCutover
        from engagement.services import config
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient
        stores = (resolve_store(), xinan_store())
        identities = []
        for store in stores:
            with store_context(store):
                tier = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=100,
                    monthly_coupon_count=2, monthly_discount=80, season_discount=80)
                config()
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                                 platform_actor_id='test', operation_id='terms-isolation')
                user = make_console_user(['engagement:view', 'engagement:edit'])
                admin = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account = admin; user.admin_membership.save(update_fields=['account'])
                identities.append((tier.pk, issue_account_tokens(admin)[0]))
        self.assertEqual(identities[0][0], identities[1][0])
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[0][1])
        response = client.patch(f'/api/admin/vip-tiers/{identities[0][0]}/',
            {'monthly_coupon_count':3, 'monthly_discount':88, 'season_discount':65}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for index, expected in enumerate(((3, 88, 65), (2, 80, 80))):
            with store_context(stores[index]):
                tier = VipTier.objects.get(pk=identities[index][0])
                self.assertEqual((tier.monthly_coupon_count, tier.monthly_discount, tier.season_discount), expected)

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                       VIP_MONTHLY_DECISIONS_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                       KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
    def test_resume_unknown_history_and_reviews_are_store_scoped(self):
        from club_accounts.models import ClubAccount
        from club_accounts.tokens import issue_account_tokens
        from coupons.models import VipMonthlyCutover
        from engagement.models import VipUpgradeReview
        from engagement.services import config
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient

        stores = (resolve_store(), xinan_store())
        identities = []
        for store in stores:
            with store_context(store):
                user = make_user(); boss = get_or_create_account_for_legacy_user(user)
                low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100)
                high = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=400)
                boss.boss_type = low.boss_type; boss.save(update_fields=['boss_type'])
                order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
                ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
                operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                                 platform_actor_id='test', operation_id='resume-isolation')
                user = make_console_user(['engagement:view', 'engagement:edit'])
                admin = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account = admin; user.admin_membership.save(update_fields=['account'])
                identities.append((boss.pk, low.boss_type_id, high.boss_type_id, issue_account_tokens(admin)[0]))
        self.assertEqual(identities[0][:3], identities[1][:3])
        first, second = [APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+item[3]) for item in identities]
        preview = first.get('/api/admin/club-config/vip-resume-preview/')
        self.assertEqual(preview.status_code, 200, preview.data)
        body = {'vip_enabled':True, 'vip_resume_token':preview.data['data']['token']}
        rejected = second.put('/api/admin/club-config/', body, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        result = first.put('/api/admin/club-config/', body, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        with store_context(stores[0]):
            self.assertTrue(config().vip_enabled)
            self.assertEqual(ClubAccount.objects.get(pk=identities[0][0]).boss_type_id, identities[0][2])
            self.assertTrue(VipUpgradeReview.objects.filter(account_id=identities[0][0]).exists())
        with store_context(stores[1]):
            self.assertFalse(config().vip_enabled)
            self.assertEqual(ClubAccount.objects.get(pk=identities[1][0]).boss_type_id, identities[1][1])
            self.assertFalse(VipUpgradeReview.objects.exists())

    def test_same_account_and_tier_ids_use_each_stores_own_confirmed_consumption(self):
        stores = (resolve_store(), xinan_store())
        snapshots = []
        for store, amount in zip(stores, (200, 800)):
            with store_context(store):
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100)
                high = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=900)
                account.boss_type = low.boss_type; account.save(update_fields=['boss_type'])
                order = make_order(user, customer_account=account, amount=amount, payment_status='PAID')
                ConsumptionConfirmation.objects.create(order=order, account=account,
                    business_cycle=account.business_cycle, amount=amount)
                before, bosses = read_store_snapshot()
                after = tuple(replace(tier, threshold=500) if tier.tier_id == high.pk else tier for tier in before)
                snapshots.append((account.pk, low.pk, high.pk, bosses[0].net_consumption,
                                  plan_changes(before, after, bosses)))
        self.assertEqual(snapshots[0][:3], snapshots[1][:3])
        self.assertEqual((snapshots[0][3], len(snapshots[0][4])), (200, 0))
        self.assertEqual((snapshots[1][3], len(snapshots[1][4])), (800, 1))

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                       VIP_MONTHLY_DECISIONS_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                       KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
    def test_threshold_update_and_preview_token_are_store_scoped(self):
        from club_accounts.tokens import issue_account_tokens
        from coupons.models import VipMonthlyCutover
        from engagement.services import config
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient

        stores = (resolve_store(), xinan_store())
        identities = []
        for store in stores:
            with store_context(store):
                user = make_user(); boss = get_or_create_account_for_legacy_user(user)
                low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100)
                high = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=900)
                boss.boss_type = low.boss_type; boss.save(update_fields=['boss_type'])
                order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
                ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
                config()
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                                 platform_actor_id='test', operation_id='threshold-isolation')
                admin_user = make_console_user(['engagement:view', 'engagement:edit'])
                admin = get_or_create_account_for_legacy_user(admin_user)
                admin_user.admin_membership.account = admin; admin_user.admin_membership.save(update_fields=['account'])
                identities.append((boss.pk, low.pk, high.pk, issue_account_tokens(admin)[0]))
        self.assertEqual(identities[0][:3], identities[1][:3])
        first_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[0][3])
        second_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[1][3])
        path = f'/api/admin/vip-tiers/{identities[0][2]}/'
        first_preview = first_client.post(path+'recalculation-preview/', {'consumption_threshold':500}, format='json')
        self.assertEqual(first_preview.status_code, 200, first_preview.data)
        token = first_preview.data['data']['token']
        rejected = second_client.patch(path, {'consumption_threshold':500, 'recalculation_token':token}, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        result = first_client.patch(path, {'consumption_threshold':500, 'recalculation_token':token}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        with store_context(stores[0]):
            self.assertEqual(VipTier.objects.get(pk=identities[0][2]).recharge_threshold, 500)
        with store_context(stores[1]):
            self.assertEqual(VipTier.objects.get(pk=identities[1][2]).recharge_threshold, 900)

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                       VIP_MONTHLY_DECISIONS_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                       KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
    def test_reactivation_recalculates_only_the_authenticated_store(self):
        from club_accounts.tokens import issue_account_tokens
        from coupons.models import VipMonthlyCutover
        from engagement.services import config
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient

        stores = (resolve_store(), xinan_store())
        identities = []
        for store in stores:
            with store_context(store):
                user = make_user(); boss = get_or_create_account_for_legacy_user(user)
                low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100)
                high = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=400, active=False)
                boss.boss_type = low.boss_type; boss.save(update_fields=['boss_type'])
                order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
                ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
                config()
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                                 platform_actor_id='test', operation_id='reactivation-isolation')
                admin_user = make_console_user(['engagement:view', 'engagement:edit'])
                admin = get_or_create_account_for_legacy_user(admin_user)
                admin_user.admin_membership.account = admin; admin_user.admin_membership.save(update_fields=['account'])
                identities.append((boss.pk, low.pk, high.pk, high.boss_type_id, issue_account_tokens(admin)[0]))
        self.assertEqual(identities[0][:4], identities[1][:4])
        client_a = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[0][4])
        client_b = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[1][4])
        path = f'/api/admin/vip-tiers/{identities[0][2]}/'
        preview = client_a.post(path+'recalculation-preview/', {'active':True}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        token = preview.data['data']['token']
        rejected = client_b.patch(path, {'active':True, 'recalculation_token':token}, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        result = client_a.patch(path, {'active':True, 'recalculation_token':token}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        with store_context(stores[0]):
            from club_accounts.models import ClubAccount
            self.assertTrue(VipTier.objects.get(pk=identities[0][2]).active)
            self.assertEqual(ClubAccount.objects.get(pk=identities[0][0]).boss_type_id, identities[0][3])
        with store_context(stores[1]):
            self.assertFalse(VipTier.objects.get(pk=identities[1][2]).active)
            self.assertNotEqual(ClubAccount.objects.get(pk=identities[1][0]).boss_type_id, identities[1][3])

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                       VIP_MONTHLY_DECISIONS_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                       KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
    def test_new_tier_preview_and_creation_do_not_cross_stores(self):
        from club_accounts.models import ClubAccount
        from club_accounts.tokens import issue_account_tokens
        from coupons.models import VipMonthlyCutover
        from engagement.services import config
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient

        stores = (resolve_store(), xinan_store())
        identities = []
        for store in stores:
            with store_context(store):
                user = make_user(); boss = get_or_create_account_for_legacy_user(user)
                low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100)
                new_type = make_boss_type()
                boss.boss_type = low.boss_type; boss.save(update_fields=['boss_type'])
                order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
                ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
                config()
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                                 platform_actor_id='test', operation_id='creation-isolation')
                admin_user = make_console_user(['engagement:view', 'engagement:edit'])
                admin = get_or_create_account_for_legacy_user(admin_user)
                admin_user.admin_membership.account = admin; admin_user.admin_membership.save(update_fields=['account'])
                identities.append((boss.pk, low.boss_type_id, new_type.pk, issue_account_tokens(admin)[0]))
        self.assertEqual(identities[0][:3], identities[1][:3])
        first = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[0][3])
        second = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[1][3])
        payload = {'boss_type': identities[0][2], 'level':2, 'consumption_threshold':400}
        preview = first.post('/api/admin/vip-tiers/creation-preview/', payload, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        body = {**payload, 'recalculation_token':preview.data['data']['token']}
        rejected = second.post('/api/admin/vip-tiers/', body, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        result = first.post('/api/admin/vip-tiers/', body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        with store_context(stores[0]):
            self.assertTrue(VipTier.objects.filter(boss_type_id=identities[0][2]).exists())
            self.assertEqual(ClubAccount.objects.get(pk=identities[0][0]).boss_type_id, identities[0][2])
        with store_context(stores[1]):
            self.assertFalse(VipTier.objects.filter(boss_type_id=identities[1][2]).exists())
            self.assertEqual(ClubAccount.objects.get(pk=identities[1][0]).boss_type_id, identities[1][1])
