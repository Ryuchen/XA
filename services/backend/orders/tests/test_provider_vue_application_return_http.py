from common.fronted_test_support import ReactLiveHTTPTestCase, current_http, fronted_root
"""Actual historical application notice through re-open and cancellation."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Evaluation, Order, OrderApplication
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_console_user, make_provider, make_service
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime
from wallet.models import IncomeHold, ProviderReport, SystemConfig, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueApplicationReturnTest(ReactLiveHTTPTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_boss_staff_and_team_see_live_application_progress(self):
        provider_root = fronted_root('provider')
        admin_root = fronted_root('admin')
        if not provider_root or not admin_root:
            self.skipTest('requires current React sources for both roles')
        SystemConfig.objects.update_or_create(key='club_income_rules_enabled', defaults={'value':'1'})
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value':'2'})
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'evaluation:view', 'dashboard:view', 'withdraw:view']
        role.save(update_fields=['permissions'])
        partner = make_provider(display_name='隔离组队伙伴')
        self.accounts[partner.pk] = get_or_create_account_for_legacy_user(partner)
        for user in (self.provider, partner):
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.deposit_mode = 'EARNINGS'
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at', 'deposit_mode'])
        self.service = make_service(name='双陪实时进度测试', price=20000, billing_provider_count=2)
        self.login(self.admin)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {'account_id':self.accounts[self.boss.pk].pk,
            'amount':50000, 'gift_amount':0, 'trade_no':'team-maturity-receipt', 'proof_image':flow.screenshot(),
            'client_request_id':'team-maturity-funding'}, format='multipart'))
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'team-progress-three-roles'}, format='json'))
        repo = Path(settings.BASE_DIR).parent.parent
        with current_http(self, isolated_realtime, report_approval=True) as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/three-role-application-progress-live.cjs')],
                input=json.dumps({'providerRoot':provider_root, 'adminRoot':admin_root, 'base':base, 'wsBase':ws_base,
                    'orderId':order['id'], 'orderNo':order['order_no'], 'bossId':self.accounts[self.boss.pk].pk,
                    'image':str(repo / 'apps/boss-miniapp/native/assets/icons/chat.png'),
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'first':{'id':self.accounts[self.provider.pk].pk,'token':issue_account_tokens(self.accounts[self.provider.pk])[0]},
                    'partner':{'id':self.accounts[partner.pk].pk,'token':issue_account_tokens(self.accounts[partner.pk])[0]}}),
                text=True, encoding='utf-8', capture_output=True, timeout=100, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order_row = Order.objects.get(pk=order['id'])
        self.assertEqual(order_row.status, 'COMPLETED')
        self.assertEqual(set(order_row.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True)),
                         {self.accounts[self.provider.pk].pk, self.accounts[partner.pk].pk})
        self.assertEqual(list(OrderApplication.objects.filter(order=order_row).order_by('pk').values_list('status', flat=True)),
                         ['WITHDRAWN', 'WITHDRAWN', 'SELECTED'])
        self.assertEqual(Transaction.objects.filter(order=order_row, tx_type='PAY').count(), 1)
        reviews = list(Evaluation.objects.filter(order=order_row).order_by('provider_account_id'))
        self.assertEqual({(row.provider_account_id, row.score, row.reply_content) for row in reviews}, {
            (self.accounts[self.provider.pk].pk, 5, '甲的独立回复'),
            (self.accounts[partner.pk].pk, 4, '乙的独立回复')})
        for user in (self.provider, partner):
            user.escort_profile.refresh_from_db()
            self.assertEqual(user.escort_profile.rating_count, 1)
        self.login(self.boss)
        for user, score in ((self.provider, 5), (partner, 4)):
            profile = self.ok(self.client.get(f'/api/users/escorts/{self.accounts[user.pk].pk}/'))
            self.assertEqual((profile['ratingCount'], profile['rating']), (1, score))
        self.login(self.admin)
        evaluation_list = self.ok(self.client.get('/api/admin/evaluations/'))['list']
        self.assertEqual({row['id'] for row in evaluation_list if row['order'] == order_row.pk}, {row.pk for row in reviews})
        snapshot = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['order_total'], snapshot['order_status']['COMPLETED'], snapshot['order_amount_total'],
                          snapshot['unreplied_evaluation_total']), (1, 1, 40000, 0))
        self.assertEqual(snapshot['open_aftersale_order_total'], 1)
        ticket = order_row.aftersales.get(status='OPEN')
        reports = ProviderReport.objects.filter(order=order_row)
        self.assertEqual(reports.count(), 2)
        self.assertTrue(all(row.entry_image and row.completion_image for row in reports))
        self.assertFalse(Transaction.objects.filter(order=order_row, tx_type='INCOME').exists())
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance, 10000)
        report_ids = []
        for user in (self.provider, partner):
            self.login(user)
            report_id = reports.get(provider_account=self.accounts[user.pk]).pk
            self.ok(self.client.post(f'/api/wallet/reports/{report_id}/images/',
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.assertEqual(self.ok(self.client.post(f'/api/wallet/reports/{report_id}/submit/'))['status'], 'PENDING')
            report_ids.append(report_id)
        self.login(self.admin)
        self.assertEqual(self.ok(self.client.get('/api/admin/dashboard/stats'))['pending_report_total'], 2)
        for remaining, report_id in zip((1, 0), report_ids):
            self.assertEqual(self.ok(self.client.post(f'/api/admin/reports/{report_id}/approve/'))['status'], 'APPROVED')
            self.assertEqual(self.ok(self.client.get('/api/admin/dashboard/stats'))['pending_report_total'], remaining)
        order_row.refresh_from_db()
        self.assertIsNone(order_row.settled_at)
        self.assertIsNotNone(order_row.consumption_ready_at)
        self.assertFalse(Transaction.objects.filter(order=order_row, tx_type='INCOME').exists())
        # Date only the isolated, API-created order and reports as a coherent
        # historical journey; execute the actual due task against the real clock.
        from engagement.models import ConsumptionConfirmation
        from engagement.tasks import confirm_due_consumption
        completed = timezone.now() - timedelta(hours=74)
        submitted = completed + timedelta(minutes=10)
        Order.objects.filter(pk=order_row.pk).update(completed_at=completed, report_ready_at=submitted,
                                                     consumption_ready_at=submitted + timedelta(hours=72))
        ProviderReport.objects.filter(pk__in=report_ids).update(submitted_at=submitted)
        self.assertEqual(confirm_due_consumption(), 0)
        order_row.refresh_from_db()
        self.assertIsNone(order_row.settled_at)
        self.assertIsNone(order_row.consumption_confirmed_at)
        self.assertFalse(Transaction.objects.filter(order=order_row, tx_type='INCOME').exists())
        for user in (self.boss, self.provider, partner, self.admin):
            self.login(user)
            prefix = '/api/admin/orders/' if user == self.admin else '/api/orders/orders/'
            self.assertEqual(self.ok(self.client.get(f'{prefix}{order_row.pk}/'))['aftersale_status'], 'OPEN')
            if user in (self.provider, partner):
                notices = [item for item in self.ok(self.client.get('/api/messages/'))['messages']
                           if item['related_order_id'] == order_row.pk and item['title'] == '订单售后处理中']
                self.assertEqual(len(notices), 1)
                self.assertNotIn('隔离双陪服务问题', notices[0]['preview'])
                self.assertIn('未结算收益暂缓', notices[0]['preview'])
                self.assertIn('已结算收益请查看钱包流水', notices[0]['preview'])
        with current_http(self, isolated_realtime, report_approval=True) as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/team-aftersale-resolution-live.cjs')],
                input=json.dumps({'providerRoot':provider_root, 'adminRoot':admin_root, 'base':base, 'wsBase':ws_base,
                    'orderId':order_row.pk, 'orderNo':order_row.order_no, 'bossId':self.accounts[self.boss.pk].pk,
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'first':{'id':self.accounts[self.provider.pk].pk,'token':issue_account_tokens(self.accounts[self.provider.pk])[0]},
                    'partner':{'id':self.accounts[partner.pk].pk,'token':issue_account_tokens(self.accounts[partner.pk])[0]}}),
                text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.resolution), ('RESOLVED', '隔离双方核实处理'))
        self.login(self.admin)
        self.assertEqual(self.ok(self.client.get('/api/admin/dashboard/stats'))['open_aftersale_order_total'], 0)
        for user in (self.boss, self.provider, partner):
            self.login(user)
            self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_row.pk}/'))['aftersale_status'], 'RESOLVED')
            title = '售后已处理' if user == self.boss else '订单售后已处理'
            notices = [item for item in self.ok(self.client.get('/api/messages/'))['messages']
                       if item['related_order_id'] == order_row.pk and item['title'] == title]
            self.assertEqual(len(notices), 1)
        self.assertEqual(confirm_due_consumption(), 1)
        self.assertEqual(confirm_due_consumption(), 0)
        order_row.refresh_from_db()
        self.assertIsNotNone(order_row.settled_at)
        self.assertIsNotNone(order_row.consumption_confirmed_at)
        self.assertEqual(ConsumptionConfirmation.objects.filter(order=order_row).count(), 1)
        for user in (self.provider, partner):
            user.escort_profile.refresh_from_db()
            wallet = Wallet.objects.get(account=self.accounts[user.pk])
            self.assertEqual((user.escort_profile.deposit_paid, wallet.balance, wallet.frozen_amount), (9600, 0, 6400))
            self.assertEqual(Transaction.objects.filter(order=order_row, wallet=wallet, tx_type='INCOME', amount=16000).count(), 1)
            self.login(user)
            self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_row.pk}/'))['settlement_status'], 'SETTLED')
            withdrawal = self.ok(self.client.get('/api/wallet/withdraw/'))
            self.assertEqual((withdrawal['balance'], withdrawal['frozen_amount'], withdrawal['can_withdraw']), (0, 6400, False))
        for user in (self.boss, self.admin):
            self.login(user)
            prefix = '/api/admin/orders/' if user == self.admin else '/api/orders/orders/'
            detail = self.ok(self.client.get(f'{prefix}{order_row.pk}/'))
            self.assertEqual(detail['settlement_status'], 'SETTLED')
            self.assertIsNotNone(detail['consumption_confirmed_at'])
        from wallet.income import release_due_income
        holds = IncomeHold.objects.filter(income_transaction__order=order_row)
        self.assertEqual(holds.count(), 2)
        holds.update(release_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(release_due_income(), 2)
        self.assertEqual(release_due_income(), 0)
        for user in (self.provider, partner):
            wallet = Wallet.objects.get(account=self.accounts[user.pk])
            self.assertEqual((wallet.balance, wallet.frozen_amount), (6400, 0))
            self.login(user)
            withdrawal = self.ok(self.client.get('/api/wallet/withdraw/'))
            self.assertEqual((withdrawal['balance'], withdrawal['frozen_amount'], withdrawal['can_withdraw']), (6400, 0, True))
        self.assertEqual(sum(Transaction.objects.filter(order=order_row).values_list('amount', flat=True)), 0)
        self.login(self.provider)
        payee = self.ok(self.client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account':'ISOLATED-TEAM-ACCOUNT', 'payee_name':'隔离测试', 'expected_id':0, 'expected_version':0,
        }, format='json'))
        requested = self.ok(self.client.post('/api/wallet/withdraw/', {'amount':6000,
            'withdrawal_account_id':payee['id'], 'expected_payee_version':payee['version'],
            'client_request_id':'team-maturity-withdraw'}, format='json'))['request']
        request_id = requested['id']
        self.assertEqual((requested['status'], requested['actual_amount'], Wallet.objects.get(account=self.accounts[self.provider.pk]).frozen_amount), ('PENDING', 5880, 6000))
        self.login(self.admin)
        snapshot = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['pending_withdraw_total'], snapshot['pending_withdraw_unclaimed'], snapshot['pending_withdraw_mine']), (1, 1, 0))
        first = make_console_user(['withdraw:view', 'withdraw:audit'])
        manager = make_console_user(['withdraw:view', 'withdraw:release'])
        for user in (first, manager):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = self.accounts[user.pk]
            user.admin_membership.save(update_fields=['account'])
        self.login(first)
        claim = self.ok(self.client.post(f'/api/admin/withdrawals/{request_id}/claim/'))
        self.login(self.admin)
        snapshot = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['pending_withdraw_total'], snapshot['pending_withdraw_unclaimed'], snapshot['pending_withdraw_mine']), (1, 0, 0))
        self.login(manager)
        self.ok(self.client.post(f'/api/admin/withdrawals/{request_id}/release-claim/', {
            'confirmed_unpaid':True, 'reason':'隔离核实未打款',
            'expected_claimed_by_account':claim['claimed_by_account'], 'expected_claimed_at':claim['claimed_at']}, format='json'))
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'withdraw:view', 'withdraw:audit']
        role.save(update_fields=['permissions'])
        self.login(self.admin)
        claim = self.ok(self.client.post(f'/api/admin/withdrawals/{request_id}/claim/'))
        snapshot = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['pending_withdraw_total'], snapshot['pending_withdraw_unclaimed'], snapshot['pending_withdraw_mine']), (1, 0, 1))
        self.ok(self.client.post(f'/api/admin/withdrawals/{request_id}/approve/', {
            'payout_reference':'ISOLATED-TEAM-OFFLINE-PAYOUT', 'expected_claimed_at':claim['claimed_at']}, format='json'))
        self.assertEqual(self.ok(self.client.get('/api/admin/dashboard/stats'))['pending_withdraw_total'], 0)
        self.login(self.provider)
        receipt = next(row for row in self.ok(self.client.get('/api/wallet/withdraw/'))['requests'] if row['id'] == request_id)
        self.assertEqual((receipt['status'], receipt['actual_amount'], receipt['payout_reference']), ('APPROVED', 5880, 'ISOLATED-TEAM-OFFLINE-PAYOUT'))
        wallet = Wallet.objects.get(account=self.accounts[self.provider.pk])
        self.assertEqual((wallet.balance, wallet.frozen_amount), (400, 0))
        other_wallet = Wallet.objects.get(account=self.accounts[partner.pk])
        self.assertEqual((other_wallet.balance, other_wallet.frozen_amount), (6400, 0))

    def test_application_history_updates_when_boss_selects_another_provider(self):
        root = fronted_root('provider')
        if not root:
            self.skipTest('requires current React source')
        other = make_provider(display_name='另一位陪玩')
        self.accounts[other.pk] = get_or_create_account_for_legacy_user(other)
        for user in (self.provider, other):
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'dispatch_mode':'BOSS_SELECT', 'client_request_id':'application-history-live'}, format='json'))
        ids = []
        for user in (self.provider, other):
            self.login(user)
            self.ok(self.client.post(f"/api/orders/orders/{order['id']}/grab/", {}, format='json'))
            ids.append(OrderApplication.objects.get(order_id=order['id'], applicant=self.accounts[user.pk]).pk)
        repo = Path(settings.BASE_DIR).parent.parent
        with current_http(self, isolated_realtime) as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-application-return-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'orderId':order['id'],
                    'orderNo':order['order_no'], 'chosenId':ids[1], 'historyOnly':True,
                    'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'id':self.accounts[self.provider.pk].pk,
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=70, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_historical_application_tracks_reopened_then_cancelled_order(self):
        root = fronted_root('provider')
        if not root:
            self.skipTest('requires current React source')
        assigned = make_provider(display_name='隔离客服指派陪玩')
        self.accounts[assigned.pk] = get_or_create_account_for_legacy_user(assigned)
        for user in (self.provider, assigned):
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'dispatch_mode':'BOSS_SELECT', 'support_contact_id':self.card.pk,
            'client_request_id':'application-history-return'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/grab/", {}, format='json'))
        app = OrderApplication.objects.get(order_id=order['id'], applicant=self.accounts[self.provider.pk])
        self.login(self.admin)
        self.ok(self.client.post(f"/api/admin/orders/{order['id']}/assign/", {
            'provider_id':self.accounts[assigned.pk].pk}, format='json'))
        self.login(self.provider)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        notice = next(item for item in messages if item['title']=='报名结果已更新' and item['related_order_id']==order['id'])
        self.assertEqual(notice['action_url'], f'/pages/orders/index?application_id={app.pk}')
        repo = Path(settings.BASE_DIR).parent.parent
        with current_http(self, isolated_realtime) as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-application-return-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'orderId':order['id'],
                    'orderNo':order['order_no'], 'applicationId':app.pk, 'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'id':self.accounts[self.provider.pk].pk, 'assignedToken':issue_account_tokens(self.accounts[assigned.pk])[0],
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'fixed':True}),
                text=True, encoding='utf-8', capture_output=True, timeout=70, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
