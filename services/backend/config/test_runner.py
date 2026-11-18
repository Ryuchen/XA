"""官方套件的门店上下文引导。

业务代码要求实体编号、媒体和令牌都有明确门店上下文。快速套件把 DRF 默认
测试 Host 绑定到部署固定的声浪入口，并在每个 Django 用例前确保控制库存在
该门店目录行；请求侧仍由中间件按 Host 自行解析门店。负路径用例用
``store_context(None)`` 显式退出上下文，不受引导影响。
"""
from django.test.runner import DiscoverRunner


def ensure_bootstrap_store():
    from stores.models import Store
    store, _ = Store.objects.using('default').get_or_create(
        slug='shenglang', defaults={'name': '声浪', 'entity_prefix': 'SL',
                                    'database_alias': 'default', 'status': Store.Status.ACTIVE})
    return store


class StoreContextRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        self._bootstrap_store = ensure_bootstrap_store()
        return old_config

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        from django.test import SimpleTestCase, TestCase
        for cls in (SimpleTestCase, TestCase):
            if getattr(cls._pre_setup, '_store_bootstrap', False):
                continue
            original = cls._pre_setup.__func__

            def _pre_setup_with_store(cls, _original=original):
                # TransactionTestCase 会清表；每个用例前重新确保目录行存在。
                ensure_bootstrap_store()
                _original(cls)

            _pre_setup_with_store._store_bootstrap = True
            cls._pre_setup = classmethod(_pre_setup_with_store)

    def run_suite(self, suite, **kwargs):
        from stores.context import store_context
        with store_context(self._bootstrap_store):
            return super().run_suite(suite, **kwargs)