"""The real order's frozen gameplay is visible before and after acceptance."""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Gameplay
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueGameplaySnapshotHTTPTest(LiveServerTestCase):
    def test_public_pool_and_assigned_order_keep_real_draw_and_rules(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires current Vue build and Node')
        boss = make_user()
        provider = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK', pass_expires_at=timezone.now()+timedelta(days=7))
        staff = make_console_user(['order:view','service:view','service:edit'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss,provider,staff)]
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss,balance=50000)
        game = Gameplay.objects.create(name='隔离转盘玩法',code='qa-vue-gameplay',kind='ROULETTE',rules='隔离原始规则')
        service = make_service(name='隔离转盘套餐',price=10000,gameplay=game,platform='MOBILE',security_level='CONFIDENTIAL',
            roulette_pool=[{'name':'隔离目标甲','description':'完成甲目标','weight':1}, {'name':'隔离目标乙','description':'完成乙目标','weight':1}])
        provider.escort_profile.service_items.add(service)
        clients=[APIClient() for _ in accounts]
        for client,account in zip(clients,accounts): client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        def ok(response):
            self.assertEqual(response.data['code'],0,response.data)
            return response.data['data']
        order=ok(clients[0].post('/api/orders/create/',{'service_id':service.pk,'dispatch_route':'PUBLIC',
            'dispatch_mode':'AUTO_GRAB','client_request_id':'vue-gameplay-snapshot'},format='json'))
        self.assertIn(order['roulette_result']['name'], ('隔离目标甲','隔离目标乙'))
        fulfillment=order['fulfillment']; rules=order['gameplay_snapshot']['rules']
        self.assertIn(order['roulette_result']['name'],fulfillment); self.assertEqual(rules,'隔离原始规则')
        pool=ok(clients[1].get('/api/orders/orders/',{'status':'pending'}))
        pool_order=next(row for row in pool if row['id']==order['id'])
        self.assertEqual((pool_order['fulfillment'],pool_order['gameplay_snapshot']['rules']),(fulfillment,rules))
        repo=Path(settings.BASE_DIR).parent.parent
        def browser(row,mode,account=None):
            account = account or accounts[1]
            run=subprocess.run(['node',str(repo/'scripts/provider-vue-gameplay-snapshot-375.cjs')],
                input=json.dumps({'root':root,'base':self.live_server_url,'orderId':row['id'],'orderNo':row['order_no'],
                    'token':issue_account_tokens(account)[0],'id':account.pk,'fulfillment':fulfillment if mode!='empty' else '',
                    'rules':rules if mode!='empty' else '', 'pool':mode=='pool'}),text=True,encoding='utf-8',capture_output=True,timeout=40,cwd=repo)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        browser(order,'pool')
        ok(clients[1].post(f"/api/orders/orders/{order['id']}/grab/",{},format='json'))
        changed=clients[2].patch(f'/api/admin/service-items/{service.pk}/',{'rules':'隔离已修改规则'},format='json')
        self.assertEqual(changed.status_code,200,changed.data)
        for client,path in ((clients[0],f"/api/orders/orders/{order['id']}/"),
                            (clients[1],f"/api/orders/orders/{order['id']}/"),
                            (clients[2],f"/api/admin/orders/{order['id']}/")):
            detail=ok(client.get(path))
            self.assertEqual(detail['gameplay_snapshot']['rules'],rules)
            self.assertEqual(detail['roulette_result'],order['roulette_result'])
        browser(order,'assigned')
        if os.environ.get('XA_GAMEPLAY_SNAPSHOT_NATIVE') == '1':
            native=subprocess.run(['node',str(repo/'scripts/native-gameplay-snapshot-iphone12.cjs')],
                input=json.dumps({'base':self.live_server_url,'orderId':order['id'],'bossId':accounts[0].pk,
                    'token':issue_account_tokens(accounts[0])[0], 'fulfillment':fulfillment,
                    'target':order['roulette_result']['name']}),text=True,encoding='utf-8',capture_output=True,timeout=40,cwd=repo)
            self.assertEqual(native.returncode,0,native.stdout+native.stderr)
        plain=make_service(name='隔离普通服务',price=10000)
        fresh=make_provider()
        fresh_account=get_or_create_account_for_legacy_user(fresh)
        fresh.escort_profile.account=fresh_account; fresh.escort_profile.save(update_fields=['account'])
        ordinary=ok(clients[0].post('/api/orders/create/',{'service_id':plain.pk,'provider_id':fresh_account.pk,
            'client_request_id':'vue-empty-gameplay'},format='json'))
        self.assertFalse(ordinary['gameplay_snapshot'])
        browser(ordinary,'empty',fresh_account)
