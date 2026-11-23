"""Verify formal points funding and the lifecycle lock order on a paid reservation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointLedger, RetirementAudit
from orders.models import Order, OrderFinancialLock, OrderPointsRedemption, OrderPointsTransferFundingRevision
from orders.tests import test_assigned_order_reservation_guard as reservation_fixture
from orders.tests import test_release_flow as release_fixture
from orders.tests import test_reservation_transfer_schedule_mysql as transfer_fixture
from orders.tests.factories import make_console_user, make_order, make_provider
from stores.models import PlatformAccount, PlatformRoleGrant
from users.models import PassRefundRepaymentReceipt, PassReceiptUsageEvent
from wallet.models import Wallet
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

    def prepare(self):
        reservation_fixture.AssignedOrderReservationGuardTest.setUp(self)
        self.original = self.accounts[self.provider.pk]
        self.buyer = self.accounts[self.boss.pk]
        self.staff = self.accounts[self.admin.pk]
        get_wallet(account=self.original, user=self.provider)
        profile = self.provider.escort_profile
        profile.deposit_paid = 0
        profile.save(update_fields=['deposit_paid'])
        self.prepare_retirement()
        # Historical completed-order fixture makes retirement check the real
        # order-money lock even while the new points reservation is in service.
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
        self.run_in_stores(self.exercise_retirement_and_points_transfer)

    def exercise_retirement_and_points_transfer(self):
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
                    lambda: transfer_client.post(f'/api/admin/orders/{self.order_id}/transfer/', {
                        'reason': '客服确认接替原服务', 'transfers': [{
                            'old_provider_id': self.original.pk, 'new_provider_id': self.replacement.pk,
                            'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方确认原价份额'},
                        }]}, format='json'),
                    self.observe_lock_attempt(attempts, lifecycle_table, 'lock in share mode', account_id=self.original.pk))
                waited = self.wait_for_lock(transfer, attempts, table=lifecycle_table, include_shared=True)
                with transaction.atomic(using=self.store.database_alias):
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
        self.ok(self.api(self.replacement).post(f'/api/orders/orders/{self.order_id}/participation-start/',
            {'segment_id': change.next_segment_id, 'entry_image': release_fixture.screenshot(),
             'client_request_id': 'points-transfer-join'}, format='multipart'))
        status = self.ok(self.finance_client.get(f'/api/admin/platform/points-funding/{self.store.slug}/orders/{self.order_id}'))
        self.assertEqual((status['required_units'], status['allocated_units'], status['remaining_units'],
                          status['fully_funded']), (1400, 1400, 0, True))
        replay = self.ok(self.finance_client.post(self.funding_url, self.funding_payload, format='json'))
        self.assertEqual((replay['allocation_id'], replay['amount']), (self.original_allocation['allocation_id'], 2000))
        events = PassReceiptUsageEvent.objects.filter(state__receipt=self.funding_receipt).order_by('version')
        self.assertEqual(list(events.values_list('kind', 'amount')), [('ORDER', 2000), ('RELEASE', 600)])
        print(f'{self.store.slug} reservation={self.reservation_id} order={self.order_id} '
              f'waiting_session={waited} retirement=400 transfer=0', flush=True)
