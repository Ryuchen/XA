"""Server-owned integration overrides; never inherit another store's recipients."""
import json

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

from .context import current_store


def store_name():
    store = current_store()
    return store.name if store else '声浪电竞'


def parse_store_integrations(raw):
    try:
        values = json.loads(raw or '{}')
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured('XA_STORE_INTEGRATIONS must be a JSON object') from exc
    if (not isinstance(values, dict) or any(
            not isinstance(slug, str) or not isinstance(config, dict)
            or any(not isinstance(key, str) or not key.startswith(
                ('WECHAT_', 'SMS_', 'KOOK_', 'WECOM_', 'AUDITION_')) for key in config)
            for slug, config in values.items())):
        raise ImproperlyConfigured('Invalid per-store integration configuration')
    for config in values.values():
        for key, value in config.items():
            if key.endswith(('_ENABLED', '_TESTING', '_MOCK_LOGIN', '_NEW_ORDERS', '_REJECTED_ORDERS')) and type(value) is not bool:
                raise ImproperlyConfigured('Store integration switches must be JSON booleans')
    return values


class StoreSettings:
    def __getattr__(self, name):
        # Identity providers are platform-wide; allowing a tenant override can
        # also bypass the startup check that forbids mock login in production.
        if name.startswith(('WECHAT_', 'SMS_')):
            return getattr(django_settings, name)
        store = current_store()
        if store is not None:
            configured = getattr(django_settings, 'STORE_INTEGRATIONS', {}).get(store.slug, {})
            if name.startswith(('KOOK_', 'WECOM_', 'AUDITION_')) and name in configured:
                return configured[name]
            if store.database_alias != 'default':
                # Existing environment recipients belong only to the original
                # store. A new store must explicitly configure its own channels.
                if name in ('KOOK_ENABLED', 'WECOM_DISPATCH_ENABLED'):
                    return False
                if name in ('KOOK_BOT_TOKEN', 'KOOK_DISPATCH_CHANNEL_ID',
                            'WECOM_DISPATCH_WEBHOOK', 'KOOK_ADMIN_ORDER_URL'):
                    return ''
                if name == 'KOOK_DISPATCH_MENTION_ROLE_IDS':
                    return []
        return getattr(django_settings, name)


settings = StoreSettings()
