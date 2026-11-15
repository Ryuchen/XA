"""Platform-controlled per-tier value-added entitlements."""
from django.db import transaction

from .license_policy import FEATURE_DEFAULTS, TIERS, validate_feature_overrides
from .models import LicenseTierFeatureOverride, PlatformAuditEvent, Store, StoreLicenseCode
from .platform_accounts import _locked_actor


def tier_features(tier):
    if tier not in TIERS:
        raise ValueError('授权档位无效')
    index = ('BASIC', 'PRO', 'ULTRA').index(tier)
    overrides = dict(LicenseTierFeatureOverride.objects.filter(tier=tier).values_list('feature', 'mode'))
    # Fail closed if directory data was modified outside this service.
    validate_feature_overrides(overrides)
    return {feature: {'mode': overrides.get(feature, defaults[index]), 'default_mode': defaults[index],
                      'overridden': feature in overrides} for feature, defaults in FEATURE_DEFAULTS.items()}


def change_feature(token, *, tier, feature, mode):
    if tier not in TIERS or feature not in FEATURE_DEFAULTS:
        raise ValueError('档位或增值功能无效')
    if mode is not None:
        validate_feature_overrides({feature: mode})
    with transaction.atomic(using='default'):
        actor = _locked_actor(token)
        row = LicenseTierFeatureOverride.objects.select_for_update().filter(tier=tier, feature=feature).first()
        previous = row.mode if row else None
        refresh_stores = ()
        if previous != mode:
            from .license_snapshot import prepare_policy_cache_refresh
            eligible = StoreLicenseCode.objects.filter(tier=tier, activated_at__isnull=False,
                                                       voided_at__isnull=True).values('store_id')
            stores = Store.objects.select_for_update().filter(pk__in=eligible, initialized_at__isnull=False).order_by('pk')
            refresh_stores = prepare_policy_cache_refresh(stores)
        if mode is None:
            if row:
                row.delete()
        elif row:
            row.mode, row.updated_by = mode, actor
            row.save(update_fields=['mode', 'updated_by', 'updated_at'])
        else:
            LicenseTierFeatureOverride.objects.create(tier=tier, feature=feature, mode=mode, updated_by=actor)
        PlatformAuditEvent.objects.create(account=actor, action='license.tier_feature_changed',
            target_type='license_tier_feature', target_id=f'{tier}:{feature}', detail={'before': previous, 'after': mode})
        if refresh_stores:
            from .license_snapshot import refresh_policy_caches
            transaction.on_commit(lambda: refresh_policy_caches(refresh_stores), using='default', robust=True)
