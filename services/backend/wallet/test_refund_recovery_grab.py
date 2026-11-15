from datetime import timedelta

from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import Order
from orders.state_machine import transition
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_completed_order, make_order
from wallet.models import RefundDepositRecoveryPermit, get_platform_wallet
from wallet.services import get_wallet


@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class RefundRecoveryGrabTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def pool(self):
        return make_order(self.boss, customer_account=self.accounts[self.boss.pk], service=self.service,
            status='PENDING', payment_status='PAID', dispatch_route='PUBLIC', requires_boss_selection=False,
            pool_entered_at=timezone.now()-timedelta(hours=1))

    def setUpGap(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        self.account = self.accounts[self.provider.pk]
        self.profile = self.provider.escort_profile
        self.profile.deposit_mode = 'EARNINGS'; self.profile.deposit_paid = 10000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.platform = get_platform_wallet()
        self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID')
        return RefundDepositDeficitTest.deficit(self, 200)

    def grab(self, order, key):
        self.login(self.provider)
        return self.client.post(f'/api/orders/orders/{order.pk}/grab/',
                                {'team_size':1, 'client_request_id':key}, format='json')

    def add_gap_for(self, user):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        names = ('provider', 'account', 'profile', 'wallet', 'order', 'platform')
        previous = {name: getattr(self, name, None) for name in names}
        self.provider = user
        self.account = self.accounts[user.pk]
        self.profile = user.escort_profile
        self.profile.deposit_mode = 'EARNINGS'; self.profile.deposit_paid = 10000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(account=self.account, user=user)
        self.platform = get_platform_wallet()
        if self.platform.balance < 10000:
            self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, user, amount=10000,
                                          provider_account=self.account, payment_status='PAID')
        result = RefundDepositDeficitTest.deficit(self, 200)
        for name, value in previous.items():
            setattr(self, name, value)
        return result

    def test_only_one_extra_order_and_prestart_cancellation_restores_permit(self):
        first, second = self.pool(), self.pool()
        self.setUpGap()
        self.ok(self.grab(first, 'recovery-first'))
        permit = RefundDepositRecoveryPermit.objects.get(order=first)
        self.assertIsNone(permit.closed_at)
        self.assertNotEqual(self.grab(second, 'recovery-second-blocked').data['code'], 0)
        transition(first.pk, Order.Status.CANCELLED, reason='隔离测试未开始取消')
        permit.refresh_from_db(); self.assertEqual(permit.close_reason, 'CANCELLED_UNSTARTED')
        self.ok(self.grab(second, 'recovery-second'))
        self.assertEqual(RefundDepositRecoveryPermit.objects.filter(account=self.account, closed_at__isnull=True).count(), 1)

    def test_existing_order_partial_income_does_not_release_permit_but_recovery_income_does(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        recovery, next_order = self.pool(), self.pool()
        gap = self.setUpGap()
        self.ok(self.grab(recovery, 'recovery-settlement-first'))
        RefundDepositDeficitTest.income(self, 50)
        permit = RefundDepositRecoveryPermit.objects.get(order=recovery)
        permit.refresh_from_db(); self.assertIsNone(permit.closed_at)
        self.assertNotEqual(self.grab(next_order, 'recovery-settlement-blocked').data['code'], 0)
        Order.objects.filter(pk=recovery.pk).update(status='COMPLETED', in_service_at=timezone.now())
        self.order = Order.objects.get(pk=recovery.pk)
        RefundDepositDeficitTest.income(self, 50)
        permit.refresh_from_db(); gap.refresh_from_db()
        self.assertEqual((permit.close_reason, gap.remaining), ('INCOME_SETTLED', 100))
        self.assertIsNotNone(permit.income_application_id)
        self.ok(self.grab(next_order, 'recovery-settlement-next'))

    def test_recovery_order_obeys_normal_concurrent_limit(self):
        pool = self.pool()
        self.setUpGap()
        active = [make_order(self.boss, service=self.service, provider=self.provider,
            provider_account=self.account, status='IN_SERVICE', payment_status='PAID') for _ in range(3)]
        blocked = self.grab(pool, 'recovery-at-capacity')
        self.assertNotEqual(blocked.data['code'], 0)
        self.assertFalse(RefundDepositRecoveryPermit.objects.exists())
        Order.objects.filter(pk=active[0].pk).update(status='COMPLETED')
        self.ok(self.grab(pool, 'recovery-after-capacity'))

    def test_cash_deposit_shortfall_must_be_fully_explained_by_refund_deficit(self):
        pool = self.pool()
        self.setUpGap()
        self.profile.deposit_mode = 'CASH'
        self.profile.deposit_paid = self.profile.effective_deposit_required - 201
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.assertNotEqual(self.grab(pool, 'recovery-other-shortfall').data['code'], 0)
        self.profile.deposit_paid += 1; self.profile.save(update_fields=['deposit_paid'])
        self.ok(self.grab(pool, 'recovery-refund-only'))

    def test_started_and_cancelled_order_keeps_permit_until_income_settles(self):
        first, second = self.pool(), self.pool()
        self.setUpGap()
        self.ok(self.grab(first, 'recovery-started'))
        transition(first.pk, Order.Status.IN_SERVICE, reason='隔离测试开始')
        transition(first.pk, Order.Status.CANCELLED, reason='隔离测试履约后终止')
        self.assertTrue(RefundDepositRecoveryPermit.objects.filter(order=first, closed_at__isnull=True).exists())
        self.assertNotEqual(self.grab(second, 'recovery-started-blocked').data['code'], 0)

    def test_failed_permit_write_rolls_back_assignment(self):
        from unittest.mock import patch
        first = self.pool()
        self.setUpGap()
        with patch('wallet.refund_recovery_order.reserve', side_effect=ValueError('补差记录写入失败')):
            self.assertNotEqual(self.grab(first, 'recovery-permit-failure').data['code'], 0)
        first.refresh_from_db()
        self.assertEqual((first.status, first.provider_account_id), ('PENDING', None))
        self.assertFalse(first.providers.exists())
        self.assertFalse(RefundDepositRecoveryPermit.objects.exists())

    def test_disabling_new_grabs_keeps_tracking_existing_permit_and_gap(self):
        first, second = self.pool(), self.pool()
        self.setUpGap()
        self.ok(self.grab(first, 'recovery-before-disable'))
        with override_settings(REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=False,
                               REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=True,
                               REFUND_DEPOSIT_DEFICIT_ENABLED=False):
            self.assertNotEqual(self.grab(second, 'recovery-after-disable').data['code'], 0)
            transition(first.pk, Order.Status.CANCELLED, reason='关闭新接单后取消')
            permit = RefundDepositRecoveryPermit.objects.get(order=first)
            self.assertEqual(permit.close_reason, 'CANCELLED_UNSTARTED')
            self.assertNotEqual(self.grab(second, 'recovery-still-protected').data['code'], 0)

    def test_disabled_new_grabs_still_close_permit_after_income_repayment(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        first = self.pool()
        self.setUpGap()
        self.ok(self.grab(first, 'recovery-before-disable-income'))
        with override_settings(REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=False,
                               REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=True):
            RefundDepositDeficitTest.income(self, 200)
        permit = RefundDepositRecoveryPermit.objects.get(order=first)
        self.assertEqual(permit.close_reason, 'GAP_PAID')

    def test_another_income_fully_repaying_gap_closes_permit(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        recovery, next_order = self.pool(), self.pool()
        gap = self.setUpGap()
        self.ok(self.grab(recovery, 'recovery-gap-paid-first'))
        RefundDepositDeficitTest.income(self, 200)
        permit = RefundDepositRecoveryPermit.objects.get(order=recovery)
        permit.refresh_from_db(); gap.refresh_from_db()
        self.assertEqual((permit.close_reason, gap.remaining), ('GAP_PAID', 0))
        self.ok(self.grab(next_order, 'recovery-gap-paid-next'))

    def test_boss_specified_order_reserves_the_recovery_permit(self):
        self.setUpGap()
        self.ok(self.recharge())
        self.login(self.boss)
        payload = {'service_id':self.service.pk, 'provider_id':self.account.pk,
                   'client_request_id':'boss-recovery-assignment'}
        data = self.ok(self.client.post('/api/orders/create/', payload, format='json'))
        permit = RefundDepositRecoveryPermit.objects.get(order_id=data['id'])
        self.assertEqual((permit.account_id, permit.closed_at), (self.account.pk, None))
        payload['client_request_id'] = 'boss-recovery-assignment-second'
        self.assertNotEqual(self.client.post('/api/orders/create/', payload, format='json').data['code'], 0)

    @override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_prestart_handoff_releases_only_the_replaced_provider_permit(self):
        from orders.tests.test_prestart_handoff import PrestartHandoffTest
        self.setUpGap()
        replacement = PrestartHandoffTest.replacement(self)
        self.ok(self.recharge())
        self.login(self.boss)
        payload = {'service_id':self.service.pk, 'provider_id':self.account.pk,
                   'client_request_id':'boss-recovery-transfer'}
        first = self.ok(self.client.post('/api/orders/create/', payload, format='json'))
        permit = RefundDepositRecoveryPermit.objects.get(order_id=first['id'], account=self.account)
        self.ok(PrestartHandoffTest.transfer(self, first['id'], self.provider, replacement))
        permit.refresh_from_db()
        self.assertEqual(permit.close_reason, 'TRANSFERRED_UNSTARTED')
        self.login(self.boss)
        payload['client_request_id'] = 'boss-recovery-after-transfer'
        second = self.ok(self.client.post('/api/orders/create/', payload, format='json'))
        self.assertTrue(RefundDepositRecoveryPermit.objects.filter(order_id=second['id'],
            account=self.account, closed_at__isnull=True).exists())

    @override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_handoff_transfers_gap_opportunity_atomically_and_rolls_back_failure(self):
        from unittest.mock import patch
        from orders.tests.test_prestart_handoff import PrestartHandoffTest
        self.setUpGap()
        replacement = PrestartHandoffTest.replacement(self)
        self.add_gap_for(replacement)
        self.ok(self.recharge())
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.account.pk, 'client_request_id':'recovery-handoff-both'}, format='json'))
        order = Order.objects.get(pk=created['id'])
        old_permit = RefundDepositRecoveryPermit.objects.get(order=order, account=self.account)
        wallet = get_wallet(account=self.accounts[self.boss.pk], user=self.boss)
        before = (wallet.balance, order.amount, order.provider_account_id)
        with patch('wallet.refund_recovery_order.reserve', side_effect=ValueError('新机会写入失败')):
            response = PrestartHandoffTest.transfer(self, order.pk, self.provider, replacement)
        self.assertNotEqual(response.data['code'], 0)
        order.refresh_from_db(); wallet.refresh_from_db(); old_permit.refresh_from_db()
        self.assertEqual((wallet.balance, order.amount, order.provider_account_id), before)
        self.assertIsNone(old_permit.closed_at)
        self.assertFalse(RefundDepositRecoveryPermit.objects.filter(account=self.accounts[replacement.pk]).exists())
        self.ok(PrestartHandoffTest.transfer(self, order.pk, self.provider, replacement))
        order.refresh_from_db(); old_permit.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual(old_permit.close_reason, 'TRANSFERRED_UNSTARTED')
        self.assertEqual((wallet.balance, order.amount), before[:2])
        self.assertTrue(RefundDepositRecoveryPermit.objects.filter(order=order,
            account=self.accounts[replacement.pk], closed_at__isnull=True).exists())
        self.login(self.boss)
        result = self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[replacement.pk].pk, 'client_request_id':'recovery-handoff-next'}, format='json')
        self.assertNotEqual(result.data['code'], 0)
        self.ok(PrestartHandoffTest.transfer(self, order.pk, replacement, self.provider))
        self.assertEqual(RefundDepositRecoveryPermit.objects.filter(order=order, account=self.account).count(), 2)
        self.assertEqual(RefundDepositRecoveryPermit.objects.filter(order=order, account=self.account,
            closed_at__isnull=True).count(), 1)
        self.assertEqual(RefundDepositRecoveryPermit.objects.get(order=order,
            account=self.accounts[replacement.pk]).close_reason, 'TRANSFERRED_UNSTARTED')

    @override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_handoff_cannot_give_second_recovery_order_to_incoming_provider(self):
        from orders.tests.test_prestart_handoff import PrestartHandoffTest
        self.setUpGap()
        replacement = PrestartHandoffTest.replacement(self)
        self.add_gap_for(replacement)
        self.ok(self.recharge())
        self.login(self.boss)
        def assign(account, key):
            return self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
                'provider_id':account.pk, 'client_request_id':key}, format='json'))
        first = assign(self.account, 'handoff-existing-original')
        second = assign(self.accounts[replacement.pk], 'handoff-existing-incoming')
        old_permit = RefundDepositRecoveryPermit.objects.get(order_id=first['id'], account=self.account)
        result = PrestartHandoffTest.transfer(self, first['id'], self.provider, replacement)
        self.assertNotEqual(result.data['code'], 0)
        old_permit.refresh_from_db()
        self.assertIsNone(old_permit.closed_at)
        self.assertEqual(Order.objects.get(pk=first['id']).provider_account_id, self.account.pk)
        self.assertTrue(RefundDepositRecoveryPermit.objects.filter(order_id=second['id'],
            account=self.accounts[replacement.pk], closed_at__isnull=True).exists())

    @override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_appointment_handoff_with_gap_requires_current_capacity(self):
        from orders.tests.test_prestart_handoff import PrestartHandoffTest
        from orders.tests.test_assigned_order_reservation_guard import AssignedOrderReservationGuardTest
        replacement = PrestartHandoffTest.replacement(self)
        self.provider.escort_profile.deposit_mode = 'EARNINGS'
        self.provider.escort_profile.save(update_fields=['deposit_mode'])
        self.ok(self.recharge())
        start = (timezone.now()+timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        _, order_id = AssignedOrderReservationGuardTest.book(self, self.provider, start, 'recovery-booking')
        self.add_gap_for(replacement)
        new_account = self.accounts[replacement.pk]
        active = [make_order(self.boss, service=self.service, provider=replacement,
            provider_account=new_account, status='IN_SERVICE', payment_status='PAID') for _ in range(3)]
        result = PrestartHandoffTest.transfer(self, order_id, self.provider, replacement)
        self.assertNotEqual(result.data['code'], 0)
        self.assertFalse(RefundDepositRecoveryPermit.objects.filter(account=new_account).exists())
        self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, self.accounts[self.provider.pk].pk)
        Order.objects.filter(pk=active[0].pk).update(status='COMPLETED')
        self.ok(PrestartHandoffTest.transfer(self, order_id, self.provider, replacement))
        self.assertTrue(RefundDepositRecoveryPermit.objects.filter(order_id=order_id,
            account=new_account, closed_at__isnull=True).exists())

    @override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                       ORIGINAL_ATTRIBUTION_ENABLED=True)
    def test_team_handoff_preserves_started_teammate_and_moves_other_permit(self):
        from orders.tests.test_prestart_handoff import PrestartHandoffTest
        self.setUpGap()
        teammate = PrestartHandoffTest.replacement(self, name='尚未入队陪玩')
        replacement = PrestartHandoffTest.replacement(self, name='替补陪玩')
        self.add_gap_for(teammate); self.add_gap_for(replacement)
        self.service.billing_provider_count = 2; self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_ids':[self.account.pk, self.accounts[teammate.pk].pk], 'dispatch_mode':'BOSS_SELECT',
            'client_request_id':'recovery-team-before-handoff'}, format='json'))
        order = Order.objects.get(pk=created['id'])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                                  {'entry_image':flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'GRABBED')
        self.ok(PrestartHandoffTest.transfer(self, order.pk, teammate, replacement))
        permits = {row.account_id:row for row in RefundDepositRecoveryPermit.objects.filter(order=order)}
        self.assertIsNone(permits[self.account.pk].closed_at)
        self.assertEqual(permits[self.accounts[teammate.pk].pk].close_reason, 'TRANSFERRED_UNSTARTED')
        self.assertIsNone(permits[self.accounts[replacement.pk].pk].closed_at)

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True)
    def test_boss_double_assignment_reserves_each_provider_and_rolls_back_together(self):
        from unittest.mock import patch
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_provider
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        from wallet import refund_recovery_order
        self.setUpGap()
        first_account = self.account
        original = (self.provider, self.account, self.profile, self.wallet, self.order)
        second = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
        second_account = get_or_create_account_for_legacy_user(second)
        self.provider, self.account, self.profile = second, second_account, second.escort_profile
        self.profile.account = second_account; self.profile.save(update_fields=['account'])
        self.wallet = get_wallet(account=second_account, user=second)
        self.order = make_completed_order(self.boss, second, amount=10000,
                                          provider_account=second_account, payment_status='PAID')
        RefundDepositDeficitTest.deficit(self, 200)
        self.provider, self.account, self.profile, self.wallet, self.order = original
        self.service.billing_provider_count = 2; self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        payload = {'service_id':self.service.pk, 'provider_ids':[first_account.pk, second_account.pk],
                   'dispatch_mode':'BOSS_SELECT', 'client_request_id':'boss-double-recovery-fail'}
        original_reserve = refund_recovery_order.reserve
        def fail_second(profile, order):
            if profile.account_id == second_account.pk:
                raise ValueError('隔离测试第二人机会写入失败')
            return original_reserve(profile, order)
        wallet = get_wallet(account=self.accounts[self.boss.pk], user=self.boss)
        before = wallet.balance
        with patch('wallet.refund_recovery_order.reserve', side_effect=fail_second):
            result = self.client.post('/api/orders/create/', payload, format='json')
        self.assertNotEqual(result.data['code'], 0)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance, before)
        self.assertFalse(RefundDepositRecoveryPermit.objects.exists())
        self.assertFalse(Order.objects.filter(customer_account=self.accounts[self.boss.pk], status='GRABBED').exists())
        payload['client_request_id'] = 'boss-double-recovery-success'
        data = self.ok(self.client.post('/api/orders/create/', payload, format='json'))
        self.assertEqual(set(RefundDepositRecoveryPermit.objects.filter(order_id=data['id'])
                             .values_list('account_id', flat=True)), {first_account.pk, second_account.pk})
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{data['id']}/start/",
                                  {'entry_image':flow.screenshot()}, format='multipart'))
        order = Order.objects.get(pk=data['id'])
        self.assertEqual(order.status, 'GRABBED')  # the other participant has not started yet
        from orders.models import OrderParticipationSegment
        from wallet.models import ProviderReport
        from wallet.refund_recovery_order import cancel_unstarted
        from stores.db import transaction
        with transaction.atomic():
            cancel_unstarted(order, account_ids=[first_account.pk])
        self.assertTrue(RefundDepositRecoveryPermit.objects.filter(order=order,
            account=first_account, closed_at__isnull=True).exists())
        self.assertTrue(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account=first_account, started_at__isnull=False).exists())
        ProviderReport.objects.filter(order=order, provider_account=first_account).update(entry_image='')
        transition(order.pk, Order.Status.CANCELLED, reason='隔离测试双陪部分入队后取消')
        first_permit = RefundDepositRecoveryPermit.objects.get(order=order, account=first_account)
        second_permit = RefundDepositRecoveryPermit.objects.get(order=order, account=second_account)
        self.assertIsNone(first_permit.closed_at)
        self.assertEqual(second_permit.close_reason, 'CANCELLED_UNSTARTED')

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_orders_cannot_both_get_one_recovery_permit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from rest_framework.test import APIClient
        from club_accounts.tokens import issue_account_tokens
        first, second = self.pool(), self.pool()
        self.setUpGap()
        token = issue_account_tokens(self.account)[0]
        barrier = Barrier(2)
        def attempt(order):
            close_old_connections()
            try:
                client = APIClient(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post(f'/api/orders/orders/{order.pk}/grab/',
                    {'team_size':1, 'client_request_id':f'recovery-concurrent-{order.pk}'}, format='json').data['code']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (first, second)))
        self.assertEqual(sum(code == 0 for code in results), 1)
        self.assertEqual(RefundDepositRecoveryPermit.objects.filter(account=self.account, closed_at__isnull=True).count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    @override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_concurrent_handoffs_cannot_reuse_incoming_recovery_permit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from rest_framework.test import APIClient
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.test_prestart_handoff import PrestartHandoffTest
        replacement = PrestartHandoffTest.replacement(self)
        self.add_gap_for(replacement)
        self.provider.escort_profile.deposit_mode = 'EARNINGS'
        self.provider.escort_profile.save(update_fields=['deposit_mode'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_ids = []
        for key in ('handoff-race-a', 'handoff-race-b'):
            result = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
                'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':key}, format='json'))
            order_ids.append(result['id'])
        token = issue_account_tokens(self.accounts[self.admin.pk])[0]
        old_id, new_id = self.accounts[self.provider.pk].pk, self.accounts[replacement.pk].pk
        barrier = Barrier(2)
        def handoff(order_id):
            close_old_connections()
            try:
                client = APIClient(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'并发隔离改派',
                    'transfers':[{'old_provider_id':old_id, 'new_provider_id':new_id,
                                  'split_type':'PERCENT', 'split_value':0, 'penalty_amount':0}]}, format='json').data['code']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(handoff, order_ids))
        self.assertEqual(sum(code == 0 for code in results), 1)
        self.assertEqual(RefundDepositRecoveryPermit.objects.filter(account_id=new_id, closed_at__isnull=True).count(), 1)
        self.assertEqual(Order.objects.filter(pk__in=order_ids, provider_account_id=new_id).count(), 1)
