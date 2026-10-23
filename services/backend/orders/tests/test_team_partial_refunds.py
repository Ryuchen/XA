"""Unequal team refunds use posted credits, not team/approval display totals."""
from datetime import timedelta
from fractions import Fraction
import tempfile
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.db.models import Sum
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITestCase, APITransactionTestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.models import ConsumptionConfirmation, MemberProfile, OrderPointAllocation, PointLedger, RewardClaim
from orders.models import AfterSale, Order, OrderProvider, OrderRefund, OrderStatusLog, RefundRecoveryLine
from orders.notifier import notify_order_update
from orders.tests import test_partial_refunds as partial
from orders.tests import test_settled_refund_concurrency as concurrent
from orders.tests.test_release_flow import screenshot
from orders.tests.test_report_realtime import RecordingLayer
from wallet.income import release_due_income
from wallet.models import IncomeHold, ProviderReport, ProviderReportImage, Transaction, Wallet, WithdrawRequest
from wallet.services import get_wallet


class TeamRefundFixtures:
    client_for = partial.PartialRefundTest.client_for
    ok = partial.PartialRefundTest.ok
    preview_amount = partial.PartialRefundTest.preview_amount
    pay_refund = partial.PartialRefundTest.pay_refund

    def setUp(self):
        partial.PartialRefundTest.setUp(self)
        media = tempfile.TemporaryDirectory(prefix='xa-team-refund-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        role = self.admin_user.admin_membership.roles.first()
        role.permissions = list(set(role.permissions) | {'report:view', 'report:audit'})
        role.save(update_fields=['permissions'])
        self.other_account = self.accounts[self.other.pk]
        self.other_wallet = get_wallet(user=self.other, account=self.other_account)
        self.boss_wallet.balance = 500000
        self.boss_wallet.save(update_fields=['balance'])

    def team(self, *, amount=10000, bases=(6500, 3500), incomes=(5200, 2800), hold=72):
        # Shape the immutable fixture snapshots before the real two-report
        # approval/settlement path. No live fixture or database is used.
        order = partial.PartialRefundTest.settled(self, amount=amount, hold=hold,
                                                double=True, perform_settlement=False)
        self.assertEqual(sum(bases), amount)
        self.assertLessEqual(sum(incomes), amount)
        Order.objects.filter(pk=order.pk).update(provider_income=sum(incomes), shop_income=amount-sum(incomes))
        for row, base, income in zip(order.providers.order_by('pk'), bases, incomes):
            row.settlement_base, row.provider_income = base, income
            row.commission_type, row.commission_fixed = 'FIXED', base-income
            row.save(update_fields=['settlement_base', 'provider_income', 'commission_type', 'commission_fixed'])
        reports = list(order.provider_reports.order_by('pk'))
        for report, base in zip(reports, bases):
            report.status, report.amount = 'PENDING', base
            report.entry_image, report.completion_image = screenshot(), screenshot()
            report.save(update_fields=['status', 'amount', 'entry_image', 'completion_image'])
            ProviderReportImage.objects.create(report=report, image=screenshot())
        self.ok(self.admin.post(f'/api/admin/reports/{reports[0].pk}/approve/', {}, format='json'))
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.ok(self.admin.post(f'/api/admin/reports/{reports[1].pk}/approve/', {}, format='json'))
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(list(order.provider_reports.order_by('pk').values_list('payout_amount', flat=True)), list(incomes))
        self.assertEqual(list(self.credits(order).values_list('amount', flat=True)), [*incomes, amount-sum(incomes)])
        return order

    def credits(self, order):
        return Transaction.objects.filter(order=order, status='SUCCESS', reversal_of__isnull=True,
            tx_type__in=['INCOME', 'SHOP_INCOME'], amount__gt=0).order_by('pk')

    def recovered(self, order):
        return [-sum(row.reversals.filter(status='SUCCESS').values_list('amount', flat=True))
                for row in self.credits(order)]

    def full_snapshot(self):
        models = (Wallet, Transaction, IncomeHold, OrderRefund, RefundRecoveryLine, Order, OrderProvider,
                  ProviderReport, AfterSale, OrderStatusLog, WithdrawRequest, MemberProfile, PointLedger,
                  OrderPointAllocation, ConsumptionConfirmation, RewardClaim, type(self.provider.escort_profile))
        return {model._meta.label: list(model.objects.order_by('pk').values()) for model in models}

    def release_own(self, order, wallet):
        hold = IncomeHold.objects.get(income_transaction__order=order, wallet=wallet)
        now = timezone.now()
        self.assertFalse(IncomeHold.objects.filter(released_at=None, cancelled_at=None, release_at__lte=now).exists())
        IncomeHold.objects.filter(pk=hold.pk).update(release_at=now-timedelta(seconds=1))
        self.assertEqual(release_due_income(now=now, limit=1), 1)
        self.assertEqual(release_due_income(now=now, limit=1), 0)
        hold.refresh_from_db()
        return hold

    def frozen_foreign(self):
        order = self.team(bases=(5000, 5000), incomes=(4000, 4000))
        return order, list(IncomeHold.objects.filter(income_transaction__order=order).order_by('pk').values())

    def withdraw(self, account, amount, key):
        return self.client_for(account).post('/api/wallet/withdraw/', {
            'amount': amount, 'payee_method': 'WECHAT', 'payee_account': 'LOCAL_TEST_ONLY',
            'payee_name': '隔离双陪样例', 'client_request_id': key,
        }, format='json')

    def reject_withdraw(self, account):
        row = WithdrawRequest.objects.get(account=account, status='PENDING')
        self.ok(self.admin.post(f'/api/admin/withdrawals/{row.pk}/reject/', {'audit_remark': '隔离测试退回'}, format='json'))


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class TeamPartialRefundTest(TeamRefundFixtures, APITestCase):
    def test_unequal_actual_credits_override_unchanged_team_bases(self):
        order = self.team(incomes=(4000, 3000))
        self.ok(self.pay_refund(order, 2000))
        self.assertEqual(self.recovered(order), [800, 600, 600])
        self.assertEqual(list(order.providers.order_by('pk').values_list('settlement_base', flat=True)), [6500, 3500])

    def test_each_mixed_direction_uses_only_own_hold_or_available_income(self):
        for available in (self.provider_wallet, self.other_wallet):
            with self.subTest(available_wallet=available.pk):
                order = self.team()
                self.release_own(order, available)
                before = {row.pk: (row.balance, row.frozen_amount) for row in Wallet.objects.all()}
                clocks = order.completed_at, order.settled_at
                hold_clocks = dict(order.income_holds.values_list('pk', 'release_at')) if hasattr(order, 'income_holds') else dict(
                    IncomeHold.objects.filter(income_transaction__order=order).values_list('pk', 'release_at'))
                self.ok(self.pay_refund(order, 1000))
                self.assertEqual(self.recovered(order), [520, 280, 200])
                for wallet, income_delta in ((self.provider_wallet, 520), (self.other_wallet, 280)):
                    wallet.refresh_from_db()
                    b, f = before[wallet.pk]
                    self.assertEqual((wallet.balance, wallet.frozen_amount),
                        (b-income_delta, f) if wallet.pk == available.pk else (b, f-income_delta))
                order.refresh_from_db()
                self.assertEqual((order.completed_at, order.settled_at), clocks)
                self.assertEqual(dict(IncomeHold.objects.filter(pk__in=hold_clocks).values_list('pk', 'release_at')), hold_clocks)
                self.ok(self.pay_refund(order))
                self.assertEqual(self.recovered(order), [5200, 2800, 2000])

    def test_one_raw_multiple_refunds_match_independent_midpoint_prefix_and_final_cap(self):
        order = self.team(amount=43, bases=(28, 15), incomes=(23, 12), hold=0)
        credits = list(self.credits(order))
        atoms = sorted((Fraction(2*k+1, 2*row.amount), row.pk, i)
                       for i, row in enumerate(credits) for k in range(row.amount))
        expected = [0, 0, 0]
        for refunded in range(1, 14):
            expected[atoms[refunded-1][2]] += 1
            self.ok(self.pay_refund(order, 1, key=f'raw-{refunded}'))
            self.assertEqual(self.recovered(order), expected)
            self.assertEqual(sum(self.recovered(order)), refunded)
        same_total = self.team(amount=43, bases=(28, 15), incomes=(23, 12), hold=0)
        self.ok(self.pay_refund(same_total, 13))
        self.assertEqual(self.recovered(same_total), expected)
        snapshot = self.full_snapshot()
        rejected = self.pay_refund(order, 31, token='amount-guard')
        self.assertEqual(rejected.data['errorCode'], 'REFUND_INVALID_AMOUNT')
        self.assertEqual(self.full_snapshot(), snapshot)
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order), [23, 12, 8])
        self.assertEqual(sum(OrderRefund.objects.filter(order=order).values_list('amount', flat=True)), 43)

    def test_second_provider_shortfall_rolls_back_every_row_and_protects_foreign_and_withdraw_funds(self):
        foreign, foreign_holds = self.frozen_foreign()
        order = self.team(amount=100000, bases=(65000, 35000), incomes=(52000, 28000))
        self.release_own(order, self.other_wallet)
        # Other provider and platform are sufficient; only the SECOND provider
        # has a shortage, so the first provider's hold must not be consumed.
        self.ok(self.withdraw(self.other_account, 15000, 'team-insufficient-withdraw'))
        token = self.preview_amount(order, 50000)['plan_token']
        before = self.full_snapshot()
        result = self.pay_refund(order, 50000, key='retry-same-intent', token=token)
        self.assertEqual((result.data['code'], result.data['errorCode']), (400, 'REFUND_RECOVERY_INSUFFICIENT'))
        self.assertEqual(before, self.full_snapshot())
        self.reject_withdraw(self.other_account)
        stale_before = self.full_snapshot()
        self.assertEqual(self.pay_refund(order, 50000, key='retry-same-intent', token=token).data['code'], 409)
        self.assertEqual(stale_before, self.full_snapshot())
        self.ok(self.pay_refund(order, 50000, key='retry-same-intent'))
        self.assertEqual(self.recovered(order), [26000, 14000, 10000])
        self.ok(self.pay_refund(order))
        for wallet in (self.provider_wallet, self.other_wallet):
            wallet.refresh_from_db()
            self.assertEqual((wallet.balance, wallet.frozen_amount), (0, 4000))
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()), foreign_holds)
        self.assertEqual(self.recovered(order), [52000, 28000, 20000])

    def test_refund_notifies_both_wallet_owners_once_only_after_commit(self):
        order = self.team()
        with patch('console.views.notify_order_update') as orders, patch('orders.services.notify_wallet_update') as wallets:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                self.ok(self.pay_refund(order, 1000, key='team-committed'))
                orders.assert_not_called(); wallets.assert_not_called()
            for callback in callbacks:
                callback()
            orders.assert_called_once()
            self.assertEqual(wallets.call_count, 4)
            self.assertEqual({call.args[0].pk for call in wallets.call_args_list},
                             {self.boss_wallet.pk, self.provider_wallet.pk, self.other_wallet.pk, self.platform.pk})
            with self.captureOnCommitCallbacks(execute=False) as replay:
                self.ok(self.pay_refund(order, 1000, key='team-committed'))
            self.assertEqual(replay, [])

    def test_http_and_private_events_have_only_each_providers_own_financial_summary(self):
        order = self.team()
        self.ok(self.pay_refund(order, 1000))
        order.refresh_from_db()
        layer = RecordingLayer()
        with patch('orders.notifier.get_channel_layer', return_value=layer):
            notify_order_update(order)
        events = {group: event['data'] for group, event in layer.sent}
        for account, expected, recovered, net in ((self.provider_account, 5200, 520, 4680),
                                                 (self.other_account, 2800, 280, 2520)):
            payload = events[f'account_{account.pk}']
            self.assertEqual(payload['expected_income'], expected)
            self.assertIn('provider', payload)  # provider H5 personal-event discriminator
            self.assertIsInstance(payload['settled_at'], str)
            self.assertIsInstance(payload['aftersale_deadline'], str)
            summary = payload['refund_summary']
            self.assertEqual((summary['recovered_amount'], summary['net_income'], summary['remaining_frozen_amount']),
                             (recovered, net, net))
            self.assertEqual(summary['records'][0]['recovered_amount'], recovered)
            self.assertNotIn('recovery_lines', summary)
            self.assertNotIn('recovery_lines', summary['records'][0])
            response = self.client_for(account).get(f'/api/orders/orders/{order.pk}/')
            self.ok(response)
            http = response.json()['data']
            self.assertEqual(http['expected_income'], payload['expected_income'])
            self.assertEqual(http['refund_summary'], summary)
            self.assertEqual(http['settled_at'], payload['settled_at'])
        boss = events[f'account_{self.boss_account.pk}']
        self.assertNotIn('recovered_amount', boss['refund_summary'])
        self.assertIn('EVALUATE', boss['allowed_actions'])
        self.assertEqual(sum(group == f'account_{self.provider_account.pk}' for group, _ in layer.sent), 1)
        self.assertEqual(sum(group == f'account_{self.other_account.pk}' for group, _ in layer.sent), 1)

    def test_legacy_secondary_map_is_resolved_without_guessing_equal_numeric_ids(self):
        original_account_id = self.other_account.pk
        mapped_account = ClubAccount.objects.create(username='isolated-mapped-secondary', account_type='PROVIDER')
        self.assertNotEqual(mapped_account.pk, self.other.pk)
        LegacyAccountMap.objects.filter(legacy_user_id=self.other.pk).update(account=mapped_account)
        type(self.other.escort_profile).objects.filter(user=self.other).update(account=mapped_account)
        Wallet.objects.filter(pk=self.other_wallet.pk).update(account=mapped_account)
        self.other_account = self.accounts[self.other.pk] = mapped_account
        order = self.team()
        OrderProvider.objects.filter(order=order, provider=self.other).update(provider_account=None)
        self.assertEqual(LegacyAccountMap.objects.get(legacy_user_id=self.other.pk).account_id, self.other_account.pk)
        layer = RecordingLayer()
        with patch('orders.notifier.get_channel_layer', return_value=layer):
            notify_order_update(order)
        own = [event['data'] for group, event in layer.sent if group == f'account_{self.other_account.pk}']
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]['expected_income'], 2800)
        self.assertNotIn(f'account_{original_account_id}', [group for group, _ in layer.sent])

    def test_failed_primary_channel_does_not_block_secondary_or_boss(self):
        order = self.team()
        layer = RecordingLayer()
        original = layer.group_send
        async def deliver(group, event):
            if group == f'account_{self.provider_account.pk}':
                raise RuntimeError('isolated channel outage')
            await original(group, event)
        layer.group_send = deliver
        with patch('orders.notifier.get_channel_layer', return_value=layer), self.assertLogs('orders.notifier', level='WARNING'):
            notify_order_update(order)
        self.assertIn(f'account_{self.other_account.pk}', [group for group, _ in layer.sent])
        self.assertIn(f'account_{self.boss_account.pk}', [group for group, _ in layer.sent])


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
@skipUnlessDBFeature('has_select_for_update')
class TeamPartialRefundConcurrencyTest(TeamRefundFixtures, APITransactionTestCase):
    race = concurrent.SettledRefundConcurrencyTest.race

    def action(self, order, amount, key, token):
        client = self.client_for(self.admin_account)
        return lambda: client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'refund_amount': amount, 'client_request_id': key,
            'reason': '双陪MySQL并发部分退款', 'plan_token': token,
        }, format='json').data

    def test_same_key_mixed_team_refund_commits_once_and_replays(self):
        order = self.team()
        self.release_own(order, self.provider_wallet)
        token = self.preview_amount(order, 2500)['plan_token']
        results = self.race(self.action(order, 2500, 'team-same', token), self.action(order, 2500, 'team-same', token))
        self.assertTrue(all(row['code'] == 0 for row in results), results)
        self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
        self.assertEqual(self.recovered(order), [1300, 700, 500])

    def test_different_keys_same_team_plan_cannot_double_spend(self):
        order = self.team()
        token = self.preview_amount(order, 4000)['plan_token']
        results = self.race(self.action(order, 4000, 'team-key-a', token), self.action(order, 4000, 'team-key-b', token))
        self.assertEqual(sorted(row['code'] for row in results), [0, 409], results)
        self.assertEqual(self.recovered(order), [2080, 1120, 800])
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order), [5200, 2800, 2000])

    def test_partial_refund_racing_secondary_release_preserves_both_sources_and_foreign_hold(self):
        foreign, foreign_holds = self.frozen_foreign()
        order = self.team()
        self.release_own(order, self.provider_wallet)
        hold = IncomeHold.objects.get(income_transaction__order=order, wallet=self.other_wallet)
        # Make only this fixture's hold due in the isolated DB.
        now = timezone.now()
        IncomeHold.objects.filter(pk=hold.pk).update(release_at=now-timedelta(seconds=1))
        token = self.preview_amount(order, 2500)['plan_token']
        result, released = self.race(self.action(order, 2500, 'team-release', token),
                                    lambda: release_due_income(now=now, limit=1))
        self.assertEqual(released, 1)
        if result['code']:
            self.assertEqual(result['errorCode'], 'REFUND_PLAN_CHANGED', result)
            self.ok(self.pay_refund(order, 2500))
        self.provider_wallet.refresh_from_db(); self.other_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (3900, 4000))
        self.assertEqual((self.other_wallet.balance, self.other_wallet.frozen_amount), (2100, 4000))
        self.ok(self.pay_refund(order))
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()), foreign_holds)
        self.assertEqual(self.recovered(order), [5200, 2800, 2000])

    def test_partial_refund_racing_secondary_withdrawal_is_all_or_nothing(self):
        foreign, foreign_holds = self.frozen_foreign()
        order = self.team(amount=100000, bases=(65000, 35000), incomes=(52000, 28000))
        self.release_own(order, self.other_wallet)
        token = self.preview_amount(order, 50000)['plan_token']
        result, withdrawal = self.race(self.action(order, 50000, 'team-vs-withdraw', token),
            lambda: self.withdraw(self.other_account, 15000, 'team-concurrent-withdraw').data)
        self.assertNotEqual(result['code'] == 0, withdrawal['code'] == 0, (result, withdrawal))
        if withdrawal['code'] == 0:
            self.assertEqual(result['errorCode'], 'REFUND_RECOVERY_INSUFFICIENT', result)
            self.assertEqual(self.recovered(order), [0, 0, 0])
            self.assertFalse(OrderRefund.objects.filter(order=order).exists())
            self.reject_withdraw(self.other_account)
            self.ok(self.pay_refund(order, 50000))
        self.assertEqual(self.recovered(order), [26000, 14000, 10000])
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order), [52000, 28000, 20000])
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()), foreign_holds)
        for wallet in (self.provider_wallet, self.other_wallet):
            wallet.refresh_from_db()
            self.assertEqual((wallet.balance, wallet.frozen_amount), (0, 4000))


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LOGIN_OTP_REQUIRED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TeamOrderWebSocketTest(TeamRefundFixtures, APITransactionTestCase):
    def test_real_private_sockets_receive_own_full_payload_once_and_recheck_active_account(self):
        order = self.team()
        self.ok(self.pay_refund(order, 1000))
        order.refresh_from_db()
        accounts = (self.provider_account, self.other_account, self.boss_account)
        tokens = [issue_account_tokens(account)[0] for account in accounts]

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                await database_sync_to_async(notify_order_update)(order)
                payloads = [(await socket.receive_json_from())['data'] for socket in sockets]
                for payload, expected, recovered, remaining in zip(payloads[:2], (5200, 2800), (520, 280), (4680, 2520)):
                    self.assertEqual(payload['expected_income'], expected)
                    self.assertIn('provider', payload)
                    self.assertIsInstance(payload['settled_at'], str)
                    self.assertEqual(payload['refund_summary']['recovered_amount'], recovered)
                    self.assertEqual(payload['refund_summary']['remaining_frozen_amount'], remaining)
                    self.assertNotIn('recovery_lines', payload['refund_summary'])
                self.assertNotIn('recovered_amount', payloads[2]['refund_summary'])
                for socket in sockets:
                    self.assertTrue(await socket.receive_nothing(timeout=0.1))
                await database_sync_to_async(ClubAccount.objects.filter(pk=self.other_account.pk).update)(can_login=False)
                await sockets[1].send_json_to({'type': 'ping'})
                self.assertEqual(await sockets[1].receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()
