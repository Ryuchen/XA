"""Verify formal points funding and the lifecycle lock order on a paid reservation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from console import test_payroll_late_activity as payroll_fixture
from console.payroll_models import PayrollEventBarrier, PayrollRun
from console.points_redemption_config import save_points_terms
from engagement.models import ConsumptionConfirmation, MemberProfile, PointLedger, RetirementAudit
from engagement.tasks import confirm_due_consumption
from orders.models import (AfterSale, Order, OrderFinancialLock, OrderPointsRedemption, OrderPointsTransferFundingRevision,
                           OrderSegmentReportSubmission, OrderSegmentSettlementManifest)
from orders.tests import test_assigned_order_reservation_guard as reservation_fixture
from orders.tests import test_release_flow as release_fixture
from orders.tests import test_reservation_transfer_schedule_mysql as transfer_fixture
from orders.tests.factories import make_console_user, make_order, make_provider
from stores.models import PlatformAccount, PlatformRoleGrant
from users.models import PassRefundRepaymentReceipt, PassReceiptUsageEvent
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
        self.ok(self.recharge())
        self.points, _ = MemberProfile.objects.update_or_create(account=self.buyer, defaults={'points_balance': 15000})
        save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)

        self.start = (timezone.localtime() + timedelta(days=2)).replace(hour=23, minute=30, second=0, microsecond=0)
        self.end = self.start + timedelta(hours=1)
        buyer = self.api(self.buyer)
        original = self.api(self.original)
        booking = self.ok(buyer.post('/api/orders/reservations/', {'service_id': self.service.pk,
            'provider_account_id': self.original.pk, 'start_time': self.start.isoformat(),
            'end_time': self.end.isoformat()}, format='json'))
        self.reservation_id = booking['id']
        path = f'/api/orders/reservations/{self.reservation_id}/'
        self.ok(original.post(path + 'confirm/', {}, format='json'))
        quote = self.ok(buyer.get(path + 'quote/', {'preview_points_hundredths': 10000}))
        self.assertEqual((quote['amount'], quote['payable_amount']), (10000, 6000))
        self.assertIs(quote['points_preview']['checkout_test_only'], False)
        paid = self.ok(buyer.post(path + 'convert/', {'discount_selection_token': quote['discount_selection_token'],
            'points_checkout_test_token': quote['points_preview']['checkout_test_token'], 'expected_amount': 6000,
            'expected_service': quote['service'], 'client_request_id': 'points-transfer-booking'}, format='json'))
        self.order_id = paid['order']['id']
        self.receipt = OrderPointsRedemption.objects.get(order_id=self.order_id)
        self.assertEqual((self.receipt.deal_units, self.receipt.paid_units, self.receipt.points_hundredths), (10000, 6000, 10000))
        self.funding_receipt = self.fund_reservation()
        self.ok(original.post(f'/api/orders/orders/{self.order_id}/start/',
            {'entry_image': release_fixture.screenshot()}, format='multipart'))
        self.service.commission_override = True
        self.service.commission_rate = 30
        self.service.save(update_fields=['commission_override', 'commission_rate'])
        order = Order.objects.get(pk=self.order_id)
        self.funding_before = list(order.transactions.values_list('pk', 'tx_type', 'amount'))
        self.balance_before = Wallet.objects.get(account=self.buyer).balance

    def transfer(self, client):
        return client.post(f'/api/admin/orders/{self.order_id}/transfer/', {
            'reason': '客服确认接替原服务', 'transfers': [{
                'old_provider_id': self.original.pk, 'new_provider_id': self.replacement.pk,
                'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方确认原价份额'},
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
        self.original_allocation = self.ok(client.post(self.funding_url, self.funding_payload, format='json'))
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

    def approve_report(self, account, segment_id):
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
        self.ok(staff.post(f'/api/admin/orders/{self.order_id}/segments/{segment_id}/report-review/',
            {'action': 'approve', 'version': 1}, format='json'))
        return OrderSegmentReportSubmission.objects.get(report__segment_id=segment_id, version=1)

    def assert_preview(self, order, change, first_due, second_due):
        detail_url = f'/api/admin/orders/{order.pk}/'
        url = detail_url + 'segment-settlement-plan/'
        self.assertEqual(self.api(self.original).get(url).status_code, 403)
        alias = self.store.database_alias

        def reject_writes(execute, sql, params, many, context):
            verb = sql.lstrip().split(maxsplit=1)[0].upper()
            self.assertNotIn(verb, ('INSERT', 'UPDATE', 'DELETE', 'REPLACE'), '预览禁止写入本店记录')
            return execute(sql, params, many, context)

        with patch('django.utils.timezone.now', return_value=first_due - timedelta(seconds=1)):
            staff = self.api(self.staff)
            with connections[alias].execute_wrapper(reject_writes):
                preview = self.ok(staff.get(url))
            with override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, ORDINARY_TRANSFER_REFUND_MYSQL_ENABLED=True,
                                   NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True):
                with connections[alias].execute_wrapper(reject_writes):
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
            with connections[alias].execute_wrapper(reject_writes):
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
