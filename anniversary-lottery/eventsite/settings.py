import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DJANGO_DEBUG', 'true').lower() == 'true'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-lottery-development-only-change-in-production')
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
if not DEBUG and (len(SECRET_KEY) < 40 or SECRET_KEY.startswith('local-')):
    raise ImproperlyConfigured('生产环境必须配置独立 DJANGO_SECRET_KEY')
PREFIX = os.getenv('LOTTERY_SCRIPT_NAME', '').rstrip('/')
FORCE_SCRIPT_NAME = PREFIX or None
# The campaign now accepts only previously issued opportunities. This is not a
# control-panel preference: reopening admissions requires an explicit release.
LOTTERY_REMAINING_ONLY = True
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'rest_framework', 'lottery',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'eventsite.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [], 'APP_DIRS': True, 'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]}}]
WSGI_APPLICATION = 'eventsite.wsgi.application'
ASGI_APPLICATION = 'eventsite.asgi.application'
if os.getenv('MYSQL_DATABASE'):
    DATABASES = {'default': {'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ['MYSQL_DATABASE'], 'HOST': os.getenv('MYSQL_HOST', 'db'),
        'PORT': os.getenv('MYSQL_PORT', '3306'), 'USER': os.environ['MYSQL_USER'],
        'PASSWORD': os.environ['MYSQL_PASSWORD'], 'CONN_MAX_AGE': 60,
        'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed'}}}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'lottery.sqlite3', 'OPTIONS': {'timeout': 20}}}
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = PREFIX + '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
LOGIN_URL = PREFIX + '/admin/login/'
LOGIN_REDIRECT_URL = PREFIX + '/control/'
SESSION_COOKIE_NAME = 'anniversary_admin_session'
CSRF_COOKIE_NAME = 'anniversary_csrf'
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = PREFIX + '/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
CSRF_TRUSTED_ORIGINS = [value for value in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if value]
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
]
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'anniversary-local'}}
if os.getenv('LOTTERY_CACHE_URL'):
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': os.environ['LOTTERY_CACHE_URL'], 'KEY_PREFIX': 'xa-anniversary'}}
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
    'EXCEPTION_HANDLER': 'lottery.views.exception_handler',
    'UNAUTHENTICATED_USER': None,
}
