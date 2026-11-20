from contextlib import ContextDecorator
from contextvars import ContextVar

from django.conf import settings

from .dynamic_database import (discover_store_connection, ensure_store_connection,
                               uses_dynamic_connection)


_store = ContextVar('xa_current_store', default=None)


class StoreNotFound(ValueError):
    pass


class StoreUnavailable(ValueError):
    pass


def default_store_slug():
    return getattr(settings, 'DEFAULT_STORE_SLUG', 'shenglang')


def current_store():
    return _store.get()


def current_currency_name():
    """Display the active store's currency without crossing tenant data."""
    store = current_store()
    return getattr(store, 'currency_name', '') or '店币'


def current_database():
    store = current_store()
    return store.database_alias if store is not None else 'default'


class store_context(ContextDecorator):
    def __init__(self, store):
        self.store = store
        self._tokens = []

    def __enter__(self):
        if self.store is not None:
            ensure_store_connection(self.store)
        self._tokens.append(_store.set(self.store))
        return self.store

    def __exit__(self, *exc):
        _store.reset(self._tokens.pop())

    async def __aenter__(self):
        if (self.store is not None and uses_dynamic_connection(self.store)
                and getattr(self.store, '_verified_database_fingerprint', None) is None):
            from channels.db import database_sync_to_async
            await database_sync_to_async(ensure_store_connection)(self.store)
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)

    def _recreate_cm(self):
        return type(self)(self.store)


def _fetch_store(slug):
    from .models import Store
    query = Store.objects
    if getattr(settings, 'ISOLATED_CONTROL_PLANE', False):
        from django.db.models import BigIntegerField, Value
        from django.db.models.functions import Coalesce
        query = query.annotate(route_snapshot_version=Coalesce(
            'routing_revision__version', Value(0), output_field=BigIntegerField()))
    return query.filter(slug=slug).first()


def resolve_store(slug=None, *, require_active=True, allow_cached=False):
    from django.db import DatabaseError, transaction

    slug = default_store_slug() if slug is None else slug
    cached = False
    try:
        # A failed control read must not poison a caller's outer transaction.
        with transaction.atomic(using='default'):
            store = _fetch_store(slug)
            if store is not None:
                discover_store_connection(store)
    except DatabaseError:
        if not getattr(settings, 'ISOLATED_CONTROL_PLANE', False) or not allow_cached:
            raise
        from .routing_snapshot import lookup
        store = lookup(slug)
        if store is None:
            raise
        cached = True
    except ValueError as exc:
        raise StoreUnavailable('门店数据库尚未配置，请联系平台管理员') from exc
    if store is None:
        raise StoreNotFound('门店不存在，请检查门店链接')
    if cached:
        try:
            ensure_store_connection(store)
        except ValueError as exc:
            raise StoreUnavailable('门店数据库尚未配置，请联系平台管理员') from exc
    if (not cached and getattr(settings, 'ISOLATED_CONTROL_PLANE', False)
            and store.initialized_at and store.status in ('ACTIVE', 'DISABLED')):
        from .routing_snapshot import schedule
        schedule(store, store.route_snapshot_version)
    if require_active and not store.is_active:
        raise StoreUnavailable('门店尚未开放或已停用，请联系门店管理员')
    if getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and store.database_alias == 'default':
        raise StoreUnavailable('门店业务库不能使用平台控制库，请联系平台配置独立业务库')
    if store.database_alias not in settings.DATABASES:
        raise StoreUnavailable('门店数据库尚未配置，请联系平台管理员')
    return store


def active_stores():
    from .models import Store

    return Store.objects.filter(status=Store.Status.ACTIVE)
