"""The boss's committed in-service case wins over waiting transfer and completion."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection, connections, transaction
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from orders.tests.test_negotiated_transfer_refund import NegotiatedTransferRefundTest
from wallet.models import Transaction, Wallet
from common.mysql_test_support import mysql_locking_statements


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
@override_settings(ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NegotiatedAfterSaleMySQLRaceTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    prepare_transfer = NegotiatedTransferRefundTest.prepare_transfer

    def aftersale_waiting_for(self, order, operation):
        token = issue_account_tokens(self.accounts[self.boss.pk])[0]

        def submit():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                return client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                    'reason': '服务安排需要客服处理', 'client_request_id': 'after-operation-case',
                }, format='json').data
            finally:
                close_old_connections()

        observer = connections['default'].copy()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    self.ok(operation())
                    future = pool.submit(submit)
                    deadline = monotonic() + 15
                    while monotonic() < deadline:
                        if mysql_locking_statements(observer, 'orders_order'):
                            break
                        sleep(.03)
                    else:
                        self.fail('售后请求未等待订单锁')
                result = future.result(timeout=25)
        finally:
            observer.close()
        self.assertEqual(result['code'], 0, result)
        return result['data']

    def test_transfer_commits_first_then_aftersale_attaches_to_latest_participant(self):
        order, incoming = self.prepare_transfer()
        replacement = make_provider(display_name='并发转单后接手陪玩')
        account = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = account
        replacement.escort_profile.save(update_fields=['account'])
        payload = {'transfers': [{'old_provider_id': self.accounts[incoming.pk].pk,
                    'new_provider_id': account.pk, 'split_type': 'PERCENT', 'split_value': 40,
                    'penalty_amount': 0}], 'reason': '并发先转单'}
        self.login(self.admin)
        case = self.aftersale_waiting_for(order, lambda: self.client.post(
            f'/api/admin/orders/{order.pk}/transfer/', payload, format='json'))
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('IN_SERVICE', account.pk))
        self.assertEqual(order.aftersales.get(pk=case['id']).status, 'OPEN')
        self.assertTrue(order.providers.filter(provider_account=account, released_at__isnull=True).exists())
        active = APIClient()
        active.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        detail = self.ok(active.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual(detail['completion_block_reason'], '售后处理中，暂不能结单，请等待客服处理')
        self.assertNotIn('COMPLETE', detail['allowed_actions'])
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 0)

    def test_completion_commits_first_then_boss_can_open_completed_order_aftersale(self):
        order, incoming = self.prepare_transfer()
        self.login(self.provider)
        report = order.provider_reports.get(provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind': 'COMPLETION', 'image': flow.screenshot()}, format='multipart'))
        self.login(incoming)
        case = self.aftersale_waiting_for(order, lambda: self.client.post(
            f'/api/orders/orders/{order.pk}/complete/', {'completion_image': flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'COMPLETED')
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(order.aftersales.get(pk=case['id']).status, 'OPEN')
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 3200)
        self.assertEqual(Wallet.objects.get(user=incoming).balance, 4800)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())

    def test_committed_aftersale_blocks_waiting_completion_and_transfer(self):
        order, incoming = self.prepare_transfer()
        replacement = make_provider(display_name='并发接手陪玩')
        replacement_account = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = replacement_account
        replacement.escort_profile.save(update_fields=['account'])
        admin_token = issue_account_tokens(self.accounts[self.admin.pk])[0]
        incoming_token = issue_account_tokens(self.accounts[incoming.pk])[0]
        before_wallets = list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        before_transactions = list(Transaction.objects.order_by('pk').values_list('pk', flat=True))
        transfer = {'transfers': [{'old_provider_id': self.accounts[incoming.pk].pk,
                    'new_provider_id': replacement_account.pk, 'split_type': 'PERCENT', 'split_value': 40,
                    'penalty_amount': 0}], 'reason': '并发转单'}

        def submit(token, action):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                if action == 'complete':
                    return client.post(f'/api/orders/orders/{order.pk}/complete/',
                                       {'completion_image': flow.screenshot()}, format='multipart').data
                return client.post(f'/api/admin/orders/{order.pk}/transfer/', transfer, format='json').data
            finally:
                close_old_connections()

        observer = connections['default'].copy()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                with transaction.atomic():
                    self.login(self.boss)
                    case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                        'reason': '服务中提交售后', 'client_request_id': 'negotiated-race-case',
                    }, format='json'))
                    futures = [pool.submit(submit, incoming_token, 'complete'),
                               pool.submit(submit, admin_token, 'transfer')]
                    deadline = monotonic() + 15
                    while monotonic() < deadline:
                        waits = mysql_locking_statements(observer, 'orders_order')
                        if waits >= 2:
                            break
                        sleep(.03)
                    else:
                        self.fail('两项请求未同时等待订单锁')
                results = [future.result(timeout=25) for future in futures]
        finally:
            observer.close()
        for result in results:
            self.assertEqual(result['code'], 400, result)
            self.assertIn('售后处理中', result['msg'])
        order.refresh_from_db()
        self.assertEqual(order.status, 'IN_SERVICE')
        self.assertIsNone(order.settled_at)
        self.assertEqual(order.aftersales.get(pk=case['id']).status, 'OPEN')
        self.assertFalse(order.providers.filter(provider_account=replacement_account).exists())
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')), before_wallets)
        self.assertEqual(list(Transaction.objects.order_by('pk').values_list('pk', flat=True)), before_transactions)
