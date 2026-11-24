import os
from datetime import timedelta
from time import monotonic, sleep
from unittest import skipUnless

from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from console.capacity_config import save_capacity
from console.grab_config import save_grab_window
from orders.grab_receipt_probe import _db_clock
from orders.models import Order, OrderGrabPoolSnapshot
from orders.tests import test_game_commission_payment as fixture
from orders.tests.factories import make_console_user
from wallet.models import SystemConfig
from wallet.services import get_wallet


@override_settings(GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True,
                   GAME_COMMISSION_PAYMENT_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabEntryRealFlowTest(APITransactionTestCase):
    setUp = fixture.GameCommissionPaymentTest.setUp
    link = fixture.GameCommissionPaymentTest.link
    auth = fixture.GameCommissionPaymentTest.auth
    create = fixture.GameCommissionPaymentTest.create

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    @skipUnless(connection.vendor == 'mysql', '陪玩声明须使用真实 MySQL 生命周期锁')
    def test_boss_and_staff_entry_snapshot_survives_real_later_pass_purchase(self):
        rejected = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(rejected.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())
        save_grab_window(30, 0, lambda *args: None)
        save_capacity(3, 0, lambda *args: None)
        delay = 4
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            SystemConfig.objects.update_or_create(key=f'provider_pass_delay_seconds_{tier}',
                defaults={'value': str(delay if tier == 'NONE' else 0)})
        self.service.game_category = None
        self.service.save(update_fields=['game_category'])
        player = self.providers[0]
        profile = player.escort_profile
        account = self.accounts[0]
        account.is_openid_bound, account.openid = True, 'grab-entry-pass-buyer'
        account.save(update_fields=['is_openid_bound', 'openid'])
        profile.pass_tier, profile.pass_expires_at = '', None
        profile.wechat_avatar_verified_at = timezone.now()
        profile.save(update_fields=['pass_tier', 'pass_expires_at', 'wechat_avatar_verified_at'])
        first = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(first.data['code'], 0, first.data)
        first_order = Order.objects.get(pk=first.data['data']['id'])
        first_pool = OrderGrabPoolSnapshot.objects.get(order=first_order)
        self.assertFalse(first_pool.entry_passes.filter(profile_id_snapshot=profile.pk).exists())
        self.assertEqual(first_order.pool_entered_at, first_pool.opened_at)

        player_wallet = get_wallet(account=self.accounts[0], user=player)
        player_wallet.balance = 100000
        player_wallet.save(update_fields=['balance'])
        self.auth(self.accounts[0])
        bought = self.client.post('/api/users/provider-pass/', {'tier': 'BLACK', 'days': 1,
            'client_request_id': 'pass-after-first-entry'}, format='json')
        self.assertEqual(bought.data['code'], 0, bought.data)

        staff_user = make_console_user(['order:view', 'order:dispatch'])
        staff = self.link(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        self.auth(staff)
        second = self.client.post('/api/admin/orders/dispatch/', {'customer_id': self.boss_account.pk,
            'service_id': self.service.pk, 'client_request_id': 'staff-entry-test'}, format='json')
        self.assertEqual(second.data['code'], 0, second.data)
        second_order = Order.objects.get(pk=second.data['data']['id'])
        second_pool = OrderGrabPoolSnapshot.objects.get(order=second_order)
        self.assertEqual(second_pool.entry_passes.get(profile_id_snapshot=profile.pk).tier, 'BLACK')

        self.auth(self.boss_account)
        arranged = self.create(dispatch_mode='BOSS_SELECT', dispatch_route='STAFF',
                               client_request_id='boss-staff-arranged-entry')
        self.assertEqual(arranged.data['code'], 0, arranged.data)
        arranged_order = Order.objects.get(pk=arranged.data['data']['id'])
        self.assertTrue(OrderGrabPoolSnapshot.objects.filter(order=arranged_order).exists())

        self.auth(self.accounts[0])
        listing = self.client.get('/api/orders/orders/', {'status': 'pending'}).data['data']
        self.assertNotIn(first_order.pk, [row['id'] for row in listing])
        self.assertIn(second_order.pk, [row['id'] for row in listing])
        arranged_item = next(row for row in listing if row['id'] == arranged_order.pk)
        self.assertIn('GRAB', arranged_item['allowed_actions'])
        denied = self.client.post(f'/api/orders/orders/{first_order.pk}/grab/', {}, format='json')
        self.assertNotEqual(denied.data['code'], 0, denied.data)
        accepted = self.client.post(f'/api/orders/orders/{second_order.pk}/grab/', {}, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)

        # The first order retains its no-card delay even though the pass is now active.
        self.assertEqual(first_pool.visibility_delays[''], delay)
        visible_at = first_pool.opened_at + timedelta(seconds=delay)
        deadline = monotonic() + delay + 3
        while _db_clock()[0] <= visible_at:
            self.assertLess(monotonic(), deadline)
            sleep(.025)
        listing = self.client.get('/api/orders/orders/', {'status': 'pending'}).data['data']
        self.assertIn(first_order.pk, [row['id'] for row in listing])
        accepted = self.client.post(f'/api/orders/orders/{first_order.pk}/grab/', {}, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        accepted = self.client.post(f'/api/orders/orders/{arranged_order.pk}/grab/', {}, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
