"""Verify the open native pages after an actual platform-funded settlement."""
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon
from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
from engagement.models import VipTier
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
from users.test_native_checkin_order_realtime import isolated_redis_asgi
from wallet.models import Wallet, get_platform_wallet


@skipUnless(os.environ.get('XA_SUBSIDY_REALTIME_TEST') == '1', 'Requires isolated Redis and ASGI')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SubsidyRealtimeNetworkTest(LiveServerTestCase):
    setUp = CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(CouponAvailabilityWorkflowTest.client_for)
    ok = CouponAvailabilityWorkflowTest.ok

    def test_open_boss_and_provider_pages_refresh_after_platform_topup(self):
        media = TemporaryDirectory(prefix='xa-subsidy-realtime-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        service = make_service(name='实时补贴安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='实时六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier
        self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'wallet:view', 'wallet:adjust', 'dashboard:view', 'transaction:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        player = make_provider(display_name='实时补贴陪玩')
        player_account = get_or_create_account_for_legacy_user(player)
        player.escort_profile.account = player_account
        player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        order = self.ok(self.boss.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'vip-realtime-termination'}, format='json'))
        admin = self.client_for(staff_account)
        self.ok(admin.post(f"/api/admin/orders/{order['id']}/assign/", {'provider_id': player_account.pk}, format='json'))
        self.ok(self.client_for(player_account).post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        platform = get_platform_wallet()
        platform.balance = 7999
        platform.save(update_fields=['balance'])
        base = f"/api/admin/orders/{order['id']}/"
        share = self.ok(admin.get(base + 'subsidy-negotiation-options/'))['shares'][0]
        allocations = [{'share_id': share['share_id'], 'amount': 8000}]
        plan = self.ok(admin.post(base + 'subsidy-negotiation-preview/', {'allocations': allocations}, format='json'))
        self.ok(admin.post(base + 'subsidy-negotiation-confirm/', {'plan_token': plan['plan_token'], 'allocations': allocations,
            'client_request_id': 'vip-realtime-confirm', 'reason': '双方协商终止', 'reviewed': True}, format='json'))
        repo = Path(settings.BASE_DIR).parent.parent
        with override_settings():
            old = os.environ.get('VIP_SAFE_BOX_SUBSIDY_ENABLED')
            os.environ['VIP_SAFE_BOX_SUBSIDY_ENABLED'] = 'true'
            try:
                with isolated_redis_asgi(include_env=True) as (api_url, ws_url, env):
                    env.update(CELERY_BROKER_URL='memory://', CELERY_RESULT_BACKEND='cache+memory://')
                    result = subprocess.run(['node', str(repo / 'scripts/native-subsidy-realtime.cjs')], cwd=repo, env=env,
                        input=json.dumps({'base': api_url, 'wsBase': ws_url, 'bossId': self.account.pk,
                            'bossToken': issue_account_tokens(self.account)[0], 'staffToken': issue_account_tokens(staff_account)[0],
                            'providerToken': issue_account_tokens(player_account)[0], 'providerId': player_account.pk,
                            'providerRoot': os.environ['XA_PROVIDER_VUE_ROOT'], 'adminRoot': os.environ['XA_ADMIN_VUE_ROOT'], 'orderNo': order['order_no'],
                            'orderId': order['id'], 'voucherId': voucher.pk, 'python': sys.executable,
                            'backend': str(settings.BASE_DIR)}), text=True, encoding='utf-8', capture_output=True, timeout=80)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                if old is None:
                    os.environ.pop('VIP_SAFE_BOX_SUBSIDY_ENABLED', None)
                else:
                    os.environ['VIP_SAFE_BOX_SUBSIDY_ENABLED'] = old
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'USED')
        self.assertEqual(UserCoupon.objects.filter(account=self.account, coupon__scope='SAFE_BOX', status='UNUSED').count(), 1)
        self.assertEqual(Wallet.objects.get(account=player_account).balance, 8000)


del CouponAvailabilityWorkflowTest
