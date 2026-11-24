"""Real completed orders remain navigable when a provider has many evaluations."""
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from orders.tests import test_provider_aftersale_status as flow
from wallet.models import Wallet


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
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:delete']
        role.save(update_fields=['permissions'])
        self.login(self.provider)
        replied = self.ok(self.client.post(f'/api/orders/evaluations/{evaluations[0]}/reply/',
            {'reply_content': '感谢您的评价', 'reply_version': 0}, format='json'))
        self.assertEqual(replied['reply_content'], '感谢您的评价')
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{orders[0]}/'))
        self.assertEqual(detail['evaluations'][0]['reply_content'], '感谢您的评价')
        self.login(self.provider)
        self.assertEqual(self.ok(self.client.get('/api/orders/evaluations/mine/', {'page':1,'page_size':20}))['pending_total'], 20)
        self.login(self.staff)
        removed = self.client.delete(f'/api/admin/evaluations/{evaluations[0]}/', {'reply_version': 1}, format='json')
        self.assertEqual(removed.data['code'], 0, removed.data)
        self.login(self.provider)
        after = self.ok(self.client.get('/api/orders/evaluations/mine/', {'page':1,'page_size':20}))
        self.assertEqual((after['total'], after['pending_total'], len(after['list'])), (20, 20, 20))
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{orders[0]}/'))
        self.assertEqual(detail['evaluations'], [])
