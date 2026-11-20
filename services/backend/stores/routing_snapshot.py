"""Authenticated per-store routing state for control-directory outages.

Only isolated deployments may use these snapshots.  A snapshot binds a store,
its configured database identity and a monotonic control-plane revision; it is
never an authentication credential or a source of domain bindings.
"""
import hmac
import json
import logging

from django.conf import settings
from django.db import DatabaseError, InterfaceError, transaction
from django.utils.crypto import salted_hmac
from django.utils.dateparse import parse_datetime

from .database_identity import database_identity, identity_fingerprint
from .dynamic_database import ensure_store_connection

logger = logging.getLogger(__name__)
SALT = 'xa.store-routing.snapshot.v1'
FIELDS = ('id', 'slug', 'name', 'entity_prefix', 'provider_name_prefix', 'currency_name',
          'logo_url', 'database_alias', 'status', 'initialized_at', 'boss_url', 'provider_url',
          'admin_url', 'created_at', 'updated_at')
PRESENTATION_FIELDS = ('background_url', 'introduction')
TIMES = frozenset({'initialized_at', 'created_at', 'updated_at'})


def _signature(payload, secret):
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    return salted_hmac(SALT, canonical, secret=secret, algorithm='sha256').hexdigest()


def capture(store, revision):
    ensure_store_connection(store)
    if (store.database_alias == 'default' or store.database_alias not in getattr(settings, 'STORE_DATABASE_ALIASES', ())
            or type(revision) is not int or revision < 0 or not store.pk or not store.initialized_at):
        raise ValueError('门店路由状态尚未完成初始化')
    # Keep the exact v2 representation until a real directory update adds
    # presentation data and advances the store's revision.
    has_presentation = any(getattr(store, field) for field in PRESENTATION_FIELDS)
    fields = FIELDS + PRESENTATION_FIELDS if has_presentation else FIELDS
    values = {field: (getattr(store, field).isoformat() if field in TIMES and getattr(store, field) is not None
                      else getattr(store, field)) for field in fields}
    from .models import StoreDomain
    domains = list(StoreDomain.objects.filter(store_id=store.pk).order_by('hostname')
                   .values('hostname', 'revision', 'active'))
    return {'schema': 3 if has_presentation else 2, 'revision': revision,
            'database_identity': list(database_identity(store.database_alias)),
            'store': values, 'domains': domains}


def _validated(row, alias, *, expected_slug=None):
    if row is None or not isinstance(row.payload, dict) or not isinstance(row.signature, str):
        return None
    try:
        secrets = [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', [])]
        if not any(hmac.compare_digest(row.signature, _signature(row.payload, secret)) for secret in secrets):
            return None
        payload = row.payload
        schema = payload.get('schema')
        fields = FIELDS + PRESENTATION_FIELDS if schema == 3 else FIELDS
        if (set(payload) != {'schema', 'revision', 'database_identity', 'store', 'domains'}
                or type(schema) is not int or schema not in (2, 3)
                or type(payload['revision']) is not int or payload['revision'] < 0
                or not isinstance(payload['store'], dict) or set(payload['store']) != set(fields)):
            return None
        domains = payload['domains']
        if (not isinstance(domains, list) or any(not isinstance(item, dict)
                or set(item) != {'hostname', 'revision', 'active'}
                or not isinstance(item['hostname'], str) or type(item['revision']) is not int
                or item['revision'] < 1 or type(item['active']) is not bool for item in domains)
                or len({item['hostname'] for item in domains}) != len(domains)):
            return None
        data = payload['store']
        if (type(data['id']) is not int or data['id'] < 1 or data['database_alias'] != alias
                or (expected_slug is not None and data['slug'] != expected_slug)
                or data['status'] not in ('ACTIVE', 'DISABLED')
                or payload['database_identity'] != list(database_identity(alias))):
            return None
        from .hosts import STORE_SLUG
        if not isinstance(data['slug'], str) or not STORE_SLUG.fullmatch(data['slug']):
            return None
        for key in TIMES:
            value = parse_datetime(data[key]) if isinstance(data[key], str) else None
            if value is None or value.utcoffset() is None:
                return None
        if not all(isinstance(data[key], str) for key in (
                'name', 'provider_name_prefix', 'currency_name', 'logo_url', 'boss_url', 'provider_url', 'admin_url')):
            return None
        if schema == 3 and not all(isinstance(data[key], str) for key in PRESENTATION_FIELDS):
            return None
        if data['entity_prefix'] is not None and not isinstance(data['entity_prefix'], str):
            return None
        return payload
    except (TypeError, ValueError, OverflowError):
        return None


def publish(payload, *, require_current=False):
    """Publish verified directory state; higher revisions always win."""
    from club_accounts.models import StoreRoutingSnapshot
    from .context import store_context
    values = payload['store']
    alias, slug = values['database_alias'], values['slug']
    signature = _signature(payload, settings.SECRET_KEY)
    candidate = type('Candidate', (), {'payload': payload, 'signature': signature})()
    if _validated(candidate, alias, expected_slug=slug) is None:
        raise ValueError('门店路由快照无效')
    def unchanged(row):
        existing = _validated(row, alias, expected_slug=slug)
        if existing is not None:
            if existing['revision'] > payload['revision']:
                if require_current:
                    raise ValueError('门店路由已有更新的版本')
                return True
            if existing['revision'] == payload['revision']:
                if existing != payload:
                    raise ValueError('同一门店路由版本内容不一致')
                return row.signature == signature
        return False

    temporary = _store_from_snapshot(payload)
    with store_context(temporary):
        # The overwhelmingly common unchanged request never takes an exclusive
        # lock; recheck under lock whenever a write may actually be necessary.
        with transaction.atomic(using=alias):
            if unchanged(StoreRoutingSnapshot.objects.using(alias).filter(pk=1).first()):
                return
        with transaction.atomic(using=alias):
            StoreRoutingSnapshot.objects.using(alias).get_or_create(pk=1)
            row = StoreRoutingSnapshot.objects.using(alias).select_for_update().get(pk=1)
            if unchanged(row):
                return
            row.payload, row.signature = payload, signature
            row.save(using=alias, update_fields=['payload', 'signature'])


def schedule(store, revision):
    """Only publish an active identity after the directory transaction commits."""
    try:
        payload = capture(store, revision)
    except ValueError:
        return

    def write():
        try:
            publish(payload)
            from .domain_index import write as write_domain_index
            for domain in payload['domains']:
                write_domain_index(domain['hostname'], slug=payload['store']['slug'],
                    alias=payload['store']['database_alias'], store_id=payload['store']['id'],
                    revision=domain['revision'], active=domain['active'] and payload['store']['status'] == 'ACTIVE')
        except (DatabaseError, InterfaceError, OSError, ValueError, TypeError):
            logger.warning('Unable to persist routing snapshot for %s', store.slug, exc_info=True)
    transaction.on_commit(write, using='default', robust=True)


def latest_revision(store):
    """Include durable tombstones from a control transaction that rolled back."""
    from club_accounts.models import StoreRoutingSnapshot
    ensure_store_connection(store)
    alias = store.database_alias
    with transaction.atomic(using=alias):
        row = StoreRoutingSnapshot.objects.using(alias).filter(pk=1).first()
    existing = _validated(row, alias, expected_slug=store.slug)
    return existing['revision'] if existing is not None else 0


def _store_from_snapshot(payload):
    from .models import Store
    data = {key: (parse_datetime(value) if key in TIMES else value)
            for key, value in payload['store'].items()}
    store = Store(**data)
    store._state.adding, store._state.db = False, 'default'
    store._verified_database_fingerprint = identity_fingerprint(payload['database_identity'])
    return store


def _verified_stores(expected_slug=None):
    from club_accounts.models import StoreRoutingSnapshot
    found = []
    for alias in getattr(settings, 'STORE_DATABASE_ALIASES', ()):
        if alias == 'default' or alias not in settings.DATABASES:
            continue
        try:
            with transaction.atomic(using=alias):
                row = StoreRoutingSnapshot.objects.using(alias).filter(pk=1).first()
            payload = _validated(row, alias, expected_slug=expected_slug)
        except (DatabaseError, InterfaceError, ValueError):
            logger.warning('Unable to read cached routing state from store database %s', alias)
            continue
        if payload is not None:
            found.append(_store_from_snapshot(payload))
    return found


def lookup(slug):
    """Return a verified store from its own DB, or None if no copy is usable."""
    from .dynamic_database import enabled, materialize
    if enabled():
        try:
            materialize(slug)
        except ValueError:
            pass
    found = _verified_stores(expected_slug=slug)
    return found[0] if len(found) == 1 else None


def cached_active_stores():
    """Reconstruct and enumerate verified stores after a worker restart."""
    # Dynamic connection aliases are process-local. A fresh worker has not
    # necessarily handled any HTTP request before the directory goes down.
    # Bootstrap only deployment-owned bindings and signed hostname entries;
    # both lookups still validate the store's signed tenant snapshot and DB
    # identity before a task can be routed there.
    from .hosts import DEFAULT_HOST_BINDINGS, STORE_SLUG
    from .domain_index import active_hostnames
    bindings = getattr(settings, 'STORE_HOST_BINDINGS', DEFAULT_HOST_BINDINGS)
    stores = _verified_stores()
    for slug in set(bindings.values()):
        if isinstance(slug, str) and STORE_SLUG.fullmatch(slug):
            store = lookup(slug)
            if store is not None:
                stores.append(store)
    for hostname in active_hostnames():
        store = lookup_domain(hostname)
        if store is not None:
            stores.append(store)
    identities = {}
    for store in stores:
        identities.setdefault(store.slug, {})[(store.pk, store.database_alias)] = store
    return [next(iter(group.values())) for group in identities.values()
            if len(group) == 1 and next(iter(group.values())).status == 'ACTIVE']


def lookup_domain(hostname):
    """Resolve a signed shared index only after validating the target tenant copy."""
    from .domain_index import read
    index = read(hostname)
    if not index or not index['active']:
        return None
    from .dynamic_database import enabled, materialize
    if enabled():
        try:
            materialize(index['slug'], expected_alias=index['database_alias'])
        except ValueError:
            return None
    from club_accounts.models import StoreRoutingSnapshot
    alias = index['database_alias']
    if alias == 'default' or alias not in settings.DATABASES:
        return None
    try:
        row = StoreRoutingSnapshot.objects.using(alias).filter(pk=1).first()
        payload = _validated(row, alias, expected_slug=index['slug'])
    except (DatabaseError, InterfaceError, ValueError):
        return None
    expected = {'hostname': hostname, 'revision': index['revision'], 'active': True}
    if (not payload or payload['store']['id'] != index['store_id']
            or expected not in payload['domains']):
        return None
    return _store_from_snapshot(payload)
