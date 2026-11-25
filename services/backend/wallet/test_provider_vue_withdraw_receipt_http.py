from common.fronted_test_support import ReactLiveHTTPTestCase, current_http, fronted_root
"""An actual earned order and staff-recorded withdrawal share the same receipt wording."""
from datetime import timedelta
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from engagement.confirmation import process_order
from orders.models import Order
from orders.tests import test_provider_aftersale_status as flow
from wallet.models import ProviderReport, SystemConfig, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, PLATFORM_IDENTITY_ENABLED=True)
class ProviderVueWithdrawReceiptHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp(); self.client = APIClient(); flow.ProviderAfterSaleStatusTest.setUp(self)
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare

    def run_browser(self, root, ids):
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/scenarios/provider-vue-withdraw-receipt-375.cjs')],
            input=json.dumps({'root':root, 'base':self.live_server_url, 'token':self.tokens[self.provider.pk],
                'id':self.accounts[self.provider.pk].pk, 'fixed':True, 'withdrawals':ids}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())

    def test_earned_order_and_staff_recorded_withdrawal(self):
        root = fronted_root('provider')
        if not root: self.skipTest('requires current React source')
        SystemConfig.objects.update_or_create(key='min_withdraw_amount', defaults={'value':'1000'})
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value':'2'})
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'withdraw:view', 'withdraw:audit']
        role.save(update_fields=['permissions'])
        order_id, report_id = self.prepare()
        matured = timezone.now() + timedelta(hours=73)
        from unittest.mock import patch
        with patch('django.utils.timezone.now', return_value=matured):
            self.assertTrue(process_order(order_id))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.login(self.provider)
        payee = self.ok(self.client.put('/api/wallet/withdraw-accounts/WECHAT/', {'payee_account':'isolated-1234',
            'payee_name':'隔离收款', 'expected_id':0, 'expected_version':0}, format='json'))
        ids = []
        for index in range(3):
            result = self.ok(self.client.post('/api/wallet/withdraw/', {'amount':2000, 'withdrawal_account_id':payee['id'],
                'expected_payee_version':payee['version'], 'client_request_id':f'vue-receipt-{index}'}, format='json'))
            ids.append(result['request']['id'])
        self.login(self.staff)
        claim = self.ok(self.client.post(f'/api/admin/withdrawals/{ids[0]}/claim/'))
        store_approved = self.ok(self.client.post(
            f'/api/admin/withdrawals/{ids[0]}/approve/',
            {'expected_claimed_at':claim['claimed_at']}, format='json'))
        self.assertEqual(store_approved['status'], 'STORE_APPROVED')

        from stores.models import PlatformAccount, PlatformRoleGrant
        from stores.platform_auth import issue_tokens
        finance = PlatformAccount(
            username='vue-receipt-finance', phone='13800009999', is_phone_verified=True)
        finance.set_password('test-only-finance-password')
        finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='shenglang')
        finance_token = issue_tokens(finance, 'SMS', finance.phone)[0]
        finance_client = APIClient(
            HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + finance_token)
        platform_path = f'/api/admin/platform/withdrawals/shenglang/{ids[0]}'
        platform_approved = finance_client.post(
            platform_path + '/approve', {'client_request_id':'vue-receipt-platform-approve'}, format='json')
        self.assertEqual(platform_approved.data['code'], 0, platform_approved.data)
        self.assertEqual(platform_approved.data['data']['status'], 'PLATFORM_APPROVED')
        paid = finance_client.post(platform_path + '/paid', {
            'client_request_id':'vue-receipt-platform-paid',
            'payout_reference':'ISOLATED-RECEIPT', 'paid_amount_fen':1960,
            'paid_at':timezone.now().isoformat(),
        }, format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        self.assertEqual(paid.data['data']['status'], 'PAID')

        claim = self.ok(self.client.post(f'/api/admin/withdrawals/{ids[1]}/claim/'))
        self.ok(self.client.post(f'/api/admin/withdrawals/{ids[1]}/reject/', {'audit_remark':'隔离驳回原因',
            'expected_claimed_at':claim['claimed_at']}, format='json'))
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['status'], 'COMPLETED')
        self.login(self.provider)
        notices = self.ok(self.client.get('/api/messages/'))['messages']
        approved_notice = next(row for row in notices if row['title'] == '提现已登记打款')
        self.assertIn('平台财务已登记线下打款', approved_notice['preview'])
        self.assertEqual(approved_notice['action_url'], f'/pages/wallet/index?withdraw_id={ids[0]}')
        rejected_notice = next(row for row in notices if row['title'] == '提现审核未通过')
        self.assertEqual(rejected_notice['action_url'], f'/pages/wallet/index?withdraw_id={ids[1]}')
        self.run_browser(root, ids)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual((wallet.balance, wallet.frozen_amount), (4000, 2000))
