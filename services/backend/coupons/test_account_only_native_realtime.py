"""Native coupon controller and independent sessions against real services."""
import json
import os
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.models import VipTier
from orders.tests.factories import make_boss_type, make_service
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@skipUnless(os.environ.get('XA_ACCOUNT_ONLY_COUPON_REALTIME') == '1', 'Requires isolated Redis and ASGI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountOnlyNativeCouponRealtimeTest(LiveServerTestCase):
    def test_native_claim_and_second_device_update(self):
        service = make_service(name='原生账号安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        tier = make_boss_type(name='原生账号六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, safe_box_service_ids=[service.pk])
        boss = ClubAccount.objects.create_account(username='native-coupon-account', account_type='BOSS')
        boss.boss_type = tier
        boss.save(update_fields=['boss_type'])
        other = ClubAccount.objects.create_account(username='native-coupon-other', account_type='BOSS')
        first = Coupon.objects.create(name='原生领取券', discount_type='DIRECT', amount=100,
                                      is_active=True, is_public=True, total_qty=10)
        second = Coupon.objects.create(name='另一设备领取券', discount_type='DIRECT', amount=200,
                                       is_active=True, is_public=True, total_qty=10)
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_redis_asgi() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/native-account-only-coupons-realtime.cjs')],
                cwd=repo, input=json.dumps({'base': base, 'wsBase': ws_base, 'token': issue_account_tokens(boss)[0],
                    'otherToken': issue_account_tokens(other)[0], 'account': boss.pk, 'serviceId': service.pk,
                    'firstCoupon': first.pk, 'secondCoupon': second.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=50)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(LegacyAccountMap.objects.filter(account=boss).exists())
        self.assertEqual(UserCoupon.objects.filter(account=boss, coupon__in=[first, second], user__isnull=True).count(), 2)
        self.assertEqual(UserCoupon.objects.filter(account=boss, coupon__scope='SAFE_BOX', user__isnull=True).count(), 1)
