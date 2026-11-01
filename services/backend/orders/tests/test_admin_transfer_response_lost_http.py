"""Actual staff page controller handles an already committed handoff response loss."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import OrderProvider
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class TransferResponseLossHTTPTest(LiveServerTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_staff_lost_response_recovers_real_handoff_for_all_roles(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        self.client = APIClient()
        incoming = make_provider(display_name='隔离转入陪玩')
        self.accounts[incoming.pk] = get_or_create_account_for_legacy_user(incoming)
        incoming.escort_profile.account = self.accounts[incoming.pk]
        incoming.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'transfer-lost-real-http',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot()}, format='multipart'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-transfer-lost-live.cjs')], input=json.dumps({
            'base': self.live_server_url, 'order': order_id, 'staffId': self.accounts[self.admin.pk].pk,
            'incoming': self.accounts[incoming.pk].pk, 'old': self.accounts[self.provider.pk].pk,
            'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
            'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
            'oldToken': issue_account_tokens(self.accounts[self.provider.pk])[0],
            'newToken': issue_account_tokens(self.accounts[incoming.pk])[0],
        }), text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo,
            env={**os.environ, 'TZ': 'Asia/Shanghai'})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(OrderProvider.objects.filter(order_id=order_id).count(), 2)
        self.assertEqual(OrderProvider.objects.filter(order_id=order_id, released_at__isnull=True).count(), 1)
        old = OrderProvider.objects.get(order_id=order_id, provider_account=self.accounts[self.provider.pk])
        new = OrderProvider.objects.get(order_id=order_id, provider_account=self.accounts[incoming.pk])
        self.assertIsNotNone(old.released_at)
        self.assertEqual((old.provider_income, new.provider_income), (3200, 4800))
