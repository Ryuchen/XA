"""Run fulfillment retry races against a new task-owned MySQL instance."""
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pymysql

root = Path(__file__).resolve().parent.parent
base = root / '.tools' / 'mysql-8.0.46-winx64'
work = Path(tempfile.mkdtemp(prefix='fulfillment-receipt-race-', dir=root / '.tools'))
data = work / 'data'
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
hidden = subprocess.CREATE_NO_WINDOW
with (work / 'initialize.log').open('w', encoding='utf-8') as out:
    subprocess.run([str(base / 'bin' / 'mysqld.exe'), '--no-defaults', '--initialize-insecure',
        '--console', f'--basedir={base.as_posix()}', f'--datadir={data.as_posix()}'],
        stdout=out, stderr=subprocess.STDOUT, check=True, timeout=120, creationflags=hidden)
with (work / 'server.log').open('w', encoding='utf-8') as log:
    server = subprocess.Popen([str(base / 'bin' / 'mysqld.exe'), '--no-defaults', '--console',
        f'--basedir={base.as_posix()}', f'--datadir={data.as_posix()}', '--bind-address=127.0.0.1',
        f'--port={port}', '--mysqlx=0', '--innodb-buffer-pool-size=64M', '--max-connections=40',
        '--performance-schema=OFF', '--innodb-lock-wait-timeout=8'],
        stdout=log, stderr=subprocess.STDOUT, creationflags=hidden)
    try:
        connection = None
        for _ in range(120):
            if server.poll() is not None:
                raise RuntimeError(f'Isolated MySQL exited; inspect {work / "server.log"}')
            try:
                connection = pymysql.connect(host='127.0.0.1', port=port, user='root', password='',
                                             connect_timeout=1, autocommit=True)
                break
            except pymysql.MySQLError:
                time.sleep(0.5)
        if connection is None:
            raise RuntimeError('Isolated MySQL did not start')
        password = secrets.token_hex(24)
        with connection.cursor() as cursor:
            cursor.execute("CREATE USER 'xa_aftersale_test'@'localhost' IDENTIFIED BY %s", (password,))
            cursor.execute('CREATE DATABASE xa_test_aftersale_race CHARACTER SET utf8mb4')
            cursor.execute("GRANT ALL PRIVILEGES ON xa_test_aftersale_race.* TO 'xa_aftersale_test'@'localhost'")
            cursor.execute("GRANT ALL PRIVILEGES ON test_xa_test_aftersale_race.* TO 'xa_aftersale_test'@'localhost'")
        connection.close()
        env = os.environ.copy()
        env.update({'DJANGO_SETTINGS_MODULE':'config.mysql_test_settings', 'XA_TEST_MYSQL_DATABASE':'xa_test_aftersale_race',
                    'XA_TEST_MYSQL_HOST':'127.0.0.1', 'XA_TEST_MYSQL_PORT':str(port),
                    'XA_TEST_MYSQL_USER':'xa_aftersale_test', 'XA_TEST_MYSQL_PASSWORD':password,
                    'PYTHONIOENCODING':'utf-8'})
        with (work / 'results.log').open('w', encoding='utf-8') as out:
            result = subprocess.run([str(root / '.venv' / 'Scripts' / 'python.exe'), 'manage.py', 'test',
                'orders.tests.test_fulfillment_receipts.FulfillmentReceiptTest', '--noinput'],
                cwd=root / 'services' / 'backend', env=env, stdout=out, stderr=subprocess.STDOUT,
                creationflags=hidden, timeout=240)
        print(f'Isolated MySQL race exit code: {result.returncode}; log: {work / "results.log"}', flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    finally:
        try:
            subprocess.run([str(base / 'bin' / 'mysqladmin.exe'), '--no-defaults', '--protocol=TCP',
                '--host=127.0.0.1', f'--port={port}', '--user=root', 'shutdown'], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=30, creationflags=hidden)
        except subprocess.TimeoutExpired:
            pass
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
