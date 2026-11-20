"""SA14 regression with real tenant row locks and a severed control connection."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from select import select
import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.license_quota import LicenseAdmissionError, lock_category
from club_accounts.models import ClubAccount, LicenseQuotaLock
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from stores.db import transaction

from . import test_business_certificates as certificate_fixture
from .business_certificates import CERTIFICATE_TIMEZONE
from .context import resolve_store, store_context
from .license_codes import _activate_after_verified_store_auth, issue
from .models import Store
from .tests_foundation import admin


class _RelayConnection(BaseRequestHandler):
    def handle(self):
        if self.server.disconnected.is_set():
            return
        try:
            with socket.create_connection(self.server.upstream, timeout=2) as upstream:
                sockets = (self.request, upstream)
                while not self.server.disconnected.is_set():
                    readable, _, _ = select(sockets, (), (), .02)
                    for current in readable:
                        data = current.recv(65536)
                        if not data or self.server.disconnected.is_set():
                            return
                        peer = upstream if current is self.request else self.request
                        peer.sendall(data)
        except OSError:
            return


@contextmanager
def _control_link():
    """Route only the test control alias through a dedicated TCP relay."""
    control = connections['default']
    require_disposable_mysql(control)
    original_port = control.settings_dict['PORT']
    with ThreadingTCPServer(('127.0.0.1', 0), _RelayConnection) as relay:
        relay.upstream = ('127.0.0.1', int(original_port))
        relay.disconnected = Event()
        thread = Thread(target=relay.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        control.close()
        control.settings_dict['PORT'] = str(relay.server_address[1])
        try:
            yield relay.disconnected
        finally:
            try:
                control.close()
            finally:
                control.settings_dict['PORT'] = original_port
                relay.shutdown()
                thread.join(timeout=2)


@skipUnless(settings.SETTINGS_MODULE == 'config.certificate_mysql_test_settings',
            'Requires registered SA14 control and tenant MySQL databases')
@override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=True,
    LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True, LICENSE_SNAPSHOT_CACHE_ENABLED=True,
    LICENSE_NOTICE_DISPATCH_ENABLED=False, PLATFORM_IDENTITY_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    ORDINARY_SERVICE_FUNDING_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    PLATFORM_PASS_REPAYMENT_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False)
class CertificateAdmissionMySQLTests(TransactionTestCase):
    databases = {'default', 'store_a', 'store_b'}
    platform = certificate_fixture.BusinessCertificateTests.platform
    image = certificate_fixture.BusinessCertificateTests.image
    upload = certificate_fixture.BusinessCertificateTests.upload

    def setUp(self):
        for alias in self.databases:
            connection = connections[alias]
            require_disposable_mysql(connection)
            with connection.cursor() as cursor:
                cursor.execute('SELECT DATABASE()')
                self.assertEqual(cursor.fetchone()[0], connection.settings_dict['NAME'])
        self.now = timezone.now()
        self.actor, self.owner = self.platform('sa14-platform-owner', 'SUPER_ADMIN')
        access = AccessToken(self.owner.defaults['HTTP_AUTHORIZATION'].split()[1])
        self.stores, self.certificate_ids = {}, {}
        for slug, prefix, alias, expiry in (
            ('shenglang', 'SL', 'store_a', timezone.localdate(self.now, CERTIFICATE_TIMEZONE)),
            ('xinan', 'XA', 'store_b', None),
        ):
            store, _ = Store.objects.update_or_create(slug=slug, defaults={'database_alias': alias,
                'name': slug, 'entity_prefix': prefix, 'status': Store.Status.ACTIVE, 'initialized_at': self.now})
            self.stores[slug] = store
            with store_context(store):
                operator = admin(f'{slug}-sa14-admin', super_admin=True)
            _, code = issue(access, store_slug=slug, tier='PRO', kind='INITIAL',
                starts_at=self.now - timedelta(days=1), ends_at=self.now + timedelta(days=30), backdate_reason='隔离验收')
            _activate_after_verified_store_auth(store=store, actor_id=operator.pk, code=code)
            self.certificates = f'/api/admin/platform/store-management/{store.pk}/business-certificates'
            registered = self.upload(expires=expiry.isoformat() if expiry else '')
            self.assertEqual(registered.status_code, 201, registered.data)
            self.certificate_ids[slug] = registered.data['data']['id']

    def test_blocked_account_creation_rechecks_midnight_without_locking_the_other_store(self):
        left, right = self.stores['shenglang'], self.stores['xinan']
        attempted = Event()
        current = self.now

        def register(store):
            close_old_connections()
            try:
                with store_context(store):
                    if store == left:
                        attempted.set()
                    return ClubAccount.objects.create_account(username='same-boss-name', account_type='BOSS')
            finally:
                connections.close_all()

        def await_lock():
            self.assertTrue(attempted.wait(5))
            table = LicenseQuotaLock._meta.db_table
            until = monotonic() + 5
            while not mysql_locking_statements(connections[left.database_alias], table):
                self.assertLess(monotonic(), until, 'registration never waited on the InnoDB row')
                sleep(.02)

        with patch('django.utils.timezone.now', side_effect=lambda: current), ThreadPoolExecutor(2) as pool:
            with store_context(left), transaction.atomic():
                before = lock_category('BOSS').active_count
                waiting = pool.submit(register, left)
                await_lock()
                tomorrow = timezone.localdate(self.now, CERTIFICATE_TIMEZONE) + timedelta(days=1)
                current = datetime.combine(tomorrow, time.min, CERTIFICATE_TIMEZONE)
                independent = pool.submit(register, right).result(timeout=5)
                self.assertEqual(independent._state.db, right.database_alias)
                self.assertFalse(waiting.done())
            with self.assertRaisesMessage(LicenseAdmissionError, '证照已过期'):
                waiting.result(timeout=5)
            with store_context(left):
                self.assertEqual(LicenseQuotaLock.objects.get(account_type='BOSS').active_count, before)
                self.assertFalse(ClubAccount.objects.filter(username='same-boss-name').exists())
                attempted.clear()
                with transaction.atomic():
                    lock_category('BOSS')
                    waiting = pool.submit(register, left)
                    await_lock()
                    self.certificates = f'/api/admin/platform/store-management/{left.pk}/business-certificates'
                    renewed = self.upload(expected=self.certificate_ids[left.slug])
                    self.assertEqual(renewed.status_code, 201, renewed.data)
                    self.assertFalse(waiting.done())
                admitted = waiting.result(timeout=5)
                self.assertEqual(admitted._state.db, left.database_alias)
                self.assertEqual(LicenseQuotaLock.objects.get(account_type='BOSS').active_count, before + 1)
        with store_context(right):
            self.assertEqual(ClubAccount.objects.get(username='same-boss-name').pk, independent.pk)
        print(f'SA14 InnoDB {left.slug} 拒绝过期后开户、换证后开户={admitted.pk}，'
              f'{right.slug} 持锁窗口内开户={independent.pk}', flush=True)

    def _prepare_business(self, store, host, amount):
        from coupons.models import Coupon
        from engagement.models import KookBinding, KookPointsConfig, KookOnlineRuleVersion
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        from .license_snapshot import business_status

        store = resolve_store(store.slug)
        with store_context(store):
            ServiceFundingApiTest.setUp(self)
            coupon = Coupon.objects.create(name='到期前已有券', discount_type='DIRECT', amount=amount)
            other_coupon = Coupon.objects.create(name='故障期间的新券', discount_type='DIRECT', amount=50)
            headers = {'HTTP_HOST': host, 'HTTP_AUTHORIZATION': 'Bearer ' + issue_account_tokens(self.account)[0]}
            boss = APIClient(**headers)
            player = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider)[0])
            claim_url = f'/api/coupons/{coupon.pk}/claim/'
            claim = boss.post(claim_url, {}, format='json')
            self.assertEqual(claim.status_code, 200, claim.data)
            owned_id = claim.data['data']['id']
            payload = ServiceFundingApiTest.payload(self, user_coupon_id=owned_id)
            self.assertEqual(business_status(store)['license_source'], 'ONLINE')
            voice_start = timezone.now() + timedelta(seconds=1)
            KookPointsConfig.objects.create(enabled=True, guild_id='fixture-guild', checkin_channel_id='fixture-text',
                voice_channel_ids=['fixture-voice'], started_at=voice_start)
            KookOnlineRuleVersion.objects.get_or_create(pk=0, defaults={'window_ms': 7200000, 'points': 1})
            binding = KookBinding.objects.create(account=self.account, user_id='fixture-voice-user',
                channel_id='fixture-voice', accounted_at=voice_start, online_milliseconds=7190000,
                business_cycle=self.account.business_cycle)
        return SimpleNamespace(store=store, boss=boss, player=player, payload=payload,
            debit=1000 - amount, binding=binding, at=voice_start + timedelta(seconds=20), headers=headers,
            claim_url=claim_url, other_coupon_id=other_coupon.pk, owned_id=owned_id)

    def test_physical_control_disconnect_keeps_both_stores_operating_and_restores_certificate_review(self):
        from console.models import AdminAuditLog
        from coupons.models import UserCoupon
        from engagement.kook_points import checkpoint
        from engagement.models import KookOnlineInterval, PointLedger
        from orders.models import Order
        from wallet.models import Transaction
        from .license_benefits import check_new_benefits
        from .license_snapshot import business_status

        cases = [
            self._prepare_business(self.stores['shenglang'], 'sl.esports.club', 100),
            self._prepare_business(self.stores['xinan'], 'xa.esports.club', 200),
        ]
        with _control_link() as disconnected:
            control = connections['default']
            with control.cursor() as cursor:
                cursor.execute('SELECT DATABASE(), CONNECTION_ID()')
                schema, original_connection = cursor.fetchone()
                self.assertEqual(schema, control.settings_dict['NAME'])
                disconnected.set()
                with self.assertRaises(DatabaseError) as old:
                    cursor.execute('SELECT 1')
            self.assertIn(old.exception.args[0], (2006, 2013))
            # Reuse the stale connection first, to exercise the driver's
            # InterfaceError on the normal wallet request.
            self.assertEqual(cases[0].boss.get('/api/wallet/info/').status_code, 200)
            control.close()
            with self.assertRaises(DatabaseError) as new:
                with control.cursor() as cursor:
                    cursor.execute('SELECT 1')
            self.assertIn(new.exception.args[0], (2003, 2006, 2013))
            print(f'SA14 控制 TCP 会话断开={old.exception.args[0]}，新连接失败={new.exception.args[0]}', flush=True)
            for case in cases:
                self.assertEqual(case.boss.get('/api/wallet/info/').status_code, 200)
                repeat = case.boss.post(case.claim_url, {}, format='json')
                self.assertEqual((repeat.data['data']['id'], repeat.data['data']['replayed']), (case.owned_id, True))
                denied = case.boss.post(f'/api/coupons/{case.other_coupon_id}/claim/', {}, format='json')
                self.assertEqual(denied.status_code, 503, denied.data)
                result = case.boss.post('/api/orders/create/', case.payload, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                case.order_id = result.data['data']['id']
                started = case.player.post(f'/api/orders/orders/{case.order_id}/start/', {}, format='json')
                self.assertEqual(started.data['code'], 0, started.data)
                with store_context(case.store):
                    self.assertEqual(business_status(case.store)['license_source'], 'CACHE')
                    outcome, evidence = check_new_benefits('PROMOTIONS')
                    self.assertEqual((outcome, evidence['business_certificate']['status']),
                                     ('UNKNOWN', 'CONTROL_UNAVAILABLE'))
                    before = LicenseQuotaLock.objects.get(account_type='BOSS').active_count
                    with self.assertRaises(LicenseAdmissionError) as admission:
                        ClubAccount.objects.create_account(username='during-control-break', account_type='BOSS')
                    self.assertEqual(admission.exception.status_code, 503)
                    self.assertEqual(LicenseQuotaLock.objects.get(account_type='BOSS').active_count, before)
                    self.assertFalse(ClubAccount.objects.filter(username='during-control-break').exists())
                    self.assertFalse(UserCoupon.objects.filter(coupon_id=case.other_coupon_id).exists())
                    order = Order.objects.get(pk=case.order_id)
                    self.assertEqual((order.amount, order.status), (case.debit, 'IN_SERVICE'))
                    tx = Transaction.objects.get(order=order, tx_type='PAY')
                    self.assertEqual((tx.balance_before, tx.amount, tx.balance_after),
                                     (1700, -case.debit, 1700 - case.debit))
                    self.assertTrue(checkpoint(case.at))
                    interval = KookOnlineInterval.objects.get(binding=case.binding)
                    self.assertEqual((interval.processing_status, interval.elapsed_ms), ('WAITING', 20000))
                    self.assertFalse(PointLedger.objects.exists())
                    case.source_id = AdminAuditLog.objects.get(pk=interval.decision_source_id).pk
            self.assertEqual(cases[0].order_id, cases[1].order_id)
            mismatched = APIClient(**{**cases[0].headers, 'HTTP_HOST': 'xa.esports.club'})
            self.assertIn(mismatched.get('/api/wallet/info/').status_code, (401, 403))
            disconnected.clear()
            close_old_connections()
            with control.cursor() as cursor:
                cursor.execute('SELECT DATABASE(), CONNECTION_ID()')
                schema, recovered_connection = cursor.fetchone()
                self.assertEqual(schema, control.settings_dict['NAME'])
                self.assertNotEqual(original_connection, recovered_connection)
            for case in cases:
                with store_context(case.store):
                    self.assertEqual(business_status(case.store)['license_source'], 'ONLINE')
                claim = case.boss.post(f'/api/coupons/{case.other_coupon_id}/claim/', {}, format='json')
                self.assertEqual((claim.status_code, claim.data['code']), (200, 0))
                replay = case.boss.post('/api/orders/create/', case.payload, format='json')
                self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
                self.assertEqual(replay.data['fundResult']['data']['id'], case.order_id)
                url = f'/api/admin/platform/store-management/{case.store.pk}/license-kook-review/{case.source_id}/resolve'
                body = {'request_id': str(uuid4()), 'outcome': 'ALLOW', 'reason': '隔离控制连接恢复后核对原证照'}
                reviewed = self.owner.post(url, body, format='json')
                self.assertEqual(reviewed.status_code, 200, reviewed.data)
                result = reviewed.data['data']
                self.assertEqual((result['credited_milliseconds'], result['awarded_points_hundredths']), (20000, 100))
                self.assertEqual(self.owner.post(url, body, format='json').data, reviewed.data)
                with store_context(case.store):
                    self.assertEqual(PointLedger.objects.get(kind='KOOK_LICENSE_REVIEW').amount, 100)
                    self.assertEqual(Transaction.objects.filter(order_id=case.order_id, tx_type='PAY').count(), 1)
                print(f'SA14 {case.store.slug} 订单={case.order_id} 实扣={case.debit} '
                      f'在线原事件={case.source_id} 核对请求={body["request_id"]} 补记毫秒=20000 补发=100',
                      flush=True)
