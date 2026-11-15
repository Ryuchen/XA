"""Run the real negotiated refund and operating report in two store databases."""
from datetime import datetime, time, timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from console.roi_period import period_revenue
from console.test_roi_payment_events import RoiPaymentEventsTest
from orders.models import Order
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], NEGOTIATION_PROPOSAL_ENABLED=True,
                   NEGOTIATION_REFUND_EXECUTION_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class RoiStoreIsolationTest(APITransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    ok = RoiPaymentEventsTest.ok

    def login(self, user):
        self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.staff.pk else self.store_host
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.tokens[user.pk])

    def run_store_journey(self, store, host):
        self.store_host = host
        with store_context(store):
            self.client = APIClient(HTTP_HOST=host)
            RoiPaymentEventsTest.setUp(self)
            RoiPaymentEventsTest.test_payment_and_negotiated_refund_are_reported_in_their_own_months(self)
            order_id = Order.objects.get().pk
            today = timezone.localdate()
            start = timezone.make_aware(datetime.combine(today, time.min))
            facts = period_revenue(start, start + timedelta(days=1))
            self.assertEqual((facts['paid_units'], facts['refunded_units'], facts['net_units']), (0, 2500, -2500))
            return order_id

    def test_refund_and_reporting_remain_inside_each_store(self):
        first, second = resolve_store(), xinan_store()
        first_id = self.run_store_journey(first, 'sl.esports.club')
        with store_context(second):
            now = timezone.now()
            empty = period_revenue(now - timedelta(days=1), now + timedelta(days=1))
            self.assertEqual((empty['paid_units'], empty['refunded_units'], empty['net_units']), (0, 0, 0))
        second_id = self.run_store_journey(second, 'xa.esports.club')
        self.assertEqual(first_id, second_id)
        with store_context(first):
            self.assertEqual(Order.objects.count(), 1)
            self.assertEqual(Order.objects.get(pk=first_id).refund_records.count(), 1)
        with store_context(second):
            self.assertEqual(Order.objects.count(), 1)
            self.assertEqual(Order.objects.get(pk=second_id).refund_records.count(), 1)
