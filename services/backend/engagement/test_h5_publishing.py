"""Store-domain H5 publishing contract: immutable snapshots, compliance and withdrawal."""
import hashlib
import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.db.models import ProtectedError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.compliance import ComplianceUnavailable
from console.models import AdminAuditLog, AdminMembership, AdminRole, ContentComplianceReceipt
from orders.models import GameCategory, ServiceCategory, ServiceItem
from stores.context import resolve_store, store_context
from stores.models import Store
from stores.tests_foundation import store_b
from wallet.models import IdempotencyKey

from .models import ClubContent, H5Page, H5PageVersion


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'],
    STORE_HOST_BINDINGS={
        'sl.esports.club': 'shenglang',
        'branch.esports.club': 'branch-b',
        'admin.esports.club': None,
        'testserver': 'shenglang',
    },
)
class H5PublishingTests(APITestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.first.name = '声浪电竞'
        self.first.currency_name = '浪花'
        self.first.introduction = '<p>认真陪玩，按时履约。</p><script>window.BAD=1</script>'
        self.first.save(update_fields=['name', 'currency_name', 'introduction'])
        self.second = store_b()
        self.second.name = '分店电竞'
        self.second.currency_name = '星辉'
        self.second.introduction = '分店介绍'
        self.second.save(update_fields=['name', 'currency_name', 'introduction'])
        self.editor = self.staff(self.first, 'h5-editor', ['engagement:view', 'engagement:edit'])
        self.reader = self.staff(self.first, 'h5-reader', ['engagement:view'])
        self.other_editor = self.staff(self.second, 'h5-other-editor', ['engagement:view', 'engagement:edit'])
        self.admin = self.client_for(self.editor)
        self.seed_price_sources()

    def staff(self, store, username, permissions):
        from django.contrib.auth import get_user_model
        with store_context(store):
            user = get_user_model().objects.create_user(username=username, password='test-password', role='ADMIN')
            account = get_or_create_account_for_legacy_user(user)
            role = AdminRole.objects.create(name=username, code=username, permissions=permissions)
            membership = AdminMembership.objects.create(user=user, account=account)
            membership.roles.add(role)
            return account

    def client_for(self, account, host='admin.esports.club'):
        store = Store.objects.using('default').get(database_alias=account._state.db)
        with store_context(store):
            token = issue_account_tokens(account)[0]
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def seed_price_sources(self):
        with store_context(self.first):
            game = GameCategory.objects.create(name='王者荣耀')
            category = ServiceCategory.objects.create(name='陪玩')
            gift = ServiceCategory.objects.create(name='礼物', is_gift=True)
            self.service = ServiceItem.objects.create(
                name='双陪上分', price=1234, billing_provider_count=2,
                price_unit='局', game_category=game, service_category=category,
                description='两人一起陪你玩', rules='按约定时间开始',
                details_html='<p>服务结束由双方确认。</p><script>window.BAD=1</script>',
            )
            ServiceItem.objects.create(name='隐藏服务', price=100, is_active=False,
                                       game_category=game, service_category=category)
            ServiceItem.objects.create(name='加购服务', price=100, is_addon=True,
                                       game_category=game, service_category=category)
            ServiceItem.objects.create(name='礼物服务', price=100,
                                       game_category=game, service_category=gift)
            ServiceItem.objects.create(name='资格体验', price=100, experience_kind='NEW_VALUE',
                                       game_category=game, service_category=category)
            ClubContent.objects.create(code='h5-order-notice', kind='ORDER_RULES', title='预约须知',
                                       content='<p>请提前十分钟上线。</p>')

    def publish(self, kind='INTRODUCTION', request_id=None, expected_version=0, client=None, **extra):
        payload = {
            'client_request_id': request_id or uuid4().hex,
            'expected_version': expected_version,
            'copyright_confirmed': True,
            **extra,
        }
        return (client or self.admin).post(
            f'/api/admin/h5-pages/{kind}/publish/', payload, format='json')

    def withdraw(self, kind='INTRODUCTION', request_id=None, expected_version=1, client=None, reason='测试撤回'):
        payload = {
            'client_request_id': request_id or uuid4().hex,
            'expected_version': expected_version,
            'reason': reason,
        }
        return (client or self.admin).post(
            f'/api/admin/h5-pages/{kind}/withdraw/', payload, format='json')

    def test_list_preview_and_public_writes_follow_contract(self):
        response = self.admin.get('/api/admin/h5-pages/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['data']['total'], 3)
        rows = {row['kind']: row for row in response.data['data']['list']}
        self.assertEqual(set(rows), {'PRICE_LIST', 'ORDER_NOTICE', 'INTRODUCTION'})
        self.assertTrue(all(row['revision'] == 0 and row['publicToken'] is None for row in rows.values()))
        self.assertFalse(H5Page.objects.exists())

        preview = self.admin.get('/api/admin/h5-pages/INTRODUCTION/preview/')
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertTrue(preview.data['data']['publishable'])
        self.assertEqual(preview.data['data']['snapshot']['body'], '认真陪玩，按时履约。')
        self.assertNotIn('window.BAD', str(preview.data))
        self.assertFalse(H5Page.objects.exists())

        self.assertEqual(self.admin.post('/api/club/h5-pages/00000000-0000-0000-0000-000000000000/',
                                         {}, format='json').status_code, 405)
        self.assertEqual(self.admin.post('/h5/pages/00000000-0000-0000-0000-000000000000/',
                                         {}, format='json').status_code, 405)

    def test_action_specific_permissions_and_non_console_identity(self):
        url = '/api/admin/h5-pages/'
        self.assertEqual(APIClient(HTTP_HOST='admin.esports.club').get(url).status_code, 401)
        denied = self.client_for(self.reader)
        self.assertEqual(denied.get(url).status_code, 200)
        self.assertEqual(denied.post('/api/admin/h5-pages/INTRODUCTION/publish/', {
            'client_request_id': uuid4().hex, 'expected_version': 0, 'copyright_confirmed': True,
        }, format='json').status_code, 403)
        self.assertEqual(denied.post('/api/admin/h5-pages/INTRODUCTION/withdraw/', {
            'client_request_id': uuid4().hex, 'expected_version': 0, 'reason': '测试',
        }, format='json').status_code, 403)
        self.assertFalse(H5Page.objects.exists())

        with store_context(self.first):
            boss = ClubAccount.objects.create_account(username='h5-boss', account_type='BOSS')
        boss_client = self.client_for(boss)
        self.assertEqual(boss_client.get(url).status_code, 401)
        self.assertEqual(boss_client.post('/api/admin/h5-pages/INTRODUCTION/publish/', {
            'client_request_id': uuid4().hex, 'expected_version': 0, 'copyright_confirmed': True,
        }, format='json').status_code, 401)

    def test_publish_uses_public_price_semantics_and_freezes_snapshot(self):
        response = self.publish('PRICE_LIST', request_id='price-publish-1')
        self.assertEqual(response.status_code, 201, response.content)
        data = response.data['data']
        token = data['publicToken']
        self.assertEqual(data['version'], 1)
        self.assertEqual(data['publicPath'], f'/h5/pages/{token}/')
        self.assertEqual(data['jsonPath'], f'/api/club/h5-pages/{token}/')
        page = H5Page.objects.get(kind='PRICE_LIST')
        version = page.versions.get(version=1)
        expected_digest = hashlib.sha256(json.dumps(
            version.snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(version.content_sha256, expected_digest)
        self.assertEqual(version.snapshot['items'][0]['priceUnits'], 2468)
        self.assertEqual(version.snapshot['items'][0]['priceText'], '246.8 浪花')
        self.assertEqual([row['name'] for row in version.snapshot['items']], ['双陪上分'])
        self.assertEqual(version.snapshot['items'][0]['details'], '服务结束由双方确认。')
        self.assertNotIn('example.com', str(version.snapshot))

        public = APIClient(HTTP_HOST='sl.esports.club').get(data['jsonPath'])
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(public.data['data']['snapshot']['items'][0]['name'], '双陪上分')
        self.assertEqual(public.data['data']['version'], 1)
        self.assertEqual(public.data['data']['contentSha256'], expected_digest)
        self.assertEqual(public['Cache-Control'], 'no-store')
        self.assertEqual(public['X-Store-Slug'], 'shenglang')
        self.assertEqual(public.data['data']['copyrightConfirmed'], True)
        self.assertNotIn('operator', str(public.data).lower())

        html = APIClient(HTTP_HOST='sl.esports.club').get(data['publicPath'])
        self.assertEqual(html.status_code, 200, html.content)
        self.assertEqual(html['Content-Type'], 'text/html; charset=utf-8')
        self.assertEqual(html['Cache-Control'], 'no-store')
        self.assertIn('双陪上分', html.content.decode())
        self.assertIn('246.8 浪花', html.content.decode())
        self.assertNotIn('<script', html.content.decode().lower())

        with store_context(self.first):
            self.service.name = '已修改服务'
            self.service.price = 9999
            self.service.save(update_fields=['name', 'price'])
        unchanged = APIClient(HTTP_HOST='sl.esports.club').get(data['jsonPath'])
        self.assertEqual(unchanged.data['data']['snapshot']['items'][0]['name'], '双陪上分')
        self.assertEqual(unchanged.data['data']['snapshot']['items'][0]['priceUnits'], 2468)
        version.refresh_from_db()
        self.assertEqual(version.snapshot['items'][0]['name'], '双陪上分')

    def test_notice_and_introduction_are_text_only_and_safe(self):
        notice = self.publish('ORDER_NOTICE', request_id='notice-publish')
        self.assertEqual(notice.status_code, 201, notice.content)
        notice_public = APIClient(HTTP_HOST='sl.esports.club').get(notice.data['data']['jsonPath'])
        self.assertEqual(notice_public.data['data']['snapshot']['sections'][0]['content'], '请提前十分钟上线。')
        introduction = self.publish('INTRODUCTION', request_id='intro-publish')
        self.assertEqual(introduction.status_code, 201, introduction.content)
        intro_html = APIClient(HTTP_HOST='sl.esports.club').get(introduction.data['data']['publicPath'])
        text = intro_html.content.decode()
        self.assertIn('认真陪玩，按时履约。', text)
        self.assertNotIn('window.BAD', text)
        self.assertNotIn('<script', text.lower())

        with store_context(self.first):
            store = Store.objects.get(pk=self.first.pk)
            store.introduction = '后来修改的介绍'
            store.save(update_fields=['introduction'])
        intro_public = APIClient(HTTP_HOST='sl.esports.club').get(introduction.data['data']['jsonPath'])
        self.assertEqual(intro_public.data['data']['snapshot']['body'], '认真陪玩，按时履约。')

    def test_missing_or_image_dependent_notice_and_rules_block_without_reserving_request(self):
        request_id = 'source-retry'
        with store_context(self.first):
            ClubContent.objects.all().delete()
            self.service.rules_images = ['/media/club-catalog/rules.png']
            self.service.save(update_fields=['rules_images'])
        blocked = self.publish('PRICE_LIST', request_id=request_id)
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertFalse(H5Page.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(request_id=request_id).exists())
        with store_context(self.first):
            self.service.rules_images = []
            self.service.save(update_fields=['rules_images'])
            ClubContent.objects.create(code='image-notice', kind='ORDER_RULES', title='图片须知',
                                       image_url='/media/club-catalog/notice.png', content='')
        blocked_notice = self.publish('ORDER_NOTICE', request_id='notice-image')
        self.assertEqual(blocked_notice.status_code, 400, blocked_notice.content)
        self.assertFalse(H5Page.objects.exists())
        self.assertFalse(AdminAuditLog.objects.exists())
        self.assertFalse(ContentComplianceReceipt.objects.exists())
        with store_context(self.first):
            ClubContent.objects.all().update(content='文字须知', image_url='')
        self.assertEqual(self.publish('ORDER_NOTICE', request_id='notice-image').status_code, 201)

    def test_missing_introduction_and_copyright_confirmation_are_rejected(self):
        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(introduction='  ')
        self.assertEqual(self.publish('INTRODUCTION', request_id='empty-intro').status_code, 400)
        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(introduction='有效介绍')
        missing = self.admin.post('/api/admin/h5-pages/INTRODUCTION/publish/', {
            'client_request_id': 'no-copyright', 'expected_version': 0,
        }, format='json')
        self.assertEqual(missing.status_code, 400)
        extra = self.publish('INTRODUCTION', request_id='extra-field', body='不应接受任意正文')
        self.assertEqual(extra.status_code, 400)
        self.assertFalse(H5Page.objects.exists())

    def test_compliance_violation_and_unavailability_leave_no_partial_publication(self):
        original = self.first.introduction
        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(introduction='全网最低')
        violation = self.publish('INTRODUCTION', request_id='compliance-bad')
        self.assertEqual(violation.status_code, 400, violation.content)
        self.assertEqual(violation.data.get('errorCode'), 'CONTENT_COMPLIANCE_VIOLATION')
        self.assertFalse(H5Page.objects.exists())
        self.assertFalse(ContentComplianceReceipt.objects.exists())
        self.assertFalse(AdminAuditLog.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(request_id='compliance-bad').exists())

        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(introduction=original)
        with patch('engagement.h5_publishing.check_content', side_effect=ComplianceUnavailable()):
            unavailable = self.publish('INTRODUCTION', request_id='compliance-offline')
        self.assertEqual(unavailable.status_code, 503, unavailable.content)
        self.assertEqual(unavailable.data.get('errorCode'), 'COMPLIANCE_RULES_UNAVAILABLE')
        self.assertFalse(H5Page.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(request_id='compliance-offline').exists())

    def test_receipt_and_audit_are_digest_only_and_fail_atomically(self):
        published = self.publish('INTRODUCTION', request_id='atomic-good')
        self.assertEqual(published.status_code, 201, published.content)
        page = H5Page.objects.get(kind='INTRODUCTION')
        version = page.versions.get(version=1)
        receipt_ids = version.compliance_receipt_ids
        self.assertEqual(set(ContentComplianceReceipt.objects.filter(id__in=receipt_ids)
                             .values_list('field_name', flat=True)), {'STORE_NAME', 'STORE_INTRODUCTION'})
        for receipt in ContentComplianceReceipt.objects.filter(id__in=receipt_ids):
            self.assertEqual(receipt.content_type, 'h5_page')
            self.assertEqual(receipt.object_id, page.pk)
            self.assertEqual(len(receipt.content_sha256), 64)
            self.assertNotIn('认真陪玩', str(receipt.__dict__))
        audit = AdminAuditLog.objects.get(resource='h5-page-publish')
        self.assertNotIn('认真陪玩', json.dumps(audit.request_body, ensure_ascii=False))
        self.assertEqual(audit.operator_account_id, self.editor.pk)
        self.assertEqual(audit.request_body['contentSha256'], version.content_sha256)
        self.assertEqual(audit.request_body['clientRequestId'], 'atomic-good')

        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(introduction='第二版介绍')
        with patch('engagement.h5_publishing.record_content_receipt', side_effect=DatabaseError('receipt offline')):
            with self.assertRaises(DatabaseError):
                self.publish('INTRODUCTION', request_id='atomic-receipt-fail', expected_version=1)
        page.refresh_from_db()
        self.assertEqual((page.revision, page.current_version_id), (1, version.pk))
        self.assertEqual(page.versions.count(), 1)
        self.assertEqual(AdminAuditLog.objects.count(), 1)
        self.assertFalse(IdempotencyKey.objects.filter(request_id='atomic-receipt-fail').exists())

        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(introduction='第三版介绍')
        with patch('engagement.h5_publishing.record_audit', side_effect=DatabaseError('audit offline')):
            with self.assertRaises(DatabaseError):
                self.publish('INTRODUCTION', request_id='atomic-audit-fail', expected_version=1)
        page.refresh_from_db()
        self.assertEqual((page.revision, page.current_version_id), (1, version.pk))
        self.assertEqual(page.versions.count(), 1)
        self.assertEqual(AdminAuditLog.objects.count(), 1)

    def test_idempotent_replay_conflict_and_stale_revision(self):
        first = self.publish('INTRODUCTION', request_id='same-request')
        self.assertEqual(first.status_code, 201, first.content)
        replay = self.publish('INTRODUCTION', request_id='same-request')
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.data['data']['replayed'])
        self.assertEqual(replay.data['data']['version'], first.data['data']['version'])
        self.assertEqual(H5PageVersion.objects.count(), 1)
        self.assertEqual(AdminAuditLog.objects.count(), 1)
        self.assertEqual(IdempotencyKey.objects.count(), 1)

        conflict = self.publish('INTRODUCTION', request_id='same-request', expected_version=1)
        self.assertEqual(conflict.status_code, 409, conflict.content)
        self.assertEqual(conflict.data.get('errorCode'), 'REQUEST_ID_CONFLICT')
        other_scope = self.withdraw('INTRODUCTION', request_id='same-request', expected_version=1)
        self.assertEqual(other_scope.status_code, 409)
        self.assertEqual(other_scope.data.get('errorCode'), 'REQUEST_ID_CONFLICT')
        stale = self.publish('INTRODUCTION', request_id='stale-revision', expected_version=0)
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(stale.data.get('errorCode'), 'H5_REVISION_CONFLICT')
        self.assertEqual(H5PageVersion.objects.count(), 1)

    def test_withdraw_makes_both_public_routes_unreadable_and_republish_keeps_history(self):
        first = self.publish('INTRODUCTION', request_id='withdraw-publish')
        token = first.data['data']['publicToken']
        withdrawn = self.withdraw('INTRODUCTION', request_id='withdraw-once', expected_version=1)
        self.assertEqual(withdrawn.status_code, 200, withdrawn.content)
        page = H5Page.objects.get(kind='INTRODUCTION')
        first_version = page.versions.get(version=1)
        self.assertIsNone(page.current_version)
        self.assertEqual(page.revision, 1)
        for path in (first.data['data']['jsonPath'], first.data['data']['publicPath']):
            response = APIClient(HTTP_HOST='sl.esports.club').get(path)
            self.assertEqual(response.status_code, 404, response.content)
            self.assertEqual(response['Cache-Control'], 'no-store')
        replay = self.withdraw('INTRODUCTION', request_id='withdraw-once', expected_version=1)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data['data']['replayed'])
        self.assertEqual(AdminAuditLog.objects.filter(resource='h5-page-withdraw').count(), 1)

        republished = self.publish('INTRODUCTION', request_id='withdraw-republish', expected_version=1)
        self.assertEqual(republished.status_code, 201, republished.content)
        self.assertEqual(republished.data['data']['publicToken'], token)
        self.assertEqual(republished.data['data']['version'], 2)
        self.assertEqual(APIClient(HTTP_HOST='sl.esports.club').get(first.data['data']['jsonPath']).status_code, 200)
        self.assertEqual(page.versions.count(), 2)
        first_version.refresh_from_db()
        self.assertEqual(first_version.snapshot['body'], '认真陪玩，按时履约。')
        self.assertEqual(list(page.versions.order_by('version').values_list('version', flat=True)), [1, 2])
        self.assertEqual(self.withdraw('INTRODUCTION', request_id='withdraw-twice', expected_version=2).status_code, 200)
        self.assertEqual(self.withdraw('INTRODUCTION', request_id='withdraw-empty', expected_version=2).status_code, 409)

    def test_historical_versions_are_immutable_and_admin_history_requires_view(self):
        self.publish('INTRODUCTION', request_id='history-publish')
        page = H5Page.objects.get(kind='INTRODUCTION')
        version = page.versions.get(version=1)
        version.snapshot['body'] = '篡改'
        with self.assertRaises(DjangoValidationError):
            version.save()
        with self.assertRaises(DjangoValidationError):
            version.delete()
        with self.assertRaises(ProtectedError):
            page.delete()

        history = self.admin.get('/api/admin/h5-pages/INTRODUCTION/versions/')
        self.assertEqual(history.status_code, 200, history.content)
        self.assertEqual(history.data['data']['list'][0]['version'], 1)
        self.assertNotIn('snapshot', history.data['data']['list'][0])
        detail = self.admin.get(f'/api/admin/h5-pages/INTRODUCTION/versions/1/')
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.data['data']['snapshot']['body'], '认真陪玩，按时履约。')
        denied_receipts = self.admin.get('/api/admin/h5-pages/INTRODUCTION/versions/1/receipts/')
        self.assertEqual(denied_receipts.status_code, 403)
        compliance_reader = self.staff(self.first, 'h5-compliance-reader', ['engagement:view', 'content:view'])
        receipts = self.client_for(compliance_reader).get('/api/admin/h5-pages/INTRODUCTION/versions/1/receipts/')
        self.assertEqual(receipts.status_code, 200, receipts.content)
        self.assertNotIn('认真陪玩', str(receipts.data))
        denied = self.client_for(self.reader)
        self.assertEqual(denied.get('/api/admin/h5-pages/INTRODUCTION/versions/1/').status_code, 200)

    def test_store_domain_is_authoritative_and_tokens_never_cross_stores(self):
        published = self.publish('INTRODUCTION', request_id='domain-publish')
        token = published.data['data']['publicToken']
        public = APIClient(HTTP_HOST='sl.esports.club')
        self.assertEqual(public.get(published.data['data']['jsonPath']).status_code, 200)
        self.assertEqual(APIClient(HTTP_HOST='branch.esports.club').get(
            published.data['data']['jsonPath']).status_code, 404)
        self.assertEqual(APIClient(HTTP_HOST='unknown.esports.club').get(
            published.data['data']['jsonPath']).status_code, 404)
        self.assertEqual(APIClient(HTTP_HOST='admin.esports.club').get(
            published.data['data']['jsonPath']).status_code, 401)
        self.assertEqual(public.get(published.data['data']['jsonPath'], {'store': 'branch-b'}).status_code, 400)
        self.assertEqual(public.get(published.data['data']['jsonPath'],
                                    HTTP_X_STORE_SLUG='branch-b').status_code, 400)
        self.assertEqual(public.get(published.data['data']['jsonPath'],
                                    HTTP_X_FORWARDED_HOST='branch.esports.club').status_code, 200)
        self.assertEqual(public.get(f'/h5/pages/{token}/?version=1').status_code, 200)
        self.assertEqual(public.get(f'/h5/pages/{uuid4()}/').status_code, 404)
        self.assertEqual(public.post(published.data['data']['jsonPath'], {}, format='json').status_code, 405)

        with store_context(self.second):
            H5Page.objects.create(kind='INTRODUCTION', public_token=token)
        other_publish = self.publish('INTRODUCTION', request_id='other-domain-publish', client=self.client_for(self.other_editor))
        self.assertEqual(other_publish.status_code, 201, other_publish.content)
        first_public = public.get(published.data['data']['jsonPath'])
        second_public = APIClient(HTTP_HOST='branch.esports.club').get(published.data['data']['jsonPath'])
        self.assertEqual(first_public.data['data']['snapshot']['store']['name'], '声浪电竞')
        self.assertEqual(second_public.data['data']['snapshot']['store']['name'], '分店电竞')
        self.assertNotEqual(first_public.data['data']['contentSha256'], second_public.data['data']['contentSha256'])

    def test_disabled_store_and_nonpublished_page_are_not_readable(self):
        with store_context(self.first):
            H5Page.objects.create(kind='INTRODUCTION', public_token=uuid4())
        page = H5Page.objects.get(kind='INTRODUCTION')
        path = f'/api/club/h5-pages/{page.public_token}/'
        self.assertEqual(APIClient(HTTP_HOST='sl.esports.club').get(path).status_code, 404)
        Store.objects.filter(pk=self.first.pk).update(status=Store.Status.DISABLED)
        self.assertEqual(APIClient(HTTP_HOST='sl.esports.club').get(path).status_code, 403)

    def test_nested_dropped_markup_never_leaks_script_text(self):
        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(
                introduction='<script><script>window.BAD=1</script>window.BAD=2</script><p>安全介绍</p>')
        response = self.admin.get('/api/admin/h5-pages/INTRODUCTION/preview/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.data['data']['publishable'])
        self.assertEqual(response.data['data']['snapshot']['body'], '安全介绍')
        self.assertNotIn('window.BAD', str(response.data))

    def test_public_version_parameter_and_old_replays_never_restore_history(self):
        first = self.publish('INTRODUCTION', request_id='stable-link-v1')
        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(introduction='第二版介绍')
        second = self.publish('INTRODUCTION', request_id='stable-link-v2', expected_version=1)
        self.assertEqual(second.status_code, 201, second.content)
        public = APIClient(HTTP_HOST='sl.esports.club')
        for path in (first.data['data']['jsonPath'] + '?version=1',
                     first.data['data']['publicPath'] + '?version=1'):
            current = public.get(path)
            self.assertEqual(current.status_code, 200, current.content)
            self.assertNotIn('认真陪玩', current.content.decode())
            self.assertIn('第二版介绍', current.content.decode())
        current_json = public.get(first.data['data']['jsonPath'] + '?version=1')
        self.assertEqual(current_json.data['data']['version'], 2)
        self.assertEqual(current_json.data['data']['contentSha256'], second.data['data']['contentSha256'])

        withdrawn = self.withdraw('INTRODUCTION', request_id='stable-link-withdraw', expected_version=2)
        self.assertEqual(withdrawn.status_code, 200, withdrawn.content)
        replay = self.publish('INTRODUCTION', request_id='stable-link-v1')
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.data['data']['replayed'])
        self.assertEqual(replay.data['data']['version'], 1)
        page = H5Page.objects.get(kind='INTRODUCTION')
        self.assertIsNone(page.current_version)
        self.assertEqual(page.versions.count(), 2)
        self.assertEqual(public.get(first.data['data']['jsonPath']).status_code, 404)

        replayed_withdraw = self.withdraw('INTRODUCTION', request_id='stable-link-withdraw', expected_version=2)
        self.assertEqual(replayed_withdraw.status_code, 200, replayed_withdraw.content)
        self.assertTrue(replayed_withdraw.data['data']['replayed'])
        self.assertIsNone(H5Page.objects.get(kind='INTRODUCTION').current_version)
        self.assertEqual(AdminAuditLog.objects.count(), 3)

    def test_compliance_opt_out_is_explicit_without_rule_access_or_receipts(self):
        with store_context(self.first):
            Store.objects.filter(pk=self.first.pk).update(
                introduction='全网最低', content_compliance_enabled=False)
        with patch('common.compliance.current_rules',
                   side_effect=AssertionError('停用合规时不应读取规则')):
            response = self.publish('INTRODUCTION', request_id='compliance-disabled')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data['data']['complianceStatus'], 'DISABLED')
        version = H5PageVersion.objects.get()
        self.assertEqual(version.compliance_status, 'DISABLED')
        self.assertEqual(version.compliance_receipt_ids, [])
        self.assertFalse(ContentComplianceReceipt.objects.exists())
        public = APIClient(HTTP_HOST='sl.esports.club').get(response.data['data']['jsonPath'])
        self.assertEqual(public.status_code, 200, public.content)
        self.assertNotIn('complianceStatus', public.data['data'])
        self.assertNotIn('complianceReceiptIds', public.data['data'])
        self.assertNotIn('publisher', str(public.data).lower())

    def test_store_local_models_do_not_share_numeric_ids_or_versions(self):
        self.publish('INTRODUCTION', request_id='store-a-version')
        self.publish('INTRODUCTION', request_id='store-b-version', client=self.client_for(self.other_editor))
        with store_context(self.first):
            first = H5Page.objects.get(kind='INTRODUCTION')
            first_version = first.versions.get()
            self.assertEqual(first_version.snapshot['store']['name'], '声浪电竞')
        with store_context(self.second):
            second = H5Page.objects.get(kind='INTRODUCTION')
            second_version = second.versions.get()
            self.assertEqual(second.pk, first.pk)
            self.assertEqual(second_version.pk, first_version.pk)
            self.assertEqual(second_version.snapshot['store']['name'], '分店电竞')
            self.assertEqual(second_version.version, 1)
