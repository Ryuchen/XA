"""Approval messages describe posted, frozen and deposit amounts without claiming availability."""
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

from orders.models import Order
from orders.tests import test_provider_aftersale_status as flow
from wallet.models import IncomeHold, ProviderReport, SystemConfig, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueSettlementNoticeHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp(); self.client=APIClient(); flow.ProviderAfterSaleStatusTest.setUp(self)
    login=flow.ProviderAfterSaleStatusTest.login
    ok=flow.ProviderAfterSaleStatusTest.ok

    def test_approval_after_window_with_frozen_income(self):
        root=os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'): self.skipTest('Requires current Vue build and Node')
        SystemConfig.objects.update_or_create(key='club_income_rules_enabled',defaults={'value':'1'})
        profile=self.provider.escort_profile; profile.deposit_mode='EARNINGS'; profile.save(update_fields=['deposit_mode'])
        self.login(self.boss)
        created=self.ok(self.client.post('/api/orders/create/',{'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk,'client_request_id':'vue-notice-hold'},format='json'))
        oid=created['id']; self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{oid}/start/',{'entry_image':flow.image()},format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{oid}/complete/',{'completion_image':flow.image()},format='multipart'))
        report=ProviderReport.objects.get(order_id=oid,provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',{'kind':'RESULT','image':flow.image()},format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/',{},format='json'))
        # Simulate elapsed aftersale time in this test database only.
        Order.objects.filter(pk=oid).update(completed_at=timezone.now()-timedelta(hours=73))
        self.login(self.staff); self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/',{},format='json'))
        order=Order.objects.get(pk=oid); self.assertIsNotNone(order.settled_at); self.assertEqual(order.income_hold_hours,72)
        wallet=Wallet.objects.get(user=self.provider); profile.refresh_from_db()
        hold=IncomeHold.objects.get(income_transaction__order=order,wallet=wallet)
        self.assertEqual((wallet.balance,wallet.frozen_amount,profile.deposit_paid,hold.amount),(0,3200,4800,3200))
        self.login(self.boss); self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{oid}/'))['status'],'COMPLETED')
        self.login(self.staff); self.assertEqual(self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))['status'],'APPROVED')
        self.login(self.provider)
        notices=self.ok(self.client.get('/api/messages/'))['messages']
        notice=next(row for row in notices if row['title']=='报单审核通过' and row['related_order_id']==oid)
        self.assertEqual(notice['preview'],'凭证已通过，结算结果请查看钱包流水。')
        repo=Path(settings.BASE_DIR).parent.parent
        result=subprocess.run(['node',str(repo/'scripts/provider-vue-settlement-notice-375.cjs')],
            input=json.dumps({'root':root,'base':self.live_server_url,'token':self.tokens[self.provider.pk],
                'id':self.accounts[self.provider.pk].pk,'orderNo':created['order_no'],'messageId':notice['id']}),
            text=True,encoding='utf-8',capture_output=True,timeout=40,cwd=repo)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
