import time
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from console.grab_config import save_grab_window
from console.capacity_config import save_capacity
from orders.grab_single_flow import resolve
from orders.models import (Order, OrderGrabPoolSnapshot, OrderGrabRound, OrderGrabSingleAdmission,
                           OrderGrabSingleRoundState)
from orders.tests import test_game_commission_payment as fixture
from site_messages.models import Message
from wallet.models import SystemConfig


@override_settings(GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabSingleFlowTest(APITransactionTestCase):
    setUp = fixture.GameCommissionPaymentTest.setUp
    link = fixture.GameCommissionPaymentTest.link
    auth = fixture.GameCommissionPaymentTest.auth
    create = fixture.GameCommissionPaymentTest.create

    def test_disabled_clock_keeps_existing_round_from_legacy_assignment_or_refund(self):
        from orders.tasks import auto_cancel_pending_order
        self.prepare()
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            self.auth(self.accounts[0])
            grab = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
            self.assertEqual(grab.status_code, 503, grab.data)
            self.auth(self.boss_account)
            cancel = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {}, format='json')
            self.assertEqual(cancel.status_code, 503, cancel.data)
            self.assertEqual(auto_cancel_pending_order.run(order.pk), 'disabled')
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id, order.payment_status), ('PENDING', None, 'PAID'))

    def test_unavailable_window_does_not_assign_unsnapshotted_order_and_boss_can_cancel(self):
        self.prepare()
        self.auth(self.boss_account)
        with patch('orders.grab_pool_snapshot.initialize_entry', return_value=None):
            response = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        from orders.tests.factories import make_console_user
        staff = make_console_user(['order:view', 'order:dispatch'])
        staff_account = self.link(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        with self.settings(GRAB_SINGLE_WINDOW_ENABLED=False, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=False):
            self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=order).exists())
            self.auth(self.accounts[0])
            grab = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
            self.assertEqual(grab.status_code, 503, grab.data)
            order.refresh_from_db()
            self.assertEqual((order.status, order.provider_account_id), ('PENDING', None))
            self.auth(staff_account)
            assigned = self.client.post(f'/api/admin/orders/{order.pk}/assign/',
                                        {'provider_id': self.accounts[0].pk}, format='json')
            self.assertNotEqual(assigned.data['code'], 0, assigned.data)
            self.assertIn('抢单池自动分配', assigned.data['msg'])
            order.refresh_from_db()
            self.assertEqual((order.status, order.provider_account_id), ('PENDING', None))
            self.auth(self.boss_account)
            result = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {'reason': '无需服务'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            order.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((order.status, self.wallet.balance), ('CANCELLED', 100000))

    def test_unavailable_window_rejects_public_and_staff_orders_before_charging(self):
        self.prepare()
        self.auth(self.boss_account)
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            for arguments in ({'dispatch_mode': 'AUTO_GRAB'}, {'dispatch_route': 'STAFF'}):
                response = self.create(**arguments)
                self.assertEqual(response.status_code, 503, response.data)
                self.assertEqual(response.data['errorCode'], 'GRAB_WINDOW_UNAVAILABLE')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())

    def test_unavailable_window_does_not_block_selected_provider_or_audition(self):
        self.prepare()
        self.auth(self.boss_account)
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            selected = self.create(provider_id=self.accounts[0].pk, client_request_id='selected-unavailable-window')
            self.assertEqual(selected.data['code'], 0, selected.data)
            audition = self.create(client_request_id='audition-unavailable-window')
            self.assertEqual(audition.data['code'], 0, audition.data)
        order = Order.objects.get(pk=audition.data['data']['id'])
        self.assertTrue(order.requires_boss_selection)

    def test_staff_created_order_is_rejected_before_charging_when_window_unavailable(self):
        from orders.tests.factories import make_console_user
        self.prepare()
        staff = make_console_user(['order:view', 'order:dispatch'])
        account = self.link(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.auth(account)
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            result = self.client.post('/api/admin/orders/dispatch/', {
                'customer_id': self.boss_account.pk, 'service_id': self.service.pk,
                'client_request_id': 'staff-disabled-window'}, format='json')
        self.assertEqual(result.status_code, 503, result.data)
        self.assertEqual(result.data['errorCode'], 'GRAB_WINDOW_UNAVAILABLE')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())

    def test_double_automatic_orders_do_not_charge_when_window_unavailable(self):
        from orders.tests.factories import make_console_user
        self.prepare()
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        staff = make_console_user(['order:view', 'order:dispatch'])
        staff_account = self.link(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            self.auth(self.boss_account)
            boss = self.create(dispatch_mode='AUTO_GRAB')
            self.assertNotEqual(boss.data['code'], 0, boss.data)
            self.auth(staff_account)
            staff_result = self.client.post('/api/admin/orders/dispatch/', {
                'customer_id': self.boss_account.pk, 'service_id': self.service.pk,
                'escort_mode': 'DOUBLE', 'client_request_id': 'staff-double-disabled'}, format='json')
            self.assertEqual(staff_result.status_code, 503, staff_result.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())

    def prepare(self):
        self.enterContext(patch('orders.grab_single_flow.enqueue'))
        self.service.game_category = None
        self.service.save(update_fields=['game_category'])
        save_grab_window(1, 0, lambda *args: None)
        save_capacity(3, 0, lambda *args: None)
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_' + tier,
                                                  defaults={'value': '0'})
        for index, account in enumerate(self.accounts):
            account.is_openid_bound = True
            account.openid = f'single-window-wechat-{index}'
            account.save(update_fields=['is_openid_bound', 'openid'])
            profile = self.providers[index].escort_profile
            profile.wechat_avatar_verified_at = timezone.now()
            profile.pass_tier = 'SILVER' if index == 0 else 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(hours=1)
            profile.save(update_fields=['wechat_avatar_verified_at', 'pass_tier', 'pass_expires_at'])

    def test_first_window_ranks_committed_candidates_and_leaves_unawarded_pending(self):
        self.prepare()
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(0.01)  # SQLite clock precision must pass the pool-entry timestamp.
        for index, account in enumerate(self.accounts):
            self.auth(account)
            result = self.client.post(f'/api/orders/orders/{order.pk}/grab/',
                {'team_size': 1, 'client_request_id': f'single-candidate-{index}'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual(result.data['data']['status'], 'PENDING')
            order.refresh_from_db()
            self.assertEqual(order.status, 'PENDING')
        time.sleep(1.05)
        self.assertEqual(resolve(order.pk), 'ASSIGNED')
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', self.accounts[1].pk))
        self.assertEqual(order.providers.get().provider_account_id, self.accounts[1].pk)
        self.assertEqual(OrderGrabSingleRoundState.objects.get(round__pool__order=order).status, 'ASSIGNED')
        self.assertTrue(Message.objects.filter(recipient_account=self.accounts[1], related_order_id=order.pk,
                                               title='抢单成功').exists())
        self.assertTrue(Message.objects.filter(recipient_account=self.accounts[0], related_order_id=order.pk,
                                               title='本次未抢到订单').exists())
        self.assertEqual(resolve(order.pk), 'ASSIGNED')
        for index, expected in ((0, 'NOT_WON'), (1, 'WON')):
            self.auth(self.accounts[index])
            replay = self.client.post(f'/api/orders/orders/{order.pk}/grab/',
                {'team_size': 1, 'client_request_id': f'single-candidate-{index}'}, format='json')
            self.assertEqual((replay.status_code, replay.data['data']['status']), (200, expected), replay.data)

    def test_empty_first_window_parks_then_first_eligible_grab_assigns(self):
        self.prepare()
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(1.05)
        self.assertEqual(resolve(order.pk), 'PARKED')
        self.auth(self.accounts[0])
        result = self.client.post(f'/api/orders/orders/{order.pk}/grab/',
                                  {'client_request_id': 'parked-first'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(resolve(order.pk), 'ASSIGNED')
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[0].pk)

    def test_due_window_is_advanced_on_late_grab_and_replay_returns_result(self):
        self.prepare()
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(1.05)  # Deliberately do not run the scheduled resolver.
        self.auth(self.accounts[0])
        body = {'client_request_id': 'late-first-candidate'}
        result = self.client.post(f'/api/orders/orders/{order.pk}/grab/', body, format='json')
        self.assertEqual((result.status_code, result.data['data']['status']), (200, 'WON'), result.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', self.accounts[0].pk))
        replay = self.client.post(f'/api/orders/orders/{order.pk}/grab/', body, format='json')
        self.assertEqual((replay.status_code, replay.data['data']['status']), (200, 'WON'), replay.data)
        self.assertEqual(order.providers.count(), 1)

    def test_due_window_is_advanced_before_boss_cancel(self):
        self.prepare()
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(1.05)
        self.auth(self.accounts[0])
        denied = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {'reason': '无权操作'}, format='json')
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(OrderGrabSingleRoundState.objects.get(round__pool__order=order).status, 'OPEN')
        self.auth(self.boss_account)
        result = self.client.post(f'/api/orders/orders/{order.pk}/cancel/',
                                  {'reason': '不再需要'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        order.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((order.status, self.wallet.balance), ('CANCELLED', 100000))

    def test_due_window_resolves_earlier_candidate_before_late_grab(self):
        self.prepare()
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(0.01)
        self.auth(self.accounts[0])
        first = self.client.post(f'/api/orders/orders/{order.pk}/grab/',
                                 {'client_request_id': 'window-candidate'}, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        time.sleep(1.05)
        self.auth(self.accounts[1])
        late = self.client.post(f'/api/orders/orders/{order.pk}/grab/',
                                {'client_request_id': 'late-candidate'}, format='json')
        self.assertNotEqual(late.data['code'], 0, late.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', self.accounts[0].pk))

    def test_boss_cancel_before_deadline_refunds_and_cannot_allocate(self):
        self.prepare()
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(0.01)
        result = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {'reason': '不再需要'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((order.status, self.wallet.balance), ('CANCELLED', 100000))
        self.assertEqual(resolve(order.pk), 'CANCELLED')

    def test_highest_candidate_loses_eligibility_reopens_to_everyone(self):
        self.prepare()
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(0.01)
        for index, account in enumerate(self.accounts):
            self.auth(account)
            result = self.client.post(f'/api/orders/orders/{order.pk}/grab/',
                {'client_request_id': f'reopen-candidate-{index}'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        profile = self.providers[1].escort_profile
        profile.status = 'OFFLINE'
        profile.save(update_fields=['status'])
        time.sleep(1.05)
        self.assertEqual(resolve(order.pk), 'REOPENED')
        states = OrderGrabSingleRoundState.objects.filter(round__pool__order=order).order_by('round__number')
        self.assertEqual(list(states.values_list('status', flat=True)), ['REOPENED', 'OPEN'])
        self.assertTrue(states.last().round.simultaneous_visibility)
        order.refresh_from_db()
        self.assertEqual(order.status, 'PENDING')

    def test_equal_deadlines_prioritize_cancel_then_assignment_then_timeout_refund(self):
        from orders.tasks import auto_cancel_pending_order
        self.prepare()

        def create_order(key):
            self.auth(self.boss_account)
            response = self.create(dispatch_mode='AUTO_GRAB', client_request_id=key)
            self.assertEqual(response.data['code'], 0, response.data)
            order = Order.objects.get(pk=response.data['data']['id'])
            pool = OrderGrabPoolSnapshot.objects.get(order=order)
            OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(
                original_total_deadline=pool.first_deadline, total_deadline=pool.first_deadline)
            OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
            Order.objects.filter(pk=order.pk).update(auto_cancel_at=pool.first_deadline)
            time.sleep(0.01)
            return order

        cancelled = create_order('same-deadline-cancel')
        self.auth(self.accounts[1])
        result = self.client.post(f'/api/orders/orders/{cancelled.pk}/grab/',
                                  {'client_request_id': 'candidate-before-cancel'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.auth(self.boss_account)
        result = self.client.post(f'/api/orders/orders/{cancelled.pk}/cancel/', {'reason': '改变计划'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        cancelled.refresh_from_db()
        self.assertEqual((cancelled.status, cancelled.provider_account_id), ('CANCELLED', None))

        selected = create_order('same-deadline-assign')
        self.auth(self.accounts[1])
        result = self.client.post(f'/api/orders/orders/{selected.pk}/grab/',
                                  {'client_request_id': 'candidate-before-cutoff'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        time.sleep(1.05)
        self.auth(self.boss_account)
        late = self.client.post(f'/api/orders/orders/{selected.pk}/cancel/', {'reason': '已经超时'}, format='json')
        self.assertEqual(late.status_code, 409)
        self.assertEqual(auto_cancel_pending_order.run(selected.pk), 'assigned')
        selected.refresh_from_db()
        self.assertEqual((selected.status, selected.provider_account_id), ('GRABBED', self.accounts[1].pk))

        empty = create_order('same-deadline-empty')
        self.wallet.refresh_from_db()
        before_refund = self.wallet.balance
        time.sleep(1.05)
        self.assertEqual(auto_cancel_pending_order.run(empty.pk), 'cancelled')
        empty.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((empty.status, self.wallet.balance), ('CANCELLED', before_refund + empty.amount))
        self.assertEqual(auto_cancel_pending_order.run(empty.pk), 'cancelled')

    def test_parked_ineligible_candidate_allows_later_grab_and_preserves_timeout(self):
        from orders.tasks import auto_cancel_pending_order
        self.prepare()
        first_profile = self.providers[0].escort_profile

        def parked(key, *, short_deadline=False):
            self.auth(self.boss_account)
            result = self.create(dispatch_mode='AUTO_GRAB', client_request_id=key)
            self.assertEqual(result.data['code'], 0, result.data)
            order = Order.objects.get(pk=result.data['data']['id'])
            if short_deadline:
                pool = OrderGrabPoolSnapshot.objects.get(order=order)
                deadline = pool.first_deadline + timedelta(seconds=1)
                OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(
                    original_total_deadline=deadline, total_deadline=deadline)
                OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=deadline)
                Order.objects.filter(pk=order.pk).update(auto_cancel_at=deadline)
            time.sleep(1.05)
            self.assertEqual(resolve(order.pk), 'PARKED')
            return order

        def submit(order, index, request_id=None):
            self.auth(self.accounts[index])
            body = {'client_request_id': request_id} if request_id else {}
            # Simulate the narrow race where qualification changes before resolution.
            with patch('orders.grab_single_flow.resolve', return_value='PARKED'):
                result = self.client.post(f'/api/orders/orders/{order.pk}/grab/', body, format='json')
            self.assertEqual(result.data['code'], 0, result.data)

        order = parked('parked-invalid-then-next')
        submit(order, 0, 'parked-invalid-a')
        first_profile.status = 'OFFLINE'
        first_profile.save(update_fields=['status'])
        self.assertEqual(resolve(order.pk), 'PARKED')
        rejected = OrderGrabSingleAdmission.objects.get(round__pool__order=order, account=self.accounts[0])
        self.assertEqual(rejected.status, 'DISQUALIFIED')
        submit(order, 1, 'parked-valid-b')
        self.assertEqual(resolve(order.pk), 'ASSIGNED')
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[1].pk)

        first_profile.status = 'AVAILABLE'
        first_profile.save(update_fields=['status'])
        again = parked('parked-retry-after-invalid')
        submit(again, 0)
        first_profile.status = 'OFFLINE'
        first_profile.save(update_fields=['status'])
        self.assertEqual(resolve(again.pk), 'PARKED')
        first_profile.status = 'AVAILABLE'
        first_profile.save(update_fields=['status'])
        submit(again, 0)
        self.assertEqual(resolve(again.pk), 'ASSIGNED')
        self.assertEqual(list(OrderGrabSingleAdmission.objects.filter(round__pool__order=again)
                              .order_by('attempt').values_list('attempt', 'status')),
                         [(1, 'DISQUALIFIED'), (2, 'ACTIVE')])

        first_profile.status = 'AVAILABLE'
        first_profile.save(update_fields=['status'])
        expired = parked('parked-invalid-timeout', short_deadline=True)
        submit(expired, 0, 'parked-timeout-a')
        first_profile.status = 'OFFLINE'
        first_profile.save(update_fields=['status'])
        self.assertEqual(resolve(expired.pk), 'PARKED')
        self.wallet.refresh_from_db()
        before_refund = self.wallet.balance
        time.sleep(1.05)
        self.assertEqual(auto_cancel_pending_order.run(expired.pk), 'cancelled')
        expired.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((expired.status, self.wallet.balance), ('CANCELLED', before_refund + expired.amount))

        first_profile.status = 'AVAILABLE'
        first_profile.save(update_fields=['status'])
        cancelled = parked('parked-invalid-boss-cancel')
        submit(cancelled, 0, 'parked-cancel-a')
        first_profile.status = 'OFFLINE'
        first_profile.save(update_fields=['status'])
        self.assertEqual(resolve(cancelled.pk), 'PARKED')
        self.wallet.refresh_from_db()
        before_cancel = self.wallet.balance
        self.auth(self.boss_account)
        result = self.client.post(f'/api/orders/orders/{cancelled.pk}/cancel/', {'reason': '不再需要'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        cancelled.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((cancelled.status, self.wallet.balance), ('CANCELLED', before_cancel + cancelled.amount))
