"""Server-owned store domains shared by HTTP and WebSocket handshakes."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.http.request import split_domain_port, validate_host

from .context import StoreNotFound


DEFAULT_HOST_BINDINGS = {'sl.esports.club': 'shenglang', 'xa.esports.club': 'xinan'}
STORE_SLUG = re.compile(r'[a-z][a-z0-9-]{0,47}')


def is_admin_host(host):
    domain, _ = split_domain_port(host.lower())
    return domain == getattr(settings, 'ADMIN_STORE_HOST', 'admin.esports.club')


def host_store_slug(host):
    domain, _ = split_domain_port(host.lower())
    if is_admin_host(host):
        return None
    bindings = getattr(settings, 'STORE_HOST_BINDINGS', DEFAULT_HOST_BINDINGS)
    # Deployment-owned bootstrap names cannot be rebound or revoked by the
    # tenant-domain API; they remain stable during a directory outage.
    if domain in bindings:
        return bindings[domain]
    from django.db import DatabaseError
    from .models import StoreDomain
    try:
        row = StoreDomain.objects.select_related('store').filter(hostname=domain).first()
    except DatabaseError:
        from .routing_snapshot import lookup_domain
        store = lookup_domain(domain)
        if store is None:
            raise
        return store.slug
    if row:
        if not row.active:
            raise StoreNotFound('门店域名已停用')
        return row.store.slug
    raise StoreNotFound('门店域名未配置，请使用所属门店入口')


def request_store_slug(host, header=None, query_values=()):
    """Explicit hints may confirm a bound host, but can never replace it.

    Unbound local/deployment hosts retain the existing selectors for isolated
    tooling and the minimal WeChat bridge. Deployment ALLOWED_HOSTS still gates
    HTTP and WebSocket requests before this compatibility path is used.
    """
    bound = host_store_slug(host)
    if bound is None and not is_admin_host(host):
        raise StoreNotFound('门店域名未配置，请使用所属门店入口')
    query_values = list(query_values)
    query = query_values[0] if query_values else None
    if len(set(query_values)) > 1 or (header is not None and query is not None and header != query):
        raise ValueError('请求中的门店标识不一致，请重新打开门店链接')
    selected = header if header is not None else query
    if selected is not None and not STORE_SLUG.fullmatch(selected):
        raise ValueError('门店标识格式不正确，请检查门店链接')
    if bound is not None and selected is not None and selected != bound:
        raise ValueError('当前域名与门店标识不一致，请使用所属门店入口')
    return bound if bound is not None else selected


def websocket_store_slug(scope, params):
    headers = scope.get('headers', [])
    hosts = [value for key, value in headers if key.lower() == b'host']
    selectors = [value for key, value in headers if key.lower() == b'x-store-slug']
    if len(hosts) > 1 or len(selectors) > 1:
        raise ValueError('请求中的门店标识不一致')
    host = hosts[0].decode('ascii') if hosts else ''
    if host:
        domain, _ = split_domain_port(host.lower())
        allowed = settings.ALLOWED_HOSTS or (['.localhost', '127.0.0.1', '[::1]'] if settings.DEBUG else [])
        if not domain or not validate_host(domain, allowed):
            raise StoreNotFound('门店域名未配置')
    elif scope.get('server') is not None:
        # Real network scopes require Host. Historical in-process ASGI fixtures
        # without a transport server retain their signed-token test contract.
        raise StoreNotFound('缺少门店域名')
    header = selectors[0].decode('ascii') if selectors else None
    return request_store_slug(host, header, params.get('store', []))


def store_entry_url(value, slug):
    """Return a store-owned link or no link while its hostname is unbound."""
    if not value:
        return ''
    from django.db import DatabaseError
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username or parts.password):
            return ''
        query = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True) if key != 'store']
        if is_admin_host(parts.hostname):
            return urlunsplit(parts._replace(query=urlencode(query)))
        try:
            bound = host_store_slug(parts.hostname)
        except StoreNotFound:
            bound = None
        if bound == slug:
            return urlunsplit(parts._replace(query=urlencode(query)))
        bindings = getattr(settings, 'STORE_HOST_BINDINGS', DEFAULT_HOST_BINDINGS)
        domain = next((host for host, owner in sorted(bindings.items()) if owner == slug), None)
        if not domain:
            from .models import StoreDomain
            domain = (StoreDomain.objects.filter(store__slug=slug, active=True)
                      .order_by('hostname').values_list('hostname', flat=True).first())
        if not domain:
            return ''
        return urlunsplit(parts._replace(netloc=domain, query=urlencode(query)))
    except (DatabaseError, ValueError, UnicodeError):
        return ''
