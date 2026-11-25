"""SA02 closure notice: trusted clock, immutable expiry evidence and public isolation."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens

from .closure import (
    ClosureError, assert_new_activity_allowed, closure_guard, new_activity_allowed,
)
from .closure_clock import CLOCK_SOURCE, ControlClockEvidence, ControlClockUnavailable
from .context import resolve_store, store_context
from .models import (
    PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store,
    StoreClosureEvent, StoreClosureNotice,
)
from .platform_auth import issue_tokens
from .tests_hosts import xinan_store


@override_settings(
    PLATFORM_IDENTITY_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'],
    STORE_HOST_BINDINGS={
        'sl.esports.club': 'shenglang', 'xa.esports.club': 'xinan',
        'testserver': 'shenglang',
    },
)
class StoreClosureNoticeTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.first.status = Store.Status.ACTIVE
        self.first.save(update_fields=['status', 'updated_at'])
        self.second = xinan_store()
        self.super = self.platform_actor('closure-super', 'SUPER_ADMIN', '*', '13800008001')
        self.finance = self.platform_actor('closure-finance', 'FINANCE', '*', '13800008002')
        self.second_finance = self.platform_actor('closure-second-finance', 'FINANCE', self.second.slug, '13800008003')
        self.viewer = self.platform_actor('closure-viewer', 'ORDER_VIEWER', '*', '13800008004')
        self.super_client = self.platform_client(self.super)
        self.finance_client = self.platform_client(self.finance)
        self.second_finance_client = self.platform_client(self.second_finance)
        self.viewer_client = self.platform_client(self.viewer)
        self.public_tokens = {}
        self.public_accounts = {}
        for store in (self.first, self.second):
            with store_context(store):
                account = ClubAccount.objects.create_account(
                    id=807001, username='closure-same-boss', account_type='BOSS',
                    account_no='XA807001', nickname=store.name + '老板',
                )
                self.public_accounts[store.slug] = account
                self.public_tokens[store.slug] = issue_account_tokens(account)[0]
        self.now = datetime(2026, 11, 1, 4, 0, tzinfo=datetime_timezone.utc)
        self.clock_error = None

        def explicit_clock():
            if self.clock_error is not None:
                raise self.clock_error
            return ControlClockEvidence(self.now, CLOCK_SOURCE, 'sqlite-test:default')

        patcher = patch('stores.closure.read_control_clock', side_effect=explicit_clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def platform_actor(self, username, role, scope, phone):
        actor = PlatformAccount.objects.create(
            username=username, nickname=username + '-内部姓名', phone=phone, is_phone_verified=True,
        )
        PlatformRoleGrant.objects.create(account=actor, role=role, scope_slug=scope)
        return actor

    def platform_client(self, actor, host='admin.esports.club'):
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def url(self, store, suffix=''):
        return f'/api/admin/platform/store-management/{store.pk}/closure{suffix}'

    def start(self, store, *, client=None, request_id='closure-init-1', reason='内部原因：经营方申请停止营业',
              category='OPERATOR_REQUEST'):
        return (client or self.super_client).post(self.url(store), {
            'reason_category': category, 'reason': reason, 'client_request_id': request_id,
        }, format='json')

    def confirm(self, store, *, client=None, request_id='closure-expiry-1', reason='控制库时钟确认公示已满 30 天'):
        return (client or self.super_client).post(self.url(store, '/confirm-expiry'), {
            'reason': reason, 'client_request_id': request_id,
        }, format='json')

    def admin_read(self, store, *, client=None):
        return (client or self.finance_client).get(self.url(store))

    def public(self, store):
        host = 'sl.esports.club' if store == self.first else 'xa.esports.club'
        return APIClient(
            HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + self.public_tokens[store.slug],
        ).get('/api/stores/closure/')

    def test_independent_permissions_and_admin_host_are_enforced(self):
        self.assertEqual(APIClient(HTTP_HOST='admin.esports.club').get(self.url(self.first)).status_code, 401)
        self.assertEqual(self.viewer_client.get(self.url(self.first)).status_code, 403)
        self.assertEqual(self.start(self.first, client=self.viewer_client).status_code, 403)
        self.assertEqual(self.start(self.first, client=self.finance_client).status_code, 403)
        self.assertEqual(self.finance_client.get(self.url(self.first)).status_code, 200)
        created = self.start(self.first)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(self.finance_client.get(self.url(self.first)).status_code, 200)
        self.assertEqual(self.second_finance_client.get(self.url(self.first)).status_code, 403)
        self.assertEqual(self.confirm(self.first, client=self.finance_client).status_code, 403)
        boss = APIClient(
            HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + self.public_tokens[self.first.slug],
        )
        self.assertEqual(boss.get(self.url(self.first)).status_code, 401)
        wrong_host = self.platform_client(self.super, host='sl.esports.club')
        self.assertEqual(wrong_host.get(self.url(self.first)).status_code, 403)

    def test_two_stores_same_numeric_ids_and_request_ids_keep_separate_notices(self):
        self.assertEqual(self.public_accounts[self.first.slug].pk, self.public_accounts[self.second.slug].pk)
        first = self.start(self.first, reason='声浪内部闭店原因', request_id='shared-closure-request')
        self.assertEqual(first.status_code, 201, first.content)
        self.now += timedelta(days=1)
        second = self.start(self.second, reason='兴安内部闭店原因', request_id='shared-closure-request')
        self.assertEqual(second.status_code, 201, second.content)
        self.assertNotEqual(first.data['data']['closure_id'], second.data['data']['closure_id'])
        self.assertNotEqual(first.data['data']['started_at'], second.data['data']['started_at'])
        first_public = self.public(self.first)
        second_public = self.public(self.second)
        self.assertEqual(first_public.data['data']['started_at'], first.data['data']['started_at'])
        self.assertEqual(second_public.data['data']['started_at'], second.data['data']['started_at'])
        self.assertNotIn('声浪内部闭店原因', str(first_public.data))
        self.assertNotIn('兴安内部闭店原因', str(second_public.data))
        self.assertNotEqual(first_public.data['data']['earliest_settlement_at'], second_public.data['data']['earliest_settlement_at'])
        for path, headers in (
            ('/api/stores/closure/?store=xinan', {}),
            ('/api/stores/closure/', {'HTTP_X_STORE_SLUG': 'xinan'}),
        ):
            self.assertEqual(APIClient(HTTP_HOST='sl.esports.club').get(path, **headers).status_code, 400)
        # The existing guarantee projection and its four-state contract remain untouched.
        guarantee = APIClient(HTTP_HOST='sl.esports.club').get('/api/stores/guarantee/')
        self.assertEqual(guarantee.status_code, 200)
        self.assertEqual(guarantee.data['data']['status'], 'PENDING_REVIEW')
        self.assertEqual(set(guarantee.data['data']), {
            'status', 'label', 'currency', 'amount_due_fen', 'net_received_fen',
            'amount_remaining_fen', 'record_basis',
        })

    def test_thirty_day_boundary_enters_pending_without_disabling_store(self):
        started = self.start(self.first)
        self.assertEqual(started.status_code, 201, started.content)
        notice = StoreClosureNotice.objects.get(store=self.first)
        self.assertEqual(notice.notice_days, 30)
        self.assertEqual(notice.earliest_settlement_at, notice.started_at + timedelta(days=30))
        self.now = notice.earliest_settlement_at - timedelta(microseconds=1)
        before = self.admin_read(self.first)
        self.assertEqual(before.data['data']['status'], 'NOTICE_ACTIVE')
        early = self.confirm(self.first)
        self.assertEqual(early.status_code, 409)
        self.assertEqual(early.data['errorCode'], 'CLOSURE_NOTICE_ACTIVE')
        self.assertFalse(StoreClosureEvent.objects.exists())
        self.assertEqual(self.public(self.first).data['data']['status'], 'NOTICE_ACTIVE')
        self.now = notice.earliest_settlement_at
        at_deadline = self.admin_read(self.first)
        self.assertEqual(at_deadline.data['data']['status'], 'SETTLEMENT_PENDING')
        reused_request = self.confirm(self.first, request_id='closure-init-1')
        self.assertEqual(reused_request.status_code, 409)
        self.assertEqual(reused_request.data['errorCode'], 'REQUEST_ID_CONFLICT')
        confirmed = self.confirm(self.first)
        self.assertEqual(confirmed.status_code, 201, confirmed.content)
        self.assertEqual(confirmed.data['data']['status'], 'SETTLEMENT_PENDING')
        self.assertEqual(confirmed.data['data']['store_status'], Store.Status.ACTIVE)
        event = StoreClosureEvent.objects.get(notice=notice)
        self.assertEqual(event.occurred_at, notice.earliest_settlement_at)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Store.Status.ACTIVE)
        self.now += timedelta(days=40)
        self.assertEqual(self.admin_read(self.first).data['data']['status'], 'SETTLEMENT_PENDING')
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Store.Status.ACTIVE)

    def test_repeated_start_replay_and_conflict_never_reset_countdown(self):
        first = self.start(self.first)
        original_start = first.data['data']['started_at']
        original_deadline = first.data['data']['earliest_settlement_at']
        self.now += timedelta(days=10)
        replay = self.start(self.first)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data['data']['replayed'])
        self.assertEqual(replay.data['data']['started_at'], original_start)
        conflict = self.start(self.first, reason='同号但原因不同')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['errorCode'], 'REQUEST_ID_CONFLICT')
        existing = self.start(self.first, request_id='closure-init-2', reason='另一份重复申请也不重启')
        self.assertEqual(existing.status_code, 200)
        self.assertTrue(existing.data['data']['already_started'])
        self.assertEqual(existing.data['data']['started_at'], original_start)
        self.assertEqual(existing.data['data']['earliest_settlement_at'], original_deadline)
        self.assertEqual(StoreClosureNotice.objects.count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='closure.notice_started').count(), 1)
        self.now += timedelta(days=21)
        self.assertEqual(self.admin_read(self.first).data['data']['status'], 'SETTLEMENT_PENDING')
        notice = StoreClosureNotice.objects.get()
        self.assertEqual(notice.reason, '内部原因：经营方申请停止营业')

    def test_clock_failure_and_rollback_cannot_confirm_or_regress_expiry(self):
        self.clock_error = ControlClockUnavailable('test clock offline')
        failed = self.start(self.first)
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.data['errorCode'], 'CLOSURE_CLOCK_UNAVAILABLE')
        self.assertFalse(StoreClosureNotice.objects.exists())
        self.clock_error = None
        started = self.start(self.first)
        notice = StoreClosureNotice.objects.get()
        self.now = notice.started_at + timedelta(days=10)
        self.clock_error = ControlClockUnavailable('test clock offline')
        self.assertEqual(self.admin_read(self.first).status_code, 503)
        unknown = self.public(self.first)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual((unknown.data['data']['status'], unknown.data['data']['clock_status']), ('UNKNOWN', 'UNAVAILABLE'))
        self.assertEqual(self.confirm(self.first).status_code, 503)
        self.assertFalse(StoreClosureEvent.objects.exists())
        self.clock_error = None
        self.now = notice.started_at - timedelta(microseconds=1)
        backwards = self.public(self.first)
        self.assertEqual((backwards.data['data']['status'], backwards.data['data']['clock_status']), ('UNKNOWN', 'REVIEW'))
        rejected = self.confirm(self.first)
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.data['errorCode'], 'CLOSURE_CLOCK_REVIEW')
        self.now = notice.earliest_settlement_at
        self.assertEqual(self.confirm(self.first).status_code, 201)
        self.now = notice.started_at + timedelta(days=5)
        # A durable expiry observation does not regress to NOTICE_ACTIVE.
        self.assertEqual(self.public(self.first).data['data']['status'], 'SETTLEMENT_PENDING')
        self.assertEqual(started.data['data']['started_at'], notice.started_at.isoformat())

    def test_notice_and_expiry_event_are_immutable_and_expiry_is_idempotent(self):
        self.start(self.first)
        notice = StoreClosureNotice.objects.get()
        self.now = notice.earliest_settlement_at
        created = self.confirm(self.first)
        event_id = created.data['data']['id']
        notice.reason = '篡改'
        with self.assertRaises(DjangoValidationError):
            notice.save()
        with self.assertRaises(DjangoValidationError):
            notice.delete()
        with self.assertRaises(DjangoValidationError):
            StoreClosureNotice.objects.filter(pk=notice.pk).update(reason='篡改')
        with self.assertRaises(DjangoValidationError):
            StoreClosureNotice.objects.filter(pk=notice.pk).delete()
        event = StoreClosureEvent.objects.get(pk=event_id)
        event.reason = '篡改'
        with self.assertRaises(DjangoValidationError):
            event.save()
        with self.assertRaises(DjangoValidationError):
            event.delete()
        with self.assertRaises(DjangoValidationError):
            StoreClosureEvent.objects.filter(pk=event.pk).update(reason='篡改')
        with self.assertRaises(DjangoValidationError):
            StoreClosureEvent.objects.filter(pk=event.pk).delete()
        replay = self.confirm(self.first)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data['data']['replayed'])
        conflict = self.confirm(self.first, reason='同号异内容')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['errorCode'], 'REQUEST_ID_CONFLICT')
        existing = self.confirm(self.first, request_id='closure-expiry-2', reason='重复确认不新增事实')
        self.assertEqual(existing.status_code, 200)
        self.assertTrue(existing.data['data']['already_confirmed'])
        self.assertEqual(StoreClosureEvent.objects.count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action__startswith='closure.').count(), 2)
        self.assertEqual(self.super_client.delete(self.url(self.first)).status_code, 405)

    def test_audit_failure_rolls_back_start_and_expiry(self):
        with patch('stores.closure.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            failed_start = self.start(self.first)
        self.assertEqual(failed_start.status_code, 503)
        self.assertFalse(StoreClosureNotice.objects.exists())
        self.start(self.first)
        notice = StoreClosureNotice.objects.get()
        self.now = notice.earliest_settlement_at
        with patch('stores.closure.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            failed_expiry = self.confirm(self.first)
        self.assertEqual(failed_expiry.status_code, 503)
        self.assertFalse(StoreClosureEvent.objects.exists())
        self.assertEqual(self.confirm(self.first).status_code, 201)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Store.Status.ACTIVE)

    def test_public_not_started_active_and_pending_are_redacted(self):
        empty = self.public(self.first)
        self.assertEqual(empty.data['data'], {'status': 'NOT_STARTED', 'announcement_visible': False})
        self.start(
            self.first, reason='内部秘密原因：合同争议与联系人', category='PLATFORM_DIRECTED',
            request_id='closure-secret-request',
        )
        active = self.public(self.first)
        self.assertEqual(active.data['data']['status'], 'NOTICE_ACTIVE')
        self.assertEqual(set(active.data['data']), {
            'status', 'announcement_visible', 'clock_status', 'announcement',
            'started_at', 'earliest_settlement_at', 'notice_days',
        })
        public_text = str(active.data)
        for secret in (
            '内部秘密原因', '合同争议', 'closure-super', 'closure-secret-request',
            'sqlite-test:default', 'PLATFORM_DIRECTED',
        ):
            self.assertNotIn(secret, public_text)
        self.assertFalse(any('amount' in key or 'balance' in key for key in active.data['data']))
        self.assertEqual(active['Cache-Control'], 'no-store')
        self.assertEqual(APIClient(HTTP_HOST='sl.esports.club').post('/api/stores/closure/', {}, format='json').status_code, 405)
        notice = StoreClosureNotice.objects.get()
        self.now = notice.earliest_settlement_at
        pending = self.public(self.first)
        self.assertEqual(pending.data['data']['status'], 'SETTLEMENT_PENDING')
        self.assertIn('不代表余额已返还', pending.data['data']['announcement'])

    def test_guard_is_queryable_but_existing_account_reads_remain_available(self):
        self.assertTrue(new_activity_allowed(self.first))
        self.assertEqual(closure_guard(self.first)['guard_coverage'], 'NOT_ENFORCED_AT_BUSINESS_ENTRYPOINTS')
        assert_new_activity_allowed(self.first)
        self.start(self.first)
        self.assertFalse(new_activity_allowed(self.first))
        with self.assertRaises(ClosureError) as blocked:
            assert_new_activity_allowed(self.first)
        self.assertEqual(blocked.exception.code, 'STORE_CLOSURE_RESTRICTED')
        notice = StoreClosureNotice.objects.get()
        self.now = notice.earliest_settlement_at
        self.confirm(self.first)
        self.assertFalse(new_activity_allowed(self.first))
        response = APIClient(
            HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + self.public_tokens[self.first.slug],
        ).get('/api/users/me/')
        self.assertEqual(response.status_code, 200, response.content)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Store.Status.ACTIVE)
