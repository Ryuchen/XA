"""Real completed orders remain navigable when a provider has many evaluations."""
import json
import os
from pathlib import Path
import subprocess
from contextlib import nullcontext

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from orders.tests import test_provider_aftersale_status as flow
from wallet.models import Wallet
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderEvaluationPaginationTest(LiveServerTestCase):
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    def test_old_order_review_deep_link_and_reply_with_21_real_orders(self):
        Wallet.objects.filter(user=self.boss).update(balance=500000)
        orders=[]; evaluations=[]
        for index in range(21):
            order_id, _ = self.prepare()
            orders.append(order_id)
            self.login(self.boss)
            result=self.ok(self.client.post('/api/orders/evaluate/', {'order_id':order_id,
                'provider_account_id':self.accounts[self.provider.pk].pk, 'score':5,
                'content':f'评价{index:02d}'},format='json'))
            evaluations.append(result['id'])
        self.login(self.provider)
        first=self.ok(self.client.get('/api/orders/evaluations/mine/', {'page':1,'page_size':20}))
        second=self.ok(self.client.get('/api/orders/evaluations/mine/', {'page':2,'page_size':20}))
        self.assertEqual((first['total'], first['pending_total'], len(first['list']),len(second['list'])),(21,21,20,1))
        self.assertEqual(second['list'][0]['id'], evaluations[0])
        focused=self.ok(self.client.get('/api/orders/evaluations/mine/', {'order_id':orders[0],'page':1}))
        self.assertEqual((focused['filtered_total'], focused['list'][0]['id'],focused['total']),(1,evaluations[0],21))
        self.assertEqual(len(self.ok(self.client.get('/api/orders/evaluations/mine/'))['list']),21)
        root=os.environ.get('XA_PROVIDER_VUE_ROOT')
        if root:
            role = self.staff.admin_membership.roles.first()
            role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:delete']
            role.save(update_fields=['permissions'])
            repo=Path(settings.BASE_DIR).parent.parent
            realtime = os.environ.get('XA_EVALUATION_PAGE_REALTIME') == '1'
            backend = isolated_redis_asgi() if realtime else nullcontext((self.live_server_url, None))
            with backend as (base, ws_base):
                result=subprocess.run(['node',str(repo/'scripts/provider-evaluation-pagination-live.cjs')],cwd=repo,
                    input=json.dumps({'root':root,'base':base,'wsBase':ws_base,'orderId':orders[0],
                        'reviewId':evaluations[0], 'bossToken':self.tokens[self.boss.pk],
                        'staffToken':self.tokens[self.staff.pk],
                        'id':self.accounts[self.provider.pk].pk,'token':self.tokens[self.provider.pk]}),
                    text=True,encoding='utf-8',capture_output=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.login(self.boss)
            detail=self.ok(self.client.get(f'/api/orders/orders/{orders[0]}/'))
            self.assertEqual(detail['evaluations'], [])
