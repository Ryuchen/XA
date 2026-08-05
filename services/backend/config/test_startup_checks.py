"""启动期配置自检的单元测试（纯函数，无需数据库）。

这些断言守的是「生产不能带着开发默认值上线」这条底线 —— 一旦有人
放宽了 checks.py 的判断，这里必须红。
"""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.checks import (
    DEV_INSECURE_DB_PASSWORD,
    DEV_INSECURE_SECRET_KEY,
    MIN_SECRET_KEY_LENGTH,
    check_allowed_hosts,
    check_cors,
    check_database,
    check_secret_key,
    check_wechat,
    run_startup_checks,
)

PROD_SECRET = 'x' * (MIN_SECRET_KEY_LENGTH + 8)

SAFE_PROD_KWARGS = {
    'debug': False,
    'secret_key': PROD_SECRET,
    'allowed_hosts': ['api.example.com'],
    'cors_allow_all_origins': False,
    'cors_allowed_origins': ['https://app.example.com'],
    'databases': {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'PASSWORD': 'a-real-production-password',
        }
    },
    'wechat_mock_login': False,
}


class DebugModeSkipsEverythingTest(SimpleTestCase):
    """DEBUG=True 是本地开发路径：所有校验必须放行，保证零配置可跑。"""

    def test_all_checks_pass_in_debug(self):
        self.assertEqual(check_secret_key(True, ''), [])
        self.assertEqual(check_allowed_hosts(True, []), [])
        self.assertEqual(check_cors(True, True, []), [])
        self.assertEqual(
            check_database(True, {'default': {'PASSWORD': DEV_INSECURE_DB_PASSWORD}}),
            [],
        )
        self.assertEqual(check_wechat(True, True), [])

    def test_run_startup_checks_noop_in_debug(self):
        run_startup_checks(
            debug=True,
            secret_key='',
            allowed_hosts=[],
            cors_allow_all_origins=True,
            cors_allowed_origins=[],
            databases={'default': {'PASSWORD': DEV_INSECURE_DB_PASSWORD}},
            wechat_mock_login=True,
        )


class SecretKeyCheckTest(SimpleTestCase):
    def test_empty_secret_key_rejected(self):
        self.assertTrue(check_secret_key(False, ''))

    def test_dev_default_secret_key_rejected(self):
        self.assertTrue(check_secret_key(False, DEV_INSECURE_SECRET_KEY))

    def test_short_secret_key_rejected(self):
        self.assertTrue(check_secret_key(False, 'short'))

    def test_strong_secret_key_accepted(self):
        self.assertEqual(check_secret_key(False, PROD_SECRET), [])


class AllowedHostsCheckTest(SimpleTestCase):
    def test_empty_hosts_rejected(self):
        self.assertTrue(check_allowed_hosts(False, []))

    def test_wildcard_rejected(self):
        self.assertTrue(check_allowed_hosts(False, ['*']))

    def test_explicit_hosts_accepted(self):
        self.assertEqual(check_allowed_hosts(False, ['api.example.com']), [])


class CorsCheckTest(SimpleTestCase):
    def test_allow_all_origins_rejected_in_production(self):
        self.assertTrue(check_cors(False, True, []))

    def test_explicit_whitelist_accepted(self):
        self.assertEqual(check_cors(False, False, ['https://app.example.com']), [])


class DatabaseCheckTest(SimpleTestCase):
    def test_dev_password_rejected(self):
        errors = check_database(
            False,
            {'default': {
                'ENGINE': 'django.db.backends.mysql',
                'PASSWORD': DEV_INSECURE_DB_PASSWORD,
            }},
        )
        self.assertTrue(errors)

    def test_sqlite_has_no_password_concept(self):
        self.assertEqual(
            check_database(
                False,
                {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
            ),
            [],
        )

    def test_real_password_accepted(self):
        self.assertEqual(
            check_database(
                False,
                {'default': {
                    'ENGINE': 'django.db.backends.mysql',
                    'PASSWORD': 'real-secret',
                }},
            ),
            [],
        )


class WechatCheckTest(SimpleTestCase):
    def test_mock_login_rejected_in_production(self):
        self.assertTrue(check_wechat(False, True))

    def test_real_credentials_accepted(self):
        self.assertEqual(check_wechat(False, False), [])


class RunStartupChecksTest(SimpleTestCase):
    def test_fully_configured_production_passes(self):
        run_startup_checks(**SAFE_PROD_KWARGS)

    def test_single_failure_blocks_startup(self):
        kwargs = {**SAFE_PROD_KWARGS, 'wechat_mock_login': True}
        with self.assertRaises(ImproperlyConfigured):
            run_startup_checks(**kwargs)

    def test_all_failures_reported_at_once(self):
        """运维应该一次看全所有待补项，而不是修一个报一个。"""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            run_startup_checks(
                debug=False,
                secret_key=DEV_INSECURE_SECRET_KEY,
                allowed_hosts=['*'],
                cors_allow_all_origins=True,
                cors_allowed_origins=[],
                databases={'default': {
                    'ENGINE': 'django.db.backends.mysql',
                    'PASSWORD': DEV_INSECURE_DB_PASSWORD,
                }},
                wechat_mock_login=True,
            )
        message = str(ctx.exception)
        for marker in ('1.', '2.', '3.', '4.', '5.'):
            self.assertIn(marker, message)
