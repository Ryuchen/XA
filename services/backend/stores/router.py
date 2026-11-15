from .context import current_database, current_store
from django.conf import settings


TENANT_APPS = frozenset({
    'identifiers', 'club_accounts', 'users', 'orders', 'wallet', 'support',
    'site_messages', 'announcements', 'banners', 'coupons', 'chat', 'audition',
    'promotions', 'console', 'engagement',
})


class StoreRouter:
    def _route(self, model, hints):
        if model._meta.app_label == 'stores':
            return 'default'
        alias = current_database()
        if (getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and alias == 'default'
                and model._meta.app_label in TENANT_APPS):
            raise ValueError('平台控制库不能访问门店业务数据，请先确定门店')
        instance = hints.get('instance')
        if (current_store() is not None and instance is not None
                and instance._state.db is not None and instance._state.db != alias):
            raise ValueError('禁止跨门店访问业务对象')
        return alias

    def db_for_read(self, model, **hints):
        return self._route(model, hints)

    def db_for_write(self, model, **hints):
        return self._route(model, hints)

    def allow_relation(self, obj1, obj2, **hints):
        return obj1._state.db == obj2._state.db

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == 'stores':
            return db == 'default'
        if (getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and db == 'default'
                and (app_label in TENANT_APPS or app_label == 'admin')):
            return False
        return True
