"""E3 read-only, platform-scoped settlement preflight API."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.db import DatabaseError, connections
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberProfile
from stores.closure_clock import CLOCK_SOURCE, ControlClockEvidence
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from stores.tests_hosts import xinan_store
from wallet.models import ClosureReturnOutbox, Wallet


OBLIGATION_CODES = (
    'ORDERS', 'RESERVATIONS', 'AFTERSALES', 'REPORTS', 'INCOME_HOLDS', 'WITHDRAWALS',
    'DEPOSIT_DEFICITS', 'RECOVERY_PERMITS', 'GIFT_APPROVALS', 'REWARD_CLAIMS',
    'PAYROLL', 'OTHER_RIGHTS',
)
TOTALS = {
    'wallet_units': 0, 'principal_units': 0, 'gift_units': 0, 'compensation_units': 0,
    'points_hundredths': 0, 'frozen_units': 0, 'refund_fen': 0, 'paid_fen': 0,
}


def obligations_result(*, complete=True, blocked=False):
    blockers = ([{'code': 'OPEN_ORDERS', 'resource': 'order', 'reference': 'order:123'}]
                if blocked else [])
    if not complete:
        blockers = [{'code': 'READ_INCOMPLETE', 'resource': 'store', 'reference': 'store:1'}]
    return {
        'complete': complete,
        'status': 'UNKNOWN' if not complete else 'BLOCKED' if blocked else 'CLEAR',
        'metrics': [
            {'code': code, 'count': None if not complete else 1 if blocked and code == 'ORDERS' else 0}
            for code in OBLIGATION_CODES
        ],
        'blockers': blockers,
        'facts_sha256': ('c' if complete else 'd') * 64,
    }


def funds_result(store, *, complete=True, blocked=True, digest='a'):
    blockers = []
    if blocked:
        blockers = [{
            'code': 'READY_TO_PREPARE', 'object_type': 'BOSS_ACCOUNT',
            'object_id': '807001', 'account_id': 807001, 'business_cycle': 1,
        }]
    if not complete:
        blockers = [{'code': 'READ_INCOMPLETE', 'object_type': 'STORE', 'object_id': str(store.pk)}]
    return {'funds': {
        'complete': complete,
        'status': 'UNKNOWN' if not complete else 'BLOCKED' if blockers else 'CLEAR',
        'counts': {
            'accounts': 1, 'completed': 0 if blocked or not complete else 1,
            'zero_balance': 0 if blocked or not complete else 1,
        },
        'totals': dict(TOTALS) if complete else None,
        'blockers': blockers,
        'facts_sha256': digest * 64,
    }}


@override_settings(
    PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    STORE_HOST_BINDINGS={
        'sl.esports.club': 'shenglang', 'xa.esports.club': 'xinan', 'testserver': 'shenglang',
    },
)
class PlatformClosureReadinessTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.first.status = self.first.Status.ACTIVE
        self.first.save(update_fields=['status', 'updated_at'])
        self.second = xinan_store()
        self.super = self.actor('readiness-api-super', 'SUPER_ADMIN', '*', '13800009801')
        self.finance = self.actor('readiness-api-finance', 'FINANCE', '*', '13800009802')
        self.scoped = self.actor('readiness-api-scoped', 'FINANCE', self.second.slug, '13800009803')
        self.viewer = self.actor('readiness-api-viewer', 'ORDER_VIEWER', '*', '13800009804')
        self.client = self.platform_client(self.super)
        self.now = datetime(2026, 11, 1, 4, 0, tzinfo=datetime_timezone.utc)
        patcher = patch('stores.closure.read_control_clock', side_effect=lambda: ControlClockEvidence(
            self.now, CLOCK_SOURCE, 'sqlite-test:default',
        ))
        patcher.start()
        self.addCleanup(patcher.stop)

    def actor(self, username, role, scope, phone):
        account = PlatformAccount.objects.create(
            username=username, nickname='内部姓名-' + username, phone=phone, is_phone_verified=True,
        )
        PlatformRoleGrant.objects.create(account=account, role=role, scope_slug=scope)
        return account

    def platform_client(self, actor, host='admin.esports.club'):
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def url(self, store):
        return f'/api/admin/platform/store-management/{store.pk}/closure/settlement-readiness'

    def enter_settlement(self, store):
        started = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure',
            {'reason_category': 'OPERATOR_REQUEST', 'reason': '内部闭店秘密原因',
             'client_request_id': f'readiness-api-closure-{store.slug}'}, format='json',
        )
        self.assertEqual(started.status_code, 201, started.content)
        self.now += timedelta(days=31)
        confirmed = self.client.post(
            f'/api/admin/platform/store-management/{store.pk}/closure/confirm-expiry',
            {'reason': '内部到期确认秘密', 'client_request_id': f'readiness-api-expiry-{store.slug}'},
            format='json',
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.content)

    def test_independent_permission_host_scope_and_state_are_enforced(self):
        self.enter_settlement(self.first)
        with (
            patch('stores.platform_closure_readiness.build_closure_funds_readiness',
                  side_effect=lambda store: funds_result(store)),
            patch('stores.platform_closure_readiness.closure_obligation_readiness',
                  side_effect=lambda store: obligations_result()),
        ):
            self.assertEqual(APIClient(HTTP_HOST='admin.esports.club').get(self.url(self.first)).status_code, 401)
            self.assertEqual(self.platform_client(self.viewer).get(self.url(self.first)).status_code, 403)
            self.assertEqual(self.platform_client(self.scoped).get(self.url(self.first)).status_code, 403)
            self.assertEqual(self.platform_client(self.super, 'sl.esports.club').get(self.url(self.first)).status_code, 403)
            self.assertEqual(self.platform_client(self.finance).get(self.url(self.first)).status_code, 200)
        with store_context(self.first):
            boss = ClubAccount.objects.create_account(
                id=807001, username='readiness-api-boss', account_type='BOSS',
                account_no='XA807001', nickname='老板隐私',
            )
            token = issue_account_tokens(boss)[0]
        self.assertEqual(APIClient(
            HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token,
        ).get(self.url(self.first)).status_code, 401)
        self.assertEqual(self.client.get(self.url(self.second)).status_code, 409)
        for path, headers in (
            (self.url(self.first) + '?store=xinan', {}),
            (self.url(self.first), {'HTTP_X_STORE_SLUG': 'xinan'}),
        ):
            response = self.client.get(path, **headers)
            self.assertIn(response.status_code, (400, 403), response.content)

    def test_pending_contract_is_complete_redacted_stable_and_never_finalizable(self):
        self.enter_settlement(self.first)
        with (
            patch('stores.platform_closure_readiness.build_closure_funds_readiness',
                  side_effect=lambda store: funds_result(store)),
            patch('stores.platform_closure_readiness.closure_obligation_readiness',
                  side_effect=lambda store: obligations_result()),
        ):
            first = self.client.get(self.url(self.first))
            second = self.client.get(self.url(self.first))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first['Cache-Control'], 'no-store')
        data = first.data['data']
        self.assertEqual(set(data), {
            'status', 'complete', 'can_finalize', 'observed_at', 'snapshot_sha256',
            'funds', 'obligations', 'policy', 'blocking_reasons',
        })
        self.assertEqual((data['status'], data['complete'], data['can_finalize']), ('BLOCKED', True, False))
        self.assertEqual(set(data['funds']), {
            'complete', 'status', 'account_count', 'completed_count', 'zero_balance_count',
            'totals', 'blockers', 'facts_sha256',
        })
        self.assertEqual(data['funds']['blockers'], [{
            'code': 'READY_TO_PREPARE', 'resource': 'boss_account', 'reference': 'account:807001',
        }])
        self.assertEqual(set(data['funds']['totals']), set(TOTALS))
        self.assertEqual([row['code'] for row in data['obligations']['metrics']], list(OBLIGATION_CODES))
        self.assertEqual(data['policy'], {
            'complete': True, 'fee_rules': 'PENDING', 'final_write_barrier': 'UNAVAILABLE',
            'blockers': [
                {'code': 'FEE_RULES_PENDING', 'resource': 'closure_policy', 'reference': 'PC04'},
                {'code': 'FINAL_WRITE_BARRIER_UNAVAILABLE', 'resource': 'closure_policy',
                 'reference': 'shared-write-barrier'},
            ],
        })
        self.assertEqual(data['blocking_reasons'], [
            'FUNDS_REVIEW_REQUIRED', 'FEE_RULES_PENDING', 'FINAL_WRITE_BARRIER_UNAVAILABLE',
        ])
        self.assertEqual(first.data['data']['snapshot_sha256'], second.data['data']['snapshot_sha256'])
        rendered = str(first.data)
        for secret in ('内部闭店秘密原因', '内部到期确认秘密', '内部姓名', '807001老板', 'sqlite-test:default'):
            self.assertNotIn(secret, rendered)

    def test_incomplete_reads_and_changed_facts_are_explicit(self):
        self.enter_settlement(self.first)
        with (
            patch('stores.platform_closure_readiness.build_closure_funds_readiness',
                  side_effect=lambda store: funds_result(store, complete=False)),
            patch('stores.platform_closure_readiness.closure_obligation_readiness',
                  side_effect=lambda store: obligations_result(complete=False)),
        ):
            response = self.client.get(self.url(self.first))
        self.assertEqual(response.status_code, 200, response.content)
        data = response.data['data']
        self.assertEqual((data['status'], data['complete']), ('UNKNOWN', False))
        self.assertIsNone(data['funds']['totals'])
        self.assertTrue(all(row['count'] is None for row in data['obligations']['metrics']))
        self.assertIn('READ_INCOMPLETE', data['blocking_reasons'])
        with (
            patch('stores.platform_closure_readiness.build_closure_funds_readiness',
                  side_effect=lambda store: funds_result(store, digest='e')),
            patch('stores.platform_closure_readiness.closure_obligation_readiness',
                  side_effect=lambda store: obligations_result()),
        ):
            changed = self.client.get(self.url(self.first))
        self.assertNotEqual(data['snapshot_sha256'], changed.data['data']['snapshot_sha256'])

    def test_malformed_scanner_projection_fails_closed(self):
        self.enter_settlement(self.first)
        damaged = funds_result(self.first, blocked=False)
        damaged['funds']['facts_sha256'] = 'not-a-digest'
        damaged['funds']['counts']['accounts'] = None
        with (
            patch('stores.platform_closure_readiness.build_closure_funds_readiness',
                  side_effect=lambda store: damaged),
            patch('stores.platform_closure_readiness.closure_obligation_readiness',
                  side_effect=lambda store: obligations_result()),
        ):
            response = self.client.get(self.url(self.first))
        self.assertEqual(response.status_code, 200, response.content)
        data = response.data['data']
        self.assertEqual((data['status'], data['complete'], data['can_finalize']),
                         ('UNKNOWN', False, False))
        self.assertEqual(data['funds']['status'], 'UNKNOWN')
        self.assertIsNone(data['funds']['totals'])
        self.assertRegex(data['funds']['facts_sha256'], r'^[0-9a-f]{64}$')
        self.assertIn('READ_INCOMPLETE', data['blocking_reasons'])

    def test_null_obligation_counts_fail_closed_even_when_scanner_claims_clear(self):
        self.enter_settlement(self.first)
        for unknown_indexes in (set(range(len(OBLIGATION_CODES))), {1, 5, 11}):
            with self.subTest(unknown_indexes=unknown_indexes):
                damaged = obligations_result()
                for index in unknown_indexes:
                    damaged['metrics'][index]['count'] = None
                with (
                    patch('stores.platform_closure_readiness.build_closure_funds_readiness',
                          side_effect=lambda store: funds_result(store, blocked=False)),
                    patch('stores.platform_closure_readiness.closure_obligation_readiness',
                          return_value=damaged),
                ):
                    response = self.client.get(self.url(self.first))
                self.assertEqual(response.status_code, 200, response.content)
                data = response.data['data']
                self.assertEqual((data['status'], data['complete'], data['can_finalize']),
                                 ('UNKNOWN', False, False))
                self.assertEqual((data['obligations']['complete'], data['obligations']['status']),
                                 (False, 'UNKNOWN'))
                self.assertEqual(
                    [row['count'] for row in data['obligations']['metrics']],
                    [None if index in unknown_indexes else 0 for index in range(len(OBLIGATION_CODES))],
                )
                self.assertIn('READ_INCOMPLETE', {
                    row['code'] for row in data['obligations']['blockers']
                })
                self.assertLessEqual(
                    {'BUSINESS_OBLIGATIONS', 'READ_INCOMPLETE', 'FEE_RULES_PENDING',
                     'FINAL_WRITE_BARRIER_UNAVAILABLE'},
                    set(data['blocking_reasons']),
                )

    def test_store_database_failure_is_503_not_an_empty_success(self):
        self.enter_settlement(self.first)
        with patch('wallet.closure_settlement_readiness.closure_return_list',
                   side_effect=DatabaseError('tenant offline')):
            response = self.client.get(self.url(self.first))
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.data['errorCode'], 'CLOSURE_READINESS_UNAVAILABLE')

    def test_get_has_no_business_or_audit_writes(self):
        self.enter_settlement(self.first)
        with store_context(self.first):
            account = ClubAccount.objects.create_account(
                id=807001, username='readiness-zero', account_type='BOSS',
                account_no='XA807001', nickname='零余额老板',
            )
            Wallet.objects.create(account=account, balance=0, frozen_amount=0)
            MemberProfile.objects.create(account=account, points_balance=0)
        audits = PlatformAuditEvent.objects.count()
        with store_context(self.first):
            outboxes = ClosureReturnOutbox.objects.count()
        with (
            CaptureQueriesContext(connections['default']) as control_queries,
            CaptureQueriesContext(connections[self.first.database_alias]) as tenant_queries,
        ):
            response = self.client.get(self.url(self.first))
        self.assertEqual(response.status_code, 200, response.content)
        data = response.data['data']
        self.assertEqual(data['status'], 'UNKNOWN')
        self.assertEqual(data['can_finalize'], False)
        self.assertEqual([row['code'] for row in data['obligations']['metrics']], list(OBLIGATION_CODES))
        counts = {row['code']: row['count'] for row in data['obligations']['metrics']}
        self.assertIsNone(counts['PAYROLL'])
        self.assertIsNone(counts['OTHER_RIGHTS'])
        writes = []
        for queries in (control_queries, tenant_queries):
            writes.extend(row['sql'] for row in queries.captured_queries
                          if row['sql'].lstrip().split(None, 1)[0].upper() in {
                              'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP',
                          })
        self.assertEqual(writes, [])
        self.assertEqual(PlatformAuditEvent.objects.count(), audits)
        with store_context(self.first):
            self.assertEqual(ClosureReturnOutbox.objects.count(), outboxes)
