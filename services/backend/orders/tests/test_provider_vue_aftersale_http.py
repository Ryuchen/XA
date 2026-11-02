"""The active Vue provider page shows actual aftersale and refund outcomes."""
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

from orders.tests import test_provider_aftersale_status as flow
from wallet.models import Transaction, Wallet
from wallet.models import ProviderReport
from orders.models import Order
from engagement.confirmation import process_order


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueAfterSaleHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    open_aftersale = flow.ProviderAfterSaleStatusTest.open_aftersale

    def exercise_receipt(self, partial, ambiguous=False):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires current Vue build and Node')
        if ambiguous:
            self.boss.inviter = self.provider
            self.boss.inviter_commission_rate = 50
            self.boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        order_id, report_id = self.prepare()
        if partial:
            matured = timezone.now() - timedelta(hours=73)
            Order.objects.filter(pk=order_id).update(completed_at=matured)
            ProviderReport.objects.filter(pk=report_id).update(submitted_at=matured)
            self.assertTrue(process_order(order_id))
        case_id = self.open_aftersale(order_id)
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'order:refund']
        role.save(update_fields=['permissions'])
        self.login(self.boss)
        order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-aftersale-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'token': self.tokens[self.provider.pk],
                'id': self.accounts[self.provider.pk].pk, 'bossToken': self.tokens[self.boss.pk],
                'staffToken': self.tokens[self.staff.pk], 'orderId': order_id, 'orderNo': order['order_no'], 'caseId': case_id, 'partial': partial}),
            text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 42500 if partial else 50000)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 6750 if ambiguous else 6000 if partial else 0)
        self.login(self.provider)
        provider_order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        summary = provider_order['refund_summary']
        self.assertEqual((summary['amount'], summary['remaining_amount']), (2500, 7500) if partial else (10000, 0))
        self.assertEqual((summary['recovered_amount'], summary['net_income']), (2000, 6000) if partial else (0, 0))
        own = Transaction.objects.filter(order_id=order_id, wallet__user=self.provider, status='SUCCESS', tx_type__in=['INCOME','GIFT']).select_related('reversal_of')
        self.assertEqual(sum(row.amount for row in own if not (row.reversal_of or row).remark.startswith('推荐分佣：')), summary['net_income'])
        self.assertEqual(bool(provider_order['settled_at']), partial)
        for user,path in ((self.boss,f'/api/orders/orders/{order_id}/'),(self.staff,f'/api/admin/orders/{order_id}/')):
            self.login(user)
            self.assertEqual(self.ok(self.client.get(path))['refund_summary']['amount'],summary['amount'])
        if os.environ.get('XA_AFTERSALE_RECEIPT_NATIVE') == '1':
            native = subprocess.run(['node',str(repo / 'scripts/native-aftersale-receipt-iphone12.cjs')],
                input=json.dumps({'base':self.live_server_url,'orderId':order_id,'bossId':self.accounts[self.boss.pk].pk,
                    'token':self.tokens[self.boss.pk],'refundAmount':2500 if partial else 10000,
                    'refundText':'250' if partial else '1000'}),text=True,encoding='utf-8',capture_output=True,timeout=40,cwd=repo)
            self.assertEqual(native.returncode,0,native.stdout+native.stderr)
        if ambiguous:
            self.login(self.provider)
            Transaction.objects.filter(order_id=order_id, remark__startswith='推荐分佣：').update(remark='历史收入')
            summary = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['refund_summary']
            self.assertTrue(summary['income_breakdown_unavailable'])
            self.assertIsNone(summary['net_income']); self.assertIsNone(summary['recovered_amount'])
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-aftersale-375.cjs')],
                input=json.dumps({'root':root,'base':self.live_server_url,'token':self.tokens[self.provider.pk],
                    'id':self.accounts[self.provider.pk].pk,'orderId':order_id,'orderNo':order['order_no'],'ambiguous':True}),
                text=True,encoding='utf-8',capture_output=True,timeout=45,cwd=repo)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_unsettled_full_refund(self):
        self.exercise_receipt(False)

    def test_settled_partial_refund(self):
        self.exercise_receipt(True)

    def test_ambiguous_legacy_income_is_not_presented_as_a_split(self):
        self.exercise_receipt(True, ambiguous=True)

    def test_in_service_remains_estimated(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires current Vue build and Node')
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'client_request_id': 'vue-service-receipt'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.image()}, format='multipart'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-aftersale-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'token': self.tokens[self.provider.pk],
                'id': self.accounts[self.provider.pk].pk, 'orderId': order['id'], 'orderNo': order['order_no'], 'inProgress': True}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
