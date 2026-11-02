"""Two providers compete for an actual order while one Vue workbench is stale."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueStaleGrabHTTPTest(LiveServerTestCase):
    def exercise(self, lost_response, *, hold_pool=False, sound=False, realtime=False):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        accounts = []
        for _ in range(2):
            provider = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                     pass_expires_at=timezone.now()+timedelta(days=1))
            account = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, account)
            provider.escort_profile.account = account
            provider.escort_profile.save(update_fields=['account'])
            accounts.append(account)
        service = make_service(price=10000, billing_provider_count=1)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'AUTO_GRAB', 'client_request_id': 'vue-stale-grab-live'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now()-timedelta(hours=1))
        repo = Path(settings.BASE_DIR).parent.parent
        def run(base, ws_base=None):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-stale-grab-375.cjs')], input=json.dumps({
                'root': root, 'base': base, 'wsBase': ws_base, 'orderId': order.pk, 'orderNo': order.order_no,
                'id': accounts[0].pk, 'token': issue_account_tokens(accounts[0])[0],
                'otherToken': issue_account_tokens(accounts[1])[0], 'lostResponse': lost_response,
                'holdPool': hold_pool, 'sound': sound, 'bossToken': issue_account_tokens(boss_account)[0],
                'serviceId': service.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
        if realtime:
            from orders.tests.test_provider_vue_team_progress_http import isolated_realtime
            with isolated_realtime() as (base, ws_base):
                run(base, ws_base)
        else:
            run(self.live_server_url)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id),
                         ('PENDING', None) if sound else ('GRABBED', accounts[0 if lost_response else 1].pk))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        detail = client.get(f'/api/orders/orders/{order.pk}/')
        self.assertEqual((detail.data['code'], detail.data['data']['status']), (0, 'PENDING' if sound else 'GRABBED'))
        if sound:
            wallet.refresh_from_db()
            self.assertEqual(wallet.balance, 20000)

    def test_competing_grab_updates_stale_workbench(self):
        self.exercise(False)

    def test_lost_grab_response_remains_uncertain_and_refreshes(self):
        self.exercise(True)

    def test_preexisting_inflight_pool_snapshot(self):
        self.exercise(False, hold_pool=True)

    def test_pool_sound_and_mute_after_new_orders(self):
        self.exercise(False, sound=True)

    def test_realtime_pool_sound_and_mute_after_new_orders(self):
        if os.environ.get('XA_STALE_GRAB_REALTIME') != '1':
            self.skipTest('requires isolated Redis and Daphne workers')
        self.exercise(False, sound=True, realtime=True)
