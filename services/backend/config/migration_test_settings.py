"""从空数据库执行完整迁移链；与同步当前模型的单元测试分开验证部署。"""
from .test_settings import *  # noqa: F403

MIGRATION_MODULES = {}
# 与快速套件相同的别名布局：support 等双店用例声明 store_b，
# 迁移链在每个别名上独立从空库执行。
DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    'store_a': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    'store_b': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    'store_new': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
}
