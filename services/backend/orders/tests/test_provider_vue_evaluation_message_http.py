"""A real review notice opens its own review and keeps the order link."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests import test_provider_aftersale_status as flow
from orders.tests.factories import make_provider


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueEvaluationMessageHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare

    def test_review_notice_and_reply(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        other_id, _ = self.prepare()
        self.login(self.boss)
        self.ok(self.client.post('/api/orders/evaluate/', {
            'order_id': other_id, 'provider_account_id': self.accounts[self.provider.pk].pk,
            'score': 4, 'content': '另一笔订单评价',
        }, format='json'))
        foreign_provider = make_provider()
        foreign_account = get_or_create_account_for_legacy_user(foreign_provider)
        foreign_provider.escort_profile.account = foreign_account
        foreign_provider.escort_profile.save(update_fields=['account'])
        self.accounts[foreign_provider.pk] = foreign_account
        self.tokens[foreign_provider.pk] = issue_account_tokens(foreign_account)[0]
        self.login(self.boss)
        foreign_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':foreign_account.pk, 'client_request_id':'evaluation-foreign-order'}, format='json'))['id']
        self.login(foreign_provider)
        self.ok(self.client.post(f'/api/orders/orders/{foreign_id}/start/', {'entry_image':flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{foreign_id}/complete/', {'completion_image':flow.image()}, format='multipart'))
        self.login(self.boss)
        self.ok(self.client.post('/api/orders/evaluate/', {'order_id':foreign_id,
            'provider_account_id':foreign_account.pk, 'score':5, 'content':'仅另一位陪玩可见'}, format='json'))
        order_id, _ = self.prepare()
        self.login(self.boss)
        review = self.ok(self.client.post('/api/orders/evaluate/', {
            'order_id': order_id, 'provider_account_id': self.accounts[self.provider.pk].pk,
            'score': 5, 'content': '真实订单评价验证',
        }, format='json'))
        order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.login(self.provider)
        notices = self.ok(self.client.get('/api/messages/'))['messages']
        notice = next(row for row in notices if row['title'] == '收到新的服务评价' and row['related_order_id'] == order_id)
        own = self.ok(self.client.get('/api/orders/evaluations/mine/'))['list']
        self.assertEqual({row['order'] for row in own}, {other_id, order_id})
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:delete']
        role.save(update_fields=['permissions'])
        self.login(self.staff)
        self.assertTrue(any(row['id'] == review['id'] for row in self.ok(self.client.get('/api/admin/evaluations/'))['list']))
        repo = Path(settings.BASE_DIR).parent.parent
        fixed = os.environ.get('XA_EVALUATION_MESSAGE_FIXED') == '1'
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-evaluation-message-375.cjs')],
            input=json.dumps({'root':root, 'base':self.live_server_url, 'orderId':order_id, 'foreignOrderId':foreign_id,
                'orderNo':order['order_no'], 'messageId':notice['id'], 'id':self.accounts[self.provider.pk].pk,
                'token':self.tokens[self.provider.pk], 'fixed':fixed}),
            text=True, encoding='utf-8', capture_output=True, timeout=50, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        if fixed:
            self.login(self.boss)
            evaluation = next(row for row in self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['evaluations'] if row['id'] == review['id'])
            self.assertEqual(evaluation['reply_content'], '感谢老板，已收到评价')
            self.login(self.staff)
            item = self.ok(self.client.get(f"/api/admin/evaluations/{review['id']}/"))
            self.assertEqual(item['reply_content'], '感谢老板，已收到评价')
            self.assertEqual(self.client.delete(f"/api/admin/evaluations/{review['id']}/", {'reply_version':item['reply_version']}, format='json').data['code'], 0)
            missing = subprocess.run(['node', str(repo / 'scripts/provider-vue-evaluation-message-375.cjs')],
                input=json.dumps({'root':root, 'base':self.live_server_url, 'orderId':order_id,
                    'orderNo':order['order_no'], 'messageId':notice['id'], 'id':self.accounts[self.provider.pk].pk,
                    'token':self.tokens[self.provider.pk], 'fixed':True, 'missing':True}),
                text=True, encoding='utf-8', capture_output=True, timeout=50, cwd=repo)
            self.assertEqual(missing.returncode, 0, missing.stdout + missing.stderr)
            print(missing.stdout.strip())
