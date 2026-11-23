import re
import secrets
import string
import threading

from django.db import IntegrityError, OperationalError, ProgrammingError, transaction

from .models import EntityNumber

ALPHABET = string.ascii_uppercase + string.digits
CACHE = threading.local()
INTERNAL_APPS = {
    'admin', 'auth', 'contenttypes', 'sessions', 'messages', 'staticfiles',
    'rest_framework', 'rest_framework_simplejwt', 'channels', 'stores', 'identifiers',
    'django_migrations',
}


def entity_prefix_for(store=None):
    if store is None:
        from stores.context import current_store
        store = current_store()
    if store is None:
        raise ValueError('生成业务编号需要明确的门店上下文')
    configured = str(getattr(store, 'entity_prefix', '') or '').strip().upper()
    if re.fullmatch(r'[A-Z0-9]{2,4}', configured):
        return configured
    slug = str(getattr(store, 'slug', '') or '')
    fallback = {'shenglang': 'SL', 'xinan': 'XA'}.get(slug)
    if fallback:
        return fallback
    return re.sub(r'[^A-Z0-9]', '', slug.upper())[:2].ljust(2, 'X')


def numbered_model(model):
    return (
        getattr(model, '_meta', None) is not None
        and model._meta.app_label not in INTERNAL_APPS
        and model._meta.managed
        and model._meta.pk is not None
    )


def entity_key(instance):
    return instance._meta.label_lower, str(instance.pk)


def entity_alias(instance):
    return instance._state.db or 'default'


def cache_store():
    if not hasattr(CACHE, 'values'):
        CACHE.values = {}
    return CACHE.values


def prime_entity_numbers(instances, using=None):
    groups = {}
    for instance in instances:
        if instance is None or getattr(instance, 'pk', None) is None or not numbered_model(instance.__class__):
            continue
        alias = using or entity_alias(instance)
        label, entity_id = entity_key(instance)
        groups.setdefault((alias, label), set()).add(entity_id)
    for (alias, label), entity_ids in groups.items():
        rows = dict(EntityNumber.objects.using(alias).filter(
            entity_type=label, entity_id__in=entity_ids,
        ).values_list('entity_id', 'code'))
        cache = cache_store()
        for entity_id in entity_ids:
            cache[(alias, label, entity_id)] = rows.get(entity_id, '')


def get_entity_number(instance):
    if instance is None or getattr(instance, 'pk', None) is None:
        return ''
    alias = entity_alias(instance)
    label, entity_id = entity_key(instance)
    key = (alias, label, entity_id)
    cached = cache_store().get(key)
    if cached is not None:
        return cached
    code = EntityNumber.objects.using(alias).filter(
        entity_type=label, entity_id=entity_id,
    ).values_list('code', flat=True).first() or ''
    cache_store()[key] = code
    return code


def ensure_entity_number(instance, using=None, prefix=None):
    if instance is None or getattr(instance, 'pk', None) is None or not numbered_model(instance.__class__):
        return ''
    alias = using or entity_alias(instance)
    label, entity_id = entity_key(instance)
    manager = EntityNumber.objects.using(alias)
    existing = manager.filter(entity_type=label, entity_id=entity_id).first()
    if existing:
        cache_store()[(alias, label, entity_id)] = existing.code
        return existing.code
    prefix = prefix or entity_prefix_for()
    try:
        for _ in range(32):
            code = prefix + ''.join(secrets.choice(ALPHABET) for _ in range(10))
            try:
                with transaction.atomic(using=alias):
                    row = manager.create(code=code, entity_type=label, entity_id=entity_id)
                cache_store()[(alias, label, entity_id)] = row.code
                return row.code
            except IntegrityError:
                existing = manager.filter(entity_type=label, entity_id=entity_id).first()
                if existing:
                    cache_store()[(alias, label, entity_id)] = existing.code
                    return existing.code
        raise RuntimeError('暂时无法生成唯一实体编号，请重试')
    except (OperationalError, ProgrammingError) as exc:
        # Historical business migrations may save models before this new table exists.
        if 'identifiers_entitynumber' in str(exc):
            return ''
        raise


def entity_ids_for_code(value, entity_type, exact=False):
    """Resolve new public codes without removing legacy identifier lookups."""
    normalized = str(value or '').strip().upper()
    if not normalized:
        return []
    manager = EntityNumber.objects.filter(entity_type=entity_type)
    rows = manager.filter(code=normalized) if exact else manager.filter(code__icontains=normalized)
    return list(rows.values_list('entity_id', flat=True))


def normalize_entity_numbers(prefix, using):
    """Repair pre-migration codes whose prefix or suffix no longer matches the store."""
    manager = EntityNumber.objects.using(using)
    repaired = 0
    for row in manager.all().iterator():
        if re.fullmatch(re.escape(prefix) + r'[A-Z0-9]{10}', row.code):
            continue
        for _ in range(32):
            candidate = prefix + ''.join(secrets.choice(ALPHABET) for _ in range(10))
            try:
                with transaction.atomic(using=using):
                    row.code = candidate
                    row.save(update_fields=['code'])
                repaired += 1
                break
            except IntegrityError:
                continue
        else:
            raise RuntimeError('无法修复历史实体编号，请重试')
    return repaired
