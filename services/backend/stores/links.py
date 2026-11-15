"""Keep the tenant in links opened without API request headers."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .context import current_store
from .hosts import store_entry_url


def store_url(url):
    if not url:
        return url
    store = current_store()
    if store is None:
        return url
    return store_entry_url(url, store.slug)


def route_query_url(url, **values):
    parts = urlsplit(url)
    if parts.fragment:
        route, separator, query = parts.fragment.partition('?')
        route_query = dict(parse_qsl(query, keep_blank_values=True))
        route_query.update(values)
        return urlunsplit(parts._replace(fragment=route + '?' + urlencode(route_query)))
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit(parts._replace(query=urlencode(query)))


def audition_url(base, token, role):
    return route_query_url(store_url(base), token=str(token), role=role)
