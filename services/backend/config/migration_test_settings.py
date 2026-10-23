"""从空数据库执行完整迁移链；与同步当前模型的单元测试分开验证部署。"""
from .test_settings import *  # noqa: F403

MIGRATION_MODULES = {}
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
