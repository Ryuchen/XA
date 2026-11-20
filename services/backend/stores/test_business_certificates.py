from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store, StoreBusinessCertificate
from .platform_auth import issue_tokens


@override_settings(PLATFORM_IDENTITY_ENABLED=True, STORE_FIRST_OPEN_ENABLED=True)
class BusinessCertificateTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.branch = Store.objects.create(slug='xinan', name='证照隔离店', database_alias='store_b',
                                          initialized_at=timezone.now(), status=Store.Status.CONFIGURING)
        self.other = Store.objects.get(slug='shenglang')
        self.actor, self.owner = self.platform('certificate-platform-owner', 'SUPER_ADMIN')
        self.base = f'/api/admin/platform/store-management/{self.branch.pk}'
        self.certificates = self.base + '/business-certificates'

    def platform(self, username, role):
        account = PlatformAccount.objects.create(username=username, phone='13800001111', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=account, role=role, scope_slug='*')
        token, _ = issue_tokens(account, 'SMS', account.phone)
        return account, APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)

    def image(self, color='navy'):
        data = BytesIO()
        Image.new('RGB', (32, 24), color).save(data, format='PNG')
        return data.getvalue()

    def upload(self, *, client=None, request_id=None, expected='', expires='', color='navy', long_term=None):
        return (client or self.owner).post(self.certificates, {
            'document': SimpleUploadedFile('certificate.png', self.image(color), content_type='image/png'),
            'expires_on': expires, 'expected_certificate_id': expected,
            'long_term': not expires if long_term is None else long_term,
            'client_request_id': str(request_id or uuid4()), 'confirm_verified': True,
        }, format='multipart')

    def readiness(self):
        response = self.owner.get(self.base + '/setup/readiness')
        self.assertEqual(response.status_code, 200, response.content)
        return next(check for check in response.data['data']['checks'] if check['key'] == 'business_certificate')

    def test_private_document_and_replacement_history_are_bound_to_the_store(self):
        _, finance = self.platform('certificate-finance', 'FINANCE')
        self.assertEqual(finance.get(self.certificates).status_code, 403)
        self.assertEqual(self.upload(client=finance).status_code, 403)
        self.assertEqual(self.upload(long_term=False).status_code, 400)
        self.assertEqual(self.readiness()['details']['status'], 'MISSING')

        first_request_id = uuid4()
        created = self.upload(request_id=first_request_id)
        self.assertEqual(created.status_code, 201, created.content)
        first = created.data['data']['id']
        replacement = self.upload(expected=first, color='teal')
        self.assertEqual(replacement.status_code, 201, replacement.content)
        second = replacement.data['data']['id']
        retry = self.upload(request_id=first_request_id)
        self.assertEqual((retry.status_code, retry.data['data']['id']), (200, first))
        self.assertEqual(self.upload(request_id=first_request_id, color='red').status_code, 409)
        self.assertEqual(self.upload(expected=first, color='red').status_code, 409)

        history = self.owner.get(self.certificates)
        self.assertEqual([row['id'] for row in history.data['data']['results']], [second, first])
        self.assertEqual(history.data['data']['current']['id'], second)
        self.assertNotIn('document', history.data['data']['results'][0])
        self.assertEqual(self.readiness()['status'], 'READY')
        self.assertEqual(PlatformAuditEvent.objects.filter(account=self.actor, action='store.certificate.recorded').count(), 2)

        endpoint = f'{self.certificates}/{first}/document'
        self.assertEqual(finance.get(endpoint).status_code, 403)
        wrong = f'/api/admin/platform/store-management/{self.other.pk}/business-certificates/{first}/document'
        self.assertEqual(self.owner.get(wrong).status_code, 404)
        document = self.owner.get(endpoint)
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document['Cache-Control'], 'private, no-store')
        self.assertEqual(document.content, self.image())

    def test_expired_certificate_blocks_opening_and_failed_audit_rolls_back(self):
        expires = timezone.localdate() - timedelta(days=1)
        result = self.upload(expires=expires.isoformat())
        self.assertEqual(result.status_code, 201, result.content)
        first = result.data['data']['id']
        self.assertEqual(self.readiness()['details']['status'], 'EXPIRED')
        refused = self.owner.post(self.base + '/setup/readiness', {'confirm': True}, format='json')
        self.assertEqual(refused.status_code, 409, refused.content)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.status, Store.Status.CONFIGURING)

        with patch('stores.business_certificates.PlatformAuditEvent.objects.create',
                   side_effect=DatabaseError('audit unavailable')):
            failed = self.upload(expected=first, color='teal')
        self.assertEqual(failed.status_code, 503, failed.content)
        recovery = self.owner.get(self.certificates).data['data']
        self.assertEqual(recovery['current']['id'], first)
        self.assertEqual(len(recovery['results']), 1)

    def test_document_digest_mismatch_blocks_opening_and_download(self):
        result = self.upload()
        self.assertEqual(result.status_code, 201, result.content)
        identifier = result.data['data']['id']
        StoreBusinessCertificate.objects.filter(pk=identifier).update(document=b'damaged test document')
        check = self.readiness()
        self.assertEqual((check['status'], check['details']['status']), ('BLOCKED', 'DOCUMENT_UNAVAILABLE'))
        unavailable = self.owner.get(f'{self.certificates}/{identifier}/document')
        self.assertEqual(unavailable.status_code, 503, unavailable.content)
        self.assertFalse(PlatformAuditEvent.objects.filter(account=self.actor, action='store.certificate.downloaded').exists())
