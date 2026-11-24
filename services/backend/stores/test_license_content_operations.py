"""License gates banner configuration without removing existing content."""
import os
from unittest import skipUnless
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from banners.models import Banner
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.context import store_context
from stores.license_features import change_feature
from stores.test_license_account_admission import LicenseAccountAdmissionTests
from stores.tests_foundation import admin


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True,
                   LICENSE_SNAPSHOT_CACHE_ENABLED=True, LICENSE_NOTICE_DISPATCH_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class ContentOperationsLicenseTest(TestCase):
    databases = {'default', 'store_b'}
    setUp = LicenseAccountAdmissionTests.setUp

    def clients(self, store, host):
        with store_context(store):
            staff = admin('banner-editor')
            role = staff.admin_membership.roles.first()
            role.permissions = ['banner:view', 'banner:edit', 'banner:delete', 'announcement:view', 'announcement:edit']
            role.save(update_fields=['permissions'])
            boss = ClubAccount.objects.create_account(username='banner-boss', account_type='BOSS')
            token, _ = issue_account_tokens(staff)
            boss_token, _ = issue_account_tokens(boss)
        return (APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token),
                APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + boss_token))

    @staticmethod
    def image():
        image = BytesIO()
        Image.new('RGB', (750, 320), '#224466').save(image, format='PNG')
        return SimpleUploadedFile('test.png', image.getvalue(), content_type='image/png')

    def test_configuration_permission_and_existing_content_are_separate(self):
        staff, boss = self.clients(self.second, 'xa.esports.club')
        first, _ = self.clients(self.first, 'sl.esports.club')
        with store_context(self.second):
            existing = Banner.objects.create(title='现有轮播', image='banners/existing.png')
        url = f'/api/admin/banners/{existing.pk}/'
        self.assertEqual(staff.post('/api/admin/banners/', {'title': '拒绝'}, format='json').status_code, 403)
        self.assertEqual(staff.patch(url, {'title': '拒绝'}, format='json').status_code, 403)
        self.assertEqual(staff.get(url).status_code, 200)
        self.assertIn(existing.pk, [r['id'] for r in boss.get('/api/banners/').data['data']])
        self.assertIn(staff.post('/api/admin/announcements/', {'title': '基础公告', 'content': '内容'}, format='json').status_code, (200, 201))
        change_feature(self.platform_token, tier='BASIC', feature='CONTENT_OPERATIONS', mode='FULL')
        with TemporaryDirectory(prefix='xa-license-banner-') as directory, override_settings(MEDIA_ROOT=directory):
            response = staff.post('/api/admin/banners/', {'title': '新增', 'image': self.image()}, format='multipart')
            self.assertIn(response.status_code, (200, 201), response.data)
            self.assertEqual(staff.patch(url, {'title': '已修改'}, format='json').status_code, 200)
            self.assertEqual(first.post('/api/admin/banners/', {'title': '跨店'}, format='json').status_code, 403)
            self.assertEqual(staff.post('/api/admin/banners/', {'title': '跨店'}, format='json', HTTP_HOST='sl.esports.club').status_code, 401)
            self.assertEqual(boss.post('/api/admin/banners/', {'title': '无角色权限'}, format='json').status_code, 403)
        change_feature(self.platform_token, tier='BASIC', feature='CONTENT_OPERATIONS', mode='OFF')
        self.assertEqual(staff.patch(url, {'title': '拒绝'}, format='json').status_code, 403)
        with store_context(self.second):
            self.assertEqual(Banner.objects.get(pk=existing.pk).title, '已修改')
        self.assertEqual(staff.delete(url).status_code, 200)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_grace_expiry_and_control_outage(self):
        staff, _ = self.clients(self.second, 'xa.esports.club')
        first, _ = self.clients(self.first, 'sl.esports.club')
        change_feature(self.platform_token, tier='BASIC', feature='CONTENT_OPERATIONS', mode='FULL')
        with store_context(self.second):
            banner = Banner.objects.create(title='保留', image='banners/existing.png')
        url = f'/api/admin/banners/{banner.pk}/'
        self.license.ends_at = timezone.now() - timedelta(days=1)
        self.license.starts_at = timezone.now() - timedelta(days=30)
        self.license.activated_at = timezone.now() - timedelta(days=29)
        self.license.save(update_fields=['starts_at', 'ends_at', 'activated_at'])
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            self.assertEqual(staff.patch(url, {'title': '宽限'}, format='json').status_code, 200)
        with patch('stores.license_status.status_for_store', side_effect=DatabaseError('offline')):
            self.assertEqual(staff.patch(url, {'title': '缓存'}, format='json').status_code, 200)
            self.assertEqual(first.post('/api/admin/banners/', {'title': '无缓存'}, format='json').status_code, 403)
            with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=7)):
                self.assertEqual(staff.patch(url, {'title': '到期'}, format='json').status_code, 403)
        self.license.ends_at = timezone.now() - timedelta(days=8)
        self.license.save(update_fields=['ends_at'])
        self.assertEqual(staff.patch(url, {'title': '到期'}, format='json').status_code, 403)
        self.assertEqual(staff.get(url).data['data']['title'], '缓存')
