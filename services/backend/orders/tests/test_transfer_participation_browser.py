"""Exercise the real transfer-entry API against an isolated SQLite store."""
import os
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings

from club_accounts.tokens import issue_account_tokens
from orders.tests import test_prestart_handoff as fixture
from orders.tests import test_release_flow as flow


@skipUnless(os.environ.get('XA_TRANSFER_ENTRY_BROWSER') == '1', 'Requires isolated ASGI and the managed local gateway')
@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class TransferParticipationBrowserTest(LiveServerTestCase):
    login = fixture.PrestartHandoffTest.login
    ok = fixture.PrestartHandoffTest.ok
    recharge = fixture.PrestartHandoffTest.recharge
    new_order = fixture.PrestartHandoffTest.new_order
    replacement = fixture.PrestartHandoffTest.replacement
    transfer = fixture.PrestartHandoffTest.transfer

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        self.client = APIClient()
        fixture.PrestartHandoffTest.setUp(self)

    def test_incoming_provider_confirms_entry_from_real_mobile_page(self):
        from orders.models import OrderParticipationSegment
        with TemporaryDirectory(prefix='xa-asgi-media-') as media, override_settings(MEDIA_ROOT=media):
            replacement = self.replacement()
            order = self.new_order()
            self.login(self.provider)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':flow.screenshot()}, format='multipart'))
            self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
                'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}))
            segment = OrderParticipationSegment.objects.get(attribution__order=order,
                provider_account=self.accounts[replacement.pk], released_at__isnull=True)
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.accounts[replacement.pk])[0])
            started = self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': flow.screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            segment.refresh_from_db()
            self.assertEqual(segment.start_source, 'TRANSFER_ENTRY')
            self.assertIsNotNone(segment.started_at)
            self.assertTrue(segment.entry_image.storage.exists(segment.entry_image.name))
