from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import GameCategory, Order
from orders.tests import test_game_commission_payment as fixture
from orders.tests import test_staff_team_dispatch as team_fixture
from orders.tests.factories import make_console_user, make_service
from users.models import EscortLevel, ProviderQualification


@override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class PaidScopePoolTest(APITransactionTestCase):
    setUp = fixture.GameCommissionPaymentTest.setUp
    link = fixture.GameCommissionPaymentTest.link
    auth = fixture.GameCommissionPaymentTest.auth
    create = fixture.GameCommissionPaymentTest.create

    def pool_order(self, **values):
        response = self.create(dispatch_mode='AUTO_GRAB', **values)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now()-timedelta(minutes=10),
                                                  auto_cancel_at=timezone.now()+timedelta(hours=1))
        return order

    def test_original_minimum_controls_pool_action_and_final_grab(self):
        self.service.game_category = None; self.service.save(update_fields=['game_category'])
        level = EscortLevel.objects.create(name='原最低等级', commission_rate=20, sort_order=10)
        low = EscortLevel.objects.create(name='较低等级', commission_rate=20, sort_order=5)
        self.service.required_level = level; self.service.save(update_fields=['required_level'])
        self.providers[0].escort_profile.level = low; self.providers[0].escort_profile.save(update_fields=['level'])
        self.providers[1].escort_profile.level = level; self.providers[1].escort_profile.save(update_fields=['level'])
        order = self.pool_order()
        self.service.required_level = None; self.service.save(update_fields=['required_level'])
        self.auth(self.accounts[0])
        pool = self.client.get('/api/orders/orders/', {'status':'pending'}).data['data']
        self.assertNotIn(order.pk, [row['id'] for row in pool])
        denied = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertNotEqual(denied.data['code'], 0, denied.data)
        self.auth(self.accounts[1])
        pool = self.client.get('/api/orders/orders/', {'status':'pending'}).data['data']
        row = next(row for row in pool if row['id'] == order.pk)
        self.assertIn('GRAB', row['allowed_actions'])
        self.providers[1].escort_profile.level = low; self.providers[1].escort_profile.save(update_fields=['level'])
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json').data['code'], 0)
        self.providers[1].escort_profile.level = level; self.providers[1].escort_profile.save(update_fields=['level'])
        grabbed = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertEqual(grabbed.data['code'], 0, grabbed.data)
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[1].pk)

    def test_changed_game_cannot_authorize_from_mutable_service_id(self):
        self.service.catalog_family = '原游戏服务'
        self.service.platform = 'PC'; self.service.security_level = 'ANY'; self.service.provider_grade = 'OPEN'
        self.service.save(update_fields=['catalog_family','platform','security_level','provider_grade'])
        for provider in self.providers:
            ProviderQualification.objects.create(profile=provider.escort_profile, game_category=self.game,
                provider_kind='RANK', platform='PC', security_level='ANY', grade='OPEN')
        equivalent = make_service(game_category=self.game, catalog_family='原游戏服务', provider_kind='RANK',
                                  platform='PC', security_level='ANY', provider_grade='OPEN')
        self.providers[1].escort_profile.service_items.add(equivalent)
        order = self.pool_order()
        other = GameCategory.objects.create(name='修改后游戏')
        self.service.game_category = other; self.service.catalog_family = '其他游戏服务'
        self.service.save(update_fields=['game_category','catalog_family'])
        self.auth(self.accounts[0])
        pool = self.client.get('/api/orders/orders/', {'status':'pending'}).data['data']
        self.assertNotIn(order.pk, [row['id'] for row in pool])
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json').data['code'], 0)
        self.auth(self.accounts[1])
        pool = self.client.get('/api/orders/orders/', {'status':'pending', 'game_category':self.game.pk}).data['data']
        self.assertIn(order.pk, [row['id'] for row in pool])
        result = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)

    def test_staff_assignment_respects_original_minimum(self):
        self.service.game_category = None; self.service.save(update_fields=['game_category'])
        level = EscortLevel.objects.create(name='原最低等级', commission_rate=20, sort_order=10)
        self.service.required_level = level; self.service.save(update_fields=['required_level'])
        response = self.create(dispatch_route='STAFF')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.service.required_level = None; self.service.save(update_fields=['required_level'])
        staff = make_console_user(['order:view','order:dispatch']); admin = self.link(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.auth(admin)
        body = {'provider_id':self.accounts[0].pk}
        denied = self.client.post(f'/api/admin/orders/{order.pk}/assign/', body, format='json')
        self.assertNotEqual(denied.data['code'], 0, denied.data)
        self.providers[0].escort_profile.level = level; self.providers[0].escort_profile.save(update_fields=['level'])
        accepted = self.client.post(f'/api/admin/orders/{order.pk}/assign/', body, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)

    def test_application_and_selection_recheck_original_minimum(self):
        from orders.models import OrderApplication
        self.service.game_category = None; self.service.save(update_fields=['game_category'])
        level = EscortLevel.objects.create(name='原最低等级', commission_rate=20, sort_order=10)
        low = EscortLevel.objects.create(name='较低等级', commission_rate=20, sort_order=5)
        self.service.required_level = level; self.service.save(update_fields=['required_level'])
        self.providers[0].escort_profile.level = low; self.providers[0].escort_profile.save(update_fields=['level'])
        self.providers[1].escort_profile.level = level; self.providers[1].escort_profile.save(update_fields=['level'])
        response = self.create(dispatch_mode='BOSS_SELECT')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now()-timedelta(minutes=10),
                                                  auto_cancel_at=timezone.now()+timedelta(hours=1))
        self.service.required_level = None; self.service.save(update_fields=['required_level'])
        self.auth(self.accounts[0])
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json').data['code'], 0)
        self.auth(self.accounts[1])
        accepted = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        application = OrderApplication.objects.get(order=order, applicant=self.accounts[1])
        self.providers[1].escort_profile.level = low; self.providers[1].escort_profile.save(update_fields=['level'])
        self.auth(self.boss_account)
        url = f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/'
        self.assertNotEqual(self.client.post(url, {}, format='json').data['code'], 0)
        self.providers[1].escort_profile.level = level; self.providers[1].escort_profile.save(update_fields=['level'])
        self.assertEqual(self.client.post(url, {}, format='json').data['code'], 0)

    def test_corrupt_paid_scope_is_neither_visible_nor_grabbable(self):
        from orders.models import OrderPaidServiceScope
        self.service.game_category = None; self.service.save(update_fields=['game_category'])
        order = self.pool_order()
        snapshot = OrderPaidServiceScope.objects.get(order=order)
        snapshot.scope.pop('provider_kind'); snapshot.save(update_fields=['scope'])
        self.auth(self.accounts[0])
        pool = self.client.get('/api/orders/orders/', {'status':'pending'}).data['data']
        self.assertNotIn(order.pk, [row['id'] for row in pool])
        denied = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertIn('原服务范围待核对', denied.data['msg'])

    def test_malformed_qualification_snapshot_does_not_break_pool(self):
        self.service.game_category = None; self.service.save(update_fields=['game_category'])
        order = self.pool_order()
        Order.objects.filter(pk=order.pk).update(provider_requirement_snapshot=['invalid'])
        self.auth(self.accounts[0])
        pool = self.client.get('/api/orders/orders/', {'status':'pending'}).data['data']
        self.assertNotIn(order.pk, [row['id'] for row in pool])
        result = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertIn('原服务范围待核对', result.data['msg'])


@override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class PaidScopeTeamAssignmentTest(APITransactionTestCase):
    setUp = team_fixture.StaffTeamDispatchTest.setUp
    auth = team_fixture.StaffTeamDispatchTest.auth
    create = team_fixture.StaffTeamDispatchTest.create
    assign = team_fixture.StaffTeamDispatchTest.assign

    def test_each_team_member_must_satisfy_original_minimum(self):
        required = EscortLevel.objects.create(name='团队原最低等级', commission_rate=20, sort_order=10)
        lower = EscortLevel.objects.create(name='团队较低等级', commission_rate=20, sort_order=2)
        self.service.required_level = required; self.service.save(update_fields=['required_level'])
        self.providers[0].escort_profile.level = required; self.providers[0].escort_profile.save(update_fields=['level'])
        self.providers[1].escort_profile.level = lower; self.providers[1].escort_profile.save(update_fields=['level'])
        order = self.create()
        self.service.required_level = None; self.service.save(update_fields=['required_level'])
        self.assertNotEqual(self.assign(order).data['code'], 0)
        self.assertFalse(order.providers.exists())
        self.providers[1].escort_profile.level = required; self.providers[1].escort_profile.save(update_fields=['level'])
        accepted = self.assign(order)
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        self.assertEqual(order.providers.count(), 2)
