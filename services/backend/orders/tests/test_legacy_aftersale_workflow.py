"""Historical orders retain the same customer access across order and aftersale APIs."""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from engagement.confirmation import process_order
from orders.tests.factories import make_user
from orders.tests import test_provider_aftersale_status as workflow


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class LegacyAfterSaleWorkflowTest(APITransactionTestCase):
    setUp = workflow.ProviderAfterSaleStatusTest.setUp
    login = workflow.ProviderAfterSaleStatusTest.login
    ok = workflow.ProviderAfterSaleStatusTest.ok
    prepare = workflow.ProviderAfterSaleStatusTest.prepare
    resolve = workflow.ProviderAfterSaleStatusTest.resolve
    provider_state = workflow.ProviderAfterSaleStatusTest.provider_state

    def test_legacy_customer_can_open_and_resolve_aftersale_after_real_service(self):
        order_id, report_id = self.prepare()
        # Simulate an older order predating business-account snapshots.
        Order.objects.filter(pk=order_id).update(customer_account=None)
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertTrue(detail['can_aftersale'])
        self.assertIsNone(self.ok(self.client.get(f'/api/orders/orders/{order_id}/aftersale/')))
        case = self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '历史订单服务咨询', 'client_request_id': 'legacy-service-question',
        }, format='json'))
        self.assertEqual(case['status'], 'OPEN')
        self.provider_state(order_id, report_id, 'OPEN', False)
        self.login(self.staff)
        queue = self.ok(self.client.get('/api/admin/orders/', {'aftersale_open': '1'}))
        self.assertIn(order_id, [row['id'] for row in queue['list']])
        self.resolve(case['id'])
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/aftersale/'))['status'], 'RESOLVED')
        # Existing confirmation rules leave unmigrated customer accounts for
        # reconciliation; this change does not invent a financial owner.
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertFalse(process_order(order_id))
        self.provider_state(order_id, report_id, 'RESOLVED', False)

    def test_explicit_customer_account_takes_precedence_over_old_user(self):
        order_id, _ = self.prepare()
        new_owner = make_user()
        new_account = get_or_create_account_for_legacy_user(new_owner)
        Order.objects.filter(pk=order_id).update(customer_account=new_account)
        self.login(self.boss)
        self.assertEqual(self.client.get(f'/api/orders/orders/{order_id}/aftersale/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '无权访问的旧账号', 'client_request_id': 'old-owner',
        }, format='json').status_code, 404)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(new_account)[0])
        self.assertEqual(self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '当前老板咨询', 'client_request_id': 'current-owner',
        }, format='json'))['status'], 'OPEN')
