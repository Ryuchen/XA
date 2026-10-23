"""Report audit notifications are commit-bound and scoped to the report owner."""
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from wallet.models import ProviderReport, ProviderReportImage, Transaction


class RecordingLayer:
    def __init__(self):
        self.sent = []

    async def group_send(self, group, event):
        self.sent.append((group, event))


class ReportFixtures:
    def make_owner(self):
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        return provider, account

    def make_reviewer(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        membership = user.admin_membership
        membership.account = account
        membership.save(update_fields=['account'])
        return account, membership

    def make_report(self, provider, account, *, linked=False,
                    status=ProviderReport.Status.PENDING, **kwargs):
        order = None
        if linked:
            order = make_order(
                make_user(), provider=provider, provider_account=account,
                status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
                settlement_mode=Order.SettlementMode.COMPLETION,
                completed_at=timezone.now(), amount=10000, commission_rate=20,
                provider_income=8000, shop_income=2000,
            )
        return ProviderReport.objects.create(
            provider=provider, provider_account=account, order=order,
            game_name='报单实时通知测试', amount=10000,
            status=status, **kwargs,
        )

    def event(self, report, *, status=None):
        return {
            'type': 'report_status_update',
            'data': {
                'report_id': report.pk, 'order_id': report.order_id,
                'status': status or report.status,
            },
        }

    def notifications(self, report, account):
        events = [('report_reviewers', self.event(report))]
        if account:
            events.append((f'account_{account.pk}', self.event(report)))
        return events


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=False, ORDER_SETTLEMENT_MODE='COMPLETION')
class ReportAuditRealtimeTest(ReportFixtures, APITestCase):
    def setUp(self):
        self.provider, self.account = self.make_owner()
        self.admin = make_console_user(['report:view', 'report:audit'])
        admin_account = get_or_create_account_for_legacy_user(self.admin)
        membership = self.admin.admin_membership
        membership.account = admin_account
        membership.save(update_fields=['account'])
        token, _ = issue_account_tokens(admin_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.layer = RecordingLayer()
        self.enterContext(patch('orders.notifier.get_channel_layer', return_value=self.layer))

    def audit(self, report, action):
        return self.client.post(
            f'/api/admin/reports/{report.pk}/{action}/',
            {'audit_remark': '请补充清晰的结单截图'} if action == 'reject' else {},
            format='json',
        ).data

    def test_linked_and_legacy_approval_and_rejection_notify_only_after_commit(self):
        for linked in (True, False):
            for action, expected_status in (('approve', 'APPROVED'), ('reject', 'REJECTED')):
                with self.subTest(linked=linked, action=action):
                    report = self.make_report(self.provider, self.account, linked=linked)
                    self.layer.sent.clear()
                    with self.captureOnCommitCallbacks(execute=True):
                        result = self.audit(report, action)
                        self.assertEqual(result['code'], 0, result)
                        self.assertEqual(self.layer.sent, [])
                    report.refresh_from_db()
                    self.assertEqual(report.status, expected_status)
                    self.assertCountEqual(self.layer.sent, self.notifications(report, self.account))

    @override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_report_approval_mode_notifies_during_aftersale_window_without_settling(self):
        report = self.make_report(
            self.provider, self.account, linked=True, submitted_at=timezone.now(),
            entry_image='reports/entry/realtime.png',
            completion_image='reports/completion/realtime.png',
        )
        order = report.order
        order.settlement_mode = Order.SettlementMode.REPORT_APPROVAL
        order.save(update_fields=['settlement_mode'])
        OrderProvider.objects.create(
            order=order, provider=self.provider, provider_account=self.account,
            settlement_base=10000, commission_rate=20, provider_income=8000,
        )
        ProviderReportImage.objects.create(report=report, image='reports/results/realtime.png')
        with self.captureOnCommitCallbacks(execute=True):
            result = self.audit(report, 'approve')
            self.assertEqual(result['code'], 0, result)
            self.assertEqual(self.layer.sent, [])
        report.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(report.status, ProviderReport.Status.APPROVED)
        self.assertIsNone(order.settled_at)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type=Transaction.TxType.INCOME).exists())
        self.assertCountEqual(self.layer.sent, self.notifications(report, self.account))

    def test_rolled_back_audit_does_not_notify_or_change_status(self):
        for linked in (True, False):
            for action in ('approve', 'reject'):
                with self.subTest(linked=linked, action=action):
                    report = self.make_report(self.provider, self.account, linked=linked)
                    self.layer.sent.clear()
                    with self.captureOnCommitCallbacks(execute=True):
                        with self.assertRaisesRegex(RuntimeError, 'rollback test'):
                            with transaction.atomic():
                                self.assertEqual(self.audit(report, action)['code'], 0)
                                raise RuntimeError('rollback test')
                    report.refresh_from_db()
                    self.assertEqual(report.status, ProviderReport.Status.PENDING)
                    self.assertEqual(self.layer.sent, [])

    def test_repeated_audit_and_invalid_rejection_do_not_notify(self):
        for linked in (True, False):
            for action in ('approve', 'reject'):
                with self.subTest(linked=linked, action=action):
                    report = self.make_report(self.provider, self.account, linked=linked)
                    with self.captureOnCommitCallbacks(execute=True):
                        self.assertEqual(self.audit(report, action)['code'], 0)
                    self.layer.sent.clear()
                    with self.captureOnCommitCallbacks(execute=True):
                        self.assertNotEqual(self.audit(report, action)['code'], 0)
                    self.assertEqual(self.layer.sent, [])
        report = self.make_report(self.provider, self.account)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.client.post(f'/api/admin/reports/{report.pk}/reject/', {}).data
            self.assertNotEqual(result['code'], 0)
        self.assertEqual(self.layer.sent, [])


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False)
class ProviderReportWriteRealtimeTest(ReportFixtures, APITestCase):
    def setUp(self):
        self.provider, self.account = self.make_owner()
        token, _ = issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.layer = RecordingLayer()
        self.enterContext(patch('orders.notifier.get_channel_layer', return_value=self.layer))

    def completed_order(self):
        return make_order(
            make_user(), provider=self.provider, provider_account=self.account,
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
            settlement_mode=Order.SettlementMode.REPORT_APPROVAL,
            completed_at=timezone.now(), amount=10000, commission_rate=20,
            provider_income=8000, shop_income=2000,
        )

    def complete_evidence(self, status):
        report = self.make_report(
            self.provider, self.account, linked=True, status=status,
            entry_image='reports/entry/write-realtime.png',
            completion_image='reports/completion/write-realtime.png',
        )
        ProviderReportImage.objects.create(report=report, image='reports/results/write-realtime.png')
        return report

    def test_creating_a_draft_notifies_after_commit_and_replaying_it_is_silent(self):
        order = self.completed_order()
        with self.captureOnCommitCallbacks(execute=True):
            result = self.client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json').data
            self.assertEqual(result['code'], 0, result)
            self.assertEqual(self.layer.sent, [])
        report = ProviderReport.objects.get(pk=result['data']['id'])
        self.assertEqual(report.status, ProviderReport.Status.DRAFT)
        self.assertCountEqual(self.layer.sent, self.notifications(report, self.account))
        self.layer.sent.clear()
        with self.captureOnCommitCallbacks(execute=True):
            repeated = self.client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json').data
            self.assertEqual(repeated['code'], 0, repeated)
            self.assertEqual(repeated['data']['id'], report.pk)
        self.assertEqual(self.layer.sent, [])

    def test_rolled_back_draft_creation_does_not_notify_or_keep_the_draft(self):
        order = self.completed_order()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaisesRegex(RuntimeError, 'rollback test'):
                with transaction.atomic():
                    result = self.client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json').data
                    self.assertEqual(result['code'], 0, result)
                    raise RuntimeError('rollback test')
        self.assertFalse(ProviderReport.objects.filter(order=order).exists())
        self.assertEqual(self.layer.sent, [])

    def test_legacy_draft_account_backfill_notifies_after_commit(self):
        report = self.make_report(self.provider, None, linked=True, status=ProviderReport.Status.DRAFT)
        order = report.order
        order.provider_account = self.account
        order.save(update_fields=['provider_account'])
        with self.captureOnCommitCallbacks(execute=True):
            result = self.client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json').data
            self.assertEqual(result['code'], 0, result)
            self.assertEqual(result['data']['id'], report.pk)
            self.assertEqual(self.layer.sent, [])
        report.refresh_from_db()
        self.assertEqual(report.provider_account_id, self.account.pk)
        self.assertCountEqual(self.layer.sent, self.notifications(report, self.account))

    def test_automatic_drafts_notify_each_provider_once_after_commit_without_replay(self):
        from orders.services import ensure_fulfillment_reports
        second_provider, second_account = self.make_owner()
        order = self.completed_order()
        for provider, account in ((self.provider, self.account), (second_provider, second_account)):
            OrderProvider.objects.create(
                order=order, provider=provider, provider_account=account,
                settlement_base=5000, commission_rate=20, provider_income=4000,
            )
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                ensure_fulfillment_reports(order)
                ensure_fulfillment_reports(order)
            self.assertEqual(self.layer.sent, [])
        expected = []
        for account in (self.account, second_account):
            report = ProviderReport.objects.get(order=order, provider_account=account)
            self.assertEqual(report.status, ProviderReport.Status.DRAFT)
            expected.extend(self.notifications(report, account))
        self.assertCountEqual(self.layer.sent, expected)

    def test_rolled_back_automatic_drafts_do_not_notify(self):
        from orders.services import ensure_fulfillment_reports
        order = self.completed_order()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaisesRegex(RuntimeError, 'rollback test'):
                with transaction.atomic():
                    ensure_fulfillment_reports(order)
                    raise RuntimeError('rollback test')
        self.assertFalse(ProviderReport.objects.filter(order=order).exists())
        self.assertEqual(self.layer.sent, [])

    def test_submit_and_resubmit_notify_after_commit_but_repeated_submission_does_not(self):
        for status in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
            with self.subTest(status=status):
                report = self.complete_evidence(status)
                self.layer.sent.clear()
                with self.captureOnCommitCallbacks(execute=True):
                    result = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}).data
                    self.assertEqual(result['code'], 0, result)
                    self.assertEqual(self.layer.sent, [])
                report.refresh_from_db()
                self.assertEqual(report.status, ProviderReport.Status.PENDING)
                self.assertIsNotNone(report.submitted_at)
                self.assertCountEqual(self.layer.sent, self.notifications(report, self.account))
                self.layer.sent.clear()
                with self.captureOnCommitCallbacks(execute=True):
                    repeated = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}).data
                    self.assertNotEqual(repeated['code'], 0, repeated)
                self.assertEqual(self.layer.sent, [])

    def test_rolled_back_submit_and_resubmit_do_not_notify_or_change_status(self):
        for status in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
            with self.subTest(status=status):
                report = self.complete_evidence(status)
                with self.captureOnCommitCallbacks(execute=True):
                    with self.assertRaisesRegex(RuntimeError, 'rollback test'):
                        with transaction.atomic():
                            result = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}).data
                            self.assertEqual(result['code'], 0, result)
                            raise RuntimeError('rollback test')
                report.refresh_from_db()
                self.assertEqual(report.status, status)
                self.assertIsNone(report.submitted_at)
                self.assertEqual(self.layer.sent, [])

    def test_failed_creation_and_missing_evidence_submission_do_not_notify(self):
        report = self.make_report(self.provider, self.account, linked=True, status=ProviderReport.Status.DRAFT)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.client.post('/api/wallet/reports/', {'order_id': 99999999}, format='json').data
            self.assertNotEqual(result['code'], 0, result)
            result = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}).data
            self.assertNotEqual(result['code'], 0, result)
        self.assertEqual(self.layer.sent, [])


class ReportNotifierTest(ReportFixtures, TestCase):
    def setUp(self):
        self.provider, self.account = self.make_owner()
        self.layer = RecordingLayer()
        self.enterContext(patch('orders.notifier.get_channel_layer', return_value=self.layer))

    def notify(self, report):
        from orders.notifier import notify_report_update
        notify_report_update(report)

    def test_explicit_report_account_takes_priority_over_legacy_mapping(self):
        _, explicit_account = self.make_owner()
        report = self.make_report(self.provider, explicit_account)
        self.notify(report)
        self.assertCountEqual(self.layer.sent, self.notifications(report, explicit_account))

    def test_null_report_account_uses_existing_legacy_mapping(self):
        report = self.make_report(self.provider, None)
        self.notify(report)
        self.assertCountEqual(self.layer.sent, self.notifications(report, self.account))

    def test_no_mapping_never_uses_an_unrelated_same_number_account(self):
        provider = make_user(role='PROVIDER', id=987654)
        ClubAccount.objects.create(
            id=provider.pk, username='unrelated-report-account', account_type='PROVIDER',
        )
        self.assertFalse(LegacyAccountMap.objects.filter(legacy_user_id=provider.pk).exists())
        report = self.make_report(provider, None)
        self.notify(report)
        self.assertEqual(self.layer.sent, [('report_reviewers', self.event(report))])

    def test_channel_failure_does_not_raise_after_successful_audit(self):
        report = self.make_report(self.provider, self.account)

        async def failing_send(group, event):
            raise RuntimeError('test channel unavailable')

        with self.assertLogs('orders.notifier', level='WARNING'):
            with patch('orders.notifier.get_channel_layer', return_value=SimpleNamespace(group_send=failing_send)):
                self.notify(report)

    def test_failure_in_private_channel_does_not_block_the_reviewer_channel(self):
        report = self.make_report(self.provider, self.account)

        async def failing_private_send(group, event):
            if group.startswith('account_'):
                raise RuntimeError('test private channel unavailable')
            self.layer.sent.append((group, event))

        with self.assertLogs('orders.notifier', level='WARNING'):
            with patch('orders.notifier.get_channel_layer', return_value=SimpleNamespace(group_send=failing_private_send)):
                self.notify(report)
        self.assertEqual(self.layer.sent, [('report_reviewers', self.event(report))])


@override_settings(LOGIN_OTP_REQUIRED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ReportConsumerTest(ReportFixtures, TransactionTestCase):
    def test_report_view_reviewers_receive_in_multiple_sessions_but_other_roles_do_not(self):
        provider, owner = self.make_owner()
        report = self.make_report(provider, owner)
        reviewer, _ = self.make_reviewer(['report:view'])
        second_reviewer, _ = self.make_reviewer(['report:view', 'order:view'])
        order_reader, _ = self.make_reviewer(['order:view'])
        auditor_only, _ = self.make_reviewer(['report:audit'])
        _, unrelated_provider = self.make_owner()
        boss = get_or_create_account_for_legacy_user(make_user())
        accounts = (reviewer, second_reviewer, order_reader, auditor_only, unrelated_provider, boss)
        tokens = [issue_account_tokens(account)[0] for account in accounts]

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                # Only report:view connections join this separate broadcast group.
                await get_channel_layer().group_send('report_reviewers', self.event(report, status='APPROVED'))
                for socket in sockets[:2]:
                    self.assertEqual(await socket.receive_json_from(), self.event(report))
                for socket in sockets[2:]:
                    self.assertTrue(await socket.receive_nothing(timeout=0.1))
                # Delivery also checks permissions/ownership if an event is misrouted.
                for account in accounts[2:]:
                    await get_channel_layer().group_send(f'account_{account.pk}', self.event(report))
                for socket in sockets[2:]:
                    self.assertTrue(await socket.receive_nothing(timeout=0.1))
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()

    def test_report_view_revocation_blocks_an_open_socket_and_membership_revocation_closes_it(self):
        provider, owner = self.make_owner()
        report = self.make_report(provider, owner)
        reviewer, membership = self.make_reviewer(['report:view', 'order:view'])
        role = membership.roles.get()
        token, _ = issue_account_tokens(reviewer)

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                await get_channel_layer().group_send('report_reviewers', self.event(report))
                self.assertEqual(await socket.receive_json_from(), self.event(report))
                role.permissions = ['order:view']
                await database_sync_to_async(role.save)(update_fields=['permissions'])
                await get_channel_layer().group_send('report_reviewers', self.event(report))
                self.assertTrue(await socket.receive_nothing(timeout=0.1))
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
                membership.is_active = False
                await database_sync_to_async(membership.save)(update_fields=['is_active'])
                await get_channel_layer().group_send('report_reviewers', self.event(report))
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_reviewer_receives_historical_report_even_without_a_provider_mapping(self):
        from orders.notifier import notify_report_update
        provider = make_provider()
        self.assertFalse(LegacyAccountMap.objects.filter(legacy_user_id=provider.pk).exists())
        report = self.make_report(provider, None)
        reviewer, _ = self.make_reviewer(['report:view'])
        token, _ = issue_account_tokens(reviewer)

        async def verify():
            socket = WebsocketCommunicator(application, f'/api/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                await database_sync_to_async(notify_report_update)(report)
                self.assertEqual(await socket.receive_json_from(), self.event(report))
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_only_current_owner_provider_receives_current_database_status(self):
        provider, owner = self.make_owner()
        _, other = self.make_owner()
        boss = get_or_create_account_for_legacy_user(make_user())
        staff_user = make_console_user(['order:view'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        membership = staff_user.admin_membership
        membership.account = staff
        membership.save(update_fields=['account'])
        report = self.make_report(provider, owner)
        accounts = (owner, other, boss, staff)
        tokens = [issue_account_tokens(account)[0] for account in accounts]

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                for account in accounts:
                    await get_channel_layer().group_send(
                        f'account_{account.pk}', self.event(report, status='APPROVED'),
                    )
                # Socket events only invalidate: report ownership/status are re-read.
                self.assertEqual(await sockets[0].receive_json_from(), self.event(report))
                for socket in sockets[1:]:
                    self.assertTrue(await socket.receive_nothing(timeout=0.1))
                await get_channel_layer().group_send(f'account_{owner.pk}', {
                    'type': 'report_status_update', 'data': {'report_id': 99999999},
                })
                self.assertTrue(await sockets[0].receive_nothing(timeout=0.1))
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()

    def test_legacy_mapping_can_receive_but_explicit_owner_change_revokes_delivery(self):
        provider, owner = self.make_owner()
        _, other = self.make_owner()
        report = self.make_report(provider, None)
        token, _ = issue_account_tokens(owner)

        async def verify():
            socket = WebsocketCommunicator(application, f'/api/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                await get_channel_layer().group_send(f'account_{owner.pk}', self.event(report))
                self.assertEqual(await socket.receive_json_from(), self.event(report))
                await database_sync_to_async(ProviderReport.objects.filter(pk=report.pk).update)(provider_account=other)
                await get_channel_layer().group_send(f'account_{owner.pk}', self.event(report))
                self.assertTrue(await socket.receive_nothing(timeout=0.1))
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_disabling_owner_closes_an_already_connected_socket(self):
        provider, owner = self.make_owner()
        report = self.make_report(provider, owner)
        token, _ = issue_account_tokens(owner)

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                await database_sync_to_async(ClubAccount.objects.filter(pk=owner.pk).update)(is_active=False)
                await get_channel_layer().group_send(f'account_{owner.pk}', self.event(report))
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
