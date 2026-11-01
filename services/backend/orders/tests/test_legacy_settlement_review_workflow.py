"""Three-end status reflects a legacy order that cannot mature automatically."""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from engagement.confirmation import process_order
from orders.models import Order
from orders.tests import test_provider_aftersale_status as flow
from site_messages.models import Message
from wallet.models import ProviderReport, Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class LegacySettlementReviewWorkflowTest(APITransactionTestCase):
    setUp = flow.ProviderAfterSaleStatusTest.setUp
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok

    def awaiting_report(self, *, legacy):
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'late-report-legacy' if legacy else 'late-report-normal',
        }, format='json'))['id']
        if legacy:
            # Historical orders may predate the customer business-account snapshot.
            Order.objects.filter(pk=order_id).update(customer_account=None)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        return order_id, report.pk

    def test_late_approval_keeps_legacy_order_for_review(self):
        order_id, report_id = self.awaiting_report(legacy=True)
        self.login(self.staff)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.ok(self.client.post(f'/api/admin/reports/{report_id}/approve/', {}, format='json'))
            self.assertFalse(process_order(order_id))
            order = self.ok(self.client.get(f'/api/admin/orders/{order_id}/'))
        self.assertEqual(order['settlement_status'], 'REVIEW')
        self.assertIn('核对订单归属和付款记录', order['settlement_review_reason'])
        self.assertEqual(ProviderReport.objects.get(pk=report_id).status, 'APPROVED')
        self.assertIsNone(Order.objects.get(pk=order_id).settled_at)
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())

    def test_late_approval_still_settles_normal_order_once(self):
        order_id, report_id = self.awaiting_report(legacy=False)
        self.login(self.staff)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.ok(self.client.post(f'/api/admin/reports/{report_id}/approve/', {}, format='json'))
            process_order(order_id)
            process_order(order_id)
            order = self.ok(self.client.get(f'/api/admin/orders/{order_id}/'))
        self.assertEqual(order['settlement_status'], 'SETTLED')
        self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').count(), 1)

    def test_approved_legacy_report_is_not_promised_automatic_settlement(self):
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'legacy-settlement-clarity',
        }, format='json'))['id']
        # Reproduce an order predating the business-account snapshot.
        Order.objects.filter(pk=order_id).update(customer_account=None)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        before = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual(before['settlement_status'], 'REVIEW')
        self.assertIn('缺少老板业务账号', before['settlement_review_reason'])
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'transaction:view']
        role.save(update_fields=['permissions'])
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        admin_order = self.ok(self.client.get(f'/api/admin/orders/{order_id}/'))
        self.assertEqual(admin_order['settlement_status'], 'REVIEW')
        self.assertIn('核对订单归属和付款记录', admin_order['settlement_review_reason'])
        rows = self.ok(self.client.get('/api/admin/transactions/', {'order_no_exact': admin_order['order_no']}))
        self.assertEqual(len(rows['list']), 1)
        self.assertEqual((rows['list'][0]['order_no'], rows['list'][0]['tx_type']), (admin_order['order_no'], 'PAY'))
        self.assertEqual(self.ok(self.client.get('/api/admin/transactions/', {'order_no_exact': '不存在'}))['total'], 0)
        self.login(self.provider)
        reports = self.ok(self.client.get('/api/wallet/reports/'))
        own = next(item for item in reports if item['id'] == report.pk)
        self.assertEqual(own['settlement_status'], 'REVIEW')
        order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(order['settlement_status'], 'REVIEW')
        notice = Message.objects.get(recipient_account=self.accounts[self.provider.pk], title='报单审核通过', related_order_id=order_id)
        self.assertIn('收益待核对', notice.preview)
        self.assertNotIn('自动结算', notice.preview)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertFalse(process_order(order_id))
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())

    def test_already_settled_legacy_order_has_no_unsettled_reason(self):
        order_id, report_id = flow.ProviderAfterSaleStatusTest.prepare(self)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
        Order.objects.filter(pk=order_id).update(customer_account=None)
        self.login(self.staff)
        order = self.ok(self.client.get(f'/api/admin/orders/{order_id}/'))
        report = self.ok(self.client.get(f'/api/admin/reports/{report_id}/'))
        self.assertEqual((order['settlement_review_reason'], report['settlement_review_reason']), ('', ''))
        self.assertEqual(order['settlement_status'], 'SETTLED')
