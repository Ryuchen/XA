"""Read-only platform order aggregation across explicitly authorized stores.

The projection is deliberately separate from store order serializers: platform
readers must not receive internal remarks, customer contact details, amounts,
wallet evidence, or settlement breakdowns through this API.
"""
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from heapq import merge
from itertools import islice

from django.db import DatabaseError, InterfaceError
from django.db.models import Case, CharField, Exists, OuterRef, Q, Subquery, Value, When
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from orders.direct_payment_models import DirectPaymentIntent
from orders.models import AfterSale, Order, OrderStatusLog, Reservation
from orders.pool_visibility import pending_deadline_expired
from wallet.settlement_display import order_settlement_status, prepare_order_settlement_display

from .context import store_context
from .display import provider_display_name
from .dynamic_database import discover_store_connection
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication, account_for_token
from .platform_permissions import platform_stores_for
from .platform_views import admin_origin, fail

ORDERS_ACTION = 'orders:view'
ORDER_SOURCES = frozenset({'STAFF_MANUAL', 'BOSS_H5', 'RESERVATION', 'GIFT'})
ORDER_STATUSES = frozenset(Order.Status.values)
PAYMENT_STATUSES = frozenset(Order.PaymentStatus.values)
MAX_PAGE = 100000
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100000


class OrderQueryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class StoreLookupError(LookupError):
    pass


class StoreReadFailure(Exception):
    def __init__(self, store):
        super().__init__(store.slug)
        self.store = store


@dataclass(frozen=True)
class ListQuery:
    page: int
    page_size: int
    store_slug: str | None
    source: str | None
    status: str | None
    payment: str | None
    keyword: str | None


def _one_param(params, name, *, maximum=None):
    values = params.getlist(name)
    if len(values) > 1:
        raise OrderQueryError(f'{name} 不能重复')
    if not values:
        return None
    value = values[0]
    if maximum is not None and len(value) > maximum:
        raise OrderQueryError(f'{name} 过长')
    return value


def _positive_int(params, name, default, maximum):
    raw = _one_param(params, name, maximum=20)
    if raw is None:
        return default
    if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= maximum:
        raise OrderQueryError(f'{name} 无效')
    return int(raw)


def parse_list_query(params):
    allowed = {'page', 'page_size', 'store', 'source', 'status', 'payment', 'keyword'}
    unknown = set(params.keys()) - allowed
    if unknown:
        raise OrderQueryError(f'不支持的查询参数：{sorted(unknown)[0]}')

    page = _positive_int(params, 'page', 1, MAX_PAGE)
    page_size = _positive_int(params, 'page_size', 20, MAX_PAGE_SIZE)
    if (page - 1) * page_size > MAX_OFFSET:
        raise OrderQueryError('页码超出查询范围，请使用筛选条件缩小结果')
    store_slug = _one_param(params, 'store', maximum=48)
    if store_slug is not None and not store_slug:
        raise OrderQueryError('store 无效')

    enum_values = {}
    for name, choices in (('source', ORDER_SOURCES), ('status', ORDER_STATUSES), ('payment', PAYMENT_STATUSES)):
        value = _one_param(params, name, maximum=32)
        if value is not None and value not in choices:
            raise OrderQueryError(f'{name} 无效')
        enum_values[name] = value

    keyword = _one_param(params, 'keyword', maximum=100)
    if keyword is not None and (not keyword.strip() or any(ord(char) < 32 for char in keyword)):
        raise OrderQueryError('keyword 无效')
    return ListQuery(page, page_size, store_slug, keyword=keyword, **enum_values)


def _order_source():
    """Derive source from creation facts, never from the dispatch route."""
    create_logs = OrderStatusLog.objects.filter(order_id=OuterRef('pk'), action=OrderStatusLog.Action.CREATE)
    staff_created = Exists(create_logs.filter(operator_account__account_type='STAFF'))
    boss_created = Exists(create_logs.filter(operator_account__account_type='BOSS'))
    reservation_created = Exists(create_logs.filter(reason__startswith='预约转单：'))
    direct_created = Exists(DirectPaymentIntent.objects.filter(order_id=OuterRef('pk')))
    return Case(
        When(Q(is_gift_snapshot=True) | Q(source_order_id__isnull=False), then=Value('GIFT')),
        When(Q(reservation__isnull=False) | reservation_created, then=Value('RESERVATION')),
        When(staff_created, then=Value('STAFF_MANUAL')),
        When(boss_created | direct_created, then=Value('BOSS_H5')),
        default=Value(None),
        output_field=CharField(max_length=16),
    )


def _order_queryset():
    aftersale_status = AfterSale.objects.filter(order_id=OuterRef('pk')).order_by('-created_at', '-pk').values('status')[:1]
    return Order.objects.annotate(
        _platform_source=_order_source(),
        _platform_aftersale_status=Subquery(aftersale_status, output_field=CharField(max_length=24)),
    )


def _filtered_orders(query):
    rows = _order_queryset()
    if query.source is not None:
        rows = rows.filter(_platform_source=query.source)
    if query.status is not None:
        rows = rows.filter(status=query.status)
    if query.payment is not None:
        rows = rows.filter(payment_status=query.payment)
    if query.keyword is not None:
        keyword = query.keyword
        rows = rows.filter(
            Q(order_no__icontains=keyword)
            | Q(customer_account__nickname__icontains=keyword)
            | Q(customer_account__username__icontains=keyword)
            | Q(customer__nickname__icontains=keyword)
            | Q(customer__username__icontains=keyword)
        )
    return rows


def _store_ready(store):
    if not store.initialized_at or not discover_store_connection(store):
        raise ValueError('门店数据尚未就绪')
    return store


def _store_identity(store):
    return (store.pk, store.slug, store.database_alias, store.status, store.initialized_at)


def _identity_name(account, user, snapshot=''):
    if snapshot:
        return str(snapshot)
    if account is not None:
        return account.nickname or account.username or ''
    if user is not None:
        return user.nickname or user.username or ''
    return ''


def _provider_names(order):
    names, seen = [], set()

    def add(identity, raw_name):
        if identity in seen:
            return
        seen.add(identity)
        name = provider_display_name(raw_name or '')
        if name and name not in names:
            names.append(name)

    add(('account', order.provider_account_id), _identity_name(
        order.provider_account, order.provider, order.provider_name_snapshot,
    ))
    add(('user', order.provider_id), _identity_name(None, order.provider, order.provider_name_snapshot))
    for share in order.providers.all():
        add(('account', share.provider_account_id), _identity_name(
            share.provider_account, share.provider, share.provider_name_snapshot,
        ))
        add(('user', share.provider_id), _identity_name(None, share.provider, share.provider_name_snapshot))
    return names


def _iso(value):
    return value.isoformat() if value is not None else None


def _serialize_order(order, store):
    aftersale_status = getattr(order, '_platform_aftersale_status', None)
    settlement_status = order_settlement_status(order)
    payment_display = {'kind': 'DIRECT_TEST'} if getattr(order, '_display_has_direct', False) else None
    return {
        'id': str(order.pk),
        'store_slug': store.slug,
        'store_name': store.name,
        'order_no': order.order_no,
        'service_name': order.service_name_snapshot or (order.service.name if order.service_id else ''),
        'customer_name': _identity_name(order.customer_account, order.customer),
        'provider_names': _provider_names(order),
        'currency_name': store.currency_name,
        'version': order.updated_at.isoformat() if order.updated_at else '',
        'allowed_actions': [],
        'status': order.status,
        'payment_status': order.payment_status,
        'payment_display': payment_display,
        'dispatch_route': order.dispatch_route,
        'order_source': getattr(order, '_platform_source', None),
        'requires_boss_selection': order.requires_boss_selection,
        'pending_deadline_expired': pending_deadline_expired(order),
        'aftersale_status': aftersale_status or None,
        'settlement_status': settlement_status,
        'created_at': _iso(order.created_at),
        'grabbed_at': _iso(order.grabbed_at),
        'started_at': None,
        'in_service_at': _iso(order.in_service_at),
        'completed_at': _iso(order.completed_at),
        'cancelled_at': _iso(order.cancelled_at),
        'updated_at': _iso(order.updated_at),
    }


def _loaded_orders(rows):
    return list(_order_queryset().filter(pk__in=rows).select_related(
        'customer', 'customer_account', 'provider', 'provider_account', 'service',
    ).prefetch_related(
        'providers__provider', 'providers__provider_account', 'refund_records',
    ))


def _count_store(store, query):
    with store_context(store):
        try:
            _store_ready(store)
            return _filtered_orders(query).count()
        except (DatabaseError, InterfaceError, ValueError) as error:
            raise StoreReadFailure(store) from error


def _datetime_key(value):
    if timezone.is_naive(value):
        value = value.replace(tzinfo=datetime_timezone.utc)
    delta = value.astimezone(datetime_timezone.utc) - datetime(1970, 1, 1, tzinfo=datetime_timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _merge_key(key):
    created_at, order_id, store_slug = key
    return (-_datetime_key(created_at), -order_id, store_slug)


def _iter_store_keys(store, query):
    with store_context(store):
        try:
            _store_ready(store)
            rows = _filtered_orders(query).order_by('-created_at', '-pk').values_list('created_at', 'pk')
            for created_at, order_id in rows.iterator(chunk_size=500):
                yield created_at, order_id, store.slug
        except (DatabaseError, InterfaceError, ValueError) as error:
            raise StoreReadFailure(store) from error


def _load_store_page(store, order_ids):
    with store_context(store):
        try:
            _store_ready(store)
            orders = _loaded_orders(order_ids)
            by_id = {order.pk: order for order in orders}
            if len(by_id) != len(order_ids):
                raise OrderQueryError('订单列表已变化，请重试', 409)
            prepare_order_settlement_display(orders, order_list_only=True)
            return {order.pk: _serialize_order(order, store) for order in orders}
        except (DatabaseError, InterfaceError, ValueError) as error:
            if isinstance(error, OrderQueryError):
                raise
            raise StoreReadFailure(store) from error


def _authorized_stores(account, store_slug):
    stores = list(platform_stores_for(account, ORDERS_ACTION).order_by('slug'))
    if not stores:
        raise PermissionError('无平台订单查看权限')
    if store_slug is None:
        return stores, stores
    selected = [store for store in stores if store.slug == store_slug]
    if not selected:
        raise StoreLookupError()
    return stores, selected


def _revalidate(account, token, required_stores, *, exact):
    actor = account_for_token(token)
    current = list(platform_stores_for(actor, ORDERS_ACTION))
    if not current:
        raise PermissionError('无平台订单查看权限')
    visible = {_store_identity(store) for store in current}
    required = {_store_identity(store) for store in required_stores}
    if not required.issubset(visible) or (exact and visible != required):
        raise PermissionError('平台订单查看权限已变化')
    return actor


def aggregate_orders(account, token, query):
    authorized, selected = _authorized_stores(account, query.store_slug)
    records, missing = [], []
    for store in selected:
        try:
            records.append({'store': store, 'count': _count_store(store, query)})
        except StoreReadFailure:
            missing.append(store.slug)

    # A failure can also occur while streaming keys or loading the selected
    # page. Remove only that store and restart against the remaining stores.
    while True:
        results = []
        total = sum(record['count'] for record in records)
        start = (query.page - 1) * query.page_size
        if start >= total and not (total == 0 and query.page == 1):
            if missing:
                break
            raise OrderQueryError('page 越界')

        active = [record for record in records if record['count']]

        try:
            streams = [_iter_store_keys(record['store'], query) for record in active]
            keys = list(islice(merge(*streams, key=_merge_key), start, start + query.page_size))
            grouped = {}
            for _, order_id, store_slug in keys:
                grouped.setdefault(store_slug, []).append(order_id)
            loaded = {}
            for record in active:
                order_ids = grouped.get(record['store'].slug, [])
                if order_ids:
                    loaded[record['store'].slug] = _load_store_page(record['store'], order_ids)
            results = [loaded[store_slug][order_id] for _, order_id, store_slug in keys]
        except StoreReadFailure as error:
            records = [record for record in records if record['store'].pk != error.store.pk]
            if error.store.slug not in missing:
                missing.append(error.store.slug)
            continue
        break

    _revalidate(account, token, authorized, exact=True)
    return {
        'results': results,
        'total': total,
        'complete': not missing,
        'generated_at': timezone.now().isoformat(),
        'missing_stores': sorted(missing),
    }


def _order_events(order):
    events = []
    logs = order.status_logs.select_related('operator', 'operator_account').order_by('created_at', 'pk')
    for log in logs:
        actor = _identity_name(log.operator_account, log.operator)
        events.append({
            'id': str(log.pk),
            'at': log.created_at.isoformat() if log.created_at else '',
            'title': log.get_action_display(),
            'actor': actor or None,
        })
    return events


def read_order_detail(account, token, store, order_id):
    with store_context(store):
        try:
            _store_ready(store)
            order = _loaded_orders([order_id])
            if len(order) != 1:
                raise StoreLookupError()
            row = order[0]
            prepare_order_settlement_display([row], order_list_only=True)
            result = _serialize_order(row, store)
            result['events'] = _order_events(row)
        except StoreLookupError:
            raise
        except (DatabaseError, InterfaceError, ValueError) as error:
            raise StoreReadFailure(store) from error
    _revalidate(account, token, [store], exact=False)
    return result


class PlatformOrderView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response

    def handle_exception(self, exc):
        if isinstance(exc, OrderQueryError):
            return fail(str(exc), exc.status, 'PLATFORM_ORDERS_INVALID')
        return super().handle_exception(exc)

    def _unavailable(self):
        return fail('平台订单服务暂不可用，请稍后重试', 503, 'PLATFORM_UNAVAILABLE')


class PlatformOrdersView(PlatformOrderView):
    def get(self, request):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        try:
            query = parse_list_query(request.query_params)
            data = aggregate_orders(request.platform_account, request.auth, query)
        except OrderQueryError as error:
            return fail(str(error), error.status, 'PLATFORM_ORDERS_INVALID')
        except PermissionError:
            return fail('无平台订单查看权限', 403, 'PLATFORM_ORDERS_FORBIDDEN')
        except StoreLookupError:
            return fail('门店不存在或无权查看', 404, 'PLATFORM_ORDERS_NOT_FOUND')
        except TokenError as error:
            raise AuthenticationFailed('平台登录已失效，请重新登录') from error
        except (DatabaseError, InterfaceError):
            return self._unavailable()
        return Response({'code': 0, 'data': data})


class PlatformOrderDetailView(PlatformOrderView):
    def get(self, request, slug, order_id):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        try:
            _, stores = _authorized_stores(request.platform_account, slug)
            data = read_order_detail(request.platform_account, request.auth, stores[0], order_id)
        except PermissionError:
            return fail('无平台订单查看权限', 403, 'PLATFORM_ORDERS_FORBIDDEN')
        except (StoreLookupError, Order.DoesNotExist):
            return fail('订单不存在或不属于该门店', 404, 'PLATFORM_ORDERS_NOT_FOUND')
        except StoreReadFailure:
            return self._unavailable()
        except TokenError as error:
            raise AuthenticationFailed('平台登录已失效，请重新登录') from error
        except (DatabaseError, InterfaceError):
            return self._unavailable()
        return Response({'code': 0, 'data': data})
