"""An open provider profile follows real order-review events."""
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


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueMineEvaluationRealtimeTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def test_boss_review_and_staff_reply_refresh_open_profile(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        order = self.new_order()
        self.finish(order)
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:reply']
        role.save(update_fields=['permissions'])
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_realtime() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-mine-evaluation-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'orderId':order.pk,
                    'id':self.accounts[self.provider.pk].pk, 'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'fixed':os.environ.get('XA_MINE_EVALUATION_FIXED') == '1'}),
                text=True, encoding='utf-8', capture_output=True, cwd=repo, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
