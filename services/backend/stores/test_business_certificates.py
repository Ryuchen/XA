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

from club_accounts.tokens import issue_account_tokens
from site_messages.models import LicenseNoticeDelivery, Message
from .context import store_context
from .models import (LicenseNotice, PlatformAccount, PlatformAuditEvent, PlatformLicenseNotice,
                     PlatformRoleGrant, Store, StoreBusinessCertificate, StoreLicenseActivatorGrant,
                     StoreProvisioningClaim)
from .platform_auth import issue_tokens
from .tests_foundation import admin


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

    def test_due_dates_use_both_notice_centers_and_deliver_once_per_threshold(self):
        from .license_tasks import deliver_license_notices

        with store_context(self.branch):
            owner = admin('certificate-owner', super_admin=True)
            delegate = admin('license-only-delegate')
        StoreLicenseActivatorGrant.objects.create(store=self.branch, account_id=delegate.pk,
            account_no=delegate.account_no, is_active=True, updated_by=self.actor)
        now = timezone.now()
        expiry = timezone.localdate(now) + timedelta(days=31)
        result = self.upload(expires=expiry.isoformat())
        self.assertEqual(result.status_code, 201, result.content)
        certificate_id = result.data['data']['id']
        with override_settings(LICENSE_NOTICE_DISPATCH_ENABLED=True), patch('stores.license_notices._wake_after_commit'):
            for day in (1, 24, 30, 31, 32):
                with patch('django.utils.timezone.now', return_value=now+timedelta(days=day)):
                    deliver_license_notices.apply().get(propagate=True)
                    deliver_license_notices.apply().get(propagate=True)
        notices = LicenseNotice.objects.filter(business_certificate_id=certificate_id)
        self.assertEqual(set(notices.values_list('kind', flat=True)), {'D30', 'D7', 'D1'})
        self.assertEqual(PlatformLicenseNotice.objects.filter(notice__in=notices, account=self.actor).count(), 3)
        center = self.owner.get('/api/admin/platform/license-notices').data['data']
        self.assertEqual(center['unread'], 3)
        self.assertTrue(all(row['source'] == 'BUSINESS_CERTIFICATE' and expiry.isoformat() in row['detail']
                            for row in center['results']))
        notice_id = center['results'][0]['id']
        self.assertEqual(self.owner.post(f'/api/admin/platform/license-notices/{notice_id}/read').status_code, 200)
        self.assertEqual(self.owner.get('/api/admin/platform/license-notices').data['data']['unread'], 2)
        StoreProvisioningClaim.objects.create(store=self.branch, database_fingerprint='a' * 64,
                                               admin_username=owner.username)
        with store_context(self.branch):
            self.assertEqual(LicenseNoticeDelivery.objects.filter(account=owner).count(), 3)
            self.assertFalse(LicenseNoticeDelivery.objects.filter(account=delegate).exists())
            self.assertEqual(Message.objects.filter(title='门店营业执照即将到期').count(), 3)
            owner.is_phone_verified = True
            owner.save(update_fields=['is_phone_verified'])
            token, _ = issue_account_tokens(owner, auth_method='password_sms')
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        site_messages = staff_client.get('/api/messages/')
        self.assertEqual(site_messages.status_code, 200, site_messages.content)
        self.assertEqual([row['title'] for row in site_messages.data['data']['messages']].count('门店营业执照即将到期'), 3)
        with store_context(self.other):
            self.assertFalse(Message.objects.filter(title='门店营业执照即将到期').exists())

    def test_replacement_stops_pending_old_delivery_after_platform_received_it(self):
        from .license_notices import deliver_notice

        with store_context(self.branch):
            admin('certificate-owner', super_admin=True)
        expiry = timezone.localdate() + timedelta(days=20)
        original = self.upload(expires=expiry.isoformat())
        self.assertEqual(original.status_code, 201, original.content)
        original_id = original.data['data']['id']
        notice = LicenseNotice.objects.get(business_certificate_id=original_id, kind='D30')
        with patch('stores.dynamic_database.store_connection_ready', return_value=False):
            deliver_notice(notice.pk)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.platform_delivered_at)
        self.assertIsNone(notice.store_delivered_at)

        replacement = self.upload(expected=original_id, color='teal')
        self.assertEqual(replacement.status_code, 201, replacement.content)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.suppressed_at)
        deliver_notice(notice.pk)
        self.assertEqual(PlatformLicenseNotice.objects.filter(notice=notice).count(), 1)
        with store_context(self.branch):
            self.assertFalse(LicenseNoticeDelivery.objects.filter(notice_id=notice.pk).exists())
