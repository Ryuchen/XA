"""Exercise points checkout and settlement against an actual isolated recharge."""
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from engagement.services import change_points
from orders.tests import test_points_checkout_integration as integration
from orders.tests import test_points_redemption_preview as quote_fixture
from orders.tests.factories import make_console_user, make_provider
from stores.context import store_context
from stores import test_platform_pass_repayment as platform_tests
from wallet.models import RechargeRecord, Transaction
from engagement.models import PointLedger
from orders.models import Order, OrderPointsRefund, OrderRefund


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsRealRechargeClosureTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    payload = quote_fixture.PointsServiceQuoteTest.payload
    quoted_payload = integration.PointsCheckoutIntegrationTests.quoted_payload
    fund = integration.PointsFundingPlatformRecoveryTests.fund
    verify = integration.PointsFundingPlatformRecoveryTests.verify

    def setUp(self):
        platform_tests.PlatformPassRepaymentTests.setUp(self)
        self.platform_client = self.client
        with store_context(self.second):
            quote_fixture.PointsServiceQuoteTest.setUp(self)
            # The shared quote fixture sets a balance directly. Replace it with
            # a real, isolated staff recharge before any order is submitted.
            self.wallet.balance = 0
            self.wallet.save(update_fields=['balance'])
            self.profile.points_balance = 0
            self.profile.save(update_fields=['points_balance'])
            change_points(self.profile, 15000, 'isolated-points-closure:award',
                          'TEST_AWARD', '隔离测试积分')
            staff = make_console_user(['wallet:recharge'])
            staff_account = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = staff_account
            staff.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
            quote = admin.get('/api/admin/wallets/recharge-quote/', {'amount': 8000})
            self.assertEqual(quote.status_code, 200, quote.data)
            result = admin.post('/api/admin/wallets/recharge/', {
                'account_id': self.account.pk, 'amount': 8000, 'gift_amount': 0,
                'trade_no': 'isolated-points-closure', 'client_request_id': 'isolated-points-closure',
                'bonus_version': quote.data['data']['version'],
            }, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(RechargeRecord.objects.filter(account=self.account).count(), 1)
            self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='TOPUP').count(), 1)
            self.client = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            save_points_terms(group_point_hundredths=10000, group_coin_units=2000,
                              max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
            provider = make_provider(deposit_mode='EARNINGS')
            provider_account = get_or_create_account_for_legacy_user(provider)
            provider.escort_profile.account = provider_account
            provider.escort_profile.save(update_fields=['account'])
            payload = {**self.payload(), 'dispatch_mode': 'BOSS_SELECT', 'dispatch_route': 'PUBLIC',
                       'provider_ids': [provider_account.pk]}
            quote = self.client.post('/api/orders/quote/', payload, format='json')
            self.assertEqual(quote.status_code, 200, quote.data)
            self.assertEqual(quote.data['code'], 0, quote.data)
            token = quote.data['data']['points_preview']['checkout_test_token']
            create_data = {key: value for key, value in payload.items() if key != 'preview_points_hundredths'}
            created = self.client.post('/api/orders/create/',
                {**create_data, 'points_checkout_test_token': token}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            self.order = Order.objects.get(pk=created.data['data']['id'])
            self.assertEqual(self.order.status, Order.Status.GRABBED)
            self.assertTrue(self.order.providers.filter(provider_account=provider_account).exists())
        self.client = self.platform_client
        registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan')
        self.assertEqual(registered.status_code, 200, registered.data)
        self.claim = registered.data['data']['claim_id']
        self.receipt_id = registered.data['data']['receipt_id']
        self.url = f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/points-allocation'
        from uuid import uuid4
        self.action_data = {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 300}

    test_report_waits_for_full_funding_then_posts_real_income_once = (
        integration.PointsSettlementTwoStoreTests.test_report_waits_for_full_funding_then_posts_real_income_once)

    @override_settings(SERVICE_POINTS_ASSIGNED_CANCEL_ENABLED=True)
    def test_staff_refund_of_unstarted_assigned_order_is_atomic_and_idempotent(self):
        with store_context(self.second):
            staff = make_console_user(['order:refund'])
            account = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = account
            staff.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            path = f'/api/orders/orders/{self.order.pk}/refund/'
            data = {'reason': '客服协调取消', 'client_request_id': 'assigned-points-cancel-1'}
            with patch('orders.points_service_refund.change_points', side_effect=RuntimeError('模拟积分退款失败')):
                with self.assertRaises(RuntimeError):
                    admin.post(path, data, format='json')
            self.wallet.refresh_from_db(); self.profile.refresh_from_db(); self.order.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.profile.points_balance, self.order.status), (4000, 5000, 'GRABBED'))
            self.assertFalse(OrderRefund.objects.filter(order=self.order).exists())
            result = admin.post(path, data, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            self.wallet.refresh_from_db(); self.profile.refresh_from_db(); self.order.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.profile.points_balance, self.order.status), (8000, 15000, 'CANCELLED'))
            self.assertEqual(self.order.payment_status, Order.PaymentStatus.REFUNDED)
            receipt = OrderPointsRefund.objects.get(refund__order=self.order)
            self.assertEqual((receipt.coin_units, receipt.points_hundredths), (4000, 10000))
            repeated = admin.post(path, data, format='json')
            self.assertEqual(repeated.data['code'], 0, repeated.data)
            self.assertEqual(OrderRefund.objects.filter(order=self.order).count(), 1)
            self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)
            PointLedger.objects.filter(pk=receipt.points_ledger_id).update(amount=1)
            damaged = admin.post(path, data, format='json')
            self.assertEqual(damaged.data['errorCode'], 'REFUND_LEDGER_INCOMPLETE')
            self.assertEqual(OrderRefund.objects.filter(order=self.order).count(), 1)

    @override_settings(SERVICE_POINTS_ASSIGNED_CANCEL_ENABLED=True,
                       RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True,
                       RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING=True)
    def test_unstarted_refund_releases_each_receipt_and_preserves_other_use(self):
        from uuid import uuid4

        from orders.models import OrderPointsFundingAllocation, PointsPrestartCancelFundingEvidence
        from stores.db import transaction
        from users.models import PassReceiptUsageEvent, PassRefundRepaymentReceipt
        from users.receipt_usage_ledger import initialize_empty_locked
        from users.receipt_usage_sources import verify_allocations_locked

        # Both incoming receipts are isolated bookkeeping fixtures.
        register = platform_tests.PlatformPassRepaymentTests.post
        finance_action = platform_tests.PlatformPassRepaymentTests.action
        other = register(self, 'xinan', {**self.data, 'request_id': str(uuid4()),
                                        'external_reference': 'isolated-prestart-second-receipt'})
        self.assertEqual(other.status_code, 200, other.data)
        other_claim = other.data['data']['claim_id']
        other_receipt = other.data['data']['receipt_id']
        receipts = (self.receipt_id, other_receipt)
        for claim in (self.claim, other_claim):
            result = finance_action(self, claim, 'verify', {'request_id': str(uuid4())})
            self.assertEqual(result.status_code, 200, result.data)
        with store_context(self.second), transaction.atomic():
            for receipt_id in receipts:
                initialize_empty_locked(receipt_id)
        pass_refund, _ = platform_tests.PlatformPassRepaymentTests.setup_refunds(self)
        passage = finance_action(self, self.claim, 'allocate',
            {'request_id': str(uuid4()), 'refund_id': pass_refund, 'amount': 100})
        self.assertEqual(passage.status_code, 200, passage.data)
        for claim, amount in ((self.claim, 300), (other_claim, 200)):
            allocated = finance_action(self, claim, 'points-allocation',
                {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': amount})
            self.assertEqual(allocated.status_code, 200, allocated.data)

        def usage():
            with transaction.atomic():
                states = [verify_allocations_locked(receipt_id) for receipt_id in receipts]
                return [(state.pass_units, state.order_units, state.released_units) for state in states]

        def available():
            response = platform_tests.PlatformPassRepaymentTests.reconciliation(self)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['data']['status'], 'OK', response.data)
            return {row['receipt']['id']: row['receipt']['unallocated_amount']
                    for row in response.data['data']['claims']}

        self.assertEqual(available(), {self.receipt_id: 100, other_receipt: 300})

        # Substitute the MySQL configuration prerequisite for this SQLite test.
        with (store_context(self.second),
              self.settings(SERVICE_POINTS_MYSQL_ENABLED=True, RECEIPT_USAGE_LEDGER_ENABLED=True),
              patch('orders.points_business_access.configured', return_value=True)):
            staff = make_console_user(['order:refund'])
            account = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = account
            staff.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            order_url = f'/api/admin/orders/{self.order.pk}/'
            request = {'reason': '核对未开工后全退', 'client_request_id': 'prestart-two-receipt-refund'}
            preview = admin.get(order_url + 'refund-preview/')
            self.assertEqual(preview.data['code'], 0, preview.data)
            self.assertEqual(preview.data['data']['released_funding_units'], 500)
            self.assertEqual(usage(), [(100, 300, 0), (0, 200, 0)])

            create_event = PassReceiptUsageEvent.objects.create
            release_attempts = []

            def fail_second_release(**fields):
                if fields['kind'] == 'RELEASE':
                    receipt_id = fields['state'].receipt_id
                    release_attempts.append(receipt_id)
                    if receipt_id == other_receipt:
                        raise RuntimeError('隔离测试中断第二笔释放写入')
                return create_event(**fields)

            with patch('users.receipt_usage_ledger.PassReceiptUsageEvent.objects.create', side_effect=fail_second_release):
                with self.assertRaisesMessage(RuntimeError, '隔离测试中断第二笔释放写入'):
                    admin.post(order_url + 'refund/', request, format='json')
            self.assertEqual(release_attempts, list(receipts))
            self.assertEqual(usage(), [(100, 300, 0), (0, 200, 0)])
            self.assertFalse(self.order.refund_records.exists())
            self.assertFalse(PointsPrestartCancelFundingEvidence.objects.exists())
            self.wallet.refresh_from_db()
            self.profile.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.profile.points_balance), (4000, 5000))

            result = admin.post(order_url + 'refund/', request, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual(admin.post(order_url + 'refund/', request, format='json').data['code'], 0)
            refund = self.order.refund_records.get()
            evidence = PointsPrestartCancelFundingEvidence.objects.get(points_refund__refund=refund)
            self.assertEqual(evidence.store_slug, self.second.slug)
            self.assertEqual(list(evidence.releases.order_by('allocation__receipt_id').values_list(
                'allocation__receipt_id', 'amount')), [(self.receipt_id, 300), (other_receipt, 200)])
            self.assertEqual(OrderPointsFundingAllocation.objects.filter(redemption__order=self.order).count(), 2)
            self.assertEqual(usage(), [(100, 300, 300), (0, 200, 200)])
            self.wallet.refresh_from_db()
            self.profile.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 15000))
            self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)

        self.assertEqual(available(), {self.receipt_id: 400, other_receipt: 500})
        with store_context(self.second):
            buyer = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            with patch.object(self, 'client', buyer):
                payload = self.payload()
            payload.update(dispatch_mode='BOSS_SELECT', dispatch_route='PUBLIC',
                           provider_ids=[self.order.providers.get().provider_account_id],
                           client_request_id='prestart-reuse-after-refund')
            quote = buyer.post('/api/orders/quote/', payload, format='json')
            self.assertEqual(quote.data['code'], 0, quote.data)
            token = quote.data['data']['points_preview']['checkout_test_token']
            payload.pop('preview_points_hundredths')
            created = buyer.post('/api/orders/create/', {**payload, 'points_checkout_test_token': token}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            new_order_id = created.data['data']['id']
        self.assertNotEqual(new_order_id, self.order.pk)
        reallocated = self.fund({'request_id': str(uuid4()), 'order_id': new_order_id, 'amount': 350})
        self.assertEqual(reallocated.status_code, 200, reallocated.data)
        self.assertEqual(available(), {self.receipt_id: 50, other_receipt: 500})
        with store_context(self.second):
            self.assertEqual(usage(), [(100, 650, 300), (0, 200, 200)])
            self.assertEqual(self.order.refund_records.get().pk, refund.pk)
            self.assertEqual(admin.post(order_url + 'refund/', request, format='json').data['code'], 0)
        self.assertEqual(available(), {self.receipt_id: 50, other_receipt: 500})
        with store_context(self.first):
            self.assertFalse(PassRefundRepaymentReceipt.objects.exists())
