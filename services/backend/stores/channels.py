"""Store-aware groups over the existing Redis or in-memory channel layer."""
from hashlib import sha256

from django.conf import settings
from django.utils.module_loading import import_string

from .context import current_store


# Daphne only allows application-initiated closes with 1000 or codes 3000–4999.
STORE_UNAVAILABLE_CLOSE_CODE = 4503


class StoreChannelLayer:
    def __init__(self, backend=None, config=None, **kwargs):
        configured = getattr(settings, 'STORE_CHANNEL_LAYER', {})
        backend = backend or configured.get('BACKEND', 'channels.layers.InMemoryChannelLayer')
        if backend == 'stores.channels.StoreChannelLayer':
            raise ValueError('Store channel layer cannot wrap itself')
        options = {**configured.get('CONFIG', {}), **(config or {}), **kwargs}
        self.backend = import_string(backend)(**options)

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def _group(self, group):
        store = current_store()
        if store is None:
            return 'platform.' + sha256(group.encode()).hexdigest()
        slug = store.slug
        # Both the namespace and the group are hashed to stay below Redis'
        # 100-character group limit, even for long account IDs / store slugs.
        return 'store.' + sha256(f'{slug}\0{group}'.encode()).hexdigest()

    async def group_add(self, group, channel):
        return await self.backend.group_add(self._group(group), channel)

    async def group_discard(self, group, channel):
        return await self.backend.group_discard(self._group(group), channel)

    async def group_send(self, group, message):
        return await self.backend.group_send(self._group(group), message)
