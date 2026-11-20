"""Verified per-store License snapshots for a temporarily unavailable control DB.

Routing establishes the store separately, either online or from a verified
store-routing snapshot and trusted hostname binding. Snapshot writes happen
after both control and business commits, so a cache row never reverses
business locks.
"""
import hmac
import json
import logging
from copy import deepcopy
from types import SimpleNamespace

from django.conf import settings
from django.db import DatabaseError, InterfaceError, transaction as directory_transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.dateparse import parse_datetime

from .context import current_database, current_store, store_context
from .license_policy import (FEATURE_DEFAULTS, TIERS, LicenseGrant, evaluate_scheduled,
                             feature_mode, validate_feature_overrides)

logger = logging.getLogger(__name__)
SALT = 'xa.store-license.snapshot.v1'


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _signature(payload, secret):
    return salted_hmac(SALT, _canonical(payload), secret=secret, algorithm='sha256').hexdigest()


def _parse_time(value):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or timezone.is_naive(parsed):
        raise ValueError('授权缓存时间无效')
    return parsed


def _validate_row(row, store, *, now):
    if (row is None or row.store_slug != store.slug or not isinstance(row.payload, dict)
            or not isinstance(row.signature, str) or len(row.signature) != 64):
        return None
    try:
        secrets = [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', [])]
        if not any(hmac.compare_digest(row.signature, _signature(row.payload, secret)) for secret in secrets):
            return None
        payload = row.payload
        if (set(payload) != {'version', 'store_slug', 'checked_at', 'grace_days', 'current', 'scheduled', 'tier_modes'}
                or payload['version'] != 1 or payload['store_slug'] != store.slug
                or type(payload['grace_days']) is not int or not 0 <= payload['grace_days'] <= 999999999
                or not isinstance(payload['tier_modes'], dict)):
            return None
        checked = _parse_time(payload['checked_at'])
        if row.checked_at != checked or checked > now:
            return None
        for terms in (payload['current'], payload['scheduled']):
            if terms is None:
                continue
            if (not isinstance(terms, dict) or set(terms) != {'id', 'kind', 'tier', 'starts_at', 'ends_at', 'effective_at'}
                    or not isinstance(terms['id'], str) or terms['kind'] not in ('INITIAL', 'RENEW', 'CHANGE')
                    or terms['tier'] not in TIERS):
                return None
            start, end, effective = map(_parse_time, (terms['starts_at'], terms['ends_at'], terms['effective_at']))
            if not start <= effective < end:
                return None
            modes = payload['tier_modes'].get(terms['tier'])
            if not isinstance(modes, dict) or set(modes) != set(FEATURE_DEFAULTS):
                return None
            validate_feature_overrides(modes)
        expected_tiers = {terms['tier'] for terms in (payload['current'], payload['scheduled']) if terms}
        if set(payload['tier_modes']) != expected_tiers:
            return None
        return payload
    except (TypeError, ValueError, OverflowError):
        return None


def _evaluate(payload, store, *, now, source, signature):
    grace_seconds = payload['grace_days'] * 86400
    def grant(terms):
        if terms is None:
            return None
        return LicenseGrant(store.slug, terms['tier'], _parse_time(terms['effective_at']),
                            _parse_time(terms['ends_at']), grace_seconds)
    current, scheduled = payload['current'], payload['scheduled']
    decision = evaluate_scheduled(grant(current), grant(scheduled), store_slug=store.slug, now=now)
    overrides = payload['tier_modes'].get(decision.tier.code, {}) if decision.tier else {}
    if scheduled and now >= _parse_time(scheduled['effective_at']):
        current, scheduled = scheduled, None
    return {'status': decision.status, 'tier': decision.tier.code if decision.tier else None,
            'allow_new_accounts': decision.allow_new_accounts, 'allow_new_benefits': decision.allow_new_benefits,
            'can_open_store': decision.can_open_store, 'grace_days': payload['grace_days'],
            'current': current, 'scheduled': scheduled,
            'feature_modes': {key: feature_mode(decision, key, overrides=overrides) for key in FEATURE_DEFAULTS},
            'license_source': source, 'snapshot_checked_at': payload['checked_at'],
            'verified_snapshot': {'payload': payload, 'signature': signature}}


def invalidate_for_cancellation(store):
    """Fail closed before committing cancellation of an activated future grant.

    Caller holds the control-store row lock. A separate tenant commit can
    survive a later control rollback; that only removes offline admission,
    never core-business access, until the next successful online refresh.
    """
    from club_accounts.models import LicenseStatusSnapshot
    alias = store.database_alias
    with store_context(store), directory_transaction.atomic(using=alias):
        row = LicenseStatusSnapshot.objects.using(alias).select_for_update().filter(pk=1).first()
        if row is not None:
            row.signature = ''
            row.save(using=alias, update_fields=['signature'])


def prepare_policy_cache_refresh(stores):
    """Invalidate affected snapshots before a platform policy is committed."""
    if not getattr(settings, 'LICENSE_SNAPSHOT_CACHE_ENABLED', True):
        return ()
    targets = tuple(store for store in stores if store.initialized_at)
    for store in targets:
        invalidate_for_cancellation(store)
    return targets


def refresh_policy_caches(stores):
    """Refresh independently after the control transaction has committed."""
    for store in stores:
        _persist_after_commit(store, None, None)


def _persist_after_commit(store, payload, signature):
    from club_accounts.models import LicenseStatusSnapshot
    alias = store.database_alias
    try:
        # Cancellation takes this same control lock before invalidating the
        # tenant snapshot. Re-read after obtaining it: an old on-commit
        # callback can never republish a canceled future grant.
        from .models import Store
        from .license_status import status_for_store
        from .license_features import tier_features
        with directory_transaction.atomic(using='default'):
            locked = Store.objects.select_for_update().filter(pk=store.pk, slug=store.slug).first()
            if locked is None or locked.database_alias != alias:
                raise ValueError('门店数据库已变化')
            status = status_for_store(locked)
            tiers = {terms['tier'] for terms in (status['current'], status['scheduled']) if terms}
            modes = {tier: {key: value['mode'] for key, value in tier_features(tier).items()} for tier in tiers}
            checked = timezone.now()
            payload = json.loads(_canonical({'version': 1, 'store_slug': locked.slug,
                'checked_at': checked.isoformat(), 'grace_days': status['grace_days'],
                'current': status['current'], 'scheduled': status['scheduled'], 'tier_modes': modes}))
            signature = _signature(payload, settings.SECRET_KEY)
            candidate = SimpleNamespace(store_slug=locked.slug, payload=payload, signature=signature, checked_at=checked)
            if _validate_row(candidate, locked, now=timezone.now()) is None:
                raise ValueError('授权缓存候选不完整或签名无效')
            with store_context(locked), directory_transaction.atomic(using=alias):
                LicenseStatusSnapshot.objects.using(alias).get_or_create(pk=1)
                row = LicenseStatusSnapshot.objects.using(alias).select_for_update().get(pk=1)
                existing = _validate_row(row, locked, now=timezone.now())
                if existing and _parse_time(existing['checked_at']) > checked:
                    return
                row.store_slug, row.payload, row.checked_at, row.signature = locked.slug, payload, checked, signature
                row.save(using=alias, update_fields=['store_slug', 'payload', 'checked_at', 'signature'])
    except (DatabaseError, InterfaceError, ValueError, TypeError):
        logger.warning('Unable to persist License snapshot for store %s', store.slug, exc_info=True)


def business_status(store):
    """Read online terms first; on a control DB failure use a verified snapshot."""
    if current_store() is None or current_store().slug != store.slug or current_database() != store.database_alias:
        raise ValueError('授权查询与当前门店不一致')
    enabled = getattr(settings, 'LICENSE_SNAPSHOT_CACHE_ENABLED', True)
    try:
        from .license_status import status_for_store
        from .license_features import tier_features
        with directory_transaction.atomic(using='default'):
            status = status_for_store(store)
            if not enabled:
                return {**status, 'license_source': 'ONLINE'}
            tiers = {terms['tier'] for terms in (status['current'], status['scheduled']) if terms}
            modes = {tier: {key: value['mode'] for key, value in tier_features(tier).items()} for tier in tiers}
            checked = timezone.now()
            payload = {'version': 1, 'store_slug': store.slug, 'checked_at': checked.isoformat(),
                       'grace_days': status['grace_days'], 'current': status['current'],
                       'scheduled': status['scheduled'], 'tier_modes': modes}
            payload = json.loads(_canonical(payload))
            signature = _signature(payload, settings.SECRET_KEY)
            result = _evaluate(deepcopy(payload), store, now=checked, source='ONLINE', signature=signature)
    except (DatabaseError, InterfaceError):
        if enabled:
            from club_accounts.models import LicenseStatusSnapshot
            # Keep a failed local cache read from poisoning an outer business
            # transaction when the tenant DB has a transient read error.
            with directory_transaction.atomic(using=store.database_alias):
                row = LicenseStatusSnapshot.objects.using(store.database_alias).filter(pk=1).first()
            now = timezone.now()
            payload = _validate_row(row, store, now=now)
            if payload is not None:
                return _evaluate(payload, store, now=now, source='CACHE', signature=row.signature)
        raise
    # Neither connection may publish data from the other's rolled-back work.
    # Register both callbacks now: if either transaction rolls back its callback
    # is discarded, even when the other transaction commits first.
    committed = {'directory': False, 'tenant': False, 'published': False}

    def after_commit(which):
        committed[which] = True
        if committed['directory'] and committed['tenant'] and not committed['published']:
            committed['published'] = True
            _persist_after_commit(store, payload, signature)

    directory_transaction.on_commit(lambda: after_commit('directory'), using='default', robust=True)
    directory_transaction.on_commit(lambda: after_commit('tenant'), using=store.database_alias, robust=True)
    return {**status, **result}
