import csv
import io
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import path
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog, AdminMembership
from console.order_export import filters_digest
from console.order_export_view import StaffOrderExportView
from console.views import OrderViewSet
from orders.models import Order, OrderProvider
from orders.tests.factories import (
    make_completed_order,
    make_console_user,
    make_order,
    make_provider,
    make_service,
    make_user,
)


urlpatterns = [
    path('api/admin/orders/export/', StaffOrderExportView.as_view()),
    path('api/admin/orders/', OrderViewSet.as_view({'get': 'list'})),
]


def staff_client(permissions):
    user = make_console_user(permissions)
    account = get_or_create_account_for_legacy_user(user)
    link_legacy_relations(user, account)
    AdminMembership.objects.filter(user=user).update(account=account)
    return APIClient(
        HTTP_HOST='admin.esports.club',
        HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0],
    )


@override_settings(
    ROOT_URLCONF=__name__,
    LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'],
    LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False,
)
class StaffOrderExportViewTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.exporter = staff_client(['order:export', 'order:view'])
        cls.export_only = staff_client(['order:export'])
        cls.denied = staff_client(['order:view'])
        cls.customer_user = make_user(nickname='导出回归老板')
        cls.customer = get_or_create_account_for_legacy_user(cls.customer_user)
        link_legacy_relations(cls.customer_user, cls.customer)
        cls.customer.phone = '13800138000'
        cls.customer.save(update_fields=['phone'])
        cls.provider_user = make_provider(display_name='导出回归陪玩')
        cls.provider = get_or_create_account_for_legacy_user(cls.provider_user)
        link_legacy_relations(cls.provider_user, cls.provider)
        cls.provider_user.escort_profile.account = cls.provider
        cls.provider_user.escort_profile.save(update_fields=['account'])
        cls.service = make_service(name='导出回归服务', price=10000)
        cls.completed = make_completed_order(
            cls.customer_user, cls.provider_user, service=cls.service,
            amount=1000, original_amount=1200, provider_income=800,
            payment_status=Order.PaymentStatus.PAID,
            customer_account=cls.customer, provider_account=cls.provider,
            remark='=HYPERLINK("https://example.invalid")',
        )
        OrderProvider.objects.create(
            order=cls.completed, provider=cls.provider_user, provider_account=cls.provider,
            provider_name_snapshot='导出回归陪玩', settlement_base=1000, provider_income=800,
        )
        cls.in_service = make_order(
            cls.customer_user, provider=cls.provider_user, service=cls.service,
            amount=1000, original_amount=1200, provider_income=800,
            status=Order.Status.IN_SERVICE, payment_status=Order.PaymentStatus.PAID,
            customer_account=cls.customer, provider_account=cls.provider,
        )
        cls.url = '/api/admin/orders/export/'

    def parse(self, response):
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'no-store')
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        return dict(zip(rows[9], rows[10])), rows

    def test_export_reuses_list_filters_and_masks_sensitive_values(self):
        response = self.exporter.get(self.url, {
            'status': 'completed', 'customer_phone': '13800138000',
        })
        data, rows = self.parse(response)
        self.assertEqual(data['订单号'], self.completed.order_no)
        self.assertNotEqual(data['订单号'], self.in_service.order_no)
        self.assertEqual(data['老板手机号（脱敏）'], '138****8000')
        self.assertEqual(data['原价（店币）'], '120')
        self.assertEqual(data['成交金额（店币）'], '100')
        self.assertTrue(data['订单备注'].startswith("'="))
        self.assertNotIn('13800138000', response.content.decode('utf-8-sig'))
        listed = self.exporter.get('/api/admin/orders/', {
            'status': 'completed', 'customer_phone': '13800138000',
        })
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual([row['order_no'] for row in listed.data['data']['list']], [self.completed.order_no])
        audit = AdminAuditLog.objects.get(resource='order-export', status_code=200)
        self.assertEqual(audit.request_body['row_count'], 1)
        self.assertEqual(audit.request_body['filters']['customer_phone'], '***')
        self.assertNotIn('13800138000', str(audit.request_body))
        self.assertEqual(audit.object_id, response['X-Export-Id'])
        self.assertIn(['导出凭证', response['X-Export-Id']], rows)
        self.assertEqual(audit.request_body['filters_digest'], rows[5][1])
        self.assertNotEqual(audit.request_body['filters_digest'],
                            filters_digest(audit.request_body['filters']))

    def test_successful_export_digest_distinguishes_sensitive_filter_values(self):
        first = self.exporter.get(self.url, {'order_no_exact': self.completed.order_no})
        second = self.exporter.get(self.url, {'order_no_exact': self.in_service.order_no})
        self.assertEqual(first.status_code, 200, getattr(first, 'data', None))
        self.assertEqual(second.status_code, 200, getattr(second, 'data', None))
        audits = list(AdminAuditLog.objects.filter(resource='order-export', status_code=200).order_by('id'))
        self.assertEqual(len(audits), 2)
        self.assertNotEqual(audits[0].request_body['filters_digest'],
                            audits[1].request_body['filters_digest'])
        self.assertEqual(audits[0].request_body['filters']['order_no_exact'], '***')
        self.assertEqual(audits[1].request_body['filters']['order_no_exact'], '***')
        self.assertNotIn(self.completed.order_no, str([audit.request_body for audit in audits]))

    def test_export_permission_is_independent_of_order_view_permission(self):
        response = self.export_only.get(self.url, {'order_no_exact': self.completed.order_no})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        listed = self.export_only.get('/api/admin/orders/')
        self.assertEqual(listed.status_code, 403, listed.data)

    def test_registered_route_exports_before_order_detail_routing(self):
        with override_settings(ROOT_URLCONF='config.urls'):
            response = self.exporter.get(self.url, {'order_no_exact': self.completed.order_no})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertIn('attachment;', response['Content-Disposition'])

    def test_export_permission_is_explicit_and_denial_is_audited(self):
        response = self.denied.get(self.url, {'customer_username': 'secret-boss'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Content-Disposition', response)
        audit = AdminAuditLog.objects.get(resource='order-export', status_code=403)
        self.assertEqual(audit.request_body['result'], 'PERMISSION_DENIED')
        self.assertEqual(audit.request_body['filters']['customer_username'], '***')
        self.assertNotIn('secret-boss', str(audit.request_body))

    def test_authentication_failures_return_401_without_denial_audit(self):
        for headers in ({}, {'HTTP_AUTHORIZATION': 'Bearer invalid-token'}):
            with self.subTest(headers=headers):
                client = APIClient(HTTP_HOST='admin.esports.club', **headers)
                response = client.get(self.url, {'customer_phone': '13800138000'})
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('Content-Disposition', response)
        self.assertFalse(AdminAuditLog.objects.filter(
            resource='order-export', status_code=403,
        ).exists())

    def test_authenticated_denials_are_throttled_before_more_audits_are_written(self):
        cache.clear()
        self.addCleanup(cache.clear)
        responses = [self.denied.get(self.url) for _ in range(21)]
        self.assertEqual([response.status_code for response in responses], [403] * 20 + [429])
        self.assertEqual(AdminAuditLog.objects.filter(
            resource='order-export', status_code=403,
        ).count(), 20)

    def test_authorized_exports_do_not_consume_denial_throttle(self):
        cache.clear()
        self.addCleanup(cache.clear)
        with patch('console.order_export_view.ORDER_EXPORT_DENIAL_RATE', '1/min'):
            responses = [self.exporter.get(self.url) for _ in range(3)]
        self.assertEqual([response.status_code for response in responses], [200, 200, 200])

    def test_invalid_filters_are_audited_and_return_no_file(self):
        response = self.exporter.get(self.url, {'order_kind': 'INVALID'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Content-Disposition', response)
        audit = AdminAuditLog.objects.get(resource='order-export', status_code=400)
        self.assertEqual(audit.request_body['result'], 'INVALID_FILTERS')

    def test_row_limit_is_enforced_before_serialization(self):
        with patch('console.order_export_view.MAX_ORDER_EXPORT_ROWS', 1):
            response = self.exporter.get(self.url)
        self.assertEqual(response.status_code, 413)
        audit = AdminAuditLog.objects.get(resource='order-export', status_code=413)
        self.assertEqual(audit.request_body['result'], 'ROW_LIMIT_EXCEEDED')
        self.assertEqual(audit.request_body['row_count'], 2)

    def test_successful_export_is_blocked_when_audit_cannot_be_written(self):
        with patch('console.order_export_view._record_export_audit', side_effect=RuntimeError('audit unavailable')):
            response = self.exporter.get(self.url)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('Content-Disposition', response)
        self.assertNotIn('Content-Disposition', response)
