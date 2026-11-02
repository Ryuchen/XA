"""An actual gift updates an open paginated wallet through isolated Redis."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime
from orders.tests.test_release_flow import screenshot


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_RECHARGE_PROOF=True)
class ProviderVueGiftPageRealtimeTest(TransactionTestCase):
    def test_gift_update_keeps_loaded_history_and_new_gift(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root: self.skipTest('requires isolated provider Vue build')
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        with tempfile.TemporaryDirectory(prefix='xa-gift-pages-') as media, override_settings(MEDIA_ROOT=media):
            boss, provider = make_user(), make_provider(display_name='隔离礼物陪玩')
            staff = make_console_user(['wallet:recharge', 'order:view', 'escort:dispose'])
            accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
            for user, account in zip((boss, provider, staff), accounts): link_legacy_relations(user, account)
            provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
            staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
            gift = make_service(name='隔离分页礼物', price=1000, commission_rate=20,
                                service_category=make_service_category(is_gift=True))
            clients = [APIClient(), APIClient(), APIClient()]
            tokens = [issue_account_tokens(account)[0] for account in accounts]
            for client, token in zip(clients, tokens): client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            def ok(response):
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['code'], 0, response.data)
                return response.data['data']
            ok(clients[2].post('/api/admin/wallets/recharge/', {'account_id':accounts[0].pk, 'amount':50000,
                'gift_amount':0, 'trade_no':'gift-pages-recharge', 'proof_image':screenshot(),
                'client_request_id':'gift-pages-recharge'}, format='multipart'))
            gifts = []
            for index in range(21):
                gifts.append(ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id':gift.pk,
                    'provider_id':accounts[1].pk, 'quantity':1, 'expected_amount':1000,
                    'client_request_id':f'gift-pages-{index:03}'}, format='json')))
            for index in range(30):
                ok(clients[2].post(f'/api/admin/escorts/{provider.escort_profile.pk}/dispose/', {'dispose_type':'REWARD',
                    'amount':10, 'reason':f'隔离奖励 {index:02}', 'client_request_id':f'gift-pages-reward-{index:03}'}, format='json'))
            initial = ok(clients[1].get('/api/wallet/provider-benefits/'))
            self.assertEqual((initial['pagination']['gift']['total'], len(initial['gift_records'])), (21, 20))
            self.assertEqual((initial['pagination']['dispose']['total'], len(initial['dispose_records'])), (30, 20))
            repo = Path(settings.BASE_DIR).parent.parent
            with isolated_realtime() as (base, ws_base):
                result = subprocess.run(['node', str(repo / 'scripts/provider-vue-gift-page-realtime-375.cjs')],
                    input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'token':tokens[1], 'id':accounts[1].pk,
                        'bossToken':tokens[0], 'giftId':gift.pk, 'oldest':gifts[0]['order_no'],
                        'fixed':os.environ.get('XA_GIFT_PAGE_FIXED') == '1', 'concurrent':os.environ.get('XA_GIFT_PAGE_CONCURRENT') == '1'}),
                    text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(result.stdout.strip())
            records = ok(clients[1].get('/api/wallet/provider-benefits/', {'page_size':100}))
            self.assertEqual(records['pagination']['gift']['total'], 22)
            self.assertEqual(len({row['id'] for row in records['gift_records']}), 22)
            recent = records['gift_records'][0]
            self.assertEqual(ok(clients[0].get(f"/api/orders/orders/{recent['id']}/"))['status'], 'COMPLETED')
            self.assertEqual(ok(clients[2].get(f"/api/admin/orders/{recent['id']}/"))['status'], 'COMPLETED')
