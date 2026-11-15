"""Platform super-admin creates an actual disposable SQLite tenant via the UI."""
from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest import skipUnless

from django.conf import settings
from django.db import connections
from django.test import LiveServerTestCase, override_settings
from stores.models import PlatformAccount, PlatformRoleGrant, Store, StoreProvisioningClaim
from stores.platform_auth import issue_tokens


@contextmanager
def setup_asgi():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    server_log = tempfile.TemporaryFile(mode='w+t', encoding='utf-8')
    process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
        cwd=settings.BASE_DIR, env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.platform_store_browser_settings'},
        stdout=server_log, stderr=server_log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError('isolated ASGI exited')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('isolated ASGI did not start')
        try:
            yield f'http://127.0.0.1:{port}'
        except Exception:
            server_log.seek(0)
            print('隔离测试服务日志：', server_log.read()[-12000:])
            raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=10)
        server_log.close()


@skipUnless(os.environ.get('XA_PLATFORM_STORE_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True)
class PlatformStoreBrowserTest(LiveServerTestCase):
    def test_create_store_in_platform_screen(self):
        account = PlatformAccount(username='platform-browser-admin', phone='13800001111', is_phone_verified=True)
        account.set_password('Synthetic-Platform-Password-2026!'); account.save()
        PlatformRoleGrant.objects.create(account=account, role='SUPER_ADMIN', scope_slug='*')
        token, refresh = issue_tokens(account, 'SMS', account.phone)
        target = Path(os.environ['XA_PLATFORM_STORE_TEST_DB']).resolve()
        self.assertFalse(target.exists(), 'Never overwrite an existing target database')
        try:
            with setup_asgi() as api:
                root = Path(settings.BASE_DIR).parent.parent
                try:
                    result = subprocess.run(['node', str(root / 'scripts/platform-store-browser.cjs')], cwd=root,
                        input=json.dumps({'api': api, 'token': token, 'refresh': refresh, 'id': str(account.pk),
                                          'username': account.username,
                                          'points_usage': os.environ.get('XA_PLATFORM_POINTS_BROWSER') == '1',
                                          'monthly_cutover': os.environ.get('XA_MONTHLY_CUTOVER_BROWSER') == '1'}), text=True, encoding='utf-8', capture_output=True, timeout=100)
                except subprocess.TimeoutExpired as exc:
                    raise AssertionError(f'平台页面测试超时，已完成阶段：{exc.stdout!r}') from None
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            store = Store.objects.get(slug='platform-browser-store')
            self.assertEqual(store.status, Store.Status.CONFIGURING)
            self.assertEqual(store.name, '页面测试新店已更新')
            self.assertEqual((store.entity_prefix, store.provider_name_prefix), ('PB', 'PX'))
            with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                if os.environ.get('XA_PLATFORM_POINTS_BROWSER') == '1':
                    terms = json.loads(db.execute("SELECT value FROM wallet_systemconfig WHERE key='points_redemption_terms'").fetchone()[0])
                    self.assertEqual((terms['group_point_hundredths'], terms['group_coin_units'], terms['max_ratio_basis_points']), (10025, 30, 4555))
                    switches = json.loads(db.execute("SELECT value FROM wallet_systemconfig WHERE key='points_usage_switches'").fetchone()[0])
                    self.assertFalse(switches['lottery_enabled'])
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM console_adminauditlog WHERE resource IN ('points-redemption-terms', 'points-usage-switches')").fetchone()[0], 2)
                if os.environ.get('XA_MONTHLY_CUTOVER_BROWSER') == '1':
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM coupons_vipmonthlycutover').fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM console_adminauditlog WHERE resource='vip-monthly-cutover'").fetchone()[0], 1)
                raw = db.execute("SELECT value FROM wallet_systemconfig WHERE key='provider_capacity_rules'").fetchone()
                self.assertEqual(json.loads(raw[0])['max_concurrent_orders'], 4)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM console_adminauditlog WHERE resource='store-setup-confirmation' AND object_id='capacity'").fetchone()[0], 1)
                clocks = json.loads(db.execute("SELECT value FROM wallet_systemconfig WHERE key='settlement_clock_rules'").fetchone()[0])
                self.assertEqual((clocks['aftersale_hours'], clocks['income_freeze_hours']), (48, 36))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM console_adminauditlog WHERE resource='store-setup-confirmation' AND object_id='settlement_clocks'").fetchone()[0], 1)
                targets = json.loads(db.execute("SELECT value FROM wallet_systemconfig WHERE key='provider_deposit_targets'").fetchone()[0])
                self.assertEqual((targets['cash_target'], targets['earnings_target']), (50000, 90000))
                self.assertEqual(db.execute("SELECT value FROM wallet_systemconfig WHERE key='provider_deposit_income_percent'").fetchone()[0], '55')
                self.assertEqual(db.execute("SELECT COUNT(*) FROM console_adminauditlog WHERE resource='store-setup-confirmation' AND object_id IN ('deposit_targets', 'deposit_percent')").fetchone()[0], 2)
                for key, value in [('min_withdraw_amount', '1000'), ('withdraw_balance_threshold', '5000'), ('withdraw_tax_rate', '3')]:
                    self.assertEqual(db.execute('SELECT value FROM wallet_systemconfig WHERE key=?', (key,)).fetchone()[0], value)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM console_adminauditlog WHERE resource='store-setup-confirmation' AND object_id='withdraw'").fetchone()[0], 1)
        finally:
            if target.exists():
                claim = StoreProvisioningClaim.objects.select_related('store').filter(store__slug='platform-browser-store').first()
                if claim is None:
                    raise RuntimeError(f'临时库归属未完成，保留供核对：{target}')
                with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                    rows = db.execute('SELECT id, token, store_id, slug FROM xa_store_provision_identity').fetchall()
                if rows != [(1, claim.token.hex, claim.store_id, 'platform-browser-store')]:
                    raise RuntimeError(f'临时库归属不匹配，保留供核对：{target}')
                connections['store_new'].close()
                target.unlink()
