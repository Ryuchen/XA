"""Boss order details survive staff assignment into the provider's current Vue UI."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from datetime import timedelta
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueGameContextHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_boss_input_staff_assignment_provider_view(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires current Vue build and Node')
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'dispatch_route': 'STAFF',
            'game_region': '隔离大区', 'game_nickname': '隔离游戏昵称', 'game_uid': 'ISOLATED-UID-123',
            'client_request_id': 'vue-game-context'}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f"/api/admin/orders/{order['id']}/assign/", {'provider_id': self.accounts[self.provider.pk].pk}, format='json'))
        admin = self.ok(self.client.get(f"/api/admin/orders/{order['id']}/"))
        self.assertEqual((admin['game_region'], admin['game_nickname'], admin['game_uid']), ('隔离大区', '隔离游戏昵称', 'ISOLATED-UID-123'))
        candidate = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK', pass_expires_at=timezone.now() + timedelta(days=7))
        candidate_account = get_or_create_account_for_legacy_user(candidate)
        candidate.escort_profile.account = candidate_account
        candidate.escort_profile.save(update_fields=['account'])
        candidate.escort_profile.service_items.add(self.service)
        self.login(self.boss)
        pool = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'AUTO_GRAB', 'game_region': '隔离大区', 'game_nickname': '池中隔离昵称',
            'game_uid': 'POOL-ISOLATED-UID', 'client_request_id': 'vue-game-pool'}, format='json'))
        self.assertEqual((pool['game_region'], pool['game_nickname'], pool['game_uid']), ('隔离大区', '池中隔离昵称', 'POOL-ISOLATED-UID'))
        self.login(self.admin)
        pool_admin = self.ok(self.client.get(f"/api/admin/orders/{pool['id']}/"))
        self.assertEqual((pool_admin['game_region'], pool_admin['game_nickname'], pool_admin['game_uid']), ('隔离大区', '池中隔离昵称', 'POOL-ISOLATED-UID'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-game-context-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'orderId': order['id'], 'orderNo': order['order_no'],
                'token': issue_account_tokens(self.accounts[self.provider.pk])[0], 'id': self.accounts[self.provider.pk].pk,
                'poolToken': issue_account_tokens(candidate_account)[0], 'poolId': candidate_account.pk, 'poolOrderNo': pool['order_no'], 'poolOrderId': pool['id']}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(candidate_account)[0])
        claimed = self.ok(self.client.get(f"/api/orders/orders/{pool['id']}/"))
        self.assertEqual((claimed['status'], claimed['game_region'], claimed['game_nickname'], claimed['game_uid']),
                         ('GRABBED', '隔离大区', '池中隔离昵称', 'POOL-ISOLATED-UID'))
        if os.environ.get('XA_GAME_CONTEXT_NATIVE') == '1':
            native = subprocess.run(['node', str(repo / 'scripts/native-game-context-iphone12.cjs')],
                input=json.dumps({'base':self.live_server_url, 'orderId':pool['id'], 'bossId':self.accounts[self.boss.pk].pk,
                    'token':issue_account_tokens(self.accounts[self.boss.pk])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
            self.assertEqual(native.returncode,0,native.stdout+native.stderr)
