"""Verify formal points funding and the lifecycle lock order on a paid reservation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import os
from pathlib import Path
from queue import Queue
from tempfile import mkdtemp
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from common.mysql_test_support import mysql_locking_sessions
from console import test_payroll_late_activity as payroll_fixture
from console.models import AdminAuditLog
from console.payroll_models import PayrollEventBarrier, PayrollRun
from console.points_redemption_config import save_points_terms
from engagement.models import (ConsumptionConfirmation, MemberProfile, PointLedger, RetirementAudit,
                               TransferRefundPointsConsumptionEvidence)
from engagement.tasks import confirm_due_consumption
from orders.addon_funding_release import record_addon_funding_release
from orders.models import (AfterSale, GameCategory, Order, OrderAddonFundingReleaseRun, OrderAddonHandover, OrderAddonPayment, OrderAddonRefund,
                           OrderAddonConsumption, OrderFinancialLock, OrderOriginalShareTransfer, OrderPointsRedemption,
                           OrderParticipationSegment, OrderSegmentAddonSettlement, OrderSegmentPersonSettlement,
                           OrderPointsTransferFundingRevision, OrderSegmentReportSubmission, OrderSegmentSettlementManifest,
                           PointsNegotiationPosting,
                           ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_assigned_order_reservation_guard as reservation_fixture
from orders.tests import test_release_flow as release_fixture
from orders.tests import test_reservation_transfer_schedule_mysql as transfer_fixture
from orders.tests.factories import make_console_user, make_order, make_provider, make_service
from site_messages.addon_refund_notice import deliver as deliver_addon_refund_notice
from site_messages.models import AfterSaleOpenNotice, Message
from site_messages.tasks import recover_addon_refund_notices
from stores.context import store_context
from stores.models import PlatformAccount, PlatformRoleGrant
from users.models import EscortProfile, PassRefundRepaymentReceipt, PassReceiptUsageEvent
from wallet.models import IdempotencyKey, Transaction, Wallet
from wallet.services import get_wallet


@skipUnless(connection.vendor == 'mysql', 'Requires formal MySQL points and row locks')
@override_settings(**{**transfer_fixture.ReservationTransferScheduleMySQLTest._overridden_settings,
    'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED': True, 'PROVIDER_RETIREMENT_ENABLED': True,
    'SERVICE_POINTS_MYSQL_ENABLED': True, 'RECEIPT_USAGE_LEDGER_ENABLED': True,
    'BOSS_FUNDING_SELECTION_ENABLED': True, 'POINTS_REDEMPTION_PREVIEW_ENABLED': True,
    'POINTS_USAGE_SWITCHES_ENABLED': True, 'SERVICE_ORDER_POINTS_RECEIPT_ENABLED': True,
    'DISCOUNT_COMBINATIONS_PAYMENT_ENABLED': True, 'DISCOUNT_OPTIONS_PREVIEW_ENABLED': True,
    'PLATFORM_IDENTITY_ENABLED': True, 'PLATFORM_PASS_REPAYMENT_ENABLED': True})
class ReservationPointsTransferMySQLTest(TransactionTestCase):
    routes = transfer_fixture.ReservationTransferScheduleMySQLTest.routes
    databases = transfer_fixture.ReservationTransferScheduleMySQLTest.databases
    login = reservation_fixture.AssignedOrderReservationGuardTest.login
    ok = reservation_fixture.AssignedOrderReservationGuardTest.ok
    recharge = reservation_fixture.AssignedOrderReservationGuardTest.recharge
    api = transfer_fixture.ReservationTransferScheduleMySQLTest.api
    run_in_stores = transfer_fixture.ReservationTransferScheduleMySQLTest.run_in_stores
    wait_for_lock = transfer_fixture.ReservationTransferScheduleMySQLTest.wait_for_lock
    independent_request = transfer_fixture.ReservationTransferScheduleMySQLTest.independent_request
    observe_lock_attempt = staticmethod(transfer_fixture.ReservationTransferScheduleMySQLTest.observe_lock_attempt)
    assert_occupancy = transfer_fixture.ReservationTransferScheduleMySQLTest.assert_occupancy
    retirement_race = False
    team_returned_addon = False
    teammate_paid_addon = False
    same_seat_paid_addon = False
    gift_returned_addon = False
    main_refund_after_addon = False
    defer_funding = False

    def prepare(self):
        reservation_fixture.AssignedOrderReservationGuardTest.setUp(self)
        self.original = self.accounts[self.provider.pk]
        self.buyer = self.accounts[self.boss.pk]
        self.staff = self.accounts[self.admin.pk]
        get_wallet(account=self.original, user=self.provider)
        profile = self.provider.escort_profile
        profile.deposit_paid = 0
        profile.save(update_fields=['deposit_paid'])
        if self.retirement_race:
            self.prepare_retirement()
            # Make retirement check the order-money lock while the new order is in service.
            make_order(self.boss, provider=self.provider, status=Order.Status.COMPLETED, amount=0,
                       customer_account=self.buyer, provider_account=self.original, completed_at=timezone.now())

        replacement = make_provider(deposit_mode='EARNINGS')
        self.replacement = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = self.replacement
        replacement.escort_profile.save(update_fields=['account'])
        get_wallet(account=self.replacement, user=replacement)
        self.teammate = None
        if self.team_returned_addon:
            teammate = make_provider(deposit_mode='EARNINGS')
            self.teammate = get_or_create_account_for_legacy_user(teammate)
            teammate.escort_profile.account = self.teammate
            teammate.escort_profile.save(update_fields=['account'])
            get_wallet(account=self.teammate, user=teammate)
            self.service.price = 5000
            self.service.billing_provider_count = 2
            self.service.fulfillment_provider_count = 2
            self.service.save(update_fields=['price', 'billing_provider_count', 'fulfillment_provider_count'])
        self.ok(self.recharge())
        self.points, _ = MemberProfile.objects.update_or_create(
            account=self.buyer, defaults={'points_balance': 20000 if self.gift_returned_addon else 15000})
        save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)

        self.start = (timezone.localtime() + timedelta(days=2)).replace(hour=23, minute=30, second=0, microsecond=0)
        self.end = self.start + timedelta(hours=1)
        provider_accounts = [self.original, self.teammate] if self.teammate else [self.original]
        self.reservation_id, self.order_id = self.book_points_reservation(
            provider_accounts, self.start, 'points-transfer-booking')
        self.receipt = OrderPointsRedemption.objects.get(order_id=self.order_id)
        self.assertEqual((self.receipt.deal_units, self.receipt.paid_units, self.receipt.points_hundredths), (10000, 6000, 10000))
        self.funding_receipt = self.fund_reservation()
        for account in provider_accounts:
            self.ok(self.api(account).post(f'/api/orders/orders/{self.order_id}/start/',
                {'entry_image': release_fixture.screenshot()}, format='multipart'))
        self.service.commission_override = True
        self.service.commission_rate = 30
        self.service.save(update_fields=['commission_override', 'commission_rate'])
        order = Order.objects.get(pk=self.order_id)
        self.funding_before = list(order.transactions.values_list('pk', 'tx_type', 'amount'))
        self.balance_before = Wallet.objects.get(account=self.buyer).balance

    def book_points_reservation(self, provider_accounts, start, request_id, *, service=None):
        buyer = self.api(self.buyer)
        selection = ({'provider_account_ids': [account.pk for account in provider_accounts]} if len(provider_accounts) == 2
                     else {'provider_account_id': provider_accounts[0].pk})
        booking = self.ok(buyer.post('/api/orders/reservations/', {'service_id': (service or self.service).pk,
            **selection, 'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat()}, format='json'))
        path = f'/api/orders/reservations/{booking["id"]}/'
        for account in provider_accounts:
            self.ok(self.api(account).post(path + 'confirm/', {}, format='json'))
        quote = self.ok(buyer.get(path + 'quote/', {'preview_points_hundredths': 10000}))
        self.assertEqual((quote['amount'], quote['payable_amount']), (10000, 6000))
        self.assertIs(quote['points_preview']['checkout_test_only'], False)
        paid = self.ok(buyer.post(path + 'convert/', {'discount_selection_token': quote['discount_selection_token'],
            'points_checkout_test_token': quote['points_preview']['checkout_test_token'], 'expected_amount': 6000,
            'expected_service': quote['service'], 'client_request_id': request_id}, format='json'))
        return booking['id'], paid['order']['id']

    def transfer(self, client, *, share=(4000, 6000)):
        return client.post(f'/api/admin/orders/{self.order_id}/transfer/', {
            'reason': '客服确认接替原服务', 'transfers': [{
                'old_provider_id': self.original.pk, 'new_provider_id': self.replacement.pk,
                'original_share': {'retained': share[0], 'transferred': share[1], 'agreement': '双方确认原价份额'},
            }]}, format='json')

    def join_replacement(self, client, change):
        self.ok(client.post(f'/api/orders/orders/{self.order_id}/participation-start/',
            {'segment_id': change.next_segment_id, 'entry_image': release_fixture.screenshot(),
             'client_request_id': 'points-transfer-join'}, format='multipart'))

    def prepare_retirement(self):
        operator = make_console_user(['escort:retire'])
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account
        operator.admin_membership.save(update_fields=['account'])
        self.retirement_client = self.api(account)
        self.retirement_url = f'/api/admin/providers/{self.original.pk}/retirement/'
        preview = self.ok(self.retirement_client.get(self.retirement_url))
        self.assertTrue(preview['can_retire'], preview['facts']['blocking_reasons'])
        self.retirement_payload = {'confirm': True, 'confirmation_token': preview['confirmation_token'],
                                   'reason': '隔离场景核对已结清账号', 'client_request_id': 'points-transfer-retire'}

    def fund_reservation(self):
        finance = PlatformAccount(username=f'points-funding-{self.store.slug}', phone='13800001111', is_phone_verified=True)
        finance.set_password('isolated-only-password')
        finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug=self.store.slug)
        client = APIClient(HTTP_HOST='admin.esports.club')
        delivered = []
        credentials = {'username': finance.username, 'password': 'isolated-only-password'}
        with patch('stores.platform_auth.send_verification_code', side_effect=lambda phone, code: delivered.append(code)):
            login = '/api/admin/platform/auth/login'
            challenge = self.ok(client.post(login, credentials, format='json'))
            signed = self.ok(client.post(login, {**credentials, 'smsChallenge': challenge['smsChallenge'],
                                                 'smsCode': delivered[0]}, format='json'))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + signed['token'])
        url = f'/api/admin/platform/pass-refund-repayments/{self.store.slug}/'
        registered = self.ok(client.post(url + 'register', {'request_id': str(uuid4()), 'amount': 2000,
            'external_reference': f'POINTS-TRANSFER-{self.store.slug}', 'received_at': timezone.now().isoformat(),
            'evidence': '隔离测试到账记录'}, format='json'))
        claim = registered['claim_id']
        self.ok(client.post(f'{url}{claim}/verify', {'request_id': str(uuid4())}, format='json'))
        self.finance_client = client
        self.funding_url = f'{url}{claim}/points-allocation'
        self.funding_payload = {'request_id': str(uuid4()), 'amount': 2000, 'order_id': self.order_id}
        self.original_allocation = (None if self.defer_funding else
                                    self.ok(client.post(self.funding_url, self.funding_payload, format='json')))
        return PassRefundRepaymentReceipt.objects.get(external_reference=f'POINTS-TRANSFER-{self.store.slug}')

    def test_retirement_and_funded_points_transfer_lock_lifecycle_before_money(self):
        self.retirement_race = True
        self.run_in_stores(self.exercise_retirement_and_points_transfer)

    def test_funded_points_transfer_waits_for_payroll_while_retirement_waits_for_transfer(self):
        self.retirement_race = True
        self.run_in_stores(self.exercise_payroll_and_transfer)

    @staticmethod
    def pause_payroll_barrier(held, resume):
        table = PayrollEventBarrier._meta.db_table

        def pause(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if not held.is_set() and f'`{table}`' in sql.lower() and 'for update' in sql.lower():
                held.set()
                if not resume.wait(8):
                    raise AssertionError('工资屏障等待未在测试时限内结束')
            return result
        return pause

    @override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True)
    def exercise_payroll_and_transfer(self):
        PayrollEventBarrier.objects.get_or_create(pk=1)
        payroll = payroll_fixture.LateActivityPayrollTest()
        payroll.setUp()
        payroll.assign(payroll.original_text)
        preview = payroll.preview(payroll.original_text)
        self.assertTrue(preview['can_confirm'], preview['warnings'])
        payroll_url = '/api/admin/payroll/confirm/'
        payload = {'month': payroll.original_text, 'fingerprint': preview['fingerprint']}
        table = PayrollEventBarrier._meta.db_table
        lifecycle_table = transfer_fixture.LIFECYCLE_TABLE
        held, resume = Event(), Event()
        payroll_attempts, retirement_attempts = Queue(), Queue()

        transfer_client = self.api(self.staff)
        with ThreadPoolExecutor(max_workers=3) as pool:
            confirming = pool.submit(self.independent_request,
                lambda: payroll.client.post(payroll_url, payload, format='json'), self.pause_payroll_barrier(held, resume))
            try:
                self.assertTrue(held.wait(3), '工资确认未取得独占屏障')
                transfer = pool.submit(self.independent_request, lambda: self.transfer(transfer_client),
                    self.observe_lock_attempt(payroll_attempts, table, 'lock in share mode'))
                waited_for_payroll = self.wait_for_lock(transfer, payroll_attempts, table=table, include_shared=True)
                retiring = pool.submit(self.independent_request,
                    lambda: self.retirement_client.post(self.retirement_url, self.retirement_payload, format='json'),
                    self.observe_lock_attempt(retirement_attempts, lifecycle_table, 'for update', account_id=self.original.pk))
                waited_for_transfer = self.wait_for_lock(retiring, retirement_attempts, table=lifecycle_table)
                with transaction.atomic(using=self.store.database_alias):
                    Order.objects.select_for_update(nowait=True).get(pk=self.order_id)
                    OrderFinancialLock.objects.select_for_update(nowait=True).get(pk=1)
                    self.assertFalse(PayrollRun.objects.filter(month=payroll.original).exists())
                    self.assertFalse(OrderPointsTransferFundingRevision.objects.filter(order_id=self.order_id).exists())
                self.assertFalse(transfer.done())
                self.assertFalse(retiring.done())
            finally:
                resume.set()
            confirmation = self.ok(confirming.result(timeout=10))
            self.ok(transfer.result(timeout=10))
            retirement = retiring.result(timeout=10)

        run = PayrollRun.objects.get(month=payroll.original)
        self.assertEqual((confirmation['run_id'], run.fingerprint), (run.pk, preview['fingerprint']))
        replay = self.ok(payroll.client.post(payroll_url, payload, format='json'))
        self.assertEqual((replay['run_id'], replay['totals']), (run.pk, preview['totals']))
        self.assertEqual(retirement.status_code, 400, retirement.data)
        self.assertIn('注销内容已变化', retirement.data['msg'])
        self.assertFalse(RetirementAudit.objects.filter(account=self.original).exists())
        self.assert_occupancy(self.replacement, self.original, 1)
        self.assertEqual(list(PointLedger.objects.filter(order_id=self.order_id).values_list('amount', flat=True)), [-10000])
        revision = OrderPointsTransferFundingRevision.objects.get(order_id=self.order_id)
        self.assertEqual((revision.after_funding['additional_store_funding_units'], revision.releases.get().amount), (1400, 600))
        usage = PassReceiptUsageEvent.objects.filter(state__receipt=self.funding_receipt).order_by('version')
        self.assertEqual(list(usage.values_list('kind', 'amount')), [('ORDER', 2000), ('RELEASE', 600)])
        print(f'{self.store.slug} reservation={self.reservation_id} order={self.order_id} '
              f'payroll_run={run.pk} transfer_waiting={waited_for_payroll} '
              f'retirement_waiting={waited_for_transfer} retirement=400 transfer=0', flush=True)

    def test_transferred_points_aftersale_waits_for_payroll_before_locking_order(self):
        self.run_in_stores(self.exercise_payroll_and_aftersale)

    def test_order_card_waits_for_funding_without_locking_buyer(self):
        self.run_in_stores(self.exercise_chat_funding_race)

    def test_consultation_aftersale_reconciles_original_message_and_payroll(self):
        self.run_in_stores(self.exercise_consultation_aftersale)

    def test_consultation_aftersale_rechecks_staff_permission_after_payroll_wait(self):
        self.run_in_stores(self.exercise_consultation_permission_change)

    def test_consultation_addon_aftersale_binds_paid_segment_without_repeating_writes(self):
        self.run_in_stores(self.exercise_paid_addon_consultation)

    def test_returned_addon_consultation_refunds_only_the_returned_segment(self):
        self.run_in_stores(self.exercise_returned_addon_consultation)

    def test_team_returned_addon_refund_does_not_interrupt_teammate(self):
        self.team_returned_addon = True
        self.run_in_stores(self.exercise_returned_addon_consultation)

    def test_team_returned_addon_refund_preserves_teammate_paid_addon(self):
        self.team_returned_addon = True
        self.teammate_paid_addon = True
        self.run_in_stores(self.exercise_returned_addon_consultation)

    def test_unfunded_team_returned_addon_refund_preserves_teammate_paid_addon(self):
        self.team_returned_addon = True
        self.teammate_paid_addon = True
        self.defer_funding = True
        self.run_in_stores(self.exercise_returned_addon_consultation)

    def test_team_returned_addon_refund_selects_one_of_two_same_seat_payments(self):
        self.team_returned_addon = True
        self.same_seat_paid_addon = True
        self.run_in_stores(self.exercise_returned_addon_consultation)

    def test_unfunded_team_returned_addon_refund_selects_one_of_two_same_seat_payments(self):
        self.team_returned_addon = True
        self.same_seat_paid_addon = True
        self.defer_funding = True
        self.run_in_stores(self.exercise_returned_addon_consultation)

    def test_team_returned_addon_refund_reuses_funding_released_by_gift_refund(self):
        self.team_returned_addon = True
        self.same_seat_paid_addon = True
        self.gift_returned_addon = True
        self.run_in_stores(self.exercise_returned_addon_consultation)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_team_main_refund_after_returned_addon_refund_preserves_other_payment(self):
        self.team_returned_addon = True
        self.same_seat_paid_addon = True
        self.main_refund_after_addon = True
        self.run_in_stores(self.exercise_returned_addon_consultation)

    def test_team_returned_addon_refund_preserves_unfunded_snapshot_after_allocation(self):
        self.team_returned_addon = True
        self.defer_funding = True
        self.run_in_stores(self.exercise_returned_addon_consultation)

    def test_consultation_refund_notice_waits_for_buyer_retirement(self):
        self.run_in_stores(lambda: self.exercise_paid_addon_consultation(retire_buyer=True))

    def test_consultation_refund_notice_waits_for_provider_retirement(self):
        self.run_in_stores(lambda: self.exercise_provider_retirement_notice_race(retirement_first=True))

    def test_provider_retirement_waits_for_consultation_refund_notice(self):
        self.run_in_stores(lambda: self.exercise_provider_retirement_notice_race(retirement_first=False))

    @skipUnless(getattr(settings, 'SA14_TEST_REDIS', None) and getattr(settings, 'SA14_TEST_CONTROL_INSTANCE', None),
                'Requires a registered disposable Redis broker and independent MySQL control instance')
    def test_consultation_refund_worker_waits_for_provider_retirement(self):
        with self.refund_notice_worker() as (publisher, _, _, _):
            self.run_in_stores(lambda: self.exercise_provider_retirement_notice_race(
                retirement_first=True, publisher=publisher))

    @skipUnless(getattr(settings, 'SA14_TEST_REDIS', None) and getattr(settings, 'SA14_TEST_CONTROL_INSTANCE', None),
                'Requires a registered disposable Redis broker and independent MySQL control instance')
    def test_provider_retirement_waits_for_consultation_refund_worker(self):
        with self.refund_notice_worker() as (publisher, _, _, _):
            self.run_in_stores(lambda: self.exercise_provider_retirement_notice_race(
                retirement_first=False, publisher=publisher))

    @skipUnless(getattr(settings, 'SA14_TEST_REDIS', None) and getattr(settings, 'SA14_TEST_CONTROL_INSTANCE', None),
                'Requires a registered disposable Redis broker and independent MySQL control instance')
    def test_consultation_refund_notices_recover_on_independent_beat_and_worker(self):
        import re
        import shelve

        from celery import Celery
        from stores.test_certificate_admission_mysql import _celery_process, _registered_redis_urls

        self.assertFalse(any(os.environ.get(key) for key in ('CELERY_BROKER_URL', 'CELERY_BROKER_READ_URL',
            'CELERY_BROKER_WRITE_URL', 'CELERY_RESULT_BACKEND')))
        broker, backend = _registered_redis_urls()
        cases = []

        def prepare_notice():
            refund = self.exercise_paid_addon_consultation(leave_notices_pending=True)
            cases.append((self.store, refund.pk))

        self.run_in_stores(prepare_notice)
        self.assertCountEqual([store.slug for store, _ in cases], ['shenglang', 'xinan'])
        before = self.refund_notice_worker_facts(cases)
        for case in before.values():
            self.assertEqual(len(case['intents']), 2)
            self.assertTrue(all(notice[-3:] == (None, None, '') for notice in case['intents']))
            self.assertEqual(case['messages'], [])

        directory = Path(os.environ['XA_SA14_MYSQL_REGISTRATION']).resolve().parent
        work = Path(mkdtemp(prefix='addon-refund-worker-', dir=directory))
        queue = f'xa_sa14_cli_{uuid4().hex}'
        beat_entry = 'recover-addon-refund-notices'
        schedule = work / 'celerybeat-schedule'
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.certificate_celery_test_settings',
               'XA_SA14_CELERY_QUEUE': queue, 'XA_SA14_CELERY_MEDIA': str(work / 'media'),
               'XA_SA14_CELERY_BEAT_ENTRY': beat_entry, 'ADDON_REFUND_NOTICE_RECOVERY_ENABLED': '1',
               'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED': '1'}
        command = ['worker', '-P', 'solo', '-l', 'INFO', '--without-mingle', '--without-gossip', '--without-heartbeat']
        task_name = recover_addon_refund_notices.name
        interval = settings.CELERY_BEAT_SCHEDULE[beat_entry]['schedule']
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE[beat_entry]['task'], task_name)
        self.assertEqual(interval, 60.0)
        sent = re.compile(re.escape(task_name) + r' sent\. id->([0-9a-f-]{36})')
        seen = set()
        beat_log = work / 'beat.log'
        print(f'加购退款通知 worker 日志目录 {work.name}', flush=True)

        def wait_until_ready(worker, log):
            deadline = monotonic() + 20
            while worker.poll() is None and monotonic() < deadline:
                if ' ready.' in log.read_text():
                    return
                sleep(.1)
            self.fail(f'退款通知 worker 未就绪，见 {log}')

        with Celery(queue, broker=broker, backend=backend, set_as_current=False) as publisher:
            publisher.conf.update(task_default_queue=queue, task_default_exchange=queue,
                                 task_default_routing_key=queue, broker_transport_options={'global_keyprefix': queue + ':'},
                                 result_backend_transport_options={'global_keyprefix': queue + ':'})

            def receive_scheduled_dispatch(worker, beat, expected):
                deadline = monotonic() + interval + 20
                while monotonic() < deadline:
                    self.assertIsNone(worker.poll(), f'退款通知 worker 提前退出，见 {work}')
                    self.assertIsNone(beat.poll(), f'退款通知 Beat 提前退出，见 {beat_log}')
                    task_id = next((item for item in sent.findall(beat_log.read_text()) if item not in seen), None)
                    if task_id:
                        observed = monotonic()
                        break
                    sleep(.15)
                else:
                    self.fail(f'退款通知 Beat 未在规定时限派发，见 {beat_log}')
                seen.add(task_id)
                fanout = publisher.AsyncResult(task_id).get(timeout=20)
                self.assertEqual((fanout['enqueue_unknown'], fanout['directory_fallback']), ([], False))
                self.assertCountEqual([job['store'] for job in fanout['stores']], list(expected))
                results = {job['store']: publisher.AsyncResult(job['task_id']).get(timeout=20)
                           for job in fanout['stores']}
                self.assertEqual(results, expected)
                print(f'退款通知 Beat 父任务 {task_id} 子任务 {fanout["stores"]} 扫描数 {results}', flush=True)
                return observed

            log = work / 'worker-first.log'
            with _celery_process(command, env=env, log=log) as worker:
                wait_until_ready(worker, log)
                first = publisher.send_task(task_name, headers={'store_slug': 'shenglang'}, queue=queue)
                self.assertEqual(first.get(timeout=20), 2)
                partial = self.refund_notice_worker_facts(cases)
                self.assertEqual(partial['xinan'], before['xinan'])
                self.assertEqual(len(partial['shenglang']['messages']), 2)
                print(f'退款通知 worker PID={worker.pid} 声浪任务 {first.id}', flush=True)
            self.assertEqual(worker.returncode, 0)

            log = work / 'worker-restarted.log'
            with _celery_process(command, env=env, log=log) as restarted:
                wait_until_ready(restarted, log)
                print(f'退款通知 worker 重启 PID={restarted.pid}', flush=True)
                started = monotonic()
                with _celery_process(['beat', '-l', 'DEBUG', '--schedule', str(schedule)], env=env, log=beat_log) as beat:
                    first_dispatch = receive_scheduled_dispatch(restarted, beat, {'shenglang': 0, 'xinan': 2})
                    self.assertGreaterEqual(first_dispatch - started, interval - 2)
                    delivered = self.refund_notice_worker_facts(cases)
                    second_dispatch = receive_scheduled_dispatch(restarted, beat, {'shenglang': 0, 'xinan': 0})
                    self.assertAlmostEqual(second_dispatch - first_dispatch, interval, delta=5)
                    print(f'退款通知 Beat PID={beat.pid} 首次派发 {first_dispatch - started:.2f}s '
                          f'两轮间隔 {second_dispatch - first_dispatch:.2f}s', flush=True)
                self.assertEqual(beat.returncode, 0)
                with shelve.open(str(schedule), flag='r') as saved:
                    self.assertEqual(saved['entries'][beat_entry].total_run_count, 2)
            self.assertEqual(restarted.returncode, 0)
            self.assertEqual(self.refund_notice_worker_facts(cases), delivered)

        self.assertEqual(delivered['shenglang'], partial['shenglang'])
        for slug, case in delivered.items():
            self.assertEqual(case['refund'], before[slug]['refund'])
            self.assertCountEqual([row[:4] for row in case['intents']], [row[:4] for row in before[slug]['intents']])
            self.assertTrue(all(message and completed and not skipped for *_, message, completed, skipped in case['intents']))
            self.assertCountEqual(case['messages'], [(message, account, cycle)
                                  for _, account, cycle, _, message, _, _ in case['intents']])
            print(f'{slug} order={case["order"]} refund={case["refund"][0]} messages={case["messages"]}', flush=True)

    def refund_notice_worker_facts(self, cases):
        facts = {}
        for store, refund_id in cases:
            with store_context(store):
                refund = OrderAddonRefund.objects.select_related('buyer_transaction', 'funding_release_run').get(pk=refund_id)
                facts[store.slug] = {'order': refund.order_id,
                    'refund': (refund.pk, refund.amount, refund.buyer_transaction_id, refund.funding_release_run.required_amount,
                               Wallet.objects.get(pk=refund.buyer_transaction.wallet_id).balance,
                               refund.payment.refund_records.count()),
                    'intents': list(refund.notice_intents.order_by('pk').values_list('pk', 'account_id', 'business_cycle',
                        'role', 'message_id', 'completed_at', 'skipped_reason')),
                    'messages': list(Message.objects.filter(related_order_id=refund.order_id, title='加购退款已处理')
                        .order_by('pk').values_list('pk', 'recipient_account_id', 'recipient_business_cycle'))}
        return facts

    @skipUnless(getattr(settings, 'SA14_TEST_REDIS', None) and getattr(settings, 'SA14_TEST_CONTROL_INSTANCE', None),
                'Requires a registered disposable Redis broker and independent MySQL control instance')
    def test_consultation_refund_notice_refreshes_real_store_websockets(self):
        self.exercise_refund_notice_websockets()

    @skipUnless(getattr(settings, 'SA14_TEST_REDIS', None) and getattr(settings, 'SA14_TEST_CONTROL_INSTANCE', None),
                'Requires a registered disposable Redis broker and independent MySQL control instance')
    def test_consultation_refund_websocket_rejects_retired_buyer_cycle(self):
        self.exercise_refund_notice_websockets(retirement_role='buyer')

    @skipUnless(getattr(settings, 'SA14_TEST_REDIS', None) and getattr(settings, 'SA14_TEST_CONTROL_INSTANCE', None),
                'Requires a registered disposable Redis broker and independent MySQL control instance')
    @override_settings(SEGMENT_SETTLEMENT_ENABLED=True)
    def test_consultation_refund_websocket_rejects_retired_provider_cycle(self):
        self.exercise_refund_notice_websockets(retirement_role='replacement')

    def exercise_refund_notice_websockets(self, *, retirement_role=None):
        import asyncio
        import socket
        import subprocess
        import sys
        from functools import partial

        from club_accounts.tokens import issue_account_tokens

        cases, identities = [], {}
        retirements = {}

        def save_account(label, account):
            token, _ = issue_account_tokens(account)
            identities[label] = (self.store_host, account.pk, account.business_cycle, token)

        def prepare_sockets():
            refund = self.exercise_paid_addon_consultation(leave_notices_pending=True,
                                                         settle_before_recovery=bool(retirement_role))
            cases.append((self.store, refund.pk))
            if retirement_role:
                settled_at = refund.payment.segment_settlements.get().settled_at
                provider = retirement_role == 'replacement'
                account = self.replacement if provider else self.buyer
                audience = 'provider' if provider else 'boss'
                check_at = settled_at + (timedelta(days=8) if provider else timedelta(minutes=1))
                with patch('django.utils.timezone.now', return_value=check_at):
                    if provider:
                        submit, adjustment = self.prepare_refund_provider_retirement(settled_at)
                    else:
                        submit, adjustment = self.prepare_refund_buyer_retirement()
                retirements[self.store.slug] = (retirement_role, partial(self.retire_and_rejoin_socket_account,
                    self.store, self.store_host, account, submit, adjustment.pk, check_at, audience))
            for role, account in (('buyer', self.buyer), ('replacement', self.replacement), ('original', self.original)):
                save_account((self.store.slug, role), account)
            if self.store.slug == 'xinan' and not retirement_role:
                same_id = identities['shenglang', 'buyer'][1]
                peer = ClubAccount.objects.filter(pk=same_id).first()
                if peer is None:
                    peer = ClubAccount.objects.create_account(username='refund-socket-same-id', pk=same_id, account_type='BOSS')
                save_account(('xinan', 'same_id'), peer)

        self.run_in_stores(prepare_sockets)
        self.assertCountEqual([store.slug for store, _ in cases], ['shenglang', 'xinan'])
        if not retirement_role:
            self.assertEqual(identities['shenglang', 'buyer'][1:3], identities['xinan', 'same_id'][1:3])
        before = self.refund_notice_worker_facts(cases)
        self.assertTrue(all(len(row['intents']) == 2 and not row['messages'] for row in before.values()))
        with self.refund_notice_worker(redis_channels=True) as (publisher, worker, env, work):
            with socket.socket() as listener, (work / 'asgi.log').open('wb') as output:
                listener.bind(('127.0.0.1', 0))
                listener.listen()
                port = listener.getsockname()[1]
                process = subprocess.Popen([sys.executable, '-m', 'daphne', '--fd', str(listener.fileno()),
                    '-v', '0', 'config.asgi:application'], cwd=Path(__file__).resolve().parents[2], env=env,
                    pass_fds=(listener.fileno(),), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                listener.close()
                try:
                    asyncio.run(self.check_refund_notice_socket_events(port, identities, publisher, retirements))
                    self.assertIsNone(process.poll(), f'ASGI 提前退出，见 {work}')
                    print(f'退款通知 ASGI PID={process.pid} port={port} worker PID={worker.pid} logs={work.name}', flush=True)
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                self.assertEqual(process.returncode, 0)
        after = self.refund_notice_worker_facts(cases)
        for slug, case in after.items():
            self.assertEqual(case['refund'], before[slug]['refund'])
            self.assertCountEqual([row[:4] for row in case['intents']], [row[:4] for row in before[slug]['intents']])
            recipients = ('buyer', 'replacement')
            if retirement_role:
                host, account, cycle, token = identities[slug, retirement_role]
                skipped = next(row for row in case['intents'] if row[1] == account)
                self.assertEqual(skipped[2], cycle - (2 if retirement_role == 'replacement' else 1))
                self.assertIsNone(skipped[4])
                self.assertIsNotNone(skipped[5])
                self.assertIn('周期已变化', skipped[6])
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
                fresh = self.ok(client.get('/api/messages/', {'type': 'ORDER'}))
                self.assertFalse(any(row['related_order_id'] == case['order'] and row['title'] == '加购退款已处理'
                                     for row in fresh['messages']))
                recipients = tuple(role for role in recipients if role != retirement_role)
                print(f'{slug} refund={case["refund"][0]} skipped_notice={skipped[0]} cycle={skipped[2]}', flush=True)
            self.assertCountEqual(case['messages'], [(message, account, cycle)
                                  for _, account, cycle, _, message, _, _ in case['intents'] if message])
            self.assertEqual(len(case['messages']), len(recipients))
            for role in recipients:
                host, account, _, token = identities[slug, role]
                message_id = next(message for message, recipient, _ in case['messages'] if recipient == account)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
                detail = self.ok(client.get(f'/api/messages/{message_id}/'))
                self.assertEqual((detail['related_order_id'], detail['cycle_relation']), (case['order'], 'CURRENT'))
            print(f'{slug} order={case["order"]} refund={case["refund"][0]} messages={case["messages"]}', flush=True)

    @contextmanager
    def refund_notice_worker(self, *, redis_channels=False):
        from celery import Celery
        from stores.test_certificate_admission_mysql import _celery_process, _registered_redis_urls

        self.assertFalse(any(os.environ.get(key) for key in ('CELERY_BROKER_URL', 'CELERY_BROKER_READ_URL',
            'CELERY_BROKER_WRITE_URL', 'CELERY_RESULT_BACKEND')))
        broker, backend = _registered_redis_urls()
        directory = Path(os.environ['XA_SA14_MYSQL_REGISTRATION']).resolve().parent
        work = Path(mkdtemp(prefix='addon-refund-socket-' if redis_channels else 'addon-refund-race-worker-',
                            dir=directory))
        queue = f'xa_sa14_cli_{uuid4().hex}'
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.certificate_celery_test_settings',
               'XA_SA14_CELERY_QUEUE': queue, 'XA_SA14_CELERY_MEDIA': str(work / 'media'),
               'XA_SA14_CELERY_REDIS_CHANNELS': '1' if redis_channels else '0',
               'ADDON_REFUND_NOTICE_RECOVERY_ENABLED': '1', 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED': '1'}
        command = ['worker', '-P', 'solo', '-l', 'INFO', '--without-mingle', '--without-gossip', '--without-heartbeat']
        worker_log = work / 'worker.log'
        with (Celery(queue, broker=broker, backend=backend, set_as_current=False) as publisher,
              _celery_process(command, env=env, log=worker_log) as worker):
            publisher.conf.update(task_default_queue=queue, task_default_exchange=queue, task_default_routing_key=queue,
                                 broker_transport_options={'global_keyprefix': queue + ':'},
                                 result_backend_transport_options={'global_keyprefix': queue + ':'})
            deadline = monotonic() + 20
            while worker.poll() is None and monotonic() < deadline:
                if ' ready.' in worker_log.read_text():
                    break
                sleep(.1)
            else:
                self.fail(f'退款通知 worker 未就绪，见 {worker_log}')
            print(f'退款通知 worker PID={worker.pid} logs={work.name}', flush=True)
            yield publisher, worker, env, work
            self.assertIsNone(worker.poll(), f'退款通知 worker 提前退出，见 {worker_log}')
        self.assertEqual(worker.returncode, 0)

    async def check_refund_notice_socket_events(self, port, identities, publisher, retirements):
        import asyncio
        import json

        from autobahn.asyncio.websocket import WebSocketClientFactory, WebSocketClientProtocol

        loop = asyncio.get_running_loop()

        class Socket(WebSocketClientProtocol):
            def __init__(self):
                super().__init__()
                self.opened = loop.create_future()
                self.closed = loop.create_future()
                self.notifications = asyncio.Queue()
                self.pong = asyncio.Event()

            def onOpen(self):
                self.opened.set_result(True)

            def onClose(self, _was_clean, code, _reason):
                if not self.opened.done():
                    self.opened.set_result(False)
                if not self.closed.done():
                    self.closed.set_result(code)

            def onMessage(self, payload, _is_binary):
                frame = json.loads(payload)
                if frame.get('type') == 'site_message_update':
                    self.notifications.put_nowait(frame)
                elif frame.get('type') == 'pong':
                    self.pong.set()

        sockets, transports, previous = {}, [], {}

        async def connect(host, token):
            factory = WebSocketClientFactory(f'ws://{host}:{port}/ws/orders/?token={token}')
            factory.protocol = Socket
            transport, peer = await loop.create_connection(factory, '127.0.0.1', port)
            transports.append(transport)
            return peer

        async def check_pong(peer):
            peer.pong.clear()
            peer.sendMessage(b'{"type":"ping"}')
            await asyncio.wait_for(peer.pong.wait(), timeout=8)

        async def check_quiet(key, peer):
            try:
                frame = await asyncio.wait_for(peer.notifications.get(), timeout=.7)
            except TimeoutError:
                return
            self.fail(f'{key} 收到多余的退款通知刷新 {frame}')

        def recover(slug):
            task = publisher.send_task(recover_addon_refund_notices.name, headers={'store_slug': slug})
            result = task.get(timeout=20)
            print(f'{slug} 实时退款通知任务 {task.id} 回收 {result}', flush=True)
            return result

        try:
            for key, (host, _, _, token) in identities.items():
                peer = await connect(host, token)
                self.assertTrue(await asyncio.wait_for(peer.opened, timeout=15), key)
                sockets[key] = peer
            await asyncio.gather(*(check_pong(peer) for peer in sockets.values()))
            wrong = await connect(identities['xinan', 'buyer'][0], identities['shenglang', 'buyer'][3])
            self.assertFalse(await asyncio.wait_for(wrong.opened, timeout=8))
            for slug, (role, retire) in retirements.items():
                key = (slug, role)
                previous[slug] = sockets[key]
                host, _, _, old_token = identities[key]
                identities[key] = await asyncio.to_thread(retire, old_token)
                refused = await connect(host, old_token)
                self.assertFalse(await asyncio.wait_for(refused.opened, timeout=8), key)
                sockets[key] = await connect(host, identities[key][3])
                self.assertTrue(await asyncio.wait_for(sockets[key].opened, timeout=8), key)
                await check_pong(sockets[key])
            update = {'type': 'site_message_update', 'data': {}}
            for slug, count in (('shenglang', 2), ('xinan', 2), ('shenglang', 0), ('xinan', 0)):
                self.assertEqual(await asyncio.to_thread(recover, slug), count)
                if count:
                    retired_role = retirements[slug][0] if slug in retirements else None
                    roles = [role for role in ('buyer', 'replacement') if role != retired_role]
                    accounts = {identities[slug, role][1] for role in roles}
                    expected = [key for key in sockets if key[0] == slug and identities[key][1] in accounts]
                    frames = await asyncio.gather(*(asyncio.wait_for(sockets[key].notifications.get(), timeout=8)
                                                    for key in expected))
                    self.assertEqual(frames, [update] * len(expected))
                    print(f'{slug} 收到退款刷新 {expected}', flush=True)
                await asyncio.gather(*(check_quiet(key, peer) for key, peer in sockets.items()))
                if count and slug in previous:
                    old = previous[slug]
                    await check_quiet((slug, 'previous', retirements[slug][0]), old)
                    old.pong.clear()
                    if not old.closed.done():
                        old.sendMessage(b'{"type":"ping"}')
                    self.assertEqual(await asyncio.wait_for(old.closed, timeout=8), 4401)
                    self.assertFalse(old.pong.is_set())
                    print(f'{slug} 旧周期握手拒绝，原 WebSocket 关闭 4401', flush=True)
                await asyncio.gather(*(check_pong(peer) for peer in sockets.values()))
        finally:
            for transport in transports:
                transport.close()

    def transfer_and_join(self, *, share=(4000, 6000)):
        self.ok(self.transfer(self.api(self.staff), share=share))
        revision = OrderPointsTransferFundingRevision.objects.get(order_id=self.order_id)
        self.join_replacement(self.api(self.replacement), revision.last_transfer)

    def while_funding_reads_order(self, request, *, funding_units=1400):
        held, resume = Event(), Event()
        attempts = Queue()
        table = Order._meta.db_table

        def pause_funding(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if (not held.is_set() and f'`{table}`' in sql.lower() and 'for update' in sql.lower()
                    and self.order_id in (params or ())):
                held.set()
                self.assertTrue(resume.wait(8), '资金接口等待未在测试时限内结束')
            return result

        funding_url = f'/api/admin/platform/points-funding/{self.store.slug}/orders/{self.order_id}'
        with ThreadPoolExecutor(max_workers=2) as pool:
            funding = pool.submit(self.independent_request, lambda: self.finance_client.get(funding_url), pause_funding)
            try:
                self.assertTrue(held.wait(3), '平台资金接口未锁定原订单')
                pending = pool.submit(self.independent_request, request,
                    self.observe_lock_attempt(attempts, table, 'for update'))
                waited = self.wait_for_lock(pending, attempts, table=table)
                with transaction.atomic(using=self.store.database_alias):
                    ClubAccount.objects.select_for_update(nowait=True).get(pk=self.buyer.pk)
            finally:
                resume.set()
            status = self.ok(funding.result(timeout=10))
            self.assertEqual((status['required_units'], status['allocated_units'], status['remaining_units']),
                             (funding_units, funding_units, 0))
            return pending.result(timeout=10), waited

    def send_order_card(self, buyer, key):
        return buyer.post('/api/chat/send/', {'content': '请核对本单接替服务', 'order_ref': str(self.order_id),
                                              'client_message_id': key}, format='json')

    def exercise_chat_funding_race(self):
        self.transfer_and_join()
        buyer = self.api(self.buyer)
        key = 'transfer-chat-funding-race'
        response, waited = self.while_funding_reads_order(lambda: self.send_order_card(buyer, key))
        stored = ChatMessage.objects.get(pk=self.ok(response)['id'])
        replay = self.ok(self.send_order_card(buyer, key))
        self.assertEqual((stored.related_order_id, stored.sender_account_id, replay['id']),
                         (self.order_id, self.buyer.pk, stored.pk))
        self.assert_occupancy(self.replacement, self.original, 1)
        print(f'{self.store.slug} order={self.order_id} chat_message={stored.pk} chat_waiting={waited}', flush=True)

    def prepare_consultation(self):
        PayrollEventBarrier.objects.get_or_create(pk=1)
        self.transfer_and_join()
        return self.order_consultation()

    def order_consultation(self, *, extra_permissions=()):
        sent = self.ok(self.send_order_card(self.api(self.buyer), 'transfer-consultation-card'))
        message = ChatMessage.objects.get(pk=sent['id'])
        operator = make_console_user(['order:view', 'support:edit', 'chat:view', 'chat:reply', *extra_permissions])
        account = get_or_create_account_for_legacy_user(operator)
        membership = operator.admin_membership
        membership.account = account
        membership.save(update_fields=['account'])
        return self.api(account), membership, message

    def consultation_payload(self, message):
        return {'reason': '根据订单咨询登记售后', 'order_id': self.order_id, 'chat_message_id': message.pk,
                'client_request_id': 'transfer-consultation-aftersale', 'affected_provider_account_ids': [self.replacement.pk]}

    def make_consultation_addon(self):
        game = GameCategory.objects.create(name='咨询售后加购项目')
        self.service.game_category = game
        self.service.save(update_fields=['game_category'])
        addon = make_service(name='咨询售后测试加购', is_addon=True, game_category=game)
        config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
            enabled=True, condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
        return addon

    def pay_consultation_addon(self, provider, *, addon=None, request_id='consultation-addon-confirm', gift=0):
        if addon is None:
            addon = self.make_consultation_addon()
        buyer = self.api(self.buyer)
        path = f'/api/orders/orders/{self.order_id}/addons/intents/'
        intent = self.ok(buyer.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': provider.pk,
            'request_id': request_id, 'expected_amount': 1000, 'gift_balance_amount': gift,
            'compensation_balance_amount': 0}, format='json'))
        paid = self.ok(buyer.post(path + f'{intent["id"]}/pay/', {}, format='json'))
        return OrderAddonPayment.objects.select_related('intent', 'buyer_transaction').get(pk=paid['payment_id'])

    @override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True,
                       SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
                       AFTERSALE_OPEN_NOTICE_RECOVERY_ENABLED=True, ADDON_REFUND_NOTICE_RECOVERY_ENABLED=True)
    def exercise_paid_addon_consultation(self, *, retire_buyer=False, leave_notices_pending=False,
                                         settle_before_recovery=False):
        PayrollEventBarrier.objects.get_or_create(pk=1)
        self.transfer_and_join()
        transfer = OrderPointsTransferFundingRevision.objects.get(order_id=self.order_id).last_transfer
        self.ok(self.api(self.original).post(f'/api/orders/orders/{self.order_id}/participation-evidence/',
            {'segment_id': transfer.previous_segment_id, 'completion_image': release_fixture.screenshot(),
             'client_request_id': 'consultation-addon-original-evidence'}, format='multipart'))
        payment = self.pay_consultation_addon(self.replacement)
        self.assertEqual((payment.principal, payment.provider_income, payment.commission_fee,
                          payment.buyer_transaction.amount), (1000, 700, 300, -1000))
        self.ok(self.api(self.replacement).post(f'/api/orders/orders/{self.order_id}/complete/',
            {'completion_image': release_fixture.screenshot(), 'client_request_id': 'consultation-addon-complete'},
            format='multipart'))
        staff, _, message = self.order_consultation(extra_permissions=['order:refund'])
        self.ok(staff.post(f'/api/admin/chat-sessions/{message.session_id}/claim/', {'expected_version': 0}, format='json'))
        path = '/api/admin/aftersales/open-negotiation/'
        payload = {**self.consultation_payload(message), 'addon_payment_id': payment.pk,
                   'affected_segment_ids': [payment.intent.segment_id]}
        wrong_segment = {**payload, 'affected_segment_ids': [transfer.previous_segment_id]}
        rejected = staff.post(path, wrong_segment, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertFalse(AfterSale.objects.filter(order_id=self.order_id).exists())

        payment_events = PassReceiptUsageEvent.objects.filter(state__receipt=self.funding_receipt).order_by('version')
        events_before = list(payment_events.values_list('kind', 'amount'))
        self.assertEqual(events_before, [('ORDER', 2000), ('RELEASE', 600), ('RELEASE', 300)])
        response, payroll_waited = self.consultation_during_payroll(
            self.prepare_consultation_payroll(), staff, path, payload, message)
        opened = self.ok(response)
        self.assertEqual((opened['addon_payment'], opened['affected_segment_ids']), (payment.pk, [payment.intent.segment_id]))
        notice_ids = list(AfterSaleOpenNotice.objects.filter(case_id=opened['id']).order_by('pk').values_list('pk', flat=True))
        self.assertEqual(len(notice_ids), 2)
        replay, funding_waited = self.while_funding_reads_order(lambda: staff.post(path, payload, format='json'),
                                                               funding_units=1100)
        self.assertEqual(self.ok(replay)['id'], opened['id'])
        for changed in (wrong_segment, {key: value for key, value in payload.items() if key != 'affected_segment_ids'}):
            conflict = staff.post(path, changed, format='json')
            self.assertEqual((conflict.status_code, conflict.data['errorCode']), (409, 'AFTERSALE_REQUEST_CONFLICT'))
        row = AfterSale.objects.get(order_id=self.order_id)
        self.assertEqual(len(row.history), 1)
        self.assertEqual((row.history[0]['consultation_message_id'], row.history[0]['addon_payment_id'],
                          row.history[0]['affected_segment_ids']), (message.pk, payment.pk, [payment.intent.segment_id]))
        self.assertEqual(list(row.open_notice_intents.order_by('pk').values_list('pk', flat=True)), notice_ids)

        proposal_url = f'/api/admin/orders/{self.order_id}/addons/payments/{payment.pk}/refund-proposals/'
        proposal_payload = {'request_id': 'consultation-addon-proposal', 'amount_raw': 500,
                            'case_id': row.pk, 'explanation': '请双方确认加购退款方案'}
        opening = row.history[0]
        missing_scope = {key: value for key, value in opening.items() if key != 'affected_segment_ids'}
        damaged_histories = ([], {}, [None], [missing_scope], [{**opening, 'affected_segment_ids': None}],
                             [{**opening, 'affected_segment_ids': 'unknown'}],
                             [opening, {**opening, 'affected_segment_ids': [transfer.previous_segment_id]}])
        for damaged in damaged_histories:
            AfterSale.objects.filter(pk=row.pk).update(history=damaged)
            try:
                blocked = staff.post(proposal_url, proposal_payload, format='json')
                self.assertEqual(blocked.status_code, 400, blocked.data)
                self.assertFalse(payment.refund_proposals.exists())
            finally:
                AfterSale.objects.filter(pk=row.pk).update(history=row.history)
        proposal = self.ok(staff.post(proposal_url, proposal_payload, format='json'))
        self.assertEqual((proposal['status'], proposal['payment_id'], proposal['case_id']), ('PENDING', payment.pk, row.pk))
        self.assertEqual([(person['account_id'], share['segment_id'], share['refund_basis'])
                          for person in proposal['participants'] for share in person['share_plan']],
                         [(self.replacement.pk, payment.intent.segment_id, 500)])
        self.assertFalse(payment.refund_records.exists())
        self.assertEqual(list(payment_events.values_list('kind', 'amount')), events_before)
        self.assertEqual(list(Order.objects.get(pk=self.order_id).transactions.values_list('pk', 'tx_type', 'amount')),
                         self.funding_before)
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, self.balance_before - 1000)
        refund, refund_waits = self.execute_consultation_addon_refund(staff, row, payment, proposal, events_before)
        if leave_notices_pending:
            if settle_before_recovery:
                self.settle_consultation_addon_refund(staff, transfer, refund)
            return refund
        if retire_buyer:
            paid_addon = self.settle_consultation_addon_refund(staff, transfer, refund)
            with patch('django.utils.timezone.now', return_value=paid_addon.settled_at + timedelta(minutes=1)):
                notice_wait, messages = self.recover_notices_during_buyer_retirement(refund)
        else:
            notice_wait, messages = self.recover_consultation_addon_refund_notices(refund)
            paid_addon = self.settle_consultation_addon_refund(staff, transfer, refund)
        print(f'{self.store.slug} order={self.order_id} addon_payment={payment.pk} addon_segment={payment.intent.segment_id} '
              f'chat_message={message.pk} aftersale={row.pk} proposal={proposal["id"]} refund={refund.pk} '
              f'buyer_refund_transaction={refund.buyer_transaction_id} payroll_wait={payroll_waited} '
              f'funding_wait={funding_waited} refund_waits={refund_waits} '
              f'notice_wait={notice_wait} refund_messages={messages} '
              f'addon_income_transaction={paid_addon.income_transaction_id}:{paid_addon.amount}', flush=True)

    @override_settings(SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
                       AFTERSALE_OPEN_NOTICE_RECOVERY_ENABLED=True)
    def exercise_returned_addon_consultation(self):
        # A two-provider reservation has the same total price and half the
        # original-price share on the seat that changes providers.
        seat_share = self.service.price
        expected_main = 7700 if self.teammate else 7400
        refund_amount = 250 if self.gift_returned_addon else 100
        released = 70 if self.gift_returned_addon else 0
        gift = 820 if self.gift_returned_addon else 0
        required_funding = expected_main - 6000 - 260 + gift - released
        addon = self.make_consultation_addon()
        payment = self.pay_consultation_addon(self.original, addon=addon)
        same_seat_payment = None
        if self.same_seat_paid_addon:
            same_seat_payment = payment
            payment = self.pay_consultation_addon(
                self.original, addon=addon, request_id='consultation-same-seat-addon-confirm', gift=gift)
            required_funding -= 260
            same_seat_allocation_fields = ('pk', 'lot_id', 'kind', 'amount', 'refunded_amount')
            same_seat_allocations = list(same_seat_payment.allocations.order_by('pk').values_list(*same_seat_allocation_fields))
        teammate_payment = None
        if self.teammate_paid_addon:
            teammate_payment = self.pay_consultation_addon(
                self.teammate, addon=addon, request_id='consultation-teammate-addon-confirm')
            self.assertEqual((teammate_payment.principal, teammate_payment.provider_income,
                              teammate_payment.buyer_transaction.amount), (1000, 800, -1000))
            required_funding -= 200
            allocation_fields = ('pk', 'lot_id', 'kind', 'amount', 'refunded_amount')
            teammate_allocations = list(teammate_payment.allocations.order_by('pk').values_list(*allocation_fields))
            self.assertEqual(sum(row[3] for row in teammate_allocations), 1000)
            self.assertTrue(all(row[4] == 0 for row in teammate_allocations))
        dispatch = self.api(self.staff)
        buyer, original, replacement = (self.api(account) for account in (self.buyer, self.original, self.replacement))
        path = f'/api/orders/orders/{self.order_id}/'
        def hand_over(paid, target, retained, request_id):
            handover_url = f'/api/admin/orders/{self.order_id}/addons/payments/{paid.pk}/handovers/'
            proposal = self.ok(dispatch.post(handover_url, {'request_id': request_id,
                'target_provider_account_id': target.pk, 'retained_basis_raw': retained,
                'completed_work': '已完成的加购内容', 'remaining_work': '接替人继续完成剩余加购'}, format='json'))
            decision = path + f'addons/payments/{paid.pk}/handovers/{proposal["id"]}/decision/'
            for actor in (buyer, original, replacement):
                self.ok(actor.post(decision, {'accepted': True}, format='json'))
            return OrderAddonHandover.objects.get(pk=proposal['id'], status='ACCEPTED')

        transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=self.order_id)
        self.ok(self.transfer(dispatch, share=(4 * seat_share // 10, 6 * seat_share // 10)))
        transfer_out = transfers.get()
        self.join_replacement(replacement, transfer_out)
        self.ok(original.post(path + 'participation-evidence/', {'segment_id': transfer_out.previous_segment_id,
            'completion_image': release_fixture.screenshot(), 'client_request_id': 'consultation-first-leg-evidence'},
            format='multipart'))
        first = hand_over(payment, self.replacement, 400, 'consultation-addon-first-handover')
        if same_seat_payment:
            same_seat_first = hand_over(same_seat_payment, self.replacement, 400, 'consultation-other-addon-first-handover')
        self.ok(dispatch.post(f'/api/admin/orders/{self.order_id}/transfer/', {
            'reason': '由原陪玩接回剩余服务', 'transfers': [{
                'old_provider_id': self.replacement.pk, 'new_provider_id': self.original.pk,
                'original_share': {'retained': 2 * seat_share // 10, 'transferred': 4 * seat_share // 10,
                                   'agreement': '双方确认实际原价份额'},
            }]}, format='json'))
        transfer_back = transfers.exclude(pk=transfer_out.pk).get()
        revisions = OrderPointsTransferFundingRevision.objects.filter(order_id=self.order_id).order_by('pk')
        self.assertEqual(list(revisions.values_list('last_transfer_id', flat=True)),
                         [] if self.defer_funding else [transfer_out.pk, transfer_back.pk])
        self.join_replacement(original, transfer_back)
        second = hand_over(payment, self.original, 200, 'consultation-addon-return-handover')
        self.assertEqual((first.target_segment_id, second.previous_id, second.target_segment_id),
                         (transfer_back.previous_segment_id, first.pk, transfer_back.next_segment_id))
        shares = [(transfer_out.previous_segment_id, self.original.pk, 400, 320),
                  (transfer_back.previous_segment_id, self.replacement.pk, 200, 140),
                  (transfer_back.next_segment_id, self.original.pk, 400, 280)]
        self.assertCountEqual(second.shares.values_list('segment_id', 'segment__provider_account_id', 'basis', 'income'), shares)
        def complete(actor, request_id):
            return self.ok(actor.post(path + 'complete/', {'completion_image': release_fixture.screenshot(),
                'client_request_id': request_id}, format='multipart'))

        teammate_client = self.api(self.teammate) if self.teammate else None
        if teammate_client:
            teammate_segment = OrderParticipationSegment.objects.get(attribution__order_id=self.order_id,
                provider_account=self.teammate, released_at__isnull=True)
            self.assertEqual(Order.objects.get(pk=self.order_id).status, Order.Status.IN_SERVICE)
        else:
            complete(original, 'consultation-returned-addon-complete')
        staff, membership, message = self.order_consultation(extra_permissions=['order:refund'])
        self.ok(staff.post(f'/api/admin/chat-sessions/{message.session_id}/claim/', {'expected_version': 0}, format='json'))

        buyer_balance = Wallet.objects.get(account=self.buyer).balance
        usage = PassReceiptUsageEvent.objects.filter(state__receipt=self.funding_receipt).order_by('version')
        ledger = list(usage.values_list('pk', 'kind', 'amount'))
        if self.defer_funding:
            self.assertEqual(ledger, [])
        case_url = '/api/admin/aftersales/open-negotiation/'
        case_payload = {**self.consultation_payload(message),
            'addon_payment_id': payment.pk, 'affected_provider_account_ids': [self.original.pk],
            'affected_segment_ids': [transfer_back.next_segment_id]}
        handover_replays = [(first, replacement), (second, original)]
        if same_seat_payment:
            denied = staff.post(case_url, case_payload, format='json')
            self.assertEqual(denied.status_code, 400, denied.data)
            self.assertFalse(AfterSale.objects.filter(order_id=self.order_id).exists())
            same_seat_second = hand_over(same_seat_payment, self.original, 200, 'consultation-other-addon-return-handover')
            self.assertEqual((same_seat_second.previous_id, same_seat_second.target_segment_id),
                             (same_seat_first.pk, transfer_back.next_segment_id))
            self.assertCountEqual(same_seat_second.shares.values_list(
                'segment_id', 'segment__provider_account_id', 'basis', 'income'), shares)
            handover_replays.extend([(same_seat_first, replacement), (same_seat_second, original)])
            ledger = list(usage.values_list('pk', 'kind', 'amount'))
        funding_url = f'/api/admin/platform/points-funding/{self.store.slug}/orders/{self.order_id}'
        if released:
            later_allocation, allocation_url = self.fund_gift_addon_gap(funding_url, required_funding + released)
            self.assertEqual(list(payment.allocations.order_by('kind').values_list('kind', 'amount', 'refunded_amount')),
                             [('GIFT', 820, 0), ('PREPAID', 180, 0)])
        self.ok(replacement.post(path + 'participation-evidence/', {'segment_id': transfer_back.previous_segment_id,
            'completion_image': release_fixture.screenshot(), 'client_request_id': 'consultation-second-leg-evidence'},
            format='multipart'))
        handover_runs = OrderAddonFundingReleaseRun.objects.filter(
            handover__in=[handover for handover, _ in handover_replays]).order_by('pk')
        handover_hashes = list(handover_runs.values_list('pk', 'points_handover_hash'))
        self.assertEqual(len(handover_hashes), 0 if self.defer_funding else len(handover_replays))
        for wrong_segments in ([transfer_out.previous_segment_id], None):
            denied = staff.post(case_url, {**case_payload, 'affected_segment_ids': wrong_segments}, format='json')
            self.assertEqual(denied.status_code, 400, denied.data)
        if teammate_payment:
            self.assertEqual(teammate_payment.intent.segment_id, teammate_segment.pk)
            for change in ({'addon_payment_id': teammate_payment.pk},
                           {'affected_segment_ids': [teammate_segment.pk]},
                           {'affected_segment_ids': [transfer_back.next_segment_id, teammate_segment.pk]}):
                denied = staff.post(case_url, {**case_payload, **change}, format='json')
                self.assertEqual(denied.status_code, 400, denied.data)
        self.assertFalse(AfterSale.objects.filter(order_id=self.order_id).exists())
        self.assertFalse(payment.refund_proposals.exists())
        self.assertFalse(payment.refund_records.exists())
        self.assertFalse(AfterSaleOpenNotice.objects.filter(case__order_id=self.order_id).exists())
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, buyer_balance)
        self.assertEqual(list(usage.values_list('pk', 'kind', 'amount')), ledger)

        opened = self.ok(staff.post(case_url, case_payload, format='json'))
        self.assertEqual(opened['affected_segment_ids'], [transfer_back.next_segment_id])
        self.assertEqual(self.ok(staff.post(case_url, case_payload, format='json'))['id'], opened['id'])
        if same_seat_payment:
            swapped = staff.post(case_url, {**case_payload, 'addon_payment_id': same_seat_payment.pk}, format='json')
            self.assertEqual(swapped.status_code, 409, swapped.data)
        if teammate_client:
            detail = self.ok(teammate_client.get(path))
            self.assertEqual(detail['aftersale_status'], '')
            recipients = AfterSaleOpenNotice.objects.filter(case_id=opened['id']).values_list('account_id', flat=True)
            self.assertCountEqual(recipients, [self.buyer.pk, self.original.pk])
        proposal_url = f'/api/admin/orders/{self.order_id}/addons/payments/{payment.pk}/refund-proposals/'
        proposal_payload = {'request_id': 'consultation-returned-addon-refund', 'case_id': opened['id'],
                            'amount_raw': refund_amount, 'explanation': '只退转回后未完成的加购部分'}
        if released:
            fractional = staff.post(proposal_url, {**proposal_payload, 'amount_raw': 101}, format='json')
            self.assertEqual(fractional.status_code, 400, fractional.data)
        other_payment = teammate_payment or same_seat_payment
        if other_payment:
            wrong_payment_url = f'/api/admin/orders/{self.order_id}/addons/payments/{other_payment.pk}/refund-proposals/'
            denied = staff.post(wrong_payment_url, proposal_payload, format='json')
            self.assertEqual(denied.status_code, 400, denied.data)
        over = staff.post(proposal_url, {**proposal_payload, 'amount_raw': 290}, format='json')
        self.assertEqual(over.status_code, 400, over.data)
        self.assertFalse(payment.refund_proposals.exists())
        proposal = self.ok(staff.post(proposal_url, proposal_payload, format='json'))
        self.assertEqual([(person['account_id'], row['segment_id'], row['refund_basis'])
                          for person in proposal['participants'] for row in person['share_plan']],
                         [(self.original.pk, transfer_back.next_segment_id, refund_amount)])
        decision = path + f'addons/payments/{payment.pk}/refund-proposals/{proposal["id"]}/'
        self.assertEqual(replacement.post(decision, {'accepted': True}, format='json').status_code, 404)
        if teammate_client:
            self.assertEqual(teammate_client.get(decision).status_code, 404)
            if teammate_payment:
                self.assertEqual(teammate_client.post(decision, {'accepted': True}, format='json').status_code, 404)
        for actor in (buyer, original):
            self.ok(actor.post(decision, {'accepted': True}, format='json'))
        execute_url = proposal_url + f'{proposal["id"]}/execute/'
        waits = []
        if teammate_client and not self.main_refund_after_addon:
            clients = [self.api(membership.account) for _ in range(2)]
            table = Order._meta.db_table
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = []
                with transaction.atomic(using=self.store.database_alias):
                    Order.objects.select_for_update().get(pk=self.order_id)
                    for client in clients:
                        attempts = Queue()
                        request = pool.submit(self.independent_request,
                            lambda client=client: client.post(execute_url, {}, format='json'),
                            self.observe_lock_attempt(attempts, table, 'for update', account_id=self.order_id))
                        pending.append(request)
                        waits.append(self.wait_for_lock(request, attempts, table=table))
                receipt, replay = [self.ok(request.result(timeout=20)) for request in pending]
            self.assertEqual(len(set(waits)), 2)
            self.assertEqual(receipt, replay)
        else:
            receipt = self.ok(staff.post(execute_url, {}, format='json'))
        self.assertEqual(self.ok(staff.post(execute_url, {}, format='json')), receipt)
        refund = payment.refund_records.get()
        self.assertEqual((refund.amount, refund.buyer_transaction.amount, refund.addon_pending_deduction,
                         refund.main_pending_deduction, refund.wallet_deduction, refund.deposit_deduction,
                         refund.rounding_surplus), (refund_amount, refund_amount, refund_amount, 0, 0, 0, 0))
        self.assertEqual(list(refund.earning_deductions.values_list('payment_id', 'segment_id', 'amount')),
                         [(payment.pk, transfer_back.next_segment_id, refund_amount)])
        self.assertEqual((refund.funding_release_run.required_amount, refund.funding_release_run.released_amount),
                         (required_funding, released))
        refund_runs = OrderAddonFundingReleaseRun.objects.filter(refund=refund)
        version_fields = ('pk', 'allocated_before', 'allocation_snapshot', 'points_addon_snapshot', 'points_addon_hash')
        version = refund_runs.values(*version_fields).get()
        if self.defer_funding:
            self.assertEqual((version['allocated_before'], version['allocation_snapshot']), (0, []))
            self.assertTrue(version['points_addon_hash'])
        if released:
            release = refund.funding_release_run.releases.get()
            self.assertEqual((release.allocation_id, release.amount), (later_allocation.pk, released))
            self.assertEqual(list(payment.allocations.order_by('kind').values_list('kind', 'refunded_amount')),
                             [('GIFT', 70), ('PREPAID', 180)])
            later_usage = PassReceiptUsageEvent.objects.filter(state__receipt=later_allocation.receipt).order_by('version')
            event = later_usage.get(request_key=f'points-addon-release:{release.pk}')
            self.assertEqual((event.kind, event.allocation_key, event.amount),
                             ('RELEASE', f'order:{release.allocation_id}', released))
            later_ledger = list(later_usage.values_list('kind', 'amount'))
            self.assertEqual(later_ledger, [('ORDER', later_allocation.amount), ('RELEASE', released)])
            print(f'{self.store.slug} order={self.order_id} refund_release={release.pk} allocation={release.allocation_id} '
                  f'receipt={later_allocation.receipt_id} usage_event={event.pk} amount={released}', flush=True)
        self.assertEqual(list(usage.values_list('pk', 'kind', 'amount')), ledger)
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, buyer_balance + refund_amount)
        if teammate_client and not self.main_refund_after_addon:
            self.assertEqual(Order.objects.get(pk=self.order_id).status, Order.Status.IN_SERVICE)
            self.assertEqual(AfterSale.objects.get(pk=opened['id']).status, 'OPEN')
            complete(teammate_client, 'consultation-unaffected-teammate-complete')
            in_service = Order.objects.get(pk=self.order_id)
            self.assertEqual(in_service.status, Order.Status.IN_SERVICE)
            self.assertTrue(in_service.provider_completion_facts.filter(segment=teammate_segment,
                                                                       provider_account=self.teammate).exists())
        self.ok(dispatch.post(f'/api/admin/aftersales/{opened["id"]}/resolve/',
            {'resolution': '按原方案退回转回段未完成的加购'}, format='json'))
        self.assertEqual(self.ok(staff.post(case_url, case_payload, format='json'))['id'], opened['id'])
        repeat_case = staff.post(case_url, {**case_payload, 'client_request_id': 'new-returned-addon-case'}, format='json')
        self.assertEqual(repeat_case.status_code, 400, repeat_case.data)
        if same_seat_payment:
            other_case = staff.post(case_url, {**case_payload, 'addon_payment_id': same_seat_payment.pk,
                'client_request_id': 'new-case-other-same-seat-payment'}, format='json')
            self.assertEqual(other_case.status_code, 400, other_case.data)

        if self.main_refund_after_addon:
            self.exercise_main_refund_after_addon(staff, buyer, original, teammate_client, teammate_segment,
                transfer_out, transfer_back, case_payload, execute_url, receipt, refund, same_seat_payment,
                version_fields, version, funding_url, usage, ledger)
            return

        reporting = [
            (self.original, transfer_out.previous_segment_id), (self.replacement, transfer_back.previous_segment_id),
            (self.original, transfer_back.next_segment_id),
        ]
        if teammate_client:
            complete(original, 'consultation-returned-addon-complete')
            reporting.append((self.teammate, teammate_segment.pk))
        reports = [self.approve_report(account, segment) for account, segment in reporting]
        order = Order.objects.get(pk=self.order_id)
        self.assertEqual(order.status, Order.Status.COMPLETED)
        due = max(report.submitted_at for report in reports) + timedelta(hours=order.service_freeze_hours)
        funding = self.ok(self.finance_client.get(funding_url))
        self.assertEqual((funding['required_units'], funding['allocated_units']),
                         (required_funding, 0 if self.defer_funding else required_funding))
        with patch('django.utils.timezone.now', return_value=due):
            if self.defer_funding:
                funding_receipts = self.allocate_funding_after_returned_addon_refund(
                    order, funding_url, refund.created_at, required_funding)
                self.assertEqual(self.ok(staff.post(execute_url, {}, format='json')), receipt)
                self.assertEqual(refund_runs.values(*version_fields).get(), version)
                self.assertEqual(list(usage.values_list('kind', 'amount')),
                                 [('ORDER', required_funding - 10), ('ORDER', 10)])
                if teammate_payment:
                    source = teammate_payment.allocations.filter(pk=teammate_allocations[0][0])
                    source.update(refunded_amount=1)
                    try:
                        self.assert_returned_refund_blocks_settlement(
                            order, staff, execute_url, funding_url, '加购退款与原资金来源不一致')
                    finally:
                        source.update(refunded_amount=0)
            run = refund.funding_release_run
            runs = OrderAddonFundingReleaseRun.objects.filter(pk=run.pk)
            if same_seat_payment and not self.defer_funding:
                other_runs = handover_runs.filter(handover=same_seat_second)
                other_hash = other_runs.values_list('points_handover_hash', flat=True).get()
                other_runs.update(points_handover_hash='0' * 64)
                try:
                    self.assert_returned_refund_blocks_settlement(
                        order, staff, execute_url, funding_url, '积分加购接替补资快照异常')
                finally:
                    other_runs.update(points_handover_hash=other_hash)
            allocation_error = ('加购补资释放金额不一致' if released
                                else '转回段加购退款的补资分配时序或范围异常')
            if self.defer_funding:
                payload, result = funding_receipts[0]
                amount = payload['amount']
                wrong_allocations = {'allocated_before': amount,
                                     'allocation_snapshot': [[result['allocation_id'], amount, 0, []]]}
                late = order.points_redemption.funding_allocations.filter(pk=result['allocation_id'])
                actual_time = late.values_list('created_at', flat=True).get()
                late.update(created_at=refund.created_at)
                try:
                    self.assert_returned_refund_blocks_settlement(order, staff, execute_url, funding_url, allocation_error)
                finally:
                    late.update(created_at=actual_time)
            else:
                wrong_allocations = {'allocated_before': 0, 'allocation_snapshot': []}
            for updates, reason in (
                (wrong_allocations, allocation_error),
                ({'points_addon_hash': '0' * 64}, '积分加购退款补资快照异常'),
            ):
                runs.update(**updates)
                try:
                    self.assert_returned_refund_blocks_settlement(order, staff, execute_url, funding_url, reason)
                finally:
                    runs.update(**{field: getattr(run, field) for field in updates})
            self.assertEqual(confirm_due_consumption.run(), 1)
            self.assertEqual(confirm_due_consumption.run(), 0)
        order.refresh_from_db()
        self.assertEqual((order.provider_income, order.settlement_store_funding), (expected_main, required_funding))
        if teammate_client:
            payouts = OrderSegmentPersonSettlement.objects.filter(manifest__order=order)
            self.assertCountEqual(payouts.values_list('account_id', 'amount'),
                                  [(self.original.pk, 3000), (self.replacement.pk, 700), (self.teammate.pk, 4000)])
            unaffected = payouts.get(account=self.teammate)
            self.assertEqual((unaffected.amount, unaffected.segment_ids, unaffected.income_transaction.amount),
                             (4000, [teammate_segment.pk], 4000))
        self.assertCountEqual(OrderSegmentAddonSettlement.objects.filter(payment=payment).values_list('segment_id', 'amount'),
                              [(transfer_out.previous_segment_id, 320), (transfer_back.previous_segment_id, 140),
                               (transfer_back.next_segment_id, 280 - refund_amount)])
        consumption = OrderAddonConsumption.objects.get(payment=payment)
        self.assertEqual((consumption.paid_units, consumption.refunded_units), (1000, refund_amount))
        self.assertEqual(self.ok(staff.post(execute_url, {}, format='json')), receipt)
        self.assertEqual(refund_runs.values(*version_fields).get(), version)
        if teammate_payment:
            untouched = OrderAddonPayment.objects.select_related('intent', 'buyer_transaction').get(pk=teammate_payment.pk)
            self.assertEqual((untouched.intent.segment_id, untouched.buyer_transaction_id, untouched.buyer_transaction.amount),
                             (teammate_segment.pk, teammate_payment.buyer_transaction_id, -1000))
            self.assertEqual(list(untouched.allocations.order_by('pk').values_list(*allocation_fields)), teammate_allocations)
            self.assertFalse(untouched.handovers.exists())
            self.assertFalse(untouched.refund_proposals.exists())
            self.assertFalse(untouched.refund_records.exists())
            self.assertFalse(untouched.refund_earning_deductions.exists())
            payout = untouched.segment_settlements.get()
            self.assertEqual((payout.segment_id, payout.report_id, payout.amount, payout.income_transaction.amount,
                              payout.income_transaction.wallet.account_id),
                             (teammate_segment.pk, reports[-1].report_id, 800, 800, self.teammate.pk))
            teammate_consumption = OrderAddonConsumption.objects.get(payment=untouched)
            self.assertEqual((teammate_consumption.paid_units, teammate_consumption.refunded_units), (1000, 0))
            print(f'{self.store.slug} order={order.pk} teammate_payment={untouched.pk} '
                  f'teammate_segment={teammate_segment.pk} teammate_debit={untouched.buyer_transaction_id} '
                  f'teammate_report={reports[-1].pk} teammate_income={payout.income_transaction_id}:{payout.amount} '
                  f'teammate_consumption={teammate_consumption.pk}', flush=True)
        if self.defer_funding:
            for payload, result in funding_receipts:
                self.assertEqual(self.ok(self.finance_client.post(self.funding_url, payload, format='json')), result)
            funding = self.ok(self.finance_client.get(funding_url))
            self.assertEqual((funding['required_units'], funding['allocated_units'], funding['remaining_units']),
                             (required_funding, required_funding, 0))
            self.assertEqual(list(usage.values_list('kind', 'amount')),
                             [('ORDER', required_funding - 10), ('ORDER', 10)])
        self.assertEqual(payment.refund_records.count(), 1)
        if released:
            self.assertEqual(list(usage.values_list('pk', 'kind', 'amount')), ledger)
            self.assertEqual(list(later_usage.values_list('kind', 'amount')), later_ledger)
            reused = self.reuse_gift_addon_release(allocation_url, order, released)
            self.assertEqual(self.ok(staff.post(execute_url, {}, format='json')), receipt)
            self.assertEqual(refund_runs.values(*version_fields).get(), version)
            self.assertEqual(payment.refund_records.count(), 1)
            self.assertEqual(list(later_usage.values_list('kind', 'amount')), [*later_ledger, ('ORDER', released)])
            self.assertEqual(later_usage.last().allocation_key, f'order:{reused["allocation_id"]}')
            funding = self.ok(self.finance_client.get(funding_url))
            self.assertEqual((funding['required_units'], funding['allocated_units'], funding['remaining_units']),
                             (required_funding, required_funding, 0))
        self.assertEqual(AdminAuditLog.objects.filter(path=execute_url, resource='addon-refund', status_code=200).count(), 1)
        for handover, actor in handover_replays:
            handover_response = self.ok(actor.post(path + f'addons/payments/{handover.payment_id}/handovers/{handover.pk}/decision/',
                {'accepted': True}, format='json'))
            self.assertEqual((handover_response['id'], handover_response['status']), (handover.pk, 'ACCEPTED'))
        self.assertEqual(list(handover_runs.values_list('pk', 'points_handover_hash')), handover_hashes)
        if same_seat_payment:
            self.assertFalse(same_seat_payment.refund_records.exists())
            self.assertFalse(same_seat_payment.refund_proposals.exists())
            self.assertFalse(same_seat_payment.refund_earning_deductions.exists())
            self.assertEqual(list(same_seat_payment.allocations.order_by('pk').values_list(*same_seat_allocation_fields)),
                             same_seat_allocations)
            self.assertCountEqual(same_seat_payment.segment_settlements.values_list(
                'segment_id', 'amount', 'income_transaction__amount', 'income_transaction__wallet__account_id'),
                [(segment, income, income, account) for segment, account, _, income in shares])
            untouched = OrderAddonConsumption.objects.get(payment=same_seat_payment)
            self.assertEqual((untouched.paid_units, untouched.refunded_units), (1000, 0))
            print(f'{self.store.slug} order={order.pk} same_seat_payment={same_seat_payment.pk} '
                  f'same_seat_handovers={[same_seat_first.pk, same_seat_second.pk]} '
                  f'original_debit={same_seat_payment.buyer_transaction_id} '
                  f'income={list(same_seat_payment.segment_settlements.values_list("income_transaction_id", "amount"))}', flush=True)
        print(f'{self.store.slug} order={order.pk} consultation={message.pk} payment={payment.pk} '
              f'handovers={[first.pk, second.pk]} segments={shares} case={opened["id"]} proposal={proposal["id"]} '
              f'refund={refund.pk} buyer_transaction={refund.buyer_transaction_id} '
              f'funding={order.settlement_store_funding} consumption={consumption.pk} order_waits={waits}', flush=True)

    def exercise_main_refund_after_addon(self, staff, buyer, original, teammate, teammate_segment,
            transfer_out, transfer_back, case_payload, addon_url, addon_receipt, addon_refund, other_payment,
            version_fields, version, funding_url, usage, ledger):
        case_url = '/api/admin/aftersales/open-negotiation/'
        main_payload = {key: value for key, value in case_payload.items() if key != 'addon_payment_id'}
        main_payload.update({'reason': '另行协商留队席未完成的主服务', 'client_request_id': 'main-case-after-addon',
                             'affected_provider_account_ids': [self.teammate.pk],
                             'affected_segment_ids': [teammate_segment.pk]})
        wrong = staff.post(case_url, {**main_payload,
            'affected_provider_account_ids': [self.original.pk],
            'affected_segment_ids': [transfer_back.next_segment_id]}, format='json')
        self.assertEqual(wrong.status_code, 400, wrong.data)
        case = self.ok(staff.post(case_url, main_payload, format='json'))
        self.assertIsNone(AfterSale.objects.get(pk=case['id']).addon_payment_id)
        self.assertEqual(case['affected_segment_ids'], [teammate_segment.pk])
        saved_case = staff.post(case_url, case_payload, format='json')
        self.assertEqual((saved_case.status_code, saved_case.data['errorCode']), (409, 'AFTERSALE_REQUEST_PREVIOUS'))
        admin_path = f'/api/admin/aftersales/{case["id"]}/'
        preview = self.ok(staff.get(admin_path + 'negotiation-preview/', {'refund_units': 1000}))
        self.assertTrue(preview['agreement_available'], preview)
        self.assertTrue(preview['execution_available'], preview)
        self.assertEqual([(term['account_id'], term['retained_units']) for term in preview['provider_terms']],
                         [(self.teammate.pk, 3000)])
        proposed = self.ok(staff.post(admin_path + 'negotiation-proposal/', {
            'request_id': 'main-proposal-after-addon', 'expected_version': 0, 'refund_units': 1000,
            'provider_terms': [{'account_id': self.teammate.pk, 'retained_units': 3000}],
            'explanation': '协商退回未履约主服务以及相应积分'}, format='json'))
        path = f'/api/orders/orders/{self.order_id}/'
        decision = {'proposal_id': proposed['id'], 'accepted': True}
        denied = original.post(path + 'negotiation-proposal/', decision, format='json')
        self.assertEqual(denied.status_code, 404, denied.data)
        for actor in (buyer, teammate):
            self.ok(actor.post(path + 'negotiation-proposal/', decision, format='json'))
        money_before = Wallet.objects.get(account=self.buyer).balance
        points_before = MemberProfile.objects.get(account=self.buyer).points_balance
        main_url = admin_path + 'negotiation-execution/'
        payload = {'proposal_id': proposed['id'], 'client_request_id': 'main-refund-after-addon'}
        executed = self.ok(staff.post(main_url, payload, format='json'))
        posting = PointsNegotiationPosting.objects.get(pk=executed['execution_id'])
        main = posting.points_refund
        self.assertEqual((main.equivalent_units, main.coin_units, main.prepaid_units, main.points_hundredths),
                         (1000, 600, 600, 1000))
        self.assertEqual((main.coin_transaction.amount, main.points_ledger.amount), (600, 1000))
        self.assertEqual(list(posting.shares.values_list(
            'account_id', 'pending_deduction', 'retained_income', 'wallet_deduction', 'deposit_deduction')),
            [(self.teammate.pk, 1000, 3000, 0, 0)])
        self.assertEqual((Wallet.objects.get(account=self.buyer).balance,
                          MemberProfile.objects.get(account=self.buyer).points_balance),
                         (money_before + 600, points_before + 1000))
        self.assertEqual(posting.funding_release_evidence.expected_release, 400)
        events = list(usage.values_list('pk', 'kind', 'amount'))
        self.assertEqual(events[:-1], ledger)
        self.assertEqual(events[-1][1:], ('RELEASE', 400))
        self.assertEqual(self.ok(staff.post(main_url, payload, format='json'))['execution_id'], posting.pk)
        self.assertEqual(self.ok(staff.post(addon_url, {}, format='json')), addon_receipt)
        self.assertEqual(OrderAddonFundingReleaseRun.objects.filter(refund=addon_refund).values(*version_fields).get(), version)
        order = Order.objects.get(pk=self.order_id)
        self.assertEqual((order.status, order.payment_status), (Order.Status.IN_SERVICE, Order.PaymentStatus.PARTIALLY_REFUNDED))
        self.assertFalse(order.transactions.filter(tx_type=Transaction.TxType.INCOME).exists())
        for account, segment in ((self.original, transfer_back.next_segment_id), (self.teammate, teammate_segment.pk)):
            reopened = staff.post(case_url, {**main_payload, 'client_request_id': f'another-main-case-{segment}',
                'affected_provider_account_ids': [account.pk], 'affected_segment_ids': [segment]}, format='json')
            self.assertEqual(reopened.status_code, 400, reopened.data)
        self.assertEqual(order.refund_records.count(), 1)
        self.ok(teammate.post(path + 'participation-evidence/', {'segment_id': teammate_segment.pk,
            'completion_image': release_fixture.screenshot(), 'client_request_id': 'main-refund-released-segment'},
            format='multipart'))
        self.assert_main_refund_completion_actions(path, original, teammate)
        self.ok(original.post(path + 'complete/', {'completion_image': release_fixture.screenshot(),
            'client_request_id': 'main-refund-unaffected-seat-complete'}, format='multipart'))
        with connections[self.store.database_alias].execute_wrapper(self.reject_detail_writes):
            detail = self.ok(original.get(path))
        self.assertCountEqual(
            [(row['id'], row['segment_report_available']) for row in detail['my_participation_segments']],
            [(transfer_out.previous_segment_id, True), (transfer_back.next_segment_id, True)])
        reporting = [(self.original, transfer_out.previous_segment_id),
                     (self.replacement, transfer_back.previous_segment_id),
                     (self.original, transfer_back.next_segment_id), (self.teammate, teammate_segment.pk)]
        reports = [self.approve_report(account, segment, verify_detail=index == 0)
                   for index, (account, segment) in enumerate(reporting)]
        due = max(report.submitted_at for report in reports) + timedelta(hours=order.service_freeze_hours)
        with patch('django.utils.timezone.now', return_value=due):
            funding = self.ok(self.finance_client.get(funding_url))
            self.assertEqual((funding['required_units'], funding['allocated_units']), (780, 780))
            runs = OrderAddonFundingReleaseRun.objects.filter(refund=addon_refund)
            runs.update(points_addon_hash='0' * 64)
            try:
                blocked = self.finance_client.get(funding_url)
                self.assertEqual((blocked.status_code, blocked.data['errorCode']), (409, 'POINTS_FUNDING_REVIEW'))
                self.assertEqual(staff.post(addon_url, {}, format='json').status_code, 400)
                with self.assertLogs('engagement.tasks', level='ERROR'):
                    self.assertEqual(confirm_due_consumption.run(), 0)
                self.assertFalse(order.transactions.filter(tx_type=Transaction.TxType.INCOME).exists())
            finally:
                runs.update(points_addon_hash=version['points_addon_hash'])
            self.assertEqual(confirm_due_consumption.run(), 1)
            self.assertEqual(confirm_due_consumption.run(), 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.settled_at)
        self.assertEqual((order.provider_income, order.settlement_store_funding), (6700, 780))
        self.assertCountEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).values_list(
            'account_id', 'amount', 'income_transaction__amount'), [
                (self.original.pk, 3000, 3000), (self.replacement.pk, 700, 700), (self.teammate.pk, 3000, 3000)])
        membership = TransferRefundPointsConsumptionEvidence.objects.get(posting=posting)
        self.assertEqual((membership.paid_units, membership.refunded_coin_units,
                          membership.net_paid_units, membership.net_principal_units), (6000, 600, 5400, 5400))
        for payment, expected in ((addon_refund.payment, (320, 140, 180)), (other_payment, (320, 140, 280))):
            self.assertCountEqual(payment.segment_settlements.values_list('segment_id', 'amount'),
                                  list(zip((item[1] for item in reporting[:3]), expected)))
        self.assertEqual(list(OrderAddonConsumption.objects.filter(payment__intent__order=order)
            .order_by('payment_id').values_list('payment_id', 'paid_units', 'refunded_units', 'refunded_principal_units')),
            [(other_payment.pk, 1000, 0, 0), (addon_refund.payment_id, 1000, 100, 100)])
        self.assertFalse(other_payment.refund_records.exists())
        self.assertEqual(self.ok(staff.post(main_url, payload, format='json'))['execution_id'], posting.pk)
        self.assertEqual(self.ok(staff.post(addon_url, {}, format='json')), addon_receipt)
        self.assertEqual(OrderAddonRefund.objects.filter(order=order).count(), 1)
        self.assertEqual(order.refund_records.count(), 1)
        self.assertEqual(list(usage.values_list('pk', 'kind', 'amount')), events)
        print(f'{self.store.slug} order={order.pk} addon_refund={addon_refund.pk} main_posting={posting.pk} '
              f'main_refund={main.refund_id} main_credit={main.coin_transaction_id} points={main.points_ledger_id} '
              f'funding=780 release={events[-1][0]} unchanged_payment={other_payment.pk}', flush=True)

    def assert_main_refund_completion_actions(self, detail_path, continuing, terminated):
        listing = '/api/orders/orders/?status=in_progress&role=provider&page=1&page_size=20'
        alias = self.store.database_alias
        with connections[alias].execute_wrapper(self.reject_detail_writes):
            detail = self.ok(continuing.get(detail_path))
            rows = self.ok(continuing.get(listing))['list']
            listed = next(row for row in rows if row['id'] == self.order_id)
            for row in (detail, listed):
                self.assertIn('COMPLETE', row['allowed_actions'])
                self.assertFalse(row['my_assignment_released'])
                progress = row['team_progress']
                self.assertEqual((progress['stage'], progress['submitted_count'], progress['total_count']),
                                 ('COMPLETION', 0, 1))
                self.assertEqual([member['id'] for member in progress['members']], [self.original.pk])
            for client in (terminated, self.api(self.replacement)):
                other = self.ok(client.get(detail_path))
                self.assertNotIn('COMPLETE', other['allowed_actions'])
                self.assertTrue(other['my_assignment_released'])

    def fund_gift_addon_gap(self, funding_url, required):
        funding = self.ok(self.finance_client.get(funding_url))
        self.assertEqual(funding['required_units'], required)
        gap = funding['remaining_units']
        self.assertGreaterEqual(gap, 70)
        url = f'/api/admin/platform/pass-refund-repayments/{self.store.slug}/'
        claim = self.ok(self.finance_client.post(url + 'register', {'request_id': str(uuid4()),
            'amount': gap, 'external_reference': f'POINTS-ADDON-GIFT-{self.store.slug}',
            'received_at': timezone.now().isoformat(), 'evidence': '隔离测试加购补资到账'}, format='json'))['claim_id']
        self.ok(self.finance_client.post(f'{url}{claim}/verify', {'request_id': str(uuid4())}, format='json'))
        allocation_url = f'{url}{claim}/points-allocation'
        allocated = self.ok(self.finance_client.post(allocation_url, {
            'request_id': str(uuid4()), 'amount': gap, 'order_id': self.order_id}, format='json'))
        self.assertTrue(self.ok(self.finance_client.get(funding_url))['fully_funded'])
        return self.receipt.funding_allocations.get(pk=allocated['allocation_id']), allocation_url

    def reuse_gift_addon_release(self, allocation_url, order, amount):
        fresh_service = make_service(price=5000, commission_rate=20)
        fresh_service.billing_provider_count = fresh_service.fulfillment_provider_count = 2
        fresh_service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count'])
        _, new_order_id = self.book_points_reservation([self.original, self.teammate],
            self.start + timedelta(hours=3), 'consultation-reuse-addon-release', service=fresh_service)
        payload = {'request_id': str(uuid4()), 'amount': amount, 'order_id': new_order_id}
        allocate = lambda: self.finance_client.post(allocation_url, payload, format='json')
        attempts = Queue()
        observer = self.observe_lock_attempt(attempts, Order._meta.db_table, 'for update', account_id=new_order_id)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic(using=self.store.database_alias):
                Order.objects.select_for_update().get(pk=order.pk)
                # Receipt-local replay must not need the original order's row lock.
                reused = self.ok(pool.submit(self.independent_request, allocate, observer).result(timeout=15))
                session = attempts.get_nowait()
        self.assertEqual(self.ok(allocate()), reused)
        self.assertEqual((reused['order_id'], reused['amount']), (new_order_id, amount))
        print(f'{self.store.slug} original_order={order.pk} reuse_order={new_order_id} '
              f'reuse_allocation={reused["allocation_id"]} session={session} request={payload["request_id"]}', flush=True)
        return reused

    def assert_returned_refund_blocks_settlement(self, order, staff, execute_url, funding_url, reason):
        review = self.finance_client.get(funding_url)
        self.assertEqual(review.status_code, 409, review.data)
        self.assertEqual(review.data['errorCode'], 'POINTS_FUNDING_REVIEW')
        self.assertEqual(staff.post(execute_url, {}, format='json').status_code, 400)
        with self.assertLogs('engagement.tasks', level='ERROR') as blocked:
            self.assertEqual(confirm_due_consumption.run(), 0)
        self.assertIn(reason, '\n'.join(blocked.output))
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        self.assertFalse(order.transactions.filter(tx_type=Transaction.TxType.INCOME).exists())

    def allocate_funding_after_returned_addon_refund(self, order, funding_url, refund_at, required):
        first = {**self.funding_payload, 'amount': required - 10}
        last = {**self.funding_payload, 'request_id': str(uuid4()), 'amount': 10}
        receipts = []
        for payload, before in ((first, 0), (last, required - 10)):
            funding = self.ok(self.finance_client.get(funding_url))
            self.assertEqual((funding['allocated_units'], funding['remaining_units']), (before, required - before))
            self.assertEqual(confirm_due_consumption.run(), 0)
            self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
            self.assertFalse(order.transactions.filter(tx_type=Transaction.TxType.INCOME).exists())
            record = self.ok(self.finance_client.post(self.funding_url, payload, format='json'))
            self.assertEqual(self.ok(self.finance_client.post(self.funding_url, payload, format='json')), record)
            allocation = order.points_redemption.funding_allocations.get(pk=record['allocation_id'])
            self.assertEqual((allocation.receipt_id, allocation.amount), (self.funding_receipt.pk, payload['amount']))
            self.assertGreater(allocation.created_at, refund_at)
            receipts.append((payload, record))
        funding = self.ok(self.finance_client.get(funding_url))
        self.assertEqual((funding['allocated_units'], funding['remaining_units']), (required, 0))
        print(f'{self.store.slug} deferred_funding_order={order.pk} '
              f'allocations={[record["allocation_id"] for _, record in receipts]} '
              f'allocated={required} requests={[first["request_id"], last["request_id"]]}', flush=True)
        return receipts

    def execute_consultation_addon_refund(self, staff, case, payment, proposal, events_before):
        proposal_id = proposal['id']
        path = f'/orders/{self.order_id}/addons/payments/{payment.pk}/refund-proposals/{proposal_id}/'
        execute_path = '/api/admin' + path + 'execute/'
        decision_path = '/api/orders' + path
        buyer = self.api(self.buyer)
        replacement = self.api(self.replacement)
        self.assertEqual(staff.post(execute_path, {}, format='json').status_code, 400)
        self.assertEqual(self.api(self.original).post(decision_path, {'accepted': True}, format='json').status_code, 404)
        self.assertEqual(self.ok(buyer.post(decision_path, {'accepted': True}, format='json'))['status'], 'PENDING')
        self.assertEqual(staff.post(execute_path, {}, format='json').status_code, 400)
        accepted = self.ok(replacement.post(decision_path, {'accepted': True}, format='json'))
        self.assertEqual(accepted['status'], 'ACCEPTED')
        decisions = list(payment.refund_proposals.get(pk=proposal_id).decisions.order_by('account_id').values_list(
            'account_id', 'role', 'accepted', 'decided_at'))
        self.assertEqual({(account, role, approved) for account, role, approved, _ in decisions},
                         {(self.buyer.pk, 'BOSS', True), (self.replacement.pk, 'PROVIDER', True)})

        source = payment.allocations.select_related('lot').get()
        self.assertEqual((source.kind, source.amount, source.refunded_amount), ('PREPAID', 1000, 0))
        lot_before = source.lot.remaining
        refund_transactions = Transaction.objects.filter(wallet__account=self.buyer, tx_type=Transaction.TxType.REFUND)
        self.assertFalse(refund_transactions.exists())

        opening = case.history[0]
        conflict = {**opening, 'affected_segment_ids': None}
        AfterSale.objects.filter(pk=case.pk).update(history=[opening, conflict])
        try:
            blocked = staff.post(execute_path, {}, format='json')
            self.assertEqual((blocked.status_code, blocked.data['msg']), (400, '加购售后原履约段记录待核对'))
            self.assertFalse(payment.refund_records.exists())
            self.assertFalse(refund_transactions.exists())
        finally:
            AfterSale.objects.filter(pk=case.pk).update(history=case.history)

        def fail_funding(order, refund):
            self.assertEqual(refund.buyer_transaction.amount, 500)
            self.assertEqual(payment.allocations.get().refunded_amount, 500)
            run = record_addon_funding_release(order, refund)
            self.assertEqual((run.required_amount, run.released_amount), (1100, 0))
            raise ValidationError('隔离测试补资核验失败')

        with patch('orders.addon_funding_release.record_addon_funding_release', side_effect=fail_funding) as failed:
            blocked = staff.post(execute_path, {}, format='json')
        self.assertEqual(blocked.status_code, 400, blocked.data)
        failed.assert_called_once()
        self.assertFalse(payment.refund_records.exists())
        self.assertFalse(refund_transactions.exists())
        self.assertFalse(AdminAuditLog.objects.filter(path=execute_path, resource='addon-refund').exists())
        source.refresh_from_db()
        self.assertEqual((source.refunded_amount, source.lot.remaining), (0, lot_before))
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, self.balance_before - 1000)

        message_insert = f'insert into `{Message._meta.db_table}`'
        failed_deliveries = []

        def fail_first_delivery(intent_id):
            def fail_after_insert(execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if sql.lstrip().lower().startswith(message_insert):
                    failed_deliveries.append(intent_id)
                    raise RuntimeError('隔离测试消息写入后投递失败')
                return result

            with connections[self.store.database_alias].execute_wrapper(fail_after_insert):
                deliver_addon_refund_notice(intent_id)

        table = Order._meta.db_table
        with (patch('site_messages.addon_refund_notice.deliver', side_effect=fail_first_delivery) as first_send,
              self.assertLogs('django.db.backends.base', level='ERROR'), ThreadPoolExecutor(max_workers=2) as pool):
            with transaction.atomic(using=self.store.database_alias):
                Order.objects.select_for_update().get(pk=self.order_id)
                requests = []
                for client in (staff, self.api(case.operator)):
                    attempts = Queue()
                    pending = pool.submit(self.independent_request, lambda client=client: client.post(
                        execute_path, {}, format='json'), self.observe_lock_attempt(attempts, table, 'for update'))
                    requests.append((pending, attempts))
                waits = [self.wait_for_lock(pending, attempts, table=table) for pending, attempts in requests]
            posted = [self.ok(pending.result(timeout=15)) for pending, _ in requests]
        self.assertEqual(first_send.call_count, 2)
        self.assertCountEqual(failed_deliveries, [call.args[0] for call in first_send.call_args_list])
        self.assertEqual(posted[0], posted[1])
        self.assertEqual((posted[0]['id'], posted[0]['refund_executed'], posted[0]['provider_income_raw']),
                         (proposal_id, True, 200))
        resolve_path = f'/api/admin/aftersales/{case.pk}/resolve/'
        resolution = {'resolution': '已按双方确认的加购方案退款'}
        self.assertEqual(staff.post(resolve_path, resolution, format='json').status_code, 403)
        resolved = self.ok(self.api(self.staff).post(resolve_path, resolution, format='json'))
        self.assertEqual(resolved['status'], 'RESOLVED')
        self.assertEqual(self.ok(staff.post(execute_path, {}, format='json')), posted[0])

        refund = payment.refund_records.select_related('buyer_transaction', 'funding_release_run').get()
        self.assertEqual((refund.proposal_id, refund.amount, refund.addon_pending_deduction,
                          refund.main_pending_deduction, refund.wallet_deduction, refund.deposit_deduction,
                          refund.rounding_surplus), (proposal_id, 500, 500, 0, 0, 0, 0))
        self.assertEqual(list(refund.earning_deductions.values_list('payment_id', 'segment_id', 'amount')),
                         [(payment.pk, payment.intent.segment_id, 500)])
        self.assertEqual(list(refund_transactions.values_list('pk', 'amount', 'balance_before', 'balance_after')),
                         [(refund.buyer_transaction_id, 500, self.balance_before - 1000, self.balance_before - 500)])
        source.refresh_from_db()
        self.assertEqual((source.refunded_amount, source.lot.remaining), (500, lot_before + 500))
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, self.balance_before - 500)
        run = refund.funding_release_run
        self.assertEqual((run.required_amount, run.allocated_before, run.released_amount), (1100, 1100, 0))
        self.assertFalse(run.releases.exists())
        self.assertEqual(list(PassReceiptUsageEvent.objects.filter(state__receipt=self.funding_receipt)
                              .order_by('version').values_list('kind', 'amount')), events_before)
        order = Order.objects.get(pk=self.order_id)
        self.assertFalse(order.refund_records.exists())
        self.assertEqual(list(order.transactions.values_list('pk', 'tx_type', 'amount')), self.funding_before)
        self.assertEqual(list(PointLedger.objects.filter(order=order).values_list('amount', flat=True)), [-10000])
        self.assertEqual(payment.buyer_transaction.amount, -1000)
        audit = AdminAuditLog.objects.get(path=execute_path, resource='addon-refund', status_code=200)
        self.assertEqual((audit.object_id, audit.operator_account_id), (str(refund.pk), case.operator_id))
        return refund, waits

    def recover_consultation_addon_refund_notices(self, refund):
        notices = refund.notice_intents.order_by('account_id')
        pending = list(notices.values_list('pk', 'account_id', 'role', 'amount', 'completed_at', 'message_id'))
        self.assertEqual(len(pending), 2)
        self.assertCountEqual([entry[1:] for entry in pending], [
            (self.buyer.pk, 'BOSS', 500, None, None), (self.replacement.pk, 'PROVIDER', 500, None, None),
        ])
        messages = Message.objects.filter(related_order_id=self.order_id, title='加购退款已处理')
        self.assertFalse(messages.exists())
        recovered, waited = self.while_funding_reads_order(self.recover_refund_notices, funding_units=1100)
        self.assertEqual(recovered, 2)
        deliveries = list(notices.values_list('account_id', 'message_id', 'completed_at', 'skipped_reason'))
        self.assertTrue(all(message_id and completed and not skipped for _, message_id, completed, skipped in deliveries))
        message_ids = {account: message_id for account, message_id, _, _ in deliveries}
        self.assertEqual(dict(messages.values_list('recipient_account_id', 'pk')), message_ids)
        self.assertEqual(messages.count(), 2)
        for account in (self.buyer, self.replacement):
            own = self.ok(self.api(account).get(f'/api/messages/{message_ids[account.pk]}/'))
            self.assertEqual((own['id'], own['related_order_id'], own['cycle_relation']),
                             (message_ids[account.pk], self.order_id, 'CURRENT'))
        denied = self.api(self.original).get(f'/api/messages/{message_ids[self.replacement.pk]}/')
        self.assertEqual(denied.data['code'], 404)
        self.assertEqual(self.recover_refund_notices(), 0)
        for intent_id, *_ in pending:
            deliver_addon_refund_notice(intent_id)
        self.assertEqual(list(notices.values_list('account_id', 'message_id', 'completed_at', 'skipped_reason')), deliveries)
        self.assertEqual(messages.count(), 2)
        return waited, list(message_ids.values())

    def recover_refund_notices(self):
        return recover_addon_refund_notices.apply(headers={'store_slug': self.store.slug}, throw=True).result

    def refund_retirement_operator(self, permissions):
        user = make_console_user(permissions)
        operator = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = operator
        user.admin_membership.save(update_fields=['account'])
        return operator, self.api(operator)

    def clear_refund_retirement_wallet(self, account, operator, retirement):
        account.phone = '13800001234'
        account.is_phone_verified = True
        account.set_password('isolated-retirement-password')
        account.save(update_fields=['phone', 'is_phone_verified', 'password'])
        wallet = Wallet.objects.get(account=account)
        # Clear the remaining isolated test credit through its regular ledger entry.
        self.ok(retirement.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {'amount': -wallet.balance,
            'remark': '冲回隔离测试本金及赠额', 'client_request_id': 'consultation-notice-retire-adjust'}, format='json'))
        adjustment = wallet.transactions.get(tx_type=Transaction.TxType.ADJUST, operator_account=operator)
        self.assertEqual((adjustment.amount, adjustment.balance_before, adjustment.balance_after),
                         (-wallet.balance, wallet.balance, 0))
        return adjustment

    def prepare_refund_buyer_retirement(self):
        buyer = self.buyer
        cycle = buyer.business_cycle
        operator, retirement = self.refund_retirement_operator(['user:retire', 'wallet:adjust'])
        adjustment = self.clear_refund_retirement_wallet(buyer, operator, retirement)
        path = f'/api/admin/users/{buyer.pk}/'
        preview = self.ok(retirement.get(path + 'retire-preview/'))
        self.assertTrue(preview['can_retire'], preview['facts']['blocking_reasons'])
        self.assertEqual((preview['facts']['wallet_balance'], preview['facts']['wallet_frozen']), (0, 0))

        def submit():
            retired = self.ok(retirement.post(path + 'retire/', {'confirm': True, 'reason': '隔离退店周期核对',
                'confirmation_token': preview['confirmation_token']}, format='json'))
            self.assertEqual(retired['business_cycle'], cycle + 1)
            return RetirementAudit.objects.get(account=buyer, business_cycle=cycle, operator=operator)

        return submit, adjustment

    def prepare_refund_provider_retirement(self, settled_at):
        provider = self.replacement
        cycle = provider.business_cycle
        operator, retirement = self.refund_retirement_operator(['escort:retire', 'escort:deposit_refund', 'wallet:adjust'])
        profile = EscortProfile.objects.get(account=provider)
        wallet = Wallet.objects.get(account=provider)
        self.assertEqual((wallet.balance, wallet.frozen_amount, profile.deposit_paid), (1760, 0, 2640))

        deposit_path = f'/api/admin/escorts/{profile.pk}/deposit-refund/'
        with patch('django.utils.timezone.now', return_value=settled_at + timedelta(minutes=1)):
            waiting = self.ok(retirement.get(deposit_path))
        self.assertEqual((waiting['can_refund'], waiting['wait_status']), (False, 'WAITING'), waiting)
        eligible = self.ok(retirement.get(deposit_path))
        self.assertTrue(eligible['can_refund'], eligible)
        self.ok(retirement.post(deposit_path, {'amount': profile.deposit_paid, 'reason': '隔离注销前退还本人押金',
            'client_request_id': 'consultation-notice-provider-deposit'}, format='json'))
        returned = Transaction.objects.get(wallet=wallet, operator_account=operator, tx_type=Transaction.TxType.DEPOSIT)
        paired = Transaction.objects.get(deposit_pair=returned, tx_type=Transaction.TxType.DEPOSIT_INCOME)
        self.assertEqual((returned.amount, returned.balance_after, paired.amount, returned.deposit_business_cycle,
                          paired.deposit_business_cycle), (2640, 4400, -2640, cycle, cycle))
        print(f'{self.store.slug} 退押金流水 {returned.pk}/{paired.pk} amount={returned.amount}', flush=True)

        adjustment = self.clear_refund_retirement_wallet(provider, operator, retirement)
        path = f'/api/admin/providers/{provider.pk}/retirement/'
        preview = self.ok(retirement.get(path))
        self.assertTrue(preview['can_retire'], preview['facts']['blocking_reasons'])
        facts = preview['facts']
        self.assertEqual((facts['wallet_balance'], facts['wallet_frozen'], facts['deposit_paid']), (0, 0, 0))

        def submit():
            payload = {'confirm': True, 'reason': '隔离陪玩注销周期核对', 'confirmation_token': preview['confirmation_token'],
                       'client_request_id': 'consultation-notice-provider-retire'}
            retired = self.ok(retirement.post(path, payload, format='json'))
            self.assertEqual((retired['retired_cycle'], retired['business_cycle'], retired['replayed']),
                             (cycle, cycle + 1, False))
            replay = self.ok(retirement.post(path, payload, format='json'))
            self.assertEqual(replay, {**retired, 'replayed': True})
            return RetirementAudit.objects.get(account=provider, business_cycle=cycle, operator=operator)

        return submit, adjustment

    def login_refund_account_after_retirement(self, account, host, audience):
        rejoined = APIClient(HTTP_HOST=host)
        credentials = {'audience': audience, 'username': account.username, 'password': 'isolated-retirement-password'}
        delivered_codes = []
        login = '/api/users/password-login/'
        with patch('users.password_login.send_verification_code', side_effect=lambda _, code: delivered_codes.append(code)):
            challenge = self.ok(rejoined.post(login, credentials, format='json'))
            self.assertTrue(challenge['requiresSms'])
            signed = self.ok(rejoined.post(login, {**credentials, 'smsChallenge': challenge['smsChallenge'],
                'smsCode': delivered_codes[0]}, format='json'))
        rejoined.credentials(HTTP_AUTHORIZATION='Bearer ' + signed['token'])
        return rejoined, signed['token']

    def retire_and_rejoin_socket_account(self, store, host, account, submit, adjustment_id, check_at, audience, old_token):
        try:
            with store_context(store), patch('django.utils.timezone.now', return_value=check_at):
                cycle = account.business_cycle
                audit = submit()
                account.refresh_from_db()
                self.assertEqual((account.business_cycle, account.license_membership_active), (cycle + 1, False))
                _, token = self.login_refund_account_after_retirement(account, host, audience)
                account.refresh_from_db()
                current_cycle = cycle + (2 if audience == 'provider' else 1)
                self.assertEqual((account.business_cycle, account.license_membership_active), (current_cycle, True))
                original = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + old_token)
                self.assertEqual(original.get('/api/messages/').status_code, 401)
                print(f'{store.slug} adjustment={adjustment_id} {audience}_retirement={audit.pk} '
                      f'cycle={cycle}->{cycle + 1}->{current_cycle}', flush=True)
                return host, account.pk, current_cycle, token
        finally:
            connections.close_all()

    def recover_notices_during_buyer_retirement(self, refund):
        submit, adjustment = self.prepare_refund_buyer_retirement()
        buyer = self.api(self.buyer)
        cycle = self.buyer.business_cycle
        pending = refund.notice_intents.filter(completed_at__isnull=True)
        self.assertEqual(pending.count(), 2)

        table = ClubAccount._meta.db_table
        attempts = Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic(using=self.store.database_alias):
                audit = submit()
                recovery = pool.submit(self.independent_request, self.recover_refund_notices,
                    self.observe_lock_attempt(attempts, table, 'for update', account_id=self.buyer.pk))
                waited = self.wait_for_lock(recovery, attempts, table=table)
            self.assertEqual(recovery.result(timeout=15), 2)

        self.buyer.refresh_from_db()
        self.assertEqual((self.buyer.business_cycle, self.buyer.license_membership_active), (cycle + 1, False))
        self.assertEqual(buyer.get('/api/messages/').status_code, 401)
        skipped = refund.notice_intents.get(account=self.buyer)
        self.assertEqual(skipped.business_cycle, cycle)
        self.assertIsNotNone(skipped.completed_at)
        self.assertIsNone(skipped.message_id)
        self.assertIn('周期已变化', skipped.skipped_reason)
        provider = refund.notice_intents.get(account=self.replacement)
        message = self.ok(self.api(self.replacement).get(f'/api/messages/{provider.message_id}/'))
        self.assertEqual((message['related_order_id'], message['cycle_relation']), (self.order_id, 'CURRENT'))
        rejoined, _ = self.login_refund_account_after_retirement(self.buyer, self.store_host, 'boss')
        fresh = self.ok(rejoined.get('/api/messages/', {'type': 'ORDER'}))
        self.assertFalse(any(message['related_order_id'] == self.order_id and message['title'] == skipped.title
                             for message in fresh['messages']))
        self.buyer.refresh_from_db()
        self.assertEqual((self.buyer.business_cycle, self.buyer.license_membership_active), (cycle + 1, True))
        self.assertEqual(buyer.get('/api/messages/').status_code, 401)
        self.assertEqual(self.recover_refund_notices(), 0)
        deliver_addon_refund_notice(skipped.pk)
        self.assertEqual(list(Message.objects.filter(related_order_id=self.order_id, title=skipped.title)
                              .values_list('pk', flat=True)), [provider.message_id])
        self.assertEqual(list(refund.payment.refund_records.values_list('pk', 'amount')), [(refund.pk, 500)])
        self.assertEqual(Wallet.objects.get(pk=adjustment.wallet_id).balance, 0)
        print(f'{self.store.slug} refund={refund.pk} adjustment={adjustment.pk} buyer_retirement={audit.pk} cycle={cycle}->{cycle + 1} '
              f'skipped_notice={skipped.pk} retirement_wait={waited}', flush=True)
        return waited, [provider.message_id]

    @override_settings(SEGMENT_SETTLEMENT_ENABLED=True, ADDON_REFUND_NOTICE_RECOVERY_ENABLED=True)
    def exercise_provider_retirement_notice_race(self, *, retirement_first, publisher=None):
        refund = self.exercise_paid_addon_consultation(leave_notices_pending=True, settle_before_recovery=True)
        settled_at = refund.payment.segment_settlements.get().settled_at
        cases = [(self.store, refund.pk)]
        provider = self.replacement
        cycle = provider.business_cycle
        old = self.api(provider)
        buyer_notice = refund.notice_intents.get(account=self.buyer)
        provider_notice = refund.notice_intents.get(account=provider)
        self.assertEqual(refund.notice_intents.filter(completed_at__isnull=True).count(), 2)
        self.assertEqual(provider_notice.business_cycle, cycle)

        with patch('django.utils.timezone.now', return_value=settled_at + timedelta(days=8)):
            submit, adjustment = self.prepare_refund_provider_retirement(settled_at)
            before = self.refund_notice_worker_facts(cases)[self.store.slug]
            wallet_ledger = adjustment.wallet.transactions.order_by('pk').values_list(
                'pk', 'tx_type', 'amount', 'balance_before', 'balance_after', 'deposit_business_cycle')
            ledger_before = list(wallet_ledger)
            # Leave only the provider's intent pending so the observed order lock is for that delivery.
            deliver_addon_refund_notice(buyer_notice.pk)
            buyer_notice.refresh_from_db()
            self.assertIsNotNone(buyer_notice.message_id)

            if publisher is None:
                audit, waited = self.race_provider_retirement_locally(submit, provider_notice, retirement_first)
            else:
                audit, waited = self.race_provider_retirement_with_worker(submit, provider_notice, retirement_first,
                                                                       publisher)

            provider.refresh_from_db()
            self.assertEqual((provider.business_cycle, provider.license_membership_active), (cycle + 1, False))
            self.assertEqual(old.get('/api/messages/').status_code, 401)
            self.assertEqual(RetirementAudit.objects.filter(account=provider, business_cycle=cycle).count(), 1)
            rejoined, _ = self.login_refund_account_after_retirement(provider, self.store_host, 'provider')
            provider.refresh_from_db()
            self.assertEqual((provider.business_cycle, provider.license_membership_active), (cycle + 2, True))
            self.assertEqual(old.get('/api/messages/').status_code, 401)

            provider_notice.refresh_from_db()
            self.assertIsNotNone(provider_notice.completed_at)
            self.assertEqual(provider_notice.business_cycle, cycle)
            fresh = self.ok(rejoined.get('/api/messages/', {'type': 'ORDER'}))
            own_refunds = [row for row in fresh['messages'] if row['related_order_id'] == refund.order_id
                           and row['title'] == provider_notice.title]
            expected_ids = [buyer_notice.message_id]
            if retirement_first:
                self.assertIsNone(provider_notice.message_id)
                self.assertIn('周期已变化', provider_notice.skipped_reason)
                self.assertEqual(own_refunds, [])
            else:
                self.assertFalse(provider_notice.skipped_reason)
                message_id = provider_notice.message_id
                self.assertIsNotNone(message_id)
                expected_ids.append(message_id)
                self.assertEqual([(row['id'], row['cycle_relation']) for row in own_refunds],
                                 [(message_id, 'PREVIOUS')])
                own = self.ok(rejoined.get(f'/api/messages/{message_id}/'))
                self.assertEqual((own['related_order_id'], own['cycle_relation']), (refund.order_id, 'PREVIOUS'))
                denied = self.api(self.original).get(f'/api/messages/{message_id}/')
                self.assertEqual(denied.data['code'], 404)
            buyer = self.ok(self.api(self.buyer).get(f'/api/messages/{buyer_notice.message_id}/'))
            self.assertEqual((buyer['related_order_id'], buyer['cycle_relation']), (refund.order_id, 'CURRENT'))
            after = self.refund_notice_worker_facts(cases)[self.store.slug]
            self.assertEqual(after['refund'], before['refund'])
            self.assertCountEqual([row[:4] for row in after['intents']], [row[:4] for row in before['intents']])
            self.assertCountEqual([message for message, _, _ in after['messages']], expected_ids)
            if publisher is None:
                repeated = self.recover_refund_notices()
            else:
                repeated = self.publish_refund_recovery(publisher).get(timeout=20)
            self.assertEqual(repeated, 0)
            deliver_addon_refund_notice(provider_notice.pk)
            self.assertEqual(self.refund_notice_worker_facts(cases)[self.store.slug], after)
            self.assertEqual(list(wallet_ledger.all()), ledger_before)
            self.assertEqual(Wallet.objects.get(pk=adjustment.wallet_id).balance, 0)
            self.assertEqual(EscortProfile.objects.get(account=provider).deposit_paid, 0)

        print(f'{self.store.slug} order={refund.order_id} refund={refund.pk} adjustment={adjustment.pk} '
              f'provider_retirement={audit.pk} cycle={cycle}->{cycle + 1}->{cycle + 2} retirement_first={retirement_first} '
              f'order_wait={waited} provider_intent={provider_notice.pk} messages={expected_ids}', flush=True)

    @staticmethod
    def pause_refund_race(table, sql_fragment, row_id, held, resume):
        def pause_first(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            normalized = sql.lower()
            if (not held.is_set() and f'`{table}`' in normalized and sql_fragment in normalized
                    and row_id in (params or ())):
                held.set()
                if not resume.wait(8):
                    raise AssertionError('首个退款通知或注销事务未在时限内放行')
            return result
        return pause_first

    def race_provider_retirement_locally(self, submit, notice, retirement_first):
        table = Order._meta.db_table
        order_id = notice.refund.order_id
        attempts = Queue()
        held, resume = Event(), Event()
        if retirement_first:
            first, second = submit, self.recover_refund_notices
            pause = self.pause_refund_race(table, 'for update', order_id, held, resume)
        else:
            first, second = self.recover_refund_notices, submit
            notice_table = notice._meta.db_table
            pause = self.pause_refund_race(notice_table, f'update `{notice_table}` set ', notice.pk, held, resume)

        with ThreadPoolExecutor(max_workers=2) as pool:
            holding = pool.submit(self.independent_request, first, pause)
            try:
                self.assertTrue(held.wait(5), '未观察到首个事务持有订单锁')
                waiting = pool.submit(self.independent_request, second,
                    self.observe_lock_attempt(attempts, table, 'for update', account_id=order_id))
                waited = self.wait_for_lock(waiting, attempts, table=table)
            finally:
                resume.set()
            results = (holding.result(timeout=15), waiting.result(timeout=15))
        audit, recovered = results if retirement_first else results[::-1]
        self.assertEqual(recovered, 1)
        return audit, waited

    def publish_refund_recovery(self, publisher):
        return publisher.send_task(recover_addon_refund_notices.name, headers={'store_slug': self.store.slug})

    def wait_for_refund_worker_lock(self, task, table, row_id):
        observer = connections[self.store.database_alias].copy()
        try:
            deadline = monotonic() + 3
            while monotonic() < deadline and not task.ready():
                sessions = mysql_locking_sessions(observer, table, row_id=row_id)
                if sessions:
                    self.assertEqual(len(sessions), 1)
                    return sessions[0]
                sleep(.025)
            self.fail(f'退款恢复任务 {task.id} 未等待原行锁 {table}[{row_id}]')
        finally:
            observer.close()

    def race_provider_retirement_with_worker(self, submit, notice, retirement_first, publisher):
        table = Order._meta.db_table
        order_id = notice.refund.order_id
        with ThreadPoolExecutor(max_workers=1) as pool:
            if retirement_first:
                held, resume = Event(), Event()
                retiring = pool.submit(self.independent_request, submit,
                    self.pause_refund_race(table, 'for update', order_id, held, resume))
                try:
                    self.assertTrue(held.wait(5), '未观察到注销事务持有订单锁')
                    task = self.publish_refund_recovery(publisher)
                    waited = worker_session = self.wait_for_refund_worker_lock(task, table, order_id)
                finally:
                    resume.set()
            else:
                attempts = Queue()
                with transaction.atomic(using=self.store.database_alias):
                    # Hold only the intent row while the worker locks the order.
                    type(notice).objects.select_for_update().values_list('pk', flat=True).get(pk=notice.pk)
                    task = self.publish_refund_recovery(publisher)
                    worker_session = self.wait_for_refund_worker_lock(task, notice._meta.db_table, notice.pk)
                    retiring = pool.submit(self.independent_request, submit,
                        self.observe_lock_attempt(attempts, table, 'for update', account_id=order_id))
                    waited = self.wait_for_lock(retiring, attempts, table=table)
                    self.assertNotEqual(waited, worker_session)
            audit = retiring.result(timeout=15)
        self.assertEqual(task.get(timeout=20), 1)
        print(f'{self.store.slug} worker_task={task.id} worker_session={worker_session} '
              f'worker_wait={table if retirement_first else notice._meta.db_table}', flush=True)
        return audit, waited

    def settle_consultation_addon_refund(self, staff, transfer, refund):
        first_report = self.approve_report(self.original, transfer.previous_segment_id)
        with patch('django.utils.timezone.now', return_value=first_report.submitted_at + timedelta(minutes=15)):
            second_report = self.approve_report(self.replacement, transfer.next_segment_id)
        order = Order.objects.get(pk=self.order_id)
        freeze = timedelta(hours=order.service_freeze_hours)
        income = order.transactions.filter(tx_type=Transaction.TxType.INCOME).order_by('pk')
        with patch('django.utils.timezone.now', return_value=first_report.submitted_at + freeze):
            self.assertEqual(confirm_due_consumption.run(), 0)
        self.assertEqual(list(income.values_list('wallet__account_id', 'amount')), [(self.original.pk, 3200)])
        self.assertFalse(refund.payment.segment_settlements.exists())
        with patch('django.utils.timezone.now', return_value=second_report.submitted_at + freeze):
            self.assertEqual(confirm_due_consumption.run(), 1)
            self.assertEqual(confirm_due_consumption.run(), 0)
        order.refresh_from_db()
        self.assertEqual((order.provider_income, order.settlement_store_funding, order.settlement_addon_contribution),
                         (7400, 1100, 300))
        paid_addon = refund.payment.segment_settlements.select_related('income_transaction').get()
        self.assertEqual((paid_addon.segment_id, paid_addon.amount, paid_addon.income_transaction.wallet.account_id),
                         (transfer.next_segment_id, 200, self.replacement.pk))
        ledger = list(income.values_list('wallet__account_id', 'amount'))
        self.assertCountEqual(ledger, [(self.original.pk, 3200), (self.replacement.pk, 4200), (self.replacement.pk, 200)])
        self.assertEqual(sum(amount for _, amount in ledger), 6000 + 1000 - 500 + 1100)
        self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 6000)
        consumption = refund.payment.consumption
        self.assertEqual((consumption.paid_units, consumption.refunded_units), (1000, 500))
        path = f'/api/admin/orders/{order.pk}/addons/payments/{refund.payment_id}/refund-proposals/{refund.proposal_id}/execute/'
        replayed = self.ok(staff.post(path, {}, format='json'))
        self.assertEqual((replayed['id'], replayed['refund_executed'], replayed['provider_income_raw']),
                         (refund.proposal_id, True, 200))
        self.assertEqual(list(income.values_list('wallet__account_id', 'amount')), ledger)
        self.assertEqual(list(refund.payment.refund_records.values_list('pk', flat=True)), [refund.pk])
        self.assertEqual(AdminAuditLog.objects.filter(path=path, resource='addon-refund', status_code=200).count(), 1)
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, self.balance_before - 500)
        return paid_addon

    @override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True)
    def exercise_consultation_aftersale(self):
        staff, _, message = self.prepare_consultation()
        buyer = self.api(self.buyer)
        consultation_url = f'/api/admin/chat-sessions/{message.session_id}/'
        path = '/api/admin/aftersales/open-negotiation/'
        payload = self.consultation_payload(message)
        unclaimed = staff.post(path, payload, format='json')
        self.assertEqual(unclaimed.status_code, 400, unclaimed.data)
        missing = staff.post(path, {**payload, 'chat_message_id': 9223372036854775807}, format='json')
        self.assertEqual((missing.status_code, missing.data), (400, unclaimed.data))
        message_rows = ChatMessage.objects.filter(pk=message.pk)
        message_rows.update(notification_recipient_cycle=self.buyer.business_cycle + 1)
        try:
            hidden = staff.post(path, payload, format='json')
            self.assertEqual((hidden.status_code, hidden.data), (400, unclaimed.data))
            self.ok(staff.post(consultation_url + 'claim/', {'expected_version': 0}, format='json'))
            authorized = staff.post(path, payload, format='json')
            self.assertEqual((authorized.status_code, authorized.data['msg']), (400, '咨询原账号或业务周期待核对'))
        finally:
            message_rows.update(notification_recipient_cycle=message.notification_recipient_cycle)
        self.assertFalse(AfterSale.objects.filter(order_id=self.order_id).exists())
        response, waited = self.while_funding_reads_order(lambda: staff.post(path, payload, format='json'))
        case = self.ok(response)
        source = {'consultation_message_id': message.pk, 'consultation_session_id': message.session_id,
                  'consultation_account_id': self.buyer.pk, 'consultation_business_cycle': self.buyer.business_cycle}
        self.assertEqual({key: case['history'][-1].get(key) for key in source}, source)
        replay, payroll_wait = self.consultation_during_payroll(
            self.prepare_consultation_payroll(), staff, path, payload, message)
        replay = self.ok(replay)
        self.assertEqual(replay['id'], case['id'])
        self.assertEqual({key: replay['history'][-1].get(key) for key in source}, source)
        changed = self.ok(self.send_order_card(buyer, 'another-consultation-card'))
        conflict = staff.post(path, {**payload, 'chat_message_id': changed['id']}, format='json')
        self.assertEqual((conflict.status_code, conflict.data['errorCode']), (409, 'AFTERSALE_REQUEST_CONFLICT'))

        self.ok(staff.post(consultation_url + 'release/', {'expected_version': 1}, format='json'))
        self.assertEqual(staff.post(path, payload, format='json').status_code, 400)
        detail = self.ok(staff.get(f'/api/admin/aftersales/{case["id"]}/'))
        self.assertFalse(set(source).intersection(detail['history'][-1]))
        row = AfterSale.objects.get(order_id=self.order_id)
        self.assertEqual(len(row.history), 1)
        self.assertEqual({key: row.history[-1].get(key) for key in source}, source)
        self.assert_occupancy(self.replacement, self.original, 1)
        print(f'{self.store.slug} order={self.order_id} chat_message={message.pk} aftersale={row.pk} '
              f'consultation_waiting={waited} payroll_waiting={payroll_wait}', flush=True)

    @override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True,
                       AFTERSALE_OPEN_NOTICE_RECOVERY_ENABLED=True)
    def exercise_consultation_permission_change(self):
        staff, membership, message = self.prepare_consultation()
        consultation_url = f'/api/admin/chat-sessions/{message.session_id}/'
        self.ok(staff.post(consultation_url + 'claim/', {'expected_version': 0}, format='json'))
        path = '/api/admin/aftersales/open-negotiation/'
        payload = self.consultation_payload(message)
        manager = make_console_user(['role:edit', 'admin:edit'])
        manager_account = get_or_create_account_for_legacy_user(manager)
        manager.admin_membership.account = manager_account
        manager.admin_membership.save(update_fields=['account'])
        manager_client = self.api(manager_account)
        role = membership.roles.get()
        read_permissions = ['order:view', 'chat:view', 'chat:reply']
        reader_role = make_console_user(read_permissions).admin_membership.roles.get()
        role_url = f'/api/admin/roles/{role.pk}/'
        membership_url = f'/api/admin/admins/{membership.pk}/'
        payroll = self.prepare_consultation_payroll()
        changes = (
            ('role_edit', role_url, {'permissions': read_permissions}, {'permissions': role.permissions}, 200),
            ('role_assignment', membership_url, {'roles': [reader_role.pk]}, {'roles': [role.pk]}, 200),
            ('membership_suspension', membership_url, {'is_active': False}, {'is_active': True}, 403),
        )

        def apply_change(url, change, readable):
            self.ok(manager_client.patch(url, change, format='json'))
            access = staff.get(consultation_url + 'messages/')
            self.assertEqual(access.status_code, readable, access.data)

        for label, url, revoke, restore, readable in changes:
            response, waited = self.consultation_during_payroll(payroll, staff, path, payload, message,
                while_waiting=lambda: apply_change(url, revoke, readable))
            self.assertEqual(response.status_code, 403, response.data)
            self.assertFalse(AfterSale.objects.filter(order_id=self.order_id).exists())
            self.assertFalse(AfterSaleOpenNotice.objects.filter(case__order_id=self.order_id).exists())
            self.ok(manager_client.patch(url, restore, format='json'))
            print(f'{self.store.slug} order={self.order_id} message={message.pk} membership={membership.pk} '
                  f'change={label} waiting_session={waited} aftersale_status=403', flush=True)

        opened = self.ok(staff.post(path, payload, format='json'))
        notices = AfterSaleOpenNotice.objects.filter(case_id=opened['id'])
        initial_notices = dict(notices.values_list('account_id', 'pk'))
        self.assertCountEqual(initial_notices, [self.buyer.pk, self.replacement.pk])
        response, waited = self.consultation_during_payroll(payroll, staff, path, payload, message,
            while_waiting=lambda: apply_change(role_url, {'permissions': read_permissions}, 200))
        self.assertEqual(response.status_code, 403, response.data)
        self.assertNotIn('data', response.data)
        self.ok(manager_client.patch(role_url, {'permissions': role.permissions}, format='json'))
        replay = self.ok(staff.post(path, payload, format='json'))
        self.assertEqual(replay['id'], opened['id'])
        case = AfterSale.objects.get(pk=opened['id'])
        self.assertEqual(len(case.history), 1)
        self.assertEqual(case.history[0]['consultation_message_id'], message.pk)
        self.assertEqual(dict(notices.values_list('account_id', 'pk')), initial_notices)
        self.assertEqual(ChatSession.objects.get(pk=message.session_id).claimed_by_id, membership.account_id)
        self.assert_occupancy(self.replacement, self.original, 1)
        print(f'{self.store.slug} order={self.order_id} aftersale={case.pk} replay_waiting={waited} '
              'revoked_replay_status=403 restored_replay_status=200', flush=True)

    def prepare_consultation_payroll(self):
        payroll = payroll_fixture.LateActivityPayrollTest()
        payroll.setUp()
        payroll.assign(payroll.original_text)
        preview = payroll.preview(payroll.original_text)
        self.assertTrue(preview['can_confirm'], preview['warnings'])
        return payroll, preview

    def consultation_during_payroll(self, payroll_preview, staff, path, payload, message, *, while_waiting=None):
        payroll, preview = payroll_preview
        payroll_url = '/api/admin/payroll/confirm/'
        confirm_payload = {'month': payroll.original_text, 'fingerprint': preview['fingerprint']}
        held, resume = Event(), Event()
        attempts = Queue()
        table = PayrollEventBarrier._meta.db_table
        with ThreadPoolExecutor(max_workers=2) as pool:
            confirming = pool.submit(self.independent_request,
                lambda: payroll.client.post(payroll_url, confirm_payload, format='json'), self.pause_payroll_barrier(held, resume))
            try:
                self.assertTrue(held.wait(3), '工资确认未取得独占屏障')
                replay = pool.submit(self.independent_request, lambda: staff.post(path, payload, format='json'),
                    self.observe_lock_attempt(attempts, table, 'lock in share mode'))
                waited = self.wait_for_lock(replay, attempts, table=table, include_shared=True)
                with transaction.atomic(using=self.store.database_alias):
                    Order.objects.select_for_update(nowait=True).get(pk=self.order_id)
                    ClubAccount.objects.select_for_update(nowait=True).get(pk=self.buyer.pk)
                    ChatSession.objects.select_for_update(nowait=True).get(pk=message.session_id)
                    ChatMessage.objects.select_for_update(nowait=True).get(pk=message.pk)
                if while_waiting is not None:
                    while_waiting()
            finally:
                resume.set()
            self.assertEqual(self.ok(confirming.result(timeout=10))['totals'], preview['totals'])
            return replay.result(timeout=10), waited

    @override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True,
                       SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True)
    def exercise_payroll_and_aftersale(self):
        PayrollEventBarrier.objects.get_or_create(pk=1)
        self.ok(self.transfer(self.api(self.staff)))
        revision = OrderPointsTransferFundingRevision.objects.get(order_id=self.order_id)
        replacement = self.api(self.replacement)
        self.join_replacement(replacement, revision.last_transfer)
        order_url = f'/api/orders/orders/{self.order_id}/'
        self.ok(replacement.post(order_url + 'complete/', {'completion_image': release_fixture.screenshot(),
            'client_request_id': 'points-after-transfer-complete'}, format='multipart'))
        buyer = self.api(self.buyer)
        buyer_url = order_url + 'aftersale/'
        payload = {'reason': '核对接替段服务', 'client_request_id': 'points-after-transfer-payroll',
                   'affected_provider_account_ids': [self.replacement.pk]}

        operator = make_console_user(['order:view', 'support:edit'])
        operator_account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = operator_account
        operator.admin_membership.save(update_fields=['account'])
        staff = self.api(operator_account)
        staff_payload = {**payload, 'order_id': self.order_id, 'client_request_id': 'points-after-transfer-staff'}
        staff_url = '/api/admin/aftersales/open-negotiation/'

        payroll = payroll_fixture.LateActivityPayrollTest()
        payroll.setUp()
        payroll.assign(payroll.original_text)
        preview = payroll.preview(payroll.original_text)
        self.assertTrue(preview['can_confirm'], preview['warnings'])
        payroll_url = '/api/admin/payroll/confirm/'
        payroll_payload = {'month': payroll.original_text, 'fingerprint': preview['fingerprint']}
        table = PayrollEventBarrier._meta.db_table
        case_id = None
        opens = ((buyer, buyer_url, payload, 200), (buyer, buyer_url, payload, 200),
                 (staff, staff_url, staff_payload, 409))
        for actor, url, body, expected in opens:
            creating = case_id is None
            held, resume = Event(), Event()
            creation_ready, continue_creation = Event(), Event()
            attempts = Queue()
            observe_payroll = self.observe_lock_attempt(attempts, table, 'lock in share mode')

            def observe_case(execute, sql, params, many, context):
                query = sql.lower()
                if (creating and not creation_ready.is_set() and f'`{transfer_fixture.LIFECYCLE_TABLE}`' in query
                        and 'lock in share mode' in query and self.replacement.pk in (params or ())):
                    # The first read of the case has finished. Start payroll now
                    # so it competes with the separate creation transaction.
                    creation_ready.set()
                    self.assertTrue(continue_creation.wait(8), '售后创建等待未在测试时限内结束')
                return observe_payroll(execute, sql, params, many, context)

            with ThreadPoolExecutor(max_workers=2) as pool:
                try:
                    if creating:
                        aftersale = pool.submit(self.independent_request,
                            lambda: actor.post(url, body, format='json'), observe_case)
                        self.assertTrue(creation_ready.wait(3), '售后未进入创建事务')
                    confirmation = pool.submit(self.independent_request,
                        lambda: payroll.client.post(payroll_url, payroll_payload, format='json'),
                        self.pause_payroll_barrier(held, resume))
                    self.assertTrue(held.wait(3), '工资确认未取得独占屏障')
                    if creating:
                        continue_creation.set()
                    else:
                        aftersale = pool.submit(self.independent_request,
                            lambda: actor.post(url, body, format='json'), observe_case)
                    waited = self.wait_for_lock(aftersale, attempts, table=table, include_shared=True)
                    with transaction.atomic(using=self.store.database_alias):
                        Order.objects.select_for_update(nowait=True).get(pk=self.order_id)
                        current = AfterSale.objects.select_for_update(nowait=True).filter(order_id=self.order_id).first()
                        self.assertEqual(current.pk if current else None, case_id)
                    self.assertFalse(aftersale.done())
                finally:
                    continue_creation.set()
                    resume.set()
                confirmed = self.ok(confirmation.result(timeout=10))
                response = aftersale.result(timeout=10)
            self.assertEqual(confirmed['totals'], preview['totals'])
            self.assertEqual(response.status_code, expected, response.data)
            if expected == 200:
                result = self.ok(response)
                if case_id is None:
                    case_id = result['id']
                self.assertEqual(result['id'], case_id)
            else:
                self.assertEqual(response.data['errorCode'], 'AFTERSALE_ALREADY_OPEN')
            print(f'{self.store.slug} order={self.order_id} payroll_run={confirmed["run_id"]} '
                  f'aftersale={case_id} creating={creating} waiting_session={waited} aftersale_status={expected}', flush=True)
        case = AfterSale.objects.get(order_id=self.order_id)
        changed = buyer.post(buyer_url, {**payload, 'reason': '用原编号改写本次申请'}, format='json')
        self.assertEqual((changed.status_code, changed.data['errorCode']), (409, 'AFTERSALE_REQUEST_CONFLICT'))
        self.assertEqual((case.pk, case.client_request_id, case.affected_provider_account_ids),
                         (case_id, payload['client_request_id'], [self.replacement.pk]))
        self.assertEqual(len(case.history), 1)
        order = Order.objects.get(pk=self.order_id)
        self.assertEqual((order.status, order.provider_account_id), (Order.Status.COMPLETED, self.replacement.pk))
        self.assertEqual(list(order.transactions.values_list('pk', 'tx_type', 'amount')), self.funding_before)
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, self.balance_before)

    @override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True)
    def exercise_retirement_and_points_transfer(self):
        PayrollEventBarrier.objects.get_or_create(pk=1)
        held, resume = Event(), Event()
        attempts = Queue()
        lifecycle_table = transfer_fixture.LIFECYCLE_TABLE

        def pause_retirement(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            query = sql.lower()
            if (not held.is_set() and f'`{lifecycle_table}`' in query and 'for update' in query
                    and self.original.pk in (params or ())):
                held.set()
                self.assertTrue(resume.wait(8), '未观测到转单等待注销')
            return result

        transfer_client = self.api(self.staff)
        with ThreadPoolExecutor(max_workers=2) as pool:
            retirement = pool.submit(self.independent_request,
                lambda: self.retirement_client.post(self.retirement_url, self.retirement_payload, format='json'),
                pause_retirement)
            try:
                self.assertTrue(held.wait(3), '注销未取得原陪玩的生命周期锁')
                transfer = pool.submit(self.independent_request,
                    lambda: self.transfer(transfer_client),
                    self.observe_lock_attempt(attempts, lifecycle_table, 'lock in share mode', account_id=self.original.pk))
                waited = self.wait_for_lock(transfer, attempts, table=lifecycle_table, include_shared=True)
                with transaction.atomic(using=self.store.database_alias):
                    PayrollEventBarrier.objects.select_for_update(nowait=True).get(pk=1)
                    Order.objects.select_for_update(nowait=True).get(pk=self.order_id)
                    OrderFinancialLock.objects.select_for_update(nowait=True).get(pk=1)
            finally:
                resume.set()
            retirement_response, transfer_response = retirement.result(timeout=10), transfer.result(timeout=10)

        self.assertEqual(retirement_response.status_code, 400, retirement_response.data)
        self.assertIn('注销内容已变化', retirement_response.data['msg'])
        self.assertFalse(RetirementAudit.objects.filter(account=self.original).exists())
        self.ok(transfer_response)
        self.assert_occupancy(self.replacement, self.original, 1)
        self.points.refresh_from_db()
        self.assertEqual(self.points.points_balance, 5000)
        self.assertEqual(list(PointLedger.objects.filter(order_id=self.order_id).values_list('amount', flat=True)), [-10000])
        revision = OrderPointsTransferFundingRevision.objects.get(order_id=self.order_id)
        change = revision.last_transfer
        allocation = change.deal_allocation
        self.assertEqual((allocation.basis_before, allocation.retained_basis, allocation.transferred_basis), (10000, 4000, 6000))
        self.assertEqual((change.previous_segment.financial_snapshot.commission_rate,
                          change.next_segment.financial_snapshot.commission_rate), (20, 30))
        self.assertEqual((revision.before_funding['additional_store_funding_units'],
                          revision.after_funding['additional_store_funding_units']), (2000, 1400))
        self.assertEqual(revision.releases.get().amount, 600)
        self.join_replacement(self.api(self.replacement), change)
        status = self.ok(self.finance_client.get(f'/api/admin/platform/points-funding/{self.store.slug}/orders/{self.order_id}'))
        self.assertEqual((status['required_units'], status['allocated_units'], status['remaining_units'],
                          status['fully_funded']), (1400, 1400, 0, True))
        replay = self.ok(self.finance_client.post(self.funding_url, self.funding_payload, format='json'))
        self.assertEqual((replay['allocation_id'], replay['amount']), (self.original_allocation['allocation_id'], 2000))
        events = PassReceiptUsageEvent.objects.filter(state__receipt=self.funding_receipt).order_by('version')
        self.assertEqual(list(events.values_list('kind', 'amount')), [('ORDER', 2000), ('RELEASE', 600)])
        print(f'{self.store.slug} reservation={self.reservation_id} order={self.order_id} '
              f'waiting_session={waited} retirement=400 transfer=0', flush=True)

    @override_settings(SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True)
    def test_funded_points_reservation_pays_each_provider_at_their_own_deadline(self):
        self.run_in_stores(self.exercise_settlement_after_transfer)

    def approve_report(self, account, segment_id, *, verify_detail=False):
        report_url = f'/api/orders/orders/{self.order_id}/segment-report/'
        provider = self.api(account)
        staff = self.api(self.staff)
        for action, payload, request_format in (
            ('open', {}, 'json'),
            ('upload', {'image': release_fixture.screenshot()}, 'multipart'),
            ('submit', {'version': 0}, 'json'),
        ):
            self.ok(provider.post(report_url, {'segment_id': segment_id, 'action': action,
                'client_request_id': str(uuid4()), **payload}, format=request_format))
        if verify_detail:
            with connections[self.store.database_alias].execute_wrapper(self.reject_detail_writes):
                detail = self.ok(staff.get(f'/api/admin/orders/{self.order_id}/'))
            segment = next(row for row in detail['original_attribution']['segments'] if row['id'] == segment_id)
            self.assertEqual(segment['segment_report']['status'], 'PENDING')
        self.ok(staff.post(f'/api/admin/orders/{self.order_id}/segments/{segment_id}/report-review/',
            {'action': 'approve', 'version': 1}, format='json'))
        return OrderSegmentReportSubmission.objects.get(report__segment_id=segment_id, version=1)

    def reject_detail_writes(self, execute, sql, params, many, context):
        verb = sql.lstrip().split(maxsplit=1)[0].upper()
        self.assertNotIn(verb, ('INSERT', 'UPDATE', 'DELETE', 'REPLACE'), '详情读取不得写入本店记录')
        return execute(sql, params, many, context)

    def assert_preview(self, order, change, first_due, second_due):
        detail_url = f'/api/admin/orders/{order.pk}/'
        url = detail_url + 'segment-settlement-plan/'
        self.assertEqual(self.api(self.original).get(url).status_code, 403)
        alias = self.store.database_alias

        with patch('django.utils.timezone.now', return_value=first_due - timedelta(seconds=1)):
            staff = self.api(self.staff)
            with connections[alias].execute_wrapper(self.reject_detail_writes):
                preview = self.ok(staff.get(url))
            with override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, ORDINARY_TRANSFER_REFUND_MYSQL_ENABLED=True,
                                   NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True):
                with connections[alias].execute_wrapper(self.reject_detail_writes):
                    self.assertEqual(preview, self.ok(staff.get(url)))
                detail = self.ok(staff.get(detail_url))
                segments = detail['original_attribution']['segments']
                self.assertEqual({part['id']: (part.get('segment_report') or {}).get('status') for part in segments},
                                 {change.previous_segment_id: 'APPROVED', change.next_segment_id: 'APPROVED'})

        self.assertEqual((preview['deal_amount'], preview['inviter_income'], preview['store_income']), (10000, 0, 0))
        self.assertEqual({part['segment_id']: (part['account_id'], part['basis'], part['fee'], part['income'])
                          for part in preview['segments']}, {
            change.previous_segment_id: (self.original.pk, 4000, 800, 3200),
            change.next_segment_id: (self.replacement.pk, 6000, 1800, 4200),
        })
        self.assertEqual({person['account_id']: person['freeze_until'] for person in preview['persons']},
                         {self.original.pk: first_due, self.replacement.pk: second_due})
        for person in preview['persons']:
            self.assertIn('本人报单冻结期尚未结束', person['blockers'])

        with transaction.atomic(using=alias):
            OrderFinancialLock.objects.filter(pk=1).update(id=2)
            with connections[alias].execute_wrapper(self.reject_detail_writes):
                unavailable = self.api(self.staff).get(url)
            self.assertEqual(unavailable.status_code, 409, unavailable.data)
            self.assertIn('订单资金互斥记录缺失', unavailable.data['msg'])
            self.assertFalse(OrderFinancialLock.objects.filter(pk=1).exists())
            transaction.set_rollback(True, using=alias)

    def exercise_settlement_after_transfer(self):
        self.ok(self.transfer(self.api(self.staff)))
        revision = OrderPointsTransferFundingRevision.objects.get(order_id=self.order_id)
        change = revision.last_transfer
        path = f'/api/orders/orders/{self.order_id}/'
        preview_url = f'/api/admin/orders/{self.order_id}/segment-settlement-plan/'
        self.assertEqual(self.api(self.staff).get(preview_url).status_code, 409)
        original = self.api(self.original)
        evidence_key = 'points-transfer-original-evidence'

        def submit_evidence(segment_id):
            return original.post(path + 'participation-evidence/', {'segment_id': segment_id,
                'completion_image': release_fixture.screenshot(), 'client_request_id': evidence_key}, format='multipart')

        self.ok(submit_evidence(change.previous_segment_id))
        original_segment = change.previous_segment
        completion_image = original_segment.completion_image.name
        self.assertTrue(completion_image)
        self.assertTrue(self.ok(submit_evidence(original_segment.pk))['request_replayed'])
        self.assertEqual(submit_evidence(change.next_segment_id).status_code, 409)
        original_segment.refresh_from_db()
        self.assertEqual(original_segment.completion_image.name, completion_image)
        receipt = IdempotencyKey.objects.get(account=self.original, request_id=evidence_key)
        self.assertEqual((receipt.scope, receipt.response),
                         ('order.segment-completion-evidence', {'order_id': self.order_id, 'action': 'evidence'}))
        replacement = self.api(self.replacement)
        self.join_replacement(replacement, change)
        self.ok(replacement.post(path + 'complete/', {'completion_image': release_fixture.screenshot(),
            'client_request_id': 'points-transfer-complete'}, format='multipart'))
        original_report = self.approve_report(self.original, change.previous_segment_id)
        with patch('django.utils.timezone.now', return_value=original_report.submitted_at + timedelta(minutes=15)):
            replacement_report = self.approve_report(self.replacement, change.next_segment_id)

        order = Order.objects.get(pk=self.order_id)
        hold = timedelta(hours=order.service_freeze_hours)
        first_due = original_report.submitted_at + hold
        second_due = replacement_report.submitted_at + hold
        income = order.transactions.filter(tx_type=Transaction.TxType.INCOME).order_by('pk')
        consumption = ConsumptionConfirmation.objects.filter(order=order)
        loyalty_points = PointLedger.objects.filter(order=order, kind='CONSUMPTION')
        self.assert_preview(order, change, first_due, second_due)
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        with patch('django.utils.timezone.now', return_value=first_due - timedelta(seconds=1)):
            self.assertEqual(confirm_due_consumption.run(), 0)
        self.assertFalse(income.exists())
        self.assertFalse(consumption.exists())

        with patch('django.utils.timezone.now', return_value=first_due):
            self.assertEqual(confirm_due_consumption.run(), 0)
            self.assertEqual(confirm_due_consumption.run(), 0)
            self.assertEqual(self.api(self.staff).get(preview_url).status_code, 409)
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        self.assertIsNone(manifest.finalized_at)
        original = manifest.people.get()
        self.assertEqual((original.account_id, original.segment_ids, original.amount, original.settled_at),
                         (self.original.pk, [change.previous_segment_id], 3200, first_due))
        self.assertEqual(list(income.values_list('wallet__account_id', 'amount')), [(self.original.pk, 3200)])
        self.assertFalse(consumption.exists())
        self.assertFalse(loyalty_points.exists())

        with patch('django.utils.timezone.now', return_value=second_due):
            self.assertEqual(confirm_due_consumption.run(), 1)
            self.assertEqual(confirm_due_consumption.run(), 0)
        order.refresh_from_db()
        manifest.refresh_from_db()
        self.assertEqual((order.settled_at, manifest.finalized_at, order.consumption_ready_at),
                         (second_due, second_due, second_due))
        self.assertEqual((order.provider_income, order.settlement_store_funding, order.shop_income,
                          order.inviter_commission), (7400, 1400, 0, 0))
        self.assertEqual(list(income.values_list('wallet__account_id', 'amount')),
                         [(self.original.pk, 3200), (self.replacement.pk, 4200)])
        replacement_payment = manifest.people.get(account=self.replacement)
        self.assertEqual((replacement_payment.segment_ids, replacement_payment.amount, replacement_payment.settled_at),
                         ([change.next_segment_id], 4200, second_due))
        confirmed = consumption.get()
        self.assertEqual((confirmed.account_id, confirmed.amount), (self.buyer.pk, 6000))
        earned_points = 6000 * order.funding_terms['points_rate'] * order.funding_terms['birthday_multiplier'] // 100
        self.assertEqual(loyalty_points.get().amount, earned_points)
        self.assertEqual(list(PointLedger.objects.filter(order=order, amount__lt=0).values_list('amount', flat=True)), [-10000])
        self.assertEqual(list(order.transactions.filter(tx_type=Transaction.TxType.PAY).values_list('amount', flat=True)), [-6000])
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, self.balance_before)
        events = PassReceiptUsageEvent.objects.filter(state__receipt=self.funding_receipt).order_by('version')
        self.assertEqual(list(events.values_list('kind', 'amount')), [('ORDER', 2000), ('RELEASE', 600)])
        print(f'{self.store.slug} reservation={self.reservation_id} order={order.pk} '
              f'preview_segments={change.previous_segment_id},{change.next_segment_id} '
              f'first_income={original.income_transaction_id}:3200 second_income={replacement_payment.income_transaction_id}:4200 '
              f'first_due={first_due.isoformat()} second_due={second_due.isoformat()} consumption={confirmed.pk}:6000',
              flush=True)
