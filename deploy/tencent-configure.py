"""Prepare domain deployment credentials from existing values; print no secrets."""
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path('/opt/xa-upgrade-20260928')
OUT = Path('/etc/xa/release.env')
assert os.geteuid() == 0
ROOT.mkdir(mode=0o700, exist_ok=True)
OUT.parent.mkdir(mode=0o700, exist_ok=True)
if OUT.exists():
    raise SystemExit('release.env already exists; preserve it and verify before retrying')


def inspect_env(name):
    obj = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
    return dict(line.split('=', 1) for line in obj['Config']['Env'])


db = inspect_env('xa-db-1')
web = inspect_env('xa-web-1')
wechat = dict(line.strip().split('=', 1) for line in (ROOT/'wechat.env').read_text().splitlines()
              if line.strip() and not line.lstrip().startswith('#') and '=' in line)
assert wechat.get('WECHAT_APPID') == 'wxd63488bdf6491c90'
assert len(wechat.get('WECHAT_SECRET', '')) >= 16
password = secrets.token_hex(32)
username = 'xa_app_20260928'
sql = f"CREATE USER '{username}'@'%' IDENTIFIED BY '{password}';\nGRANT ALL PRIVILEGES ON `xa_db`.* TO '{username}'@'%';\n"
subprocess.run(['docker', 'exec', '-i', 'xa-db-1', 'sh', '-c',
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'],
               input=sql.encode(), check=True)


def image(name):
    return subprocess.check_output(['docker', 'inspect', name, '--format', '{{.Image}}'], text=True).strip()


def digest(tag):
    return subprocess.check_output(['docker', 'image', 'inspect', tag, '--format', '{{index .RepoDigests 0}}'], text=True).strip()


env = {
    'XA_SERVER_IP': '43.167.213.74',
    'XA_ROOT_DOMAIN': 'xaesports.club',
    'XA_API_DOMAIN': 'api.xaesports.club',
    'XA_ADMIN_DOMAIN': 'admin.xaesports.club',
    'XA_PROVIDER_DOMAIN': 'provider.xaesports.club',
    'XA_PUBLIC_ORIGIN': 'https://api.xaesports.club',
    'DJANGO_ALLOWED_HOSTS': '43.167.213.74,localhost,127.0.0.1,api.xaesports.club,admin.xaesports.club,provider.xaesports.club',
    'CORS_ALLOWED_ORIGINS': 'https://admin.xaesports.club,https://provider.xaesports.club',
    'DJANGO_SECRET_KEY': web['DJANGO_SECRET_KEY'],
    'MYSQL_ROOT_PASSWORD': db['MYSQL_ROOT_PASSWORD'],
    'MYSQL_USER': username, 'MYSQL_PASSWORD': password,
    'WECHAT_APPID': wechat['WECHAT_APPID'], 'WECHAT_SECRET': wechat['WECHAT_SECRET'],
    'XA_BACKEND_IMAGE': subprocess.check_output(['docker', 'image', 'inspect', 'xa-backend:release-20260928', '--format', '{{.Id}}'], text=True).strip(),
    'XA_MYSQL_IMAGE': image('xa-db-1'), 'XA_REDIS_IMAGE': image('xa-redis-1'),
    'XA_NGINX_IMAGE': digest('nginx:alpine'), 'XA_CADDY_IMAGE': digest('caddy:2-alpine'),
}
assert len(env['DJANGO_SECRET_KEY']) >= 32
assert all('\n' not in value and '\r' not in value and "'" not in value for value in env.values())
OUT.write_text('\n'.join(f"{key}='{value}'" for key, value in env.items()) + '\n')
OUT.chmod(0o600)
print('Production environment prepared; mock login off, restricted database user, existing credentials and volumes preserved.')
