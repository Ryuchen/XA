"""Local-only frontend acceptance: real isolated MySQL and unchanged business views.

Secrets and fixtures live under the ignored .tools/fronted-acceptance directory.
The private server substitutes only the authorized external WeChat/SMS transport.
Never use this settings module for production or the xa_verify application DB.
"""
import json
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

_root = Path(__file__).resolve().parents[3]
_folder = _root / '.tools' / 'fronted-acceptance'
_runtime_path = _folder / 'runtime.private.json'
if not _runtime_path.is_file():
    raise ImproperlyConfigured('Run .tools/fronted-acceptance/bootstrap.py before local acceptance.')
_runtime = json.loads(_runtime_path.read_text(encoding='utf-8'))
_database_name = 'xa_test_fronted_acceptance_20261104'
if _runtime.get('database') != _database_name:
    raise ImproperlyConfigured('Frontend acceptance database must be the dedicated xa_test schema.')
os.environ.update({
    'XA_TEST_MYSQL_DATABASE': _database_name,
    'XA_TEST_MYSQL_HOST': '127.0.0.1',
    'XA_TEST_MYSQL_PORT': '13306',
    'XA_TEST_MYSQL_USER': _runtime['mysql_user'],
    'XA_TEST_MYSQL_PASSWORD': _runtime['mysql_password'],
    'DJANGO_SECRET_KEY': _runtime['django_secret_key'],
    'DJANGO_DEBUG': 'true',
    'DJANGO_CACHE_URL': '',
    'KOOK_ENABLED': 'false',
    'WECOM_DISPATCH_ENABLED': 'false',
    'WECHAT_MOCK_LOGIN': 'false',
    'WECHAT_APPID': 'wx_fronted_acceptance_test',
    'WECHAT_SECRET': 'fronted-acceptance-external-substitute-only',
})

from .mysql_test_settings import *  # noqa: E402,F403

DEBUG = True
# This registered local preview deliberately retains its pre-existing two-DB
# layout. Independent control-plane testing uses isolated_control_test_settings.
ISOLATED_CONTROL_PLANE = False
SECRET_KEY = _runtime['django_secret_key']
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver', 'sl.esports.club', 'xa.esports.club', 'admin.esports.club']
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [f'http://127.0.0.1:{port}' for port in (13101, 13102, 13103, 13111, 13112)]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS
LOGIN_OTP_REQUIRED = True
WECHAT_H5_APPID = _runtime.get('wechat_h5_appid', '')
WECHAT_H5_SECRET = _runtime.get('wechat_h5_secret', '')
WECHAT_H5_ALLOWED_ORIGINS = [
    'http://sl.esports.club', 'http://xa.esports.club', 'http://admin.esports.club',
    'http://127.0.0.1:13101', 'http://127.0.0.1:13102', 'http://127.0.0.1:13103',
]
LEGACY_FORCE_AUTH_COMPAT = False
ORDER_SETTLEMENT_MODE = 'REPORT_APPROVAL'
REQUIRE_ORDER_EVIDENCE_IMAGES = True
REQUIRE_RECHARGE_PROOF = True
ORDER_TRAFFIC_ENABLED = False
KOOK_ENABLED = False
KOOK_BOT_TOKEN = ''
WECOM_DISPATCH_ENABLED = False
WECOM_DISPATCH_WEBHOOK = ''
SMS_SECRET_ID = SMS_SECRET_KEY = SMS_APP_ID = SMS_TEMPLATE_ID = SMS_SIGN_NAME = ''
# Only this explicitly isolated development environment enables the control
# plane by default. Its SMS sender is replaced by serve-backend.py.
PLATFORM_IDENTITY_ENABLED = True
# These stores have the provider evidence migration. Real WeChat userinfo and
# image fetching remain disabled in this external-substitute environment.
PROVIDER_WECHAT_GATE_ENABLED = True
WECHAT_H5_AVATAR_SYNC_ENABLED = False
CELERY_BROKER_URL = 'redis://127.0.0.1:26379/1'
CELERY_RESULT_BACKEND = 'redis://127.0.0.1:26379/2'
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BEAT_SCHEDULE = {}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer', 'CONFIG': {'hosts': ['redis://127.0.0.1:26379/0']}}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': 'redis://127.0.0.1:26379/3'}}
MEDIA_ROOT = str(_folder / 'media')
STATIC_ROOT = str(_folder / 'staticfiles')
MEDIA_BASE_URL = 'http://127.0.0.1:18105'
AUDITION_BOSS_LINK_BASE_URL = 'http://127.0.0.1:13101/#/audition'
AUDITION_PROVIDER_LINK_BASE_URL = 'http://127.0.0.1:13102/#/audition'

if DATABASES['default']['NAME'] != _database_name or DATABASES['default']['HOST'] != '127.0.0.1':  # noqa: F405
    raise ImproperlyConfigured('Frontend acceptance database boundary was not preserved.')

# Optional additional acceptance stores use dedicated test schemas. Keep the
# same tenant boundaries as deployment even when this module replaces backends.
from stores.database_config import install_store_databases  # noqa: E402
_store_databases = _runtime.get('store_databases', {})
for _store_config in _store_databases.values():
    if (not str(_store_config.get('NAME', '')).startswith('xa_test_')
            or _store_config.get('HOST', '127.0.0.1') != '127.0.0.1'):
        raise ImproperlyConfigured('Additional acceptance stores must use local xa_test schemas.')
STORE_DATABASE_ALIASES = install_store_databases(DATABASES, json.dumps(_store_databases))
STORE_CHANNEL_LAYER = CHANNEL_LAYERS['default']
CHANNEL_LAYERS = {'default': {'BACKEND': 'stores.channels.StoreChannelLayer'}}
CACHES['default']['KEY_FUNCTION'] = 'stores.cache.make_key'
