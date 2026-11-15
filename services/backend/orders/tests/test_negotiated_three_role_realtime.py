"""Real Redis/ASGI keeps three roles current during a negotiated termination."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from orders.tests import test_negotiated_transfer_refund as negotiated_flow
from users.test_native_checkin_order_realtime import isolated_redis_asgi
from wallet.models import ProviderReport, Transaction, Wallet


@skipUnless(os.environ.get('XA_NEGOTIATED_REALTIME') == '1', 'Requires isolated Redis and ASGI')
@override_settings(ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NegotiatedRealtimeJourneyTest(LiveServerTestCase):
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    prepare_transfer = negotiated_flow.NegotiatedTransferRefundTest.prepare_transfer

    def setUp(self):
        super().setUp()
        media = TemporaryDirectory(prefix='xa-negotiated-live-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)
        role = self.admin.admin_membership.roles.first()
        role.permissions = list(set(role.permissions) | {'dashboard:view', 'player_dashboard:view'})
        role.save(update_fields=['permissions'])

    def test_provider_page_restores_completion_after_aftersale_resolved(self):
        order, incoming = self.prepare_transfer()
        self.login(self.boss)
        case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '服务中需要客服处理', 'client_request_id': 'live-negotiated-hold',
        }, format='json'))
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/provider-negotiated-aftersale-hold-live.cjs')],
                input=json.dumps({'base': base, 'wsBase': ws_base, 'root': os.environ['XA_PROVIDER_VUE_ROOT'],
                                  'id': self.accounts[incoming.pk].pk, 'token': issue_account_tokens(self.accounts[incoming.pk])[0],
                                  'bossId': self.accounts[self.boss.pk].pk, 'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
                                  'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
                                  'orderId': order.pk, 'orderNo': order.order_no, 'caseId': case['id']}),
                cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order.refresh_from_db()
        self.assertEqual((order.status, order.aftersales.get(pk=case['id']).status), ('IN_SERVICE', 'RESOLVED'))
        self.assertFalse(order.refund_records.exists())

    def test_open_boss_and_both_providers_refresh_after_staff_negotiation(self):
        order, incoming = self.prepare_transfer()
        self.login(self.boss)
        repo = Path(settings.BASE_DIR).parent.parent
        native = subprocess.run(['node', str(repo / 'scripts/native-subsidy-aftersale-real.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': order.pk,
                              'bossId': str(self.accounts[self.boss.pk].pk),
                              'token': issue_account_tokens(self.accounts[self.boss.pk])[0]}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=30)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        case = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/aftersale/'))
        self.assertEqual(case['status'], 'OPEN')
        root = os.environ['XA_PROVIDER_VUE_ROOT']
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            data = {'base':base, 'wsBase':ws_base, 'root':root, 'adminRoot':os.environ['XA_ADMIN_VUE_ROOT'], 'orderId':order.pk, 'orderNo':order.order_no, 'refundDay':timezone.localdate().isoformat(), 'expectAftersale':True,
                    'bossId':self.accounts[self.boss.pk].pk,
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'providers':[{'id':self.accounts[user.pk].pk, 'token':issue_account_tokens(self.accounts[user.pk])[0],
                                  'burden':burden, 'net':net, 'view':view, 'escortNo':user.escort_profile.escort_no}
                                 for user,burden,net,view in ((self.provider,100,220,'orders'), (incoming,300,180,'reports'))]}
            result = subprocess.run(['node',str(repo/'scripts/negotiated-refund-three-role-realtime.cjs')],
                input=json.dumps(data),cwd=repo,text=True,encoding='utf-8',capture_output=True,timeout=70)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, order.refund_records.count()),
                         ('CANCELLED', 'PARTIALLY_REFUNDED', 1))
        self.assertEqual(order.refund_records.get().amount, 5000)
        self.assertEqual(order.aftersales.get(pk=case['id']).status, 'PARTIALLY_REFUNDED')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 16000)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 2200)
        self.assertEqual(Wallet.objects.get(user=incoming).balance, 1800)
        money = order.transactions.filter(status='SUCCESS', tx_type__in=['PAY', 'REFUND', 'INCOME', 'SHOP_INCOME'])
        self.assertEqual(sum(money.values_list('amount', flat=True)), 0)
        self.login(self.admin)
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['paid_amount_total'], dashboard['order_status'].get('CANCELLED'),
                          dashboard['order_status'].get('IN_SERVICE', 0)), (5000, 1, 0))
        player = self.ok(self.client.get('/api/admin/dashboard/player'))
        income = {row['escort_no']:row['value'] for row in player['rank']['income']}
        self.assertEqual((income[self.provider.escort_profile.escort_no], income[incoming.escort_profile.escort_no]), (2200, 1800))

    def test_transferred_completed_order_aftersale_preserves_paid_shares(self):
        order, incoming = self.prepare_transfer()
        order.refresh_from_db()
        old_share = order.providers.get(provider=self.provider)
        self.assertIsNotNone(old_share.settled_at)
        self.assertIsNone(order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=order, wallet__user=self.provider, tx_type='INCOME').count(), 1)
        old_report = ProviderReport.objects.get(order=order, provider=self.provider)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{old_report.pk}/images/',
                                 {'kind':'COMPLETION', 'image':flow.screenshot()}, format='multipart'))
        self.login(incoming)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                                 {'completion_image':flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'COMPLETED')
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)
        self.login(self.boss)
        case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/',
            {'reason':'隔离转单售后核对', 'client_request_id':'transferred-complete-aftersale'}, format='json'))
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['aftersale_status'], 'OPEN')
        self.login(self.admin)
        self.assertEqual(self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))['aftersale_status'], 'OPEN')
        for user in (self.provider, incoming):
            self.login(user)
            messages = [item for item in self.ok(self.client.get('/api/messages/'))['messages']
                        if item['related_order_id'] == order.pk and item['title'] == '订单售后处理中']
            self.assertEqual(len(messages), 1)
            self.assertIn('已结算收益以钱包流水为准', messages[0]['preview'])
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/transferred-aftersale-views.cjs')], cwd=repo,
            input=json.dumps({'base':self.live_server_url, 'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                'orderId':order.pk, 'orderNo':order.order_no,
                'providers':[{'id':self.accounts[user.pk].pk, 'token':issue_account_tokens(self.accounts[user.pk])[0],
                              'transferred':user == self.provider} for user in (self.provider, incoming)]}),
            text=True, encoding='utf-8', capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/resolve/',
                                 {'resolution':'隔离核对完成'}, format='json'))
        incomes = Transaction.objects.filter(order=order, tx_type='INCOME')
        self.assertEqual(incomes.count(), 2)
        self.assertEqual(incomes.get(wallet__user=self.provider).amount, 3200)
        self.assertEqual(incomes.get(wallet__user=incoming).amount, 4800)

    def test_staff_allocates_refund_without_recovering_old_provider_income(self):
        order, incoming = self.prepare_transfer()
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            data = {'base':base, 'wsBase':ws_base, 'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                    'adminRoot':os.environ['XA_ADMIN_VUE_ROOT'], 'orderId':order.pk, 'orderNo':order.order_no,
                    'bossId':self.accounts[self.boss.pk].pk, 'noRecovery':True,
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'providers':[{'id':self.accounts[user.pk].pk, 'token':issue_account_tokens(self.accounts[user.pk])[0],
                                  'burden':burden, 'net':net, 'view':view, 'escortNo':user.escort_profile.escort_no}
                                 for user,burden,net,view in ((self.provider,0,320,'orders'), (incoming,400,80,'reports'))]}
            result = subprocess.run(['node',str(repo/'scripts/negotiated-refund-three-role-realtime.cjs')],
                input=json.dumps(data),cwd=repo,text=True,encoding='utf-8',capture_output=True,timeout=70)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'PARTIALLY_REFUNDED'))
        refund = order.refund_records.get()
        self.assertEqual((refund.amount, refund.recovered_amount), (5000, 0))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 3200)
        self.assertEqual(Wallet.objects.get(user=incoming).balance, 800)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 16000)
        self.assertFalse(refund.recovery_lines.exists())

    def test_staff_allocates_referral_share_in_live_negotiation(self):
        from club_accounts.models import ClubAccount
        from wallet.services import get_wallet
        inviter = ClubAccount.objects.create_account(username='live-transfer-inviter', account_type='BOSS')
        boss_account = self.accounts[self.boss.pk]
        boss_account.inviter = inviter
        boss_account.inviter_commission_rate = 50
        boss_account.save(update_fields=['inviter', 'inviter_commission_rate'])
        order, incoming = self.prepare_transfer()
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            data = {'base':base, 'wsBase':ws_base, 'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                    'adminRoot':os.environ['XA_ADMIN_VUE_ROOT'], 'orderId':order.pk, 'orderNo':order.order_no,
                    'bossId':boss_account.pk, 'inviter':True,
                    'bossToken':issue_account_tokens(boss_account)[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'providers':[{'id':self.accounts[user.pk].pk, 'token':issue_account_tokens(self.accounts[user.pk])[0],
                                  'burden':burden, 'net':net, 'view':view, 'escortNo':user.escort_profile.escort_no}
                                 for user,burden,net,view in ((self.provider,100,220,'orders'), (incoming,300,180,'reports'))]}
            result = subprocess.run(['node',str(repo/'scripts/negotiated-refund-three-role-realtime.cjs')],
                input=json.dumps(data),cwd=repo,text=True,encoding='utf-8',capture_output=True,timeout=70)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'PARTIALLY_REFUNDED'))
        allocation = order.refund_records.get().negotiated_allocation
        referral = next(row for row in allocation if row['role'] == 'INVITER')
        self.assertEqual((referral['account_id'], referral['entitlement'], referral['amount'], referral['retained']),
                         (inviter.pk, 1000, 500, 500))
        self.assertEqual(get_wallet(account=inviter).balance, 500)
        self.assertEqual(order.transactions.get(wallet__account=inviter, tx_type='INCOME').amount, 500)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 16000)
        inviter_client = APIClient()
        inviter_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(inviter)[0])
        notices = self.ok(inviter_client.get('/api/messages/'))['messages']
        notices = [row for row in notices if row['title'] == '订单部分退款' and order.order_no in row['preview']]
        self.assertEqual(len(notices), 1)
        self.assertIn('推荐收益变化', notices[0]['preview'])
        self.assertEqual(notices[0]['action_url'], '/pages/wallet/index')
        self.assertIsNone(notices[0]['related_order_id'])
        result = subprocess.run(['node', str(repo/'scripts/native-reservation-notice-live.cjs')],
            input=json.dumps({'base':self.live_server_url, 'account':inviter.pk,
                'token':issue_account_tokens(inviter)[0], 'message':notices[0]['id'],
                'expectedTitle':'订单部分退款', 'expectedPreview':'推荐收益变化',
                'expectedTarget':'/pages/wallet/index', 'expectedActionText':'查看钱包'}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        wallet = self.ok(inviter_client.get('/api/wallet/transactions/', {'tx_type':'INCOME'}))
        self.assertEqual(wallet['balance'], 500)
        rows = [row for row in wallet['transactions'] if row['is_referral_income']]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['amount'], 500)

    def test_transferred_provider_who_is_inviter_gets_one_clear_notice(self):
        boss_account = self.accounts[self.boss.pk]
        boss_account.inviter = self.accounts[self.provider.pk]
        boss_account.inviter_commission_rate = 50
        boss_account.save(update_fields=['inviter', 'inviter_commission_rate'])
        order, incoming = self.prepare_transfer()
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            data = {'base':base, 'wsBase':ws_base, 'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                    'adminRoot':os.environ['XA_ADMIN_VUE_ROOT'], 'orderId':order.pk, 'orderNo':order.order_no,
                    'bossId':boss_account.pk, 'inviter':True, 'sameInviter':True,
                    'bossToken':issue_account_tokens(boss_account)[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'providers':[{'id':self.accounts[user.pk].pk, 'token':issue_account_tokens(self.accounts[user.pk])[0],
                                  'burden':burden, 'net':net, 'view':view, 'escortNo':user.escort_profile.escort_no}
                                 for user,burden,net,view in ((self.provider,100,220,'orders'), (incoming,300,180,'reports'))]}
            result = subprocess.run(['node',str(repo/'scripts/negotiated-refund-three-role-realtime.cjs')],
                input=json.dumps(data),cwd=repo,text=True,encoding='utf-8',capture_output=True,timeout=70)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.login(self.provider)
        notices = [row for row in self.ok(self.client.get('/api/messages/'))['messages']
                   if row['title'] == '订单部分退款' and order.order_no in row['preview']]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['related_order_id'], order.pk)
        self.assertIn('收益（含推荐收益）变化', notices[0]['preview'])
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 2700)
        rows = self.ok(self.client.get('/api/wallet/transactions/', {'tx_type':'INCOME'}))['transactions']
        self.assertEqual(sum(row['amount'] for row in rows if row['is_referral_income']), 500)
        self.assertEqual(sum(row['amount'] for row in rows if not row['is_referral_income']), 2200)

    def test_team_transfer_negotiation_updates_three_providers_and_dashboard(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.models import Order
        from orders.tests.factories import make_provider
        teammate = make_provider(display_name='隔离留队陪玩')
        incoming = make_provider(display_name='隔离接替陪玩')
        for user in (teammate, incoming):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            user.escort_profile.account = self.accounts[user.pk]
            user.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_ids':[self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk],
            'client_request_id':'team-live-negotiation'}, format='json'))
        order = Order.objects.get(pk=created['id'])
        for user in (self.provider, teammate):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                                     {'entry_image':flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers':[{
            'old_provider_id':self.accounts[self.provider.pk].pk, 'new_provider_id':self.accounts[incoming.pk].pk,
            'split_type':'PERCENT', 'split_value':40, 'penalty_amount':0}], 'reason':'隔离双陪转单'}, format='json'))
        for user in (self.provider, teammate, incoming):
            self.login(user)
            reports = self.ok(self.client.get('/api/wallet/reports/'))
            if not any(row['order_id'] == order.pk for row in reports):
                self.ok(self.client.post('/api/wallet/reports/', {'order_id':order.pk}, format='json'))
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            data = {'base':base, 'wsBase':ws_base, 'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                    'adminRoot':os.environ['XA_ADMIN_VUE_ROOT'], 'orderId':order.pk, 'orderNo':order.order_no,
                    'bossId':self.accounts[self.boss.pk].pk, 'team':True, 'initialTotal':'2000', 'finalTotal':'1500',
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'providers':[{'id':self.accounts[user.pk].pk, 'token':issue_account_tokens(self.accounts[user.pk])[0],
                                  'burden':burden, 'net':net, 'initial':initial, 'view':'orders',
                                  'escortNo':user.escort_profile.escort_no}
                                 for user,burden,net,initial in ((self.provider,100,220,320),
                                     (teammate,100,700,800), (incoming,200,280,480))]}
            result = subprocess.run(['node',str(repo/'scripts/negotiated-refund-three-role-realtime.cjs')],
                input=json.dumps(data),cwd=repo,text=True,encoding='utf-8',capture_output=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'PARTIALLY_REFUNDED'))
        self.assertEqual(order.refund_records.get().amount, 5000)
        self.login(self.boss)
        boss_notices = [row for row in self.ok(self.client.get('/api/messages/'))['messages']
                        if row['related_order_id'] == order.pk and row['title'] == '订单部分退款']
        self.assertEqual(len(boss_notices), 1)
        self.assertIn('已终止，已退还', boss_notices[0]['preview'])
        for user, net in ((self.provider,2200), (teammate,7000), (incoming,2800)):
            self.assertEqual(Wallet.objects.get(user=user).balance, net)
            self.login(user)
            report = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['order_id'] == order.pk)
            self.assertEqual((report['order_status'], report['can_edit'], report['can_submit']), ('CANCELLED', False, False))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 6000)
        self.assertEqual(sum(order.transactions.filter(status='SUCCESS',
            tx_type__in=['PAY','REFUND','INCOME','SHOP_INCOME']).values_list('amount', flat=True)), 0)

    def test_team_negotiation_preserves_coupon_points_and_vip(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from coupons.models import Coupon, UserCoupon
        from engagement.models import ConsumptionConfirmation, MemberProfile, PointLedger
        from orders.models import Order
        from orders.tests.factories import make_boss_type, make_provider
        teammate, incoming, inviter = (make_provider(display_name=name) for name in ('隔离留队', '隔离接替', '隔离推荐人'))
        for user in (teammate, incoming, inviter):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            user.escort_profile.account = self.accounts[user.pk]
            user.escort_profile.save(update_fields=['account'])
        vip = make_boss_type(discount_rate=100)
        boss_account = self.accounts[self.boss.pk]
        boss_account.inviter, boss_account.inviter_commission_rate, boss_account.boss_type = self.accounts[inviter.pk], 50, vip
        boss_account.save(update_fields=['inviter', 'inviter_commission_rate', 'boss_type'])
        self.boss.inviter, self.boss.inviter_commission_rate, self.boss.boss_type = inviter, 50, vip
        self.boss.save(update_fields=['inviter', 'inviter_commission_rate', 'boss_type'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        coupon = Coupon.objects.create(name='隔离双陪协商券', discount_type='DIRECT', amount=1000)
        self.ok(self.recharge())
        self.login(self.boss)
        self.ok(self.client.post(f'/api/coupons/{coupon.pk}/claim/'))
        voucher = UserCoupon.objects.get(account=boss_account, coupon=coupon)
        created = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_ids':[self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk],
            'user_coupon_id':voucher.pk, 'client_request_id':'team-negotiation-benefits-live'}, format='json'))
        order = Order.objects.get(pk=created['id'])
        self.assertEqual((order.amount, order.coupon_discount), (19000, 1000))
        for user in (self.provider, teammate):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers':[{
            'old_provider_id':self.accounts[self.provider.pk].pk, 'new_provider_id':self.accounts[incoming.pk].pk,
            'split_type':'PERCENT', 'split_value':40, 'penalty_amount':0}], 'reason':'隔离双陪转单'}, format='json'))
        for user in (self.provider, teammate, incoming):
            self.login(user)
            reports = self.ok(self.client.get('/api/wallet/reports/'))
            if not any(row['order_id'] == order.pk for row in reports):
                self.ok(self.client.post('/api/wallet/reports/', {'order_id':order.pk}, format='json'))
        profile = MemberProfile.objects.get(account=boss_account)
        points_before = profile.points_balance
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            data = {'base':base, 'wsBase':ws_base, 'root':os.environ['XA_PROVIDER_VUE_ROOT'],
                    'adminRoot':os.environ['XA_ADMIN_VUE_ROOT'], 'orderId':order.pk, 'orderNo':order.order_no,
                    'bossId':boss_account.pk, 'team':True, 'teamInviter':True, 'initialTotal':'1900',
                    'finalTotal':'950', 'refundCoins':'950', 'recoveryCoins':'304',
                    'bossToken':issue_account_tokens(boss_account)[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'providers':[{'id':self.accounts[user.pk].pk, 'token':issue_account_tokens(self.accounts[user.pk])[0],
                                  'burden':burden, 'net':net, 'initial':initial, 'view':'orders',
                                  'escortNo':user.escort_profile.escort_no}
                                 for user,burden,net,initial in ((self.provider,304,0,304),
                                     (teammate,0,760,760), (incoming,456,0,456))]}
            result = subprocess.run(['node',str(repo/'scripts/negotiated-refund-three-role-realtime.cjs')],
                input=json.dumps(data),cwd=repo,text=True,encoding='utf-8',capture_output=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        order.refresh_from_db(); voucher.refresh_from_db(); profile.refresh_from_db(); boss_account.refresh_from_db()
        self.assertEqual((order.status, voucher.status, profile.points_balance, boss_account.boss_type_id),
                         ('CANCELLED', 'USED', points_before, vip.pk))
        self.login(self.boss)
        receipt = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['refund_summary']['records'][0]
        self.assertEqual(receipt['coupon_results'], [{'name':'隔离双陪协商券', 'outcome':'NOT_RETURNED'}])
        self.login(teammate)
        provider_receipt = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['refund_summary']['records'][0]
        self.assertNotIn('coupon_results', provider_receipt)
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertEqual(sum(order.point_allocations.values_list('refunded_amount', flat=True)), 9500)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11500)
        self.assertEqual(Wallet.objects.get(user=inviter).balance, 950)
