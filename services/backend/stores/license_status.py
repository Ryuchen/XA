"""Authoritative online license status; does not yet provide offline caching."""
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from .license_policy import FEATURE_DEFAULTS, LicenseGrant, evaluate_scheduled, feature_mode
from .models import PlatformAuditEvent, PlatformLicenseSettings, Store, StoreLicenseCode, StoreLicenseSettings
from .platform_accounts import _locked_actor

MAX_GRACE_DAYS = 999999999


def status_for_store(store, *, now=None):
    now = now or timezone.now()
    default_days = PlatformLicenseSettings.objects.filter(pk=1).values_list('default_grace_days', flat=True).first()
    default_days = 7 if default_days is None else default_days
    override = StoreLicenseSettings.objects.filter(store=store).values_list('grace_days_override', flat=True).first()
    grace_days = default_days if override is None else override
    activated = (StoreLicenseCode.objects.filter(store=store, activated_at__isnull=False, voided_at__isnull=True)
                 .annotate(effective_at=Greatest(F('starts_at'), F('activated_at'))))
    current = activated.filter(effective_at__lte=now).order_by('-effective_at', '-activated_at', '-id').first()
    future = activated.filter(effective_at__gt=now).order_by('effective_at', 'activated_at', 'id').first()

    def grant(row):
        return None if row is None else LicenseGrant(store.slug, row.tier, row.effective_at, row.ends_at, grace_days * 86400)

    decision = evaluate_scheduled(grant(current), grant(future), store_slug=store.slug, now=now)
    from .license_features import tier_features
    configured = tier_features(decision.tier.code) if decision.tier else {}
    overrides = {feature: value['mode'] for feature, value in configured.items() if value['overridden']}
    return {'status': decision.status, 'tier': decision.tier.code if decision.tier else None,
            'allow_new_accounts': decision.allow_new_accounts, 'allow_new_benefits': decision.allow_new_benefits,
            'can_open_store': decision.can_open_store, 'grace_days': grace_days, 'default_grace_days': default_days,
            'grace_days_override': override,
            'current': _terms(current), 'scheduled': _terms(future),
            'feature_modes': {feature: feature_mode(decision, feature, overrides=overrides) for feature in FEATURE_DEFAULTS}}


def _terms(row):
    if row is None:
        return None
    return {'id': str(row.pk), 'kind': row.kind, 'tier': row.tier,
            'starts_at': row.starts_at.isoformat(), 'ends_at': row.ends_at.isoformat(),
            'effective_at': row.effective_at.isoformat()}


def change_grace(token, *, days, store_id=None):
    if ((days is None and store_id is None) or (days is not None and
            (type(days) is not int or not 0 <= days <= MAX_GRACE_DAYS))):
        raise ValueError('宽限期须为非负整数天')
    with transaction.atomic(using='default'):
        actor = _locked_actor(token)
        refresh_stores = ()
        if store_id is None:
            row, _ = PlatformLicenseSettings.objects.select_for_update().get_or_create(pk=1)
            before = row.default_grace_days
            if before != days:
                from .license_snapshot import prepare_policy_cache_refresh
                overrides = StoreLicenseSettings.objects.filter(grace_days_override__isnull=False).values('store_id')
                stores = Store.objects.select_for_update().filter(initialized_at__isnull=False).exclude(pk__in=overrides).order_by('pk')
                refresh_stores = prepare_policy_cache_refresh(stores)
            row.default_grace_days, row.updated_by = days, actor
            row.save(update_fields=['default_grace_days', 'updated_by', 'updated_at'])
            slug, target = '', 'platform'
        else:
            store = Store.objects.select_for_update().filter(pk=store_id).first()
            if store is None:
                raise ValueError('门店不存在')
            row = StoreLicenseSettings.objects.select_for_update().filter(store=store).first()
            before = row.grace_days_override if row else None
            if before != days:
                from .license_snapshot import prepare_policy_cache_refresh
                refresh_stores = prepare_policy_cache_refresh([store])
            if row is None:
                row = StoreLicenseSettings.objects.create(store=store, grace_days_override=days, updated_by=actor)
            else:
                row.grace_days_override, row.updated_by = days, actor
                row.save(update_fields=['grace_days_override', 'updated_by', 'updated_at'])
            slug, target = store.slug, str(store.pk)
        PlatformAuditEvent.objects.create(account=actor, action='license.grace_changed', target_store_slug=slug,
            target_type='license_settings', target_id=target, detail={'before': before, 'after': days})
        if refresh_stores:
            from .license_snapshot import refresh_policy_caches
            transaction.on_commit(lambda: refresh_policy_caches(refresh_stores), using='default', robust=True)
        from .license_notices import _wake_after_commit
        _wake_after_commit()
