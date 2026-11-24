from datetime import timedelta
import os
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from engagement.models import ConsumptionConfirmation, LicenseConsumptionPointsDecision, PointLedger
from orders.models import Order
from orders.tests import test_service_funding_api as funding_fixture
from stores.context import store_context
from stores import test_license_consumption_points as fixture


@override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
                   LICENSE_NOTICE_DISPATCH_ENABLED=False, LICENSE_SNAPSHOT_CACHE_ENABLED=False,
                   PLATFORM_IDENTITY_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ALLOWED_HOSTS=['*'])
class DefaultPointsLicenseTest(TestCase):
    databases = {'default', 'store_b'}
    setUp = fixture.LicenseConsumptionPointsTests.setUp
    payload = funding_fixture.ServiceFundingApiTest.payload
    mature = funding_fixture.ServiceFundingApiTest.mature
    end_license_days_ago = fixture.LicenseConsumptionPointsTests.end_license_days_ago

    def paid_order(self):
        result = self.boss.post('/api/orders/create/', self.payload(), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        return Order.objects.get(pk=result.data['data']['id'])

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License 实销确认积分（确认时 License 状态与正式积分结算能力）属正式契约深水区（18-盘点 消费积分补充待联调）；最终联调启用')
    def test_expired_at_confirmation_does_not_back_award_after_renewal(self):
        with store_context(self.second):
            order = self.paid_order()
            self.end_license_days_ago(8)
            self.assertTrue(self.mature(order))
            self.assertTrue(ConsumptionConfirmation.objects.filter(order=order).exists())
            self.assertEqual(LicenseConsumptionPointsDecision.objects.get(order=order).outcome, 'DENY')
            self.license.ends_at = timezone.now() + timedelta(days=30)
            self.license.save(update_fields=['ends_at'])
            self.assertFalse(self.mature_again(order))
            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License 实销确认积分（确认时 License 状态与正式积分结算能力）属正式契约深水区（18-盘点 消费积分补充待联调）；最终联调启用')
    def test_unavailable_license_creates_reviewable_evidence(self):
        with store_context(self.second):
            order = self.paid_order()
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated outage')):
                self.assertTrue(self.mature(order))
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
            self.assertEqual(decision.outcome, 'UNKNOWN')
            from engagement.license_points_review import _principal
            order.refresh_from_db()
            self.assertEqual(_principal(order, decision)[:2], (1000, 0))
            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())

    def mature_again(self, order):
        from engagement.confirmation import process_order
        return process_order(order.pk)
