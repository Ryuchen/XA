import os
import json
from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from orders.models import GameCategory, GameCommissionProfile, OrderTimeoutConfig, ServiceCategory, ServiceItem
from stores.context import store_context
from stores.license_codes import _activate_after_verified_store_auth, issue
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store, StoreRoutingRevision
from stores.platform_auth import issue_tokens
from stores.tests_foundation import admin
from wallet.models import SystemConfig


@override_settings(PLATFORM_IDENTITY_ENABLED=True, STORE_FIRST_OPEN_ENABLED=True,
                   GAME_COMMISSION_PAYMENT_ENABLED=True, ALLOWED_HOSTS=['*'],
                   STORE_HOST_BINDINGS={'xa.esports.club':'xinan', 'sl.esports.club':'shenglang'})
class FirstOpenTests(TransactionTestCase):
    databases = {'default', 'store_a', 'store_b'}

    def setUp(self):
        self.stores = [Store.objects.update_or_create(slug=slug, defaults=dict(name=name, entity_prefix=prefix,
            provider_name_prefix=prefix, currency_name=currency, database_alias=alias,
            status=Store.Status.CONFIGURING, initialized_at=timezone.now()))[0] for slug, name, prefix, currency, alias in (
                ('shenglang', '声浪电竞', 'SL', '浪花', 'store_a'), ('xinan', '兴安电竞', 'XA', '星辉', 'store_b'))]
        Store.objects.update_or_create(slug='local-preview', defaults=dict(name='本地预览',
            database_alias='default', status=Store.Status.ACTIVE))
        self.actor = PlatformAccount.objects.create(username='first-open-owner', phone='13800001111', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=self.actor, role='SUPER_ADMIN', scope_slug='*')
        raw, _ = issue_tokens(self.actor, 'SMS', self.actor.phone)
        self.token = AccessToken(raw)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+raw)

    def setup_store(self, store):
        with store_context(store):
            owner = admin(f'open-{store.slug}', super_admin=True)
            owner.phone = '13800001234'; owner.set_password('Synthetic-Store-Password-2026!')
            owner.save(update_fields=['phone', 'password'])
            game = GameCategory.objects.create(name='瓦罗兰特')
            category = ServiceCategory.objects.create(name='陪玩')
            ServiceItem.objects.create(name='陪玩', price=1000, price_unit='小时',
                game_category=game, service_category=category, provider_kind='RANK')
            GameCommissionProfile.objects.create(game_category=game, game='VALORANT', rules={'companion': 20, 'grind': 30})
            OrderTimeoutConfig.objects.update_or_create(pk=1, defaults={'minutes':30, 'revision':1})
            values = {'provider_capacity_rules': {'max_concurrent_orders': 3, 'version': 1},
                'automatic_grab_window': {'window_seconds': 30, 'version': 1},
                'settlement_clock_rules': {'aftersale_hours': 72, 'income_freeze_hours': 72, 'version': 1},
                'provider_deposit_targets': {'cash_target': 40000, 'earnings_target': 80000, 'version': 1},
                f'order_account_fields_game_{game.pk}': {'version': 1, 'fields': []}}
            for key, value in values.items():
                SystemConfig.objects.create(key=key, value=json.dumps(value))
            for key, value in {'provider_deposit_income_percent':'0', 'min_withdraw_amount':'1000',
                               'withdraw_balance_threshold':'5000', 'withdraw_tax_rate':'0'}.items():
                SystemConfig.objects.create(key=key, value=value)
        url = f'/api/admin/platform/store-management/{store.pk}/setup'
        rows = {row['key']:row for row in self.client.get(url).data['data']['evidence']}
        for key in ('capacity', 'grab_window', 'settlement_clocks', 'deposit_targets', 'deposit_percent', 'withdraw'):
            response = self.client.post(url+'/confirm/'+key, {'fingerprint':rows[key]['snapshot']['fingerprint']}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        now = timezone.now()
        self.activate_opening_license(store, owner.pk, kind='INITIAL', tier='PRO',
                                      starts_at=now-timedelta(minutes=1), ends_at=now+timedelta(days=1))

    def activate_opening_license(self, store, actor_id, *, kind, tier, starts_at, ends_at):
        row, code = issue(self.token, store_slug=store.slug, tier=tier, kind=kind,
                          starts_at=starts_at, ends_at=ends_at, backdate_reason='隔离测试')
        _activate_after_verified_store_auth(store=store, actor_id=actor_id, code=code)
        return row

    def opening_with_license_boundary(self, *, replacement):
        from club_accounts.models import StoreRoutingSnapshot
        from stores import routing_snapshot

        store = self.stores[0]
        with store_context(store):
            owner = admin('opening-license-owner', super_admin=True)
        now = timezone.now()
        boundary = now + timedelta(minutes=10)
        end = now + timedelta(days=1) if replacement else boundary
        self.activate_opening_license(store, owner.pk, kind='INITIAL', tier='PRO', starts_at=now, ends_at=end)
        if replacement:
            next_grant = self.activate_opening_license(store, owner.pk, kind='CHANGE', tier='BASIC',
                                                      starts_at=boundary, ends_at=end)
        initial_updated_at = store.updated_at
        clock = now + timedelta(minutes=1)
        url = f'/api/admin/platform/store-management/{store.pk}/setup/readiness'
        latest_revision = routing_snapshot.latest_revision

        def wait_for_route_revision(locked_store):
            nonlocal clock
            revision = latest_revision(locked_store)
            clock = boundary
            return revision

        # Isolate the commit boundary from service/configuration fixtures. The
        # license, directory transaction, revision and snapshot are real.
        with (patch('stores.opening_readiness.opening_readiness', return_value={'can_open': True, 'checks': []}),
              patch('django.utils.timezone.now', side_effect=lambda: clock),
              patch.object(routing_snapshot, 'latest_revision', side_effect=wait_for_route_revision),
              patch.object(routing_snapshot, 'publish', wraps=routing_snapshot.publish) as publish):
            response = self.client.post(url, {'confirm': True}, format='json')
            self.assertEqual(response.status_code, 409, response.data)
            expected = 'STORE_OPENING_LICENSE_CHANGED' if replacement else 'STORE_OPENING_LICENSE_EXPIRED'
            self.assertEqual(response.data['errorCode'], expected)
            publish.assert_not_called()
            store.refresh_from_db()
            self.assertEqual((store.status, store.updated_at), (Store.Status.CONFIGURING, initial_updated_at))
            opened_events = PlatformAuditEvent.objects.filter(action='store.first_opened', target_store_slug=store.slug)
            self.assertFalse(opened_events.exists())
            self.assertFalse(StoreRoutingRevision.objects.filter(store=store).exists())
            with store_context(store):
                self.assertFalse(StoreRoutingSnapshot.objects.exists())
            if replacement:
                retry = self.client.post(url, {'confirm': True}, format='json')
                self.assertEqual(retry.status_code, 200, retry.data)
                self.assertEqual(opened_events.get().detail['license']['id'], str(next_grant.pk))
                publish.assert_called_once()

    @override_settings(ISOLATED_CONTROL_PLANE=True, STORE_DATABASE_ALIASES=('store_a', 'store_b'))
    def test_license_expiry_while_reading_routing_revision_rolls_back_opening(self):
        self.opening_with_license_boundary(replacement=False)

    @override_settings(ISOLATED_CONTROL_PLANE=True, STORE_DATABASE_ALIASES=('store_a', 'store_b'))
    def test_scheduled_license_taking_effect_while_opening_requires_a_fresh_check(self):
        self.opening_with_license_boundary(replacement=True)

    @override_settings(ISOLATED_CONTROL_PLANE=True, STORE_DATABASE_ALIASES=('store_a', 'store_b'))
    def test_notice_expiring_while_preparing_route_rolls_back_opening(self):
        from engagement.models import ClubContent
        from stores import routing_snapshot
        from stores.opening_materials import order_notice_evidence

        store = self.stores[0]
        now = timezone.now()
        deadline = now + timedelta(minutes=10)
        with store_context(store):
            owner = admin('opening-notice-owner', super_admin=True)
            ClubContent.objects.create(code='opening-notice', title='下单须知', kind=ClubContent.Kind.ORDER_RULES,
                                       content='请确认服务时间。', ends_at=deadline)
            evidence = order_notice_evidence(now)
        self.activate_opening_license(store, owner.pk, kind='INITIAL', tier='PRO',
                                      starts_at=now, ends_at=now+timedelta(days=1))
        clock = now + timedelta(minutes=1)
        schedule = routing_snapshot.schedule

        def delay_before_commit(*args):
            nonlocal clock
            schedule(*args)
            clock = deadline

        readiness = {'can_open': True, 'checks': [{'key': 'order_notice', 'details': evidence}]}
        with (patch('stores.opening_readiness.opening_readiness', return_value=readiness),
              patch('django.utils.timezone.now', side_effect=lambda: clock),
              patch.object(routing_snapshot, 'schedule', side_effect=delay_before_commit),
              patch.object(routing_snapshot, 'publish') as publish):
            url = f'/api/admin/platform/store-management/{store.pk}/setup/readiness'
            result = self.client.post(url, {'confirm': True}, format='json')
            self.assertEqual(result.status_code, 409, result.data)
            self.assertEqual(result.data['errorCode'], 'STORE_OPENING_NOTICE_CHANGED')
            publish.assert_not_called()
        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.CONFIGURING)
        self.assertFalse(StoreRoutingRevision.objects.filter(store=store).exists())

    @override_settings(ISOLATED_CONTROL_PLANE=True, STORE_DATABASE_ALIASES=('store_a', 'store_b'))
    def test_certificate_expiring_while_preparing_route_rolls_back_opening(self):
        from io import BytesIO
        from uuid import uuid4
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        from stores import routing_snapshot
        from stores.business_certificates import CERTIFICATE_TIMEZONE, certificate_evidence

        store = self.stores[0]
        now = timezone.now()
        expires = (timezone.localtime(now, CERTIFICATE_TIMEZONE) + timedelta(days=1)).replace(
            hour=23, minute=59, second=0, microsecond=0)
        with store_context(store):
            owner = admin('opening-certificate-owner', super_admin=True)
        self.activate_opening_license(store, owner.pk, kind='INITIAL', tier='PRO',
                                      starts_at=now, ends_at=expires+timedelta(days=1))
        asset = BytesIO()
        Image.new('RGB', (32, 24), 'navy').save(asset, format='PNG')
        base = f'/api/admin/platform/store-management/{store.pk}'
        uploaded = self.client.post(base + '/business-certificates', {
            'document': SimpleUploadedFile('certificate.png', asset.getvalue(), content_type='image/png'),
            'expires_on': expires.date().isoformat(), 'long_term': False, 'expected_certificate_id': '',
            'client_request_id': str(uuid4()), 'confirm_verified': True,
        }, format='multipart')
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        evidence = certificate_evidence(store, expires)
        self.assertEqual(evidence['status'], 'VALID')
        clock = expires
        schedule = routing_snapshot.schedule

        def delay_before_commit(*args):
            nonlocal clock
            schedule(*args)
            clock = expires + timedelta(minutes=1)

        readiness = {'can_open': True, 'checks': [{'key': 'business_certificate', 'details': evidence}]}
        with (patch('stores.opening_readiness.opening_readiness', return_value=readiness),
              patch('django.utils.timezone.now', side_effect=lambda: clock),
              patch.object(routing_snapshot, 'schedule', side_effect=delay_before_commit),
              patch.object(routing_snapshot, 'publish') as publish):
            response = self.client.post(base + '/setup/readiness', {'confirm': True}, format='json')
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.data['errorCode'], 'STORE_OPENING_CERTIFICATE_EXPIRED')
            publish.assert_not_called()
        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.CONFIGURING)
        self.assertFalse(StoreRoutingRevision.objects.filter(store=store).exists())
        self.assertFalse(PlatformAuditEvent.objects.filter(action='store.first_opened', target_store_slug=store.slug).exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_open_valid_store_and_leave_other_store_closed(self):
        store, other = self.stores
        self.setup_store(store)
        url = f'/api/admin/platform/store-management/{store.pk}/setup/readiness'
        readiness = self.client.get(url)
        self.assertTrue(readiness.data['data']['can_open'], readiness.data)
        opened = self.client.post(url, {'confirm':True}, format='json')
        self.assertEqual(opened.status_code, 200, opened.data)
        store.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((store.status, other.status), (Store.Status.ACTIVE, Store.Status.CONFIGURING))
        self.assertTrue(PlatformAuditEvent.objects.filter(action='store.first_opened', target_store_slug=store.slug).exists())
        self.assertEqual(self.client.post(url, {'confirm':True}, format='json').status_code, 409)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_expired_license_and_audit_failure_never_open(self):
        store = self.stores[0]
        self.setup_store(store)
        url = f'/api/admin/platform/store-management/{store.pk}/setup/readiness'
        from stores.models import StoreLicenseCode
        code = StoreLicenseCode.objects.get(store=store)
        code.ends_at = timezone.now()-timedelta(minutes=1); code.save(update_fields=['ends_at'])
        self.assertEqual(self.client.post(url, {'confirm':True}, format='json').status_code, 409)
        code.ends_at = timezone.now()+timedelta(days=1); code.save(update_fields=['ends_at'])
        with patch('stores.platform_store_setup.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit')):
            self.assertEqual(self.client.post(url, {'confirm':True}, format='json').status_code, 503)
        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.CONFIGURING)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_optional_room_requires_confirmed_timeout_and_enabled_completion(self):
        store = self.stores[0]
        self.setup_store(store)
        url = f'/api/admin/platform/store-management/{store.pk}/setup'
        with store_context(store):
            service = ServiceItem.objects.get(name='陪玩')
            service.room_enabled = True; service.save(update_fields=['room_enabled'])
        readiness = self.client.get(url+'/readiness').data['data']
        rows = {row['key']: row for row in readiness['checks']}
        self.assertEqual(rows['room_confirmation']['status'], 'BLOCKED')
        self.assertEqual(self.client.post(url+'/readiness', {'confirm':True}, format='json').status_code, 409)
        configured = self.client.put(url+'/room-confirmation', {'minutes': 60, 'version': 0}, format='json')
        self.assertEqual(configured.status_code, 200, configured.data)
        readiness = self.client.get(url+'/readiness').data['data']
        self.assertEqual(next(row for row in readiness['checks'] if row['key'] == 'room_confirmation')['status'], 'BLOCKED')
        evidence = {row['key']:row for row in self.client.get(url).data['data']['evidence']}
        response = self.client.post(url+'/confirm/room_confirmation',
            {'fingerprint': evidence['room_confirmation']['snapshot']['fingerprint']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        readiness = self.client.get(url+'/readiness').data['data']
        self.assertTrue(readiness['can_open'], readiness)
        with override_settings(ROOM_AUTO_CONFIRMATION_ENABLED=False):
            blocked = self.client.get(url+'/readiness').data['data']
            self.assertEqual(next(row for row in blocked['checks'] if row['key'] == 'room_completion')['status'], 'BLOCKED')
            self.assertEqual(self.client.post(url+'/readiness', {'confirm':True}, format='json').status_code, 409)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_optional_reservation_requires_confirmed_time_range(self):
        store = self.stores[0]
        self.setup_store(store)
        url = f'/api/admin/platform/store-management/{store.pk}/setup'
        self.assertTrue(self.client.get(url+'/readiness').data['data']['can_open'])
        configured = self.client.put(url+'/reservation-deadlines',
            {'confirmation_minutes':30, 'payment_minutes':60, 'version':0, 'slot_minutes':15,
             'min_duration_minutes':15, 'max_duration_minutes':720, 'max_advance_days':30}, format='json')
        self.assertEqual(configured.status_code, 200, configured.data)
        from orders.reservation_deadlines import current_rules
        with store_context(self.stores[1]):
            self.assertEqual(current_rules()['version'], 0)
        evidence = {row['key']:row for row in self.client.get(url).data['data']['evidence']}
        response = self.client.post(url+'/confirm/reservation_deadlines',
            {'fingerprint':evidence['reservation_deadlines']['snapshot']['fingerprint']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        rows = {row['key']:row for row in self.client.get(url+'/readiness').data['data']['checks']}
        self.assertEqual(rows['reservation_deadlines']['status'], 'READY')
        self.assertTrue(self.client.get(url+'/readiness').data['data']['can_open'])
        changed = self.client.put(url+'/reservation-deadlines',
            {'confirmation_minutes':30, 'payment_minutes':60, 'version':1, 'slot_minutes':30,
             'min_duration_minutes':30, 'max_duration_minutes':720, 'max_advance_days':7}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertFalse(self.client.get(url+'/readiness').data['data']['can_open'])

    @skipUnless(connection.vendor == 'mysql', 'requires isolated MySQL row locks')
    def test_setup_write_waits_for_the_same_store_opening_lock(self):
        from club_accounts.models import ClubAccount
        from club_accounts.tokens import issue_account_tokens
        from stores.models import StoreProvisioningClaim
        database = connection.settings_dict
        self.assertTrue(str(database['NAME']).startswith('test_xa_test_'))
        self.assertTrue(str(database['USER']).startswith('xa_test'))
        self.assertEqual(database['HOST'], '127.0.0.1')
        store = self.stores[0]
        self.setup_store(store)
        with store_context(store):
            owner = ClubAccount.objects.get(username='open-shenglang')
            owner.is_phone_verified = True; owner.save(update_fields=['is_phone_verified'])
            owner.admin_membership.roles.filter(code__startswith='foundation_').update(permissions=['order:view', 'order:timeout'])
            token = issue_account_tokens(owner, auth_method='password_sms')[0]
        StoreProvisioningClaim.objects.create(store=store, admin_username=owner.username, database_fingerprint='a'*64)
        started = Event()

        def change():
            close_old_connections()
            try:
                client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+token)
                started.set()
                return client.put('/api/admin/config/order-timeout/', {'minutes': 40, 'revision': 1}, format='json')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic(using='default'):
                Store.objects.select_for_update().get(pk=store.pk)
                future = executor.submit(change)
                self.assertTrue(started.wait(5))
                from time import monotonic, sleep
                observer = connections['default'].copy()
                try:
                    end = monotonic() + 8
                    waiting = False
                    while monotonic() < end:
                        with observer.cursor() as cursor:
                            cursor.execute('SHOW FULL PROCESSLIST')
                            waiting = any(row[3] == database['NAME'] and row[4] == 'Query'
                                and '`STORES_STORE`' in str(row[7] or '').upper()
                                and 'FOR UPDATE' in str(row[7] or '').upper() for row in cursor.fetchall())
                        if waiting:
                            break
                        if future.done():
                            self.fail(f'配置写入未等待门店锁: {future.result().status_code}')
                        sleep(.02)
                    self.assertTrue(waiting, '没有观察到门店行锁等待')
                finally:
                    observer.close()
                opened = self.client.post(f'/api/admin/platform/store-management/{store.pk}/setup/readiness',
                                          {'confirm': True}, format='json')
                self.assertEqual(opened.status_code, 200, opened.data)
            response = future.result(timeout=15)
        self.assertEqual(response.status_code, 200, response.data)
        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.ACTIVE)
        with store_context(store):
            self.assertEqual(OrderTimeoutConfig.objects.get(pk=1).minutes, 40)
