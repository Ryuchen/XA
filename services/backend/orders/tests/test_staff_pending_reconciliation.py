from datetime import timedelta
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.direct_payment import VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, WECHAT_DIRECT_DOMAIN_TESTING=True)
class StaffPendingReconciliationTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.service = make_service()

    def authenticate(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def order(self, **fields):
        return make_order(self.boss, service=self.service, customer_account=self.boss_account, **fields)

    def test_equal_creation_times_have_stable_page_order(self):
        orders = [self.order(dispatch_route='STAFF') for _ in range(3)]
        same = timezone.now()
        Order.objects.filter(pk__in=[row.pk for row in orders]).update(created_at=same)
        self.authenticate(['order:view'])
        first = self.client.get('/api/admin/orders/?page=1&page_size=1').data['data']
        second = self.client.get('/api/admin/orders/?page=2&page_size=1').data['data']
        third = self.client.get('/api/admin/orders/?page=3&page_size=1').data['data']
        self.assertEqual([first['list'][0]['id'], second['list'][0]['id'], third['list'][0]['id']],
                         sorted([row.pk for row in orders], reverse=True))

    def direct_order(self, suffix='1', route='STAFF'):
        self.boss_account.openid = 'staff-test-openid'
        self.boss_account.wechat_appid = 'wx-staff-test'
        self.boss_account.is_openid_bound = True
        self.boss_account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(self.boss, balance=50000)
        request = SimpleNamespace(account=self.boss_account, legacy_user=self.boss, data={
            'service_id': self.service.pk, 'expected_amount': self.service.price,
            'dispatch_route': route, 'client_request_id': f'staff-review-{suffix}',
        })
        intent = prepare_direct_order(request, appid='wx-staff-test', mchid='mch-staff-test')
        evidence = VerifiedPayment(intent.merchant_order_no, f'staff-test-transaction-{suffix}', intent.appid,
            intent.mchid, intent.openid, intent.amount_fen, 'CNY', timezone.now(),
            payer_total_fen=intent.amount_fen, payer_currency='CNY')
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(intent.order_id, evidence)
        intent.refresh_from_db()
        return intent

    def test_expired_newer_rows_cannot_hide_actionable_order_on_first_page(self):
        active = self.order(dispatch_route='STAFF', auto_cancel_at=timezone.now() + timedelta(minutes=5))
        expired = self.order(dispatch_route='STAFF', auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.order(dispatch_route='PUBLIC', auto_cancel_at=timezone.now() + timedelta(minutes=5))
        self.order(dispatch_route='STAFF', payment_status=Order.PaymentStatus.REFUNDED)
        assigned = self.order(dispatch_route='STAFF', provider=make_provider(), auto_cancel_at=None)
        self.authenticate(['order:view', 'order:dispatch'])
        response = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=1')
        self.assertEqual(response.data['data']['total'], 1)
        self.assertEqual(response.data['data']['list'][0]['id'], active.pk)
        self.assertTrue(response.data['data']['list'][0]['staff_dispatch_actionable'])
        expired_detail = self.client.get(f'/api/admin/orders/{expired.pk}/')
        self.assertFalse(expired_detail.data['data']['staff_dispatch_actionable'])
        assigned_detail = self.client.get(f'/api/admin/orders/{assigned.pk}/')
        self.assertFalse(assigned_detail.data['data']['staff_dispatch_actionable'])

    def test_reconciliation_requires_order_view_permission(self):
        self.order(dispatch_route='STAFF')
        self.authenticate(['order:dispatch'])
        response = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=1')
        self.assertEqual(response.status_code, 403)

    def test_order_without_deadline_remains_actionable(self):
        order = self.order(dispatch_route='STAFF', auto_cancel_at=None)
        self.authenticate(['order:view'])
        result = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=1').data['data']
        self.assertEqual(result['list'][0]['id'], order.pk)
        self.assertTrue(result['list'][0]['staff_dispatch_actionable'])

    def test_review_and_unknown_direct_contract_do_not_count_as_staff_dispatch(self):
        legacy = self.order(dispatch_route='STAFF')
        intent = self.direct_order()
        self.authenticate(['order:view', 'order:dispatch'])
        result = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=1').data['data']
        self.assertEqual(result['total'], 2)
        detail = self.client.get(f'/api/admin/orders/{intent.order_id}/').data['data']
        self.assertTrue(detail['staff_dispatch_actionable'])
        self.assertTrue(detail['dispatch_assignment_allowed'])

        intent.status = 'REVIEW'
        intent.save(update_fields=['status'])
        result = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=1').data['data']
        self.assertEqual((result['total'], result['list'][0]['id']), (1, legacy.pk))
        self.assertTrue(result['staff_queue_needs_review'])
        detail = self.client.get(f'/api/admin/orders/{intent.order_id}/').data['data']
        self.assertFalse(detail['staff_dispatch_actionable'])
        self.assertFalse(detail['dispatch_assignment_allowed'])

        intent.status = 'PAID'
        intent.save(update_fields=['status'])
        quote = intent.fee_quote
        quote.contract_version = 'FUTURE_CONTRACT'
        quote.save(update_fields=['contract_version'])
        result = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=1').data['data']
        self.assertEqual((result['total'], result['list'][0]['id']), (1, legacy.pk))
        detail = self.client.get(f'/api/admin/orders/{intent.order_id}/').data['data']
        self.assertFalse(detail['staff_dispatch_actionable'])
        self.assertFalse(detail['dispatch_assignment_allowed'])

    def test_non_test_environment_excludes_simulated_payment_and_reports_review(self):
        legacy = self.order(dispatch_route='STAFF')
        self.direct_order()
        self.authenticate(['order:view', 'order:dispatch'])
        with patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            result = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=20').data['data']
        self.assertEqual(result['total'], 1)
        self.assertEqual([item['id'] for item in result['list']], [legacy.pk])
        self.assertTrue(result['staff_queue_needs_review'])

    def test_corrupt_hash_cannot_hide_valid_order_or_inflate_count(self):
        legacy = self.order(dispatch_route='STAFF')
        valid = self.direct_order('valid')
        intent = self.direct_order('bad')
        self.authenticate(['order:view', 'order:dispatch'])
        quote = intent.fee_quote
        quote.quote_hash = '0' * 64 if quote.quote_hash != '0' * 64 else '1' * 64
        quote.save(update_fields=['quote_hash'])
        result = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=1').data['data']
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['list'][0]['id'], valid.order_id)
        self.assertTrue(result['staff_queue_needs_review'])
        detail = self.client.get(f'/api/admin/orders/{intent.order_id}/').data['data']
        self.assertFalse(detail['staff_dispatch_actionable'])
        self.assertFalse(detail['dispatch_assignment_allowed'])
        next_page = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=1&page=2').data['data']
        self.assertEqual(next_page['list'][0]['id'], legacy.pk)

    def test_direct_scan_limit_reports_incomplete_without_losing_balance_orders(self):
        legacy = self.order(dispatch_route='STAFF')
        first, second = self.direct_order('1'), self.direct_order('2')
        now = timezone.now()
        Order.objects.filter(pk=first.order_id).update(auto_cancel_at=now + timedelta(minutes=3))
        Order.objects.filter(pk=second.order_id).update(auto_cancel_at=now + timedelta(minutes=10))
        self.authenticate(['order:view', 'order:dispatch'])
        with patch('console.views.STAFF_DIRECT_VERIFY_LIMIT', 1):
            page = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=20').data['data']
        self.assertTrue(page['staff_queue_needs_review'])
        self.assertEqual(page['total'], 2)
        self.assertIn(legacy.pk, {item['id'] for item in page['list']})
        self.assertEqual([item['id'] for item in page['list']], [first.order_id, legacy.pk])
        # The review path remains accessible without the actionable-only
        # filter; it contains all staff PENDING/PAID orders, including the one
        # not checked under the bound.
        review = self.client.get('/api/admin/orders/?status=PENDING&payment_status=PAID&dispatch_route=STAFF&page_size=20').data['data']
        self.assertEqual({item['id'] for item in review['list']}, {legacy.pk, first.order_id, second.order_id})

    def test_direct_quotes_are_loaded_in_bulk_for_queue_verification(self):
        self.direct_order('1')
        self.authenticate(['order:view', 'order:dispatch'])
        def quote_queries():
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/api/admin/orders/?actionable_staff=1&page_size=20')
            self.assertEqual(response.data['code'], 0)
            return sum('orders_directpaymentquote' in entry['sql'].lower() for entry in queries.captured_queries)
        initial = quote_queries()
        self.direct_order('2')
        self.direct_order('3')
        self.assertEqual(quote_queries(), initial)

    def test_expired_direct_order_is_not_misreported_as_review(self):
        legacy = self.order(dispatch_route='STAFF')
        intent = self.direct_order()
        Order.objects.filter(pk=intent.order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.authenticate(['order:view', 'order:dispatch'])
        result = self.client.get('/api/admin/orders/?actionable_staff=1').data['data']
        self.assertFalse(result['staff_queue_needs_review'])
        self.assertEqual((result['total'], result['list'][0]['id']), (1, legacy.pk))

    def test_staff_team_with_existing_members_is_not_counted_as_actionable(self):
        legacy_single = self.order(dispatch_route='STAFF')
        OrderProvider.objects.create(order=legacy_single, provider=make_provider())
        team_service = make_service(billing_provider_count=2)
        team = make_order(self.boss, service=team_service, customer_account=self.boss_account,
                          dispatch_route='STAFF')
        OrderProvider.objects.create(order=team, provider=make_provider())
        self.authenticate(['order:view', 'order:dispatch'])
        result = self.client.get('/api/admin/orders/?actionable_staff=1').data['data']
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['list'][0]['id'], legacy_single.pk)
        detail = self.client.get(f'/api/admin/orders/{team.pk}/').data['data']
        self.assertFalse(detail['staff_dispatch_actionable'])
        self.assertFalse(detail['dispatch_assignment_allowed'])

    def test_unscoped_admin_serialization_never_offers_assignment(self):
        from console.serializers import AdminOrderSerializer
        order = self.order(dispatch_route='STAFF')
        data = AdminOrderSerializer(order).data
        self.assertFalse(data['staff_dispatch_actionable'])
        self.assertFalse(data['dispatch_assignment_allowed'])
        self.assertFalse(data['dispatch_message_allowed'])

    def test_public_dispatch_message_respects_payment_and_team_rules(self):
        legacy = self.order(dispatch_route='PUBLIC')
        team_service = make_service(billing_provider_count=2)
        team = make_order(self.boss, service=team_service, customer_account=self.boss_account,
                          dispatch_route='PUBLIC')
        intent = self.direct_order(route='PUBLIC')
        self.authenticate(['order:view', 'order:dispatch'])
        def detail(order_id):
            return self.client.get(f'/api/admin/orders/{order_id}/').data['data']
        self.assertTrue(detail(legacy.pk)['dispatch_message_allowed'])
        self.assertFalse(detail(team.pk)['dispatch_message_allowed'])
        self.assertIn('双陪订单暂不支持群发', detail(team.pk)['dispatch_message_unavailable_reason'])
        self.assertTrue(detail(intent.order_id)['dispatch_message_allowed'])
        quote = intent.fee_quote
        original_hash = quote.quote_hash
        quote.quote_hash = '0' * 64 if original_hash != '0' * 64 else '1' * 64
        quote.save(update_fields=['quote_hash'])
        bad_quote = detail(intent.order_id)
        self.assertFalse(bad_quote['dispatch_message_allowed'])
        self.assertIn('待核对', bad_quote['dispatch_message_unavailable_reason'])
        quote.quote_hash = original_hash
        quote.save(update_fields=['quote_hash'])
        intent.evidence_risk_at = timezone.now()
        intent.save(update_fields=['evidence_risk_at'])
        self.assertFalse(detail(intent.order_id)['dispatch_message_allowed'])
        intent.evidence_risk_at = None
        intent.status = 'REVIEW'
        intent.save(update_fields=['evidence_risk_at', 'status'])
        self.assertFalse(detail(intent.order_id)['dispatch_message_allowed'])
        self.assertTrue(detail(legacy.pk)['dispatch_message_allowed'])

    def test_legacy_team_snapshot_and_missing_snapshot_are_distinguished(self):
        old_single = self.order(dispatch_route='STAFF')
        old_team = self.order(dispatch_route='STAFF')
        Order.objects.filter(pk=old_single.pk).update(required_provider_count=None, gameplay_snapshot={})
        Order.objects.filter(pk=old_team.pk).update(required_provider_count=None,
            gameplay_snapshot={'billing_provider_count': 2})
        for order in (old_single, old_team):
            OrderProvider.objects.create(order=order, provider=make_provider())
        self.authenticate(['order:view', 'order:dispatch'])
        data = self.client.get('/api/admin/orders/?actionable_staff=1').data['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['list'][0]['id'], old_single.pk)
        detail = self.client.get(f'/api/admin/orders/{old_team.pk}/').data['data']
        self.assertFalse(detail['staff_dispatch_actionable'])
