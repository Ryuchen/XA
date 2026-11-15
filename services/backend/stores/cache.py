"""Separate tenant cache keys from each other and the platform control plane."""
from .context import current_store


def make_key(key, key_prefix, version):
    store = current_store()
    if store is None:
        return f'{key_prefix}:{version}:platform:{key}'
    slug = store.slug
    return f'{key_prefix}:{version}:store:{slug}:{key}'
