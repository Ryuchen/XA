"""Feature-gated, real order payments with game rule snapshots."""
import os
from unittest import skipUnless
from unittest.mock import patch
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import GameCategory, GameCommissionProfile, Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction


@override_settings(GAME_COMMISSION_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GameCommissionPaymentTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account = self.link(self.boss)
        self.wallet = make_wallet(self.boss, 100000)
        self.wallet.account = self.boss_account
        self.wallet.save(update_fields=['account'])
        from engagement.models import ClubOperationConfig, PointFundingLot
        ClubOperationConfig.objects.get_or_create(pk=1)
        PointFundingLot.objects.get_or_create(account=self.boss_account, source_key='game-commission-principal',
            kind=PointFundingLot.Kind.PREPAID, defaults={'remaining': 100000})
        self.game = GameCategory.objects.create(name='可更名游戏')
        self.service = make_service(price=5000, game_category=self.game, provider_kind='RANK',
                                    commission_policy='REGULAR')
        self.config = GameCommissionProfile.objects.create(game_category=self.game, game='VALORANT',
            rules={'companion': 11, 'grind': 30})
        self.providers = [make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
            pass_expires_at=timezone.now() + timedelta(days=1)) for _ in range(2)]
        self.accounts = [self.link(user) for user in self.providers]
        for user in self.providers:
            user.escort_profile.service_items.add(self.service)
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch',
                     'notify_order_update', '_push_message_safe'):
            p = patch('orders.views.' + name)
            p.start(); self.addCleanup(p.stop)
        self.auth(self.boss_account)

    def link(self, user):
        account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=user).update(account=account)
        return account

    def auth(self, account):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def create(self, **kwargs):
        return self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'client_request_id': 'game-commission-payment', **kwargs}, format='json')

    def test_selected_provider_keeps_payment_rule_after_config_changes(self):
        response = self.create(provider_id=self.accounts[0].pk)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        row = order.providers.get()
        self.assertEqual((order.commission_rate, row.commission_rate, row.provider_income), (30, 30, 3500))
        self.assertEqual(row.game_commission_snapshot, order.game_commission_snapshot)
        self.assertEqual((row.game_commission_snapshot['game'], row.game_commission_snapshot['version']), ('VALORANT', 1))
        self.config.rules = {'companion': 11, 'grind': 50}; self.config.version = 2; self.config.save()
        from orders.pricing import club_provider_specs
        self.assertEqual(club_provider_specs(order, [self.providers[0].escort_profile], order.amount)[0]['commission_rate'], 30)
        row.refresh_from_db(); self.assertEqual(row.game_commission_snapshot['version'], 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_selected_team_uses_same_game_rule_for_both_shares(self):
        self.service.billing_provider_count = 2; self.service.save(update_fields=['billing_provider_count'])
        response = self.create(provider_ids=[account.pk for account in self.accounts])
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(list(order.providers.values_list('commission_rate', flat=True)), [30, 30])
        self.assertTrue(all(row.game_commission_snapshot == order.game_commission_snapshot for row in order.providers.all()))

    def test_missing_rule_blocks_before_debit(self):
        self.config.rules = {'companion': 11, 'grind': None}; self.config.save(update_fields=['rules'])
        result = self.create()
        self.assertEqual(result.data['code'], 400, result.data)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists()); self.assertFalse(Transaction.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_configuration_change_between_pricing_and_debit_rejects(self):
        from orders import views
        original = views.ensure_provider_dispatch_locks
        def change_before_transaction(ids):
            original(ids)
            GameCommissionProfile.objects.filter(pk=self.config.pk).update(
                rules={'companion': 11, 'grind': 35}, version=2)
        with patch('orders.views.ensure_provider_dispatch_locks', side_effect=change_before_transaction):
            response = self.create()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['errorCode'], 'COMMISSION_CHANGED')
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists()); self.assertFalse(Transaction.objects.exists())

    def test_required_level_rank_change_before_payment_rejects_stale_snapshot(self):
        from users.models import EscortLevel
        level = EscortLevel.objects.create(name='原门槛等级', commission_rate=20, sort_order=1)
        qualified = EscortLevel.objects.create(name='下单陪玩等级', commission_rate=20, sort_order=2)
        self.service.required_level = level; self.service.save(update_fields=['required_level'])
        self.providers[0].escort_profile.level = qualified; self.providers[0].escort_profile.save(update_fields=['level'])
        from orders import views
        original = views.ensure_provider_dispatch_locks
        def change_before_transaction(ids):
            original(ids)
            EscortLevel.objects.filter(pk=level.pk).update(sort_order=5)
        with patch('orders.views.ensure_provider_dispatch_locks', side_effect=change_before_transaction):
            response = self.create(provider_id=self.accounts[0].pk)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['errorCode'], 'COMMISSION_CHANGED')
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists()); self.assertFalse(Transaction.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_later_assignment_uses_original_order_snapshot(self):
        response = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.config.rules = {'companion': 11, 'grind': 60}; self.config.version = 2; self.config.save()
        self.auth(self.accounts[0])
        grabbed = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertEqual(grabbed.data['code'], 0, grabbed.data)
        share = order.providers.get()
        self.assertEqual((share.commission_rate, share.provider_income, share.game_commission_snapshot['version']),
                         (30, 3500, 1))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_staff_order_uses_game_rule_and_snapshot(self):
        staff = make_console_user(['order:view', 'order:dispatch'])
        account = self.link(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.auth(account)
        response = self.client.post('/api/admin/orders/dispatch/', {'customer_id': self.boss_account.pk,
            'service_id': self.service.pk, 'provider_id': self.accounts[0].pk,
            'client_request_id': 'game-commission-staff'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        share = order.providers.get()
        self.assertEqual((share.commission_rate, share.provider_income, share.game_commission_snapshot['version']),
                         (30, 3500, 1))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_staff_stale_config_rejected_before_wallet_debit(self):
        from console import views
        staff = make_console_user(['order:view', 'order:dispatch'])
        account = self.link(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.auth(account)
        original = views.ensure_provider_dispatch_locks
        def changed(ids):
            original(ids)
            GameCommissionProfile.objects.filter(pk=self.config.pk).update(
                rules={'companion': 11, 'grind': 35}, version=2)
        with patch('console.views.ensure_provider_dispatch_locks', side_effect=changed):
            response = self.client.post('/api/admin/orders/dispatch/', {'customer_id': self.boss_account.pk,
                'service_id': self.service.pk, 'provider_id': self.accounts[0].pk,
                'client_request_id': 'game-commission-staff-race'}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists()); self.assertFalse(Transaction.objects.exists())

    def test_unsupported_service_override_is_not_silently_ignored(self):
        self.service.commission_override = True; self.service.commission_rate = 42
        self.service.save(update_fields=['commission_override', 'commission_rate'])
        response = self.create()
        self.assertEqual(response.data['code'], 400, response.data)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists()); self.assertFalse(Transaction.objects.exists())


from orders.tests.test_reservation_api import ReservationApiTestBase


@override_settings(GAME_COMMISSION_PAYMENT_ENABLED=True)
class GameCommissionReservationTest(ReservationApiTestBase):
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_conversion_keeps_game_rule_on_order_and_provider(self):
        game = GameCategory.objects.create(name='预约规则测试')
        GameCommissionProfile.objects.create(game_category=game, game='LEAGUE',
            rules={'companion': 15, 'grind': 25})
        self.service.game_category = game; self.service.provider_kind = 'RANK'
        self.service.save(update_fields=['game_category', 'provider_kind'])
        self.provider.escort_profile.service_items.add(self.service)
        reservation = self.create_reservation_via_api()
        self.client.force_authenticate(self.provider)
        self.assertEqual(self.client.post(f'/api/orders/reservations/{reservation.pk}/confirm/', {}).data['code'], 0)
        self.client.force_authenticate(self.boss)
        result = self.client.post(f'/api/orders/reservations/{reservation.pk}/convert/',
                                  {'client_request_id': 'game-reservation'})
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        row = order.providers.get()
        self.assertEqual((order.commission_rate, row.commission_rate, row.provider_income), (25, 25, 1500))
        self.assertEqual(row.game_commission_snapshot['game'], 'LEAGUE')
