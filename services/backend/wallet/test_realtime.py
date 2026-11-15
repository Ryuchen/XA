"""Committed wallet changes invalidate only the owner and authorized reviewers."""
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import Order
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from wallet.income import hold_provider_income, release_due_income
from wallet.models import IncomeHold, ProviderReport, ProviderReportImage, SystemConfig, Transaction, Wallet, WithdrawRequest, get_platform_wallet
from wallet.notifier import notify_wallet_update, notify_withdraw_update, owner_account_id
from wallet.services import get_wallet


class RecordingLayer:
    def __init__(self):
        self.sent = []

    async def group_send(self, group, event):
        self.sent.append((group, event))


class WalletFixtures:
    def owner(self):
        user = make_provider(deposit_mode='CASH')
        account = get_or_create_account_for_legacy_user(user)
        wallet = get_wallet(account=account, user=user)
        return user, account, wallet

    def reviewer(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        membership = user.admin_membership
        membership.account = account
        membership.save(update_fields=['account'])
        return account, membership

    def client_for(self, account):
        client = APIClient()
        token, _ = issue_account_tokens(account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        return client

    def withdrawal(self, user, account):
        return WithdrawRequest.objects.create(
            user=user, account=account, amount=20000, tax_rate=2, tax_amount=400,
            payee_method='WECHAT', payee_account='LOCAL_TEST_ONLY', payee_name='本地样例',
        )

    def wallet_event(self, wallet, account):
        return {'type': 'wallet_update', 'data': {'account_id': account.pk if account else None, 'wallet_id': wallet.pk}}

    def withdraw_event(self, row, account):
        return {'type': 'withdraw_status_update', 'data': {
            'withdraw_id': row.pk, 'account_id': account.pk if account else None, 'status': row.status,
        }}

    def expected(self, wallet, account, withdraw=None):
        events = [(f'account_{account.pk}', self.wallet_event(wallet, account)), ('wallet_reviewers', self.wallet_event(wallet, account))]
        if withdraw:
            events += [(f'account_{account.pk}', self.withdraw_event(withdraw, account)), ('withdraw_reviewers', self.withdraw_event(withdraw, account))]
        return events


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, PLATFORM_IDENTITY_ENABLED=True)
class WalletMutationRealtimeTest(WalletFixtures, APITestCase):
    def setUp(self):
        self.user, self.account, self.wallet = self.owner()
        self.wallet.balance = 50000
        self.wallet.save(update_fields=['balance'])
        self.client = self.client_for(self.account)
        admin, _ = self.reviewer(['wallet:view', 'withdraw:view', 'withdraw:audit'])
        self.admin = self.client_for(admin)
        from stores.models import PlatformAccount, PlatformRoleGrant
        from stores.platform_auth import issue_tokens
        finance = PlatformAccount(username='realtime-finance', phone='13800001111', is_phone_verified=True)
        finance.set_password('test-only-password'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='shenglang')
        self.finance = APIClient(HTTP_HOST='admin.esports.club')
        self.finance.credentials(HTTP_AUTHORIZATION='Bearer '+issue_tokens(finance, 'SMS', finance.phone)[0])
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value':'2'})
        self.media = TemporaryDirectory(prefix='xa-wallet-realtime-')
        self.addCleanup(self.media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=self.media.name))
        self.layer = RecordingLayer()
        self.enterContext(patch('wallet.notifier.get_channel_layer', return_value=self.layer))

    def payload(self, request_id='wallet-realtime-1'):
        return {'amount': 20000, 'payee_method': 'WECHAT', 'payee_account': 'LOCAL_TEST_ONLY',
                'payee_name': '本地样例', 'client_request_id': request_id}

    def create(self, request_id='wallet-realtime-1'):
        result = self.client.post('/api/wallet/withdraw/', self.payload(request_id), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        return WithdrawRequest.objects.get(pk=result.data['data']['request']['id'])

    def test_withdraw_creation_and_replay_publish_only_one_committed_invalidation(self):
        with self.captureOnCommitCallbacks(execute=True):
            row = self.create()
            self.assertEqual(self.layer.sent, [])
        self.assertCountEqual(self.layer.sent, self.expected(self.wallet, self.account, row))
        self.layer.sent.clear()
        with self.captureOnCommitCallbacks(execute=True):
            replay = self.client.post('/api/wallet/withdraw/', self.payload(), format='json')
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(self.layer.sent, [])
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (30000, 20000))
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='WITHDRAW').count(), 1)

    def test_approval_and_rejection_notify_after_commit_without_duplicate_money(self):
        for action in ('paid', 'store_reject', 'finance_reject'):
            with self.subTest(action=action):
                with self.captureOnCommitCallbacks(execute=True):
                    row = self.create(action)
                with self.captureOnCommitCallbacks(execute=True):
                    claim = self.admin.post(f'/api/admin/withdrawals/{row.pk}/claim/').data
                    self.assertEqual(claim['code'], 0)
                self.wallet.refresh_from_db()
                before = self.wallet.balance
                self.layer.sent.clear()
                data = {'expected_claimed_at': claim['data']['claimed_at']}
                if action == 'store_reject':
                    data['audit_remark'] = '本地驳回回归'
                with self.captureOnCommitCallbacks(execute=True):
                    stage = 'reject' if action == 'store_reject' else 'approve'
                    response = self.admin.post(f'/api/admin/withdrawals/{row.pk}/{stage}/', data, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                    self.assertEqual(self.layer.sent, [])
                row.refresh_from_db()
                self.assertEqual(row.status, 'REJECTED' if action == 'store_reject' else 'STORE_APPROVED')
                expected = self.expected(self.wallet, self.account, row)
                self.assertCountEqual(self.layer.sent, expected if action == 'store_reject' else expected[2:])
                self.wallet.refresh_from_db()
                self.assertEqual((self.wallet.balance, self.wallet.frozen_amount),
                    (before + row.amount, 0) if action == 'store_reject' else (before, row.amount))
                row.transaction.refresh_from_db()
                self.assertEqual(row.transaction.status, 'FAILED' if action == 'store_reject' else 'PENDING')
                self.assertEqual(row.actual_amount + row.tax_amount, row.amount)
                self.layer.sent.clear()
                with self.captureOnCommitCallbacks(execute=True):
                    duplicate = self.admin.post(f'/api/admin/withdrawals/{row.pk}/{stage}/', data, format='json')
                self.assertNotEqual(duplicate.data['code'], 0)
                self.assertEqual(self.layer.sent, [])
                if action == 'store_reject':
                    continue
                url=f'/api/admin/platform/withdrawals/shenglang/{row.pk}/'
                if action == 'finance_reject':
                    final_action='reject'
                    final_data={'client_request_id':'realtime-reject', 'reason':'核实未打款', 'confirmed_unpaid':True}
                else:
                    with self.captureOnCommitCallbacks(execute=True):
                        reviewed=self.finance.post(url+'approve', {'client_request_id':'realtime-review'}, format='json')
                        self.assertEqual(reviewed.data['code'], 0, reviewed.data)
                        self.assertEqual(self.layer.sent, [])
                    row.refresh_from_db()
                    self.assertEqual(row.status, 'PLATFORM_APPROVED')
                    row.transaction.refresh_from_db(); self.wallet.refresh_from_db()
                    self.assertEqual((row.transaction.status, self.wallet.frozen_amount), ('PENDING', row.amount))
                    self.assertFalse(Transaction.objects.filter(tx_type='WITHDRAW_TAX').exists())
                    self.assertCountEqual(self.layer.sent, self.expected(self.wallet, self.account, row)[2:])
                    self.layer.sent.clear()
                    final_action='paid'
                    final_data={'client_request_id':'realtime-paid', 'payout_reference':'LOCAL_SIMULATED_PAYOUT',
                                'paid_at':timezone.now().isoformat()}
                with self.captureOnCommitCallbacks(execute=True):
                    result=self.finance.post(url+final_action, final_data, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    self.assertEqual(self.layer.sent, [])
                row.refresh_from_db(); self.wallet.refresh_from_db()
                self.assertEqual(row.status, 'PAID' if action == 'paid' else 'REJECTED')
                row.transaction.refresh_from_db()
                self.assertEqual(row.transaction.status, 'SUCCESS' if action == 'paid' else 'FAILED')
                self.assertEqual((self.wallet.balance, self.wallet.frozen_amount),
                                 (before, 0) if action == 'paid' else (before+row.amount, 0))
                self.assertCountEqual(self.layer.sent, self.expected(self.wallet, self.account, row))
                self.layer.sent.clear()
                with self.captureOnCommitCallbacks(execute=True):
                    replay=self.finance.post(url+final_action, final_data, format='json')
                self.assertTrue(replay.data['data']['replayed'])
                self.assertEqual(self.layer.sent, [])
        self.assertEqual(Transaction.objects.filter(tx_type='WITHDRAW_TAX').count(), 1)
        self.assertEqual(get_platform_wallet().balance, 400)

    def test_invalid_request_and_enclosing_rollback_do_not_publish_or_freeze(self):
        with self.captureOnCommitCallbacks(execute=True):
            invalid = self.client.post('/api/wallet/withdraw/', {**self.payload(), 'expected_tax_rate': 90}, format='json')
            self.assertNotEqual(invalid.data['code'], 0)
            with self.assertRaisesRegex(RuntimeError, 'rollback fixture'):
                with transaction.atomic():
                    self.create()
                    raise RuntimeError('rollback fixture')
        self.assertEqual(self.layer.sent, [])
        self.assertFalse(WithdrawRequest.objects.exists())
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (50000, 0))

    def test_audit_rollback_does_not_publish_or_change_frozen_funds(self):
        with self.captureOnCommitCallbacks(execute=True):
            row = self.create()
        with self.captureOnCommitCallbacks(execute=True):
            claim = self.admin.post(f'/api/admin/withdrawals/{row.pk}/claim/').data
            self.assertEqual(claim['code'], 0)
        self.layer.sent.clear()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaisesRegex(RuntimeError, 'rollback audit'):
                with transaction.atomic():
                    response = self.admin.post(f'/api/admin/withdrawals/{row.pk}/approve/', {'expected_claimed_at': claim['data']['claimed_at']}, format='json')
                    self.assertEqual(response.data['code'], 0)
                    raise RuntimeError('rollback audit')
        self.assertEqual(self.layer.sent, [])
        self.wallet.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount, row.status), (30000, 20000, 'PENDING'))
        self.assertFalse(Transaction.objects.filter(tx_type='WITHDRAW_TAX').exists())

    def test_settlement_credit_and_freeze_share_one_commit_event(self):
        for hours in (0, 72):
            with self.subTest(hours=hours):
                self.layer.sent.clear()
                with self.captureOnCommitCallbacks(execute=True):
                    with transaction.atomic():
                        self.wallet.refresh_from_db()
                        before = self.wallet.balance
                        self.wallet.balance += 2000
                        self.wallet.save(update_fields=['balance'])
                        income = Transaction.objects.create(wallet=self.wallet, amount=2000, tx_type='INCOME', balance_before=before, balance_after=self.wallet.balance)
                        hold = hold_provider_income(income, profile=self.user.escort_profile, hold_hours=hours)
                    self.assertEqual(self.layer.sent, [])
                self.assertCountEqual(self.layer.sent, self.expected(self.wallet, self.account))
                self.wallet.refresh_from_db()
                self.assertEqual(self.wallet.balance, before + (0 if hours else 2000))
                self.assertEqual(bool(hold), bool(hours))

    def test_release_exact_boundary_and_retry_notify_once(self):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                self.wallet.balance += 2000
                self.wallet.save(update_fields=['balance'])
                income = Transaction.objects.create(wallet=self.wallet, amount=2000, tx_type='INCOME')
                hold = hold_provider_income(income, profile=self.user.escort_profile, hold_hours=72)
        self.layer.sent.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(release_due_income(now=hold.release_at - timedelta(microseconds=1)), 0)
        self.assertEqual(self.layer.sent, [])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(release_due_income(now=hold.release_at), 1)
            self.assertEqual(self.layer.sent, [])
        self.assertCountEqual(self.layer.sent, self.expected(self.wallet, self.account))
        self.layer.sent.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(release_due_income(now=hold.release_at), 0)
        self.assertEqual(self.layer.sent, [])
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (52000, 0))
        self.assertEqual(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').count(), 1)

    def test_report_and_aftersale_clocks_overlap_and_invalidate_once_at_settlement(self):
        from engagement.confirmation import process_order
        from orders.tests.test_release_flow import screenshot
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        boss_wallet = get_wallet(account=boss_account, user=boss)
        boss_wallet.balance = 90000
        boss_wallet.save(update_fields=['balance'])
        completed = timezone.now()
        order = make_order(
            boss, make_service(price=10000), self.user,
            customer_account=boss_account, provider_account=self.account,
            status='COMPLETED', payment_status='PAID', completed_at=completed,
            settlement_mode='REPORT_APPROVAL', commission_rate=20,
            provider_income=8000, shop_income=2000,
        )
        Order.objects.filter(pk=order.pk).update(service_clock_version=1, service_aftersale_hours=72,
                                                  service_freeze_hours=72, service_deposit_percent=60)
        Transaction.objects.create(wallet=boss_wallet, order=order, tx_type='PAY', amount=-10000,
                                   balance_before=100000, balance_after=90000)
        report = ProviderReport.objects.create(
            provider=self.user, provider_account=self.account, order=order,
            game_name='本地结算样例', amount=10000, payout_amount=8000, commission_rate=20, status='APPROVED',
            entry_image=screenshot(), completion_image=screenshot(),
            submitted_at=completed + timedelta(seconds=1),
        )
        ProviderReportImage.objects.create(report=report, image=screenshot())
        boundary = report.submitted_at + timedelta(hours=72)
        with self.captureOnCommitCallbacks(execute=True):
            with patch('django.utils.timezone.now', return_value=boundary - timedelta(microseconds=1)):
                self.assertFalse(process_order(order.pk))
        self.assertEqual(self.layer.sent, [])
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (50000, 0))
        with self.captureOnCommitCallbacks(execute=True):
            with patch('django.utils.timezone.now', return_value=boundary):
                process_order(order.pk)
                process_order(order.pk)
            self.assertEqual(self.layer.sent, [])
        self.assertCountEqual(self.layer.sent, self.expected(self.wallet, self.account))
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (58000, 0))
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=order).exists())
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)

    def test_income_freeze_rollback_discards_money_and_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaisesRegex(RuntimeError, 'rollback income'):
                with transaction.atomic():
                    self.wallet.balance += 2000
                    self.wallet.save(update_fields=['balance'])
                    income = Transaction.objects.create(wallet=self.wallet, amount=2000, tx_type='INCOME')
                    hold_provider_income(income, profile=self.user.escort_profile, hold_hours=72)
                    raise RuntimeError('rollback income')
        self.assertEqual(self.layer.sent, [])
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (50000, 0))
        self.assertFalse(IncomeHold.objects.exists())
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet).exists())

    def test_broken_channel_cannot_turn_committed_withdrawal_into_failure(self):
        with patch('wallet.notifier.get_channel_layer', side_effect=RuntimeError('channel down')):
            with self.assertLogs('wallet.notifier', level='WARNING'):
                with self.captureOnCommitCallbacks(execute=True):
                    self.create()
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (30000, 20000))


class WalletNotifierOwnershipTest(WalletFixtures, APITestCase):
    def setUp(self):
        self.layer = RecordingLayer()
        self.enterContext(patch('wallet.notifier.get_channel_layer', return_value=self.layer))

    def test_explicit_owner_wins_and_historical_records_follow_mapping(self):
        user, mapped, wallet = self.owner()
        _, explicit, _ = self.owner()
        wallet.account = explicit
        # The notifier resolver must prioritize the explicit field, even for a
        # historical object whose separate legacy mapping points elsewhere.
        self.assertEqual(owner_account_id(wallet), explicit.pk)
        wallet.account = None
        self.assertEqual(owner_account_id(wallet), mapped.pk)
        row = self.withdrawal(user, None)
        notify_withdraw_update(row)
        self.assertCountEqual(self.layer.sent, [(f'account_{mapped.pk}', self.withdraw_event(row, mapped)), ('withdraw_reviewers', self.withdraw_event(row, mapped))])

    def test_unmapped_legacy_user_never_targets_a_same_number_account(self):
        user = make_provider()
        self.assertFalse(LegacyAccountMap.objects.filter(legacy_user_id=user.pk).exists())
        ClubAccount.objects.get_or_create(pk=user.pk, defaults={'username': 'same-number-not-owner', 'account_type': 'PROVIDER'})
        wallet = user.wallet
        row = self.withdrawal(user, None)
        notify_wallet_update(wallet)
        notify_withdraw_update(row)
        self.assertCountEqual(self.layer.sent, [('wallet_reviewers', self.wallet_event(wallet, None)), ('withdraw_reviewers', self.withdraw_event(row, None))])

    def test_private_group_failure_does_not_block_reviewers(self):
        _, account, wallet = self.owner()
        async def fail_private(group, event):
            if group.startswith('account_'):
                raise RuntimeError('private group unavailable')
            self.layer.sent.append((group, event))
        self.layer.group_send = fail_private
        with self.assertLogs('wallet.notifier', level='WARNING'):
            notify_wallet_update(wallet)
        self.assertEqual(self.layer.sent, [('wallet_reviewers', self.wallet_event(wallet, account))])


@override_settings(LOGIN_OTP_REQUIRED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class WalletConsumerTest(WalletFixtures, TransactionTestCase):
    def test_boss_receives_own_wallet_and_transaction_reader_rechecks_live_permission(self):
        boss_user = make_user()
        boss = get_or_create_account_for_legacy_user(boss_user)
        wallet = get_wallet(user=boss_user, account=boss)
        other_boss = get_or_create_account_for_legacy_user(make_user())
        reader, membership = self.reviewer(['transaction:view'])
        role = membership.roles.get()
        tokens = [issue_account_tokens(account)[0] for account in (boss, other_boss, reader)]

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                await database_sync_to_async(notify_wallet_update)(wallet)
                for socket in (sockets[0], sockets[2]):
                    event = await socket.receive_json_from()
                    self.assertEqual(event, self.wallet_event(wallet, boss))
                    self.assertEqual(set(event['data']), {'account_id', 'wallet_id'})
                self.assertTrue(await sockets[1].receive_nothing(timeout=0.05))
                await get_channel_layer().group_send(f'account_{other_boss.pk}', self.wallet_event(wallet, boss))
                self.assertTrue(await sockets[1].receive_nothing(timeout=0.05))
                role.permissions = ['order:view']
                await database_sync_to_async(role.save)(update_fields=['permissions'])
                await get_channel_layer().group_send('wallet_reviewers', self.wallet_event(wallet, boss))
                self.assertTrue(await sockets[2].receive_nothing(timeout=0.1))
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()

    def test_separate_reviewer_groups_and_private_ownership_are_enforced(self):
        user, owner, wallet = self.owner()
        row = self.withdrawal(user, owner)
        wallet_reader, _ = self.reviewer(['wallet:view'])
        withdraw_reader, _ = self.reviewer(['withdraw:view'])
        auditor, _ = self.reviewer(['withdraw:audit', 'report:view'])
        _, other, _ = self.owner()
        boss = get_or_create_account_for_legacy_user(make_user())
        accounts = (owner, wallet_reader, withdraw_reader, auditor, other, boss)
        tokens = [issue_account_tokens(account)[0] for account in accounts]

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                await database_sync_to_async(notify_wallet_update)(wallet)
                await database_sync_to_async(notify_withdraw_update)(row)
                self.assertEqual(await sockets[0].receive_json_from(), self.wallet_event(wallet, owner))
                self.assertEqual(await sockets[0].receive_json_from(), self.withdraw_event(row, owner))
                self.assertEqual(await sockets[1].receive_json_from(), self.wallet_event(wallet, owner))
                self.assertEqual(await sockets[2].receive_json_from(), self.withdraw_event(row, owner))
                for socket in sockets[1:]:
                    self.assertTrue(await socket.receive_nothing(timeout=0.05))
                for account in accounts[3:]:
                    await get_channel_layer().group_send(f'account_{account.pk}', self.wallet_event(wallet, owner))
                    await get_channel_layer().group_send(f'account_{account.pk}', self.withdraw_event(row, owner))
                for socket in sockets[3:]:
                    self.assertTrue(await socket.receive_nothing(timeout=0.05))
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()

    def test_open_socket_rechecks_permissions_membership_and_current_withdraw_status(self):
        user, owner, wallet = self.owner()
        row = self.withdrawal(user, owner)
        reviewer, membership = self.reviewer(['wallet:view', 'withdraw:view'])
        role = membership.roles.get()
        token, _ = issue_account_tokens(reviewer)

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                row.status = 'APPROVED'
                await database_sync_to_async(row.save)(update_fields=['status'])
                stale = self.withdraw_event(row, owner)
                stale['data']['status'] = 'PENDING'
                await get_channel_layer().group_send('withdraw_reviewers', stale)
                self.assertEqual(await socket.receive_json_from(), self.withdraw_event(row, owner))
                role.permissions = ['report:view']
                await database_sync_to_async(role.save)(update_fields=['permissions'])
                await get_channel_layer().group_send('wallet_reviewers', self.wallet_event(wallet, owner))
                await get_channel_layer().group_send('withdraw_reviewers', stale)
                self.assertTrue(await socket.receive_nothing(timeout=0.1))
                membership.is_active = False
                await database_sync_to_async(membership.save)(update_fields=['is_active'])
                await get_channel_layer().group_send('wallet_reviewers', self.wallet_event(wallet, owner))
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()
        async_to_sync(verify)()

    def test_historical_null_accounts_reach_mapping_owner_and_authorized_reviewers(self):
        user, owner, wallet = self.owner()
        wallet.account = None
        wallet.save(update_fields=['account'])
        row = self.withdrawal(user, None)
        reviewer, _ = self.reviewer(['wallet:view', 'withdraw:view'])
        tokens = [issue_account_tokens(account)[0] for account in (owner, reviewer)]

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                await database_sync_to_async(notify_wallet_update)(wallet)
                await database_sync_to_async(notify_withdraw_update)(row)
                for socket in sockets:
                    self.assertEqual(await socket.receive_json_from(), self.wallet_event(wallet, owner))
                    self.assertEqual(await socket.receive_json_from(), self.withdraw_event(row, owner))
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()
