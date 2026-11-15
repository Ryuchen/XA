from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import ConsumptionConfirmation, MemberProfile, PointFundingLot, RewardClaim, RewardItem, VipTier
from engagement.services import config, update_vip
from orders.tests.factories import make_boss_type, make_console_user, make_order, make_service, make_user
from rest_framework.test import APIClient
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                   VIP_MONTHLY_DECISIONS_ENABLED=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CustomVipStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_batch_reorder_token_and_changes_are_store_scoped(self):
        stores = (resolve_store(), xinan_store())
        clients, ids = [], []
        for store in stores:
            with store_context(store):
                config()
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(days=1),
                    platform_actor_id='test', operation_id='reorder-isolation')
                tiers = [VipTier.objects.create(level=level, boss_type=make_boss_type(), recharge_threshold=level*100)
                         for level in (1, 2)]
                ids.append([tier.pk for tier in tiers])
                user = make_console_user(['engagement:view', 'engagement:edit'])
                staff = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
                clients.append(APIClient(HTTP_X_STORE_SLUG=store.slug,
                    HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0]))
        self.assertEqual(ids[0], ids[1])
        mapping = [{'id': ids[0][0], 'level': 2}, {'id': ids[0][1], 'level': 1}]
        with store_context(stores[0]):
            preview = clients[0].post('/api/admin/vip-tiers/reorder-preview/', {'mapping': mapping}, format='json')
            self.assertEqual(preview.status_code, 200, preview.data)
        payload = {'mapping': mapping, 'recalculation_token': preview.data['data']['token']}
        with store_context(stores[1]):
            denied = clients[1].post('/api/admin/vip-tiers/reorder/', payload, format='json')
            self.assertEqual(denied.status_code, 400, denied.data)
        with store_context(stores[0]):
            applied = clients[0].post('/api/admin/vip-tiers/reorder/', payload, format='json')
            self.assertEqual(applied.status_code, 200, applied.data)
            self.assertEqual(list(VipTier.objects.order_by('pk').values_list('level', flat=True)), [2, 1])
        with store_context(stores[1]):
            self.assertEqual(list(VipTier.objects.order_by('pk').values_list('level', flat=True)), [1, 2])

    def test_seventh_level_uses_each_stores_own_rules(self):
        stores = (resolve_store(), xinan_store())
        ids = []
        for store, quantity in zip(stores, (1, 3)):
            with store_context(store):
                config()
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(days=1),
                                                 platform_actor_id='test', operation_id='isolated')
                user = make_user()
                account = get_or_create_account_for_legacy_user(user)
                MemberProfile.objects.create(account=account, birthday=timezone.localdate())
                tier = VipTier.objects.create(level=7, boss_type=make_boss_type(), recharge_threshold=100,
                    monthly_coupon_count=quantity, monthly_discount=75,
                    benefits={'issuance': {'companion_quantity': quantity, 'companion_variant': store.slug,
                        'birthday': True, 'birthday_discount': 80 + quantity,
                        'birthday_max_discount': quantity * 1000, 'monthly_max_discount': quantity * 1500}})
                order = make_order(user, customer_account=account, amount=200, status='COMPLETED', payment_status='PAID')
                ConsumptionConfirmation.objects.create(order=order, account=account, business_cycle=1, amount=200)
                with patch('engagement.tasks.enqueue_broadcast'):
                    update_vip(account)
                account.refresh_from_db()
                self.assertEqual(account.boss_type_id, tier.boss_type_id)
                self.assertEqual(UserCoupon.objects.filter(account=account,
                    coupon__grant_key__startswith='vip-month:').count(), quantity)
                self.assertEqual(set(UserCoupon.objects.filter(account=account, coupon__grant_key__startswith='vip-month:')
                                     .values_list('coupon__max_discount', flat=True)), {quantity * 1500})
                birthday = UserCoupon.objects.get(account=account, coupon__grant_key__startswith='vip-birthday:')
                self.assertEqual((birthday.coupon.discount_rate, birthday.coupon.max_discount),
                                 (80 + quantity, quantity * 1000))
                coupons = UserCoupon.objects.filter(account=account, coupon__grant_key__startswith='vip-upgrade:')
                self.assertEqual(coupons.count(), quantity)
                self.assertEqual(set(coupons.values_list('coupon__scope_variant', flat=True)), {store.slug})
                ids.append((account.pk, tier.pk))
        self.assertEqual(ids[0], ids[1])

    @override_settings(BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
        SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
        SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
        SERVICE_ORDINARY_FUNDING_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
        SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
    def test_real_settings_birthday_coupon_reward_and_payment_in_each_store(self):
        from wallet.services import get_wallet
        from orders.models import Order
        stores = (resolve_store(), xinan_store())
        for store, multiplier in zip(stores, (2, 4)):
            with store_context(store):
                config()
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(days=1),
                    platform_actor_id='test', operation_id='birthday-isolation')
                user = make_user()
                account = get_or_create_account_for_legacy_user(user)
                MemberProfile.objects.create(account=account, birthday=timezone.localdate())
                tier = VipTier.objects.create(level=7, boss_type=make_boss_type(), recharge_threshold=100)
                account.boss_type = tier.boss_type; account.save(update_fields=['boss_type'])
                reward = RewardItem.objects.create(code=f'birthday-{store.slug}', name=store.slug,
                    kind='SERVICE', vip_quota_kind='BIRTHDAY_GIFT', in_shop=False, is_active=True)
                staff_user = make_console_user(['engagement:view', 'engagement:edit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_X_STORE_SLUG=store.slug, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
                rule = {'birthday': True, 'birthday_discount': 80 + multiplier, 'birthday_valid_days': multiplier,
                        'birthday_multiplier': multiplier, 'birthday_reward_id': reward.pk,
                        'birthday_reward_quantity': multiplier}
                response = admin.patch(f'/api/admin/vip-tiers/{tier.pk}/', {'benefits': {'issuance': rule}}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                boss = APIClient(HTTP_X_STORE_SLUG=store.slug, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
                self.assertEqual(boss.get('/api/coupons/mine/').status_code, 200)
                birthday = UserCoupon.objects.select_related('coupon').get(account=account,
                    coupon__grant_key__startswith='vip-birthday:')
                self.assertEqual(birthday.coupon.discount_rate, 80 + multiplier)
                local_expiry = timezone.localtime(birthday.coupon.valid_to)
                self.assertEqual(local_expiry.date(), timezone.localdate() + timedelta(days=multiplier))
                self.assertEqual(RewardClaim.objects.filter(account=account, reward=reward).count(), multiplier)
                self.assertEqual(boss.get('/api/coupons/mine/').status_code, 200)
                self.assertEqual(RewardClaim.objects.filter(account=account, reward=reward).count(), multiplier)
                wallet = get_wallet(account=account, user=user)
                wallet.balance = 20000; wallet.save(update_fields=['balance'])
                PointFundingLot.objects.create(account=account, source_key='birthday-principal', kind='PREPAID', remaining=20000)
                service = make_service(price=10000)
                paid = boss.post('/api/orders/create/', {'service_id': service.pk, 'client_request_id': 'birthday-paid'}, format='json')
                self.assertEqual(paid.data['code'], 0, paid.data)
                order = Order.objects.get(pk=paid.data['data']['id'])
                self.assertEqual(order.funding_terms['birthday_multiplier'], multiplier)
                self.assertEqual(admin.patch(f'/api/admin/vip-tiers/{tier.pk}/',
                    {'benefits': {'issuance': {**rule, 'birthday_multiplier': 1, 'birthday_valid_days': 1,
                                               'birthday_reward_quantity': multiplier + 1}}},
                    format='json').status_code, 200)
                self.assertEqual(boss.get('/api/coupons/mine/').status_code, 200)
                order.refresh_from_db(); birthday.coupon.refresh_from_db()
                self.assertEqual(order.funding_terms['birthday_multiplier'], multiplier)
                self.assertEqual(timezone.localtime(birthday.coupon.valid_to).date(),
                                 timezone.localdate() + timedelta(days=multiplier))
                self.assertEqual(RewardClaim.objects.filter(account=account, reward=reward).count(), multiplier)
