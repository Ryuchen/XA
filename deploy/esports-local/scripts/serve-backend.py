"""Loopback development backend over the existing, isolated two-store databases.

Private runtime credentials and external-login proofs remain in .tools. This
runner never creates databases, applies migrations, or seeds business records.
Only the explicitly isolated WeChat/SMS transports are substituted.
"""
import argparse
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[3]
PRIVATE = ROOT / '.tools' / 'fronted-acceptance'
EXPECTED_DATABASES = {
    'default': 'xa_test_fronted_acceptance_20261104',
    'store_b': 'xa_test_store_preview_b_20261104',
}
EXPECTED_STORES = {'shenglang': 'default', 'xinan': 'store_b'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def configure(port):
    require((PRIVATE / 'runtime.private.json').is_file(),
            'Missing .tools/fronted-acceptance/runtime.private.json; no bootstrap was run.')
    sys.path.insert(0, str(ROOT / 'services' / 'backend'))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
    import django
    django.setup()
    from django.conf import settings
    settings.MEDIA_BASE_URL = f'http://127.0.0.1:{port}'
    return settings


def check(settings, port):
    """Read configuration, SELECT from MySQL, and PING Redis; never repair state."""
    from django.db import connections
    from redis import Redis
    from stores.models import Store

    require(set(settings.DATABASES) == set(EXPECTED_DATABASES),
            'Expected exactly the two dedicated development database aliases.')
    for alias, expected_name in EXPECTED_DATABASES.items():
        config = settings.DATABASES[alias]
        require(config['ENGINE'] == 'django.db.backends.mysql'
                and config['NAME'] == expected_name
                and config['HOST'] == '127.0.0.1'
                and str(config['PORT']) == '13306',
                f'Isolated database boundary mismatch: {alias}.')
    require(settings.LOGIN_OTP_REQUIRED and not settings.LEGACY_FORCE_AUTH_COMPAT
            and settings.ORDER_SETTLEMENT_MODE == 'REPORT_APPROVAL'
            and settings.REQUIRE_ORDER_EVIDENCE_IMAGES and settings.REQUIRE_RECHARGE_PROOF,
            'Authentication or settlement safeguards do not match development settings.')
    require(not settings.KOOK_ENABLED and not settings.WECOM_DISPATCH_ENABLED
            and not settings.WECHAT_MOCK_LOGIN and not settings.CELERY_TASK_ALWAYS_EAGER
            and not settings.CELERY_BEAT_SCHEDULE,
            'External delivery or scheduled tasks must remain disabled.')
    require(settings.STORE_HOST_BINDINGS == {
        'sl.esports.club': 'shenglang', 'xa.esports.club': 'xinan',
    } and settings.ADMIN_STORE_HOST == 'admin.esports.club',
            'Store/admin Host bindings do not match this environment.')
    require(set(settings.ALLOWED_HOSTS) == {
        '127.0.0.1', 'localhost', 'testserver', 'sl.esports.club',
        'xa.esports.club', 'admin.esports.club',
    }, 'Allowed hosts do not match the explicit local development domains.')
    require(Path(settings.MEDIA_ROOT).resolve() == (PRIVATE / 'media').resolve()
            and Path(settings.STATIC_ROOT).resolve() == (PRIVATE / 'staticfiles').resolve(),
            'Media/static paths must remain inside the private development directory.')
    require(settings.CHANNEL_LAYERS['default']['BACKEND'] == 'stores.channels.StoreChannelLayer'
            and settings.STORE_CHANNEL_LAYER['BACKEND'] == 'channels_redis.core.RedisChannelLayer'
            and len(settings.STORE_CHANNEL_LAYER['CONFIG']['hosts']) == 1
            and settings.CACHES['default']['KEY_FUNCTION'] == 'stores.cache.make_key',
            'Tenant channel/cache isolation is not configured.')
    for filename in ('external-proofs.private.json', 'sms-delivery.private.json'):
        require(isinstance(json.loads((PRIVATE / filename).read_text(encoding='utf-8')), dict),
                f'Invalid private transport data: {filename}.')

    redis_urls = {
        'broker': (settings.CELERY_BROKER_URL, 1),
        'results': (settings.CELERY_RESULT_BACKEND, 2),
        'channels': (settings.STORE_CHANNEL_LAYER['CONFIG']['hosts'][0], 0),
        'cache': (settings.CACHES['default']['LOCATION'], 3),
    }
    for name, (url, database) in redis_urls.items():
        parsed = urlsplit(url)
        require(parsed.scheme == 'redis' and parsed.hostname == '127.0.0.1'
                and parsed.port == 26379 and parsed.path == f'/{database}'
                and not parsed.query and not parsed.fragment,
                f'Isolated Redis boundary mismatch: {name}.')

    try:
        for alias, expected_name in EXPECTED_DATABASES.items():
            with connections[alias].cursor() as cursor:
                cursor.execute('SELECT DATABASE(), 1')
                require(cursor.fetchone() == (expected_name, 1),
                        f'Connected to an unexpected database: {alias}.')
        for slug, alias in EXPECTED_STORES.items():
            store = Store.objects.filter(slug=slug).first()
            require(store is not None and store.database_alias == alias
                    and store.status == Store.Status.ACTIVE and store.initialized_at is not None,
                    f'Store is missing, inactive, uninitialized, or misrouted: {slug}.')
            host = 'sl.esports.club' if slug == 'shenglang' else 'xa.esports.club'
            require(store.boss_url.rstrip('/') == f'http://{host}/boss'
                    and store.provider_url.rstrip('/') == f'http://{host}/player'
                    and store.admin_url.rstrip('/') == 'http://admin.esports.club',
                    f'Store entry URLs do not match the local domain deployment: {slug}.')
        for name, (url, _) in redis_urls.items():
            client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
            try:
                require(client.ping(), f'Redis is unavailable: {name}.')
            finally:
                client.close()
    finally:
        connections.close_all()
    return {'status': 'ok', 'bind': '127.0.0.1', 'port': port,
            'databases': EXPECTED_DATABASES, 'stores': EXPECTED_STORES,
            'redis': 'ok', 'external_transports': 'isolated-substitutes'}


def install_external_substitutes():
    from PIL import Image
    from users import h5_login, password_login
    from users.views import WechatLoginError
    from stores import platform_auth

    def proof(code, kind):
        try:
            values = json.loads((PRIVATE / 'external-proofs.private.json').read_text(encoding='utf-8'))
            row = values[str(code)]
            if row['kind'] != kind or row['expires_at'] < time.time():
                raise KeyError('expired')
            return row['value']
        except (OSError, ValueError, KeyError, TypeError):
            raise WechatLoginError(
                '隔离验收外部测试凭证无效或过期', 'TEST_EXTERNAL_PROOF_INVALID', 400)

    def sms_send(phone, code):
        path = PRIVATE / 'sms-delivery.private.json'
        try:
            records = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            records = {}
        records[phone] = {'code': code, 'sent_at': time.time(), 'external_substitute': True}
        path.write_text(json.dumps(records, indent=2), encoding='utf-8')

    def isolated_avatar_url(authorized_profile):
        """Represent only an explicitly registered avatar grant; never call a CDN."""
        if (authorized_profile.get('avatar_test_marker') != 'isolated-wechat-avatar'
                or not authorized_profile.get('openid')):
            raise ValueError('隔离头像授权凭证无效，请使用登记的测试授权')
        # Compact test representation must fit the persisted avatar URL field.
        image = Image.new('RGB', (8, 8), '#2563eb')
        output = BytesIO()
        image.save(output, format='PNG')
        url = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        from club_accounts.models import ClubAccount
        require(len(url) <= ClubAccount._meta.get_field('avatar_url').max_length,
                'Isolated avatar representation exceeds the avatar field.')
        return url

    password_login.send_verification_code = sms_send
    platform_auth.send_verification_code = sms_send
    h5_login.exchange_code = lambda code: proof(code, 'wechat-h5-profile')
    h5_login.verified_avatar_url = isolated_avatar_url
    # The external avatar exchange is available only through the substitute above.
    from django.conf import settings
    settings.WECHAT_H5_AVATAR_SYNC_ENABLED = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int,
                        default=os.environ.get('XA_FRONTEND_ACCEPTANCE_PORT', '18106'))
    parser.add_argument('--check', action='store_true', help='Read-only dependency/configuration check; do not serve.')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    try:
        settings = configure(args.port)
        result = check(settings, args.port)
    except Exception as exc:
        # Driver exceptions may contain credentials/DSNs. Keep them out of logs.
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__,
                          'message': str(exc) if type(exc) is RuntimeError else 'Private configuration/dependency check failed.'}),
              file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result), flush=True)
    if args.check:
        return 0
    install_external_substitutes()
    from config.serve import CommandLine
    sys.argv = [sys.argv[0], '-b', '127.0.0.1', '-p', str(args.port), 'config.asgi:application']
    CommandLine.entrypoint()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
