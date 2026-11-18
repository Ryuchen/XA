from uuid import uuid4
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from stores.context import resolve_store, store_context
from stores.models import (PlatformAccount, PlatformAuditEvent, PlatformPassRepaymentClaim,
                           PlatformPassRepaymentOperation, PlatformRoleGrant)
from stores.platform_auth import issue_tokens
from stores.tests_hosts import xinan_store
from users.models import PassRefundRepaymentAction, PassRefundRepaymentAllocation, PassRefundRepaymentReceipt


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True)
class PlatformPassRepaymentTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        from console.grab_config import save_grab_window
        for store in (self.first, self.second):
            with store_context(store):
                save_grab_window(30, 0, lambda *args: None)
        self.finance = PlatformAccount(username='repayment-finance', phone='13800001111', is_phone_verified=True)
        self.finance.set_password('platform-password'); self.finance.save()
        self.grant = PlatformRoleGrant.objects.create(account=self.finance, role='FINANCE', scope_slug='*')
        self.client = APIClient(HTTP_HOST='admin.esports.club')
        self.token = issue_tokens(self.finance, 'SMS', self.finance.phone)[0]
        self.data = {'request_id': str(uuid4()), 'external_reference': 'TX-100', 'amount': 500,
                     'received_at': timezone.now().isoformat(), 'evidence': '到账凭据'}

    def post(self, slug, data=None, token=None):
        return self.client.post(f'/api/admin/platform/pass-refund-repayments/{slug}/register', data or self.data,
            format='json', HTTP_AUTHORIZATION='Bearer ' + (token or self.token))

    def test_scoped_registration_and_cross_store_duplicate(self):
        first = self.post('xinan'); self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['data']['status'], 'RECORDED')
        self.assertEqual(self.post('xinan').data, first.data)
        self.assertEqual(PlatformPassRepaymentClaim.objects.count(), 1)
        self.assertTrue(PlatformAuditEvent.objects.filter(action='pass_repayment.register_recorded').exists())
        with store_context(self.second):
            self.assertEqual(PassRefundRepaymentReceipt.objects.count(), 1)
            self.assertIsNone(PassRefundRepaymentReceipt.objects.get().verified_at)
        duplicate = {**self.data, 'request_id': str(uuid4()), 'external_reference': 'tx-100'}
        response = self.post('shenglang', duplicate)
        self.assertEqual((response.status_code, response.data['errorCode']), (409, 'RECEIPT_DUPLICATE'))
        with store_context(self.first):
            self.assertEqual(PassRefundRepaymentReceipt.objects.count(), 0)
        conflict = self.post('xinan', {**self.data, 'amount': 400})
        self.assertEqual((conflict.status_code, conflict.data['errorCode']), (409, 'REQUEST_CONFLICT'))

    def test_target_commit_then_directory_failure_retries_original_request(self):
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.register_recorded':
                raise DatabaseError('simulated directory failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            response = self.post('xinan')
        self.assertEqual((response.status_code, response.data['errorCode']), (503, 'REPAYMENT_RESULT_UNCERTAIN'))
        claim = PlatformPassRepaymentClaim.objects.get()
        self.assertIsNone(claim.tenant_receipt_id)
        with store_context(self.second):
            self.assertEqual(PassRefundRepaymentReceipt.objects.count(), 1)
        another = self.post('xinan', {**self.data, 'request_id': str(uuid4()), 'external_reference': 'TX-OTHER'})
        self.assertEqual((another.status_code, another.data['errorCode']), (409, 'REPAYMENT_BUSY'))
        replay = self.post('xinan'); self.assertEqual(replay.status_code, 200, replay.data)
        claim.refresh_from_db(); self.assertIsNotNone(claim.tenant_receipt_id)
        with store_context(self.second):
            self.assertEqual(PassRefundRepaymentReceipt.objects.count(), 1)

    def test_replay_does_not_claim_missing_tenant_record_was_recorded(self):
        self.assertEqual(self.post('xinan').status_code, 200)
        with store_context(self.second):
            PassRefundRepaymentReceipt.objects.all().delete()
        response = self.post('xinan')
        self.assertEqual((response.status_code, response.data['errorCode']), (409, 'REPAYMENT_REVIEW'))
        with store_context(self.second): self.assertEqual(PassRefundRepaymentReceipt.objects.count(), 0)

    def test_authorization_disabled_feature_and_invalid_data(self):
        self.grant.scope_slug = 'shenglang'; self.grant.save(update_fields=['scope_slug'])
        self.assertEqual(self.post('xinan').status_code, 404)
        self.grant.is_active = False; self.grant.save(update_fields=['is_active'])
        self.assertEqual(self.post('shenglang').status_code, 401)
        self.grant.is_active = True; self.grant.save(update_fields=['is_active'])
        for change in ({'amount': True}, {'received_at': '2026-55-01'}, {'external_reference': ''}):
            self.assertEqual(self.post('shenglang', {**self.data, **change}).status_code, 400)
        with self.settings(PLATFORM_PASS_REPAYMENT_ENABLED=False):
            self.assertEqual(self.post('shenglang').status_code, 503)
        self.assertEqual(PlatformPassRepaymentClaim.objects.count(), 0)

    def test_super_admin_without_finance_grant_cannot_register(self):
        self.grant.is_active = False; self.grant.save(update_fields=['is_active'])
        PlatformRoleGrant.objects.create(account=self.finance, role='SUPER_ADMIN', scope_slug='*')
        self.assertEqual(self.post('xinan').status_code, 404)
        self.assertEqual(PlatformPassRepaymentClaim.objects.count(), 0)

    def action(self, claim_id, kind, data, slug='xinan'):
        return self.client.post(f'/api/admin/platform/pass-refund-repayments/{slug}/{claim_id}/{kind}', data,
            format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)

    def reconciliation(self, slug='xinan'):
        return self.client.get(f'/api/admin/platform/pass-refund-repayments/{slug}/reconciliation',
            HTTP_AUTHORIZATION='Bearer ' + self.token)

    def recovery(self, target_type, target_id, token=None, slug='xinan'):
        return self.client.post(f'/api/admin/platform/pass-refund-repayments/{slug}/recover/{target_type}/{target_id}',
            {}, format='json', HTTP_AUTHORIZATION='Bearer ' + (token or self.token))

    def void(self, target_type, target_id, reason='已核对门店无记录', token=None, slug='xinan'):
        return self.client.post(f'/api/admin/platform/pass-refund-repayments/{slug}/void/{target_type}/{target_id}',
            {'reason': reason}, format='json', HTTP_AUTHORIZATION='Bearer ' + (token or self.token))

    def other_finance(self, scope='xinan'):
        actor = PlatformAccount(username='repayment-second', phone='13800002222', is_phone_verified=True)
        actor.set_password('platform-password'); actor.save()
        PlatformRoleGrant.objects.create(account=actor, role='FINANCE', scope_slug=scope)
        return actor, issue_tokens(actor, 'SMS', actor.phone)[0]

    def setup_refunds(self):
        from stores.test_platform_pass_refunds import PlatformPassRefundTests
        with store_context(self.second):
            first = PlatformPassRefundTests.create_shortfall(self, 300)
            second = PlatformPassRefundTests.create_shortfall(self, 400)
            return first.pk, second.pk

    def test_verify_and_partial_allocations_with_retry_and_overallocation(self):
        from wallet.models import get_platform_wallet
        first, second = self.setup_refunds()
        claim_id = self.post('xinan').data['data']['claim_id']
        verify = {'request_id': str(uuid4())}
        with store_context(self.second): before = get_platform_wallet().balance
        unverified = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        denied = self.action(claim_id, 'allocate', unverified)
        self.assertEqual((denied.status_code, denied.data['errorCode']), (409, 'REPAYMENT_REJECTED'))
        verified = self.action(claim_id, 'verify', verify)
        self.assertEqual(verified.status_code, 200, verified.data)
        self.assertEqual(self.action(claim_id, 'verify', verify).data, verified.data)
        self.assertEqual(self.action(claim_id, 'allocate', unverified).status_code, 409)
        a = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        response = self.action(claim_id, 'allocate', a)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.action(claim_id, 'allocate', a).data, response.data)
        self.assertEqual(self.action(claim_id, 'allocate', {**a, 'amount': 100}).data['errorCode'], 'REQUEST_CONFLICT')
        b = {'request_id': str(uuid4()), 'refund_id': second, 'amount': 300}
        self.assertEqual(self.action(claim_id, 'allocate', b).status_code, 200)
        extra = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 1}
        self.assertEqual(self.action(claim_id, 'allocate', extra).data['errorCode'], 'REPAYMENT_REJECTED')
        with store_context(self.second):
            self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 2)
            self.assertEqual(get_platform_wallet().balance, before)
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}, slug='shenglang').status_code, 404)

    def test_allocation_commit_then_directory_failure_recovers_once(self):
        first, _ = self.setup_refunds()
        claim_id = self.post('xinan').data['data']['claim_id']
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        data = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.allocate_completed':
                raise DatabaseError('simulated directory failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            result = self.action(claim_id, 'allocate', data)
        self.assertEqual((result.status_code, result.data['errorCode']), (503, 'REPAYMENT_RESULT_UNCERTAIN'))
        self.assertIsNone(PlatformPassRepaymentOperation.objects.get(request_id=data['request_id']).tenant_action_id)
        with store_context(self.second):
            self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 1)
        another = self.action(claim_id, 'allocate', {**data, 'request_id': str(uuid4()), 'amount': 100})
        self.assertEqual((another.status_code, another.data['errorCode']), (409, 'REPAYMENT_BUSY'))
        another_receipt = self.post('xinan', {**self.data, 'request_id': str(uuid4()), 'external_reference': 'TX-OTHER'})
        self.assertEqual((another_receipt.status_code, another_receipt.data['errorCode']), (409, 'REPAYMENT_BUSY'))
        self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 200)
        with store_context(self.second):
            self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 1)

    def test_mutated_receipt_blocks_verification_and_unavailable_actions(self):
        claim_id = self.post('xinan').data['data']['claim_id']
        with store_context(self.second): PassRefundRepaymentReceipt.objects.update(amount=400)
        result = self.action(claim_id, 'verify', {'request_id': str(uuid4())})
        self.assertEqual((result.status_code, result.data['errorCode']), (409, 'REPAYMENT_REVIEW'))
        with store_context(self.second):
            self.assertIsNone(PassRefundRepaymentReceipt.objects.get().verified_at)
            self.assertEqual(PassRefundRepaymentAction.objects.count(), 0)

    def test_verification_recovery_and_tampered_action_are_not_reported_as_success(self):
        claim_id = self.post('xinan').data['data']['claim_id']
        data = {'request_id': str(uuid4())}
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.verify_completed':
                raise DatabaseError('simulated directory failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            result = self.action(claim_id, 'verify', data)
        self.assertEqual((result.status_code, result.data['errorCode']), (503, 'REPAYMENT_RESULT_UNCERTAIN'))
        with store_context(self.second):
            self.assertIsNotNone(PassRefundRepaymentReceipt.objects.get().verified_at)
            self.assertEqual(PassRefundRepaymentAction.objects.count(), 1)
        self.assertEqual(self.action(claim_id, 'verify', data).status_code, 200)
        with store_context(self.second): PassRefundRepaymentAction.objects.update(result={})
        replay = self.action(claim_id, 'verify', data)
        self.assertEqual((replay.status_code, replay.data['errorCode']), (409, 'REPAYMENT_REVIEW'))

    def test_reconciliation_shows_verified_balance_and_original_refund_separately(self):
        first, _ = self.setup_refunds()
        claim_id = self.post('xinan').data['data']['claim_id']
        with self.captureOnCommitCallbacks(execute=True):
            initial = self.reconciliation()
        self.assertEqual(initial.status_code, 200, initial.data)
        self.assertEqual(initial['Cache-Control'], 'no-store')
        data = initial.data['data']
        self.assertEqual(data['status'], 'OK')
        self.assertIsNone(data['claims'][0]['receipt']['unallocated_amount'])
        self.assertEqual(next(r for r in data['refunds'] if r['refund_id'] == first)['remaining_amount'], 300)
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        self.assertEqual(self.action(claim_id, 'allocate', {'request_id': str(uuid4()), 'refund_id': first,
                                                            'amount': 200}).status_code, 200)
        data = self.reconciliation().data['data']
        self.assertEqual(data['status'], 'OK')
        self.assertEqual((data['claims'][0]['receipt']['allocated_amount'],
                          data['claims'][0]['receipt']['unallocated_amount']), (200, 300))
        row = next(r for r in data['refunds'] if r['refund_id'] == first)
        self.assertEqual((row['shortfall_amount'], row['allocated_amount'], row['remaining_amount']), (300, 200, 100))
        self.assertTrue(PlatformAuditEvent.objects.filter(action='pass_repayment.read_reconciliation').exists())

    def test_reconciliation_hides_balances_for_uncertain_and_inconsistent_operations(self):
        first, _ = self.setup_refunds()
        claim_id = self.post('xinan').data['data']['claim_id']
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        data = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.allocate_completed':
                raise DatabaseError('simulated directory failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 503)
        pending = self.reconciliation().data['data']
        self.assertEqual(pending['status'], 'PENDING')
        self.assertIsNone(pending['claims'][0]['receipt']['unallocated_amount'])
        self.assertTrue(all(row['remaining_amount'] is None for row in pending['refunds']))
        self.assertIn('PENDING', [op['status'] for op in pending['operations']])
        self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 200)
        with store_context(self.second): PassRefundRepaymentAction.objects.filter(kind='ALLOCATE').update(result={})
        broken = self.reconciliation().data['data']
        self.assertEqual(broken['status'], 'REVIEW')
        self.assertTrue(all(row['remaining_amount'] is None for row in broken['refunds']))

    def test_reconciliation_respects_scope_and_fails_closed_when_unavailable(self):
        self.grant.scope_slug = 'shenglang'; self.grant.save(update_fields=['scope_slug'])
        self.assertEqual(self.reconciliation().status_code, 404)
        with patch('stores.platform_pass_repayment_read.read', side_effect=DatabaseError('offline')):
            self.assertEqual(self.reconciliation('shenglang').status_code, 503)

    def test_another_finance_recovers_recorded_claim_with_audit(self):
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.register_recorded': raise DatabaseError('simulated failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            self.assertEqual(self.post('xinan').status_code, 503)
        claim = PlatformPassRepaymentClaim.objects.get()
        other, token = self.other_finance()
        result = self.recovery('claim', claim.pk, token)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.recovery('claim', claim.pk, token).data, result.data)
        claim.refresh_from_db(); self.assertIsNotNone(claim.tenant_receipt_id)
        event = PlatformAuditEvent.objects.get(action='pass_repayment.register_recovered')
        self.assertEqual((event.account_id, event.detail['original_actor_id']), (other.pk, str(self.finance.pk)))
        self.assertEqual(self.post('xinan').status_code, 200)
        with store_context(self.second): self.assertEqual(PassRefundRepaymentReceipt.objects.count(), 1)

    def test_another_finance_recovers_allocation_and_original_request_remains_idempotent(self):
        first, _ = self.setup_refunds()
        claim_id = self.post('xinan').data['data']['claim_id']
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        data = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.allocate_completed': raise DatabaseError('simulated failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 503)
        op = PlatformPassRepaymentOperation.objects.get(request_id=data['request_id'])
        other, token = self.other_finance()
        result = self.recovery('operation', op.pk, token)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['amount'], 200)
        self.assertEqual(self.recovery('operation', op.pk, token).data, result.data)
        self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 200)
        event = PlatformAuditEvent.objects.get(action='pass_repayment.allocate_recovered')
        self.assertEqual((event.account_id, event.detail['original_actor_id']), (other.pk, str(self.finance.pk)))
        with store_context(self.second): self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 1)

    def test_recovery_requires_existing_matching_evidence_and_scope(self):
        with patch('stores.platform_pass_repayment.register_receipt', side_effect=DatabaseError('store offline')):
            self.assertEqual(self.post('xinan').status_code, 503)
        claim = PlatformPassRepaymentClaim.objects.get()
        other, token = self.other_finance('shenglang')
        self.assertEqual(self.recovery('claim', claim.pk, token).status_code, 404)
        missing = self.recovery('claim', claim.pk)
        self.assertEqual((missing.status_code, missing.data['errorCode']), (409, 'RECOVERY_EVIDENCE_MISSING'))
        claim.refresh_from_db(); self.assertIsNone(claim.tenant_receipt_id)
        self.assertEqual(self.post('xinan').status_code, 200)

    def test_recovery_refuses_tampered_tenant_action(self):
        first, _ = self.setup_refunds()
        claim_id = self.post('xinan').data['data']['claim_id']
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        data = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.allocate_completed': raise DatabaseError('simulated failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 503)
        op = PlatformPassRepaymentOperation.objects.get(request_id=data['request_id'])
        with store_context(self.second): PassRefundRepaymentAction.objects.filter(kind='ALLOCATE').update(result={})
        result = self.recovery('operation', op.pk)
        self.assertEqual((result.status_code, result.data['errorCode']), (409, 'REPAYMENT_REVIEW'))
        op.refresh_from_db(); self.assertIsNone(op.tenant_action_id)

    def test_recovery_without_tenant_operation_preserves_pending_original_retry(self):
        first, _ = self.setup_refunds()
        claim_id = self.post('xinan').data['data']['claim_id']
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        data = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        with patch('stores.platform_pass_repayment.allocate', side_effect=DatabaseError('store unavailable')):
            self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 503)
        op = PlatformPassRepaymentOperation.objects.get(request_id=data['request_id'])
        missing = self.recovery('operation', op.pk)
        self.assertEqual((missing.status_code, missing.data['errorCode']), (409, 'RECOVERY_EVIDENCE_MISSING'))
        op.refresh_from_db(); self.assertIsNone(op.tenant_action_id); self.assertIsNone(op.rejected_at)
        self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 200)

    def test_recovery_refuses_changed_receipt_evidence(self):
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.register_recorded': raise DatabaseError('simulated failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            self.assertEqual(self.post('xinan').status_code, 503)
        claim = PlatformPassRepaymentClaim.objects.get()
        with store_context(self.second): PassRefundRepaymentReceipt.objects.update(evidence='changed')
        response = self.recovery('claim', claim.pk)
        self.assertEqual((response.status_code, response.data['errorCode']), (409, 'REPAYMENT_REVIEW'))
        claim.refresh_from_db(); self.assertIsNone(claim.tenant_receipt_id)

    def test_void_missing_claim_releases_reference_and_blocks_old_request(self):
        with patch('stores.platform_pass_repayment.register_receipt', side_effect=DatabaseError('store offline')):
            self.assertEqual(self.post('xinan').status_code, 503)
        claim = PlatformPassRepaymentClaim.objects.get()
        old_digest = claim.external_reference_digest
        other, token = self.other_finance()
        self.assertEqual(self.void('claim', claim.pk, reason='', token=token).status_code, 400)
        result = self.void('claim', claim.pk, token=token)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.void('claim', claim.pk, token=token).data, result.data)
        self.assertEqual(self.void('claim', claim.pk, reason='其他原因', token=token).status_code, 409)
        claim.refresh_from_db()
        self.assertIsNone(claim.external_reference_digest)
        self.assertEqual(claim.voided_reference_digest, old_digest)
        self.assertEqual(claim.voided_by_id, other.pk)
        self.assertEqual(self.post('xinan').data['errorCode'], 'REPAYMENT_VOIDED')
        new = {**self.data, 'request_id': str(uuid4())}
        self.assertEqual(self.post('xinan', new).status_code, 200)
        data = self.reconciliation().data['data']
        self.assertEqual(data['status'], 'OK')
        self.assertIn('VOIDED', [row['status'] for row in data['claims']])
        self.assertTrue(PlatformAuditEvent.objects.filter(account=other, action='pass_repayment.register_voided').exists())

    def test_void_missing_operation_blocks_old_request_and_allows_new_one(self):
        first, _ = self.setup_refunds()
        claim_id = self.post('xinan').data['data']['claim_id']
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        data = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        with patch('stores.platform_pass_repayment.allocate', side_effect=DatabaseError('store offline')):
            self.assertEqual(self.action(claim_id, 'allocate', data).status_code, 503)
        op = PlatformPassRepaymentOperation.objects.get(request_id=data['request_id'])
        other, token = self.other_finance()
        self.assertEqual(self.void('operation', op.pk, token=token).status_code, 200)
        self.assertEqual(self.void('operation', op.pk, token=token).status_code, 200)
        self.assertEqual(self.action(claim_id, 'allocate', data).data['errorCode'], 'REPAYMENT_VOIDED')
        self.assertEqual(self.action(claim_id, 'allocate', {**data, 'request_id': str(uuid4())}).status_code, 200)
        with store_context(self.second): self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 1)
        self.assertIn('VOIDED', [row['status'] for row in self.reconciliation().data['data']['operations']])
        self.assertTrue(PlatformAuditEvent.objects.filter(account=other, action='pass_repayment.allocate_voided').exists())

    def test_void_refuses_existing_evidence_or_unavailable_store(self):
        original = PlatformAuditEvent.objects.create
        def fail_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.register_recorded': raise DatabaseError('simulated failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_result):
            self.assertEqual(self.post('xinan').status_code, 503)
        claim = PlatformPassRepaymentClaim.objects.get()
        self.assertEqual(self.void('claim', claim.pk).data['errorCode'], 'RECOVERY_EVIDENCE_EXISTS')
        with patch('stores.platform_pass_repayment_recovery.PassRefundRepaymentReceipt.objects.select_for_update',
                   side_effect=DatabaseError('offline')):
            self.assertEqual(self.void('claim', claim.pk).status_code, 503)
        claim.refresh_from_db(); self.assertIsNone(claim.voided_at)
        self.assertEqual(self.recovery('claim', claim.pk).status_code, 200)
        first, _ = self.setup_refunds()
        self.assertEqual(self.action(claim.pk, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        data = {'request_id': str(uuid4()), 'refund_id': first, 'amount': 200}
        def fail_allocation(**kwargs):
            if kwargs.get('action') == 'pass_repayment.allocate_completed': raise DatabaseError('simulated failure')
            return original(**kwargs)
        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_allocation):
            self.assertEqual(self.action(claim.pk, 'allocate', data).status_code, 503)
        op = PlatformPassRepaymentOperation.objects.get(request_id=data['request_id'])
        self.assertEqual(self.void('operation', op.pk).data['errorCode'], 'RECOVERY_EVIDENCE_EXISTS')
        op.refresh_from_db(); self.assertIsNone(op.voided_at)
