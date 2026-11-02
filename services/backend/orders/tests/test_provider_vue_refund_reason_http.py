"""A provider can see the reason of a refunded assigned order."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.models import Order
from orders.tests import test_provider_cancelled_order_flow as flow
from orders.tests import test_release_flow


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class ProviderVueRefundReasonHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderCancelledOrderFlowTest.setUp(self)

    login = flow.ProviderCancelledOrderFlowTest.login
    ok = flow.ProviderCancelledOrderFlowTest.ok

    def test_staff_refund_reason_reaches_each_party(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires current Vue build and Node')
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'dispatch_route':'STAFF', 'client_request_id':'vue-refund-reason'}, format='json'))
        oid = order['id']
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{oid}/assign/', {'provider_id':self.accounts[self.provider.pk].pk}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{oid}/start/', {'entry_image':test_release_flow.screenshot()}, format='multipart'))
        self.login(self.staff)
        preview = self.ok(self.client.get(f'/api/admin/orders/{oid}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{oid}/refund/', {'reason':'隔离客服核实终止服务',
            'plan_token':preview['plan_token'], 'client_request_id':'vue-refund-reason-refund'}, format='json'))
        for user in (self.boss,self.staff,self.provider):
            self.login(user)
            path = f'/api/admin/orders/{oid}/' if user == self.staff else f'/api/orders/orders/{oid}/'
            detail = self.ok(self.client.get(path))
            self.assertEqual((detail['status'], detail['cancel_reason']), ('CANCELLED','隔离客服核实终止服务'))
        repo = Path(settings.BASE_DIR).parent.parent
        def browser(row, expected, refunded):
            run = subprocess.run(['node', str(repo / 'scripts/provider-vue-refund-reason-375.cjs')],
                input=json.dumps({'root':root,'base':self.live_server_url,'orderId':row['id'],'orderNo':row['order_no'],
                    'token':issue_account_tokens(self.accounts[self.provider.pk])[0], 'id':self.accounts[self.provider.pk].pk,
                    'expected':expected, 'refunded':refunded}), text=True,encoding='utf-8',capture_output=True,timeout=35,cwd=repo)
            self.assertEqual(run.returncode,0,run.stdout + run.stderr)
        def native(row, expected, label):
            if os.environ.get('XA_REFUND_REASON_NATIVE') != '1':
                return
            run = subprocess.run(['node', str(repo / 'scripts/native-refund-reason-iphone12.cjs')],
                input=json.dumps({'base':self.live_server_url, 'orderId':row['id'],
                    'token':issue_account_tokens(self.accounts[self.boss.pk])[0], 'bossId':self.accounts[self.boss.pk].pk,
                    'expected':expected, 'label':label}),text=True,encoding='utf-8',capture_output=True,timeout=35,cwd=repo)
            self.assertEqual(run.returncode,0,run.stdout + run.stderr)
        browser(order, '退款原因：隔离客服核实终止服务', True)
        native(order, '隔离客服核实终止服务', '退款原因')
        # Historical rows can lack a recorded reason; do not invent one.
        Order.objects.filter(pk=oid).update(cancel_reason='')
        browser(order, '', True)

        # A fully discounted order can be cancelled without returning money.
        self.login(self.boss)
        coupon = Coupon.objects.create(name='隔离全额券',discount_type='DIRECT',amount=10000)
        self.ok(self.client.post(f'/api/coupons/{coupon.pk}/claim/'))
        voucher = UserCoupon.objects.get(account=self.accounts[self.boss.pk], coupon=coupon)
        free = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk, 'dispatch_route':'STAFF',
            'user_coupon_id':voucher.pk, 'client_request_id':'vue-cancel-zero'}, format='json'))
        self.assertEqual(free['amount'],0)
        self.login(self.staff)
        self.ok(self.client.post(f"/api/admin/orders/{free['id']}/assign/", {'provider_id':self.accounts[self.provider.pk].pk}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{free['id']}/reject/", {'reason':'隔离拒单'}, format='json'))
        self.login(self.boss)
        cancelled = self.ok(self.client.post(f"/api/orders/orders/{free['id']}/cancel/", {'reason':'隔离老板取消'}, format='json'))
        self.assertEqual((cancelled['amount'],cancelled['payment_status'],cancelled['cancel_reason']), (0,'REFUNDED','隔离老板取消'))
        self.login(self.provider)
        self.assertEqual(self.ok(self.client.get(f"/api/orders/orders/{free['id']}/"))['cancel_reason'], '隔离老板取消')
        browser(free, '取消原因：隔离老板取消', False)
        native(free, '隔离老板取消', '取消原因')
