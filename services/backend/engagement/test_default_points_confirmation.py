from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation, PointLedger
from engagement.services import config
from orders.tests import test_release_flow as flow


@override_settings(ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class DefaultPointsConfirmationTest(APITestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def prepare(self, *, payment_vip=True):
        operation = config()
        operation.vip_enabled = payment_vip
        operation.points_enabled = True
        operation.save(update_fields=['vip_enabled', 'points_enabled'])
        self.card.staff_account = self.accounts[self.admin.pk]
        self.card.save(update_fields=['staff_account'])
        order = self.new_order()
        report = self.finish(order)
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        return order

    def mature(self, order):
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            return process_order(order.pk)

    def test_awards_once_only_after_confirmation(self):
        order = self.prepare()
        self.assertTrue(self.mature(order))
        self.assertTrue(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 10000)
        self.assertFalse(self.mature(order))
        self.assertEqual(PointLedger.objects.filter(order=order, kind='CONSUMPTION').count(), 1)

    def test_disabled_lottery_pauses_usage_but_not_confirmed_consumption_award(self):
        import json
        from engagement.points_usage import lock_usage
        from stores.db import transaction
        order = self.prepare()
        with transaction.atomic():
            _, row, switches = lock_usage()
            switches['lottery_enabled'] = False
            row.value = json.dumps(switches)
            row.save(update_fields=['value'])
        self.assertTrue(self.mature(order))
        self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 10000)

    def test_disabled_at_payment_is_not_granted_after_reenable(self):
        order = self.prepare(payment_vip=False)
        operation = config(); operation.vip_enabled = True; operation.save(update_fields=['vip_enabled'])
        self.assertFalse(self.mature(order))
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())

    def test_disabled_at_confirmation_does_not_back_award(self):
        order = self.prepare()
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        self.assertFalse(self.mature(order))
        operation.vip_enabled = True; operation.save(update_fields=['vip_enabled'])
        self.assertFalse(self.mature(order))
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
