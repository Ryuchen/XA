"""Isolated regressions for durable store-local manual message campaigns."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_user
from site_messages.manual_push import campaign_result, fingerprint, process_campaign, stage_campaign
from site_messages.models import ManualMessageCampaign, ManualMessageRecipient, Message
from site_messages.tasks import drain_manual_messages
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ManualPushRecoveryTest(APITestCase):
    def setUp(self):
        self.creator, self.client = self.operator(['message:view', 'message:push'])
        self.account = self.business_account('BOSS')

    def operator(self, permissions):
        legacy = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(legacy)
        AdminMembership.objects.filter(user=legacy).update(account=account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return account, client

    def business_account(self, account_type='BOSS', **fields):
        suffix = uuid4().hex
        values = {'username': f'push-{suffix}', 'nickname': '群发接收人',
                  'account_no': f'XA{suffix[:12].upper()}', 'account_type': account_type}
        values.update(fields)
        return ClubAccount.objects.create_account(**values)

    def stage(self, accounts, request_id='recovery-key'):
        selector = {'accounts': sorted(account.pk for account in accounts)}
        digest = fingerprint({'title': '恢复核对', 'preview': '摘要', 'detail': '正文',
                              'type': 'SYSTEM', 'selector': selector})
        return stage_campaign(creator=self.creator, request_id=request_id, digest=digest,
            title='恢复核对', preview='摘要', detail='正文', message_type='SYSTEM', selector=selector,
            account_cycles={account.pk: (account.business_cycle, account.account_type) for account in accounts})

    def test_same_request_replays_but_different_content_conflicts(self):
        payload = {'client_request_id': 'same-key', 'recipient_account_ids': [self.account.pk],
                   'title': '原请求', 'preview': '原摘要', 'detail': '原正文'}
        first = self.client.post('/api/admin/messages/push/', payload, format='json')
        self.assertEqual((first.status_code, first.data['code']), (200, 0), first.data)
        self.assertFalse(first.data['data']['request_replayed'])
        campaign_id = first.data['data']['campaign_id']

        replay = self.client.post('/api/admin/messages/push/', payload, format='json')
        self.assertEqual((replay.status_code, replay.data['code']), (200, 0), replay.data)
        self.assertTrue(replay.data['data']['request_replayed'])
        self.assertEqual(replay.data['data']['campaign_id'], campaign_id)
        self.assertEqual(Message.objects.filter(recipient_account=self.account).count(), 1)
        self.assertEqual(ManualMessageCampaign.objects.count(), 1)
        self.assertEqual(ManualMessageRecipient.objects.get().attempts, 1)

        conflict = self.client.post('/api/admin/messages/push/',
            {**payload, 'detail': '同编号不同正文'}, format='json')
        self.assertEqual((conflict.status_code, conflict.data['code']), (409, 409), conflict.data)
        self.assertEqual(Message.objects.filter(recipient_account=self.account).count(), 1)
        self.assertEqual(ManualMessageCampaign.objects.get().detail, '原正文')

    def test_failed_write_waits_then_retries_once_and_repeated_scans_are_idempotent(self):
        with patch('site_messages.models.Message.objects.create',
                   side_effect=DatabaseError('isolated message write outage')):
            with self.assertLogs('site_messages.manual_push', level='ERROR'):
                response = self.client.post('/api/admin/messages/push/', {
                    'client_request_id': 'write-failure', 'recipient_account_ids': [self.account.pk],
                    'title': '失败后恢复', 'preview': '摘要', 'detail': '正文'}, format='json')
        self.assertEqual((response.status_code, response.data['code']), (200, 0), response.data)
        self.assertEqual((response.data['data']['count'], response.data['data']['pending_count'],
                          response.data['data']['failed_count']), (0, 1, 1))
        recipient = ManualMessageRecipient.objects.get()
        self.assertEqual((recipient.status, recipient.attempts, recipient.last_error),
                         ('PENDING', 1, 'DatabaseError'))
        self.assertIsNone(recipient.message_id)
        self.assertGreater(recipient.next_attempt_at, timezone.now())
        self.assertFalse(Message.objects.filter(recipient_account=self.account).exists())

        self.assertEqual(drain_manual_messages.run(), 0)
        ManualMessageRecipient.objects.filter(pk=recipient.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(drain_manual_messages.run(), 1)
        self.assertEqual(drain_manual_messages.run(), 0)
        process_campaign(ManualMessageCampaign.objects.get())
        recipient.refresh_from_db()
        self.assertEqual((recipient.status, recipient.attempts, recipient.last_error),
                         ('CREATED', 2, ''))
        message = Message.objects.get(recipient_account=self.account)
        self.assertEqual((recipient.message_id, message.title, message.recipient_business_cycle),
                         (message.pk, '失败后恢复', self.account.business_cycle))
        self.assertEqual(campaign_result(ManualMessageCampaign.objects.get())['count'], 1)

    def test_cycle_role_and_staff_changes_skip_without_redirecting_to_new_identity(self):
        reentered = self.business_account('BOSS')
        role_changed = self.business_account('BOSS')
        disabled_staff = self.business_account('STAFF')
        inactive_staff = self.business_account('STAFF')
        inactive_membership = AdminMembership.objects.create(
            user=make_user(role='OPERATOR'), account=inactive_staff, is_active=False)
        accounts = [reentered, role_changed, disabled_staff, inactive_staff]
        campaign, created = self.stage(accounts, 'identity-change')
        self.assertTrue(created)

        ClubAccount.objects.filter(pk=reentered.pk).update(business_cycle=2)
        ClubAccount.objects.filter(pk=role_changed.pk).update(account_type='PROVIDER')
        ClubAccount.objects.filter(pk=disabled_staff.pk).update(can_login=False)
        AdminMembership.objects.filter(pk=inactive_membership.pk).update(is_active=False)
        process_campaign(campaign)

        self.assertEqual(set(campaign.recipients.values_list('status', flat=True)), {'SKIPPED'})
        self.assertFalse(Message.objects.filter(recipient_account__in=accounts).exists())
        self.assertFalse(LegacyAccountMap.objects.filter(account__in=accounts).exists())
        self.assertTrue(all(row.attempts == 0 and row.completed_at for row in campaign.recipients.all()))

        # Re-admission under a newer cycle/type must not inherit the old audience slot.
        ClubAccount.objects.filter(pk=reentered.pk).update(business_cycle=3)
        ClubAccount.objects.filter(pk=role_changed.pk).update(account_type='BOSS')
        ClubAccount.objects.filter(pk=disabled_staff.pk).update(can_login=True)
        AdminMembership.objects.filter(pk=inactive_membership.pk).update(is_active=True)
        self.assertEqual(drain_manual_messages.run(), 0)
        self.assertEqual(set(campaign.recipients.values_list('status', flat=True)), {'SKIPPED'})
        self.assertFalse(Message.objects.filter(recipient_account__in=accounts).exists())

    def test_push_retry_caps_at_200_and_requires_push_permission(self):
        accounts = ClubAccount.objects.bulk_create([
            ClubAccount(id=50000 + index, username=f'retry-{index}',
                        account_no=f'XA{index:012d}', account_type='BOSS')
            for index in range(201)
        ])
        campaign, _ = self.stage(accounts, 'retry-limit')
        selected = list(campaign.recipients.order_by('pk').values_list('pk', flat=True))

        accepted = self.client.post('/api/admin/messages/push-retry/',
            {'campaign_id': campaign.pk, 'recipient_ids': selected[:200]}, format='json')
        self.assertEqual((accepted.status_code, accepted.data['code']), (200, 0), accepted.data)
        self.assertEqual((accepted.data['data']['count'], accepted.data['data']['pending_count']), (200, 1))
        self.assertEqual(Message.objects.filter(recipient_account__in=accounts).count(), 200)

        rejected = self.client.post('/api/admin/messages/push-retry/',
            {'campaign_id': campaign.pk, 'recipient_ids': selected}, format='json')
        self.assertEqual((rejected.status_code, rejected.data['code']), (400, 400), rejected.data)
        foreign = self.client.post('/api/admin/messages/push-retry/',
            {'campaign_id': campaign.pk, 'recipient_ids': [selected[0], 999999]}, format='json')
        self.assertEqual(foreign.status_code, 400)
        self.assertEqual(Message.objects.filter(recipient_account__in=accounts).count(), 200)

        viewer, viewer_client = self.operator(['message:view'])
        self.assertEqual(viewer_client.get('/api/admin/messages/push-campaigns/').status_code, 200)
        self.assertEqual(viewer_client.get('/api/admin/messages/push-status/',
            {'campaign_id': campaign.pk}).status_code, 200)
        denied = viewer_client.post('/api/admin/messages/push-retry/',
            {'campaign_id': campaign.pk, 'recipient_ids': [selected[-1]]}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(ManualMessageRecipient.objects.get(pk=selected[-1]).status, 'PENDING')

        role = viewer.admin_membership.roles.get()
        role.is_active = False
        role.save(update_fields=['is_active'])
        self.assertEqual(viewer_client.get('/api/admin/messages/push-campaigns/').status_code, 403)
        self.assertEqual(viewer_client.get('/api/admin/messages/push-status/',
            {'campaign_id': campaign.pk}).status_code, 403)

    def make_campaign_row(self, request_id, states):
        campaign = ManualMessageCampaign.objects.create(creator=self.creator, request_id=request_id,
            request_fingerprint='a' * 64, selector={'audience': 'BOSS'}, title=request_id,
            preview='批次摘要', detail='批次正文', message_type='SYSTEM')
        for status, error in states:
            account = self.business_account('BOSS')
            ManualMessageRecipient.objects.create(campaign=campaign, account=account,
                business_cycle=account.business_cycle, account_type=account.account_type,
                status=status, last_error=error)
        return campaign

    def test_campaign_pagination_and_filters_include_empty_and_terminal_states(self):
        pending = self.make_campaign_row('pending', [('PENDING', '')])
        failed = self.make_campaign_row('failed', [('PENDING', 'DatabaseError')])
        skipped = self.make_campaign_row('skipped', [('SKIPPED', '')])
        created = self.make_campaign_row('created', [('CREATED', '')])
        empty = self.make_campaign_row('empty', [])

        first = self.client.get('/api/admin/messages/push-campaigns/', {'page_size': 2})
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual((first.data['data']['total'], len(first.data['data']['list'])),
                         (5, 2))
        second = self.client.get('/api/admin/messages/push-campaigns/', {'page_size': 2, 'page': 2})
        third = self.client.get('/api/admin/messages/push-campaigns/', {'page_size': 2, 'page': 3})
        ids = [row['campaign_id'] for page in (first, second, third) for row in page.data['data']['list']]
        self.assertEqual((len(ids), len(set(ids))), (5, 5))
        self.assertEqual({pending.pk, failed.pk, skipped.pk, created.pk, empty.pk}, set(ids))

        expected = {'PENDING': {pending.pk, failed.pk}, 'FAILED': {failed.pk},
                    'SKIPPED': {skipped.pk}, 'COMPLETED': {skipped.pk, created.pk, empty.pk}}
        for state, campaign_ids in expected.items():
            with self.subTest(state=state):
                response = self.client.get('/api/admin/messages/push-campaigns/', {'status': state})
                self.assertEqual({row['campaign_id'] for row in response.data['data']['list']}, campaign_ids)
        exact = self.client.get('/api/admin/messages/push-campaigns/', {
            'creator_id': self.creator.pk, 'client_request_id': 'failed'})
        self.assertEqual([row['campaign_id'] for row in exact.data['data']['list']], [failed.pk])
        no_result = self.client.get('/api/admin/messages/push-campaigns/', {'client_request_id': 'missing'})
        self.assertEqual((no_result.data['data']['list'], no_result.data['data']['total']), ([], 0))
        self.assertEqual(self.client.get('/api/admin/messages/push-campaigns/',
            {'creator_id': '0'}).status_code, 400)
        self.assertEqual(self.client.get('/api/admin/messages/push-campaigns/',
            {'client_request_id': ' padded'}).status_code, 400)
        self.assertEqual(self.client.get('/api/admin/messages/push-campaigns/',
            {'status': 'UNKNOWN'}).status_code, 400)
        self.assertEqual(self.client.get('/api/admin/messages/push-campaigns/',
            {'page_size': 101}).data['data']['page_size'], 100)
        self.assertEqual(self.client.get('/api/admin/messages/push-campaigns/',
            {'page': 999}).status_code, 404)

    def test_recipient_status_pagination_filters_and_campaign_totals(self):
        campaign = self.make_campaign_row('status-page', [
            ('CREATED', ''), ('CREATED', ''), ('PENDING', 'DatabaseError'),
            ('PENDING', ''), ('SKIPPED', ''),
        ])
        endpoint = '/api/admin/messages/push-status/'
        first = self.client.get(endpoint, {'campaign_id': campaign.pk, 'page_size': 2})
        second = self.client.get(endpoint, {'campaign_id': campaign.pk, 'page_size': 2, 'page': 2})
        third = self.client.get(endpoint, {'campaign_id': campaign.pk, 'page_size': 2, 'page': 3})
        self.assertEqual((first.data['data']['total'], len(first.data['data']['list'])), (5, 2))
        ids = [row['id'] for page in (first, second, third) for row in page.data['data']['list']]
        self.assertEqual((len(ids), len(set(ids))), (5, 5))
        summary = third.data['data']['campaign']
        self.assertEqual((summary['total'], summary['count'], summary['pending_count'],
                          summary['failed_count'], summary['skipped_count']), (5, 2, 2, 1, 1))
        self.assertEqual((summary['title'], summary['preview'], summary['detail']),
                         ('status-page', '批次摘要', '批次正文'))

        for state, count in [('CREATED', 2), ('PENDING', 2), ('FAILED', 1), ('SKIPPED', 1)]:
            with self.subTest(state=state):
                response = self.client.get(endpoint, {'campaign_id': campaign.pk, 'status': state})
                self.assertEqual((response.data['data']['total'], len(response.data['data']['list'])),
                                 (count, count))
        self.assertEqual(self.client.get(endpoint, {'campaign_id': campaign.pk, 'status': 'UNKNOWN'}).status_code, 400)
        self.assertEqual(self.client.get(endpoint, {'campaign_id': 'abc'}).status_code, 400)
        self.assertEqual(self.client.get(endpoint, {'campaign_id': 999999}).status_code, 404)
        self.assertEqual(self.client.get(endpoint, {'campaign_id': campaign.pk, 'page': 999}).status_code, 404)

        empty = self.make_campaign_row('status-empty', [])
        response = self.client.get(endpoint, {'campaign_id': empty.pk})
        self.assertEqual((response.data['data']['list'], response.data['data']['total']), ([], 0))
        self.assertEqual(response.data['data']['campaign']['total'], 0)


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False,
                   STORE_DATABASE_ALIASES=('store_b',), KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ManualPushStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_same_numeric_campaign_account_and_request_ids_stay_store_local(self):
        stores = [resolve_store(), xinan_store()]
        staged = []
        for store in stores:
            with store_context(store):
                creator = ClubAccount.objects.create_account(username='same-creator',
                    account_no='XA900000000001', account_type='STAFF', id=90001)
                account = ClubAccount.objects.create_account(username='same-recipient',
                    account_no='XA900000000002', account_type='BOSS', id=90002)
                digest = fingerprint({'title': '双店隔离', 'preview': '', 'detail': '',
                                      'type': 'SYSTEM', 'selector': {'accounts': [account.pk]}})
                campaign, created = stage_campaign(creator=creator, request_id='shared-request',
                    digest=digest, title='双店隔离', preview='', detail='', message_type='SYSTEM',
                    selector={'accounts': [account.pk]},
                    account_cycles={account.pk: (account.business_cycle, account.account_type)})
                self.assertTrue(created)
                staged.append((store, campaign, account))
        self.assertEqual(staged[0][1].pk, staged[1][1].pk)
        self.assertEqual(staged[0][2].pk, staged[1][2].pk)

        with store_context(stores[0]):
            self.assertEqual(drain_manual_messages.run(), 1)
        with store_context(stores[1]):
            self.assertEqual(ManualMessageRecipient.objects.get().status, 'PENDING')
            self.assertFalse(Message.objects.exists())
            self.assertEqual(drain_manual_messages.run(), 1)
        for store, campaign, account in staged:
            with store_context(store):
                self.assertEqual(drain_manual_messages.run(), 0)
                recipient = ManualMessageRecipient.objects.get(campaign_id=campaign.pk, account_id=account.pk)
                message = Message.objects.get(pk=recipient.message_id)
                self.assertEqual((message.recipient_account_id, message.recipient_business_cycle,
                                  message.title), (account.pk, account.business_cycle, '双店隔离'))
                self.assertEqual(ManualMessageCampaign.objects.count(), 1)
                self.assertEqual(Message.objects.count(), 1)
