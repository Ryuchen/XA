"""Staff claim and release a real service-income withdrawal while the wallet is open."""
import os
from datetime import timedelta
from unittest import skipUnless
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_console_user, make_provider
from wallet.models import SystemConfig, Wallet, WithdrawRequest


@skipUnless(os.environ.get('XA_PROVIDER_REPORT_ROOT'), 'requires isolated provider build and Redis')
@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderWithdrawalLiveTest(LiveServerTestCase):
    def setUp(self):
        super().setUp(); self.client = APIClient(); flow.ReleaseFlowTest.setUp(self)
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def test_wallet_tracks_claim_release_and_recorded_payout(self):
        SystemConfig.objects.update_or_create(key='min_withdraw_amount', defaults={'value':'1000'})
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value':'10'})
        order = self.new_order(); report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin); self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        Order.objects.filter(pk=order.pk).update(completed_at=timezone.now()-timedelta(hours=73))
        process_order(order.pk)
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.provider.pk]).balance, 8000)
        self.login(self.provider)
        payee = self.ok(self.client.put('/api/wallet/withdraw-accounts/WECHAT/', {'payee_account':'ISOLATED-4321',
            'payee_name':'隔离收款', 'expected_id':0, 'expected_version':0}, format='json'))
        request = self.ok(self.client.post('/api/wallet/withdraw/', {'amount':6000, 'withdrawal_account_id':payee['id'],
            'expected_payee_version':payee['version'], 'client_request_id':'withdraw-page-live'}, format='json'))['request']
        staff = make_console_user(['withdraw:view', 'withdraw:audit']); manager = make_console_user(['withdraw:view', 'withdraw:release'])
        tokens = []
        for user in (staff, manager):
            account = get_or_create_account_for_legacy_user(user); user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account']); tokens.append(issue_account_tokens(account)[0])
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        manager_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        self.ok(staff_client.put('/api/admin/config/withdraw', {'min_amount': 1000, 'tax_rate': 20}, format='json'))
        payee = self.ok(self.client.put('/api/wallet/withdraw-accounts/WECHAT/', {'payee_account': 'ISOLATED-9876',
            'payee_name': '隔离收款', 'expected_id': payee['id'], 'expected_version': payee['version']}, format='json'))
        payee = self.ok(self.client.put('/api/wallet/withdraw-accounts/WECHAT/', {'payee_account': 'ISOLATED-LOCAL-9876',
            'payee_name': '隔离收款', 'expected_id': payee['id'], 'expected_version': payee['version']}, format='json'))
        listed = self.ok(self.client.get('/api/wallet/withdraw-accounts/'))
        self.assertEqual(listed[0]['payee_account'], 'ISOLATED-LOCAL-9876')
        payee = self.ok(self.client.put('/api/wallet/withdraw-accounts/WECHAT/', {'payee_account': 'ISOLATED-9877',
            'payee_name': '隔离收款', 'expected_id': payee['id'], 'expected_version': payee['version']}, format='json'))
        self.ok(self.client.delete('/api/wallet/withdraw-accounts/WECHAT/',
            {'expected_id': payee['id'], 'expected_version': payee['version']}, format='json'))
        self.assertEqual(self.ok(self.client.get('/api/wallet/withdraw-accounts/')), [])
        payee = self.ok(self.client.put('/api/wallet/withdraw-accounts/WECHAT/', {'payee_account': 'ISOLATED-9877',
            'payee_name': '隔离收款', 'expected_id': 0, 'expected_version': 0}, format='json'))
        def admin_post(action, client, data=None, withdraw_id=None):
            return self.ok(client.post(f'/api/admin/withdrawals/{withdraw_id or request["id"]}/{action}/', data or {}, format='json'))
        claim = admin_post('claim', staff_client)
        self.ok(manager_client.post(f'/api/admin/withdrawals/{request["id"]}/release-claim/',
            {'confirmed_unpaid': True, 'reason': '隔离测试已核实未打款',
             'expected_claimed_by_account': claim['claimed_by_account'], 'expected_claimed_at': claim['claimed_at']}, format='json'))
        claim = admin_post('claim', staff_client)
        self.ok(staff_client.post(f'/api/admin/withdrawals/{request["id"]}/approve/',
            {'payout_reference': 'ISOLATED-TEST-RECEIPT', 'expected_claimed_at': claim['claimed_at']}, format='json'))
        second = self.ok(self.client.post('/api/wallet/withdraw/', {'amount': 1000, 'withdrawal_account_id': payee['id'],
            'expected_payee_version': payee['version'], 'client_request_id': 'withdraw-rejection-live'}, format='json'))['request']
        second_claim = admin_post('claim', staff_client, withdraw_id=second['id'])
        over = self.client.post('/api/wallet/withdraw/', {'amount': 1500, 'withdrawal_account_id': payee['id'],
            'expected_payee_version': payee['version'], 'client_request_id': 'withdraw-page-live-over'}, format='json')
        self.assertEqual(over.data['code'], 400, over.data)
        self.ok(staff_client.post(f'/api/admin/withdrawals/{second["id"]}/reject/',
            {'audit_remark': '收款信息需核对', 'expected_claimed_at': second_claim['claimed_at']}, format='json'))
        self.ok(staff_client.put('/api/admin/config/withdraw', {'min_amount': 1000, 'tax_rate': 30}, format='json'))
        self.ok(self.client.post('/api/wallet/withdraw/', {'amount': 1500, 'withdrawal_account_id': payee['id'],
            'expected_payee_version': payee['version'], 'client_request_id': 'withdraw-page-live-third'}, format='json'))
        wallet = Wallet.objects.get(account=self.accounts[self.provider.pk])
        self.assertEqual((wallet.balance,wallet.frozen_amount),(500,1500))
        receipts = list(WithdrawRequest.objects.filter(account=self.accounts[self.provider.pk]).select_related('transaction').order_by('pk'))
        self.assertEqual([(row.status, row.transaction.status) for row in receipts],
                         [('APPROVED', 'SUCCESS'), ('REJECTED', 'FAILED'), ('PENDING', 'PENDING')])
        self.assertEqual(receipts[1].audit_remark, '收款信息需核对')
        self.assertEqual(receipts[2].amount, 1500)
        self.assertEqual([(row.tax_rate,row.tax_amount,row.actual_amount) for row in receipts],
                         [(10,600,5400),(20,200,800),(30,450,1050)])
        self.assertEqual([row.payee_account for row in receipts],['ISOLATED-4321','ISOLATED-9877','ISOLATED-9877'])

    def test_withdraw_history_paginates_and_stays_open_after_update(self):
        from django.core.cache import cache
        from wallet.services import get_wallet
        provider = make_provider(); account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(user=provider, account=account); wallet.balance = 60000; wallet.save(update_fields=['balance'])
        SystemConfig.objects.update_or_create(key='min_withdraw_amount', defaults={'value':'1000'})
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        payee = self.ok(client.put('/api/wallet/withdraw-accounts/WECHAT/', {'payee_account':'ISOLATED-PAGES',
            'payee_name':'分页验收', 'expected_id':0, 'expected_version':0}, format='json'))
        ids = []
        for index in range(23):
            cache.clear()
            data = self.ok(client.post('/api/wallet/withdraw/', {'amount':1000+index*10, 'withdrawal_account_id':payee['id'],
                'expected_payee_version':payee['version'], 'client_request_id':f'withdraw-history-{index}'}, format='json'))
            ids.append(data['request']['id'])
        first = self.ok(client.get('/api/wallet/withdraw/', {'requests_page':1}))
        second = self.ok(client.get('/api/wallet/withdraw/', {'requests_page':2}))
        self.assertEqual((len(first['requests']),len(second['requests']),first['requests_pagination']['total']),(20,3,23))
        self.assertEqual(second['requests'][-1]['id'],ids[0])
        self.assertEqual(len(self.ok(client.get('/api/wallet/withdraw/'))['requests']),23)
        staff = make_console_user(['withdraw:view','withdraw:audit']); staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        self.ok(client.post('/api/wallet/withdraw/', {'amount': 1230, 'withdrawal_account_id': payee['id'],
            'expected_payee_version': payee['version'], 'client_request_id': 'withdraw-pagination-live-new'}, format='json'))
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        claim = self.ok(staff_client.post(f'/api/admin/withdrawals/{ids[0]}/claim/'))
        self.ok(staff_client.post(f'/api/admin/withdrawals/{ids[0]}/approve/',
            {'expected_claimed_at': claim['claimed_at'], 'payout_reference': 'ISOLATED-PAGE-RECEIPT'}, format='json'))
        self.assertEqual(WithdrawRequest.objects.filter(account=account).count(),24)
        self.assertEqual(WithdrawRequest.objects.get(pk=ids[0]).status, 'APPROVED')
        other = make_provider(); other_account = get_or_create_account_for_legacy_user(other)
        other_client = APIClient(); other_client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(other_account)[0])
        selected = self.ok(other_client.get('/api/wallet/withdraw/', {'requests_page':1, 'request_id':ids[0]}))
        self.assertIsNone(selected['requested_withdrawal'])
