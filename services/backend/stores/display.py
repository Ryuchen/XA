"""Store-owned presentation labels; never alter the underlying account name."""
import re

from .context import current_store


def provider_display_name(name):
    name = name.strip() if isinstance(name, str) else ''
    store = current_store()
    prefix = getattr(store, 'provider_name_prefix', '') if store else ''
    if not name or not isinstance(prefix, str) or not re.fullmatch(r'[A-Z]{2,8}', prefix, flags=re.ASCII):
        return name
    marker = prefix + '-'
    while name[:len(marker)].upper() == marker:
        name = name[len(marker):]
    return marker + name
