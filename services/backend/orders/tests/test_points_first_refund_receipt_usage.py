from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction as django_transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import (AfterSale, NegotiationDecision, Order, OrderProvider, OrderPointsFundingRelease,
                           OrderPointsFundingReleaseEvidence)
from orders.tests import test_points_recharge_closure as base
from orders.tests.test_release_flow import screenshot
from orders.tests.factories import make_console_user
from stores import test_platform_pass_repayment as platform_tests
from stores.context import store_context
from stores.db import transaction
from users.models import PassReceiptUsageEvent
from users.receipt_usage_ledger import initialize_empty_locked
from users.receipt_usage_sources import verify_allocations_locked


@override_settings(**base.PointsRealRechargeClosureTest._overridden_settings)
@override_settings(SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
    SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
    RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True, RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING=True,
    SERVICE_POINTS_RESERVATION_CANCEL_USAGE_TESTING=True, SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING=True,
    SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True, SERVICE_POINTS_RESERVATION_CANCEL_FUNDING_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
class FirstNegotiationReceiptUsageTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = base.PointsRealRechargeClosureTest.setUp
    payload = base.PointsRealRechargeClosureTest.payload
    quoted_payload = base.PointsRealRechargeClosureTest.quoted_payload
    fund = base.PointsRealRechargeClosureTest.fund
    verify = base.PointsRealRechargeClosureTest.verify

    def test_shared_receipt_first_refund_release_is_atomic_and_replayable(self):
        pass_refund = platform_tests.PlatformPassRepaymentTests.setup_refunds(self)[0]
        self.assertEqual(self.verify().status_code, 200)
        with store_context(self.second), transaction.atomic():
            initialize_empty_locked(self.receipt_id)
        allocated = platform_tests.PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
            {'request_id': str(uuid4()), 'refund_id': pass_refund, 'amount': 100})
        self.assertEqual(allocated.status_code, 200, allocated.data)
        first = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 300})
        self.assertEqual(first.status_code, 200, first.data)
        # Use the remaining 100 on a real points reservation, cancel it, then
        # reuse that same released amount on a different real service order.
        from datetime import timedelta
        from django.utils import timezone
        from console.reservation_deadlines import save_deadlines
        from engagement.models import MemberProfile
        from orders.tests.factories import make_user, make_provider, make_service
        with store_context(self.second):
            save_deadlines(30, 60, 0, lambda *_: None)
            user = make_user(); account = get_or_create_account_for_legacy_user(user)
            MemberProfile.objects.create(account=account, points_balance=10000)
            provider_user = make_provider(deposit_mode='EARNINGS')
            provider = get_or_create_account_for_legacy_user(provider_user)
            provider_user.escort_profile.account = provider; provider_user.escort_profile.save(update_fields=['account'])
            worker = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider)[0])
            buyer = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            staff_user = make_console_user(['wallet:recharge', 'order:view', 'order:refund'])
            staff_account = get_or_create_account_for_legacy_user(staff_user)
            staff_user.admin_membership.account = staff_account; staff_user.admin_membership.save(update_fields=['account'])
            staff_api = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
            recharge = staff_api.post('/api/admin/wallets/recharge/', {'account_id': account.pk, 'amount': 10000,
                'gift_amount': 0, 'trade_no': 'receipt-booking-test', 'client_request_id': 'receipt-booking-recharge'}, format='json')
            self.assertEqual(recharge.data['code'], 0, recharge.data)
            service = make_service(price=10000, commission_rate=10)
            start = (timezone.now()+timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
            created = buyer.post('/api/orders/reservations/', {'provider_account_id': provider.pk, 'service_id': service.pk,
                'start_time': start.isoformat(), 'end_time': (start+timedelta(hours=1)).isoformat()}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            path = f"/api/orders/reservations/{created.data['data']['id']}/"
            self.assertEqual(worker.post(path+'confirm/', {}, format='json').data['code'], 0)
            quote = buyer.get(path+'quote/', {'preview_points_hundredths': 10000}).data['data']
            converted = buyer.post(path+'convert/', {'discount_selection_token': quote['discount_selection_token'],
                'points_checkout_test_token': quote['points_preview']['checkout_test_token'],
                'expected_amount': quote['points_preview']['estimated_payable_units'], 'client_request_id': 'receipt-shared-booking'}, format='json')
            self.assertEqual(converted.data['code'], 0, converted.data)
            reservation_order_id = converted.data['data']['order']['id']
        reservation_alloc = self.fund({'request_id': str(uuid4()), 'order_id': reservation_order_id, 'amount': 100})
        self.assertEqual(reservation_alloc.status_code, 200, reservation_alloc.data)
        cancelled = staff_api.post(f'/api/admin/orders/{reservation_order_id}/refund/',
            {'reason': '预约未开始全退', 'client_request_id': 'receipt-booking-refund'}, format='json')
        self.assertEqual(cancelled.data['code'], 0, cancelled.data)
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        with store_context(self.second):
            other = ServiceFundingApiTest(methodName='test_payment_is_rejected_before_debit_when_funding_route_closed')
            other.setUp()
            other.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            options = other.boss.post('/api/orders/discount-options-preview/',
                {'service_id': other.service.pk}, format='json')
            self.assertEqual(options.data['code'], 0, options.data)
            created = other.boss.post('/api/orders/create/', other.payload(gift_balance_amount=400,
                discount_selection_token=options.data['data']['options'][0]['selection_token'],
                client_request_id='receipt-shared-other'), format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            other_order_id = created.data['data']['id']
        second = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/service-allocation',
            {'request_id': str(uuid4()), 'order_id': other_order_id, 'amount': 100},
            format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(second.status_code, 200, second.data)
        with TemporaryDirectory(prefix='xa-first-receipt-refund-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                assignment = OrderProvider.objects.get(order=order)
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
                started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(started.data['code'], 0, started.data)
                admin = make_console_user(['order:view', 'support:edit', 'order:refund'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                manager = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                opened = manager.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '共享到账退款', 'affected_provider_account_ids': [assignment.provider_account_id],
                    'client_request_id': 'receipt-main-case'}, format='json')
                self.assertEqual(opened.data['code'], 0, opened.data)
                case_id = opened.data['data']['id']
                proposal = manager.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                    'request_id': 'receipt-main-proposal', 'expected_version': 0, 'refund_units': 3000,
                    'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 1800}],
                    'explanation': '双方确认部分退款'}, format='json')
                self.assertEqual(proposal.data['code'], 0, proposal.data)
                proposal_id = proposal.data['data']['id']
                for actor in (boss, player):
                    result = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                        {'proposal_id': proposal_id, 'accepted': True}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                path = f'/api/admin/aftersales/{case_id}/negotiation-execution/'
                payload = {'proposal_id': proposal_id, 'client_request_id': 'receipt-main-refund'}
                original = PassReceiptUsageEvent.objects.create
                def fail_release(**kwargs):
                    if kwargs.get('kind') == 'RELEASE':
                        raise RuntimeError('release event failure')
                    return original(**kwargs)
                with patch('users.receipt_usage_ledger.PassReceiptUsageEvent.objects.create', side_effect=fail_release):
                    with self.assertRaisesMessage(RuntimeError, 'release event failure'):
                        manager.post(path, payload, format='json')
                self.assertFalse(order.refund_records.exists())
                self.assertFalse(OrderPointsFundingReleaseEvidence.objects.exists())
                with transaction.atomic():
                    state = verify_allocations_locked(self.receipt_id)
                    self.assertEqual((state.pass_units, state.order_units, state.released_units), (100, 500, 100))
                result = manager.post(path, payload, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                replay = manager.post(path, payload, format='json')
                self.assertEqual(replay.data['data'], result.data['data'])
                self.assertEqual(manager.get(path, {'proposal_id': proposal_id,
                    'client_request_id': payload['client_request_id']}).data['data']['status'], 'COMPLETED')
                from django.db import connections
                from django.test.utils import CaptureQueriesContext
                from stores.context import current_database
                with transaction.atomic():
                    mismatches = (
                        (AfterSale.objects.filter(pk=case_id), {'account_id': other.account.pk}),
                        (AfterSale.objects.filter(pk=case_id), {'order_id': other_order_id}),
                        (NegotiationDecision.objects.filter(proposal_id=proposal_id, role='BOSS'),
                         {'account_id': other.account.pk}),
                    )
                    for rows, change in mismatches:
                        with self.subTest(change=change), transaction.atomic():
                            self.assertEqual(rows.update(**change), 1)
                            with self.assertRaises(ValidationError):
                                verify_allocations_locked(self.receipt_id)
                            transaction.set_rollback(True)
                    with CaptureQueriesContext(connections[current_database()]) as queries:
                        state = verify_allocations_locked(self.receipt_id)
                    for query in queries.captured_queries:
                        self.assertNotRegex(query['sql'].lower(), r'\borders_order\b')
                    self.assertGreater(state.released_units, 0)
                    self.assertEqual(state.pass_units, 100)
                    release = OrderPointsFundingRelease.objects.get()
                    self.assertEqual(state.released_units, 100 + release.amount)
                    with transaction.atomic():
                        AfterSale.objects.filter(pk=case_id).update(
                            created_at=timezone.now(), client_request_id='fresh-aftersale-episode')
                        reopened = verify_allocations_locked(self.receipt_id)
                        self.assertEqual((reopened.version, reopened.chain_hash), (state.version, state.chain_hash))
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        OrderPointsFundingRelease.objects.filter(pk=release.pk).update(amount=release.amount - 1)
                        with self.assertRaises(ValidationError):
                            verify_allocations_locked(self.receipt_id)
                        transaction.set_rollback(True)
        report = platform_tests.PlatformPassRepaymentTests.reconciliation(self)
        self.assertEqual(report.data['data']['status'], 'OK', report.data)
