from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import require_disposable_mysql
from coupons.models import UserCoupon, VipMonthlyCouponGrant, VipMonthlyCutover
from coupons.monthly import issue_monthly_coupons
from engagement.models import ConsumptionConfirmation, MemberBenefitGrant, MemberProfile, RewardClaim, RewardItem, VipTier
from engagement.services import config, update_vip
from orders.tests.factories import make_boss_type, make_console_user, make_order, make_user


@skipUnless(connection.vendor == 'mysql', 'requires isolated MySQL')
@override_settings(VIP_MONTHLY_DECISIONS_ENABLED=True, VIP_THRESHOLD_RECALC_ENABLED=True,
                   VIP_TERMS_FREEZE_ENABLED=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CustomVipMonthlyMySQLTest(TransactionTestCase):
    def test_reorder_competes_with_live_monthly_grant_without_duplicate_rights(self):
        require_disposable_mysql(connection)
        from coupons.benefits import grant_periodic
        from wallet.services import get_wallet
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(days=1),
                                         platform_actor_id='test', operation_id='reorder-race')
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100,
                                     monthly_coupon_count=1, monthly_discount=90)
        high = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=900,
                                      monthly_coupon_count=3, monthly_discount=70)
        account.boss_type = low.boss_type; account.save(update_fields=['boss_type'])
        get_wallet(account=account)
        order = make_order(user, customer_account=account, amount=1000, status='COMPLETED', payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=account, business_cycle=1, amount=1000)
        grant_periodic(account)
        update_vip(account)
        before_grants = set(MemberBenefitGrant.objects.filter(account=account).values_list('pk', flat=True))
        before_coupons = set(UserCoupon.objects.filter(account=account).values_list('pk', flat=True))
        staff_user = make_console_user(['engagement:view', 'engagement:edit'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
        admin_token, boss_token = issue_account_tokens(staff)[0], issue_account_tokens(account)[0]
        url = '/api/admin/vip-tiers/'
        mapping = [{'id': low.pk, 'level': 2}, {'id': high.pk, 'level': 1}]
        admin = APIClient(HTTP_AUTHORIZATION='Bearer '+admin_token)
        preview = admin.post(url+'reorder-preview/', {'mapping': mapping}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        token = preview.data['data']['token']
        gate = Barrier(2)

        def worker(reorder):
            close_old_connections()
            try:
                client = APIClient(HTTP_AUTHORIZATION='Bearer '+(admin_token if reorder else boss_token))
                gate.wait(timeout=10)
                response = (client.post(url+'reorder/', {'mapping': mapping, 'recalculation_token': token}, format='json')
                            if reorder else client.get('/api/coupons/mine/'))
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(worker, (True, False)))
        self.assertEqual(results[1], 200)
        if results[0] != 200:
            self.assertEqual(results[0], 400)
            preview = admin.post(url+'reorder-preview/', {'mapping': mapping}, format='json')
            self.assertEqual(preview.status_code, 200, preview.data)
            result = admin.post(url+'reorder/', {'mapping': mapping, 'recalculation_token': preview.data['data']['token']}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        account.refresh_from_db(); low.refresh_from_db()
        self.assertEqual((low.level, account.boss_type_id), (2, low.boss_type_id))
        self.assertEqual(set(MemberBenefitGrant.objects.filter(account=account).values_list('pk', flat=True)), before_grants)
        self.assertEqual(set(UserCoupon.objects.filter(account=account).values_list('pk', flat=True)), before_coupons)

    def test_concurrent_birthday_coupon_api_issues_one_grant_and_configured_rewards(self):
        require_disposable_mysql(connection)
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(days=1),
                                         platform_actor_id='test', operation_id='birthday-race')
        account = get_or_create_account_for_legacy_user(make_user())
        MemberProfile.objects.create(account=account, birthday=timezone.localdate())
        reward = RewardItem.objects.create(code='birthday-race', name='生日礼物', kind='SERVICE',
            vip_quota_kind='BIRTHDAY_GIFT', in_shop=False, is_active=True)
        tier = VipTier.objects.create(level=7, boss_type=make_boss_type(), recharge_threshold=100,
            benefits={'issuance': {'birthday': True, 'birthday_discount': 83, 'birthday_valid_days': 3,
                                   'birthday_reward_id': reward.pk, 'birthday_reward_quantity': 2}})
        account.boss_type = tier.boss_type
        account.save(update_fields=['boss_type'])
        token = issue_account_tokens(account)[0]
        gate = Barrier(2)

        def fetch():
            close_old_connections()
            try:
                client = APIClient(HTTP_AUTHORIZATION='Bearer ' + token)
                gate.wait(timeout=10)
                return client.get('/api/coupons/mine/').status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(lambda _: fetch(), range(2))), [200, 200])
        row = UserCoupon.objects.select_related('coupon').get(account=account,
            coupon__grant_key__startswith='vip-birthday:')
        self.assertEqual(row.coupon.discount_rate, 83)
        self.assertEqual(timezone.localtime(row.coupon.valid_to).date(), timezone.localdate()+timedelta(days=3))
        self.assertEqual(RewardClaim.objects.filter(account=account, reward=reward).count(), 2)

    def test_concurrent_seventh_tier_monthly_decision_issues_once(self):
        require_disposable_mysql(connection)
        config()
        account = get_or_create_account_for_legacy_user(make_user())
        tier = VipTier.objects.create(level=7, boss_type=make_boss_type(), recharge_threshold=100,
                                      monthly_coupon_count=3, monthly_discount=73,
                                      benefits={'issuance': {'monthly_max_discount': 3450}})
        account.boss_type = tier.boss_type
        account.save(update_fields=['boss_type'])
        gate = Barrier(2)

        def issue():
            close_old_connections()
            try:
                owner = ClubAccount.objects.get(pk=account.pk)
                gate.wait(timeout=10)
                return issue_monthly_coupons(owner, tier.pk, allow_new_period=True).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: issue(), range(2)))
        self.assertEqual(results[0], results[1])
        grant = VipMonthlyCouponGrant.objects.get(pk=results[0])
        self.assertEqual((grant.tier_level_snapshot, grant.issued_count, grant.issued_discount), (7, 3, 73))
        coupons = UserCoupon.objects.filter(account=account, coupon__grant_key__startswith='vip-month:')
        self.assertEqual(coupons.count(), 3)
        self.assertEqual(set(coupons.values_list('coupon__max_discount', flat=True)), {3450})
