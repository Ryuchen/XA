"""Grab and timeout refunds serialize with payroll in disposable MySQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from console.grab_config import save_grab_window
from console.payroll_event_barrier import lock_payroll_events
from console.payroll_models import PayrollEventBarrier
from orders.models import Order, OrderGrabPoolSnapshot, OrderGrabRound, OrderGrabReceiptProbe, OrderRefund
from orders.tasks import auto_cancel_pending_order, open_committed_grab_order
from orders.tests import test_grab_single_flow
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL row locks')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True,
    GAME_COMMISSION_PAYMENT_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabTimeoutPayrollBarrierMySQLTest(TransactionTestCase):
    def prepare_flow(self):
        require_disposable_mysql(connection)
        Store.objects.get_or_create(slug='shenglang', defaults={'name': '测试门店', 'entity_prefix': 'SL',
            'database_alias': 'default', 'status': Store.Status.ACTIVE})
        store = resolve_store()
        with store_context(store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            flow = test_grab_single_flow.GrabSingleFlowTest(methodName='test_disabled_clock_keeps_existing_round_from_legacy_assignment_or_refund')
            flow.client = APIClient(HTTP_HOST='sl.esports.club')
            self.addCleanup(flow.doCleanups)
            flow.setUp()
            flow.prepare()
        return store, flow

    def buy_for_committed_opening(self, flow, request_id, *, window=30):
        save_grab_window(window, 1, lambda *_: None)
        with patch.object(open_committed_grab_order, 'delay') as enqueue:
            purchase = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=request_id)
        self.assertEqual(purchase.data['code'], 0, purchase.data)
        order = Order.objects.get(pk=purchase.data['data']['id'])
        enqueue.assert_called_once_with(order.pk)
        self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=order).exists())
        return order

    def prepare_waiting_team_invite(self, flow, request_id, *, window=30):
        from orders.grab_receipt_probe import record_intent
        from orders.models import OrderGrabTeamDraft

        flow.service.fulfillment_provider_count = 2
        flow.service.save(update_fields=['fulfillment_provider_count'])
        order = self.buy_for_committed_opening(flow, request_id, window=window)
        with patch('orders.grab_round_opening._observe_activation') as observe:
            self.assertEqual(open_committed_grab_order.run(order.pk), 'WAIT_OPENING')
        observe.assert_called_once()
        round_ = OrderGrabRound.objects.get(pool__order=order)
        # Let the conservative 50 ms lower bound follow the persisted round start.
        sleep(.075)
        captain, partner = flow.accounts
        invite = record_intent(round_.pk, captain.pk, 'INVITE', request_id + '-invite',
                               {'partner_id': partner.pk}, team_flow=True, actor_snapshot=captain)
        receipt = OrderGrabReceiptProbe.objects.get(pk=invite.receipt_id)
        self.assertEqual((receipt.state, receipt.opening_prepared_observed), ('WAIT_OPENING', True))
        self.assertIsNotNone(receipt.upper_observed_at)
        team = OrderGrabTeamDraft.objects.get(round=round_, captain=captain, request_key=receipt.request_key)
        return order, round_, team, receipt, observe.call_args.args

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_formal_pool_queries_are_bounded_for_committed_rounds(self):
        from orders.grab_round_opening import opening_bounds

        store, flow = self.prepare_flow()
        with store_context(store):
            save_grab_window(30, 1, lambda *_: None)

            def open_order(index):
                flow.auth(flow.boss_account)
                with patch.object(open_committed_grab_order, 'delay') as enqueue:
                    purchase = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=f'pool-query-{index}')
                self.assertEqual(purchase.data['code'], 0, purchase.data)
                order = Order.objects.get(pk=purchase.data['data']['id'])
                enqueue.assert_called_once_with(order.pk)
                self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
                return order

            def read_pool(expected):
                flow.auth(flow.accounts[0])
                with CaptureQueriesContext(connection) as queries:
                    result = flow.client.get('/api/orders/orders/?status=pending')
                    self.assertEqual(result.data['code'], 0, result.data)
                rows = {row['id']: row for row in result.data['data']}
                self.assertEqual(set(rows), expected)
                for row in rows.values():
                    self.assertIs(row['pending_deadline_expired'], False)
                    self.assertIn('GRAB', row['allowed_actions'])
                return len(queries), sum('UTC_TIMESTAMP(6)' in item['sql'] for item in queries)

            def wait_visible(order):
                from orders.grab_receipt_probe import _db_clock
                upper = opening_bounds(OrderGrabRound.objects.get(pool__order=order))[1]
                deadline = monotonic() + 3
                while _db_clock()[0] <= upper + timedelta(microseconds=1):
                    self.assertLess(monotonic(), deadline)
                    sleep(.005)

            first = open_order(0)
            wait_visible(first)
            small, first_clocks = read_pool({first.pk})
            orders = [first] + [open_order(index) for index in range(1, 20)]
            wait_visible(orders[-1])
            large, clocks = read_pool({order.pk for order in orders})
            print(f'Formal pool read: one={small}/{first_clocks} clock, twenty={large}/{clocks} clock', flush=True)
            self.assertLessEqual(large, small + 1)
            self.assertEqual((first_clocks, clocks), (1, 1))

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_formal_pool_prepared_cutoff_and_damaged_opening_evidence(self):
        from orders.grab_round_opening import _observe_activation

        store, flow = self.prepare_flow()
        with store_context(store):
            flow.auth(flow.boss_account)
            order = self.buy_for_committed_opening(flow, 'pool-prepared-read')
            with patch('orders.grab_round_opening._observe_activation') as activate:
                self.assertEqual(open_committed_grab_order.run(order.pk), 'WAIT_OPENING')
            activate.assert_called_once()
            evidence = OrderGrabRound.objects.get(pool__order=order).opening_evidence
            self.assertEqual(evidence.state, 'PREPARED')
            sleep(.075)
            flow.auth(flow.accounts[0])
            result = flow.client.get('/api/orders/orders/?status=pending')
            row, = result.data['data']
            self.assertEqual(row['id'], order.pk)
            self.assertIsNone(row['pending_deadline_expired'])
            self.assertNotIn('GRAB', row['allowed_actions'])
            # The provisional order timestamp cannot substitute for the missing activation.
            Order.objects.filter(pk=order.pk).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
            self.assertEqual(flow.client.get('/api/orders/orders/?status=pending').data['data'], [])
            _observe_activation(*activate.call_args.args)
            evidence.refresh_from_db()
            self.assertEqual(evidence.state, 'OBSERVED')
            sleep(.075)
            result = flow.client.get('/api/orders/orders/?status=pending')
            row, = result.data['data']
            self.assertIs(row['pending_deadline_expired'], False)
            self.assertIn('GRAB', row['allowed_actions'])
            with CaptureQueriesContext(connection) as queries:
                detail = flow.client.get(f'/api/orders/orders/{order.pk}/')
                self.assertEqual(detail.status_code, 200, detail.data)
            self.assertIn('GRAB', detail.data['data']['allowed_actions'])
            self.assertIs(detail.data['data']['pending_deadline_expired'], False)
            self.assertEqual(sum('SELECT @@server_uuid' in item['sql'] for item in queries), 0)
            with patch('orders.grab_pool_clock_read.formal_pool_times', side_effect=DatabaseError('clock read interrupted')):
                failed = flow.client.get('/api/orders/orders/?status=pending')
            self.assertEqual(failed.status_code, 503)
            self.assertNotIn('data', failed.data)
            type(evidence).objects.filter(pk=evidence.pk).update(clock_evidence={})
            self.assertEqual(flow.client.get('/api/orders/orders/?status=pending').data['data'], [])
            self.assertEqual(flow.client.get(f'/api/orders/orders/{order.pk}/').status_code, 404)
            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.PENDING)
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_single_reopen_near_the_previous_cutoff_keeps_a_full_window(self):
        self.assert_near_cutoff_reopen(team=False)

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True)
    def test_team_reopen_near_the_previous_cutoff_keeps_a_full_window(self):
        self.assert_near_cutoff_reopen(team=True)

    def assert_near_cutoff_reopen(self, *, team):
        from orders.grab_clock_contract import sample
        from orders.grab_pool_clock_read import formal_pool_times
        from orders.grab_receipt_probe import _db_clock, record_intent
        from orders.grab_round_opening import cutoff_bounds, opening_bounds
        from orders.tasks import finalize_single_grab, finalize_team_grab

        def wait_past(bound):
            limit = monotonic() + 10
            while _db_clock()[0] <= bound:
                self.assertLess(monotonic(), limit)
                sleep(.005)

        def enter(round_, key):
            captain, partner = flow.accounts
            if team:
                invitation = record_intent(round_.pk, captain.pk, 'INVITE', key + '-invite',
                    {'partner_id': partner.pk}, team_flow=True, actor_snapshot=captain)
                self.assertEqual(invitation.state, 'BEFORE')
                draft = round_.team_drafts.get(captain=captain, request_key=key + '-invite')
                result = record_intent(round_.pk, partner.pk, 'CONFIRM', key + '-confirm',
                    {'team_id': draft.pk}, team_flow=True, actor_snapshot=partner)
            else:
                result = record_intent(round_.pk, partner.pk, 'SINGLE', key, {},
                    single_flow=True, actor_snapshot=partner)
            self.assertEqual(result.state, 'BEFORE')

        store, flow = self.prepare_flow()
        # Make overlapping bounds reproducible while keeping every SQL sample real.
        precision = timedelta(milliseconds=500)
        finish = finalize_team_grab if team else finalize_single_grab
        with (store_context(store), patch('orders.grab_team_flow.enqueue'),
              patch('orders.grab_clock_contract.TOLERANCE_NS', 500_000_000)):
            if team:
                flow.service.fulfillment_provider_count = 2
                flow.service.save(update_fields=['fulfillment_provider_count'])
            order = self.buy_for_committed_opening(flow, 'near-cutoff-payment', window=4)
            self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
            first = order.grab_pool_snapshot.rounds.get(number=1)
            wait_past(opening_bounds(first)[1] + precision)
            enter(first, 'near-cutoff-first')
            profile = flow.providers[1].escort_profile
            available = profile.status
            profile.status = 'OFFLINE'
            profile.save(update_fields=['status'])
            previous_cutoff = cutoff_bounds(first, 'OPEN')
            wait_past(previous_cutoff[1] + timedelta(microseconds=1))
            self.assertEqual(finish.run(order.pk, first.pk), 'REOPENED')
            second = order.grab_pool_snapshot.rounds.get(number=2)
            raw_lower, raw_upper = opening_bounds(second)
            self.assertLess(raw_lower, previous_cutoff[1])
            new_cutoff = cutoff_bounds(second, 'OPEN')
            watermark = first.cutoff_watermark
            seal_lower = watermark.observed_at - timedelta(microseconds=watermark.precision_us)
            self.assertGreater(seal_lower, previous_cutoff[1])
            self.assertEqual(new_cutoff[0], seal_lower + timedelta(seconds=4))
            self.assertEqual(new_cutoff[1], raw_upper + timedelta(seconds=4))
            self.assertEqual(cutoff_bounds(first, 'OPEN'), previous_cutoff)
            self.assertEqual(opening_bounds(second), (raw_lower, raw_upper))
            wait_past(raw_upper + precision)
            timing = formal_pool_times([order.pk], profile, sample())[order.pk]
            self.assertEqual((timing.round_id, timing.visible_at, timing.expired), (second.pk, raw_upper, False))
            with transaction.atomic():
                type(watermark).objects.filter(pk=watermark.pk).update(receipt_digest='0' * 64)
                with self.assertRaisesMessage(ValueError, '重开时间与上轮截止区间交叉'):
                    cutoff_bounds(second, 'OPEN')
                self.assertIsNone(formal_pool_times([order.pk], profile, sample())[order.pk].expired)
                transaction.set_rollback(True)
            with transaction.atomic():
                evidence = second.opening_evidence
                type(evidence).objects.filter(pk=evidence.pk).update(
                    activation_lower_at=evidence.activation_lower_at - timedelta(seconds=1),
                    activation_upper_at=evidence.activation_upper_at - timedelta(seconds=1),
                )
                self.assertLess(opening_bounds(second)[1], previous_cutoff[1])
                with self.assertRaisesMessage(ValueError, '重开时间与上轮截止区间交叉'):
                    cutoff_bounds(second, 'OPEN')
                transaction.set_rollback(True)
            profile.status = available
            profile.save(update_fields=['status'])
            enter(second, 'near-cutoff-second')
            wait_past(new_cutoff[1] + timedelta(microseconds=1))
            self.assertEqual(finish.run(order.pk, first.pk), 'STALE_ROUND')
            self.assertEqual(finish.run(order.pk, second.pk), 'ASSIGNED')
            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.GRABBED)
            self.assertEqual(order.providers.count(), 2 if team else 1)
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_single_reopen_replays_the_original_request_before_accepting_a_new_one(self):
        from orders.grab_receipt_probe import _db_clock
        from orders.grab_round_opening import cutoff_bounds, opening_bounds
        from orders.tasks import check_pending_timeouts, finalize_single_grab

        def wait_past(bound):
            deadline = monotonic() + 8
            while _db_clock()[0] <= bound + timedelta(milliseconds=60):
                self.assertLess(monotonic(), deadline)
                sleep(.025)

        store, flow = self.prepare_flow()
        with store_context(store), patch('orders.grab_single_flow.enqueue') as enqueue:
            order = self.buy_for_committed_opening(flow, 'reopen-single-payment', window=2)
            self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
            first = OrderGrabRound.objects.get(pool__order=order, number=1)
            wait_past(opening_bounds(first)[1])
            path = f'/api/orders/orders/{order.pk}/grab/'
            runner_up, winner = flow.accounts
            old_key = 'reopen-single-original'
            flow.auth(runner_up)
            first_response = flow.client.post(path, {'client_request_id': old_key}, format='json')
            self.assertEqual(first_response.status_code, 200, first_response.data)
            first_id = first_response.data['data']['receipt_id']
            flow.auth(winner)
            submitted = flow.client.post(path, {'client_request_id': 'reopen-single-first'}, format='json')
            self.assertEqual(submitted.status_code, 200, submitted.data)
            self.assertTrue(enqueue.call_args_list)
            self.assertTrue(all(call.kwargs.get('expected_round_id') == first.pk for call in enqueue.call_args_list))
            profile = flow.providers[1].escort_profile
            profile.status = 'OFFLINE'
            profile.save(update_fields=['status'])
            wait_past(cutoff_bounds(first, 'OPEN')[1])
            enqueue.reset_mock()
            self.assertEqual(finalize_single_grab.run(order.pk, first.pk), 'REOPENED')
            second = OrderGrabRound.objects.get(pool__order=order, number=2)
            self.assertTrue(second.simultaneous_visibility)
            wait_past(opening_bounds(second)[1])

            flow.auth(runner_up)
            replay = flow.client.post(path, {'client_request_id': old_key}, format='json')
            self.assertEqual(replay.data.get('data'), {'receipt_id': first_id, 'status': 'NOT_WON'}, replay.data)
            self.assertFalse(second.receipt_probes.exists())
            self.assertTrue(enqueue.call_args_list)
            self.assertTrue(all(call.kwargs.get('expected_round_id') == second.pk for call in enqueue.call_args_list))
            self.assertEqual(finalize_single_grab.run(order.pk, first.pk), 'STALE_ROUND')

            fresh = flow.client.post(path, {'client_request_id': 'reopen-single-new'}, format='json')
            self.assertEqual(fresh.status_code, 200, fresh.data)
            fresh_id = fresh.data['data']['receipt_id']
            self.assertNotEqual(fresh_id, first_id)
            self.assertEqual(list(second.receipt_probes.values_list('pk', flat=True)), [fresh_id])
            wait_past(cutoff_bounds(second, 'OPEN')[1])
            due_replay = flow.client.post(path, {'client_request_id': old_key}, format='json')
            self.assertEqual(due_replay.data.get('data'), replay.data['data'], due_replay.data)
            with patch.object(finalize_single_grab, 'delay') as scheduled:
                check_pending_timeouts.run()
            scheduled.assert_called_once_with(order.pk, second.pk)
            self.assertEqual(finalize_single_grab.run(order.pk, first.pk), 'STALE_ROUND')
            self.assertEqual(finalize_single_grab.run(order.pk), 'STALE_ROUND')
            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.PENDING)
            self.assertEqual(finalize_single_grab.run(order.pk, second.pk), 'ASSIGNED')
            order.refresh_from_db()
            self.assertEqual((order.status, order.provider_account_id), (Order.Status.GRABBED, runner_up.pk))
            self.assertEqual(list(order.providers.values_list('provider_account_id', flat=True)), [runner_up.pk])
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])
            repeated = flow.client.post(path, {'client_request_id': old_key}, format='json')
            self.assertEqual(repeated.data.get('data'), replay.data['data'], repeated.data)
            # Model a new membership without running the separate retirement workflow.
            type(runner_up).objects.filter(pk=runner_up.pk).update(business_cycle=runner_up.business_cycle + 1)
            runner_up.refresh_from_db()
            flow.auth(runner_up)
            for key in (old_key, 'reopen-single-new'):
                repeated = flow.client.post(path, {'client_request_id': key}, format='json')
                self.assertEqual((repeated.status_code, repeated.data.get('errorCode')),
                                 (409, 'REQUEST_ID_CONFLICT'), repeated.data)
            self.assertEqual(OrderGrabReceiptProbe.objects.filter(round__pool__order=order).count(), 3)

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True)
    def test_formal_team_receipts_prove_sequence_when_commit_intervals_overlap(self):
        from orders.grab_bound_ranking import preview
        from orders.grab_receipt_probe import _db_clock, apply_team_withdrawal, record_intent
        from orders.grab_round_opening import cutoff_bounds, opening_bounds

        def wait_for_clock(bound):
            deadline = monotonic() + 10
            while _db_clock()[0] <= bound:
                self.assertLess(monotonic(), deadline)
                sleep(.025)

        def assert_overlapping(first, second):
            first_upper = first.upper_observed_at + timedelta(microseconds=first.precision_us)
            second_lower = second.lower_at - timedelta(microseconds=second.precision_us)
            self.assertGreaterEqual(first_upper, second_lower)

        store, flow = self.prepare_flow()
        # Widen the uncertainty budget so the test reliably covers overlap.
        # All clock observations and receipt commits still use real MySQL.
        with (store_context(store), patch('orders.grab_team_flow.enqueue'),
              patch('orders.grab_clock_contract.TOLERANCE_NS', 500_000_000)):
            flow.service.fulfillment_provider_count = 2
            flow.service.save(update_fields=['fulfillment_provider_count'])
            order = self.buy_for_committed_opening(flow, 'team-causal', window=4)
            self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
            round_ = OrderGrabRound.objects.get(pool__order=order)
            wait_for_clock(opening_bounds(round_)[1] + timedelta(milliseconds=500))
            captain, partner = flow.accounts
            invited = record_intent(round_.pk, captain.pk, 'INVITE', 'team-causal-invite',
                                    {'partner_id': partner.pk}, team_flow=True, actor_snapshot=captain)
            self.assertEqual(invited.state, 'BEFORE')
            team = round_.team_drafts.get(captain=captain, request_key='team-causal-invite')
            confirmed = record_intent(round_.pk, partner.pk, 'CONFIRM', 'team-causal-confirm',
                                      {'team_id': team.pk}, team_flow=True, actor_snapshot=partner)
            self.assertEqual(confirmed.state, 'BEFORE')
            # Leave the withdrawal unapplied so ranking can read the same team first.
            with patch('orders.grab_receipt_probe.apply_team_withdrawal') as deferred:
                withdrawn = record_intent(round_.pk, captain.pk, 'WITHDRAW', 'team-causal-withdraw',
                                          {'team_id': team.pk}, team_flow=True)
            deferred.assert_called_once()
            self.assertEqual(withdrawn.state, 'BEFORE')
            invitation, confirmation, withdrawal = [OrderGrabReceiptProbe.objects.get(pk=receipt.receipt_id)
                                                     for receipt in (invited, confirmed, withdrawn)]
            assert_overlapping(invitation, confirmation)
            assert_overlapping(confirmation, withdrawal)
            wait_for_clock(cutoff_bounds(round_, 'OPEN')[1] + timedelta(microseconds=1))
            for receipt, changed in ((invitation, {'cutoff_at': None}),
                                     (confirmation, {'actor_business_cycle': confirmation.actor_business_cycle + 1})):
                with self.subTest(receipt=receipt.kind), transaction.atomic():
                    OrderGrabReceiptProbe.objects.filter(pk=receipt.pk).update(**changed)
                    result = preview(round_.pk, [team.pk])
                    self.assertEqual((result.status, result.review_candidate_ids, result.can_assign),
                                     ('REVIEW', (team.pk,), False))
                    with self.assertRaisesMessage(ValueError, '退出登记或先前组队证据无法核对'):
                        apply_team_withdrawal(order, round_, withdrawal)
                    self.assertEqual(team.occupied_seats.count(), 2)
                    transaction.set_rollback(True)
            with transaction.atomic():
                result = preview(round_.pk, [team.pk])
            self.assertEqual((result.status, result.winner_id, result.can_assign),
                             ('POTENTIAL_WINNER', team.pk, False))
            with transaction.atomic():
                locked_order = Order.objects.select_for_update().get(pk=order.pk)
                apply_team_withdrawal(locked_order, round_, withdrawal)
            replay = record_intent(round_.pk, captain.pk, 'WITHDRAW', 'team-causal-withdraw',
                                   {'team_id': team.pk}, team_flow=True)
            self.assertEqual((replay.receipt_id, replay.state, replay.replayed), (withdrawal.pk, 'BEFORE', True))
            team.refresh_from_db()
            self.assertEqual((team.status, team.withdrawal_receipt_id, team.occupied_seats.count()),
                             ('WITHDRAWN', withdrawal.pk, 0))
            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.PENDING)
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True,
                       GRAB_INVITE_NOTICE_RECOVERY_ENABLED=True)
    def test_pending_activation_and_review_keep_original_invite_recoverable(self):
        from orders.grab_review import mark_round_review
        from orders.grab_round_opening import _observe_activation
        from orders.tasks import recover_grab_opening_waiters
        from orders.tests.factories import make_console_user
        from site_messages.models import GrabInviteNoticeDelivery
        from site_messages.tasks import recover_grab_invites

        store, flow = self.prepare_flow()
        with (store_context(store), patch('orders.grab_team_flow.enqueue') as enqueue,
              patch('site_messages.notifier.notify_messages_after_commit') as notify):
            order, round_, _team, receipt, activation = self.prepare_waiting_team_invite(flow, 'live-invite')
            enqueue.reset_mock()
            recover_grab_opening_waiters.run(round_.pk)
            receipt.refresh_from_db()
            self.assertEqual(receipt.state, 'WAIT_OPENING')
            state = round_.team_state
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order.pk)
                mark_round_review(state, '开池激活回执待核对')
            with patch('orders.grab_round_opening._resume_waiting'):
                _observe_activation(*activation)
            with override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=False):
                recover_grab_opening_waiters.run(round_.pk)
                receipt.refresh_from_db()
                self.assertEqual(receipt.state, 'WAIT_OPENING')
            recover_grab_opening_waiters.run(round_.pk)
            receipt.refresh_from_db()
            self.assertEqual((receipt.state, receipt.pass_evidence.state), ('BEFORE', 'KNOWN'))
            recover_grab_invites.run()
            self.assertFalse(GrabInviteNoticeDelivery.objects.filter(receipt=receipt).exists())
            enqueue.assert_not_called()
            notify.assert_not_called()
            staff = make_console_user(['order:view', 'order:dispatch'])
            account = flow.link(staff)
            staff.admin_membership.account = account
            staff.admin_membership.save(update_fields=['account'])
            flow.auth(account)
            with override_settings(GRAB_INVITE_NOTICE_RECOVERY_ENABLED=False):
                result = flow.client.post(f'/api/admin/grab-reviews/{order.pk}/actions/', {
                    'action': 'RESUME', 'reason': '已核对原激活凭证', 'client_request_id': 'resume-live-invite',
                }, format='json')
            self.assertEqual(result.data.get('code'), 0, result.data)
            self.assertEqual(recover_grab_invites.run(), 0)
            notice = GrabInviteNoticeDelivery.objects.get(receipt=receipt)
            self.assertEqual(notice.message.recipient_account_id, flow.accounts[1].pk)
            notify.assert_called_once()

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True,
                       GRAB_INVITE_NOTICE_RECOVERY_ENABLED=True)
    def test_superseded_opening_receipts_keep_time_facts_without_reopening_old_team(self):
        from orders.grab_cutoff_watermark import seal
        from orders.grab_receipt_probe import _db_clock, record_intent
        from orders.grab_round_opening import _observe_activation, cutoff_bounds
        from orders.grab_team_flow import _reopen
        from orders.tasks import finalize_team_grab, recover_grab_opening_waiters
        from site_messages.models import GrabInviteNoticeDelivery
        from site_messages.tasks import recover_grab_invites

        store, flow = self.prepare_flow()
        with (store_context(store), patch('orders.grab_team_flow.enqueue') as enqueue,
              patch('site_messages.notifier.notify_messages_after_commit') as notify):
            order, round_, team, invite, activation = self.prepare_waiting_team_invite(flow, 'old-invite', window=2)
            sleep(.12)
            withdraw = record_intent(round_.pk, flow.accounts[0].pk, 'WITHDRAW', 'old-withdraw',
                                     {'team_id': team.pk}, team_flow=True)
            self.assertEqual(OrderGrabReceiptProbe.objects.get(pk=withdraw.receipt_id).state, 'WAIT_OPENING')
            with patch('orders.grab_round_opening._resume_waiting'):
                _observe_activation(*activation)
                cutoff = cutoff_bounds(round_, 'OPEN')[1]
                deadline = monotonic() + 6
                while _db_clock()[0] <= cutoff + timedelta(milliseconds=60):
                    self.assertLess(monotonic(), deadline)
                    sleep(.025)
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=order.pk)
                    _reopen(order, round_.team_state, _db_clock()[0])
            # Reproduce an old state left open by an interrupted historical write.
            type(round_.team_state).objects.filter(pk=round_.team_state.pk).update(status='OPEN')
            enqueue.reset_mock()
            recover_grab_opening_waiters.run(round_.pk)
            recover_grab_opening_waiters.run(round_.pk)
            self.assertEqual(list(round_.receipt_probes.order_by('pk').values_list('kind', 'state')),
                             [('INVITE', 'BEFORE'), ('WITHDRAW', 'BEFORE')])
            team.refresh_from_db()
            self.assertEqual((team.status, team.withdrawal_receipt_id, team.occupied_seats.count()),
                             ('INVITING', None, 1))
            recover_grab_invites.run()
            notice = GrabInviteNoticeDelivery.objects.get(receipt=invite)
            self.assertIsNone(notice.message_id)
            self.assertTrue(notice.skipped_reason)
            notify.assert_not_called()
            enqueue.assert_not_called()
            flow.auth(flow.accounts[1])
            result = flow.client.get('/api/orders/grab-team/invitations/')
            self.assertNotIn(team.pk, [row['team_id'] for row in result.data['data']])
            flow.auth(flow.accounts[0])
            result = flow.client.post(f'/api/orders/grab-team/invitations/{team.pk}/withdraw/', {
                'client_request_id': 'old-withdraw',
            }, format='json')
            self.assertEqual(result.status_code, 409, result.data)
            self.assertEqual(finalize_team_grab.run(order.pk, expected_round_id=round_.pk), 'STALE_ROUND')
            self.assertEqual(seal(order.pk, team=True, expected_round_id=round_.pk), 'STALE_ROUND')
            team.refresh_from_db()
            self.assertIsNone(team.withdrawal_receipt_id)
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True)
    def test_periodic_recovery_classifies_terminal_orders_without_republishing(self):
        from orders.grab_round_opening import _observe_activation
        from orders.tasks import check_pending_timeouts, recover_grab_opening_waiters
        from orders.tests.factories import make_console_user

        store, flow = self.prepare_flow()
        with (store_context(store), patch('orders.grab_team_flow.enqueue') as enqueue,
              patch('site_messages.grab_invite.schedule') as notify):
            order, round_, _team, receipt, activation = self.prepare_waiting_team_invite(flow, 'cancelled-invite')
            with patch('orders.grab_round_opening._resume_waiting'):
                _observe_activation(*activation)
            staff = make_console_user(['order:view', 'order:dispatch', 'order:refund'])
            account = flow.link(staff)
            staff.admin_membership.account = account
            staff.admin_membership.save(update_fields=['account'])
            flow.auth(account)
            cancelled = flow.client.post(f'/api/admin/grab-reviews/{order.pk}/actions/', {
                'action': 'CANCEL', 'reason': '取消隔离测试订单', 'client_request_id': 'cancel-by-staff',
            }, format='json')
            self.assertEqual(cancelled.data.get('code'), 0, cancelled.data)
            enqueue.reset_mock()
            with patch.object(recover_grab_opening_waiters, 'delay') as queue:
                check_pending_timeouts.run()
                queue.assert_called_once_with(round_.pk)
            recover_grab_opening_waiters.run(round_.pk)
            recover_grab_opening_waiters.run(round_.pk)
            receipt.refresh_from_db()
            order.refresh_from_db()
            flow.wallet.refresh_from_db()
            self.assertEqual((receipt.state, order.status, flow.wallet.balance), ('BEFORE', 'CANCELLED', 100000))
            self.assertCountEqual(order.transactions.values_list('tx_type', 'amount'),
                                  [('PAY', -5000), ('REFUND', 5000)])
            enqueue.assert_not_called()
            notify.assert_not_called()

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True)
    def test_current_review_releases_only_the_seat_of_the_proven_late_confirmation(self):
        from orders.grab_receipt_probe import _db_clock, record_intent
        from orders.grab_review import mark_round_review
        from orders.grab_round_opening import _observe_activation, cutoff_bounds
        from orders.grab_team_flow import _release_late_team

        store, flow = self.prepare_flow()
        with (store_context(store), patch('orders.grab_team_flow.enqueue'),
              patch('site_messages.grab_invite.schedule')):
            order, round_, team, invite, activation = self.prepare_waiting_team_invite(flow, 'late-confirm', window=2)
            _observe_activation(*activation)
            invite.refresh_from_db()
            self.assertEqual(invite.state, 'BEFORE')
            cutoff = cutoff_bounds(round_, 'OPEN')[1]
            deadline = monotonic() + 6
            while _db_clock()[0] <= cutoff + timedelta(milliseconds=60):
                self.assertLess(monotonic(), deadline)
                sleep(.025)
            partner = flow.accounts[1]
            with patch('orders.grab_receipt_probe._release_late_team_receipt'):
                late = record_intent(round_.pk, partner.pk, 'CONFIRM', 'late-confirm-receipt',
                                     {'team_id': team.pk}, team_flow=True, actor_snapshot=partner)
            self.assertEqual(late.state, 'AFTER')
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order.pk)
                mark_round_review(round_.team_state, '核对组队确认时间')
            _release_late_team(team.pk, confirmation=True, receipt_id=invite.pk)
            team.refresh_from_db()
            self.assertEqual((team.status, team.occupied_seats.count()), ('UNVERIFIED', 2))
            for _ in range(2):
                _release_late_team(team.pk, confirmation=True, receipt_id=late.receipt_id)
            team.refresh_from_db()
            self.assertEqual(team.status, 'INVITING')
            self.assertEqual(list(team.occupied_seats.values_list('account_id', flat=True)), [flow.accounts[0].pk])
            self.assertEqual(OrderGrabReceiptProbe.objects.get(pk=late.receipt_id).state, 'AFTER')
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    def configure_group_targets(self):
        from console.models import KookDispatchConfig
        from integrations.kook_config import encrypt_token
        from orders.tests.factories import configure_isolated_wecom

        KookDispatchConfig.objects.create(enabled=True, bot_token_ciphertext=encrypt_token('isolated-kook'),
            routes=[{'id': 'test', 'name': '验收群', 'enabled': True, 'channel_id': 'isolated',
                     'mention_role_ids': [], 'template': ''}])
        configure_isolated_wecom()

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_POOL_GROUP_DISPATCH_ENABLED=True)
    def test_group_recovery_rebuilds_lost_records_only_when_current_round_can_admit(self):
        from orders.dispatching import dispatch_preview
        from orders.grab_group_recovery import recover_due
        from orders.grab_review import mark_round_review
        from wallet.models import SystemConfig

        store, flow = self.prepare_flow()
        with (store_context(store), patch('orders.dispatching.send_task_safely') as enqueue,
              patch('orders.grab_group_recovery.send_task_safely') as recover):
            self.configure_group_targets()
            SystemConfig.objects.filter(key='provider_pass_delay_seconds_NONE').update(value='30')
            order = self.buy_for_committed_opening(flow, 'formal-group-missing-record')
            with (patch('orders.kook_dispatch.enqueue_kook_dispatch'),
                  patch('orders.dispatching.enqueue_wecom_dispatch')):
                self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
            order.refresh_from_db()
            state = OrderGrabRound.objects.get(pool__order=order).single_state
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order.pk)
                mark_round_review(state, '待核对原轮候选')
            with self.subTest('preview'):
                self.assertFalse(dispatch_preview(order)['can_send'])
            self.assertEqual(recover_due(), 0)
            self.assertFalse(order.kook_dispatches.exists() or order.wecom_dispatches.exists())
            enqueue.assert_not_called()
            recover.assert_not_called()
            state.status, state.reason = 'OPEN', ''
            state.save(update_fields=['status', 'reason'])
            self.assertEqual(recover_due(), 2)
            self.assertEqual(recover_due(), 0)
            self.assertEqual(enqueue.call_count, 2)
            self.assertEqual(order.kook_dispatches.get().pool_delivery_due_at,
                             order.wecom_dispatches.get().pool_delivery_due_at)
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_POOL_GROUP_DISPATCH_ENABLED=True)
    def test_group_sender_waits_for_current_round_state_and_original_payment_and_keeps_one_record(self):
        from orders.dispatching import deliver_dispatch, dispatch_preview
        from orders.grab_group_dispatch import current_time

        store, flow = self.prepare_flow()
        with (store_context(store), patch('orders.dispatching.send_task_safely'),
              patch('integrations.wecom.WecomClient.send_text') as send):
            self.configure_group_targets()
            order = self.buy_for_committed_opening(flow, 'formal-group-hold-send')
            self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
            order.refresh_from_db()
            record = order.wecom_dispatches.get()
            latest = OrderGrabRound.objects.get(pool__order=order)
            state, evidence, intent = latest.single_state, latest.opening_evidence, order.grab_opening_intent
            deadline = monotonic() + 5
            while current_time() < record.pool_delivery_due_at:
                self.assertLess(monotonic(), deadline)
                sleep(.01)

            def deliver():
                close_old_connections()
                try:
                    with store_context(store):
                        with connection.cursor() as cursor:
                            cursor.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                        return deliver_dispatch('WECOM', record.pk)
                finally:
                    connection.close()

            blocks = [
                (state, {'status': 'REVIEW', 'reason': '候选核对'}, {'status': 'OPEN', 'reason': ''}),
                (evidence, {'state': 'PREPARED', 'activation_upper_at': None},
                 {'state': 'OBSERVED', 'activation_upper_at': evidence.activation_upper_at}),
                (intent, {'state': 'REVIEW', 'reason': '付款时钟核对'}, {'state': 'OBSERVED', 'reason': ''}),
            ]
            with ThreadPoolExecutor(max_workers=1) as executor:
                for row, blocked, restored in blocks:
                    with self.subTest(model=type(row).__name__):
                        with transaction.atomic():
                            Order.objects.select_for_update().get(pk=order.pk)
                            pending = executor.submit(deliver)
                            deadline = monotonic() + 10
                            while not mysql_locking_statements(connection, Order._meta.db_table):
                                self.assertFalse(pending.done(), 'Sender finished before the order lock was released')
                                self.assertLess(monotonic(), deadline, 'Sender did not reach the order lock')
                                sleep(.05)
                            type(row).objects.filter(pk=row.pk).update(**blocked)
                        try:
                            self.assertEqual(pending.result(timeout=20), 'waiting:round_review')
                            self.assertFalse(dispatch_preview(order)['can_send'])
                            record.refresh_from_db()
                            self.assertEqual((record.status, record.attempts), ('PENDING', 0))
                            send.assert_not_called()
                        finally:
                            type(row).objects.filter(pk=row.pk).update(**restored)
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'sent:')
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'already_sent:')
            send.assert_called_once()
            self.assertEqual(list(order.wecom_dispatches.values_list('pk', 'attempts')), [(record.pk, 1)])
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_opening_redelivery_preserves_original_activation_even_if_the_clock_changed(self):
        from orders.grab_clock_contract import round_identity_valid

        store, flow = self.prepare_flow()
        with store_context(store):
            order = self.buy_for_committed_opening(flow, 'formal-open-replay')
            self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
            order.refresh_from_db()
            intent = order.grab_opening_intent
            original = OrderGrabRound.objects.get(pool__order=order)
            evidence = original.opening_evidence
            self.assertEqual(evidence.state, 'OBSERVED')
            self.assertTrue(round_identity_valid(original))
            payload = evidence.activation_clock_evidence
            due_at = order.auto_cancel_at
            paid_at = intent.commit_observed_at
            with self.subTest(replay='ordinary'):
                self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
            wrong_clock = (paid_at, intent.clock_precision_us, 'different-isolated-clock')
            with self.subTest(replay='different clock'):
                with patch('orders.grab_opening_intent._clock_observation', return_value=wrong_clock) as sample:
                    self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
                    sample.assert_not_called()
            evidence.refresh_from_db()
            intent.refresh_from_db()
            order.refresh_from_db()
            self.assertEqual((intent.state, intent.reason, evidence.state, evidence.activation_clock_evidence),
                             ('OBSERVED', '', 'OBSERVED', payload))
            self.assertEqual((intent.commit_observed_at, order.auto_cancel_at), (paid_at, due_at))
            self.assertTrue(round_identity_valid(original))
            self.assertEqual(OrderGrabRound.objects.filter(pool__order=order).count(), 1)
            self.assertEqual(list(order.transactions.values_list('pk', 'tx_type', 'amount')),
                             [(intent.payment_id, 'PAY', -5000)])
            flow.wallet.refresh_from_db()
            self.assertEqual(flow.wallet.balance, 95000)
            type(evidence).objects.filter(pk=evidence.pk).update(activation_clock_evidence={})
            self.assertEqual(open_committed_grab_order.run(order.pk), 'REVIEW')
            evidence.refresh_from_db()
            self.assertEqual((evidence.state, evidence.activation_clock_evidence), ('OBSERVED', {}))

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_opening_redelivery_leaves_missing_activation_for_review(self):
        from orders.grab_opening_intent import _clock_observation

        store, flow = self.prepare_flow()
        with store_context(store):
            order = self.buy_for_committed_opening(flow, 'formal-open-no-activation')
            with patch('orders.grab_round_opening._observe_activation') as observe:
                result = open_committed_grab_order.run(order.pk)
                observe.assert_called_once()
            original = OrderGrabRound.objects.get(pool__order=order).opening_evidence
            self.assertEqual((original.state, original.activation_upper_at), ('PREPARED', None))
            with patch('orders.grab_opening_intent._clock_observation', wraps=_clock_observation) as sample:
                replay = open_committed_grab_order.run(order.pk)
                sample.assert_not_called()
            self.assertEqual((result, replay), ('WAIT_OPENING', 'WAIT_OPENING'))
            original.refresh_from_db()
            self.assertEqual((original.state, original.activation_upper_at), ('PREPARED', None))
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True)
    def test_initial_opening_waits_for_a_worker_with_the_formal_clock(self):
        store, flow = self.prepare_flow()
        with store_context(store):
            order = self.buy_for_committed_opening(flow, 'formal-open-worker-clock-disabled')
            with override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=False):
                self.assertEqual(open_committed_grab_order.run(order.pk), 'DISABLED')
            self.assertEqual(order.grab_opening_intent.state, 'PENDING')
            self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=order).exists())
            with patch('orders.grab_pool_snapshot.initialize_entry', side_effect=ValueError('pool setup unavailable')):
                self.assertEqual(open_committed_grab_order.run(order.pk), 'RETRY')
            intent = order.grab_opening_intent
            intent.refresh_from_db()
            self.assertEqual(intent.state, 'OBSERVED')
            paid_at = intent.commit_observed_at
            self.assertIsNotNone(paid_at)
            self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=order).exists())
            self.assertEqual(open_committed_grab_order.run(order.pk), 'OPENED')
            self.assertEqual(OrderGrabRound.objects.get(pool__order=order).opening_evidence.state, 'OBSERVED')
            intent.refresh_from_db()
            self.assertEqual(intent.commit_observed_at, paid_at)

    @override_settings(GRAB_COMMITTED_OPENING_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False,
                       PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, GRAB_POOL_GROUP_DISPATCH_ENABLED=True,
                       GRAB_TEAM_WINDOW_ENABLED=True)
    def test_concurrent_opening_delivery_keeps_one_round_and_one_group_enqueue(self):
        from orders.grab_pool_snapshot import initialize_entry

        store, flow = self.prepare_flow()
        with store_context(store):
            flow.service.fulfillment_provider_count = 2
            flow.service.save(update_fields=['fulfillment_provider_count'])
            order = self.buy_for_committed_opening(flow, 'formal-open-concurrent')
            self.assertEqual(order.required_provider_count, 2)
        entered, release = Event(), Event()

        def initialize(*args, **kwargs):
            entered.set()
            if not release.wait(15):
                raise RuntimeError('Opening transaction did not receive the release signal')
            return initialize_entry(*args, **kwargs)

        def open_order():
            close_old_connections()
            try:
                with store_context(store):
                    return open_committed_grab_order.run(order.pk)
            finally:
                close_old_connections()

        with (patch('orders.grab_pool_snapshot.initialize_entry', side_effect=initialize) as create_pool,
              patch('orders.grab_team_flow.enqueue'),
              patch('orders.kook_dispatch.enqueue_kook_dispatch') as kook,
              patch('orders.dispatching.enqueue_wecom_dispatch') as wecom,
              ThreadPoolExecutor(max_workers=2) as executor):
            first = executor.submit(open_order)
            try:
                self.assertTrue(entered.wait(10))
                second = executor.submit(open_order)
                deadline = monotonic() + 10
                while not mysql_locking_statements(connection, Order._meta.db_table):
                    self.assertFalse(second.done(), 'Second task finished before the order lock was released')
                    self.assertLess(monotonic(), deadline, 'Second task did not wait for the order lock')
                    sleep(.05)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=20), 'OPENED')
            self.assertIn(second.result(timeout=20), ('WAIT_OPENING', 'OPENED'))
        create_pool.assert_called_once()
        kook.assert_called_once()
        wecom.assert_called_once()
        with store_context(store):
            round_ = OrderGrabRound.objects.get(pool__order=order)
            self.assertEqual((round_.team_state.status, round_.opening_evidence.state), ('OPEN', 'OBSERVED'))
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -5000)])

    def test_single_grab_waits_for_order_then_rejects_new_receipt_and_keeps_the_original(self):
        from club_accounts.tokens import issue_account_tokens
        from orders.grab_customer_review import request_cancellation
        from orders.grab_review import mark_round_review
        from orders.models import OrderGrabSingleAdmission, OrderGrabSingleRoundState

        store, flow = self.prepare_flow()
        with store_context(store):
            save_grab_window(30, 1, lambda *_: None)
            purchase = flow.create(dispatch_mode='AUTO_GRAB', client_request_id='single-pause-payment')
            self.assertEqual(purchase.data['code'], 0, purchase.data)
            order_id = purchase.data['data']['id']
            path = f'/api/orders/orders/{order_id}/grab/'
            flow.auth(flow.accounts[0])
            original = flow.client.post(path, {'client_request_id': 'first-candidate'}, format='json')
            self.assertEqual(original.data.get('data', {}).get('status'), 'PENDING', original.data)
            other_token = issue_account_tokens(flow.accounts[1])[0]

        def new_grab():
            close_old_connections()
            try:
                with store_context(store):
                    client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION=f'Bearer {other_token}')
                    return client.post(path, {'client_request_id': 'waiting-candidate'}, format='json')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with store_context(store), transaction.atomic():
                Order.objects.select_for_update().get(pk=order_id)
                pending = executor.submit(new_grab)
                wait_until = monotonic() + 10
                while not mysql_locking_statements(connection, Order._meta.db_table):
                    self.assertFalse(pending.done(), 'Grab finished before the order lock was released')
                    self.assertLess(monotonic(), wait_until, 'Grab did not reach the order lock')
                    sleep(.05)
                state = OrderGrabSingleRoundState.objects.select_for_update().get(round__pool__order_id=order_id)
                mark_round_review(state, '原取消仍需核对')
                request = SimpleNamespace(account=flow.boss_account, data={'reason': '核对原付款'})
                accepted = request_cancellation(request, order_id, request_key='before-second-candidate')
                self.assertEqual(accepted.data.get('data'), {'status': 'PENDING'}, accepted.data)
                OrderGrabSingleRoundState.objects.filter(pk=state.pk).update(status='OPEN', reason='')
            rejected = pending.result(timeout=20)
        self.assertEqual((rejected.status_code, rejected.data.get('errorCode')), (409, 'GRAB_ROUND_REVIEW'), rejected.data)

        with store_context(store):
            repeated = flow.client.post(path, {'client_request_id': 'first-candidate'}, format='json')
            self.assertEqual(repeated.data.get('data'), original.data['data'], repeated.data)
            order = Order.objects.get(pk=order_id)
            self.assertEqual(list(OrderGrabSingleAdmission.objects.filter(round__pool__order=order)
                                  .values_list('receipt_id', flat=True)), [original.data['data']['receipt_id']])
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -order.amount)])
            self.assertFalse(order.providers.exists())

    @override_settings(GRAB_TEAM_WINDOW_ENABLED=True)
    def test_existing_team_can_be_read_or_withdrawn_while_new_invites_and_confirmation_pause(self):
        from orders.grab_review import mark_round_review
        from orders.models import OrderGrabTeamRoundState
        from site_messages.models import GrabInviteNoticeDelivery

        store, flow = self.prepare_flow()
        with store_context(store):
            save_grab_window(30, 1, lambda *_: None)
            flow.service.fulfillment_provider_count = 2
            flow.service.save(update_fields=['fulfillment_provider_count'])
            purchase = flow.create(dispatch_mode='AUTO_GRAB', client_request_id='team-pause-payment')
            self.assertEqual(purchase.data['code'], 0, purchase.data)
            order = Order.objects.get(pk=purchase.data['data']['id'])
            path = f'/api/orders/orders/{order.pk}/'
            invite = {'partner_id': flow.accounts[1].pk, 'client_request_id': 'original-team-invite'}
            flow.auth(flow.accounts[0])
            with patch('site_messages.grab_invite.deliver'):
                original = flow.client.post(path + 'grab-team/', invite, format='json')
            self.assertEqual(original.status_code, 200, original.data)
            team_id = original.data['data']['team_id']
            state = OrderGrabTeamRoundState.objects.get(round__pool__order=order)
            receipt = state.round.receipt_probes.get(kind='INVITE')
            mark_round_review(state, '原取消仍需核对')
            flow.auth(flow.boss_account)
            accepted = flow.client.post(path + 'cancel/', {'client_request_id': 'team-cancel-review'}, format='json')
            self.assertEqual(accepted.data.get('data'), {'status': 'PENDING'}, accepted.data)
            OrderGrabTeamRoundState.objects.filter(pk=state.pk).update(status='OPEN', reason='')

            flow.auth(flow.accounts[1])
            invitations = '/api/orders/grab-team/invitations/'
            listed = flow.client.get(invitations).data['data']
            item = next(row for row in listed if row['team_id'] == team_id)
            self.assertEqual((item['order_visible'], item['visibility_state']), (False, 'REVIEW'))
            self.assertNotIn('order_id', item)
            confirmed = flow.client.post(f'{invitations}{team_id}/confirm/', {'client_request_id': 'paused-confirm'}, format='json')
            self.assertEqual((confirmed.status_code, confirmed.data.get('errorCode')), (409, 'GRAB_ROUND_REVIEW'), confirmed.data)

            flow.auth(flow.accounts[0])
            another = flow.client.post(path + 'grab-team/', dict(invite, client_request_id='paused-new-team'), format='json')
            self.assertEqual((another.status_code, another.data.get('errorCode')), (409, 'GRAB_ROUND_REVIEW'), another.data)
            repeated = flow.client.post(path + 'grab-team/', invite, format='json')
            self.assertEqual(repeated.data.get('data'), original.data['data'], repeated.data)
            notice = GrabInviteNoticeDelivery.objects.get(receipt=receipt)
            self.assertIsNone(notice.message_id)
            self.assertTrue(notice.skipped_reason)

            withdrawn = flow.client.post(f'{invitations}{team_id}/withdraw/', {'client_request_id': 'withdraw-original-team'}, format='json')
            self.assertEqual((withdrawn.status_code, withdrawn.data.get('data', {}).get('status')), (200, 'WITHDRAWN'), withdrawn.data)
            self.assertCountEqual(state.round.receipt_probes.values_list('kind', flat=True), ['INVITE', 'WITHDRAW'])
            self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [('PAY', -order.amount)])
            self.assertFalse(order.providers.exists())

    def test_waiting_staff_refund_cannot_bypass_a_committed_customer_review(self):
        from club_accounts.tokens import issue_account_tokens
        from orders.grab_customer_review import request_cancellation
        from orders.grab_review import mark_round_review
        from orders.models import OrderGrabReviewCancellation, OrderGrabSingleRoundState
        from orders.tests.factories import make_console_user

        store, flow = self.prepare_flow()
        with store_context(store):
            save_grab_window(30, 1, lambda *_: None)
            purchase = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(purchase.data['code'], 0, purchase.data)
            order_id = purchase.data['data']['id']
            state = OrderGrabSingleRoundState.objects.get(round__pool__order_id=order_id)
            mark_round_review(state, '验证取消核对与退款并发')
            staff = make_console_user(['order:view', 'order:dispatch', 'order:refund'])
            staff_account = flow.link(staff)
            staff.admin_membership.account = staff_account
            staff.admin_membership.save(update_fields=['account'])
            staff_token = issue_account_tokens(staff_account)[0]

        def refund():
            close_old_connections()
            try:
                with store_context(store):
                    client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION=f'Bearer {staff_token}')
                    return client.post(f'/api/admin/orders/{order_id}/refund/', {'reason': '通用退款'}, format='json')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with store_context(store), transaction.atomic():
                Order.objects.select_for_update().get(pk=order_id)
                pending = executor.submit(refund)
                wait_until = monotonic() + 10
                while not mysql_locking_statements(connection, Order._meta.db_table):
                    self.assertFalse(pending.done(), 'Refund finished before the order lock was released')
                    self.assertLess(monotonic(), wait_until, 'Refund did not reach the order lock')
                    sleep(0.05)
                request = SimpleNamespace(account=flow.boss_account, data={'reason': '请核对原单'})
                accepted = request_cancellation(request, order_id, request_key='before-staff-refund')
                self.assertEqual(accepted.data.get('data'), {'status': 'PENDING'}, accepted.data)
            result = pending.result(timeout=20)
        self.assertEqual((result.status_code, result.data.get('errorCode')),
                         (409, 'GRAB_CANCEL_REVIEW_PENDING'), result.data)

        with store_context(store):
            order = Order.objects.get(pk=order_id)
            application = OrderGrabReviewCancellation.objects.get(order=order)
            self.assertEqual((order.status, order.payment_status, application.request_key, application.status),
                             ('PENDING', 'PAID', 'before-staff-refund', 'PENDING'))
            flow.wallet.refresh_from_db()
            self.assertEqual(flow.wallet.balance, 100000 - order.amount)
            self.assertFalse(order.refund_records.exists())

    def test_timeout_waiting_for_order_lock_reads_new_round_before_refunding(self):
        from orders.grab_receipt_probe import record_intent

        store, flow = self.prepare_flow()
        with store_context(store):
            save_grab_window(30, 1, lambda *_: None)
            flow.auth(flow.boss_account)
            purchase = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(purchase.data['code'], 0, purchase.data)
            order_id = purchase.data['data']['id']
            first = OrderGrabRound.objects.get(pool__order_id=order_id)
            receipt = record_intent(first.pk, flow.boss_account.pk, 'BOSS_CANCEL', 'before-round-changed',
                                    {'reason': '旧轮取消'}, single_flow=True)
            self.assertEqual(receipt.state, 'BEFORE')

        def timeout():
            close_old_connections()
            try:
                with store_context(store):
                    return auto_cancel_pending_order.run(order_id)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with store_context(store), transaction.atomic():
                Order.objects.select_for_update().get(pk=order_id)
                pending = executor.submit(timeout)
                wait_until = monotonic() + 10
                while not mysql_locking_statements(connection, Order._meta.db_table):
                    if pending.done():
                        self.fail(f'Timeout did not wait for the order: {pending.result()}')
                    self.assertLess(monotonic(), wait_until, 'Timeout did not reach the order lock')
                    sleep(0.05)
                deadline = first.deadline + timedelta(seconds=first.pool.window_seconds)
                OrderGrabRound.objects.create(pool=first.pool, number=2, opened_at=first.deadline,
                    deadline=deadline, total_deadline=max(first.total_deadline, deadline), simultaneous_visibility=True)
            self.assertEqual(pending.result(timeout=20), 'wait_round_review')

        with store_context(store):
            order = Order.objects.get(pk=order_id)
            flow.wallet.refresh_from_db()
            self.assertEqual((order.status, order.payment_status, flow.wallet.balance),
                             ('PENDING', 'PAID', 100000 - order.amount))
            self.assertFalse(order.refund_records.exists())
            self.assertFalse(order.providers.exists())
            self.assertEqual(first.receipt_probes.get().pk, receipt.receipt_id)

    def _wait_for_gate(self, store, order_id, operation, *, cancellation_receipt=False):
        def run():
            close_old_connections()
            try:
                with store_context(store):
                    return operation()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with store_context(store), transaction.atomic():
                lock_payroll_events()
                pending = pool.submit(run)
                deadline, found = monotonic() + 10, False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SHOW FULL PROCESSLIST')
                        found = any(row[3] == connection.settings_dict['NAME'] and row[4] == 'Query'
                            and 'CONSOLE_PAYROLLEVENTBARRIER' in str(row[7] or '').upper()
                            and 'LOCK IN SHARE MODE' in str(row[7] or '').upper() for row in cursor.fetchall())
                    if found:
                        break
                    if pending.done():
                        self.fail(f'Operation returned before barrier: {pending.result()}')
                    sleep(0.05)
                self.assertTrue(found, 'Operation did not wait for payroll barrier')
                Order.objects.select_for_update(nowait=True).get(pk=order_id)
                if cancellation_receipt:
                    self.assertTrue(OrderGrabReceiptProbe.objects.filter(round__pool__order_id=order_id,
                                                                          kind='BOSS_CANCEL').exists())
                self.assertFalse(OrderRefund.objects.filter(order_id=order_id).exists())
            return pending.result(timeout=20)

    def test_cancel_priority_management_candidate_clock_and_ordinary_timeout(self):
        store, flow = self.prepare_flow()
        with store_context(store):
            save_grab_window(30, 1, lambda *_: None)
            flow.auth(flow.boss_account)
            created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(created.data['code'], 0, created.data)
            cancelled = Order.objects.get(pk=created.data['data']['id'])
            flow.auth(flow.accounts[1])
            grab = flow.client.post(f'/api/orders/orders/{cancelled.pk}/grab/',
                                    {'client_request_id':'candidate-before-cancel'}, format='json')
            self.assertEqual(grab.data['code'], 0, grab.data)
            flow.auth(flow.boss_account)
        response = self._wait_for_gate(store, cancelled.pk, lambda: flow.client.post(
            f'/api/orders/orders/{cancelled.pk}/cancel/', {'reason':'计划变更'}, format='json'),
            cancellation_receipt=True)
        self.assertEqual(response.data['code'], 0, response.data)
        with store_context(store):
            cancelled.refresh_from_db()
            self.assertEqual((cancelled.status, cancelled.provider_account_id), ('CANCELLED', None))
            self.assertEqual(OrderRefund.objects.filter(order=cancelled).count(), 1)
            save_grab_window(1, 2, lambda *_: None)
            flow.auth(flow.boss_account)
            created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(created.data['code'], 0, created.data)
            candidate = Order.objects.get(pk=created.data['data']['id'])
            flow.auth(flow.accounts[1])
            grabbed = flow.client.post(f'/api/orders/orders/{candidate.pk}/grab/',
                                       {'client_request_id':'candidate-before-command'}, format='json')
            self.assertEqual(grabbed.data['code'], 0, grabbed.data)
            pool = OrderGrabPoolSnapshot.objects.get(order=candidate)
            OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(original_total_deadline=pool.first_deadline,
                                                                     total_deadline=pool.first_deadline)
            OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
            Order.objects.filter(pk=candidate.pk).update(auto_cancel_at=pool.first_deadline)
        sleep(1.1)
        self._wait_for_gate(store, candidate.pk, lambda: call_command('check_order_timeout', verbosity=0))
        with store_context(store):
            candidate.refresh_from_db()
            self.assertEqual((candidate.status, candidate.provider_account_id), ('GRABBED', flow.accounts[1].pk))
            self.assertFalse(OrderRefund.objects.filter(order=candidate).exists())
            flow.auth(flow.boss_account)
            created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(created.data['code'], 0, created.data)
            ungated_candidate = Order.objects.get(pk=created.data['data']['id'])
            flow.auth(flow.accounts[0])
            result = flow.client.post(f'/api/orders/orders/{ungated_candidate.pk}/grab/',
                                      {'client_request_id':'ungated-candidate'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            pool = OrderGrabPoolSnapshot.objects.get(order=ungated_candidate)
            OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(original_total_deadline=pool.first_deadline,
                                                                     total_deadline=pool.first_deadline)
            OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
            Order.objects.filter(pk=ungated_candidate.pk).update(auto_cancel_at=pool.first_deadline)
            sleep(1.1)
            with override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=False):
                call_command('check_order_timeout', verbosity=0)
            ungated_candidate.refresh_from_db()
            self.assertEqual((ungated_candidate.status, ungated_candidate.provider_account_id),
                             ('GRABBED', flow.accounts[0].pk))
            self.assertFalse(OrderRefund.objects.filter(order=ungated_candidate).exists())
            flow.auth(flow.boss_account)
            created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(created.data['code'], 0, created.data)
            paused = Order.objects.get(pk=created.data['data']['id'])
            Order.objects.filter(pk=paused.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
            with override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
                call_command('check_order_timeout', verbosity=0)
            paused.refresh_from_db()
            self.assertEqual((paused.status, paused.payment_status), ('PENDING', 'PAID'))
            self.assertFalse(OrderRefund.objects.filter(order=paused).exists())
            ordinary = flow.create(dispatch_mode='BOSS_SELECT', client_request_id=str(uuid4()))
            self.assertEqual(ordinary.data['code'], 0, ordinary.data)
            ordinary = Order.objects.get(pk=ordinary.data['data']['id'])
            Order.objects.filter(pk=ordinary.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
        result = self._wait_for_gate(store, ordinary.pk, lambda: auto_cancel_pending_order.run(ordinary.pk))
        self.assertEqual(result, 'cancelled')
        with store_context(store):
            ordinary.refresh_from_db()
            self.assertEqual((ordinary.status, ordinary.payment_status), ('CANCELLED', 'REFUNDED'))
            self.assertEqual(OrderRefund.objects.filter(order=ordinary).count(), 1)
            flow.auth(flow.boss_account)
            plain = flow.create(dispatch_mode='BOSS_SELECT', client_request_id=str(uuid4()))
            self.assertEqual(plain.data['code'], 0, plain.data)
            plain = Order.objects.get(pk=plain.data['data']['id'])
            Order.objects.filter(pk=plain.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
            with override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=False,
                                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
                call_command('check_order_timeout', verbosity=0)
            plain.refresh_from_db(); paused.refresh_from_db()
            self.assertEqual((plain.status, plain.payment_status), ('CANCELLED', 'REFUNDED'))
            self.assertEqual(OrderRefund.objects.filter(order=plain).count(), 1)
            self.assertEqual((paused.status, paused.payment_status), ('PENDING', 'PAID'))
            flow.service.billing_provider_count = 2
            flow.service.save(update_fields=['billing_provider_count'])
            save_grab_window(30, 3, lambda *_: None)
            with override_settings(GRAB_TEAM_WINDOW_ENABLED=True):
                created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
                self.assertEqual(created.data['code'], 0, created.data)
                team_order = Order.objects.get(pk=created.data['data']['id'])
                response = self._wait_for_gate(store, team_order.pk, lambda: flow.client.post(
                    f'/api/orders/orders/{team_order.pk}/cancel/', {'reason':'双陪取消'}, format='json'),
                    cancellation_receipt=True)
                self.assertEqual(response.data['code'], 0, response.data)
                team_order.refresh_from_db()
                self.assertEqual((team_order.status, team_order.payment_status), ('CANCELLED', 'REFUNDED'))
                self.assertEqual(OrderRefund.objects.filter(order=team_order).count(), 1)
