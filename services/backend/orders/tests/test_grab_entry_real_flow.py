from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from console.grab_config import save_grab_window
from orders.models import Order, OrderGrabPoolSnapshot
from orders.tests import test_game_commission_payment as fixture
from orders.tests.factories import make_console_user
from wallet.services import get_wallet


@override_settings(GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   GAME_COMMISSION_PAYMENT_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabEntryRealFlowTest(APITestCase):
    setUp = fixture.GameCommissionPaymentTest.setUp
    link = fixture.GameCommissionPaymentTest.link
    auth = fixture.GameCommissionPaymentTest.auth
    create = fixture.GameCommissionPaymentTest.create

    def test_boss_and_staff_entry_snapshot_survives_real_later_pass_purchase(self):
        rejected = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(rejected.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())
        save_grab_window(30, 0, lambda *args: None)
        self.service.game_category = None
        self.service.save(update_fields=['game_category'])
        player = self.providers[0]
        profile = player.escort_profile
        profile.pass_tier, profile.pass_expires_at = '', None
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
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
        later = first_pool.opened_at + timedelta(seconds=301)
        with patch('django.utils.timezone.now', return_value=later):
            listing = self.client.get('/api/orders/orders/', {'status': 'pending'}).data['data']
            self.assertIn(first_order.pk, [row['id'] for row in listing])
            accepted = self.client.post(f'/api/orders/orders/{first_order.pk}/grab/', {}, format='json')
            self.assertEqual(accepted.data['code'], 0, accepted.data)
            accepted = self.client.post(f'/api/orders/orders/{arranged_order.pk}/grab/', {}, format='json')
            self.assertEqual(accepted.data['code'], 0, accepted.data)
