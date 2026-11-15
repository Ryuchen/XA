"""Actual admin page with isolated ASGI and synthetic repayment evidence."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from stores.models import PlatformAccount, PlatformPassRepaymentClaim, PlatformPassRepaymentOperation, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_PASS_REPAYMENT_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True, ALLOWED_HOSTS=['*'])
class PlatformPassRepaymentBrowserTest(LiveServerTestCase):
    def run_page(self, pending):
        from stores.test_platform_pass_refunds import PlatformPassRefundTests
        refund = PlatformPassRefundTests.create_shortfall(self, 300)
        finance = PlatformAccount(username='repayment-browser', phone='13800001111', is_phone_verified=True)
        finance.set_password('Synthetic-Platform-Password-2026!'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='shenglang')
        token, refresh = issue_tokens(finance, 'SMS', finance.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        data = {'request_id': str(uuid4()), 'external_reference': 'BROWSER-123', 'amount': 500,
                'received_at': timezone.now().isoformat(), 'evidence': '测试到账凭证'}
        registered = client.post('/api/admin/platform/pass-refund-repayments/shenglang/register', data, format='json')
        self.assertEqual(registered.status_code, 200, registered.data)
        claim_id = registered.data['data']['claim_id']
        base = f'/api/admin/platform/pass-refund-repayments/shenglang/{claim_id}'
        self.assertEqual(client.post(base + '/verify', {'request_id': str(uuid4())}, format='json').status_code, 200)
        self.assertEqual(client.post(base + '/allocate', {'request_id': str(uuid4()), 'refund_id': refund.pk,
                                                          'amount': 200}, format='json').status_code, 200)
        if pending:
            PlatformPassRepaymentOperation.objects.create(request_id=uuid4(), claim=PlatformPassRepaymentClaim.objects.get(pk=claim_id),
                actor=finance, kind='ALLOCATE', payload_digest='0' * 64)
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/platform-pass-repayment-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'token': token, 'refresh': refresh, 'id': str(finance.pk),
                                  'username': finance.username, 'refund_id': refund.pk, 'pending': pending}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_verified_reconciliation(self):
        self.run_page(False)

    def test_pending_operation_hides_balances(self):
        self.run_page(True)

    def test_finance_records_verifies_allocates_and_recovers_lost_response(self):
        from stores.test_platform_pass_refunds import PlatformPassRefundTests
        from users.models import PassRefundRepaymentAllocation, PassRefundRepaymentReceipt
        refund = PlatformPassRefundTests.create_shortfall(self, 300)
        finance = PlatformAccount(username='repayment-actions-browser', phone='13800001111', is_phone_verified=True)
        finance.set_password('Synthetic-Platform-Password-2026!'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='shenglang')
        token, refresh = issue_tokens(finance, 'SMS', finance.phone)
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/platform-pass-repayment-actions-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'token': token, 'refresh': refresh, 'id': str(finance.pk),
                                  'username': finance.username, 'refund_id': refund.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(PassRefundRepaymentReceipt.objects.count(), 1)
        self.assertIsNotNone(PassRefundRepaymentReceipt.objects.get().verified_at)
        self.assertEqual(list(PassRefundRepaymentAllocation.objects.values_list('refund_id', 'amount')), [(refund.pk, 200)])

    def test_other_finance_recovers_a_committed_operation_without_browser_state(self):
        from stores.test_platform_pass_refunds import PlatformPassRefundTests
        from users.models import PassRefundRepaymentAllocation
        refund = PlatformPassRefundTests.create_shortfall(self, 300)
        original = PlatformAccount(username='recovery-original', phone='13800001111', is_phone_verified=True)
        original.set_password('Synthetic-Platform-Password-2026!'); original.save()
        PlatformRoleGrant.objects.create(account=original, role='FINANCE', scope_slug='shenglang')
        token, _ = issue_tokens(original, 'SMS', original.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        registration = {'request_id': str(uuid4()), 'external_reference': 'RECOVERY-123', 'amount': 500,
                        'received_at': timezone.now().isoformat(), 'evidence': '隔离到账凭证'}
        claim = client.post('/api/admin/platform/pass-refund-repayments/shenglang/register', registration, format='json').data['data']['claim_id']
        base = f'/api/admin/platform/pass-refund-repayments/shenglang/{claim}'
        self.assertEqual(client.post(base + '/verify', {'request_id': str(uuid4())}, format='json').status_code, 200)
        req = str(uuid4())
        self.assertEqual(client.post(base + '/allocate', {'request_id': req, 'refund_id': refund.pk, 'amount': 200}, format='json').status_code, 200)
        # Reconstruct the directory state after the independent tenant commit
        # and before the directory result commit; no funds are moved here.
        PlatformPassRepaymentOperation.objects.filter(request_id=req).update(tenant_action_id=None, completed_at=None)
        other = PlatformAccount(username='recovery-second', phone='13800002222', is_phone_verified=True)
        other.set_password('Synthetic-Platform-Password-2026!'); other.save()
        PlatformRoleGrant.objects.create(account=other, role='FINANCE', scope_slug='shenglang')
        other_token, refresh = issue_tokens(other, 'SMS', other.phone)
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/platform-pass-repayment-recovery-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'token': other_token, 'refresh': refresh, 'id': str(other.pk),
                                  'username': other.username, 'refund_id': refund.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        op = PlatformPassRepaymentOperation.objects.get(request_id=req)
        self.assertIsNotNone(op.tenant_action_id)
        self.assertEqual(PassRefundRepaymentAllocation.objects.filter(refund_id=refund.pk).count(), 1)
        from stores.models import PlatformAuditEvent
        self.assertTrue(PlatformAuditEvent.objects.filter(account=other, action='pass_repayment.allocate_recovered').exists())

    def test_finance_voids_unapplied_claim_and_original_request_cannot_run(self):
        from stores.test_platform_pass_refunds import PlatformPassRefundTests
        from users.models import PassRefundRepaymentReceipt
        PlatformPassRefundTests.create_shortfall(self, 300)
        original = PlatformAccount(username='void-original', phone='13800001111', is_phone_verified=True)
        original.set_password('Synthetic-Platform-Password-2026!'); original.save()
        PlatformRoleGrant.objects.create(account=original, role='FINANCE', scope_slug='shenglang')
        token, _ = issue_tokens(original, 'SMS', original.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        registration = {'request_id': str(uuid4()), 'external_reference': 'VOID-123', 'amount': 500,
                        'received_at': timezone.now().isoformat(), 'evidence': '隔离到账凭证'}
        with patch('stores.platform_pass_repayment.register_receipt', side_effect=DatabaseError('simulated store failure')):
            self.assertEqual(client.post('/api/admin/platform/pass-refund-repayments/shenglang/register', registration, format='json').status_code, 503)
        other = PlatformAccount(username='void-second', phone='13800002222', is_phone_verified=True)
        other.set_password('Synthetic-Platform-Password-2026!'); other.save()
        PlatformRoleGrant.objects.create(account=other, role='FINANCE', scope_slug='shenglang')
        other_token, refresh = issue_tokens(other, 'SMS', other.phone)
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/platform-pass-repayment-void-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'token': other_token, 'refresh': refresh, 'id': str(other.pk),
                                  'username': other.username}), text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        old = client.post('/api/admin/platform/pass-refund-repayments/shenglang/register', registration, format='json')
        self.assertEqual(old.data['errorCode'], 'REPAYMENT_VOIDED')
        self.assertEqual(client.post('/api/admin/platform/pass-refund-repayments/shenglang/register',
                                     {**registration, 'request_id': str(uuid4())}, format='json').status_code, 200)
        self.assertEqual(PassRefundRepaymentReceipt.objects.count(), 1)
        from stores.models import PlatformAuditEvent
        self.assertTrue(PlatformAuditEvent.objects.filter(account=other, action='pass_repayment.register_voided').exists())
