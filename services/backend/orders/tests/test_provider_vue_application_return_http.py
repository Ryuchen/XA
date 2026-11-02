"""Actual historical application notice through re-open and cancellation."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import OrderApplication
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueApplicationReturnTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_historical_application_tracks_reopened_then_cancelled_order(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        assigned = make_provider(display_name='隔离客服指派陪玩')
        self.accounts[assigned.pk] = get_or_create_account_for_legacy_user(assigned)
        for user in (self.provider, assigned):
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'dispatch_mode':'BOSS_SELECT', 'support_contact_id':self.card.pk,
            'client_request_id':'application-history-return'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/grab/", {}, format='json'))
        app = OrderApplication.objects.get(order_id=order['id'], applicant=self.accounts[self.provider.pk])
        self.login(self.admin)
        self.ok(self.client.post(f"/api/admin/orders/{order['id']}/assign/", {
            'provider_id':self.accounts[assigned.pk].pk}, format='json'))
        self.login(self.provider)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        notice = next(item for item in messages if item['title']=='报名结果已更新' and item['related_order_id']==order['id'])
        self.assertEqual(notice['action_url'], f'/pages/orders/index?application_id={app.pk}')
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_realtime() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-application-return-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'orderId':order['id'],
                    'orderNo':order['order_no'], 'applicationId':app.pk, 'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'id':self.accounts[self.provider.pk].pk, 'assignedToken':issue_account_tokens(self.accounts[assigned.pk])[0],
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'fixed':os.environ.get('XA_APPLICATION_RETURN_FIXED') == '1'}),
                text=True, encoding='utf-8', capture_output=True, timeout=70, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
