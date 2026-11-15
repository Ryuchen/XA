import uuid
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store, StoreLicenseContract, StoreLicenseReceipt
from .platform_auth import issue_tokens


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class LicenseBillingApiTests(TestCase):
    def setUp(self):
        self.store = Store.objects.first()
        actor = PlatformAccount.objects.create(username='license-billing-owner', phone='13800000888', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        result = self.client.post(f'/api/admin/platform/store-management/{self.store.pk}/licenses',
                                  {'tier': 'BASIC', 'kind': 'INITIAL', 'amount_due_fen': 10000}, format='json')
        self.assertEqual(result.status_code, 201, result.content)
        self.code_id = result.data['data']['id']
        self.contract_id = result.data['data']['contract_id']
        self.url = f'/api/admin/platform/license-contracts/{self.contract_id}'

    def receipt(self, amount, *, request_id=None):
        return self.client.post(self.url + '/receipts', {'request_id': str(request_id or uuid.uuid4()),
            'amount_fen': amount, 'received_on': '2026-11-11', 'note': '线下核对'}, format='json')

    def test_partial_receipts_idempotence_overpayment_void_and_zero_contract(self):
        self.assertEqual(self.client.get(self.url).data['data']['status'], 'UNPAID')
        request_id = uuid.uuid4()
        first = self.receipt(3000, request_id=request_id)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(self.receipt(3000, request_id=request_id).status_code, 200)
        self.assertEqual(self.receipt(3100, request_id=request_id).status_code, 400)
        summary = self.client.get(self.url).data['data']
        self.assertEqual((summary['status'], summary['amount_received_fen']), ('PARTIAL', 3000))
        self.assertEqual(self.receipt(7001).status_code, 400)
        self.assertEqual(self.client.patch(self.url, {'amount_due_fen': 2999, 'reason': '核对合同'}, format='json').status_code, 400)
        second = self.receipt(7000)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(self.client.get(self.url).data['data']['status'], 'PAID')
        for row in (first, second):
            result = self.client.post(f"/api/admin/platform/license-receipts/{row.data['data']['id']}/void",
                                      {'reason': '登记有误'}, format='json')
            self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.client.get(self.url).data['data']['status'], 'UNPAID')
        result = self.client.patch(self.url, {'amount_due_fen': 0, 'reason': '免费合同'}, format='json')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.data['data']['status'], 'PAID')
        self.assertEqual(self.receipt(1).status_code, 400)
        self.assertEqual(StoreLicenseReceipt.objects.count(), 2)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='license.receipt_voided').count(), 2)

    def test_reissue_preserves_contract_and_receipts(self):
        recorded = self.receipt(3000)
        self.assertEqual(recorded.status_code, 201)
        self.assertEqual(self.client.post(f'/api/admin/platform/licenses/{self.code_id}/void',
            {'reason': '码丢失'}, format='json').status_code, 200)
        replacement = self.client.post(f'/api/admin/platform/licenses/{self.code_id}/reissue', {}, format='json')
        self.assertEqual(replacement.status_code, 201, replacement.content)
        self.assertEqual(replacement.data['data']['contract_id'], self.contract_id)
        self.assertEqual(replacement.data['data']['reissued_from'], self.code_id)
        self.assertEqual(StoreLicenseContract.objects.count(), 1)
        self.assertEqual(self.client.get(self.url).data['data']['amount_received_fen'], 3000)
        self.assertEqual(self.client.post(f'/api/admin/platform/licenses/{self.code_id}/reissue', {}, format='json').status_code, 400)

    def test_finance_cannot_change_contract_or_register_receipt(self):
        actor = PlatformAccount.objects.create(username='license-billing-finance', phone='13800000889', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=actor, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(client.get(self.url).status_code, 403)
        self.assertEqual(client.patch(self.url, {'amount_due_fen': 0, 'reason': 'test'}, format='json').status_code, 403)

    def test_failed_billing_audit_rolls_back_receipt_and_contract_change(self):
        with patch('stores.license_billing.PlatformAuditEvent.objects.create', side_effect=DatabaseError):
            self.assertEqual(self.receipt(1000).status_code, 503)
            self.assertEqual(self.client.patch(self.url, {'amount_due_fen': 9000, 'reason': '核对'}, format='json').status_code, 503)
        self.assertFalse(StoreLicenseReceipt.objects.exists())
        self.assertEqual(self.client.get(self.url).data['data']['amount_due_fen'], 10000)
