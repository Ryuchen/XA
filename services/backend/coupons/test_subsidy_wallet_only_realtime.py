"""A wallet-only operator sees subsidy totals change without order access."""
import base64
import json
import os
import subprocess
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
from wallet.models import get_platform_wallet


@skipUnless(os.environ.get('XA_WALLET_ONLY_SUBSIDY_REALTIME') == '1', 'Requires isolated ASGI and Redis')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class WalletOnlySubsidyRealtimeTest(LiveServerTestCase):
    setUp = CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(CouponAvailabilityWorkflowTest.client_for)
    ok = CouponAvailabilityWorkflowTest.ok

    def test_pending_total_refreshes_without_order_permission(self):
        media = TemporaryDirectory(prefix='xa-wallet-only-subsidy-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        service = make_service(name='钱包权限安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='钱包权限会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=100, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier
        self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        dispatcher = make_console_user(['order:view', 'order:dispatch'])
        finance = make_console_user(['wallet:view'])
        dashboard_finance = make_console_user(['wallet:view', 'dashboard:view'])
        player = make_provider(display_name='钱包权限陪玩')
        dispatch_account, finance_account, dashboard_account, player_account = [get_or_create_account_for_legacy_user(user) for user in (dispatcher, finance, dashboard_finance, player)]
        for user, account in ((dispatcher, dispatch_account), (finance, finance_account), (dashboard_finance, dashboard_account)):
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
        player.escort_profile.account = player_account
        player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        data = self.ok(self.boss.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'wallet-only-visibility'}, format='json'))
        self.ok(self.client_for(dispatch_account).post(f"/api/admin/orders/{data['id']}/assign/", {'provider_id': player_account.pk}, format='json'))
        self.ok(self.client_for(player_account).post(f"/api/orders/orders/{data['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        platform = get_platform_wallet()
        platform.balance = 0
        platform.save(update_fields=['balance'])
        evidence = base64.b64encode(screenshot().read()).decode()
        repo = Path(settings.BASE_DIR).parent.parent
        old_flag = os.environ.get('VIP_SAFE_BOX_SUBSIDY_ENABLED')
        os.environ['VIP_SAFE_BOX_SUBSIDY_ENABLED'] = 'true'
        try:
            with isolated_redis_asgi() as (base, ws_base):
                result = subprocess.run(['node', str(repo / 'scripts/admin-wallet-only-subsidy-realtime.cjs')], cwd=repo,
                    input=json.dumps({'base': base, 'wsBase': ws_base, 'root': os.environ['XA_ADMIN_VUE_ROOT'],
                        'financeToken': issue_account_tokens(finance_account)[0], 'dashboardToken': issue_account_tokens(dashboard_account)[0],
                        'playerToken': issue_account_tokens(player_account)[0],
                        'orderId': data['id'], 'orderNo': data['order_no'], 'evidence': evidence}),
                    text=True, encoding='utf-8', capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            if old_flag is None:
                os.environ.pop('VIP_SAFE_BOX_SUBSIDY_ENABLED', None)
            else:
                os.environ['VIP_SAFE_BOX_SUBSIDY_ENABLED'] = old_flag


del CouponAvailabilityWorkflowTest
