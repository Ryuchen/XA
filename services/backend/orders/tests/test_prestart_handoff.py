from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderPrestartHandoff, OrderProvider
from orders.reservation_services import find_conflicting_reservations
from orders.tests import test_release_flow as flow
from orders.tests import test_game_commission_payment as game_fixture
from orders.tests import test_staff_team_dispatch as team_fixture
from orders.tests import test_assigned_order_reservation_guard as fixture
from orders.tests.factories import make_provider
from wallet.models import Transaction, Wallet


@override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class PrestartHandoffTest(APITransactionTestCase):
    setUp = fixture.AssignedOrderReservationGuardTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    book = fixture.AssignedOrderReservationGuardTest.book

    def replacement(self, name='接替陪玩'):
        user = make_provider(display_name=name, deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(user)
        user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        self.accounts[user.pk] = account
        return user

    def transfer(self, order_id, old, new, **extra):
        self.login(self.admin)
        command = {'old_provider_id':self.accounts[old.pk].pk, 'new_provider_id':self.accounts[new.pk].pk,
                   'split_type':'PERCENT', 'split_value':0, 'penalty_amount':0, **extra}
        return self.client.post(f'/api/admin/orders/{order_id}/transfer/',
                                {'reason':'客服协调更换', 'transfers':[command]}, format='json')

    def test_normal_order_reassign_and_return_preserve_payment_and_each_rule(self):
        new = self.replacement()
        order = self.new_order()
        original = OrderProvider.objects.get(order=order, provider_account=self.accounts[self.provider.pk])
        before = (order.amount, order.payment_status, Wallet.objects.get(account=self.accounts[self.boss.pk]).balance,
                  Transaction.objects.filter(order=order).count())
        self.service.commission_override = True; self.service.commission_rate = 30
        self.service.save(update_fields=['commission_override','commission_rate'])
        response = self.transfer(order.pk, self.provider, new)
        self.ok(response)
        order.refresh_from_db(); original.refresh_from_db()
        incoming = OrderProvider.objects.get(order=order, provider_account=self.accounts[new.pk])
        self.assertEqual((order.status, order.provider_account_id, incoming.commission_rate, incoming.provider_income),
                         ('GRABBED', self.accounts[new.pk].pk, 30, 7000))
        self.assertEqual((original.settlement_base, original.provider_income), (0, 0))
        self.assertIsNotNone(original.released_at)
        self.assertEqual((order.amount, order.payment_status, Wallet.objects.get(account=self.accounts[self.boss.pk]).balance,
                          Transaction.objects.filter(order=order).count()), before)
        first = OrderPrestartHandoff.objects.get(order=order)
        self.assertEqual((first.old_snapshot['commission_rate'], first.new_snapshot['commission_rate']), (20, 30))
        self.login(self.admin)
        audit = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))['prestart_handoffs']
        self.assertEqual(len(audit), 1)
        self.assertEqual((audit[0]['old_provider'], audit[0]['new_provider'], audit[0]['new_snapshot']['commission_rate']),
                         (self.accounts[self.provider.pk].pk, self.accounts[new.pk].pk, 30))
        self.login(new)
        self.assertIn('START', self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['allowed_actions'])
        self.login(self.provider)
        previous_detail = self.client.get(f'/api/orders/orders/{order.pk}/')
        if previous_detail.data.get('code') == 0:
            self.assertNotIn('START', previous_detail.data['data']['allowed_actions'])
        self.login(self.boss)
        customer_detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual(customer_detail['provider']['id'], new.pk)
        self.assertNotEqual(self.transfer(order.pk, self.provider, new).data['code'], 0)
        self.service.commission_rate = 25; self.service.save(update_fields=['commission_rate'])
        self.ok(self.transfer(order.pk, new, self.provider))
        original.refresh_from_db(); incoming.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((original.commission_rate, original.provider_income, incoming.provider_income), (25, 7500, 0))
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 2)
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).order_by('pk').first().old_snapshot['commission_rate'], 20)
        self.assertEqual(Transaction.objects.filter(order=order).count(), before[3])

    def test_appointment_conflict_keeps_original_then_handoff_moves_slot(self):
        new = self.replacement()
        self.ok(self.recharge())
        start = (timezone.now()+timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        booking_id, order_id = self.book(self.provider, start, 'prestart-reservation')
        self.login(self.boss)
        competing = self.ok(self.client.post('/api/orders/reservations/', {'service_id':self.service.pk,
            'provider_account_id':self.accounts[new.pk].pk, 'start_time':start.isoformat(),
            'end_time':(start+timedelta(hours=1)).isoformat()}, format='json'))
        denied = self.transfer(order_id, self.provider, new)
        self.assertIn('原预约时段', denied.data['msg'])
        self.assertTrue(find_conflicting_reservations(self.accounts[self.provider.pk], start, start+timedelta(hours=1)).filter(pk=booking_id).exists())
        self.assertFalse(OrderPrestartHandoff.objects.filter(order_id=order_id).exists())
        self.login(self.boss)
        self.ok(self.client.post(f"/api/orders/reservations/{competing['id']}/cancel/", {}, format='json'))
        self.ok(self.transfer(order_id, self.provider, new))
        self.assertFalse(find_conflicting_reservations(self.accounts[self.provider.pk], start, start+timedelta(hours=1)).exists())
        self.assertTrue(find_conflicting_reservations(self.accounts[new.pk], start, start+timedelta(hours=1)).filter(pk=booking_id).exists())
        self.assertEqual(Order.objects.get(pk=order_id).status, 'GRABBED')

    def test_future_appointment_can_move_to_provider_at_capacity(self):
        from orders.services import count_capacity_orders
        from wallet.models import SystemConfig
        from orders.capacity import CONFIG_KEY
        import json
        new = self.replacement(); self.ok(self.recharge())
        start = (timezone.now()+timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        _, order_id = self.book(self.provider, start, 'prestart-full-capacity')
        SystemConfig.objects.update_or_create(key=CONFIG_KEY, defaults={'value':json.dumps({'max_concurrent_orders':1, 'version':1})})
        Order.objects.create(customer=self.boss, customer_account=self.accounts[self.boss.pk], provider=new,
            provider_account=self.accounts[new.pk], service=self.service, status='IN_SERVICE', payment_status='PAID', amount=1000)
        self.assertEqual(count_capacity_orders(self.accounts[new.pk]), 1)
        self.ok(self.transfer(order_id, self.provider, new))
        self.assertEqual(count_capacity_orders(self.accounts[new.pk]), 1)
        self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, self.accounts[new.pk].pk)

    def test_prestart_handoff_rejects_earnings_or_penalty_and_missing_reason(self):
        new = self.replacement(); order = self.new_order()
        denied = self.transfer(order.pk, self.provider, new, split_value=10)
        self.assertNotEqual(denied.data['code'], 0)
        denied = self.transfer(order.pk, self.provider, new, penalty_amount=10)
        self.assertNotEqual(denied.data['code'], 0)
        self.login(self.admin)
        denied = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'', 'transfers':[{
            'old_provider_id':self.accounts[self.provider.pk].pk, 'new_provider_id':self.accounts[new.pk].pk}]}, format='json')
        self.assertNotEqual(denied.data['code'], 0)
        self.assertFalse(OrderPrestartHandoff.objects.exists())

    def test_without_dispatch_permission_or_switch_no_handoff(self):
        from orders.tests.factories import make_console_user
        new = self.replacement(); order = self.new_order()
        staff = make_console_user(['order:view']); account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.accounts[staff.pk] = account; self.login(staff)
        body = {'reason':'协调更换', 'transfers':[{'old_provider_id':self.accounts[self.provider.pk].pk,
                                                 'new_provider_id':self.accounts[new.pk].pk}]}
        denied = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', body, format='json')
        self.assertEqual(denied.status_code, 403)
        self.login(self.admin)
        with override_settings(PRESTART_HANDOFF_ENABLED=False):
            denied = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', body, format='json')
        self.assertNotEqual(denied.data['code'], 0)
        self.assertFalse(OrderPrestartHandoff.objects.exists())

    def test_audit_write_failure_rolls_back_assignments_and_funds(self):
        from unittest.mock import patch
        from django.db import DatabaseError
        new = self.replacement(); order = self.new_order()
        original_balance = Wallet.objects.get(account=self.accounts[self.boss.pk]).balance
        with patch('orders.models.OrderPrestartHandoff.objects.create', side_effect=DatabaseError('audit failed')):
            with self.assertRaises(DatabaseError):
                self.transfer(order.pk, self.provider, new)
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        original = order.providers.get(provider_account=self.accounts[self.provider.pk])
        self.assertIsNone(original.released_at)
        self.assertFalse(order.providers.filter(provider_account=self.accounts[new.pk]).exists())
        self.assertFalse(OrderPrestartHandoff.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance, original_balance)

    def test_full_refund_after_handoff_restores_original_payment_once(self):
        new = self.replacement(); order = self.new_order()
        balance_before = Wallet.objects.get(account=self.accounts[self.boss.pk]).balance
        self.ok(self.transfer(order.pk, self.provider, new))
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        response = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason':'客服协调取消',
            'plan_token':preview['plan_token'], 'client_request_id':'prestart-handoff-refund'}, format='json')
        self.ok(response)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance, balance_before + order.amount)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 1)

    def test_replacement_can_fulfill_report_and_settle_without_old_report(self):
        from unittest.mock import patch
        from engagement.confirmation import process_order
        from orders.services import required_report_providers
        from wallet.models import ProviderReport
        new = self.replacement(); order = self.new_order()
        self.ok(self.transfer(order.pk, self.provider, new))
        order.refresh_from_db()
        self.assertEqual(required_report_providers(order), {new.pk})
        self.login(new)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image':flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=new)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
            self.assertTrue(process_order(order.pk))
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Transaction.objects.get(order=order, wallet__account=self.accounts[new.pk], tx_type='INCOME').amount, 8000)
        self.assertEqual(Wallet.objects.get(account=self.accounts[new.pk]).balance, 3200)
        new.escort_profile.refresh_from_db()
        self.assertEqual(new.escort_profile.deposit_paid, 4800)
        self.assertFalse(ProviderReport.objects.filter(order=order, provider=self.provider).exists())

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True)
    def test_free_safety_box_moves_unpaid_subsidy_without_money_movement(self):
        from unittest.mock import patch
        from coupons.benefits import grant_periodic
        from coupons.models import UserCoupon
        from engagement.confirmation import process_order
        from engagement.models import VipTier
        from orders.models import OrderPlatformSubsidy, OrderPlatformSubsidyShare
        from orders.tests.factories import make_boss_type, make_service
        from wallet.models import ProviderReport, get_platform_wallet
        from wallet.services import get_wallet
        new = self.replacement()
        service = make_service(price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = VipTier.objects.create(level=6, boss_type=make_boss_type(), recharge_threshold=0, safe_box_service_ids=[service.pk])
        account = self.accounts[self.boss.pk]; account.boss_type = tier.boss_type; account.save(update_fields=['boss_type'])
        grant_periodic(account)
        voucher = UserCoupon.objects.get(account=account, coupon__scope='SAFE_BOX')
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'prestart-subsidy'}, format='json'))
        order = Order.objects.get(pk=created['id']); subsidy = OrderPlatformSubsidy.objects.get(order=order)
        before = Transaction.objects.filter(order=order).count()
        self.ok(self.transfer(order.pk, self.provider, new))
        shares = {row.assignment.provider_account_id:row.amount for row in OrderPlatformSubsidyShare.objects.filter(subsidy=subsidy).select_related('assignment')}
        self.assertEqual(shares, {self.accounts[self.provider.pk].pk:0, self.accounts[new.pk].pk:8000})
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)
        self.assertEqual(get_wallet(account_id=self.accounts[new.pk].pk).balance, 0)
        event = OrderPrestartHandoff.objects.get(order=order)
        self.assertEqual((event.old_snapshot['subsidy_amount'], event.new_snapshot['subsidy_amount']), (8000, 8000))
        self.login(new)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image':flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=new)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT','image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        platform = get_platform_wallet(); platform.balance = 10000; platform.save(update_fields=['balance'])
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
            self.assertTrue(process_order(order.pk))
        self.assertEqual(Transaction.objects.get(order=order, wallet__account_id=self.accounts[new.pk].pk, tx_type='INCOME').amount, 8000)
        self.assertFalse(Transaction.objects.filter(order=order, wallet__account_id=self.accounts[self.provider.pk].pk, tx_type='INCOME').exists())


@override_settings(PRESTART_HANDOFF_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class PrestartGameCommissionTest(APITransactionTestCase):
    setUp = game_fixture.GameCommissionPaymentTest.setUp
    link = game_fixture.GameCommissionPaymentTest.link
    auth = game_fixture.GameCommissionPaymentTest.auth
    create = game_fixture.GameCommissionPaymentTest.create

    def test_replacement_gets_current_game_rule_but_old_snapshot_is_preserved(self):
        from orders.tests.factories import make_console_user
        response = self.create(provider_id=self.accounts[0].pk)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.config.rules = {'companion':11, 'grind':50}; self.config.version = 2; self.config.save()
        staff = make_console_user(['order:view','order:dispatch']); admin = self.link(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.auth(admin)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        old = order.providers.get(provider_account=self.accounts[0]); new = order.providers.get(provider_account=self.accounts[1])
        self.assertEqual((old.game_commission_snapshot['version'], new.game_commission_snapshot['version'], new.provider_income), (1, 2, 2500))
        self.assertEqual(OrderPrestartHandoff.objects.get(order=order).old_snapshot['game_commission_snapshot']['version'], 1)

    def test_changed_catalog_does_not_change_the_purchased_game_scope(self):
        from orders.models import GameCategory, GameCommissionProfile
        from orders.tests.factories import make_console_user, make_service
        from users.models import ProviderQualification
        self.service.catalog_family = '原游戏服务家族'
        self.service.platform = 'PC'; self.service.security_level = 'ANY'; self.service.provider_grade = 'OPEN'
        self.service.save(update_fields=['catalog_family', 'platform', 'security_level', 'provider_grade'])
        for user in self.providers:
            ProviderQualification.objects.create(profile=user.escort_profile, game_category=self.game,
                provider_kind='RANK', platform='PC', security_level='ANY', grade='OPEN')
        response = self.create(provider_id=self.accounts[0].pk)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        original_snapshot = dict(order.game_commission_snapshot)
        self.config.rules = {'companion':11, 'grind':50}; self.config.version = 2; self.config.save()
        other_game = GameCategory.objects.create(name='后续修改的游戏')
        GameCommissionProfile.objects.create(game_category=other_game, game='LEAGUE', rules={'companion':5, 'grind':10})
        self.service.game_category = other_game; self.service.provider_kind = 'COMPANION'; self.service.is_active = False
        self.service.save(update_fields=['game_category','provider_kind','is_active'])
        staff = make_console_user(['order:view','order:dispatch']); admin = self.link(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.auth(admin)
        body = {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk}]}
        denied = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', body, format='json')
        self.assertIn('缺少可核实的原服务技能', denied.data['msg'])
        self.assertFalse(OrderPrestartHandoff.objects.filter(order=order).exists())
        equivalent = make_service(game_category=self.game, catalog_family='原游戏服务家族', provider_kind='RANK',
                                  platform='PC', security_level='ANY', provider_grade='OPEN')
        self.providers[1].escort_profile.service_items.add(equivalent)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', body, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        incoming = order.providers.get(provider_account=self.accounts[1])
        self.assertEqual((incoming.provider_income, incoming.game_commission_snapshot['game_category_id'],
                          incoming.game_commission_snapshot['scope']['provider_kind']), (2500, self.game.pk, 'grind'))
        order.refresh_from_db(); self.assertEqual(order.game_commission_snapshot, original_snapshot)

    def test_incomplete_purchased_scope_does_not_guess_from_current_catalog(self):
        from orders.tests.factories import make_console_user
        response = self.create(provider_id=self.accounts[0].pk)
        order = Order.objects.get(pk=response.data['data']['id'])
        order.game_commission_snapshot.pop('service_scope'); order.save(update_fields=['game_commission_snapshot'])
        staff = make_console_user(['order:view','order:dispatch']); admin = self.link(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.auth(admin)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk}]}, format='json')
        self.assertNotEqual(result.data['code'], 0, result.data)
        self.assertFalse(OrderPrestartHandoff.objects.filter(order=order).exists())
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[0].pk)

    def test_legacy_snapshot_without_level_evidence_is_not_silently_reinterpreted(self):
        from orders.tests.factories import make_console_user
        response = self.create(provider_id=self.accounts[0].pk)
        order = Order.objects.get(pk=response.data['data']['id'])
        scope = order.game_commission_snapshot['service_scope']
        for key in ('catalog_family','required_level_id','required_level_rank'):
            scope.pop(key)
        order.save(update_fields=['game_commission_snapshot'])
        staff = make_console_user(['order:view','order:dispatch']); admin = self.link(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.auth(admin)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk}]}, format='json')
        self.assertIn('历史订单未保存原最低等级', result.data['msg'])
        self.assertFalse(OrderPrestartHandoff.objects.filter(order=order).exists())

    def test_verified_legacy_minimum_is_enforced_and_audited(self):
        from orders.tests.factories import make_console_user
        from users.models import EscortLevel
        required = EscortLevel.objects.create(name='核对后的最低等级', commission_rate=20, sort_order=10)
        incoming = EscortLevel.objects.create(name='候选陪玩等级', commission_rate=20, sort_order=5)
        self.providers[1].escort_profile.level = incoming; self.providers[1].escort_profile.save(update_fields=['level'])
        response = self.create(provider_id=self.accounts[0].pk)
        order = Order.objects.get(pk=response.data['data']['id'])
        for key in ('catalog_family','required_level_id','required_level_rank'):
            order.game_commission_snapshot['service_scope'].pop(key)
        order.save(update_fields=['game_commission_snapshot'])
        staff = make_console_user(['order:view','order:dispatch']); admin = self.link(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.auth(admin)
        detail = self.client.get(f'/api/admin/orders/{order.pk}/').data['data']['prestart_legacy_minimum']
        self.assertTrue(detail['required'])
        self.assertIn(required.pk, [item['id'] for item in detail['levels']])
        body = {'reason':'更换陪玩', 'transfers':[{'old_provider_id':self.accounts[0].pk,
            'new_provider_id':self.accounts[1].pk}], 'legacy_minimum_review':{
                'choice':'LEVEL', 'level_id':required.pk, 'original_requirement':'旧价目写明十级', 'evidence':'核对当时的价目记录'}}
        denied = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', body, format='json')
        self.assertNotEqual(denied.data['code'], 0, denied.data)
        self.assertFalse(OrderPrestartHandoff.objects.filter(order=order).exists())
        incoming.sort_order = 11; incoming.save(update_fields=['sort_order'])
        accepted = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', body, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        review = OrderPrestartHandoff.objects.get(order=order).new_snapshot['legacy_minimum_review']
        self.assertEqual((review['level_id'], review['level_rank'], review['operator_id']), (required.pk, 10, admin.pk))
        self.assertEqual(review['evidence'], '核对当时的价目记录')
        order.refresh_from_db()
        self.assertNotIn('required_level_rank', order.game_commission_snapshot['service_scope'])
        detail = self.client.get(f'/api/admin/orders/{order.pk}/').data['data']['prestart_legacy_minimum']
        self.assertFalse(detail['required'])
        self.assertEqual(detail['review']['level_rank'], 10)
        self.providers[0].escort_profile.level = incoming; self.providers[0].escort_profile.save(update_fields=['level'])
        reverse = {'reason':'重新协调', 'transfers':[{'old_provider_id':self.accounts[1].pk,
                   'new_provider_id':self.accounts[0].pk}]}
        self.assertEqual(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', reverse, format='json').data['code'], 0)
        order.refresh_from_db(); order.game_commission_snapshot['version'] += 1
        order.save(update_fields=['game_commission_snapshot'])
        self.assertTrue(self.client.get(f'/api/admin/orders/{order.pk}/').data['data']['prestart_legacy_minimum']['required'])

    def test_new_order_freezes_minimum_level_and_rank_for_later_handoff(self):
        from orders.tests.factories import make_console_user
        from users.models import EscortLevel
        required = EscortLevel.objects.create(name='原订单最低级', commission_rate=20, sort_order=10)
        high = EscortLevel.objects.create(name='原陪玩级别', commission_rate=20, sort_order=20)
        lower = EscortLevel.objects.create(name='接替陪玩级别', commission_rate=20, sort_order=8)
        self.service.required_level = required; self.service.save(update_fields=['required_level'])
        self.providers[0].escort_profile.level = high; self.providers[0].escort_profile.save(update_fields=['level'])
        self.providers[1].escort_profile.level = lower; self.providers[1].escort_profile.save(update_fields=['level'])
        response = self.create(provider_id=self.accounts[0].pk)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        scope = order.game_commission_snapshot['service_scope']
        self.assertEqual((scope['required_level_id'], scope['required_level_rank']), (required.pk, 10))
        self.service.required_level = None; self.service.save(update_fields=['required_level'])
        required.sort_order = 2; required.save(update_fields=['sort_order'])
        staff = make_console_user(['order:view','order:dispatch']); admin = self.link(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.auth(admin)
        body = {'reason':'更换陪玩', 'transfers':[{'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk}]}
        denied = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', body, format='json')
        self.assertNotEqual(denied.data['code'], 0, denied.data)
        self.assertFalse(OrderPrestartHandoff.objects.filter(order=order).exists())
        lower.sort_order = 12; lower.save(update_fields=['sort_order'])
        accepted = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', body, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        self.assertEqual(order.providers.get(provider_account=self.accounts[1]).game_commission_snapshot['service_scope']['required_level_rank'], 10)

    def test_new_order_uses_original_catalog_family_for_replacement_skills(self):
        from orders.tests.factories import make_console_user, make_service
        from users.models import ProviderQualification
        self.service.catalog_family = '已购买服务家族'
        self.service.platform = 'PC'; self.service.security_level = 'ANY'; self.service.provider_grade = 'OPEN'
        self.service.save(update_fields=['catalog_family','platform','security_level','provider_grade'])
        for user in self.providers:
            ProviderQualification.objects.create(profile=user.escort_profile, game_category=self.game,
                provider_kind='RANK', platform='PC', security_level='ANY', grade='OPEN')
        equivalent = make_service(game_category=self.game, catalog_family='已购买服务家族', provider_kind='RANK',
                                  platform='PC', security_level='ANY', provider_grade='OPEN')
        self.providers[1].escort_profile.service_items.remove(self.service)
        self.providers[1].escort_profile.service_items.add(equivalent)
        response = self.create(provider_id=self.accounts[0].pk)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(order.game_commission_snapshot['service_scope']['catalog_family'], '已购买服务家族')
        self.service.catalog_family = '后来修改的家族'; self.service.save(update_fields=['catalog_family'])
        staff = make_console_user(['order:view','order:dispatch']); admin = self.link(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.auth(admin)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)


@override_settings(PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PrestartTeamHandoffTest(APITransactionTestCase):
    setUp = team_fixture.StaffTeamDispatchTest.setUp
    auth = team_fixture.StaffTeamDispatchTest.auth
    create = team_fixture.StaffTeamDispatchTest.create
    assign = team_fixture.StaffTeamDispatchTest.assign

    def test_replace_first_and_return_without_changing_teammate_or_fixed_remainder(self):
        order = self.create()
        self.assertEqual(self.assign(order).data['code'], 0)
        initial = list(order.providers.order_by('pk'))
        self.assertEqual([row.commission_fixed for row in initial], [501, 500])
        self.auth(self.staff)
        def handoff(old, new):
            return self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
                'old_provider_id':self.accounts[old.pk].pk, 'new_provider_id':self.accounts[new.pk].pk}]}, format='json')
        changed = handoff(self.providers[0], self.providers[2])
        self.assertEqual(changed.data['code'], 0, changed.data)
        old, teammate, replacement = [order.providers.get(provider_account=self.accounts[user.pk]) for user in self.providers]
        self.assertEqual((old.provider_income, teammate.commission_fixed, replacement.commission_fixed), (0, 500, 501))
        changed = handoff(self.providers[2], self.providers[0])
        self.assertEqual(changed.data['code'], 0, changed.data)
        old.refresh_from_db(); teammate.refresh_from_db(); replacement.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((old.commission_fixed, teammate.commission_fixed, replacement.provider_income), (501, 500, 0))
        self.assertEqual(order.provider_income + order.inviter_commission + order.shop_income, order.amount)
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 2)

    def test_other_team_member_can_start_while_unstarted_member_is_replaced(self):
        order = self.create()
        self.assertEqual(self.assign(order).data['code'], 0)
        self.auth(self.providers[0])
        started = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(started.data['code'], 0, started.data)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[1].pk].pk, 'new_provider_id':self.accounts[self.providers[2].pk].pk}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 1)
        from wallet.models import ProviderReport
        self.assertTrue(ProviderReport.objects.filter(order=order, provider=self.providers[0]).exists())
        self.auth(self.providers[2])
        entry = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(entry.data['code'], 0, entry.data)
        order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
