"""The outgoing and incoming providers see their real handoff in Vue."""
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
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueTransferHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def exercise_transfer(self, settled, realtime=False):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires current Vue build and Node')
        teammate, incoming = make_provider(display_name='留队陪玩'), make_provider(display_name='转入陪玩')
        for user in (teammate, incoming):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            user.escort_profile.account = self.accounts[user.pk]
            user.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk],
            'client_request_id': 'vue-transfer-receipt'}, format='json'))
        for user in (self.provider, teammate):
            self.login(user)
            self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.screenshot()}, format='multipart'))
        if not realtime:
            self.login(self.admin)
            self.ok(self.client.post(f"/api/admin/orders/{order['id']}/transfer/", {'transfers': [
                {'old_provider_id': self.accounts[self.provider.pk].pk, 'new_provider_id': self.accounts[incoming.pk].pk,
                 'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}], 'reason': '隔离转单核对'}, format='json'))
        repo = Path(settings.BASE_DIR).parent.parent
        providers = [{'token': issue_account_tokens(self.accounts[user.pk])[0], 'id': self.accounts[user.pk].pk} for user in (self.provider, incoming, teammate)]
        def exercise(base, ws_base=None):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-transfer-375.cjs')],
                input=json.dumps({'root': root, 'base': base, 'wsBase': ws_base, 'realtime': realtime,
                    'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0], 'orderId': order['id'],
                    'orderNo': order['order_no'], 'providers': providers, 'settled': settled}),
                text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if realtime:
            with isolated_realtime() as (base, ws_base): exercise(base, ws_base)
        else: exercise(self.live_server_url)
        for user in (self.boss, self.provider, teammate, incoming, self.admin):
            self.login(user)
            prefix = '/api/admin/orders/' if user == self.admin else '/api/orders/orders/'
            detail = self.ok(self.client.get(f"{prefix}{order['id']}/"))
            participants = ({row['provider'] for row in detail['providers'] if row['released_at'] is None}
                            if user == self.admin else {row['id'] for row in detail['participants']})
            self.assertEqual(participants, {self.accounts[teammate.pk].pk, self.accounts[incoming.pk].pk})
            if user in (self.provider, teammate, incoming):
                self.assertEqual(detail['my_assignment_released'], user == self.provider)
                self.assertEqual(detail['my_assignment_settled'], settled and user == self.provider)

    def test_report_approval_transfer(self):
        self.exercise_transfer(False)

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_completion_mode_transfer(self):
        self.exercise_transfer(True)

    def test_realtime_transfer(self):
        if os.environ.get('XA_TEAM_PROGRESS_REALTIME') != '1':
            self.skipTest('Requires isolated Redis and two Daphne workers')
        self.exercise_transfer(False, realtime=True)
