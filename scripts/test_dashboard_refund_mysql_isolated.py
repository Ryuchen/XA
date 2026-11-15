"""Run an opted-in business journey on a disposable MySQL 8 container."""
import os
import secrets
import shlex
import socket
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
labels = os.environ.get('XA_MYSQL_TEST_LABEL', 'console.test_dashboard_refund_real_journey').split(',')
root_var = 'XA_ADMIN_VUE_ROOT' if any(label.startswith('console.') for label in labels) else 'XA_PROVIDER_VUE_ROOT'
frontend_root = os.environ.get(root_var)
if not frontend_root or not (Path(frontend_root) / 'index.html').is_file():
    raise SystemExit(f'Set {root_var} to the current built frontend directory')
name = 'xa-refund-day-' + secrets.token_hex(6)
password = secrets.token_hex(24)
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
wsl = ['wsl.exe', '-d', 'XA-Ubuntu-24.04', '-u', 'root', '--']
def docker(*args, **kwargs):
    command = shlex.join(['docker', *args])
    return subprocess.run([*wsl, 'bash', '-s'], input=command, text=True, check=True, **kwargs)

try:
    docker('run', '-d', '--name', name, '-e', f'MYSQL_ROOT_PASSWORD={password}',
           '-e', 'MYSQL_DATABASE=xa_test_refund_day', '-e', 'MYSQL_USER=xa_refund',
           '-e', f'MYSQL_PASSWORD={password}', '-p', f'127.0.0.1:{port}:3306',
           'mysql:8.0', stdout=subprocess.DEVNULL, timeout=90)
    # docker's mysqladmin readiness retry stays inside the isolated container.
    docker('exec', name, 'sh', '-c',
           'for i in $(seq 1 90); do MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h127.0.0.1 -uroot -Nse "SELECT 1" >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1',
           timeout=110)
    docker('exec', name, 'sh', '-c',
           'mysql_tzinfo_to_sql /usr/share/zoneinfo 2>/dev/null | MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot mysql && '
           "MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysql -uroot -e \"GRANT ALL PRIVILEGES ON test_xa_test_refund_day.* TO 'xa_refund'@'%'\"",
           timeout=90)
    if os.environ.get('XA_MYSQL_GRANT_PROCESS') == '1':
        # Some lock-race tests inspect SHOW ENGINE INNODB STATUS. This grant is
        # limited to the disposable container, never the preview database.
        docker('exec', name, 'sh', '-c',
               "MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysql -uroot -e \"GRANT PROCESS ON *.* TO 'xa_refund'@'%'\"",
               timeout=30)
    env = os.environ.copy()
    env.update({'DJANGO_SETTINGS_MODULE': 'config.container_test_settings',
                'XA_TEST_MYSQL_DATABASE': 'xa_test_refund_day', 'XA_TEST_MYSQL_HOST': '127.0.0.1',
                'XA_TEST_MYSQL_PORT': str(port), 'XA_TEST_MYSQL_USER': 'xa_refund',
                'XA_TEST_MYSQL_PASSWORD': password, 'PYTHONIOENCODING': 'utf-8',
                root_var: frontend_root})
    result = subprocess.run([str(root / '.venv/Scripts/python.exe'), 'manage.py', 'test',
        *labels, '--noinput', '--verbosity', '1'],
        cwd=root / 'services/backend', env=env, timeout=600)
    raise SystemExit(result.returncode)
finally:
    subprocess.run([*wsl, 'docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=40, check=False)
