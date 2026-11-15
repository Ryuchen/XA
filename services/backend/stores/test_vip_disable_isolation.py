from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import VipTier
from engagement.services import config
from orders.tests.factories import make_boss_type, make_console_user, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@override_settings(VIP_TERMS_FREEZE_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class VipDisableIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_real_disable_changes_only_the_selected_store(self):
        fixtures = []
        stores = (resolve_store(), xinan_store())
        for store in stores:
            with store_context(store):
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                service = make_service(price=10000, benefit_kind='SAFEBOX_33', platform='PC')
                tier = VipTier.objects.create(level=6, boss_type=make_boss_type(), recharge_threshold=1,
                    monthly_coupon_count=0, monthly_discount=90, season_discount=0, safe_box_service_ids=[service.pk])
                account.boss_type = tier.boss_type; account.save(update_fields=['boss_type']); config()
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                                 platform_actor_id='test', operation_id='same-cutover')
                grant_periodic(account)
                voucher = UserCoupon.objects.get(account=account, coupon__scope='SAFE_BOX')
                tier.season_discount = 50; tier.save(update_fields=['season_discount'])
                staff = make_console_user(['engagement:view', 'engagement:edit']); operator = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
                fixtures.append((account.pk, voucher.pk, operator.pk, issue_account_tokens(operator)[0]))
        self.assertEqual(fixtures[0][:3], fixtures[1][:3])
        with store_context(stores[0]):
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + fixtures[0][3])
            result = client.put('/api/admin/club-config/', {'vip_enabled':False}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        for store, expected_enabled, expected_rate in zip(stores, (False, True), (50, 0)):
            with store_context(store):
                voucher = UserCoupon.objects.select_related('coupon').get(pk=fixtures[0][1])
                self.assertEqual((config().vip_enabled, voucher.coupon.discount_rate), (expected_enabled, expected_rate))
