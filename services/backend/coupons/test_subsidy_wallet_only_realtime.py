"""A wallet-only operator sees subsidy totals change without order access."""
import os
from tempfile import TemporaryDirectory
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon
from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
from engagement.models import VipTier
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
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
        evidence = screenshot()
        finance_client, dashboard_client = self.client_for(finance_account), self.client_for(dashboard_account)
        player_client = self.client_for(player_account)
        finance_token = issue_account_tokens(finance_account)[0]
        dashboard_token = issue_account_tokens(dashboard_account)[0]

        async def verify():
            finance_socket = WebsocketCommunicator(application, f'/ws/orders/?token={finance_token}')
            dashboard_socket = WebsocketCommunicator(application, f'/ws/orders/?token={dashboard_token}')
            self.assertTrue((await finance_socket.connect())[0])
            self.assertTrue((await dashboard_socket.connect())[0])

            async def refresh_event(socket, kind):
                for _ in range(10):
                    event = await socket.receive_json_from(timeout=5)
                    self.assertNotEqual(event['type'], 'order_status_update')
                    if event['type'] == kind:
                        self.assertEqual(event['data'], {})
                        return
                self.fail(f'no {kind} received')

            try:
                funds = await database_sync_to_async(lambda: self.ok(finance_client.get('/api/admin/wallets/platform-funds/')))()
                self.assertEqual((funds['pending']['order_count'], funds['pending']['amount']), (0, 0))
                stats = await database_sync_to_async(lambda: self.ok(dashboard_client.get('/api/admin/dashboard/stats')))()
                self.assertEqual((stats['pending_subsidy_total'], stats['pending_subsidy_amount']), (0, 0))
                await database_sync_to_async(lambda: self.ok(player_client.post(
                    f"/api/orders/orders/{data['id']}/complete/", {'completion_image': evidence}, format='multipart')))()
                await refresh_event(finance_socket, 'platform_funds_update')
                await refresh_event(dashboard_socket, 'dashboard_order_update')
                self.assertTrue(await finance_socket.receive_nothing(timeout=0.2))
                self.assertTrue(await dashboard_socket.receive_nothing(timeout=0.2))
                funds = await database_sync_to_async(lambda: self.ok(finance_client.get('/api/admin/wallets/platform-funds/')))()
                self.assertEqual((funds['pending']['order_count'], funds['pending']['amount'], funds['pending']['orders']),
                                 (1, 8000, []))
                stats = await database_sync_to_async(lambda: self.ok(dashboard_client.get('/api/admin/dashboard/stats')))()
                self.assertEqual((stats['pending_subsidy_total'], stats['pending_subsidy_amount']), (1, 8000))
            finally:
                await finance_socket.disconnect()
                await dashboard_socket.disconnect()

        async_to_sync(verify)()


del CouponAvailabilityWorkflowTest
