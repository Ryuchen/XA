"""Provider status reacts to real staff assignment, rejection and own status changes."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_RECHARGE_PROOF=True)
class ProviderVueMineAssignmentStatusTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_assignment_rejection_and_inflight_manual_status(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        self.ok(self.recharge())
        self.login(self.boss)
        orders = [self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'dispatch_route':'STAFF', 'support_contact_id':self.card.pk,
            'client_request_id':f'mine-assignment-status-{index}'}, format='json')) for index in range(2)]
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_realtime() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-mine-assignment-status-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'orders':[row['id'] for row in orders],
                    'id':self.accounts[self.provider.pk].pk, 'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'fixed':os.environ.get('XA_MINE_STATUS_FIXED') == '1'}),
                text=True, encoding='utf-8', capture_output=True, cwd=repo, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
