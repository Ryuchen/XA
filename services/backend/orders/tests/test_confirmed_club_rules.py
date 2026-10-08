from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import transaction
from django.db import close_old_connections, connections
from django.db.models import Sum
from django.test import override_settings
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APITestCase

from orders.models import Order
from orders.pricing import club_provider_specs
from orders.services import sync_single_provider_share
from orders.settlement import build_provider_shares
from orders.tests.factories import make_provider, make_service
from orders.tests.test_release_flow import ReleaseFlowTest, screenshot
from orders.tests.test_report_settlement_concurrency import ReportSettlementConcurrencyTest
from users.models import EscortLevel
from wallet.income import release_due_income
from wallet.models import IncomeHold, ProviderReport, SystemConfig, Transaction, Wallet
from club_accounts.services import get_or_create_account_for_legacy_user


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False)
class ConfirmedClubRulesTest(APITestCase):
    login = ReleaseFlowTest.login
    ok = ReleaseFlowTest.ok
    recharge = ReleaseFlowTest.recharge
    new_order = ReleaseFlowTest.new_order
    finish = ReleaseFlowTest.finish
    settle_after_window = ReleaseFlowTest.settle_after_window

    def setUp(self):
        ReleaseFlowTest.setUp(self)
        SystemConfig.objects.create(key='club_income_rules_enabled', value='1')
        profile = self.provider.escort_profile
        profile.deposit_mode = 'EARNINGS'
        profile.save(update_fields=['deposit_mode'])
        self.service.commission_policy = 'COMPANION'
        self.service.save(update_fields=['commission_policy'])

    def approve(self, report):
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        result = self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.assertFalse(IncomeHold.objects.exists())
        self.settle_after_window()
        return result

    def test_approved_net_income_offsets_deposit_then_holds_72_hours_once(self):
        order = self.new_order()
        report = self.finish(order)
        self.assertFalse(IncomeHold.objects.exists())
        self.approve(report)
        hold = IncomeHold.objects.get()
        self.assertEqual((hold.deposit_amount, hold.amount), (4800, 3200))
        self.assertAlmostEqual((hold.release_at - hold.created_at).total_seconds(), 72 * 3600, delta=1)
        profile = self.provider.escort_profile
        profile.refresh_from_db()
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual((profile.deposit_paid, wallet.balance, wallet.frozen_amount), (4800, 0, 3200))
        self.assertEqual(Transaction.objects.filter(order=order).aggregate(total=Sum('amount'))['total'] + hold.amount, 0)
        self.assertNotEqual(self.client.post(f'/api/admin/reports/{report.pk}/approve/').data['code'], 0)
        self.assertEqual(IncomeHold.objects.count(), 1)
        self.assertEqual(release_due_income(now=hold.release_at - timedelta(microseconds=1)), 0)
        self.assertEqual(release_due_income(now=hold.release_at), 1)
        self.assertEqual(release_due_income(now=hold.release_at + timedelta(days=1)), 0)
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.frozen_amount), (3200, 0))
        self.assertEqual(Transaction.objects.filter(order=order).aggregate(total=Sum('amount'))['total'], 0)

    def test_offset_caps_at_400_and_surplus_is_held(self):
        profile = self.provider.escort_profile
        profile.deposit_paid = 39000
        profile.save(update_fields=['deposit_paid'])
        self.approve(self.finish(self.new_order()))
        hold = IncomeHold.objects.get()
        profile.refresh_from_db()
        self.assertEqual((profile.deposit_paid, hold.deposit_amount, hold.amount), (40000, 1000, 7000))

    def test_cash_requires_200_and_earnings_still_requires_certification(self):
        profile = self.provider.escort_profile
        self.assertEqual(profile.order_acceptance_error(self.service), '')
        profile.is_verified = False
        self.assertIn('认证', profile.order_acceptance_error(self.service))
        profile.is_verified = True
        profile.deposit_mode = 'CASH'
        profile.deposit_paid = 19999
        self.assertIn('押金', profile.order_acceptance_error(self.service))
        profile.deposit_paid = 20000
        self.assertEqual(profile.order_acceptance_error(self.service), '')
        minimum = EscortLevel.objects.create(name='大师', sort_order=1, commission_rate=22)
        disabled = EscortLevel.objects.create(name='旧测试等级', sort_order=99, commission_rate=20, is_active=False)
        self.service.required_level = minimum
        profile.level = disabled
        self.assertIn('档位', profile.order_acceptance_error(self.service))

    def test_experience_boundary_and_double_fixed_cut(self):
        self.service.commission_policy = 'EXPERIENCE'
        for amount, cut in [(6800, 300), (9999, 300), (10000, 1000), (18800, 1880)]:
            shares = build_provider_shares(amount, club_provider_specs(self.service, [None, None], amount))
            self.assertEqual(amount - sum(row.provider_income for row in shares), cut)
        self.service.price = 6800
        self.service.save(update_fields=['commission_policy', 'price'])
        order = self.new_order()
        self.assertEqual((order.provider_income, order.shop_income), (6500, 300))
        self.assertEqual(order.providers.get().commission_fixed, 300)

    def test_regular_picks_actual_provider_grade_and_keeps_order_policy(self):
        level = EscortLevel.objects.create(name='明星', sort_order=5, commission_rate=15)
        profile = self.provider.escort_profile
        profile.level = level
        profile.save(update_fields=['level'])
        self.service.commission_policy = 'REGULAR'
        self.service.save(update_fields=['commission_policy'])
        order = self.new_order()
        self.assertEqual((order.commission_rate, order.provider_income), (15, 8500))
        self.service.commission_policy = 'COMPANION'
        self.service.save(update_fields=['commission_policy'])
        level.commission_rate = 18
        level.save(update_fields=['commission_rate'])
        order.in_service_at = order.created_at
        with transaction.atomic():
            sync_single_provider_share(order)
        order.refresh_from_db()
        self.assertEqual((order.commission_policy, order.commission_rate), ('REGULAR', 15))

    def test_withdraw_checks_available_balance_strictly_and_any_positive_amount(self):
        self.login(self.provider)
        wallet = Wallet.objects.get(user=self.provider)
        wallet.balance = 5000
        wallet.frozen_amount = 100000
        wallet.save(update_fields=['balance', 'frozen_amount'])
        payload = {'amount': 100, 'payee_method': 'WECHAT', 'payee_account': 'test-wechat', 'payee_name': '张三', 'client_request_id': 'club-withdraw'}
        self.assertEqual(self.client.get('/api/wallet/withdraw/').data['data']['can_withdraw'], False)
        self.assertEqual(self.client.post('/api/wallet/withdraw/', payload, format='json').data['code'], 400)
        wallet.balance = 5001
        wallet.save(update_fields=['balance'])
        data = self.ok(self.client.post('/api/wallet/withdraw/', payload, format='json'))
        self.assertEqual((data['request']['tax_amount'], data['request']['actual_amount']), (2, 98))
        self.assertEqual(data['balance'], 4901)

    def test_per_person_double_price_requires_two_providers_and_cuts_twenty_percent(self):
        self.service.price = 8000
        self.service.billing_provider_count = 2
        self.service.purchase_mode = 'CONSULT'
        self.service.save(update_fields=['price', 'billing_provider_count', 'purchase_mode'])
        second = make_provider(deposit_mode='EARNINGS')
        second_account = get_or_create_account_for_legacy_user(second)
        profile = second.escort_profile
        profile.account = second_account
        profile.save(update_fields=['account'])
        self.ok(self.recharge())
        quote = self.ok(self.client.get('/api/admin/orders/dispatch-quote/', {'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk, 'game_rounds': 1}))
        self.assertEqual(quote['amount'], 16000)
        result = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'escort_mode': 'DOUBLE', 'expected_amount': 16000, 'client_request_id': 'double-club',
            'providers': [{'provider_id': self.accounts[self.provider.pk].pk, 'commission_rate': 0},
                          {'provider_id': second_account.pk, 'commission_rate': 100}],
        }, format='json'))
        order = Order.objects.get(pk=result['id'])
        self.assertEqual((order.amount, order.provider_income, order.shop_income), (16000, 12800, 3200))
        self.assertEqual(list(order.providers.values_list('provider_income', flat=True)), [6400, 6400])
        self.login(self.boss)
        self.assertEqual(self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'client_request_id': 'single-on-double'}, format='json').data['code'], 400)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
@skipUnlessDBFeature('has_select_for_update')
class ConfirmedClubConcurrencyTest(TransactionTestCase):
    race = ReportSettlementConcurrencyTest.race

    def setUp(self):
        ReportSettlementConcurrencyTest.setUp(self)
        SystemConfig.objects.create(key='club_income_rules_enabled', value='1')
        profile = self.provider.escort_profile
        profile.deposit_mode = 'EARNINGS'
        profile.save(update_fields=['deposit_mode'])
        from django.utils import timezone
        self.order.completed_at = timezone.now()-timedelta(hours=73)
        self.order.save(update_fields=['completed_at'])
        self.order.income_hold_hours = 72
        self.order.save(update_fields=['income_hold_hours'])

    def test_duplicate_approval_and_simultaneous_unlock_only_move_money_once(self):
        action = (f'/api/admin/reports/{self.report.pk}/approve/', {})
        self.race([action, action])
        hold = IncomeHold.objects.get()
        self.assertEqual((hold.amount, hold.deposit_amount), (3200, 4800))
        barrier = Barrier(2)

        def unlock():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return release_due_income(now=hold.release_at)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: unlock(), range(2), timeout=20))
        self.assertEqual(sorted(results), [0, 1])
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual((wallet.balance, wallet.frozen_amount), (3200, 0))
        self.assertEqual(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').count(), 1)
