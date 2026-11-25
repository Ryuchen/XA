from django.test import override_settings
from rest_framework.test import APITestCase, APITransactionTestCase

from orders.tests import test_release_flow as flow
from orders.tests import test_ordinary_refund_without_addon_funding as prior


@override_settings(**prior.OrdinaryRefundWithoutAddonFundingTest._overridden_settings)
class OrdinaryAddonProductionRefundTest(APITestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    exercise_refund_funding = prior.OrdinaryRefundWithoutAddonFundingTest.exercise_refund_funding

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_unaffected_addon_full_refund_after_main_refund(self):
        self.exercise_refund_funding(double=True, addon=True, preallocated=True,
                                     refund_preallocated=True, addon_after_main=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_unaffected_addon_partial_refund_after_main_refund(self, *, gift=False):
        self.exercise_refund_funding(double=True, addon=True, preallocated=True,
            refund_preallocated=True, addon_after_main=True, addon_after_partial=True, addon_after_partial_gift=gift)
        from datetime import date
        from django.utils import timezone
        from engagement.achievement_metrics import confirmed_service_metrics
        from console.payroll_refund_performance import monthly_performance_bases
        self.assertEqual(confirmed_service_metrics(self.accounts[self.boss.pk]), {'amount': 19400, 'count': 1})
        now = timezone.localdate()
        self.assertEqual(monthly_performance_bases(date(now.year, now.month, 1))['store_fen'], 19400)

    def test_unaffected_addon_partial_refund_after_main_in_xinan(self):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        from wallet.models import Transaction
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.test_unaffected_addon_partial_refund_after_main_refund(gift=True)
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)

    def test_unaffected_addon_full_refund_after_main_in_xinan(self):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        from wallet.models import Transaction
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.test_unaffected_addon_full_refund_after_main_refund()
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_unaffected_addon_partial_main_refund_and_settlement(self):
        self.exercise_refund_funding(double=True, addon=True)
        from datetime import date
        from django.utils import timezone
        from console.payroll_refund_performance import monthly_performance_bases
        from engagement.achievement_metrics import confirmed_service_metrics
        account = self.accounts[self.boss.pk]
        self.assertEqual(confirmed_service_metrics(account), {'amount': 20000, 'count': 1})
        now = timezone.localdate()
        monthly = monthly_performance_bases(date(now.year, now.month, 1))
        self.assertEqual(monthly['store_fen'], 20000)
        self.assertEqual(sorted((event['origin'], event['kind'], event['amount_fen'])
                                for event in monthly['events']),
                         [('ADDON', 'PAYMENT', 1000), ('ORDER', 'PAYMENT', 20000), ('ORDER', 'REFUND', -1000)])

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_preallocated_funding_releases_after_refund(self):
        self.exercise_refund_funding(double=True, addon=True, refund_preallocated=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_unaffected_addon_refund_stays_in_xinan(self):
        from stores.context import store_context, resolve_store
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        from wallet.models import Transaction
        first = resolve_store()
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_refund_funding(double=True, addon=True)
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_addon_of_terminated_provider_waits_for_completion(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_paid_addon_without_refund_settles_and_confirms_consumption(self):
        self.exercise_paid_addon_without_refund()

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_provider_paid_addon_without_refund(self):
        self.exercise_paid_addon_without_refund(single=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_addon_partial_refund_preserves_main_income(self):
        self.exercise_paid_addon_without_refund(single=True, addon_refund=500)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_addon_refund_with_funding_rolls_back_on_release_failure(self):
        self.exercise_paid_addon_without_refund(single=True, addon_refund=500, prefunded=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_addon_full_refund(self):
        self.exercise_paid_addon_without_refund(single=True, addon_refund=1000)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False)
    def test_single_addon_full_refund_with_funding_in_xinan(self):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        from wallet.models import Transaction
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_paid_addon_without_refund(single=True, addon_refund=1000, prefunded=True, gift_funded=True)
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_single_addon_full_refund_deposit_shortfall_and_retry(self):
        self.exercise_paid_addon_without_refund(single=True, addon_refund=1000, deposit_case=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False)
    def test_single_addon_full_refund_mixed_sources(self):
        self.exercise_paid_addon_without_refund(single=True, addon_refund=1000, prefunded=True, gift_funded='mixed')

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False)
    def test_double_addon_partial_refund(self):
        self.exercise_paid_addon_without_refund(addon_refund=500)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_double_addon_full_refund_with_deposit(self):
        self.exercise_paid_addon_without_refund(addon_refund=1000, deposit_case=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False)
    def test_double_addon_full_refund_with_funding_in_xinan(self):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        from wallet.models import Transaction
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_paid_addon_without_refund(addon_refund=1000, prefunded=True, gift_funded=True)
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)

    def exercise_paid_addon_without_refund(self, *, single=False, addon_refund=0, prefunded=False, concurrent_refund=False, deposit_case=False, gift_funded=False):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from club_accounts.services import get_or_create_account_for_legacy_user
        from engagement.confirmation import process_order
        from orders.models import GameCategory, Order, OrderAddonConsumption, ServiceAddonConfiguration, ServiceAddonPriceOption
        from orders.tests.factories import make_provider, make_service
        from stores.context import current_store, resolve_store, store_context
        from wallet.models import ProviderReport
        from wallet.services import get_wallet
        second = None
        if not single:
            second = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
            second_account = get_or_create_account_for_legacy_user(second)
            self.accounts[second.pk] = second_account
            second.escort_profile.account = second_account
            second.escort_profile.deposit_business_cycle = second_account.business_cycle
            second.escort_profile.save(update_fields=['account', 'deposit_business_cycle'])
        else:
            second_account = self.accounts[self.provider.pk]
        players = (self.provider, second) if second else (self.provider,)
        self.service.billing_provider_count = len(players)
        self.service.fulfillment_provider_count = len(players)
        if deposit_case:
            self.service.commission_rate = 95
        game = GameCategory.objects.create(name='普通加购资金')
        self.service.game_category = game
        self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count',
                                         'game_category', 'commission_rate'])
        for player in players:
            player.escort_profile.service_items.add(self.service)
        extra = make_service(name='普通加购', game_category=game, is_addon=True,
                             commission_rate=95 if deposit_case else None)
        config = ServiceAddonConfiguration.objects.create(service=self.service, addon=extra, enabled=True,
            condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
        if deposit_case:
            profile = (second or self.provider).escort_profile
            profile.deposit_mode, profile.deposit_paid = 'CASH', 0
            profile.save(update_fields=['deposit_mode', 'deposit_paid'])
            role = self.admin.admin_membership.roles.first()
            role.permissions = [*role.permissions, 'escort:deposit_topup', 'deposit_rule:edit']
            role.save(update_fields=['permissions'])
            self.login(self.admin)
            self.ok(self.client.put('/api/admin/config/deposit-targets', {
                'cash_target': 200, 'earnings_target': 700, 'expected_version': 0}, format='json'))
            self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': 200, 'reason': '隔离押金', 'reference': 'addon-deposit-first',
                'client_request_id': 'addon-deposit-first'}, format='multipart'))
        if prefunded:
            from orders.tests.factories import make_user
            inviter_user = make_user()
            inviter = get_or_create_account_for_legacy_user(inviter_user)
            boss_account = self.accounts[self.boss.pk]
            boss_account.inviter, boss_account.inviter_commission_rate = inviter, 20
            boss_account.save(update_fields=['inviter', 'inviter_commission_rate'])
        self.ok(self.recharge())
        self.login(self.boss)
        selection = ({'provider_ids': [self.accounts[self.provider.pk].pk, second_account.pk]}
                     if second else {'provider_id': second_account.pk})
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            **selection, 'gift_balance_amount': 1000 if prefunded and not gift_funded else 0,
            'support_contact_id': self.card.pk, 'client_request_id': 'plain-addon-order'}, format='json'))['id']
        for player in players:
            self.login(player)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        path = f'/api/orders/orders/{order_id}/addons/intents/'
        intent = self.ok(self.client.post(path, {'addon_id': extra.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': second_account.pk, 'request_id': 'plain-addon',
            'expected_amount': 1000, 'gift_balance_amount': 500 if gift_funded == 'mixed' else (0 if prefunded and not gift_funded else 1000),
            'compensation_balance_amount': 0}, format='json'))
        self.assertTrue(intent['payment_available'])
        payment_id = self.ok(self.client.post(path + str(intent['id']) + '/pay/', {}, format='json'))['payment_id']
        if prefunded:
            from uuid import uuid4
            from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
            finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
            from stores.context import current_store, resolve_store
            tenant = current_store() or resolve_store()
            if tenant.slug == 'xinan':
                from unittest.mock import patch
                with patch('stores.test_platform_pass_repayment.xinan_store', return_value=tenant):
                    finance.setUp()
            else:
                finance.setUp()
            receipt = finance.post(tenant.slug, {**finance.data, 'amount': 1000})
            self.assertEqual(receipt.status_code, 200, receipt.data)
            claim = receipt.data['data']['claim_id']
            self.assertEqual(finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=tenant.slug).status_code, 200)
            funded = finance.client.post(f'/api/admin/platform/pass-refund-repayments/{tenant.slug}/{claim}/points-allocation',
                {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 300 if gift_funded == 'mixed' else 800}, format='json',
                HTTP_AUTHORIZATION='Bearer ' + finance.token)
            self.assertEqual(funded.status_code, 200, funded.data)
        total_addon_refund = addon_refund
        if addon_refund:
            from orders.models import OrderAddonRefund, OrderAddonFundingAllocation
            role = self.admin.admin_membership.roles.first()
            role.permissions = [*role.permissions, 'support:edit']
            role.save(update_fields=['permissions'])
            self.login(self.admin)
            case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
                'affected_provider_account_ids': [second_account.pk], 'reason': '加购未完成',
                'client_request_id': 'addon-only-case'}, format='json'))
            admin_url = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/'
            before_balance = get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance
            if deposit_case:
                denied = self.client.post(admin_url, {'request_id': 'addon-only-refund', 'amount_raw': addon_refund,
                    'case_id': case['id'], 'explanation': '加购未完成部分退款'}, format='json')
                self.assertNotEqual(denied.status_code, 200, denied.data)
                self.assertEqual(denied.data['msg'], '陪玩待结算收益、钱包及押金不足，请先补足后退款')
                self.assertFalse(OrderAddonRefund.objects.filter(payment_id=payment_id).exists())
                self.assertEqual(get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance, before_balance)
                self.ok(self.client.put('/api/admin/config/deposit-targets', {
                    'cash_target': 450, 'earnings_target': 950, 'expected_version': 1}, format='json'))
                self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                    'amount': 250, 'reason': '隔离押金补足', 'reference': 'addon-deposit-second',
                    'client_request_id': 'addon-deposit-second'}, format='multipart'))
            proposal = self.ok(self.client.post(admin_url, {'request_id': 'addon-only-refund', 'amount_raw': addon_refund,
                'case_id': case['id'], 'explanation': '加购未完成部分退款'}, format='json'))
            execute = admin_url + str(proposal['id']) + '/execute/'
            self.assertNotEqual(self.client.post(execute, {}, format='json').status_code, 200)
            decision = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/{proposal["id"]}/'
            if second:
                self.login(self.provider)
                self.assertNotEqual(self.client.post(decision, {'accepted': True}, format='json').status_code, 200)
                self.login(self.boss)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
                self.login(self.admin)
                self.assertNotEqual(self.client.post(execute, {}, format='json').status_code, 200)
            for user in (self.boss, second or self.provider):
                self.login(user)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
            self.login(self.admin)
            if deposit_case:
                from wallet.models import RefundDepositDeficit
                with patch('wallet.refund_deposit.RefundDepositDeficit.objects.create', side_effect=RuntimeError('deposit write failed')):
                    with self.assertRaisesMessage(RuntimeError, 'deposit write failed'):
                        self.client.post(execute, {}, format='json')
                profile.refresh_from_db()
                self.assertEqual(profile.deposit_paid, 450)
                self.assertFalse(OrderAddonRefund.objects.filter(payment_id=payment_id).exists())
                self.assertEqual(get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance, before_balance)
                self.ok(self.client.post(execute, {}, format='json'))
                self.ok(self.client.post(execute, {}, format='json'))
                refund = OrderAddonRefund.objects.get(payment_id=payment_id)
                self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction,
                                  refund.wallet_deduction, refund.deposit_deduction), (50, 500, 0, 450))
                self.assertEqual(RefundDepositDeficit.objects.get(addon_refund=refund).remaining, 450)
                profile.refresh_from_db()
                self.assertEqual(profile.deposit_paid, 0)
                self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/resolve/',
                                         {'resolution': '加购退款完成'}, format='json'))
                if second:
                    reports = []
                    for player in players:
                        self.login(player)
                        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                            {'completion_image': flow.screenshot()}, format='multipart'))
                    for player in players:
                        self.login(player)
                        item = ProviderReport.objects.get(order_id=order_id, provider_account=self.accounts[player.pk])
                        self.ok(self.client.post(f'/api/wallet/reports/{item.pk}/images/',
                            {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
                        self.ok(self.client.post(f'/api/wallet/reports/{item.pk}/submit/', {}, format='json'))
                        self.login(self.admin)
                        self.ok(self.client.post(f'/api/admin/reports/{item.pk}/approve/', {}, format='json'))
                        item.refresh_from_db()
                        reports.append(item)
                    with store_context(current_store() or resolve_store()), patch('django.utils.timezone.now',
                            return_value=max(item.submitted_at for item in reports) + timedelta(hours=73)):
                        process_order(order_id)
                    self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
                    self.assertEqual(get_wallet(user=self.provider, account=self.accounts[self.provider.pk]).balance, 500)
                    self.assertEqual(get_wallet(user=second, account=second_account).balance, 0)
                    self.assertEqual(RefundDepositDeficit.objects.get(addon_refund=refund).remaining, 450)
                    return
                self.login(self.provider)
                self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                    {'completion_image': flow.screenshot()}, format='multipart'))
                original_report = ProviderReport.objects.get(order_id=order_id, provider_account=second_account)
                self.ok(self.client.post(f'/api/wallet/reports/{original_report.pk}/images/',
                    {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
                self.ok(self.client.post(f'/api/wallet/reports/{original_report.pk}/submit/', {}, format='json'))
                self.login(self.admin)
                self.ok(self.client.post(f'/api/admin/reports/{original_report.pk}/approve/', {}, format='json'))
                original_report.refresh_from_db()
                with store_context(current_store() or resolve_store()), patch('django.utils.timezone.now',
                        return_value=original_report.submitted_at + timedelta(hours=73)):
                    process_order(order_id)
                self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
                self.assertEqual(get_wallet(user=self.provider, account=second_account).balance, 0)
                self.service.commission_rate = 20
                self.service.save(update_fields=['commission_rate'])
                self.login(self.boss)
                def recovery_order(key):
                    return self.client.post('/api/orders/create/', {'service_id': self.service.pk,
                        'provider_id': second_account.pk, 'support_contact_id': self.card.pk,
                        'client_request_id': key}, format='json')
                recovery_id = self.ok(recovery_order('addon-deposit-recovery'))['id']
                self.assertNotEqual(recovery_order('addon-deposit-blocked').data.get('code'), 0)
                report = flow.ReleaseFlowTest.finish(self, Order.objects.get(pk=recovery_id))
                self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
                self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
                self.login(self.admin)
                self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
                report.refresh_from_db()
                with store_context(current_store() or resolve_store()), patch('django.utils.timezone.now',
                        return_value=report.submitted_at + timedelta(hours=73)):
                    process_order(recovery_id)
                    process_order(recovery_id)
                gap = RefundDepositDeficit.objects.get(addon_refund=refund)
                gap.refresh_from_db(); profile.refresh_from_db()
                self.assertEqual((gap.remaining, profile.deposit_paid), (0, 450))
                return
            if prefunded:
                from unittest.mock import patch
                with patch('orders.addon_funding_release.OrderAddonFundingRelease.objects.bulk_create',
                           side_effect=RuntimeError('addon funding failure')):
                    with self.assertRaisesMessage(RuntimeError, 'addon funding failure'):
                        self.client.post(execute, {}, format='json')
                self.assertFalse(OrderAddonRefund.objects.filter(payment_id=payment_id).exists())
            if concurrent_refund:
                from concurrent.futures import ThreadPoolExecutor
                from threading import Barrier
                from django.db import close_old_connections
                from rest_framework.test import APIClient
                from club_accounts.tokens import issue_account_tokens
                from stores.context import current_store, resolve_store, store_context
                tenant = current_store() or resolve_store()
                with store_context(tenant):
                    token = issue_account_tokens(self.accounts[self.admin.pk])[0]
                barrier = Barrier(2)
                def post_refund(_):
                    close_old_connections()
                    try:
                        with store_context(tenant):
                            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                            barrier.wait(timeout=20)
                            response = client.post(execute, {}, format='json')
                            return response.status_code, response.data
                    finally:
                        close_old_connections()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(post_refund, range(2)))
                self.assertEqual([result[0] for result in results], [200, 200], results)
            self.ok(self.client.post(execute, {}, format='json'))
            self.ok(self.client.post(execute, {}, format='json'))
            self.assertEqual(OrderAddonRefund.objects.filter(payment_id=payment_id).count(), 1)
            refund = OrderAddonRefund.objects.get(payment_id=payment_id)
            total_addon_refund = addon_refund
            if second and addon_refund < 1000:
                total_addon_refund += 100
                buyer_before_second = get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance
                extra_proposal = self.ok(self.client.post(admin_url, {'request_id': 'second-addon-refund', 'amount_raw': 100,
                    'case_id': case['id'], 'explanation': '追加退款已开放'}, format='json'))
                extra_id = extra_proposal['id']
                extra_execute = admin_url + str(extra_id) + '/execute/'
                self.assertNotEqual(self.client.post(extra_execute, {}, format='json').status_code, 200)
                self.assertEqual(OrderAddonRefund.objects.filter(payment_id=payment_id).count(), 1)
                self.assertEqual(get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance, buyer_before_second)
                for user in (self.boss, second):
                    self.login(user)
                    self.ok(self.client.post(f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/{extra_id}/',
                                             {'accepted': True}, format='json'))
                self.login(self.admin)
                extra_result = self.ok(self.client.post(extra_execute, {}, format='json'))
                self.assertEqual(self.ok(self.client.post(extra_execute, {}, format='json')), extra_result)
                refunds = list(OrderAddonRefund.objects.filter(payment_id=payment_id))
                self.assertEqual(len(refunds), 2)
                self.assertEqual(sum(row.amount for row in refunds), total_addon_refund)
                self.assertEqual(get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance,
                                 buyer_before_second + 100)
            self.assertEqual((refund.amount, refund.addon_pending_deduction, refund.main_pending_deduction),
                             (addon_refund, min(addon_refund, 800), max(addon_refund - 800, 0)))
            expected_sources = ({'PREPAID': 500, 'GIFT': 500 + (total_addon_refund - addon_refund)} if gift_funded == 'mixed' else
                                {'PREPAID' if prefunded and not gift_funded else 'GIFT': total_addon_refund})
            self.assertEqual(dict(OrderAddonFundingAllocation.objects.filter(payment_id=payment_id)
                                  .values_list('kind', 'refunded_amount')), expected_sources)
            if gift_funded:
                from orders.models import OrderAddonFundingReleaseRun
                release = OrderAddonFundingReleaseRun.objects.get(refund=refund)
                allocated = 300 if gift_funded == 'mixed' else 800
                self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (allocated, 0, allocated))
            self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/resolve/',
                                     {'resolution': '加购退款完成'}, format='json'))
            # A separate main-order case may follow the add-on refund.
            from stores.db import transaction
            with transaction.atomic():
                reopened = self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
                    'affected_provider_account_ids': [second_account.pk], 'reason': '主单后续退款',
                    'client_request_id': 'after-addon-main-case'}, format='json')
                main_case = self.ok(reopened)
                preview = self.ok(self.client.get(f'/api/admin/aftersales/{main_case["id"]}/negotiation-preview/',
                                                  {'refund_units': 1000}))
                buyer_before_main = get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance
                self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
                main_proposal = self.ok(self.client.post(f'/api/admin/aftersales/{main_case["id"]}/negotiation-proposal/', {
                    'request_id': 'after-addon-main-plan', 'expected_version': 0, 'refund_units': 1000,
                    'provider_terms': [{'account_id': row['account_id'], 'retained_units': row['retained_units']}
                                       for row in preview['provider_terms']],
                    'explanation': '主单追加退款'}, format='json'))
                main_id = main_proposal['id']
                for user in (self.boss, second or self.provider):
                    self.login(user)
                    self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                        {'proposal_id': main_id, 'accepted': True}, format='json'))
                self.login(self.admin)
                execution_url = f'/api/admin/aftersales/{main_case["id"]}/negotiation-execution/'
                payload = {'proposal_id': main_id, 'client_request_id': 'after-addon-main-execute'}
                main_result = self.ok(self.client.post(execution_url, payload, format='json'))
                self.assertEqual(self.ok(self.client.post(execution_url, payload, format='json')), main_result)
                order = Order.objects.get(pk=order_id)
                self.assertEqual(order.refund_records.count(), 1)
                self.assertEqual(order.refund_records.get().amount, 1000)
                self.assertEqual(get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance,
                                 buyer_before_main + 1000)
                transaction.set_rollback(True)
        for player in players:
            self.login(player)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                     {'completion_image': flow.screenshot()}, format='multipart'))
        reports = []
        for player in players:
            report = ProviderReport.objects.get(order_id=order_id, provider_account=self.accounts[player.pk])
            self.login(player)
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
            report.refresh_from_db()
            reports.append(report)
        with store_context(current_store() or resolve_store()), patch('django.utils.timezone.now',
                return_value=max(report.submitted_at for report in reports) + timedelta(hours=73)):
            process_order(order_id)
            process_order(order_id)
        self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
        self.assertEqual(get_wallet(user=self.provider, account=self.accounts[self.provider.pk]).balance,
                         8000 if second else 8800 - total_addon_refund)
        if second:
            self.assertEqual(get_wallet(user=second, account=second_account).balance, 8800 - total_addon_refund)
        consumption = OrderAddonConsumption.objects.get(payment_id=payment_id)
        self.assertEqual((consumption.paid_units, consumption.refunded_units), (1000, total_addon_refund))
        if prefunded:
            self.assertEqual(get_wallet(user=inviter_user, account=inviter).balance, 4000 if second else 2000)
        if addon_refund:
            from datetime import date
            from console.payroll_refund_performance import monthly_performance_bases
            from engagement.achievement_metrics import confirmed_service_metrics
            self.assertEqual(confirmed_service_metrics(self.accounts[self.boss.pk]), {'amount': (21000 if second else 11000) - total_addon_refund, 'count': 1})
            now = timezone.localdate()
            monthly = monthly_performance_bases(date(now.year, now.month, 1))
            self.assertEqual(monthly['store_fen'], (21000 if second else 11000) - total_addon_refund)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_provider_partial_refund_requires_confirmed_addon_completion(self):
        self.exercise_refund_funding(addon=True, addon_on_affected=True, completion=True)
        from datetime import date
        from django.utils import timezone
        from console.payroll_refund_performance import monthly_performance_bases
        from engagement.achievement_metrics import confirmed_service_metrics
        self.assertEqual(confirmed_service_metrics(self.accounts[self.boss.pk]), {'amount': 10000, 'count': 1})
        now = timezone.localdate()
        monthly = monthly_performance_bases(date(now.year, now.month, 1))
        self.assertEqual(monthly['store_fen'], 10000)
        self.assertEqual(sorted((event['origin'], event['kind'], event['amount_fen']) for event in monthly['events']),
                         [('ADDON', 'PAYMENT', 1000), ('ORDER', 'PAYMENT', 10000), ('ORDER', 'REFUND', -1000)])

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_refund_and_completion_in_xinan(self):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        from wallet.models import Transaction
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_refund_funding(addon=True, addon_on_affected=True, completion=True)
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_refund_releases_preallocated_funding(self):
        self.exercise_refund_funding(addon=True, addon_on_affected=True, completion=True, refund_preallocated=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_addon_partial_refund_with_funding_in_xinan(self):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        from wallet.models import Transaction
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_paid_addon_without_refund(single=True, addon_refund=500, prefunded=True)
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)


@override_settings(**prior.OrdinaryRefundWithoutAddonFundingTest._overridden_settings)
class OrdinaryAddonProductionRefundMySQLRaceTest(APITransactionTestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    exercise_refund_funding = prior.OrdinaryRefundWithoutAddonFundingTest.exercise_refund_funding
    exercise_paid_addon_without_refund = OrdinaryAddonProductionRefundTest.exercise_paid_addon_without_refund

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_concurrent_addon_refund_after_main_refund(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        self.exercise_refund_funding(double=True, addon=True, preallocated=True,
            refund_preallocated=True, addon_after_main=True, concurrent_addon_refund=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_concurrent_addon_partial_refund_after_main_refund(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        self.exercise_refund_funding(double=True, addon=True, preallocated=True, refund_preallocated=True,
            addon_after_main=True, addon_after_partial=True, addon_after_partial_gift=True, concurrent_addon_refund=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_concurrent_single_addon_partial_refund(self):
        from django.db import connection
        from unittest.mock import patch
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_paid_addon_without_refund(single=True, addon_refund=500, prefunded=True, concurrent_refund=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False)
    def test_concurrent_single_addon_full_refund(self):
        from django.db import connection
        from unittest.mock import patch
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_paid_addon_without_refund(single=True, addon_refund=1000, prefunded=True, concurrent_refund=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False)
    def test_concurrent_double_addon_full_refund(self):
        from django.db import connection
        from unittest.mock import patch
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_paid_addon_without_refund(addon_refund=1000, prefunded=True, concurrent_refund=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_concurrent_refund_releases_prefunding_once(self):
        from django.db import connection
        from unittest.mock import patch
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_refund_funding(double=True, addon=True, refund_preallocated=True, concurrent_refund=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
        ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_concurrent_single_addon_completion_confirmation(self):
        from django.db import connection
        from unittest.mock import patch
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_refund_funding(addon=True, addon_on_affected=True, completion=True, concurrent_completion=True)
