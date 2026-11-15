"""The first-release default supports a complete points pass purchase."""
from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITestCase

from users import test_pass_points_payment as fixture


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class DefaultPassPointsPaymentTest(APITestCase):
    setUp = fixture.PassPointsPaymentTests.setUp
    quote = fixture.PassPointsPaymentTests.quote
    buy = fixture.PassPointsPaymentTests.buy

    def test_default_purchase_updates_pass_wallet_and_points_once(self):
        self.assertTrue(settings.PROVIDER_PASS_POINTS_PAYMENT_ENABLED)
        fixture.PassPointsPaymentTests.test_purchase_and_idempotent_retry_preserve_both_ledgers(self)
