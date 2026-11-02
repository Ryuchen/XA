"""The pool UI faces a real response reset after a committed grab."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueGrabRetryHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def exercise(self, changed=False):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        profile = self.provider.escort_profile
        profile.pass_tier = 'BLACK'
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        other = make_provider(display_name='重新接单陪玩')
        self.accounts[other.pk] = get_or_create_account_for_legacy_user(other)
        other_profile = other.escort_profile
        other_profile.account = self.accounts[other.pk]
        other_profile.pass_tier = 'BLACK'
        other_profile.pass_expires_at = timezone.now() + timedelta(days=1)
        other_profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'dispatch_route': 'PUBLIC', 'dispatch_mode': 'AUTO_GRAB', 'client_request_id': 'grab-reset-test'}, format='json'))
        Order.objects.filter(pk=data['id']).update(pool_entered_at=timezone.now()-timedelta(hours=1))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-grab-retry-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'orderId': data['id'],
                'orderNo': data['order_no'], 'id': self.accounts[self.provider.pk].pk,
                'token': issue_account_tokens(self.accounts[self.provider.pk])[0],
                'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
                'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
                'otherToken': issue_account_tokens(self.accounts[other.pk])[0], 'changed': changed,
                'fixed': os.environ.get('XA_GRAB_RECEIPT_FIXED') == '1'}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        order = Order.objects.get(pk=data['id'])
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', self.accounts[(other if changed else self.provider).pk].pk))

    def test_grab_response_reset(self):
        self.exercise()

    def test_delayed_replay_after_reassignment(self):
        if os.environ.get('XA_GRAB_RECEIPT_FIXED') != '1':
            self.skipTest('requires updated grab receipt')
        self.exercise(changed=True)
