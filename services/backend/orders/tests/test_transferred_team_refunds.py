"""Refund posted A/B/C credits after the official A -> C team transfer."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from fractions import Fraction
from threading import Event
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.db import close_old_connections, connections, transaction
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITestCase, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.financial_lock import lock_order_money
from orders.models import Order, OrderProvider, OrderRefund
from orders.notifier import notify_order_update
from orders.refunds import build_refund_plan
from orders.services import required_report_providers, mark_escort_busy
from orders.settlement import build_provider_shares, aggregate_order_split
from orders.tests.factories import make_provider, make_order, make_service
from orders.tests.test_release_flow import screenshot
from orders.tests.test_report_realtime import RecordingLayer
from orders.tests.test_team_partial_refunds import TeamRefundFixtures
from orders.tests import test_settled_refund_concurrency as concurrency
from wallet.income import release_due_income
from wallet.models import IncomeHold, ProviderReport, Transaction, Wallet
from wallet.services import get_wallet


class TransferredFixtures(TeamRefundFixtures):
    def setUp(self):
        super().setUp()
        self.new = make_provider(deposit_mode='CASH')
        self.new_account = get_or_create_account_for_legacy_user(self.new)
        self.accounts[self.new.pk] = self.new_account
        profile = self.new.escort_profile
        profile.account = self.new_account
        profile.save(update_fields=['account'])
        self.new_wallet = get_wallet(user=self.new, account=self.new_account)
        role = self.admin_user.admin_membership.roles.first()
        role.permissions = list(set(role.permissions) | {'order:dispatch'})
        role.save(update_fields=['permissions'])

    def in_service(self, *, amount=10000, hold=72):
        service = make_service(price=amount, billing_provider_count=2, commission_rate=20)
        shares = build_provider_shares(amount, [{'commission_rate':20}, {'commission_rate':30}])
        split = aggregate_order_split(amount, shares)
        order = make_order(self.boss, service, self.provider, customer_account=self.boss_account,
            provider_account=self.provider_account, status='IN_SERVICE', payment_status='PAID',
            amount=amount, original_amount=amount, provider_income=split.provider_income,
            shop_income=split.shop_income, commission_rate=20, income_hold_hours=hold,
            in_service_at=timezone.now(), settlement_mode='REPORT_APPROVAL', escort_mode='DOUBLE',
            required_provider_count=2, requires_boss_selection=True)
        # New-order creation snapshots the current global hold rule. This
        # independent fixture explicitly tests a pre-agreed per-order duration.
        Order.objects.filter(pk=order.pk).update(income_hold_hours=hold)
        order.refresh_from_db()
        for user, account, share in zip((self.provider,self.other), (self.provider_account,self.other_account), shares):
            OrderProvider.objects.create(order=order, provider=user, provider_account=account,
                settlement_base=share.settlement_base, provider_income=share.provider_income,
                commission_type=share.commission_type, commission_rate=share.commission_rate)
        self.boss_wallet.refresh_from_db()
        before = self.boss_wallet.balance
        self.boss_wallet.balance -= amount
        self.boss_wallet.save(update_fields=['balance'])
        Transaction.objects.create(order=order, wallet=self.boss_wallet, tx_type='PAY', amount=-amount,
            balance_before=before, balance_after=self.boss_wallet.balance)
        mark_escort_busy(account_ids=[self.provider_account.pk,self.other_account.pk])
        return order

    def transfer(self, order, *, client=None, split=40, commands=None):
        return (client or self.admin).post(f'/api/admin/orders/{order.pk}/transfer/', {
            'transfers': commands or [{'old_provider_id':self.provider_account.pk,
                'new_provider_id':self.new_account.pk, 'split_type':'PERCENT', 'split_value':split,
                'penalty_amount':0}], 'reason':'隔离正式双陪转单',
        }, format='json')

    def finish_and_approve(self, order, *, approve_old=True):
        self.ok(self.admin.post(f'/api/admin/orders/{order.pk}/complete/', {}, format='json'))
        # Fixture-only maturity adjustment; reports still use actual submit time.
        Order.objects.filter(pk=order.pk).update(completed_at=timezone.now()-timedelta(hours=75))
        order.refresh_from_db()
        reports = list(order.provider_reports.order_by('pk'))
        self.assertEqual({row.provider_id for row in reports}, required_report_providers(order))
        for report in reports:
            client = self.client_for(self.accounts[report.provider_id])
            for kind in ('ENTRY','COMPLETION','RESULT'):
                self.ok(client.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind':kind,'image':screenshot()}, format='multipart'))
            self.ok(client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        for report in reports:
            if not approve_old and report.provider_id == self.provider.pk:
                continue
            self.ok(self.admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        order.refresh_from_db()
        return order

    def transferred(self, *, amount=10000, hold=72, split=40):
        order = self.in_service(amount=amount, hold=hold)
        self.ok(self.transfer(order, split=split))
        self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())
        self.finish_and_approve(order)
        self.assertIsNotNone(order.settled_at)
        return order

    def by_owner(self, order):
        return {row.wallet.account_id: row.amount for row in self.credits(order).select_related('wallet')}

    def foreign(self):
        order = self.transferred(amount=5000)
        holds = list(IncomeHold.objects.filter(income_transaction__order=order).order_by('pk').values())
        return order, holds


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class TransferredTeamRefundTest(TransferredFixtures, APITestCase):
    def test_released_old_provider_is_required_and_credited_only_after_all_three_approvals(self):
        order = self.in_service()
        self.ok(self.transfer(order))
        old = order.providers.get(provider=self.provider)
        self.assertEqual((old.settlement_base,old.provider_income),(1600,1600))
        self.assertIsNotNone(old.released_at); self.assertIsNone(old.settled_at)
        self.finish_and_approve(order,approve_old=False)
        self.assertIsNone(order.settled_at)
        self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())
        old_report = order.provider_reports.get(provider=self.provider)
        self.ok(self.admin.post(f'/api/admin/reports/{old_report.pk}/approve/', {}, format='json'))
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(self.by_owner(order),{self.provider_account.pk:1600,self.other_account.pk:3500,self.new_account.pk:2400,None:2500})
        self.assertEqual(IncomeHold.objects.filter(income_transaction__order=order).count(),3)

    def test_partial_refund_recovers_three_actual_sources_and_preserves_foreign_holds(self):
        foreign, foreign_holds = self.foreign()
        order = self.transferred()
        self.release_own(order,self.new_wallet)
        original_clocks = order.completed_at,order.settled_at
        for amount, cumulative in ((2500,[400,875,600,625]),(1500,[640,1400,960,1000])):
            self.ok(self.pay_refund(order,amount))
            self.assertEqual(self.recovered(order),cumulative)
            order.refresh_from_db()
            self.assertEqual((order.completed_at,order.settled_at),original_clocks)
        self.ok(self.pay_refund(order,key='transferred-final'))
        self.assertEqual(self.recovered(order),[1600,3500,2400,2500])
        before = self.full_snapshot()
        self.ok(self.pay_refund(order,key='transferred-final',token='already-committed'))
        self.assertEqual(self.full_snapshot(),before)
        self.assertEqual(self.pay_refund(order,1,key='transferred-final',token='conflicting-intent').data['code'],409)
        self.assertEqual(self.full_snapshot(),before)
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)
        for wallet, frozen in ((self.provider_wallet,800),(self.other_wallet,1750),(self.new_wallet,1200)):
            wallet.refresh_from_db()
            self.assertEqual((wallet.balance,wallet.frozen_amount),(0,frozen))

    def test_old_and_new_provider_shortfalls_each_leave_every_row_unchanged(self):
        for which in ('old','new'):
            with self.subTest(short_provider=which):
                foreign, foreign_holds = self.foreign()
                order = self.transferred(amount=100000)
                account,wallet,withdrawal = ((self.provider_account,self.provider_wallet,10000) if which=='old'
                                             else (self.new_account,self.new_wallet,15000))
                self.release_own(order,wallet)
                self.ok(self.withdraw(account,withdrawal,f'transfer-short-{which}'))
                token = self.preview_amount(order,50000)['plan_token']
                before = self.full_snapshot()
                result = self.pay_refund(order,50000,token=token,key=f'transfer-retry-{which}')
                self.assertEqual((result.data['code'],result.data['errorCode']),(400,'REFUND_RECOVERY_INSUFFICIENT'))
                self.assertEqual(before,self.full_snapshot())
                self.reject_withdraw(account)
                stale_before = self.full_snapshot()
                self.assertEqual(self.pay_refund(order,50000,token=token,key=f'transfer-retry-{which}').data['code'],409)
                self.assertEqual(stale_before,self.full_snapshot())
                self.ok(self.pay_refund(order,50000,key=f'transfer-retry-{which}'))
                self.ok(self.pay_refund(order))
                self.assertEqual(self.recovered(order),[16000,35000,24000,25000])
                self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)

    def test_one_raw_prefix_and_remainder_are_not_split_between_only_current_two(self):
        order = self.transferred(amount=43,hold=0)
        credits = list(self.credits(order))
        self.assertEqual([row.amount for row in credits],[7,15,11,10])
        atoms = sorted((Fraction(2*k+1,2*row.amount),row.pk,i) for i,row in enumerate(credits) for k in range(row.amount))
        cumulative = [0]*4
        for count in range(1,12):
            cumulative[atoms[count-1][2]] += 1
            self.ok(self.pay_refund(order,1,key=f'transfer-raw-{count}'))
            self.assertEqual(self.recovered(order),cumulative)
        snapshot = self.full_snapshot()
        self.assertEqual(self.pay_refund(order,33,token='over-limit').data['errorCode'],'REFUND_INVALID_AMOUNT')
        self.assertEqual(snapshot,self.full_snapshot())
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[7,15,11,10])

    def test_released_old_and_current_two_have_own_http_list_detail_and_ws_summary(self):
        order = self.transferred()
        self.ok(self.pay_refund(order,2500))
        order.refresh_from_db()
        layer = RecordingLayer()
        with patch('orders.notifier.get_channel_layer',return_value=layer):
            notify_order_update(order)
        events = {group:event['data'] for group,event in layer.sent}
        for account,expected,recovered in ((self.provider_account,1600,400),(self.other_account,3500,875),(self.new_account,2400,600)):
            client = self.client_for(account)
            detail = client.get(f'/api/orders/orders/{order.pk}/')
            self.ok(detail)
            http = detail.json()['data']
            rows = self.ok(client.get('/api/orders/orders/',{'status':'all'}))
            self.assertEqual(next(row for row in rows if row['id']==order.pk)['expected_income'],expected)
            ws = events[f'account_{account.pk}']
            self.assertEqual(http['expected_income'],expected)
            self.assertEqual(ws['expected_income'],expected)
            self.assertEqual(http['refund_summary'],ws['refund_summary'])
            self.assertEqual((ws['refund_summary']['recovered_amount'],ws['refund_summary']['net_income'],ws['refund_summary']['remaining_frozen_amount']),
                             (recovered,expected-recovered,expected-recovered))
            self.assertNotIn('recovery_lines',ws['refund_summary'])
            self.assertNotIn('START',ws['allowed_actions']); self.assertNotIn('COMPLETE',ws['allowed_actions'])
            self.assertEqual(sum(group==f'account_{account.pk}' for group,_ in layer.sent),1)
        unrelated = get_or_create_account_for_legacy_user(make_provider())
        self.assertEqual(self.client_for(unrelated).get(f'/api/orders/orders/{order.pk}/').status_code,404)
        self.assertNotIn(f'account_{unrelated.pk}',events)

    def test_zero_retained_old_share_is_not_required_or_paid(self):
        order = self.transferred(split=0)
        self.assertEqual(required_report_providers(order),{self.other.pk,self.new.pk})
        self.assertFalse(order.transactions.filter(wallet=self.provider_wallet,tx_type='INCOME').exists())
        self.ok(self.pay_refund(order,2500))
        self.assertEqual(self.recovered(order),[875,1000,625])

    def test_double_transfer_cannot_merge_two_active_seats_into_one(self):
        order = self.in_service()
        before = self.full_snapshot()
        commands = [{'old_provider_id':account.pk,'new_provider_id':self.new_account.pk,
            'split_type':'PERCENT','split_value':40,'penalty_amount':0} for account in (self.provider_account,self.other_account)]
        self.assertEqual(self.transfer(order,commands=commands).data['code'],400)
        self.assertEqual(before,self.full_snapshot())

    def test_transfer_order_notification_follows_outer_commit_or_rollback(self):
        order = self.in_service()
        with patch('console.views.notify_order_update') as notify:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                with self.assertRaisesRegex(RuntimeError,'outer rollback'):
                    with transaction.atomic():
                        self.ok(self.transfer(order))
                        notify.assert_not_called()
                        raise RuntimeError('outer rollback')
            self.assertEqual(callbacks,[])
            order.refresh_from_db()
            self.assertEqual(order.provider_account_id,self.provider_account.pk)
            with self.captureOnCommitCallbacks(execute=False) as committed:
                self.ok(self.transfer(order))
                notify.assert_not_called()
            for callback in committed:
                callback()
            notify.assert_called_once()


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
@skipUnlessDBFeature('has_select_for_update')
class TransferredTeamConcurrencyTest(TransferredFixtures, APITransactionTestCase):
    race = concurrency.SettledRefundConcurrencyTest.race

    def refund_action(self,order,amount,key,token):
        client = self.client_for(self.admin_account)
        return lambda:client.post(f'/api/admin/orders/{order.pk}/refund/',{'refund_amount':amount,
            'client_request_id':key,'reason':'正式转单MySQL退款','plan_token':token},format='json').data

    def test_same_key_refund_replays_once_for_all_three_original_credits(self):
        order = self.transferred()
        token = self.preview_amount(order,2500)['plan_token']
        results = self.race(self.refund_action(order,2500,'transferred-same',token),
                            self.refund_action(order,2500,'transferred-same',token))
        self.assertTrue(all(row['code']==0 for row in results),results)
        self.assertEqual(OrderRefund.objects.filter(order=order).count(),1)
        self.assertEqual(self.recovered(order),[400,875,600,625])

    def test_old_provider_release_racing_partial_refund_preserves_other_holds(self):
        foreign, foreign_holds = self.foreign()
        order = self.transferred()
        hold = IncomeHold.objects.get(income_transaction__order=order,wallet=self.provider_wallet)
        now = timezone.now()
        IncomeHold.objects.filter(pk=hold.pk).update(release_at=now-timedelta(seconds=1))
        token = self.preview_amount(order,2500)['plan_token']
        result,released = self.race(self.refund_action(order,2500,'transferred-release',token),
                                    lambda:release_due_income(now=now,limit=1))
        self.assertEqual(released,1)
        if result['code']:
            self.assertEqual(result['errorCode'],'REFUND_PLAN_CHANGED')
            self.ok(self.pay_refund(order,2500))
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(1200,800))
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[1600,3500,2400,2500])
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)

    def test_in_service_transfer_racing_full_refund_cannot_leave_orphan_income(self):
        order = self.in_service()
        token = self.preview_amount(order)['plan_token']
        transfer_client = self.client_for(self.admin_account)
        _,refund = self.race(lambda:self.transfer(order,client=transfer_client).data,
            self.refund_action(order,10000,'transfer-vs-refund',token))
        if refund['code']:
            self.assertEqual(refund['errorCode'],'REFUND_PLAN_CHANGED',refund)
            self.ok(self.pay_refund(order))
        order.refresh_from_db()
        self.assertEqual((order.status,order.payment_status),('CANCELLED','REFUNDED'))
        self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())
        self.assertEqual(order.transactions.filter(tx_type='REFUND').count(),1)

    def test_transfer_waits_before_wallet_while_cross_order_refund_owns_profile(self):
        settled = self.transferred()
        pending = self.in_service()
        token = self.preview_amount(settled,2500)['plan_token']
        profile_held,transfer_started,wallet_entered = Event(),Event(),Event()
        original_get_wallet = get_wallet
        def observe_wallet(*args,**kwargs):
            if kwargs.get('user_id') == self.provider.pk and kwargs.get('for_update'):
                wallet_entered.set()
            return original_get_wallet(*args,**kwargs)
        def observe_plan(order,**kwargs):
            if order.pk == settled.pk and kwargs.get('lock'):
                type(self.provider.escort_profile).objects.select_for_update().get(user=self.provider)
                profile_held.set()
                self.assertTrue(transfer_started.wait(timeout=8))
                self.assertFalse(wallet_entered.wait(timeout=0.4), 'Transfer reached wallet before the earlier funds/profile lock')
            return build_refund_plan(order,**kwargs)
        def transfer():
            self.assertTrue(profile_held.wait(timeout=8))
            transfer_started.set()
            return self.transfer(pending,client=self.client_for(self.admin_account)).data
        with patch('orders.refunds.build_refund_plan',side_effect=observe_plan), patch('console.views.get_wallet',side_effect=observe_wallet):
            refund,moved = self.race(self.refund_action(settled,2500,'cross-order-transfer',token),transfer)
        self.assertEqual(refund['code'],0,refund); self.assertEqual(moved['code'],0,moved)
        self.assertEqual(self.recovered(settled),[400,875,600,625])

    def test_cross_order_transfer_racing_old_income_release_does_not_touch_other_two_holds(self):
        settled = self.transferred()
        pending = self.in_service()
        other_holds = list(IncomeHold.objects.filter(income_transaction__order=settled).exclude(
            wallet=self.provider_wallet).order_by('pk').values())
        hold = IncomeHold.objects.get(income_transaction__order=settled,wallet=self.provider_wallet)
        now = timezone.now()
        IncomeHold.objects.filter(pk=hold.pk).update(release_at=now-timedelta(seconds=1))
        moved,released = self.race(lambda:self.transfer(pending,client=self.client_for(self.admin_account)).data,
            lambda:release_due_income(now=now,limit=1))
        self.assertEqual(moved['code'],0,moved); self.assertEqual(released,1)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(1600,0))
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=settled).exclude(
            wallet=self.provider_wallet).order_by('pk').values()),other_holds)
        self.assertFalse(pending.transactions.filter(tx_type='INCOME').exists())

    def test_transfer_locks_old_profile_before_wallet_against_official_cash_deposit_refund(self):
        order = self.in_service()
        profile = self.provider.escort_profile
        profile.deposit_paid = 1000
        profile.save(update_fields=['deposit_paid'])
        self.platform.balance = 1000
        self.platform.save(update_fields=['balance'])
        Transaction.objects.create(wallet=self.provider_wallet,tx_type='ADJUST',amount=1000,balance_before=0,balance_after=1000)
        Transaction.objects.create(wallet=self.provider_wallet,tx_type='DEPOSIT',amount=-1000,balance_before=1000,balance_after=0)
        Transaction.objects.create(wallet=self.platform,tx_type='DEPOSIT_INCOME',amount=1000,balance_before=0,balance_after=1000)
        profile_held,transfer_started,wallet_entered = Event(),Event(),Event()
        def deposit_wallet(*args,**kwargs):
            if kwargs.get('user_id') == self.provider.pk and kwargs.get('for_update'):
                profile_held.set()
                self.assertTrue(transfer_started.wait(timeout=8))
                self.assertFalse(wallet_entered.wait(timeout=0.4), 'Transfer acquired wallet before its profile lock')
            return get_wallet(*args,**kwargs)
        def transfer_wallet(*args,**kwargs):
            if kwargs.get('user_id') == self.provider.pk and kwargs.get('for_update'):
                wallet_entered.set()
            return get_wallet(*args,**kwargs)
        def deposit():
            return self.client_for(self.admin_account).post(f'/api/admin/escorts/{profile.pk}/deposit-refund/',
                {'amount':1000,'reason':'隔离转单并发押金退还','client_request_id':'transferred-deposit'},format='json').data
        def transfer():
            self.assertTrue(profile_held.wait(timeout=8))
            transfer_started.set()
            return self.transfer(order,client=self.client_for(self.admin_account)).data
        with patch('console.deposit_refund.get_wallet',side_effect=deposit_wallet), patch('console.views.get_wallet',side_effect=transfer_wallet):
            returned,moved = self.race(deposit,transfer)
        self.assertEqual(returned['code'],0,returned);self.assertEqual(moved['code'],0,moved)
        profile.refresh_from_db();self.provider_wallet.refresh_from_db();self.platform.refresh_from_db()
        self.assertEqual((profile.deposit_paid,self.provider_wallet.balance,self.platform.balance),(0,1000,0))


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LOGIN_OTP_REQUIRED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TransferredTeamSocketTest(TransferredFixtures, APITransactionTestCase):
    def test_released_old_and_current_two_sockets_receive_only_own_income(self):
        order = self.transferred()
        self.ok(self.pay_refund(order,2500))
        order.refresh_from_db()
        unrelated = get_or_create_account_for_legacy_user(make_provider())
        accounts = (self.provider_account,self.other_account,self.new_account,self.boss_account,unrelated)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        async def verify():
            sockets = [WebsocketCommunicator(application,f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                await database_sync_to_async(notify_order_update)(order)
                for socket,income,recovered in zip(sockets[:3],(1600,3500,2400),(400,875,600)):
                    payload = (await socket.receive_json_from())['data']
                    self.assertEqual(payload['expected_income'],income)
                    self.assertEqual(payload['refund_summary']['recovered_amount'],recovered)
                    self.assertEqual(payload['refund_summary']['net_income'],income-recovered)
                    self.assertNotIn('recovery_lines',payload['refund_summary'])
                    self.assertIn('provider',payload)
                    self.assertIsInstance(payload['settled_at'],str)
                    self.assertTrue(await socket.receive_nothing(timeout=0.1))
                boss = (await sockets[3].receive_json_from())['data']
                self.assertNotIn('recovered_amount',boss['refund_summary'])
                self.assertTrue(await sockets[4].receive_nothing(timeout=0.1))
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()
