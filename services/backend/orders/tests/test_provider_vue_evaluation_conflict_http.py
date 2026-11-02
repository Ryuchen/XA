"""A staff reply cannot erase a provider's concurrent evaluation draft."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from orders.tests import test_provider_aftersale_status as flow


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueEvaluationConflictHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp(); self.client = APIClient(); flow.ProviderAfterSaleStatusTest.setUp(self)
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare

    def exercise(self, lost=False):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root: self.skipTest('requires isolated provider Vue build')
        order_id, _ = self.prepare()
        self.login(self.boss)
        review = self.ok(self.client.post('/api/orders/evaluate/', {'order_id':order_id,
            'provider_account_id':self.accounts[self.provider.pk].pk, 'score':5, 'content':'隔离真实评价'}, format='json'))
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:reply']; role.save(update_fields=['permissions'])
        repo = Path(settings.BASE_DIR).parent.parent
        fixed = os.environ.get('XA_EVALUATION_CONFLICT_FIXED') == '1'
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-evaluation-conflict-375.cjs')],
            input=json.dumps({'root':root, 'base':self.live_server_url, 'providerToken':self.tokens[self.provider.pk],
                'providerId':self.accounts[self.provider.pk].pk, 'staffToken':self.tokens[self.staff.pk],
                'reviewId':review['id'], 'fixed':fixed, 'lost':lost}),
            text=True, encoding='utf-8', capture_output=True, timeout=50, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        self.login(self.boss)
        latest = next(row for row in self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['evaluations'] if row['id']==review['id'])
        self.login(self.staff)
        staff = self.ok(self.client.get(f"/api/admin/evaluations/{review['id']}/"))
        expected = '陪玩原有草稿' if fixed else '客服先行回复'
        self.assertEqual((latest['reply_content'], staff['reply_content']), (expected, expected))
        self.assertEqual(staff['reply_version'], 1 if lost or not fixed else 2)

    def test_staff_wins_race_then_provider_reviews_and_chooses(self): self.exercise()
    def test_lost_response_can_be_checked_without_resubmission(self):
        if os.environ.get('XA_EVALUATION_CONFLICT_FIXED') != '1': self.skipTest('requires updated page')
        self.exercise(lost=True)
