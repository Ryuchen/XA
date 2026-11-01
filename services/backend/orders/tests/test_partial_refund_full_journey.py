"""Real authenticated API journey for a settled order and successive refunds."""
import tempfile
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from orders.models import Order, OrderRefund
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from support.models import SupportContactCard
from site_messages.models import Message
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PartialRefundFullJourneyTest(APITestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        setting = override_settings(MEDIA_ROOT=media.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.boss, self.provider = make_user(), make_provider()
        self.admin = make_console_user(['dashboard:view', 'wallet:recharge', 'report:view',
                                        'report:audit', 'order:dispatch', 'order:view',
                                        'order:refund', 'order:settle'])
        self.reviewer = make_console_user(['report:view'])
        self.accounts = {u.pk: get_or_create_account_for_legacy_user(u)
                         for u in (self.boss, self.provider, self.admin, self.reviewer)}
        for user in (self.admin, self.reviewer):
            membership = user.admin_membership
            membership.account = self.accounts[user.pk]
            membership.save(update_fields=['account'])
        profile = self.provider.escort_profile
        profile.account = self.accounts[self.provider.pk]
        profile.save(update_fields=['account'])
        self.service = make_service(price=10000, commission_rate=20)
        self.card = SupportContactCard.objects.create(name='客服', wecom_corp_id='wwtest',
            wecom_service_url='https://work.weixin.qq.com/kfid/kf-test')

    def login(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def dashboard(self, amount, paid_orders, blocked=0, pending=0):
        self.login(self.admin)
        data = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual(data['paid_amount_total'], amount)
        self.assertEqual(data['paid_order_total'], paid_orders)
        self.assertEqual(data['pending_report_total'], pending)
        self.assertEqual(data['pending_report_order_blocked_total'], blocked)
        blocked_reports = self.ok(self.client.get('/api/admin/reports/', {'order_blocked': '1'}))
        self.assertEqual(blocked_reports['total'], blocked)

    def check_views(self, order, report, refunded, remaining, count, provider_net, status, payment):
        for user in (self.boss, self.provider):
            self.login(user)
            detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
            self.assertEqual((detail['status'], detail['payment_status']), (status, payment))
            if user == self.boss:
                node = shutil.which('node')
                if not node:
                    self.fail('Node runtime required to check the native order presentation')
                repo = Path(settings.BASE_DIR).parent.parent
                result = subprocess.run([node, str(repo / 'scripts/native-order-refund-live.cjs')],
                    input=json.dumps({'order': detail, 'statusText': '已完成' if status == 'COMPLETED' else '已取消',
                                      'refunded': refunded, 'remaining': remaining, 'count': count}, cls=DjangoJSONEncoder),
                    text=True, encoding='utf-8', capture_output=True, timeout=15, cwd=repo)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            listing = self.ok(self.client.get('/api/orders/orders/', {'status': status.lower()}))
            listed = next(row for row in listing if row['id'] == order.pk)
            self.assertEqual(listed['refund_summary']['amount'], refunded)
            summary = detail['refund_summary']
            self.assertEqual((summary['amount'], summary['remaining_amount'], summary['refund_count']),
                             (refunded, remaining, count))
            wallet = self.ok(self.client.get('/api/wallet/transactions/'))
            self.assertEqual(wallet['balance'], 21000 - remaining if user == self.boss else provider_net)
            if user == self.provider:
                self.assertEqual((summary['recovered_amount'], summary['net_income']),
                                 (8000 - provider_net, provider_net))
                income = [row for row in wallet['transactions'] if row['tx_type'] == 'INCOME']
                self.assertEqual(sum(row['amount'] for row in income), provider_net)
                self.assertEqual(sum(row['is_reversal'] for row in income), count)
                reports = self.ok(self.client.get('/api/wallet/reports/'))
                row = next(r for r in reports if r['id'] == report.pk)
                self.assertEqual((row['status'], row['order_status'], row['payment_status']),
                                 ('APPROVED', status, payment))
                self.assertEqual(row['refund_summary']['net_income'], provider_net)
                self.assertEqual(row['refund_summary']['amount'], refunded)
                node = shutil.which('node')
                if not node:
                    self.fail('Node runtime required to check provider refund presentation')
                repo = Path(settings.BASE_DIR).parent.parent
                result = subprocess.run([
                    node, '--test', '--test-name-pattern=real provider refund response',
                    str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs'),
                    str(repo / 'apps/provider-miniapp/tests/report-flow.cjs'),
                ], env={**os.environ, 'XA_REAL_PROVIDER_REFUND': json.dumps(
                    {'order': detail, 'report': row}, cls=DjangoJSONEncoder)},
                    text=True, encoding='utf-8', capture_output=True, timeout=20, cwd=repo)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(OrderRefund.objects.filter(order=order).count(), count)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)
        self.login(self.reviewer)
        reports = self.ok(self.client.get('/api/admin/reports/'))
        listed = next(row for row in reports['list'] if row['id'] == report.pk)
        detail = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        for row in (listed, detail):
            self.assertEqual((row['order_payment_status'], row['payout_amount']), (payment, 8000))
            self.assertNotIn('refund_summary', row)

    def test_zero_payment_coupon_order_can_complete_and_settle(self):
        from coupons.models import Coupon, UserCoupon
        coupon = Coupon.objects.create(name='全额优惠券', discount_type='DIRECT', amount=10000)
        self.login(self.admin)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 20000, 'gift_amount': 0,
            'trade_no': 'zero-coupon-receipt', 'proof_image': screenshot(),
            'client_request_id': 'zero-coupon-recharge',
        }, format='multipart'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/coupons/{coupon.pk}/claim/'))
        voucher = UserCoupon.objects.get(account=self.accounts[self.boss.pk], coupon=coupon)
        created = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'user_coupon_id': voucher.pk, 'client_request_id': 'zero-coupon-order',
        }))
        self.assertEqual(created['amount'], 0)
        order = Order.objects.get(pk=created['id'])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertTrue(detail['can_aftersale'])
        case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '本地零实付售后咨询', 'client_request_id': 'zero-coupon-aftersale',
        }, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/resolve/", {'resolution': '已处理咨询'}, format='json'))
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            process_order(order.pk)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual(detail['settlement_status'], 'SETTLED')
        report_row = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)
        self.assertEqual((report_row['settlement_status'], report_row['payout_amount']), ('SETTLED', 0))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type__in=['INCOME', 'SHOP_INCOME', 'REFUND']).exists())
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'USED')
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertTrue(detail['can_aftersale'])
        second = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '本地零实付结算后咨询', 'client_request_id': 'zero-coupon-after-settlement',
        }, format='json'))
        self.assertEqual(second['status'], 'OPEN')

    def test_discount_coupon_is_reported_only_after_cumulative_full_refund(self):
        from coupons.models import Coupon, UserCoupon
        coupon = Coupon.objects.create(name='退款后返回券', discount_type='DIRECT', amount=1000)
        self.login(self.admin)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 20000, 'gift_amount': 0,
            'trade_no': 'coupon-journey-receipt', 'proof_image': screenshot(),
            'client_request_id': 'coupon-journey-recharge',
        }, format='multipart'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/coupons/{coupon.pk}/claim/'))
        voucher = UserCoupon.objects.get(account=self.accounts[self.boss.pk], coupon=coupon)
        created = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'user_coupon_id': voucher.pk, 'client_request_id': 'coupon-journey-order',
        }))
        order = Order.objects.get(pk=created['id'])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            process_order(order.pk)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'refund_amount': 2500, 'reason': '协商部分退款', 'plan_token': preview['plan_token'],
            'client_request_id': 'coupon-journey-partial',
        }, format='json'))
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'USED')
        self.login(self.boss)
        partial = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertNotIn('coupon_results', partial['refund_summary']['records'][0])
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'reason': '协商退款剩余款', 'plan_token': preview['plan_token'],
            'client_request_id': 'coupon-journey-final',
        }, format='json'))
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'UNUSED')
        self.login(self.boss)
        final = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        records = final['refund_summary']['records']
        self.assertEqual(records[0]['coupon_results'], [{'name': '退款后返回券', 'outcome': 'RETURNED'}])
        self.assertNotIn('coupon_results', records[1])
        node = shutil.which('node')
        if not node:
            self.fail('Node runtime required to check the native refund presentation')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-coupon-refund-live.cjs')],
            input=json.dumps({'order': final, 'name': '退款后返回券', 'outcome': 'RETURNED'}, cls=DjangoJSONEncoder),
            text=True, encoding='utf-8', capture_output=True, timeout=15, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.login(self.provider)
        provider_order = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertNotIn('coupon_results', provider_order['refund_summary']['records'][0])

    def test_provider_who_invited_boss_sees_settled_service_income(self):
        self.boss.inviter = self.provider
        self.boss.inviter_commission_rate = 50
        self.boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        self.login(self.admin)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 20000, 'gift_amount': 0,
            'trade_no': 'inviter-journey-receipt', 'proof_image': screenshot(),
            'client_request_id': 'inviter-journey-recharge',
        }, format='multipart'))
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'inviter-journey-order',
        }))
        order = Order.objects.get(pk=created['id'])
        self.assertEqual((order.provider_income, order.inviter_commission), (8000, 1000))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            process_order(order.pk)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 9000)
        self.login(self.provider)
        initial_wallet = self.ok(self.client.get('/api/wallet/transactions/'))
        self.assertEqual(initial_wallet['total_income'], 9000)
        income = [row for row in initial_wallet['transactions'] if row['tx_type'] == 'INCOME']
        self.assertEqual(sorted((row['amount'], row['is_referral_income']) for row in income),
                         [(1000, True), (8000, False)])
        own = self.ok(self.client.get('/api/wallet/reports/'))
        self.assertEqual(next(row for row in own if row['id'] == report.pk)['settlement_status'], 'SETTLED')
        self.login(self.reviewer)
        admin = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual((admin['settlement_status'], admin['payout_amount']), ('SETTLED', 8000))
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2506}))
        with self.captureOnCommitCallbacks(execute=True):
            self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
                'refund_amount': 2506, 'reason': '隔离分佣及单钱部分退款',
                'resolution': '部分退款', 'client_request_id': 'inviter-partial-refund',
                'plan_token': preview['plan_token'],
            }, format='json'))
        self.assertEqual(Message.objects.filter(recipient=self.provider, related_order_id=order.pk,
                                                title='订单部分退款').count(), 1)
        notice = Message.objects.get(recipient=self.boss, related_order_id=order.pk, title='订单部分退款')
        self.assertIn('250.6 兴安币至余额', notice.preview)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 6745)
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        refunded_wallet = self.ok(self.client.get('/api/wallet/transactions/'))
        self.assertEqual(refunded_wallet['total_income'], 6745)
        income = [row for row in refunded_wallet['transactions'] if row['tx_type'] == 'INCOME']
        self.assertEqual(sorted((row['amount'], row['is_referral_income']) for row in income),
                         [(-2004, False), (-251, True), (1000, True), (8000, False)])
        node = shutil.which('node')
        if not node:
            self.fail('Node runtime required to check the provider wallet presentation')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/wallet-realtime.cjs')],
            env={**os.environ, 'XA_REAL_WALLET_JSON': json.dumps(refunded_wallet, cls=DjangoJSONEncoder)},
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        own = self.ok(self.client.get('/api/wallet/reports/'))
        report_detail = next(row for row in own if row['id'] == report.pk)
        listed = next(row for row in self.ok(self.client.get('/api/orders/orders/', {'status': 'completed'})) if row['id'] == order.pk)
        for summary in (detail['refund_summary'], report_detail['refund_summary'], listed['refund_summary']):
            self.assertEqual((summary['recovered_amount'], summary['net_income']), (2004, 5996))
            self.assertEqual((summary['referral_recovered_amount'], summary['referral_net_income']), (251, 749))
        # Older wallets can have only the legacy-user mapping. Batch lists and
        # the individual detail must resolve the same owner and actual flows.
        Wallet.objects.filter(user=self.provider).update(account=None)
        legacy_detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['refund_summary']
        legacy_list = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)['refund_summary']
        self.assertEqual(legacy_list['net_income'], legacy_detail['net_income'])
        self.assertEqual(legacy_list['referral_net_income'], legacy_detail['referral_net_income'])
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'player_dashboard:view']
        role.save(update_fields=['permissions'])
        self.login(self.admin)
        dashboard = self.ok(self.client.get('/api/admin/dashboard/player'))
        self.assertEqual(dashboard['rank']['income'][0]['value'], 5996)
        self.login(self.reviewer)
        admin = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual((admin['order_payment_status'], admin['payout_amount']), ('PARTIALLY_REFUNDED', 8000))
        # Historical records may lack the referral marker; do not infer the
        # split from the amount and present it as a verified income figure.
        Transaction.objects.filter(order=order, remark__startswith='推荐分佣：').update(remark='历史收入')
        self.login(self.provider)
        ambiguous = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['refund_summary']
        self.assertTrue(ambiguous['income_breakdown_unavailable'])
        self.assertIsNone(ambiguous['net_income'])
        self.assertIsNone(ambiguous['recovered_amount'])
        self.login(self.admin)
        dashboard = self.ok(self.client.get('/api/admin/dashboard/player'))
        self.assertEqual(dashboard['rank']['income'], [])
        self.assertEqual(dashboard['income_review'], {'order_count': 1, 'provider_count': 1, 'unidentified_count': 0})
        self.assertEqual(dashboard['rank']['amount'][0]['value'], 7494)

    def test_legacy_manual_report_is_included_in_wallet_and_workbench_income(self):
        # Existing historical unlinked report: approval and wallet reads use the
        # same real paths as staff and provider, without synthesizing ledger rows.
        report = ProviderReport.objects.create(provider=self.provider,
            provider_account=self.accounts[self.provider.pk], game_name='历史手工报单', amount=10000,
            commission_rate=20)
        self.login(self.admin)
        approved = self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.assertEqual(approved['payout_amount'], 8000)
        self.login(self.provider)
        wallet = self.ok(self.client.get('/api/wallet/transactions/'))
        income = [row for row in wallet['transactions'] if row['tx_type'] == 'INCOME']
        self.assertEqual(wallet['total_income'], 8000)
        self.assertEqual(len(income), 1)
        self.assertEqual((income[0]['amount'], income[0]['is_referral_income']), (8000, False))
        self.assertIn('报单入账', income[0]['remark'])
        stats = self.ok(self.client.get('/api/users/provider-stats/'))
        self.assertEqual(stats['total_income'], wallet['total_income'])

    def test_refunded_report_list_batches_real_income_evidence(self):
        self.login(self.admin)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 50000, 'gift_amount': 0,
            'trade_no': 'batch-refund-receipt', 'proof_image': screenshot(),
            'client_request_id': 'batch-refund-recharge',
        }, format='multipart'))
        report_counts, order_counts = [], []
        for n in range(2):
            self.login(self.boss)
            created = self.ok(self.client.post('/api/orders/create/', {
                'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
                'support_contact_id': self.card.pk, 'client_request_id': f'batch-refund-order-{n}',
            }))
            order = Order.objects.get(pk=created['id'])
            self.login(self.provider)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
            report = ProviderReport.objects.get(order=order, provider=self.provider)
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
            with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
                process_order(order.pk)
            preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
            self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
                'refund_amount': 2500, 'reason': '隔离退款批量验收', 'resolution': '部分退款',
                'client_request_id': f'batch-refund-{n}', 'plan_token': preview['plan_token'],
            }, format='json'))
            self.login(self.provider)
            with CaptureQueriesContext(connection) as queries:
                rows = self.ok(self.client.get('/api/wallet/reports/'))
            report_counts.append(len(queries))
            self.assertEqual(len(rows), n + 1)
            self.assertTrue(all(row['refund_summary']['net_income'] == 6000 for row in rows))
            with CaptureQueriesContext(connection) as queries:
                orders = self.ok(self.client.get('/api/orders/orders/', {'status': 'completed'}))
            order_counts.append(len(queries))
            self.assertEqual(len(orders), n + 1)
            self.assertTrue(all(row['refund_summary']['net_income'] == 6000 for row in orders))
        self.assertEqual(report_counts[0], report_counts[1], report_counts)
        self.assertEqual(order_counts[0], order_counts[1], order_counts)

    def test_real_order_approval_partial_then_remaining_refund(self):
        self.login(self.admin)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 20000, 'gift_amount': 1000,
            'trade_no': 'journey-receipt', 'proof_image': screenshot(),
            'client_request_id': 'journey-recharge',
        }, format='multipart'))
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'journey-order',
        }))
        order = Order.objects.get(pk=created['id'])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.dashboard(10000, 1, pending=1)
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            process_order(order.pk)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.dashboard(10000, 1)

        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        partial_request = {
            'refund_amount': 2500, 'reason': '服务协商部分退款', 'resolution': '按协商退款',
            'client_request_id': 'journey-partial', 'plan_token': preview['plan_token'],
        }
        with self.captureOnCommitCallbacks(execute=True):
            self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', partial_request, format='json'))
        notices = Message.objects.filter(related_order_id=order.pk, title='订单部分退款')
        self.assertEqual(set(notices.values_list('recipient_account_id', flat=True)),
                         {self.accounts[self.boss.pk].pk, self.accounts[self.provider.pk].pk})
        self.assertEqual(notices.count(), 2)
        self.assertIn('250', notices.get(recipient=self.boss).preview)
        with self.captureOnCommitCallbacks(execute=True):
            self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', partial_request, format='json'))
        self.assertEqual(notices.count(), 2)
        self.check_views(order, report, 2500, 7500, 1, 6000, 'COMPLETED', 'PARTIALLY_REFUNDED')
        self.dashboard(7500, 1)

        self.login(self.boss)
        case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '继续申请退还余款', 'client_request_id': 'journey-after-sale',
        }, format='json'))
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        remainder_request = {
            'resolution': '退还剩余款项',
            'client_request_id': 'journey-remainder', 'plan_token': preview['plan_token'],
        }
        with self.captureOnCommitCallbacks(execute=True):
            self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/refund/', remainder_request, format='json'))
        notices = Message.objects.filter(related_order_id=order.pk, title='订单已退款')
        self.assertEqual(set(notices.values_list('recipient_account_id', flat=True)),
                         {self.accounts[self.boss.pk].pk, self.accounts[self.provider.pk].pk})
        self.assertEqual(notices.count(), 2)
        self.assertIn('750', notices.get(recipient=self.boss).preview)
        with self.captureOnCommitCallbacks(execute=True):
            self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/refund/', remainder_request, format='json'))
        self.assertEqual(notices.count(), 2)
        self.check_views(order, report, 10000, 0, 2, 0, 'CANCELLED', 'REFUNDED')
        self.dashboard(0, 0)
