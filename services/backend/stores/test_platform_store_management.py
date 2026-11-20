"""Platform identity creates a store without impersonating a store employee."""
import json
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from .context import resolve_store, store_context
from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store, StoreProvisioningClaim
from .platform_auth import issue_tokens
from .tests_foundation import admin


@override_settings(STORE_DATABASE_ALIASES=('store_b',), PLATFORM_IDENTITY_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PlatformStoreManagementTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def platform(self, username, role, scope='*'):
        account = PlatformAccount(username=username, phone='13800001111', is_phone_verified=True)
        account.set_password('Synthetic-Platform-Password-2026!')
        account.save()
        PlatformRoleGrant.objects.create(account=account, role=role, scope_slug=scope)
        token, _ = issue_tokens(account, 'SMS', account.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        return account, client

    def test_platform_super_admin_real_store_creation_and_audit(self):
        actor, client = self.platform('global-owner', 'SUPER_ADMIN')
        listing = client.get('/api/admin/platform/store-management')
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertIn('store_b', listing.data['data']['available_database_aliases'])
        password = 'Synthetic-Store-Administrator-Password-2026!'
        response = client.post('/api/admin/platform/store-management', {
            'slug': 'platform-new', 'name': '平台新门店', 'entity_prefix': 'PT',
            'currency_name': '测试币', 'database_alias': 'store_b',
            'admin_username': 'platform-new-owner', 'admin_password': password,
            'admin_phone': '13800002222', 'admin_nickname': '首位管理员',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data['data']['status'], 'CONFIGURING')
        self.assertNotIn(password, response.content.decode())
        branch = Store.objects.get(slug='platform-new')
        with store_context(branch):
            self.assertTrue(ClubAccount.objects.filter(username='platform-new-owner', account_type='STAFF').exists())
            self.assertFalse(ClubAccount.objects.filter(account_type__in=['BOSS', 'PROVIDER']).exists())
        events = list(PlatformAuditEvent.objects.filter(account=actor, action__startswith='store.create_').order_by('id'))
        self.assertEqual([event.action for event in events], ['store.create_requested', 'store.create_result'])
        self.assertNotIn(password, json.dumps([event.detail for event in events], ensure_ascii=False))
        self.assertEqual(events[-1].target_store_slug, 'platform-new')
        self.assertEqual(client.patch(f'/api/admin/platform/store-management/{branch.pk}',
                                      {'name': '缺少版本'}, format='json').status_code, 400)
        self.assertEqual(client.patch(f'/api/admin/platform/store-management/{branch.pk}',
                                      {'status': 'ACTIVE', 'expected_updated_at': branch.updated_at.isoformat()}, format='json').status_code, 400)
        branch.refresh_from_db(); self.assertEqual(branch.status, Store.Status.CONFIGURING)
        stale = branch.updated_at.isoformat()
        changed = client.patch(f'/api/admin/platform/store-management/{branch.pk}',
                               {'name': '门店更新', 'expected_updated_at': stale}, format='json')
        self.assertEqual(changed.status_code, 200, changed.content)
        conflict = client.patch(f'/api/admin/platform/store-management/{branch.pk}',
                                {'name': '旧数据覆盖', 'expected_updated_at': stale}, format='json')
        self.assertEqual(conflict.status_code, 409, conflict.content)
        branch.refresh_from_db(); self.assertEqual(branch.name, '门店更新')
        self.assertEqual(client.post(f'/api/admin/platform/store-management/{branch.pk}/initialize', {}, format='json').status_code, 200)

    def test_scope_finance_store_identity_and_audit_failure_cannot_write(self):
        _, finance = self.platform('platform-finance', 'FINANCE')
        _, scoped = self.platform('scoped-super', 'SUPER_ADMIN', scope='shenglang')
        for client in (finance, scoped):
            self.assertEqual(client.get('/api/admin/platform/store-management').status_code, 403)
            self.assertEqual(client.post('/api/admin/platform/store-management', {}, format='json').status_code, 403)
        with store_context(resolve_store()):
            store_admin = admin('legacy-not-platform', super_admin=True)
            token, _ = issue_account_tokens(store_admin)
        legacy = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(legacy.get('/api/admin/platform/store-management').status_code, 401)
        _, owner = self.platform('audit-owner', 'SUPER_ADMIN')
        with patch('stores.platform_store_management.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit unavailable')):
            result = owner.post('/api/admin/platform/store-management', {'slug': 'not-created'}, format='json')
        self.assertEqual(result.status_code, 503)
        self.assertFalse(Store.objects.filter(slug='not-created').exists())

    def test_store_background_and_introduction_follow_the_store_directory(self):
        actor, owner = self.platform('presentation-owner', 'SUPER_ADMIN')
        _, finance = self.platform('presentation-finance', 'FINANCE')
        branch = Store.objects.create(slug='xinan', name='兴安电竞', database_alias='store_b',
                                      initialized_at=timezone.now(), status=Store.Status.CONFIGURING)
        route = f'/api/admin/platform/store-management/{branch.pk}'
        asset_route = route + '/background-upload'

        def background():
            stream = BytesIO()
            Image.new('RGB', (1280, 720), 'navy').save(stream, format='PNG')
            return SimpleUploadedFile('background.png', stream.getvalue(), content_type='image/png')

        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, MEDIA_BASE_URL=''):
            self.assertEqual(finance.post(asset_route, {'image': background()}, format='multipart').status_code, 403)
            upload = owner.post(asset_route, {'image': background()}, format='multipart')
            self.assertEqual(upload.status_code, 200, upload.content)
            image_url = upload.data['data']['url']
            uploaded = PlatformAuditEvent.objects.get(account=actor, action='store.background.upload',
                                                      target_store_slug=branch.slug)
            self.assertEqual(uploaded.detail['url'], image_url)
            timestamp = branch.updated_at.isoformat()
            changed = owner.patch(route, {'background_url': image_url, 'introduction': '  本店提供战术陪练。 ',
                                          'expected_updated_at': timestamp}, format='json')
            self.assertEqual(changed.status_code, 200, changed.content)
            self.assertEqual(changed.data['data']['introduction'], '本店提供战术陪练。')
            self.assertEqual(owner.patch(route, {'introduction': '旧版本', 'expected_updated_at': timestamp},
                                         format='json').status_code, 409)
            event = PlatformAuditEvent.objects.filter(account=actor, action='store.update_requested').order_by('pk').first()
            self.assertEqual(event.detail['fields'], ['background_url', 'introduction'])
            other = Store.objects.get(slug='shenglang')
            denied = owner.patch(f'/api/admin/platform/store-management/{other.pk}', {
                'background_url': image_url.split('?')[0], 'expected_updated_at': other.updated_at.isoformat(),
            }, format='json')
            self.assertEqual(denied.status_code, 400, denied.content)
            checks = {row['key']: row['status'] for row in owner.get(route + '/setup/readiness').data['data']['checks']}
            self.assertEqual((checks['store_background'], checks['store_introduction']), ('READY', 'READY'))
            customer = APIClient(HTTP_HOST='xa.esports.club')
            self.assertEqual(customer.get('/api/stores/current/').status_code, 403)
            Store.objects.filter(pk=branch.pk).update(status=Store.Status.ACTIVE)
            metadata = customer.get('/api/stores/current/').data['data']
            self.assertEqual((metadata['background_url'], metadata['introduction']),
                             (image_url, '本店提供战术陪练。'))
            self.assertEqual(customer.get(image_url).status_code, 200)
            self.assertEqual(APIClient(HTTP_HOST='sl.esports.club').get(image_url).status_code, 400)
            other.initialized_at = timezone.now()
            other.save(update_fields=['initialized_at'])
            other_route = f'/api/admin/platform/store-management/{other.pk}'
            other_upload = owner.post(other_route + '/background-upload', {'image': background()}, format='multipart')
            self.assertEqual(other_upload.status_code, 200, other_upload.content)
            other_image = other_upload.data['data']['url']
            changed = owner.patch(other_route, {'background_url': other_image,
                'expected_updated_at': other.updated_at.isoformat()}, format='json')
            self.assertEqual(changed.status_code, 200, changed.content)
            self.assertEqual(changed.data['data']['background_url'], other_image)

    @override_settings(ISOLATED_CONTROL_PLANE=True)
    def test_presentation_keeps_old_signed_routes_and_requires_a_new_revision(self):
        from .routing_snapshot import capture, lookup, publish

        branch = Store.objects.create(slug='xinan', name='兴安电竞', database_alias='store_b',
                                      initialized_at=timezone.now(), status=Store.Status.ACTIVE)
        old = capture(branch, 0)
        self.assertEqual(old['schema'], 2)
        publish(old)
        self.assertEqual(lookup(branch.slug).introduction, '')
        branch.introduction = '本店公开介绍'
        with self.assertRaisesMessage(ValueError, '同一门店路由版本内容不一致'):
            publish(capture(branch, 0))
        _, owner = self.platform('presentation-snapshot-owner', 'SUPER_ADMIN')
        changed = owner.patch(f'/api/admin/platform/store-management/{branch.pk}', {
            'introduction': branch.introduction, 'expected_updated_at': branch.updated_at.isoformat(),
        }, format='json')
        self.assertEqual(changed.status_code, 200, changed.content)
        publish(old)
        with patch('stores.context._fetch_store', side_effect=DatabaseError('control offline')):
            offline = resolve_store(branch.slug, allow_cached=True)
        self.assertEqual(offline.introduction, '本店公开介绍')

    def test_resume_rejects_unverified_database_and_keeps_password_out_of_audit(self):
        actor, owner = self.platform('resume-platform-owner', 'SUPER_ADMIN')
        store = Store.objects.create(slug='pending-platform', name='未完成门店', entity_prefix='PN',
                                     database_alias='store_b', status=Store.Status.PROVISIONING)
        password = 'Synthetic-Recovery-Password-2026!'
        with patch('stores.provisioning.create_database', side_effect=CommandError('目标数据库归属无法核实')):
            result = owner.post(f'/api/admin/platform/store-management/{store.pk}/initialize', {
                'admin_username': 'initial-owner', 'admin_password': password, 'admin_phone': '13800003333',
            }, format='json')
        self.assertEqual(result.status_code, 409, result.content)
        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.PROVISIONING)
        self.assertIsNone(store.initialized_at)
        events = list(PlatformAuditEvent.objects.filter(account=actor, action__startswith='store.initialize_').values('detail', 'target_id'))
        self.assertEqual(len(events), 2)
        self.assertNotIn(password, json.dumps(events, ensure_ascii=False))

    def test_interrupted_create_reserves_original_admin_before_database_access(self):
        _, owner = self.platform('early-interruption-owner', 'SUPER_ADMIN')
        admin_details = {
            'admin_username': 'first-owner',
            'admin_password': 'Synthetic-Safe-Resume-Password-2026!',
            'admin_phone': '13800003333',
            'admin_nickname': '首位管理员',
        }
        path = '/api/admin/platform/store-management'
        with patch('stores.provisioning.create_database', side_effect=CommandError('建库前中断')):
            failed = owner.post(path, {
                'slug': 'early-interruption', 'name': '待恢复门店', 'entity_prefix': 'EI',
                'database_alias': 'store_b', **admin_details,
            }, format='json')
        self.assertEqual(failed.status_code, 409, failed.data)
        store = Store.objects.get(slug='early-interruption')
        claim = StoreProvisioningClaim.objects.get(store=store)
        self.assertEqual(claim.admin_username, 'first-owner')
        self.assertEqual(store.status, Store.Status.PROVISIONING)
        self.assertIsNone(store.initialized_at)

        resume_path = f'{path}/{store.pk}/initialize'
        swapped = owner.post(resume_path, {**admin_details, 'admin_username': 'replacement-owner'}, format='json')
        self.assertEqual(swapped.status_code, 409, swapped.data)
        self.assertIn('原先登记的管理员账号', swapped.data['msg'])
        with store_context(store):
            self.assertFalse(ClubAccount.objects.exists())

        recovered = owner.post(resume_path, admin_details, format='json')
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertEqual(recovered.data['data']['status'], Store.Status.CONFIGURING)
        claim.refresh_from_db()
        self.assertEqual(claim.admin_username, 'first-owner')
        with store_context(store):
            self.assertEqual(list(ClubAccount.objects.values_list('username', 'account_type')), [('first-owner', 'STAFF')])

    def test_registration_rolls_back_if_database_claim_cannot_be_saved(self):
        _, owner = self.platform('claim-interruption-owner', 'SUPER_ADMIN')
        with (
            patch('stores.provision_claim.StoreProvisioningClaim.objects.get_or_create',
                  side_effect=DatabaseError('归属记录写入中断')),
            patch('stores.provisioning.create_database') as create_database,
        ):
            response = owner.post('/api/admin/platform/store-management', {
                'slug': 'unclaimed-store', 'name': '待登记门店', 'entity_prefix': 'UC',
                'database_alias': 'store_b', 'admin_username': 'intended-owner',
                'admin_password': 'Synthetic-Safe-Claim-Password-2026!',
                'admin_phone': '13800003333', 'admin_nickname': '首位管理员',
            }, format='json')
        self.assertEqual(response.status_code, 503, response.data)
        self.assertFalse(Store.objects.filter(slug='unclaimed-store').exists())
        self.assertFalse(StoreProvisioningClaim.objects.filter(store__slug='unclaimed-store').exists())
        create_database.assert_not_called()

    def test_concurrent_resume_returns_the_completed_store(self):
        from .provisioning import create_database

        actor, owner = self.platform('concurrent-resume-owner', 'SUPER_ADMIN')
        _, other = self.platform('concurrent-resume-other', 'SUPER_ADMIN')
        store = Store.objects.create(slug='concurrent-resume', name='并发恢复门店', entity_prefix='CR',
                                     database_alias='store_b', status=Store.Status.PROVISIONING)
        path = f'/api/admin/platform/store-management/{store.pk}/initialize'
        data = {'admin_username': 'concurrent-first-owner',
                'admin_password': 'Synthetic-Concurrent-Resume-Password-2026!',
                'admin_phone': '13800003333', 'admin_nickname': '首位管理员'}

        def second_request_finishes(alias, *, admin_username=None):
            with patch('stores.provisioning.create_database', wraps=create_database):
                completed = other.post(path, data, format='json')
            self.assertEqual(completed.status_code, 200, completed.data)
            return create_database(alias, admin_username=admin_username)

        with patch('stores.provisioning.create_database', side_effect=second_request_finishes):
            resumed = owner.post(path, data, format='json')
        self.assertEqual(resumed.status_code, 200, resumed.data)
        store.refresh_from_db()
        self.assertEqual(resumed.data['data']['initialized_at'], store.initialized_at)
        self.assertEqual(resumed.data['data']['status'], Store.Status.CONFIGURING)
        event = PlatformAuditEvent.objects.get(account=actor, action='store.initialize_result')
        self.assertEqual(event.detail['status_code'], 200)
        with store_context(store):
            self.assertEqual(list(ClubAccount.objects.values_list('username', 'account_type')),
                             [('concurrent-first-owner', 'STAFF')])

    def test_create_returns_registered_store_if_concurrent_resume_has_finished(self):
        from .provisioning import create_database

        _, owner = self.platform('concurrent-create-owner', 'SUPER_ADMIN')
        _, recovery = self.platform('concurrent-create-recovery', 'SUPER_ADMIN')
        path = '/api/admin/platform/store-management'
        data = {'admin_username': 'registered-owner',
                'admin_password': 'Synthetic-Concurrent-Create-Password-2026!',
                'admin_phone': '13800003333', 'admin_nickname': '首位管理员'}

        def recovery_finishes(alias, *, admin_username=None):
            store = Store.objects.get(database_alias=alias)
            with patch('stores.provisioning.create_database', wraps=create_database):
                completed = recovery.post(f'{path}/{store.pk}/initialize', data, format='json')
            self.assertEqual(completed.status_code, 200, completed.data)
            return create_database(alias, admin_username=admin_username)

        with patch('stores.provisioning.create_database', side_effect=recovery_finishes):
            registered = owner.post(path, {
                'slug': 'create-and-resume', 'name': '并发登记门店', 'entity_prefix': 'CA',
                'database_alias': 'store_b', **data,
            }, format='json')
        self.assertEqual(registered.status_code, 201, registered.data)
        store = Store.objects.get(slug='create-and-resume')
        self.assertEqual(registered.data['data']['initialized_at'], store.initialized_at)
        self.assertEqual(registered.data['data']['status'], Store.Status.CONFIGURING)
        with store_context(store):
            self.assertEqual(list(ClubAccount.objects.values_list('username', 'account_type')),
                             [('registered-owner', 'STAFF')])

    def test_command_records_admin_in_registration_before_database_creation(self):
        options = {'slug': 'command-interruption', 'name': '命令登记门店', 'database': 'store_b',
                   'create_database': True, 'admin_username': 'command-owner', 'admin_phone': '13800003333'}
        with patch('stores.management.commands.provision_store.create_database',
                   side_effect=CommandError('建库前中断')) as create_database:
            with self.assertRaisesMessage(CommandError, '管理员用户名必须为1到150位'):
                call_command('provision_store', **{**options, 'admin_username': '   '})
            self.assertFalse(Store.objects.filter(slug=options['slug']).exists())
            create_database.assert_not_called()
            with self.assertRaisesMessage(CommandError, '建库前中断'):
                call_command('provision_store', **options)
        store = Store.objects.get(slug=options['slug'])
        self.assertEqual(store.status, Store.Status.PROVISIONING)
        self.assertEqual(StoreProvisioningClaim.objects.get(store=store).admin_username, 'command-owner')
        with self.assertRaisesMessage(CommandError, '原先登记的管理员账号'):
            call_command('provision_store', **{**options, 'admin_username': 'replacement-owner'})
        with store_context(store):
            self.assertFalse(ClubAccount.objects.exists())

    def test_result_audit_failure_reports_uncertain_after_successful_update(self):
        actor, owner = self.platform('result-audit-owner', 'SUPER_ADMIN')
        store = resolve_store()
        real_create = PlatformAuditEvent.objects.create
        def audit(**kwargs):
            if kwargs['action'] == 'store.update_result':
                raise DatabaseError('test failure')
            return real_create(**kwargs)
        with patch('stores.platform_store_management.PlatformAuditEvent.objects.create', side_effect=audit):
            result = owner.patch(f'/api/admin/platform/store-management/{store.pk}',
                                 {'name': '测试改名', 'expected_updated_at': store.updated_at.isoformat()}, format='json')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.data['errorCode'], 'STORE_RESULT_UNCERTAIN')
        store.refresh_from_db(); self.assertEqual(store.name, '测试改名')
        self.assertTrue(PlatformAuditEvent.objects.filter(account=actor, action='store.update_requested').exists())
