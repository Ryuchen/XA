from .settings import *  # noqa: F403


# 显式钉死安全基线相关配置，避免测试环境被 config.checks 的生产 fail-fast 打死：
# 无论宿主机 / CI 是否导出了 DJANGO_DEBUG=0，测试都以「本地零配置」语义运行。
DEBUG = True
SECRET_KEY = 'xa-test-only-secret-key-not-for-production-use'
ALLOWED_HOSTS = ['*']

# Unit tests and the default CI job run without a database or Redis service.
# Migration/MySQL settings override DATABASES for their separate checks.
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'

# 限流默认关闭：历史用例存在同一测试内高频登录/下单，开启会误伤。
# 需要验证 429 的用例请用 @override_settings(REST_FRAMEWORK={...}) 单点打开
# 对应 scope 的速率，而不是在这里放开全局限流。
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    'DEFAULT_THROTTLE_RATES': {
        'login': None,
        'order_write': None,
        'checkin': None,
        'wallet_write': None,
        'network_report': '6/min',
    },
}

# Fast unit tests sync current local-app models. Migration and MySQL settings
# separately exercise the complete migration chain against an empty database.
MIGRATION_MODULES = {
    app: None
    for app in (
        'announcements',
        'audition',
        'banners',
        'chat',
        'club_accounts',
        'console',
        'coupons',
        'orders',
        'promotions',
        'site_messages',
        'support',
        'users',
        'wallet',
        'engagement',
    )
}

# Most historical API tests use DRF's force_authenticate(CustomUser).  Keep
# that fixture style working while production accepts business-account JWTs
# only; the permission layer lazily mirrors and links the fixture account.
LEGACY_FORCE_AUTH_COMPAT = True
REQUIRE_ORDER_EVIDENCE_IMAGES = False
REQUIRE_RECHARGE_PROOF = False
# 历史回归场景继续验证已上线旧订单的完成结算口径；新链路用例显式启用审核结算。
ORDER_SETTLEMENT_MODE = 'COMPLETION'

LOGIN_OTP_REQUIRED = False

# 新流量场景在专项测试中显式启用。
ORDER_TRAFFIC_ENABLED = False
