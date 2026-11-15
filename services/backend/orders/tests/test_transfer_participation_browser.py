"""Exercise the real provider page against an isolated ASGI and SQLite store."""
import base64
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.tokens import issue_account_tokens
from orders.tests import test_prestart_handoff as fixture
from orders.tests import test_release_flow as flow
from wallet.test_deposit_realtime_workflow import single_asgi


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
            image = base64.b64encode(flow.screenshot().read()).decode('ascii')
            with patch.dict(os.environ, {'XA_ASGI_NETWORK_TEST_MEDIA_ROOT':media}), single_asgi() as (api, _):
                root = Path(settings.BASE_DIR).parent.parent
                result = subprocess.run(['node', str(root / 'scripts/provider-transfer-entry-browser.cjs')], cwd=root,
                    input=json.dumps({'api':api, 'order':order.pk, 'image':image,
                        'provider':{'id':self.accounts[replacement.pk].pk,
                                    'token':issue_account_tokens(self.accounts[replacement.pk])[0]}}),
                    text=True, encoding='utf-8', capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            segment.refresh_from_db()
            self.assertEqual(segment.start_source, 'TRANSFER_ENTRY')
            self.assertIsNotNone(segment.started_at)
            self.assertTrue(segment.entry_image.storage.exists(segment.entry_image.name))
