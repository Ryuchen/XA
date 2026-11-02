"""A confirmed team sees the real selection eligibility of the same order."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from orders.models import OrderApplication
from orders.tests import test_release_flow as flow
from orders.tests import test_team_invitation_detail as invitation_flow
from orders.tests.factories import make_service


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueApplicationReadinessHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    invite = invitation_flow.TeamInvitationDetailTest.invite

    def exercise_offline_member(self, offline_index):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login((self.provider, self.partner)[offline_index])
        self.ok(self.client.post('/api/users/escorts/status/', {'status': 'OFFLINE'}, format='json'))
        self.login(self.boss)
        boss = self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))
        row = next(item for item in boss['applications'] if item['id'] == self.application.pk)
        self.assertFalse(row['ready'])
        self.assertIn('离线', row['not_ready_reason'])
        self.login(self.admin)
        staff = self.ok(self.client.get(f'/api/admin/orders/{self.order.pk}/'))
        self.assertEqual(staff['status'], 'PENDING')
        self.login((self.partner, self.provider)[offline_index])
        pool = self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))
        own = next(item for item in pool if item['id'] == self.order.pk)['my_applications']
        self.assertTrue(any(item['id'] == self.application.pk and item['ready'] for item in own))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-application-readiness-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'orderId': self.order.pk,
                'orderNo': self.order.order_no, 'applicationId': self.application.pk,
                'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
                'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
                'offlineIndex': offline_index,
                'providers': [{'id': self.accounts[user.pk].pk, 'token': issue_account_tokens(self.accounts[user.pk])[0]}
                              for user in (self.provider, self.partner)],
                'expectFixed': os.environ.get('XA_READINESS_FIXED') == '1',
                'poolOnly': os.environ.get('XA_READINESS_POOL_ONLY') == '1'}),
            text=True, encoding='utf-8', capture_output=True, timeout=50, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())

    def test_confirmed_offline_partner_blocks_and_recovers_selection(self):
        self.exercise_offline_member(1)

    def test_confirmed_offline_applicant_blocks_and_recovers_selection(self):
        self.exercise_offline_member(0)

    def test_unconfirmed_invitation_hides_the_other_members_reason(self):
        self.invite()
        self.login(self.provider)
        self.ok(self.client.post('/api/users/escorts/status/', {'status': 'OFFLINE'}, format='json'))
        self.login(self.partner)
        detail = self.ok(self.client.get(self.path))
        self.assertFalse(detail['can_confirm'])
        self.assertIn('队友当前暂不符合接单条件', detail['confirm_reason'])
        self.assertNotIn('离线', detail['confirm_reason'])
        rejected = self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json')
        self.assertNotEqual(rejected.data['code'], 0)
        self.assertIn('队友当前暂不符合接单条件', rejected.data['msg'])
        self.assertNotIn('离线', rejected.data['msg'])

    def test_pool_entries_follow_actual_application_status_and_size(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        self.invite()
        repo = Path(settings.BASE_DIR).parent.parent
        def check(user, order, application, state, partner=False):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-application-readiness-375.cjs')],
                input=json.dumps({'root': root, 'base': self.live_server_url, 'orderId': order.pk,
                    'orderNo': order.order_no, 'applicationId': application.pk, 'poolState': state,
                    'isPartner': partner, 'provider': {'id': self.accounts[user.pk].pk,
                        'token': issue_account_tokens(self.accounts[user.pk])[0]}}),
                text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        check(self.provider, self.order, self.application, 'unconfirmed')
        check(self.partner, self.order, self.application, 'unconfirmed', True)
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        check(self.provider, self.order, self.application, 'withdrawn')
        service = make_service(price=200, billing_provider_count=1)
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': service.pk,
            'dispatch_mode': 'BOSS_SELECT', 'support_contact_id': self.card.pk,
            'client_request_id': 'single-pool-entry'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {'team_size': 1}, format='json'))
        application = OrderApplication.objects.select_related('order').get(order_id=order_id)
        check(self.provider, application.order, application, 'single')
