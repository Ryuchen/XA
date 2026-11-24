"""A store's configured limit is shared by actual order entry points."""
import os
from datetime import timedelta
from unittest import skipUnless

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.capacity import CONFIG_KEY, current_capacity_rules
from orders.models import Order, Reservation
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from wallet.models import SystemConfig
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class CapacityConfigurationTest(APITestCase):
    def setUp(self):
        self.boss, self.provider = make_user(), make_provider(deposit_mode='EARNINGS')
        self.staff = make_console_user(['order:view', 'order:dispatch', 'dictionary:view', 'dictionary:edit'])
        self.viewer = make_console_user(['order:view'])
        self.accounts = {user.pk: get_or_create_account_for_legacy_user(user)
                         for user in (self.boss, self.provider, self.staff, self.viewer)}
        for user in (self.staff, self.viewer):
            user.admin_membership.account = self.accounts[user.pk]
            user.admin_membership.save(update_fields=['account'])
        self.provider.escort_profile.account = self.accounts[self.provider.pk]
        self.provider.escort_profile.save(update_fields=['account'])
        self.service = make_service(price=1000)
        self.wallet = get_wallet(user=self.boss, account=self.accounts[self.boss.pk])
        self.wallet.balance = 20000
        self.wallet.save(update_fields=['balance'])
        from engagement.models import ClubOperationConfig, PointFundingLot
        from console.grab_config import save_grab_window
        ClubOperationConfig.objects.get_or_create(pk=1)
        save_grab_window(30, 0, lambda *args: None)
        PointFundingLot.objects.create(account=self.accounts[self.boss.pk], source_key='capacity-prepaid',
            kind=PointFundingLot.Kind.PREPAID, remaining=20000)

    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.accounts[user.pk])[0])
        return client

    def configure(self, limit, version=0):
        return self.client_for(self.staff).put('/api/admin/config/provider-capacity/',
            {'max_concurrent_orders': limit, 'version': version}, format='json')

    def add_active(self):
        return make_order(self.boss, service=self.service, customer_account=self.accounts[self.boss.pk],
            provider=self.provider, provider_account=self.accounts[self.provider.pk],
            status='IN_SERVICE', payment_status='PAID')

    def test_configuration_permission_validation_version_and_atomic_audit(self):
        staff = self.client_for(self.staff)
        viewer = self.client_for(self.viewer)
        self.assertEqual(staff.get('/api/admin/config/provider-capacity/').data['data'],
                         {'max_concurrent_orders': 3, 'version': 0})
        denied = viewer.put('/api/admin/config/provider-capacity/', {'max_concurrent_orders': 2, 'version': 0}, format='json')
        self.assertEqual(denied.status_code, 403)
        for invalid in (0, -1, True, 1.5, '2', 2147483648):
            response = self.configure(invalid)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        saved = self.configure(2)
        self.assertEqual(saved.data['data'], {'max_concurrent_orders': 2, 'version': 1})
        audit = AdminAuditLog.objects.get(resource='provider-capacity')
        self.assertEqual((audit.request_body['before']['max_concurrent_orders'],
                          audit.request_body['after']['max_concurrent_orders']), (3, 2))
        stale = self.configure(4, version=0)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(current_capacity_rules(), {'max_concurrent_orders': 2, 'version': 1})
        self.assertEqual(AdminAuditLog.objects.filter(resource='provider-capacity').count(), 1)

    def test_lock_sentinel_preserves_unconfigured_default_until_admin_saves(self):
        from orders.capacity import UNCONFIGURED, ensure_capacity_rule
        ensure_capacity_rule()
        self.assertEqual(SystemConfig.objects.get(key=CONFIG_KEY).value, UNCONFIGURED)
        self.assertEqual(current_capacity_rules(), {'max_concurrent_orders': 3, 'version': 0})
        self.assertEqual(self.configure(4, version=0).data['data'],
                         {'max_concurrent_orders': 4, 'version': 1})

    def test_rolled_back_nested_lock_does_not_leak_its_snapshot(self):
        from orders.capacity import ensure_capacity_rule, lock_capacity_rule_for_admission
        from stores.db import transaction
        ensure_capacity_rule()
        with transaction.atomic():
            try:
                with transaction.atomic():
                    lock_capacity_rule_for_admission()
                    self.assertEqual(current_capacity_rules()['max_concurrent_orders'], 3)
                    raise ValueError('rollback nested savepoint')
            except ValueError:
                pass
            SystemConfig.objects.filter(key=CONFIG_KEY).update(value='{"max_concurrent_orders":2,"version":1}')
            self.assertEqual(current_capacity_rules(), {'max_concurrent_orders': 2, 'version': 1})

    def test_busy_provider_is_selectable_until_configured_limit_then_blocked(self):
        self.assertEqual(self.configure(2).data['code'], 0)
        self.add_active()
        profile = self.provider.escort_profile
        profile.status = 'BUSY'
        profile.save(update_fields=['status'])
        boss, provider, staff = (self.client_for(user) for user in (self.boss, self.provider, self.staff))
        details = boss.get(f'/api/users/escorts/{self.accounts[self.provider.pk].pk}/',
                           {'service_item': self.service.pk}).data['data']
        self.assertTrue(details['can_order'], details)
        stats = provider.get('/api/users/provider-stats/').data['data']
        self.assertEqual((stats['active_orders'], stats['max_concurrent_orders']), (1, 2))
        options = staff.get('/api/admin/options/escorts/', {'service_id': self.service.pk}).data['data']
        rows = options['list'] if isinstance(options, dict) else options
        self.assertTrue(any(row['user'] == self.accounts[self.provider.pk].pk for row in rows), rows)
        created = boss.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'client_request_id': 'capacity-order-one'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        self.assertEqual(provider.get('/api/users/provider-stats/').data['data']['active_orders'], 2)
        self.wallet.refresh_from_db(); before = self.wallet.balance
        blocked = boss.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'client_request_id': 'capacity-order-two'}, format='json')
        self.assertNotEqual(blocked.data['code'], 0, blocked.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, before)
        self.assertFalse(boss.get(f'/api/users/escorts/{self.accounts[self.provider.pk].pk}/',
                                  {'service_item': self.service.pk}).data['data']['can_order'])

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '池内订单抢单须正式入池快照（raw 订单无快照）＋产能配置联动（18-盘点客服安排抢单）；最终联调启用')
    def test_new_limit_applies_to_pool_and_existing_orders_remain(self):
        self.assertEqual(self.configure(1).data['code'], 0)
        active = self.add_active()
        waiting = make_order(self.boss, service=self.service, customer_account=self.accounts[self.boss.pk],
                             status='PENDING', payment_status='PAID')
        Order.objects.filter(pk=waiting.pk).update(created_at=timezone.now() - timedelta(hours=1))
        provider = self.client_for(self.provider)
        response = provider.post(f'/api/orders/orders/{waiting.pk}/grab/', {}, format='json')
        self.assertNotEqual(response.data['code'], 0, response.data)
        self.assertEqual(self.configure(2, version=1).data['code'], 0)
        self.assertEqual(provider.post(f'/api/orders/orders/{waiting.pk}/grab/', {}, format='json').data['code'], 0)
        active.refresh_from_db(); waiting.refresh_from_db()
        self.assertEqual((active.status, waiting.status), ('IN_SERVICE', 'GRABBED'))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '未来预约不占产能须本人录音资格＋正式入池快照抢单（18-盘点客服安排抢单/试音录音）；最终联调启用')
    def test_future_booking_does_not_make_candidate_look_full(self):
        self.assertEqual(self.configure(1).data['code'], 0)
        booked_order = make_order(self.boss, service=self.service, customer_account=self.accounts[self.boss.pk],
            provider=self.provider, provider_account=self.accounts[self.provider.pk], status='GRABBED', payment_status='PAID')
        start = timezone.now() + timedelta(days=1)
        Reservation.objects.create(customer=self.boss, customer_account=self.accounts[self.boss.pk],
            provider=self.provider, provider_account=self.accounts[self.provider.pk], service=self.service,
            order=booked_order, status='CONVERTED', start_time=start, end_time=start+timedelta(hours=1), duration_minutes=60)
        waiting = make_order(self.boss, service=self.service, customer_account=self.accounts[self.boss.pk],
                             status='PENDING', payment_status='PAID', requires_boss_selection=True)
        Order.objects.filter(pk=waiting.pk).update(created_at=timezone.now() - timedelta(hours=1))
        provider = self.client_for(self.provider)
        applied = provider.post(f'/api/orders/orders/{waiting.pk}/grab/', {}, format='json')
        self.assertEqual(applied.data['code'], 0, applied.data)
        boss = self.client_for(self.boss)
        detail = boss.get(f'/api/orders/orders/{waiting.pk}/').data['data']
        self.assertEqual(len(detail['applications']), 1)
        self.assertTrue(detail['applications'][0]['ready'], detail['applications'])
