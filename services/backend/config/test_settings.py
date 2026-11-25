from .settings import *  # noqa: F403


# 显式钉死安全基线相关配置，避免测试环境被 config.checks 的生产 fail-fast 打死：
# 无论宿主机 / CI 是否导出了 DJANGO_DEBUG=0，测试都以「本地零配置」语义运行。
DEBUG = True
COMPLIANCE_ALLOW_BUILTIN_BOOTSTRAP = True
ISOLATED_CONTROL_PLANE = False
SECRET_KEY = 'xa-test-only-secret-key-not-for-production-use'
ALLOWED_HOSTS = ['*']

# 官方套件由 StoreContextRunner 建立声浪目录行与夹具侧门店上下文；
# DRF 默认测试 Host 与本机回环地址绑定到同一部署固定入口。
TEST_RUNNER = 'config.test_runner.StoreContextRunner'
STORE_HOST_BINDINGS = {'sl.esports.club': 'shenglang', 'xa.esports.club': 'xinan',
                       'testserver': 'shenglang', 'localhost': 'shenglang',
                       '127.0.0.1': 'shenglang'}

# Unit tests and the default CI job run without a database or Redis service.
# Migration/MySQL settings override DATABASES for their separate checks.
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}, 'store_a': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}, 'store_b': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}, 'store_new': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
# 宿主机可能导出 XA_STORE_DATABASES，使基础 settings 注入门店别名；
# 单测无门店库服务，必须随 DATABASES 一并重置，否则系统检查找不到连接。
STORE_DATABASE_ALIASES = ()
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
LICENSE_NOTICE_DISPATCH_ENABLED = False
LICENSE_ACCOUNT_ADMISSION_ENABLED = False
LICENSE_BENEFIT_ENFORCEMENT_ENABLED = False
STORE_CERTIFICATE_ENFORCEMENT_ENABLED = False
LICENSE_SNAPSHOT_CACHE_ENABLED = False

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
# Existing historical fixtures predate the mandatory WeChat gate. Dedicated
# acceptance tests explicitly enable it and exercise real eligibility logic.
PROVIDER_WECHAT_GATE_ENABLED = False
# SQLite is never a production clock. This explicit test-only seam preserves
# controllable reservation time fixtures; production MySQL always samples DB time.
RESERVATION_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY = True
# Settlement tests explicitly opt into the same isolated SQLite seam. Production
# still accepts only the current store MySQL clock and rejects raw datetimes.
SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY = True

# 新流量场景在专项测试中显式启用。
ORDER_TRAFFIC_ENABLED = False
