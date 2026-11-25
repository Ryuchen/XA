"""SA02 store guarantee: scoped platform writes, immutable offline evidence and public projection."""
import hashlib
from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens

from .context import resolve_store, store_context
from .models import (
    PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store,
    StoreGuaranteeContract, StoreGuaranteeEvent, StoreLicenseContract,
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
class StoreGuaranteeTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.super = self.platform_actor('guarantee-super', 'SUPER_ADMIN', '*', '13800007001')
        self.finance = self.platform_actor('guarantee-finance', 'FINANCE', '*', '13800007002')
        self.first_finance = self.platform_actor('guarantee-first-finance', 'FINANCE', self.first.slug, '13800007003')
        self.second_finance = self.platform_actor('guarantee-second-finance', 'FINANCE', self.second.slug, '13800007004')
        self.viewer = self.platform_actor('guarantee-order-viewer', 'ORDER_VIEWER', '*', '13800007005')
        self.super_client = self.platform_client(self.super)
        self.finance_client = self.platform_client(self.finance)
        self.first_finance_client = self.platform_client(self.first_finance)
        self.second_finance_client = self.platform_client(self.second_finance)
        self.viewer_client = self.platform_client(self.viewer)
        self.public_tokens = {}
        self.public_accounts = {}
        for store in (self.first, self.second):
            with store_context(store):
                account = ClubAccount.objects.create_account(
                    id=807001, username='guarantee-same-boss', account_type='BOSS',
                    account_no='XA807001', nickname=store.name + '老板',
                )
                self.public_accounts[store.slug] = account
                self.public_tokens[store.slug] = issue_account_tokens(account)[0]

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
        return f'/api/admin/platform/store-management/{store.pk}/guarantee{suffix}'

    def configure(self, store, amount, *, version=0, client=None, reason='平台合同额度核对', source=None):
        payload = {
            'amount_due_fen': amount, 'expected_version': version, 'reason': reason,
        }
        if source is not None:
            payload['source_license_contract_id'] = str(source.pk)
        return (client or self.super_client).put(self.url(store), payload, format='json')

    def payload(self, amount=1000, **overrides):
        data = {
            'business_key': 'DEP-2026-0001',
            'client_request_id': 'guarantee-request-0001',
            'direction': 'RECEIPT',
            'amount_fen': amount,
            'occurred_at': '2026-11-20T10:00:00+08:00',
            'evidence_sha256': hashlib.sha256(b'offline-evidence-0001').hexdigest(),
            'evidence_ref': 'offline:2026/11/receipt-0001',
        }
        data.update(overrides)
        return data

    def record(self, store, payload=None, *, client=None, **overrides):
        body = payload or self.payload(**overrides)
        return (client or self.finance_client).post(self.url(store, '/events'), body, format='json')

    def public(self, store, *, token=True):
        host = 'sl.esports.club' if store == self.first else 'xa.esports.club'
        authorization = 'Bearer ' + self.public_tokens[store.slug] if token else ''
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION=authorization).get('/api/stores/guarantee/')

    def test_platform_permissions_are_independent_and_host_scoped(self):
        self.assertEqual(self.configure(self.first, 10000).status_code, 200)
        self.assertEqual(APIClient(HTTP_HOST='admin.esports.club').get(self.url(self.first)).status_code, 401)
        for client in (self.viewer_client, self.second_finance_client):
            self.assertEqual(client.get(self.url(self.first)).status_code, 403)
            self.assertEqual(self.configure(self.first, 9000, version=1, client=client).status_code, 403)
            self.assertEqual(self.record(self.first, client=client).status_code, 403)
        self.assertEqual(self.finance_client.get(self.url(self.first)).status_code, 200)
        self.assertEqual(self.configure(self.first, 9000, version=1, client=self.finance_client).status_code, 403)
        recorded = self.record(self.first, client=self.first_finance_client)
        self.assertEqual(recorded.status_code, 201, recorded.content)
        boss = APIClient(
            HTTP_HOST='admin.esports.club',
            HTTP_AUTHORIZATION='Bearer ' + self.public_tokens[self.first.slug],
        )
        self.assertEqual(boss.get(self.url(self.first)).status_code, 401)
        wrong_host = self.platform_client(self.super, host='sl.esports.club')
        self.assertEqual(wrong_host.get(self.url(self.first)).status_code, 403)

    def test_two_stores_with_same_local_numeric_ids_share_no_guarantee_facts(self):
        self.assertEqual(self.public_accounts[self.first.slug].pk, self.public_accounts[self.second.slug].pk)
        self.assertEqual(self.configure(self.first, 10000).status_code, 200)
        self.assertEqual(self.configure(self.second, 20000).status_code, 200)
        first = self.record(self.first, amount_fen=4000)
        second = self.record(self.second, amount_fen=7000)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertNotEqual(first.data['data']['id'], second.data['data']['id'])
        first_public = self.public(self.first)
        second_public = self.public(self.second)
        self.assertEqual((first_public.data['data']['amount_due_fen'], first_public.data['data']['net_received_fen']), (10000, 4000))
        self.assertEqual((second_public.data['data']['amount_due_fen'], second_public.data['data']['net_received_fen']), (20000, 7000))
        self.assertEqual(StoreGuaranteeEvent.objects.filter(store=self.first).count(), 1)
        self.assertEqual(StoreGuaranteeEvent.objects.filter(store=self.second).count(), 1)
        cross = APIClient(HTTP_HOST='sl.esports.club').get('/api/stores/guarantee/?store=xinan')
        self.assertEqual(cross.status_code, 400)

    def test_amount_boundaries_and_net_conservation(self):
        self.assertEqual(self.configure(self.first, 10000).status_code, 200)
        for amount in (0, -1, True, 2 ** 63):
            response = self.record(self.first, amount_fen=amount)
            self.assertEqual(response.status_code, 400, (amount, response.content))
        self.assertEqual(self.record(self.first, amount_fen=10000).status_code, 201)
        self.assertEqual(self.record(self.first, business_key='DEP-OVER', client_request_id='over-1', amount_fen=1).status_code, 409)
        self.assertEqual(self.record(
            self.first, business_key='REF-0001', client_request_id='refund-1', direction='REFUND', amount_fen=4000,
        ).status_code, 201)
        self.assertEqual(self.record(
            self.first, business_key='REF-OVER', client_request_id='refund-over', direction='REFUND', amount_fen=6001,
        ).status_code, 409)
        # Net, rather than cumulative gross receipts, is the invariant.
        self.assertEqual(self.record(
            self.first, business_key='DEP-0002', client_request_id='receipt-2', amount_fen=4000,
        ).status_code, 201)
        summary = self.finance_client.get(self.url(self.first)).data['data']['contract']
        self.assertEqual((summary['amount_received_fen'], summary['amount_refunded_fen'], summary['net_received_fen']), (14000, 4000, 10000))
        self.assertEqual(self.record(
            self.first, business_key='REF-0002', client_request_id='refund-2', direction='REFUND', amount_fen=10000,
        ).status_code, 201)
        self.assertEqual(self.record(
            self.first, business_key='REF-EMPTY', client_request_id='refund-empty', direction='REFUND', amount_fen=1,
        ).status_code, 409)
        self.assertEqual(self.configure(self.first, 0, version=1, reason='核对后无缴纳要求').status_code, 200)

    def test_contract_source_revision_and_lower_bound_are_checked(self):
        self.assertEqual(self.configure(self.first, 10000).status_code, 200)
        self.assertEqual(self.record(self.first, amount_fen=6000).status_code, 201)
        self.assertEqual(self.configure(self.first, 5999, version=1).status_code, 409)
        self.assertEqual(self.configure(self.first, 7000, version=0).status_code, 409)
        other = StoreLicenseContract.objects.create(
            store=self.second, amount_due_fen=0, created_by=self.super, updated_by=self.super,
        )
        self.assertEqual(self.configure(self.first, 10000, version=1, source=other).status_code, 400)
        own = StoreLicenseContract.objects.create(
            store=self.first, amount_due_fen=10000, created_by=self.super, updated_by=self.super,
        )
        changed = self.configure(self.first, 10000, version=1, source=own)
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.data['data']['source_license_contract_id'], str(own.pk))
        self.assertEqual(changed.data['data']['version'], 2)

    def test_replay_conflict_business_key_and_failed_attempt_retry(self):
        self.configure(self.first, 10000)
        first = self.record(self.first, amount_fen=4000)
        self.assertEqual(first.status_code, 201, first.content)
        replay = self.record(self.first, amount_fen=4000)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.data['data']['replayed'])
        self.assertEqual(replay.data['data']['id'], first.data['data']['id'])
        self.assertEqual(StoreGuaranteeEvent.objects.count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='guarantee.offline_event_recorded').count(), 1)
        conflict = self.record(self.first, amount_fen=4001)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['errorCode'], 'REQUEST_ID_CONFLICT')
        duplicate_key = self.record(self.first, client_request_id='another-request', amount_fen=1000)
        self.assertEqual(duplicate_key.status_code, 409)
        self.assertEqual(duplicate_key.data['errorCode'], 'GUARANTEE_BUSINESS_KEY_CONFLICT')
        too_much = self.record(self.first, business_key='DEP-RETRY', client_request_id='retry-request', amount_fen=11000)
        self.assertEqual(too_much.status_code, 409)
        corrected = self.record(self.first, business_key='DEP-RETRY', client_request_id='retry-request', amount_fen=5000)
        self.assertEqual(corrected.status_code, 201, corrected.content)
        self.assertEqual(self.finance_client.get(self.url(self.first)).data['data']['contract']['net_received_fen'], 9000)

    def test_history_is_immutable_and_errors_use_compensation_events(self):
        self.configure(self.first, 10000)
        original = self.record(self.first, amount_fen=5000).data['data']
        row = StoreGuaranteeEvent.objects.get(pk=original['id'])
        row.amount_fen = 1
        with self.assertRaises(DjangoValidationError):
            row.save()
        with self.assertRaises(DjangoValidationError):
            row.delete()
        with self.assertRaises(DjangoValidationError):
            StoreGuaranteeEvent.objects.filter(pk=row.pk).update(amount_fen=1)
        with self.assertRaises(DjangoValidationError):
            StoreGuaranteeEvent.objects.filter(pk=row.pk).delete()
        same_direction = self.record(
            self.first, business_key='COMP-BAD', client_request_id='comp-bad', amount_fen=5000,
            compensates_event_id=original['id'],
        )
        self.assertEqual(same_direction.status_code, 400)
        wrong_amount = self.record(
            self.first, business_key='COMP-BAD-2', client_request_id='comp-bad-2', direction='REFUND',
            amount_fen=4999, compensates_event_id=original['id'],
        )
        self.assertEqual(wrong_amount.status_code, 400)
        compensation = self.record(
            self.first, business_key='COMP-0001', client_request_id='comp-1', direction='REFUND',
            amount_fen=5000, compensates_event_id=original['id'],
        )
        self.assertEqual(compensation.status_code, 201, compensation.content)
        self.assertEqual(compensation.data['data']['compensates_event_id'], original['id'])
        self.assertEqual(StoreGuaranteeEvent.objects.count(), 2)
        self.assertTrue(StoreGuaranteeEvent.objects.filter(pk=original['id'], amount_fen=5000).exists())
        self.assertEqual(self.finance_client.get(self.url(self.first)).data['data']['contract']['net_received_fen'], 0)
        repeated = self.record(
            self.first, business_key='COMP-REPEAT', client_request_id='comp-repeat', direction='REFUND',
            amount_fen=5000, compensates_event_id=original['id'],
        )
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.data['errorCode'], 'GUARANTEE_ALREADY_COMPENSATED')

    def test_audit_failure_rolls_back_contract_and_event(self):
        with patch('stores.guarantee.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            failed_contract = self.configure(self.first, 10000)
        self.assertEqual(failed_contract.status_code, 503)
        self.assertFalse(StoreGuaranteeContract.objects.exists())
        self.configure(self.first, 10000)
        with patch('stores.guarantee.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            failed_event = self.record(self.first, amount_fen=5000)
            failed_update = self.configure(self.first, 9000, version=1)
        self.assertEqual(failed_event.status_code, 503)
        self.assertEqual(failed_update.status_code, 503)
        self.assertFalse(StoreGuaranteeEvent.objects.exists())
        contract = StoreGuaranteeContract.objects.get()
        self.assertEqual((contract.amount_due_fen, contract.version), (10000, 1))
        self.assertEqual(self.record(self.first, amount_fen=5000).status_code, 201)

    def test_public_four_states_and_sensitive_fields_never_leak(self):
        observed = []
        pending = self.public(self.first)
        observed.append(pending.data['data']['status'])
        self.assertEqual(pending.data['data']['status'], 'PENDING_REVIEW')
        self.assertIsNone(pending.data['data']['amount_due_fen'])
        self.assertIsNone(pending.data['data']['net_received_fen'])
        self.configure(self.first, 0, reason='内部备注：零额合同仅作测试')
        no_requirement = self.public(self.first)
        observed.append(no_requirement.data['data']['status'])
        self.assertEqual((no_requirement.data['data']['status'], no_requirement.data['data']['amount_due_fen']), ('NOT_COVERED', 0))
        self.configure(self.first, 10000, version=1, reason='内部备注：保密合同编号')
        self.record(self.first, amount_fen=4000)
        partial = self.public(self.first)
        observed.append(partial.data['data']['status'])
        self.assertEqual((partial.data['data']['status'], partial.data['data']['net_received_fen']), ('PARTIAL', 4000))
        self.record(self.first, business_key='DEP-0002', client_request_id='receipt-2', amount_fen=6000)
        covered = self.public(self.first)
        observed.append(covered.data['data']['status'])
        self.assertEqual((covered.data['data']['status'], covered.data['data']['net_received_fen'], covered.data['data']['amount_remaining_fen']), ('COVERED', 10000, 0))
        self.assertEqual(set(observed), {'PENDING_REVIEW', 'NOT_COVERED', 'PARTIAL', 'COVERED'})
        self.assertEqual(set(covered.data['data']), {
            'status', 'label', 'currency', 'amount_due_fen', 'net_received_fen',
            'amount_remaining_fen', 'record_basis',
        })
        public_text = str(covered.data)
        for secret in (
            'guarantee-super', 'guarantee-finance', '保密合同编号', 'DEP-2026-0001',
            'guarantee-request-0001', 'offline:2026/11/receipt-0001',
            hashlib.sha256(b'offline-evidence-0001').hexdigest(),
        ):
            self.assertNotIn(secret, public_text)
        self.assertEqual(covered['Cache-Control'], 'no-store')
        self.assertEqual(APIClient(HTTP_HOST='sl.esports.club').post('/api/stores/guarantee/', {}, format='json').status_code, 405)

    def test_public_pending_for_incomplete_evidence_or_invalid_history(self):
        self.configure(self.first, 10000)
        contract = StoreGuaranteeContract.objects.get(store=self.first)
        StoreGuaranteeEvent.objects.create(
            store=self.first, contract=contract, contract_version=1,
            business_key='LEGACY-MISSING', client_request_id='legacy-missing', request_fingerprint='a' * 64,
            direction='RECEIPT', amount_fen=5000, occurred_at=timezone.now(),
            evidence_sha256='', evidence_ref='', created_by=self.super,
        )
        incomplete = self.public(self.first)
        self.assertEqual(incomplete.data['data']['status'], 'PENDING_REVIEW')
        self.assertIsNone(incomplete.data['data']['amount_due_fen'])
        self.assertIsNone(incomplete.data['data']['net_received_fen'])
        self.configure(self.second, 10000)
        second_contract = StoreGuaranteeContract.objects.get(store=self.second)
        StoreGuaranteeEvent.objects.create(
            store=self.second, contract=second_contract, contract_version=1,
            business_key='LEGACY-NEGATIVE', client_request_id='legacy-negative', request_fingerprint='b' * 64,
            direction='REFUND', amount_fen=100, occurred_at=timezone.now(),
            evidence_sha256='c' * 64, evidence_ref='offline:legacy/negative', created_by=self.super,
        )
        invalid = self.public(self.second)
        self.assertEqual(invalid.data['data']['status'], 'PENDING_REVIEW')
        self.assertIsNone(invalid.data['data']['amount_due_fen'])
        blocked = self.record(
            self.second, business_key='AFTER-INVALID', client_request_id='after-invalid', amount_fen=100,
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data['errorCode'], 'GUARANTEE_HISTORY_INVALID')

    def test_offline_evidence_rejects_sensitive_or_incomplete_references(self):
        self.assertEqual(self.record(self.first).status_code, 409)
        self.configure(self.first, 10000)
        for index, reference in enumerate((
            '6222020200112233445', 'bank-card:6222 0202 0011 2233 445',
            'https://outside.invalid/evidence?id=1',
        )):
            response = self.record(
                self.first, business_key=f'BAD-{index}', client_request_id=f'bad-{index}', evidence_ref=reference,
            )
            self.assertEqual(response.status_code, 400, (reference, response.content))
        for index, digest in enumerate(('', 'not-a-sha256', 'g' * 64)):
            response = self.record(
                self.first, business_key=f'DIGEST-{index}', client_request_id=f'digest-{index}', evidence_sha256=digest,
            )
            self.assertEqual(response.status_code, 400)
        self.assertFalse(StoreGuaranteeEvent.objects.exists())
        uppercase = self.record(
            self.first, business_key='UPPERCASE-DIGEST', client_request_id='uppercase-digest',
            evidence_sha256='A' * 64,
        )
        self.assertEqual(uppercase.status_code, 201, uppercase.content)
        self.assertEqual(uppercase.data['data']['evidence_sha256'], 'a' * 64)
        valid = self.record(self.first)
        self.assertEqual(valid.status_code, 201, valid.content)
        self.assertEqual(valid.data['data']['channel_status'], 'NOT_REQUESTED')
        self.assertIn('未发起或确认任何支付渠道结果', valid.data['msg'])
