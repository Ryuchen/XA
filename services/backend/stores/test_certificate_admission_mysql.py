"""SA14 regression with real tenant row locks and control database outages."""
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


@contextmanager
def _stop_registered_control():
    """Suspend only the independently registered, disposable control daemon."""
    from pathlib import Path
    import shutil
    import subprocess

    import pymysql

    control = connections['default']
    require_disposable_mysql(control)
    spec = settings.SA14_TEST_CONTROL_INSTANCE
    port = spec['port']
    datadir = spec['datadir'].resolve()
    if (str(port) != str(control.settings_dict['PORT'])
            or any(str(port) == str(connections[name].settings_dict['PORT']) for name in ('store_a', 'store_b'))):
        raise AssertionError('只允许停止独立登记的控制 MySQL')
    pidfile = datadir.parent / 'mysql-control.pid'
    executable = shutil.which('mysqld')
    if not executable:
        raise AssertionError('停机前须确认本机 mysqld 可用于恢复登记控制实例')
    mysqld = Path(executable).resolve()

    def connect():
        return pymysql.connect(unix_socket=spec['socket'], user='root', password=spec['root_password'],
                               connect_timeout=1, read_timeout=2, autocommit=True)

    def identity(cursor):
        cursor.execute('SELECT @@socket, @@datadir, @@port, @@pid_file, @@server_uuid')
        endpoint, directory, observed_port, observed_pidfile, server_id = cursor.fetchone()
        if (Path(endpoint).resolve() != Path(spec['socket']).resolve() or Path(directory).resolve() != datadir
                or observed_port != port or Path(observed_pidfile).resolve() != pidfile):
            raise AssertionError('控制实例的 socket、目录、端口或 PID 文件与登记不符')
        return server_id

    with connect() as db, db.cursor() as cursor:
        server_id = identity(cursor)
    recovery_needed = False
    process = None

    def tcp_refused():
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return False
        except ConnectionRefusedError:
            return True

    def stop():
        nonlocal recovery_needed
        # The test driver starts a new request when probing the two tenant
        # databases. Worker connections remain under Celery's own lifecycle.
        connections.close_all()
        with connect() as db, db.cursor() as cursor:
            if identity(cursor) != server_id:
                raise AssertionError('控制实例身份改变，拒绝停止')
            recovery_needed = True
            try:
                cursor.execute('SHUTDOWN')
            except pymysql.err.OperationalError as exc:
                # The daemon can close the connection before the acknowledgement.
                if exc.args[0] not in (2006, 2013):
                    raise
        deadline = monotonic() + 10
        while pidfile.exists() and monotonic() < deadline:
            sleep(.05)
        if pidfile.exists():
            raise AssertionError('登记控制进程尚未退出')
        if not tcp_refused():
            raise AssertionError('停机期间控制库仍可建立 TCP 连接')
        print(f'SA14 控制 MySQL 已实际退出 port={port}', flush=True)

    def restart():
        nonlocal recovery_needed, process
        if not recovery_needed:
            return
        deadline = monotonic() + 15
        started = False
        while monotonic() < deadline:
            if (not started and (process is None or process.poll() is not None)
                    and not pidfile.exists() and tcp_refused()):
                with (datadir.parent / 'mysql-control.log').open('a') as log:
                    process = subprocess.Popen([mysqld, '--no-defaults', f'--datadir={datadir}',
                        '--bind-address=127.0.0.1', f'--port={port}', f'--socket={spec["socket"]}',
                        f'--pid-file={pidfile}', '--mysqlx=0', '--innodb-buffer-pool-size=64M',
                        '--innodb-lock-wait-timeout=8', '--skip-log-bin'],
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                started = True
            try:
                with connect() as db, db.cursor() as cursor:
                    if identity(cursor) != server_id:
                        raise AssertionError('恢复后的控制库 UUID 与原登记不符')
            except pymysql.MySQLError:
                if started and process.poll() is not None:
                    break
                sleep(.1)
                continue
            recovery_needed = False
            control.close()
            print(f'SA14 控制 MySQL 已恢复 port={port} uuid={server_id}', flush=True)
            return
        raise AssertionError('登记控制 MySQL 未恢复，请查 mysql-control.log')

    try:
        yield SimpleNamespace(set=stop, clear=restart)
    finally:
        restart()


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

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_periodic_worker_settles_both_stores_during_control_disconnect(self):
        self._verify_periodic_settlement()

    @skipUnless(getattr(settings, 'SA14_TEST_CONTROL_INSTANCE', None) and getattr(settings, 'SA14_TEST_REDIS', None),
                'Requires a separate registered control daemon and Redis broker')
    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_beat_and_redis_settle_both_stores_when_control_instance_stops(self):
        self._verify_periodic_settlement(standalone=True)

    def _verify_periodic_settlement(self, *, standalone=False):
        from tempfile import TemporaryDirectory

        from celery import Celery, current_app
        from celery.contrib.testing.worker import start_worker
        from django.core.files.uploadedfile import SimpleUploadedFile
        from kombu import Exchange, Queue

        from club_accounts.services import get_or_create_account_for_legacy_user
        from engagement import tasks as consumption_tasks
        from engagement.models import LicenseConsumptionPointsDecision, LicenseRefundVipDecision, PointLedger
        from orders.models import Order
        from orders.tests.factories import make_console_user
        from wallet.models import ProviderReport, Transaction
        from .context import current_store

        media = self.enterContext(TemporaryDirectory(prefix='xa-sa14-worker-'))
        self.enterContext(override_settings(MEDIA_ROOT=media))

        def report_image():
            return SimpleUploadedFile('evidence.png', self.image(), content_type='image/png')

        def accepted(response):
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')

        left = self.stores['shenglang']
        self.certificates = f'/api/admin/platform/store-management/{left.pk}/business-certificates'
        renewed = self.upload(expected=self.certificate_ids[left.slug])
        self.assertEqual(renewed.status_code, 201, renewed.data)
        cases = [
            self._prepare_business(left, 'sl.esports.club', 100),
            self._prepare_business(self.stores['xinan'], 'xa.esports.club', 200),
        ]
        for case in cases:
            paid = accepted(case.boss.post('/api/orders/create/', case.payload, format='json'))
            case.order_id = paid['id']
            path = f'/api/orders/orders/{case.order_id}/'
            accepted(case.player.post(path + 'start/', {'entry_image': report_image()}, format='multipart'))
            accepted(case.player.post(path + 'complete/', {'completion_image': report_image()}, format='multipart'))
            with store_context(case.store):
                report = ProviderReport.objects.get(order_id=case.order_id)
                staff = make_console_user(['report:view', 'report:audit'])
                reviewer = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = reviewer
                staff.admin_membership.save(update_fields=['account'])
                admin_client = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer)[0])
            path = f'/api/wallet/reports/{report.pk}/'
            accepted(case.player.post(path + 'images/', {'kind': 'RESULT', 'image': report_image()}, format='multipart'))
            accepted(case.player.post(path + 'submit/', {}, format='json'))
            accepted(admin_client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
            with store_context(case.store):
                order = Order.objects.get(pk=case.order_id)
                self.assertEqual(order.status, 'COMPLETED')
                self.assertIsNone(order.settled_at)
                self.assertFalse(order.transactions.filter(amount__gt=0).exists())
        self.assertEqual(cases[0].order_id, cases[1].order_id)

        queue = f'xa_sa14_worker_{uuid4().hex}'
        previous_app = current_app._get_current_object()
        app = Celery(queue, task_cls='stores.tasks:StoreTask', set_as_current=False)
        broker_url, backend_url = 'memory://', 'cache+memory://'
        if standalone:
            from urllib.parse import quote
            import redis

            broker = settings.SA14_TEST_REDIS
            port = broker['port']
            with redis.Redis(host='127.0.0.1', port=port, password=broker['password'],
                             socket_connect_timeout=2, socket_timeout=2) as client:
                server = client.info('server')
                self.assertEqual((server['process_id'], server['tcp_port']), (broker['pid'], port))
            base = f'redis://:{quote(broker["password"], safe="")}@127.0.0.1:{port}'
            broker_url, backend_url = f'{base}/0', f'{base}/1'
        app.conf.update(broker_url=broker_url, result_backend=backend_url, task_default_queue=queue,
            task_default_exchange=queue, task_default_routing_key=queue,
            task_queues=(Queue(queue, Exchange(queue), routing_key=queue),),
            task_always_eager=False, task_ignore_result=False,
            broker_transport_options={'global_keyprefix': queue + ':'},
            result_backend_transport_options={'global_keyprefix': queue + ':'},
            worker_enable_remote_control=False, worker_hijack_root_logger=False)
        try:
            # Celery's environment overrides take precedence over app.conf.
            self.assertTrue(all(url == broker_url for url in (
                app.conf.broker_url, app.conf.broker_read_url, app.conf.broker_write_url,
            )) and app.conf.result_backend == backend_url, 'Celery 地址与隔离测试登记不符')
            app.set_current()
            app.finalize()
            scan = app.tasks[consumption_tasks.confirm_due_consumption.name]
            recovered = timezone.now() + timedelta(hours=80)

            def run_scan(*, fallback, slugs=('shenglang', 'xinan')):
                self.assertIsNone(current_store())
                if standalone:
                    from celery.beat import ScheduleEntry, Scheduler

                    spec = settings.CELERY_BEAT_SCHEDULE['confirm-member-consumption']
                    self.assertEqual(spec['task'], scan.name)
                    identifier = str(uuid4())
                    entry = ScheduleEntry(name='confirm-member-consumption', **{**spec,
                        'options': {**spec.get('options', {}), 'task_id': identifier}},
                        last_run_at=app.now() - timedelta(seconds=spec['schedule'] + 1), app=app)
                    Scheduler(app=app, schedule={entry.name: entry}, lazy=True).tick()
                    result = app.AsyncResult(identifier)
                else:
                    result = scan.delay()
                fanout = result.get(timeout=15, disable_sync_subtasks=False)
                self.assertEqual(fanout['directory_fallback'], fallback)
                self.assertEqual(fanout['enqueue_unknown'], [])
                self.assertCountEqual([job['store'] for job in fanout['stores']], slugs)
                for job in fanout['stores']:
                    app.AsyncResult(job['task_id']).get(timeout=15, disable_sync_subtasks=False)
                return result.id

            def ledger(case):
                with store_context(case.store):
                    order = Order.objects.get(pk=case.order_id)
                    self.assertIsNotNone(order.settled_at)
                    self.assertIsNotNone(order.funding_benefits_resolved_at)
                    vip = LicenseRefundVipDecision.objects.get(order=order)
                    points = LicenseConsumptionPointsDecision.objects.get(order=order)
                    self.assertEqual((vip.outcome, points.outcome), ('UNKNOWN', 'UNKNOWN'))
                    self.assertEqual(points.license_evidence['vip_decision_id'], vip.pk)
                    self.assertEqual(points.license_evidence['points_outcome'], 'UNKNOWN')
                    for evidence in (vip.license_evidence, points.license_evidence['points_evidence']):
                        self.assertEqual(evidence['license_source'], 'CACHE')
                        self.assertEqual(evidence['business_certificate']['status'], 'CONTROL_UNAVAILABLE')
                    self.assertFalse(PointLedger.objects.filter(order=order).exists())
                    entries = list(order.transactions.order_by('pk').values_list('tx_type', 'amount'))
                    self.assertCountEqual(entries, [(Transaction.TxType.PAY, -case.debit),
                        (Transaction.TxType.INCOME, order.provider_income),
                        (Transaction.TxType.SHOP_INCOME, order.shop_income)])
                    self.assertEqual(sum(amount for _, amount in entries), 0)
                    return order.settled_at, vip.pk, points.pk, entries

            control_outage = _stop_registered_control if standalone else _control_link
            with control_outage() as disconnected, patch('django.utils.timezone.now', return_value=recovered):
                disconnected.set()
                if standalone:
                    for case in cases:
                        tenant = connections[case.store.database_alias]
                        with tenant.cursor() as cursor:
                            cursor.execute('SELECT DATABASE(), @@port')
                            self.assertEqual(cursor.fetchone(), (tenant.settings_dict['NAME'],
                                                                 int(tenant.settings_dict['PORT'])))
                with start_worker(app, pool='solo', queues=[queue], perform_ping_check=False, without_heartbeat=True):
                    original = run_scan(fallback=True)
                    first = [ledger(case) for case in cases]
                    run_scan(fallback=True)
                    self.assertEqual([ledger(case) for case in cases], first)
                    disconnected.clear()
                    run_scan(fallback=False)
                    self.assertEqual([ledger(case) for case in cases], first)
                    left.refresh_from_db()
                    disabled = self.owner.patch(f'/api/admin/platform/store-management/{left.pk}', {
                        'status': 'DISABLED', 'expected_updated_at': left.updated_at.isoformat(),
                    }, format='json')
                    self.assertEqual(disabled.status_code, 200, disabled.data)
                    disconnected.set()
                    run_scan(fallback=True, slugs=('xinan',))
                    self.assertEqual([ledger(case) for case in cases], first)
            for case, (_, vip_id, points_id, entries) in zip(cases, first):
                print(f'SA14 Celery 扫描={original} 门店={case.store.slug} 订单={case.order_id} '
                      f'流水={entries} VIP待核={vip_id} 积分待核={points_id}', flush=True)
        finally:
            app.close()
            previous_app.set_current()
