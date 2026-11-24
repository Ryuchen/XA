"""Boss cancellation and real isolated API agree about a points cancellation."""
import os
from datetime import timedelta
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberProfile, PointLedger
from orders.models import Order, OrderPointsRefund, OrderRefund
from orders.tests import test_points_service_refund as refund_fixture
from wallet.models import Transaction, Wallet


@skipUnless(os.environ.get('XA_SERVICE_POINTS_CANCEL_BROWSER') == '1', 'Requires isolated ASGI and boss frontend')
@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_CANCEL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PointsServiceCancelBrowserTest(LiveServerTestCase):
    def test_owner_sees_and_recovers_full_original_assets(self):
        refund_fixture.ServicePointsRefundTests.setUp(self)
        Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=timezone.now() + timedelta(minutes=10))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        cancelled = client.post(f'/api/orders/orders/{self.order.pk}/cancel/', {'reason': '隔离业务验收'}, format='json')
        self.assertEqual((cancelled.status_code, cancelled.data['code']), (200, 0), cancelled.data)
        detail = client.get(f'/api/orders/orders/{self.order.pk}/')
        self.assertEqual((detail.status_code, detail.data['code']), (200, 0), detail.data)
        self.assertEqual(detail.data['data']['status'], 'CANCELLED')
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(account=self.account).balance, 2000)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 15000)
        self.assertEqual(OrderRefund.objects.filter(order=self.order).count(), 1)
        self.assertEqual(OrderPointsRefund.objects.filter(refund__order=self.order).count(), 1)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='REFUND').count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)
