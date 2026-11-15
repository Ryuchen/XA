from common.fronted_test_support import ReactLiveHTTPTestCase, current_http, fronted_root
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
from users.test_native_checkin_order_realtime import isolated_redis_asgi as isolated_realtime


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueMineEvaluationRealtimeTest(ReactLiveHTTPTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def test_boss_review_and_staff_reply_refresh_open_profile(self):
        root = fronted_root('provider')
        if not root:
            self.skipTest('requires current React source')
        order = self.new_order()
        self.finish(order)
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:reply']
        role.save(update_fields=['permissions'])
        repo = Path(settings.BASE_DIR).parent.parent
        with current_http(self, isolated_realtime) as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-mine-evaluation-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'orderId':order.pk,
                    'id':self.accounts[self.provider.pk].pk, 'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'fixed':True}),
                text=True, encoding='utf-8', capture_output=True, cwd=repo, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())

    def test_staff_reply_after_full_refund_refreshes_open_profile(self):
        self.verify_reply_after_refund(evaluation_page=False)

    def test_staff_reply_after_full_refund_refreshes_open_evaluations(self):
        self.verify_reply_after_refund(evaluation_page=True)

    def verify_reply_after_refund(self, *, evaluation_page):
        root = fronted_root('provider')
        if not root:
            self.skipTest('requires current React source')
        order = self.new_order()
        self.finish(order)
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:reply', 'order:refund']
        role.save(update_fields=['permissions'])
        repo = Path(settings.BASE_DIR).parent.parent
        with current_http(self, isolated_realtime) as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-mine-evaluation-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'orderId':order.pk,
                    'id':self.accounts[self.provider.pk].pk, 'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'fixed':True, 'refundBeforeReply':True, 'evaluationPage':evaluation_page}),
                text=True, encoding='utf-8', capture_output=True, cwd=repo, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
