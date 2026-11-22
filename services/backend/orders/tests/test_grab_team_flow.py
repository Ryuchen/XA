import os
from unittest import skipUnless
import time
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from console.capacity_config import save_capacity
from console.grab_config import save_grab_window
from orders.grab_team_flow import resolve
from orders.models import Order, OrderGrabTeamRoundState, ServiceItem
from orders.tests import test_game_commission_payment as fixture
from orders.tests.factories import make_provider, make_console_user
from wallet.models import IdempotencyKey, SystemConfig, Transaction


@override_settings(GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabTeamFlowTest(APITransactionTestCase):
    setUp = fixture.GameCommissionPaymentTest.setUp
    link = fixture.GameCommissionPaymentTest.link
    auth = fixture.GameCommissionPaymentTest.auth
    create = fixture.GameCommissionPaymentTest.create

    def assert_stale_team_checkout_rejected(self, billing_count, changed_terms):
        from engagement.models import PointFundingLot
        from orders import views

        self.service.fulfillment_provider_count = 2
        self.service.billing_provider_count = billing_count
        self.service.save(update_fields=['fulfillment_provider_count', 'billing_provider_count'])
        save_grab_window(30, 0, lambda *args: None)
        quote = self.client.post('/api/orders/quote/', {'service_id': self.service.pk,
                                 'dispatch_mode': 'AUTO_GRAB'}, format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        original_ensure_locks = views.ensure_provider_dispatch_locks

        def change_before_payment(ids):
            original_ensure_locks(ids)
            ServiceItem.objects.filter(pk=self.service.pk).update(**changed_terms)

        with patch('orders.views.ensure_provider_dispatch_locks', side_effect=change_before_payment) as change:
            response = self.create(dispatch_mode='AUTO_GRAB', expected_amount=quote.data['data']['amount'])

        change.assert_called_once()
        self.assertEqual(response.data.get('errorCode'), 'SERVICE_CHANGED', response.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertEqual(PointFundingLot.objects.get(account=self.boss_account).remaining, 100000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=self.boss_account).exists())

    def test_checkout_rejects_removed_team_seat_after_quote(self):
        self.assert_stale_team_checkout_rejected(1, {'fulfillment_provider_count': 1})

    def test_checkout_rejects_repriced_team_with_same_total(self):
        self.assert_stale_team_checkout_rejected(2, {'price': 10000, 'billing_provider_count': 1})

    @override_settings(GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_CUTOFF_WATERMARK_TESTING=True)
    def test_timeout_never_refunds_a_legacy_receipt_when_the_latest_state_is_missing(self):
        from io import StringIO
        from django.core.management import call_command
        from orders import grab_single_flow, grab_team_flow
        from orders.grab_receipt_probe import record_intent
        from orders.models import OrderGrabRound
        from orders.tasks import auto_cancel_pending_order

        save_grab_window(30, 0, lambda *args: None)
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'order:cancel'])
        staff_account = self.link(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        for seats, flow in ((1, grab_single_flow), (2, grab_team_flow)):
            with self.subTest(seats=seats):
                self.auth(self.boss_account)
                self.service.fulfillment_provider_count = seats
                self.service.save(update_fields=['fulfillment_provider_count'])
                purchase = self.create(dispatch_mode='AUTO_GRAB', client_request_id=f'missing-state-pay-{seats}')
                self.assertEqual(purchase.data['code'], 0, purchase.data)
                order = Order.objects.get(pk=purchase.data['data']['id'])
                first = OrderGrabRound.objects.get(pool__order=order)
                receipt = record_intent(first.pk, self.boss_account.pk, 'BOSS_CANCEL', 'old-round-cancellation',
                                        {'reason': '旧轮取消'}, single_flow=seats == 1, team_flow=seats == 2)
                self.assertEqual(receipt.state, 'BEFORE')
                opened = first.deadline
                deadline = opened + timedelta(seconds=first.pool.window_seconds)
                latest = OrderGrabRound.objects.create(pool=first.pool, number=2, opened_at=opened, deadline=deadline,
                                                       total_deadline=max(first.total_deadline, deadline),
                                                       simultaneous_visibility=True)
                self.wallet.refresh_from_db()
                paid_balance = self.wallet.balance
                for _ in range(2):
                    self.assertEqual(flow.resolve(order.pk), 'WAIT_ROUND_REVIEW')
                    self.assertEqual(auto_cancel_pending_order.run(order.pk), 'wait_round_review')
                with patch('django.utils.timezone.now', return_value=first.total_deadline + timedelta(seconds=1)):
                    call_command('check_order_timeout', stdout=StringIO())

                self.auth(staff_account)
                review_path = f'/api/admin/grab-reviews/{order.pk}/'
                detail = self.client.get(review_path)
                self.assertEqual(detail.data['data']['status'], 'REVIEW')
                self.assertEqual(detail.data['data']['round_id'], latest.pk)
                listed = self.client.get('/api/admin/grab-reviews/').data['data']
                self.assertIn(latest.pk, {row['round_id'] for row in listed}, listed)
                for action in ('RESUME', 'CANCEL'):
                    result = self.client.post(review_path + 'actions/', {
                        'action': action, 'reason': '验证证据不全', 'client_request_id': f'blocked-{action}-{seats}',
                    }, format='json')
                    self.assertEqual((result.status_code, result.data['errorCode']), (409, 'GRAB_ROUND_REVIEW'), result.data)
                cancelled = self.client.post(f'/api/admin/orders/{order.pk}/cancel/', {}, format='json')
                self.assertEqual((cancelled.status_code, cancelled.data['errorCode']), (409, 'GRAB_ROUND_REVIEW'), cancelled.data)
                refund = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '回归核对'}, format='json')
                self.assertEqual((refund.status_code, refund.data.get('errorCode')), (409, 'GRAB_ROUND_REVIEW'), refund.data)
                order.refresh_from_db()
                self.wallet.refresh_from_db()
                self.assertEqual((order.status, order.payment_status, self.wallet.balance),
                                 ('PENDING', 'PAID', paid_balance))
                self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -order.amount)])
                self.assertFalse(order.refund_records.exists())
                self.assertFalse(order.providers.exists())
                self.assertEqual(first.receipt_probes.get().pk, receipt.receipt_id)

    @override_settings(GRAB_SINGLE_WINDOW_ENABLED=True)
    def test_paid_pool_without_any_state_records_only_the_owners_review_request(self):
        from orders.models import OrderGrabReviewCancellation, OrderGrabSingleRoundState
        from orders.tasks import auto_cancel_pending_order

        save_grab_window(30, 0, lambda *args: None)
        with patch.object(OrderGrabSingleRoundState.objects, 'get_or_create', return_value=(None, False)):
            purchase = self.create(dispatch_mode='AUTO_GRAB', client_request_id='state-row-missing')
        self.assertEqual(purchase.data['code'], 0, purchase.data)
        order = Order.objects.get(pk=purchase.data['data']['id'])
        path = f'/api/orders/orders/{order.pk}/cancel/'
        payload = {'client_request_id': 'cancel-missing-state', 'reason': '请核对未分配订单'}
        self.auth(self.accounts[0])
        self.assertEqual(self.client.post(path, payload, format='json').status_code, 404)
        self.auth(self.boss_account)
        for _ in range(2):
            result = self.client.post(path, payload, format='json')
            self.assertEqual(result.data.get('data'), {'status': 'PENDING'}, result.data)
        conflict = self.client.post(path, dict(payload, reason='切换取消原因'), format='json')
        self.assertEqual((conflict.status_code, conflict.data.get('errorCode')), (409, 'REQUEST_ID_CONFLICT'))
        self.assertEqual(auto_cancel_pending_order.run(order.pk), 'wait_round_review')
        self.assertEqual(OrderGrabReviewCancellation.objects.filter(order=order).count(), 1)
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, self.wallet.balance), ('PENDING', 'PAID', 100000 - order.amount))
        self.assertFalse(order.refund_records.exists())
        self.assertFalse(order.providers.exists())

    @override_settings(GRAB_SINGLE_WINDOW_ENABLED=True)
    def test_inconsistent_round_time_is_reported_without_overwriting_old_evidence(self):
        from orders.grab_single_flow import resolve
        from orders.models import OrderGrabReviewCancellation, OrderGrabRound, OrderGrabSingleRoundState

        save_grab_window(30, 0, lambda *args: None)
        purchase = self.create(dispatch_mode='AUTO_GRAB', client_request_id='inconsistent-round-payment')
        self.assertEqual(purchase.data['code'], 0, purchase.data)
        order_id = purchase.data['data']['id']
        first = OrderGrabRound.objects.get(pool__order_id=order_id)
        opened = first.deadline - timedelta(seconds=1)
        deadline = opened + timedelta(seconds=first.pool.window_seconds)
        latest = OrderGrabRound.objects.create(pool=first.pool, number=2, opened_at=opened, deadline=deadline,
                                               total_deadline=max(first.total_deadline, deadline), simultaneous_visibility=True)
        state = OrderGrabSingleRoundState.objects.create(round=latest)
        staff = make_console_user(['order:view'])
        staff_account = self.link(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.auth(staff_account)
        review = self.client.get(f'/api/admin/grab-reviews/{order_id}/').data['data']
        self.assertEqual((review['status'], review['round_id']), ('REVIEW', latest.pk))
        self.assertIn('重开抢单轮次不一致', review['reason'])

        self.auth(self.boss_account)
        cancellation = self.client.post(f'/api/orders/orders/{order_id}/cancel/', {
            'client_request_id': 'cancel-on-inconsistent-round', 'reason': '请客服核对',
        }, format='json')
        self.assertEqual(cancellation.data.get('data'), {'status': 'PENDING'}, cancellation.data)
        for _ in range(2):
            self.assertEqual(resolve(order_id), 'WAIT_ROUND_REVIEW')
        state.refresh_from_db()
        self.assertEqual((state.status, state.ranking_snapshot.get('review_phase')), ('REVIEW', 'OPEN'))
        self.assertEqual(first.single_state.status, 'OPEN')
        self.assertFalse(first.receipt_probes.exists())
        self.assertFalse(latest.receipt_probes.exists())
        self.assertEqual(OrderGrabReviewCancellation.objects.filter(order_id=order_id).count(), 1)
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.status, order.payment_status), ('PENDING', 'PAID'))
        self.assertFalse(order.refund_records.exists())
        self.assertFalse(order.providers.exists())

    @override_settings(GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_CUTOFF_WATERMARK_TESTING=True)
    def test_owner_cancellation_is_recorded_before_first_round_arbitration(self):
        from orders import grab_single_flow, grab_team_flow
        from orders.grab_cutoff_watermark import seal
        from orders.models import OrderGrabSingleRoundState
        from site_messages.models import CustomerCancellationNotice

        save_grab_window(30, 0, lambda *args: None)
        flows = ((1, grab_single_flow, OrderGrabSingleRoundState), (2, grab_team_flow, OrderGrabTeamRoundState))
        for seats, flow, states in flows:
            with self.subTest(seats=seats):
                self.service.fulfillment_provider_count = seats
                self.service.save(update_fields=['fulfillment_provider_count'])
                bought = self.create(dispatch_mode='AUTO_GRAB', client_request_id=f'pay-before-cancel-{seats}')
                self.assertEqual(bought.data['code'], 0, bought.data)
                order = Order.objects.get(pk=bought.data['data']['id'])
                state = states.objects.get(round__pool__order=order)
                after_window = state.round.deadline + timedelta(seconds=1)
                self.assertLess(after_window, state.round.total_deadline)
                original_resolve = flow.resolve

                def resolve_after_registration(order_id, **kwargs):
                    receipt = state.round.receipt_probes.get(kind='BOSS_CANCEL')
                    self.assertEqual((receipt.state, receipt.round_phase), ('BEFORE', 'PARKED'))
                    return original_resolve(order_id, **kwargs)

                endpoint = f'/api/orders/orders/{order.pk}/cancel/'
                payload = {'client_request_id': f'cancel-before-arbitration-{seats}', 'reason': '客户主动取消'}
                from orders.grab_receipt_probe import _db_clock
                _, precision, driver = _db_clock()
                with patch('django.utils.timezone.now', return_value=after_window), \
                        patch('orders.grab_receipt_probe._db_clock', return_value=(after_window, precision, driver)):
                    if seats == 2:
                        self.assertEqual(seal(order.pk, team=True), 'SEALED')
                    with patch.object(flow, 'resolve', side_effect=resolve_after_registration), \
                            patch('site_messages.order_customer_cancel_notice.enabled', return_value=True):
                        cancelled = self.client.post(endpoint, payload, format='json')
                        repeated = self.client.post(endpoint, payload, format='json')
                        changed = self.client.post(endpoint, dict(payload, reason='改用另一个原因'), format='json')
                for response in (cancelled, repeated):
                    self.assertEqual(response.data['data']['status'], 'CANCELLED', response.data)
                self.assertEqual(changed.status_code, 409, changed.data)
                self.assertEqual(changed.data.get('errorCode'), 'REQUEST_ID_CONFLICT')
                refund = order.refund_records.get()
                wallet_entry = order.transactions.get(tx_type='REFUND')
                receipt = state.round.receipt_probes.get(kind='BOSS_CANCEL')
                notice = CustomerCancellationNotice.objects.get(order=order)
                self.assertEqual((refund.operator_account_id, wallet_entry.operator_account_id),
                                 (self.boss_account.pk, self.boss_account.pk))
                self.assertEqual((notice.receipt_id, notice.refund_id, notice.wallet_transaction_id),
                                 (receipt.pk, refund.pk, wallet_entry.pk))
                self.assertIsNotNone(notice.message_id)
                self.assertFalse(order.providers.exists())

    def test_proven_late_request_keeps_its_result_through_round_and_review_changes(self):
        from orders.grab_receipt_probe import _db_clock
        from orders.models import OrderGrabReviewCancellation, OrderGrabRound

        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        save_grab_window(30, 0, lambda *args: None)
        _, precision, driver = _db_clock()
        for label, payload in (('explicit', {'client_request_id': 'proven-late-cancel'}), ('legacy', {})):
            with self.subTest(label=label):
                bought = self.create(dispatch_mode='AUTO_GRAB', client_request_id=f'pay-before-late-cancel-{label}')
                self.assertEqual(bought.data['code'], 0, bought.data)
                order = Order.objects.get(pk=bought.data['data']['id'])
                first = OrderGrabRound.objects.get(pool__order=order)
                endpoint = f'/api/orders/orders/{order.pk}/cancel/'
                late = first.total_deadline + timedelta(seconds=1)
                with patch('orders.grab_receipt_probe._db_clock', return_value=(late, precision, driver)):
                    response = self.client.post(endpoint, payload, format='json')
                self.assertEqual((response.status_code, response.data.get('errorCode')),
                                 (409, 'GRAB_CANCEL_CLOSED'), response.data)
                self.assertEqual(first.receipt_probes.get(kind='BOSS_CANCEL').state, 'AFTER')
                self.assertFalse(OrderGrabReviewCancellation.objects.filter(order=order).exists())

                deadline = first.deadline + timedelta(seconds=first.pool.window_seconds)
                OrderGrabRound.objects.create(pool=first.pool, number=2, opened_at=first.deadline,
                    deadline=deadline, total_deadline=max(first.total_deadline, deadline), simultaneous_visibility=True)
                replay = self.client.post(endpoint, payload, format='json')
                self.assertEqual((replay.status_code, replay.data.get('errorCode')), (409, 'GRAB_CANCEL_CLOSED'), replay.data)
                self.assertFalse(OrderGrabReviewCancellation.objects.filter(order=order).exists())
                manual = self.client.post(endpoint, {'client_request_id': 'separate-review'}, format='json')
                self.assertEqual(manual.data.get('data'), {'status': 'PENDING'}, manual.data)
                replay = self.client.post(endpoint, payload, format='json')
                self.assertEqual((replay.status_code, replay.data.get('errorCode')), (409, 'GRAB_CANCEL_CLOSED'), replay.data)
                self.assertEqual(OrderGrabReviewCancellation.objects.get(order=order).request_key, 'separate-review')
                self.assertEqual(first.receipt_probes.count(), 1)
                self.assertFalse(order.refund_records.exists())

    def test_review_cancellation_replays_the_owner_request_without_a_round(self):
        self.assert_review_cancellation_replay(self.paid_order_with_opening_review())

    def test_review_request_stays_in_queue_while_opening_evidence_is_being_recovered(self):
        from orders.models import OrderGrabPoolOpeningIntent, OrderGrabReviewCancellation

        order = self.paid_order_with_opening_review()
        endpoint = f'/api/orders/orders/{order.pk}/cancel/'
        payload = {'client_request_id': 'owner-opening-review', 'reason': '请核对开池记录'}
        first = self.client.post(endpoint, payload, format='json')
        self.assertEqual(first.data.get('data'), {'status': 'PENDING'}, first.data)
        application = OrderGrabReviewCancellation.objects.get(order=order)
        OrderGrabPoolOpeningIntent.objects.filter(order=order).update(state='PENDING', reason='')

        staff = make_console_user(['order:view'])
        staff_account = self.link(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.auth(staff_account)
        queue = self.client.get('/api/admin/grab-reviews/').data['data']
        self.assertIn(order.pk, {row['order_id'] for row in queue}, queue)
        self.auth(self.boss_account)
        replay = self.client.post(endpoint, payload, format='json')
        self.assertEqual(replay.data.get('data'), {'status': 'PENDING'}, replay.data)
        with patch('orders.grab_customer_review.replay_cancellation', return_value=None):
            replay = self.client.post(endpoint, payload, format='json')
        self.assertEqual(replay.data.get('data'), {'status': 'PENDING'}, replay.data)
        self.assertEqual(OrderGrabReviewCancellation.objects.get(order=order).pk, application.pk)
        self.assertFalse(order.refund_records.exists())

    @override_settings(CUSTOMER_CANCEL_NOTICE_RECOVERY_ENABLED=True, PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_existing_review_request_owns_the_refund_and_survives_clock_disablement(self):
        from console.models import AdminAuditLog
        from orders.grab_review import mark_round_review
        from orders.models import OrderGrabReviewCancellation
        from site_messages.models import CustomerCancellationNotice

        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        save_grab_window(30, 0, lambda *args: None)
        bought = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(bought.data['code'], 0, bought.data)
        order = Order.objects.get(pk=bought.data['data']['id'])
        state = OrderGrabTeamRoundState.objects.get(round__pool__order=order)
        mark_round_review(state, '轮次证据待核对')
        endpoint = f'/api/orders/orders/{order.pk}/cancel/'
        owner = {'client_request_id': 'owner-review-replay', 'reason': '  请核对原付款  '}
        accepted = self.client.post(endpoint, owner, format='json')
        self.assertEqual(accepted.data.get('data'), {'status': 'PENDING'}, accepted.data)
        application = OrderGrabReviewCancellation.objects.get(order=order)
        # Simulate the time evidence becoming consistent before the refund is authorized.
        OrderGrabTeamRoundState.objects.filter(pk=state.pk).update(status='OPEN', reason='')

        def assert_owner_can_replay(status):
            with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False), \
                    patch('orders.grab_receipt_probe._db_clock', side_effect=AssertionError('replay sampled the clock')):
                repeated = self.client.post(endpoint, owner, format='json')
                self.assertEqual(repeated.data.get('data'), {'status': status}, repeated.data)
                changed = self.client.post(endpoint, dict(owner, reason='不同原因'), format='json')
                self.assertEqual((changed.status_code, changed.data.get('errorCode')), (409, 'REQUEST_ID_CONFLICT'))
                new_key = self.client.post(endpoint, {'client_request_id': 'read-existing-only'}, format='json')
                self.assertEqual(new_key.data.get('data'), {'status': status}, new_key.data)

        assert_owner_can_replay('PENDING')
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'order:cancel'])
        staff_account = self.link(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.auth(staff_account)
        for action in ('cancel', 'refund'):
            blocked = self.client.post(f'/api/admin/orders/{order.pk}/{action}/', {'reason': '通用入口'}, format='json')
            self.assertEqual((blocked.status_code, blocked.data.get('errorCode')),
                             (409, 'GRAB_CANCEL_REVIEW_PENDING'), blocked.data)
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, self.wallet.balance), ('PENDING', 'PAID', 100000 - order.amount))
        self.assertFalse(order.refund_records.exists())
        self.assertFalse(CustomerCancellationNotice.objects.filter(order=order).exists())

        staff_action = {'action': 'CANCEL', 'reason': '已核实原付款', 'client_request_id': 'staff-review-refund'}
        attempted_resume = self.client.post(f'/api/admin/grab-reviews/{order.pk}/actions/',
            dict(staff_action, action='RESUME', client_request_id='staff-review-resume'), format='json')
        self.assertEqual(attempted_resume.status_code, 409, attempted_resume.data)
        for _ in range(2):
            refunded = self.client.post(f'/api/admin/grab-reviews/{order.pk}/actions/', staff_action, format='json')
            self.assertEqual(refunded.data.get('code'), 0, refunded.data)
        self.auth(self.boss_account)
        assert_owner_can_replay('CANCELLED')
        application.refresh_from_db()
        self.assertEqual((application.request_key, application.reason, application.status),
                         ('owner-review-replay', '请核对原付款', 'CANCELLED'))
        audit = AdminAuditLog.objects.get(resource='grab-review', object_id=str(order.pk))
        refund = order.refund_records.get()
        notice = CustomerCancellationNotice.objects.get(order=order)
        self.assertEqual((refund.amount, refund.operator_account_id), (order.amount, staff_account.pk))
        self.assertEqual((notice.review_audit_id, notice.refund_id, notice.wallet_transaction_id),
                         (audit.pk, refund.pk, refund.customer_transaction_id))
        self.assertCountEqual(order.transactions.values_list('tx_type', 'amount'), [('PAY', -order.amount), ('REFUND', order.amount)])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)

    def paid_order_with_opening_review(self, *, client_request_id='pay-for-opening-review'):
        from console.grab_config import current_rules
        from orders.models import OrderGrabPoolOpeningIntent, OrderGrabRound

        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        save_grab_window(30, current_rules()['version'], lambda *args: None)
        with patch('orders.grab_pool_snapshot.initialize_entry', return_value=None):
            bought = self.create(dispatch_mode='AUTO_GRAB', client_request_id=client_request_id)
        self.assertEqual(bought.data['code'], 0, bought.data)
        order = Order.objects.get(pk=bought.data['data']['id'])
        OrderGrabPoolOpeningIntent.objects.create(order=order, payment=order.transactions.get(tx_type='PAY'),
            paid_amount=order.amount, state='REVIEW', reason='缺少开池证据')
        self.assertFalse(OrderGrabRound.objects.filter(pool__order=order).exists())
        return order

    @override_settings(CUSTOMER_CANCEL_NOTICE_RECOVERY_ENABLED=True, PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_staff_review_refund_notice_recovers_once_for_original_boss(self):
        from console.models import AdminAuditLog
        from site_messages.models import CustomerCancellationNotice, Message
        from site_messages.tasks import recover_customer_cancellation_notices

        order = self.paid_order_with_opening_review()
        with patch('site_messages.order_customer_cancel_notice.deliver',
                   side_effect=RuntimeError('提交后通知进程中断')) as interrupted:
            self.assert_review_cancellation_replay(order)
        intent = CustomerCancellationNotice.objects.get(order=order)
        interrupted.assert_called_once_with(intent.pk)
        audit = AdminAuditLog.objects.get(resource='grab-review', object_id=str(order.pk))
        refund = order.refund_records.get()
        self.assertEqual((intent.review_audit_id, intent.refund_id, intent.wallet_transaction_id,
                          intent.account_id, intent.business_cycle, intent.receipt_id),
                         (audit.pk, refund.pk, refund.customer_transaction_id,
                          self.boss_account.pk, order.business_cycle, None))
        self.assertIsNone(intent.completed_at)
        self.assertFalse(Message.objects.filter(related_order_id=order.pk, title='订单已取消').exists())

        self.assertEqual(recover_customer_cancellation_notices.run(), 1)
        self.assertEqual(recover_customer_cancellation_notices.run(), 0)
        intent.refresh_from_db()
        self.assertIsNotNone(intent.completed_at)
        self.assertEqual(intent.skipped_reason, '')
        message = Message.objects.get(related_order_id=order.pk, title='订单已取消')
        self.assertEqual((intent.message_id, message.recipient_account_id, message.recipient_business_cycle),
                         (message.pk, self.boss_account.pk, order.business_cycle))
        self.assertIn('退款已退回', message.preview)
        self.assertEqual(order.refund_records.get().pk, refund.pk)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)

    @override_settings(CUSTOMER_CANCEL_NOTICE_RECOVERY_ENABLED=True, PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_staff_review_refund_notice_skips_unverified_source_or_changed_boss_cycle(self):
        from club_accounts.models import ClubAccount
        from console.models import AdminAuditLog
        from site_messages.models import CustomerCancellationNotice, Message
        from site_messages.tasks import recover_customer_cancellation_notices

        for mismatch in ('audit', 'cycle'):
            with self.subTest(mismatch=mismatch):
                order = self.paid_order_with_opening_review(client_request_id=f'notice-{mismatch}')
                with patch('site_messages.order_customer_cancel_notice.deliver'):
                    self.assert_review_cancellation_replay(order)
                intent = CustomerCancellationNotice.objects.get(order=order)
                if mismatch == 'audit':
                    AdminAuditLog.objects.filter(pk=intent.review_audit_id).update(object_id='wrong-order')
                else:
                    ClubAccount.objects.filter(pk=self.boss_account.pk).update(
                        business_cycle=order.business_cycle + 1)
                self.assertEqual(recover_customer_cancellation_notices.run(), 1)
                intent.refresh_from_db()
                self.assertIsNotNone(intent.completed_at)
                self.assertTrue(intent.skipped_reason)
                self.assertIsNone(intent.message_id)
                self.assertFalse(Message.objects.filter(related_order_id=order.pk, title='订单已取消').exists())
                self.assertEqual(order.refund_records.count(), 1)

    def test_review_cancellation_keeps_an_unobserved_timed_receipt(self):
        from django.db import DatabaseError
        from orders import grab_receipt_probe
        from stores.db import transaction

        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        save_grab_window(30, 0, lambda *args: None)
        bought = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(bought.data['code'], 0, bought.data)
        order = Order.objects.get(pk=bought.data['data']['id'])
        state = OrderGrabTeamRoundState.objects.get(round__pool__order=order)
        probes = state.round.receipt_probes.filter(kind='BOSS_CANCEL')
        sample = grab_receipt_probe._db_clock

        def lose_post_commit_observation():
            if not transaction.get_connection().in_atomic_block and probes.exists():
                raise DatabaseError('取消登记已提交，第二次观察失败')
            return sample()

        with patch.object(grab_receipt_probe, '_db_clock', side_effect=lose_post_commit_observation):
            sent = self.client.post(f'/api/orders/orders/{order.pk}/cancel/',
                                    {'client_request_id': 'boss-review', 'reason': '  请核对原订单  '}, format='json')
        self.assert_review_cancellation_replay(order, sent=sent)
        receipt = probes.get()
        self.assertEqual((receipt.request_key, receipt.state, receipt.upper_observed_at),
                         ('boss-review', 'PENDING', None))

    @override_settings(GRAB_CUTOFF_WATERMARK_TESTING=True)
    def test_cancellation_crossing_total_cutoff_holds_complete_team_for_review(self):
        from orders import grab_receipt_probe
        from orders.models import OrderGrabCutoffWatermark

        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        self.prepare()
        # SQLite cannot take the MySQL shared lifecycle lock. Keep the real
        # invitation, confirmation and receipt writes for this cutoff test.
        with patch('club_accounts.provider_lifecycle.lock_provider_operations'):
            team_id = self.invite()
            self.auth(self.accounts[1])
            confirmed = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                         {'client_request_id': 'confirm-before-crossing'}, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data['data']['allocation_result'], 'PENDING')
        self.auth(self.boss_account)
        state = OrderGrabTeamRoundState.objects.get(round__pool__order=self.order)
        cutoff = state.round.total_deadline
        before, after = cutoff - timedelta(seconds=1), cutoff + timedelta(seconds=1)
        _, precision, driver = grab_receipt_probe._db_clock()
        with patch.object(grab_receipt_probe, '_db_clock', side_effect=[
                (before, precision, driver), (after, precision, driver)]):
            sent = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                    {'client_request_id': 'boss-review', 'reason': '  请核对原订单  '},
                                    format='json')

        receipt = state.round.receipt_probes.get(kind='BOSS_CANCEL')
        self.assertEqual((receipt.state, receipt.review_reason, receipt.round_phase,
                          receipt.lower_at, receipt.upper_observed_at, receipt.cutoff_at),
                         ('REVIEW', 'CROSSED_CUTOFF', 'PARKED', before, after, cutoff))
        # The first-window seal includes the valid team and leaves the
        # uncertain cancellation to the resolver's total-wait checks.
        from orders.grab_cutoff_watermark import seal
        with patch.object(grab_receipt_probe, '_db_clock', return_value=(after, precision, driver)):
            self.assertEqual(seal(self.order.pk, team=True), 'SEALED')
            self.assertEqual(resolve(self.order.pk), 'WAIT_REVIEW')
        mark = OrderGrabCutoffWatermark.objects.get(round=state.round)
        self.assertEqual(mark.receipt_count, 2)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('PENDING', 'PAID'))
        self.assertFalse(self.order.providers.exists())
        self.assert_review_cancellation_replay(self.order, sent=sent)
        receipt.refresh_from_db()
        self.assertEqual((receipt.state, receipt.upper_observed_at), ('REVIEW', after))

    @override_settings(GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_CUTOFF_WATERMARK_TESTING=True)
    def test_cancellation_crosses_total_cutoff_after_first_window_is_sealed(self):
        from orders import grab_receipt_probe, grab_single_flow, grab_team_flow
        from orders.models import OrderGrabCutoffWatermark, OrderGrabReviewCancellation, OrderGrabSingleRoundState

        save_grab_window(30, 0, lambda *args: None)
        _, precision, driver = grab_receipt_probe._db_clock()
        flows = ((1, grab_single_flow, OrderGrabSingleRoundState), (2, grab_team_flow, OrderGrabTeamRoundState))
        for seats, flow, states in flows:
            with self.subTest(seats=seats):
                self.service.fulfillment_provider_count = seats
                self.service.save(update_fields=['fulfillment_provider_count'])
                bought = self.create(dispatch_mode='AUTO_GRAB', client_request_id=f'pay-separate-cutoffs-{seats}')
                self.assertEqual(bought.data['code'], 0, bought.data)
                order = Order.objects.get(pk=bought.data['data']['id'])
                state = states.objects.get(round__pool__order=order)
                after_first_window = state.round.deadline + timedelta(seconds=1)
                before_total = state.round.total_deadline - timedelta(seconds=1)
                after_total = state.round.total_deadline + timedelta(seconds=1)
                self.assertLess(after_first_window, before_total)
                with patch.object(grab_receipt_probe, '_db_clock',
                                  return_value=(after_first_window, precision, driver)):
                    self.assertEqual(flow.resolve(order.pk), 'PARKED')
                mark = OrderGrabCutoffWatermark.objects.get(round=state.round)
                self.assertEqual(mark.cutoff_at, state.round.deadline)
                self.assertEqual(mark.receipt_count, 0)

                endpoint = f'/api/orders/orders/{order.pk}/cancel/'
                payload = {'client_request_id': f'cancel-separate-cutoffs-{seats}', 'reason': '跨总截止核对'}
                with patch.object(grab_receipt_probe, '_db_clock', side_effect=[
                        (before_total, precision, driver), (after_total, precision, driver)]):
                    sent = self.client.post(endpoint, payload, format='json')
                self.assertEqual(sent.data.get('data'), {'status': 'PENDING'}, sent.data)
                receipt = state.round.receipt_probes.get(kind='BOSS_CANCEL')
                self.assertEqual((receipt.state, receipt.round_phase, receipt.review_reason, receipt.cutoff_at),
                                 ('REVIEW', 'PARKED', 'CROSSED_CUTOFF', state.round.total_deadline))
                with patch.object(grab_receipt_probe, '_db_clock', return_value=(after_total, precision, driver)):
                    self.assertEqual(flow.resolve(order.pk), 'WAIT_REVIEW')
                self.assertTrue(OrderGrabReviewCancellation.objects.filter(
                    order=order, request_key=payload['client_request_id'], status='PENDING').exists())
                order.refresh_from_db()
                self.assertEqual((order.status, order.payment_status), ('PENDING', 'PAID'))
                self.assertFalse(order.providers.exists())
                self.assertFalse(order.refund_records.exists())
                current_mark = OrderGrabCutoffWatermark.objects.get(round=state.round)
                self.assertEqual((current_mark.pk, current_mark.cutoff_at, current_mark.observed_at,
                                  current_mark.receipt_digest, current_mark.receipt_count),
                                 (mark.pk, mark.cutoff_at, mark.observed_at, mark.receipt_digest, 0))

    def test_review_cancellation_without_an_owner_key_keeps_legacy_replay(self):
        from orders.grab_review import mark_round_review
        from orders.models import OrderGrabReviewCancellation

        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        save_grab_window(30, 0, lambda *args: None)
        bought = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(bought.data['code'], 0, bought.data)
        order = Order.objects.get(pk=bought.data['data']['id'])
        state = OrderGrabTeamRoundState.objects.get(round__pool__order=order)
        mark_round_review(state, '轮次证据待核对')
        endpoint = f'/api/orders/orders/{order.pk}/cancel/'
        for payload in ({'reason': '人工核对订单'}, {'reason': '另一个原因'},
                        {'reason': '带新号重查', 'client_request_id': 'does-not-backfill'}):
            result = self.client.post(endpoint, payload, format='json')
            self.assertEqual(result.data.get('data'), {'status': 'PENDING'}, result.data)
        record = OrderGrabReviewCancellation.objects.get(order=order)
        self.assertEqual((record.request_key, record.reason), (None, '人工核对订单'))
        self.assertFalse(state.round.receipt_probes.exists())

    def assert_review_cancellation_replay(self, order, *, sent=None):
        from orders.models import OrderGrabReviewCancellation

        endpoint = f'/api/orders/orders/{order.pk}/cancel/'
        payload = {'client_request_id': 'boss-review', 'reason': '  请核对原订单  '}
        if sent is None:
            sent = self.client.post(endpoint, payload, format='json')
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertEqual(sent.data.get('data'), {'status': 'PENDING'})
        self.auth(self.accounts[0])
        rejected = self.client.post(endpoint, payload, format='json')
        self.assertEqual(rejected.status_code, 404, rejected.data)
        self.auth(self.boss_account)

        def verify_replay(status):
            same = self.client.post(endpoint, dict(payload, reason='请核对原订单'), format='json')
            self.assertEqual(same.data.get('data'), {'status': status}, same.data)
            changed = self.client.post(endpoint, dict(payload, reason='改用另一个原因'), format='json')
            self.assertEqual((changed.status_code, changed.data.get('errorCode')),
                             (409, 'REQUEST_ID_CONFLICT'), changed.data)
            other = self.client.post(endpoint, dict(payload, client_request_id='new-key', reason='新请求'),
                                     format='json')
            self.assertEqual(other.data.get('data'), {'status': status}, other.data)

        verify_replay('PENDING')
        self.assertFalse(order.refund_records.exists())
        user = make_console_user(['order:view', 'order:dispatch', 'order:refund'])
        reviewer = self.link(user)
        user.admin_membership.account = reviewer
        user.admin_membership.save(update_fields=['account'])
        self.auth(reviewer)
        approved = self.client.post(f'/api/admin/grab-reviews/{order.pk}/actions/',
            {'action': 'CANCEL', 'reason': '客服核实原付款', 'client_request_id': 'staff-review'}, format='json')
        self.assertEqual(approved.status_code, 200, approved.data)

        self.auth(self.boss_account)
        verify_replay('CANCELLED')
        application = OrderGrabReviewCancellation.objects.get(order=order)
        self.assertEqual((application.customer_id, application.request_key, application.reason,
                          application.status), (self.boss_account.pk, 'boss-review', '请核对原订单', 'CANCELLED'))
        self.assertIsNotNone(application.completed_at)
        self.assertEqual(order.refund_records.get().operator_account_id, reviewer.pk)
        self.assertEqual(order.transactions.get(tx_type='REFUND').amount, order.amount)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)

    def test_earlier_rounds_late_cancel_cannot_cancel_the_reopened_round(self):
        from orders.grab_receipt_probe import _db_clock
        from orders.models import OrderGrabRound

        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        save_grab_window(30, 0, lambda *args: None)
        bought = self.create(dispatch_mode='AUTO_GRAB', client_request_id='paid-for-reopen-cancellation')
        self.assertEqual(bought.data['code'], 0, bought.data)
        order = Order.objects.get(pk=bought.data['data']['id'])
        state = OrderGrabTeamRoundState.objects.get(round__pool__order=order)
        _, precision, driver = _db_clock()
        endpoint = f'/api/orders/orders/{order.pk}/cancel/'
        payload = {'client_request_id': 'late-before-reopen', 'reason': '取消原轮'}
        expired = state.round.total_deadline + timedelta(seconds=1)
        with patch('orders.grab_receipt_probe._db_clock', return_value=(expired, precision, driver)):
            closed = self.client.post(endpoint, payload, format='json')
        self.assertEqual(closed.data.get('errorCode'), 'GRAB_CANCEL_CLOSED', closed.data)

        state.status = 'REOPENED'
        state.save(update_fields=['status'])
        opened = expired + timedelta(seconds=1)
        deadline = opened + timedelta(seconds=30)
        new_round = OrderGrabRound.objects.create(pool=state.round.pool, number=2, opened_at=opened,
            deadline=deadline, total_deadline=deadline, simultaneous_visibility=True)
        OrderGrabTeamRoundState.objects.create(round=new_round)
        now = opened + timedelta(seconds=1)
        with patch('orders.grab_receipt_probe._db_clock', return_value=(now, precision, driver)):
            repeated = self.client.post(endpoint, payload, format='json')
            changed = self.client.post(endpoint, dict(payload, reason='改用另一个原因'), format='json')
            self.assertEqual(repeated.data.get('errorCode'), 'GRAB_CANCEL_CLOSED', repeated.data)
            self.assertEqual(changed.status_code, 409, changed.data)
            self.assertEqual(changed.data.get('errorCode'), 'REQUEST_ID_CONFLICT')
            self.assertFalse(new_round.receipt_probes.exists())
            self.assertFalse(order.refund_records.exists())
            fresh = self.client.post(endpoint, dict(payload, client_request_id='cancel-reopened-round'), format='json')
        self.assertEqual(fresh.data['data']['status'], 'CANCELLED', fresh.data)
        original_after_refund = self.client.post(endpoint, payload, format='json')
        self.assertEqual(original_after_refund.data.get('errorCode'), 'GRAB_CANCEL_CLOSED')
        self.assertEqual(new_round.receipt_probes.get().request_key, 'cancel-reopened-round')
        self.assertEqual(order.refund_records.count(), 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_disabled_clock_keeps_existing_team_round_from_legacy_cancel_or_timeout(self):
        from orders.tasks import auto_cancel_pending_order
        self.prepare()
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            self.auth(self.boss_account)
            cancel = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/', {}, format='json')
            self.assertEqual(cancel.status_code, 503, cancel.data)
            self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'disabled')
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('PENDING', 'PAID'))

    def prepare(self, *, partner_delay=0, with_game=False):
        self.enterContext(patch('orders.grab_team_flow.enqueue'))
        if not with_game:
            self.service.game_category = None
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['game_category', 'billing_provider_count'])
        save_grab_window(1, 0, lambda *args: None)
        save_capacity(3, 0, lambda *args: None)
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            seconds = partner_delay if tier in ('SILVER', 'BRONZE', 'NONE') else 0
            SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_' + tier,
                                                  defaults={'value': str(seconds)})
        for index, account in enumerate(self.accounts):
            account.is_openid_bound, account.openid = True, f'team-window-wechat-{index}'
            account.save(update_fields=['is_openid_bound', 'openid'])
            profile = self.providers[index].escort_profile
            profile.wechat_avatar_verified_at = timezone.now()
            profile.pass_tier = 'BLACK' if index == 0 else 'SILVER'
            profile.pass_expires_at = timezone.now() + timedelta(hours=1)
            profile.save(update_fields=['wechat_avatar_verified_at', 'pass_tier', 'pass_expires_at'])
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(response.status_code, 200, response.data)
        self.order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(0.01)

    def invite(self):
        self.auth(self.accounts[0])
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'captain-invite'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return result.data['data']['team_id']

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_full_team_is_ranked_and_both_members_receive_the_order(self):
        self.prepare()
        team_id = self.invite()
        self.auth(self.accounts[1])
        response = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                    {'client_request_id': 'partner-confirm'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.GRABBED)
        self.assertEqual(set(self.order.providers.values_list('provider_account_id', flat=True)),
                         {self.accounts[0].pk, self.accounts[1].pk})
        self.assertEqual(OrderGrabTeamRoundState.objects.get(round__pool__order=self.order).winner_id, team_id)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_invitee_cannot_see_order_before_delay_and_may_complete_after_empty_round(self):
        self.prepare(partner_delay=2)
        team_id = self.invite()
        self.auth(self.accounts[1])
        listed = self.client.get('/api/orders/grab-team/invitations/')
        self.assertEqual(listed.status_code, 200)
        self.assertFalse(listed.data['data'][0]['order_visible'])
        self.assertNotIn('order_id', listed.data['data'][0])
        rejected = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                    {'client_request_id': 'early-confirm'}, format='json')
        self.assertEqual(rejected.status_code, 400)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'PARKED')
        time.sleep(1.0)
        accepted = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                    {'client_request_id': 'late-confirm'}, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.GRABBED)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_first_winning_team_loses_eligibility_and_reopens(self):
        self.prepare()
        team_id = self.invite()
        self.auth(self.accounts[1])
        result = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                  {'client_request_id': 'partner-before-offline'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        profile = self.providers[1].escort_profile
        profile.status = 'OFFLINE'
        profile.save(update_fields=['status'])
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'REOPENED')
        states = OrderGrabTeamRoundState.objects.filter(round__pool__order=self.order).order_by('round__number')
        self.assertEqual(list(states.values_list('status', flat=True)), ['REOPENED', 'OPEN'])
        self.assertTrue(states.last().round.simultaneous_visibility)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_two_full_teams_are_ranked_and_both_winning_members_notified(self):
        self.prepare()
        for index, tier in enumerate(('GOLD', 'SILVER'), start=2):
            user = make_provider(deposit_mode='EARNINGS', pass_tier=tier,
                                 pass_expires_at=timezone.now() + timedelta(hours=1))
            account = self.link(user)
            account.is_openid_bound, account.openid = True, f'team-window-wechat-{index}'
            account.save(update_fields=['is_openid_bound', 'openid'])
            profile = user.escort_profile
            profile.wechat_avatar_verified_at = timezone.now()
            profile.save(update_fields=['wechat_avatar_verified_at'])
            profile.service_items.add(self.service)
            self.accounts.append(account)
            self.providers.append(user)
        first_team = self.invite()
        self.auth(self.accounts[1])
        self.assertEqual(self.client.post(f'/api/orders/grab-team/invitations/{first_team}/confirm/',
            {'client_request_id': 'first-team-confirm'}, format='json').status_code, 200)
        self.auth(self.accounts[2])
        response = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[3].pk, 'client_request_id': 'second-team-invite'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        second_team = response.data['data']['team_id']
        self.auth(self.accounts[3])
        self.assertEqual(self.client.post(f'/api/orders/grab-team/invitations/{second_team}/confirm/',
            {'client_request_id': 'second-team-confirm'}, format='json').status_code, 200)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertEqual(set(self.order.providers.values_list('provider_account_id', flat=True)),
                         {self.accounts[0].pk, self.accounts[1].pk})
        from site_messages.models import Message
        for account in self.accounts[:2]:
            self.assertTrue(Message.objects.filter(recipient_account=account, related_order_id=self.order.pk,
                                                   title='抢单成功').exists())
        for account in self.accounts[2:]:
            self.assertTrue(Message.objects.filter(recipient_account=account, related_order_id=self.order.pk,
                                                   title='本次未抢到订单').exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_boss_cancellation_refunds_a_pending_team(self):
        self.prepare()
        team_id = self.invite()
        self.auth(self.accounts[1])
        accepted = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id': 'accepted-before-cancel'}, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.auth(self.boss_account)
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                  {'reason': '改变计划'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance), ('CANCELLED', 100000))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_boss_cancel_after_empty_window_does_not_depend_on_worker(self):
        self.prepare()
        time.sleep(1.05)
        self.auth(self.boss_account)
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                  {'reason': '不再需要'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance), ('CANCELLED', 100000))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_team_member_withdraws_before_switching_to_another_team(self):
        self.prepare()
        user = make_provider(deposit_mode='EARNINGS', pass_tier='GOLD',
                             pass_expires_at=timezone.now() + timedelta(hours=1))
        third = self.link(user)
        third.is_openid_bound, third.openid = True, 'team-switch-wechat'
        third.save(update_fields=['is_openid_bound', 'openid'])
        user.escort_profile.wechat_avatar_verified_at = timezone.now()
        user.escort_profile.save(update_fields=['wechat_avatar_verified_at'])
        user.escort_profile.service_items.add(self.service)
        first_team = self.invite()
        self.auth(self.accounts[1])
        result = self.client.post(f'/api/orders/grab-team/invitations/{first_team}/confirm/',
                                  {'client_request_id': 'confirm-before-switch'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.auth(third)
        occupied = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'occupied-invite'}, format='json')
        self.assertEqual(occupied.status_code, 400)
        self.auth(self.accounts[1])
        time.sleep(0.01)
        result = self.client.post(f'/api/orders/grab-team/invitations/{first_team}/withdraw/',
                                  {'client_request_id': 'leave-before-switch'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.auth(third)
        second = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'second-captain-invite'}, format='json')
        self.assertEqual(second.status_code, 200, second.data)
        self.auth(self.accounts[1])
        result = self.client.post(f"/api/orders/grab-team/invitations/{second.data['data']['team_id']}/confirm/",
                                  {'client_request_id': 'confirm-new-team'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_invitation_never_reveals_order_to_currently_ineligible_partner(self):
        self.prepare()
        self.invite()
        profile = self.providers[1].escort_profile
        profile.status = 'OFFLINE'
        profile.save(update_fields=['status'])
        self.auth(self.accounts[1])
        result = self.client.get('/api/orders/grab-team/invitations/')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.data['data'][0]['order_visible'])
        self.assertNotIn('order_id', result.data['data'][0])

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_incomplete_team_does_not_block_timeout_refund(self):
        from orders.models import OrderGrabPoolSnapshot, OrderGrabRound
        self.prepare()
        self.invite()
        pool = OrderGrabPoolSnapshot.objects.get(order=self.order)
        OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(
            original_total_deadline=pool.first_deadline, total_deadline=pool.first_deadline)
        OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
        Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=pool.first_deadline)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'CANCELLED')
        self.wallet.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance), ('CANCELLED', 100000))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_persisted_withdrawal_is_applied_before_allocating_team(self):
        from orders.grab_receipt_probe import record_intent
        from orders.models import OrderGrabTeamDraft
        self.prepare()
        team_id = self.invite()
        self.auth(self.accounts[1])
        result = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                  {'client_request_id': 'confirm-before-retry'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        time.sleep(0.01)
        team = OrderGrabTeamDraft.objects.get(pk=team_id)
        receipt = record_intent(team.round_id, self.accounts[1].pk, 'WITHDRAW', 'withdraw-retry',
                                {'team_id': team_id}, team_flow=True)
        self.assertEqual(receipt.state, 'BEFORE')
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'WAIT_APPLY')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        retried = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/withdraw/',
                                   {'client_request_id': 'withdraw-retry'}, format='json')
        self.assertEqual(retried.status_code, 200, retried.data)
        self.assertEqual(resolve(self.order.pk), 'PARKED')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_parked_disqualified_team_can_form_again_after_eligibility_recovers(self):
        from orders.grab_receipt_probe import record_intent
        from orders.models import OrderGrabTeamDraft
        self.prepare()
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'PARKED')
        team_id = self.invite()
        team = OrderGrabTeamDraft.objects.get(pk=team_id)
        result = record_intent(team.round_id, self.accounts[1].pk, 'CONFIRM', 'before-disqualification',
                               {'team_id': team_id}, team_flow=True)
        self.assertEqual(result.state, 'BEFORE')
        profile = self.providers[0].escort_profile
        profile.status = 'OFFLINE'
        profile.save(update_fields=['status'])
        self.assertEqual(resolve(self.order.pk), 'PARKED')
        team.refresh_from_db()
        self.assertEqual(team.status, 'DISQUALIFIED')
        self.assertFalse(team.occupied_seats.exists())
        profile.status = 'AVAILABLE'
        profile.save(update_fields=['status'])
        self.auth(self.accounts[0])
        second = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'invite-after-recovery'}, format='json')
        self.assertEqual(second.status_code, 200, second.data)
        self.auth(self.accounts[1])
        result = self.client.post(f"/api/orders/grab-team/invitations/{second.data['data']['team_id']}/confirm/",
                                  {'client_request_id': 'confirm-after-recovery'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.GRABBED)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_staff_arranged_and_staff_created_double_orders_enter_team_pool(self):
        from orders.models import OrderGrabPoolSnapshot
        self.prepare()
        self.auth(self.boss_account)
        arranged = self.create(dispatch_mode='BOSS_SELECT', dispatch_route='STAFF',
                               client_request_id='boss-staff-team')
        self.assertEqual(arranged.status_code, 200, arranged.data)
        arranged_order = Order.objects.get(pk=arranged.data['data']['id'])
        self.assertEqual(OrderGrabPoolSnapshot.objects.get(order=arranged_order).required_provider_count, 2)

        staff_user = make_console_user(['order:view', 'order:dispatch'])
        staff = self.link(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        self.auth(staff)
        result = self.client.post('/api/admin/orders/dispatch/', {'customer_id': self.boss_account.pk,
            'service_id': self.service.pk, 'escort_mode': 'DOUBLE', 'client_request_id': 'staff-team-entry'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['code'], 0, result.data)
        self.order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual(OrderGrabPoolSnapshot.objects.get(order=self.order).required_provider_count, 2)
        self.assertFalse(self.order.requires_boss_selection)
        time.sleep(0.01)
        team_id = self.invite()
        self.auth(self.accounts[1])
        confirmed = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id': 'staff-team-confirm'}, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertTrue(self.order.requires_boss_selection)

    @override_settings(GAME_COMMISSION_PAYMENT_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_double_assignment_uses_payment_time_game_commission_snapshot(self):
        self.prepare(with_game=True)
        team_id = self.invite()
        self.auth(self.accounts[1])
        response = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id': 'game-snapshot-confirm'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.config.rules = {'companion': 11, 'grind': 50}
        self.config.version = 2
        self.config.save(update_fields=['rules', 'version'])
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertEqual(list(self.order.providers.values_list('commission_rate', flat=True)), [30, 30])
        self.assertTrue(all(row.game_commission_snapshot['version'] == 1 for row in self.order.providers.all()))
