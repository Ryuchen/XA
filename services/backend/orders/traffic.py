"""共享缓存的原子固定窗口计数；交易、报价、订单池各自使用配额。"""
import math
import time
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import APIException, Throttled
from rest_framework.throttling import BaseThrottle

from .traffic_models import OrderTrafficConfig

CONFIG_KEY = 'xa:order-traffic:config:v1'
FIELDS = ('enabled', 'self_order_enabled', 'peak_start', 'peak_end', 'valley_start', 'valley_end', 'limits', 'revision')


class TrafficUnavailable(APIException):
    status_code = 503
    default_detail = '订单服务繁忙，请稍后重试'


def config_data():
    result = cache.get(CONFIG_KEY)
    if result is None:
        row = OrderTrafficConfig.objects.filter(pk=1).first() or OrderTrafficConfig()
        result = {field: getattr(row, field) for field in FIELDS}
        cache.set(CONFIG_KEY, result, 20)
    return result


def in_hours(hour, start, end):
    return start <= hour < end if start < end else hour >= start or hour < end


def current_mode(config):
    hour = timezone.now().astimezone(ZoneInfo('Asia/Shanghai')).hour
    if in_hours(hour, config['peak_start'], config['peak_end']):
        return 'peak'
    if in_hours(hour, config['valley_start'], config['valley_end']):
        return 'valley'
    return 'normal'


def atomic_count(key, amount=1, ttl=125):
    # Redis add / INCR 和 LocMemCache 的锁都保证单个计数不会被并发覆盖。
    if cache.add(key, amount, timeout=ttl):
        return amount
    try:
        return cache.incr(key, amount)
    except ValueError:
        if cache.add(key, amount, timeout=ttl):
            return amount
        return cache.incr(key, amount)


def metric(name, amount=1):
    return atomic_count(f'xa:order-metric:{int(time.time() // 60)}:{name}', amount, 960)


def metrics():
    minute = int(time.time() // 60)
    names = ['network_reports', 'latency_sum', 'jitter_sum', 'network_failures', 'network_samples']
    names += [f'{action}_{kind}' for action in ('create', 'grab', 'pool', 'quote') for kind in ('admitted', 'limited')]
    keys = [f'xa:order-metric:{m}:{name}' for m in range(minute - 14, minute + 1) for name in names]
    values = cache.get_many(keys)
    totals = {name: sum(values.get(f'xa:order-metric:{m}:{name}', 0) for m in range(minute - 14, minute + 1)) for name in names}
    reports = totals['network_reports']
    return {**totals, 'window_minutes': 15,
            'average_latency_ms': round(totals['latency_sum'] / reports) if reports else 0,
            'average_jitter_ms': round(totals['jitter_sum'] / reports) if reports else 0,
            'network_failure_percent': round(100 * totals['network_failures'] / totals['network_samples'], 1) if totals['network_samples'] else 0}


class OrderTrafficThrottle(BaseThrottle):
    def allow_request(self, request, view):
        if not getattr(settings, 'ORDER_TRAFFIC_ENABLED', True):
            return True
        action = getattr(view, 'traffic_action', None)
        if action == 'pool' and (request.method != 'GET' or request.query_params.get('status') != 'pending' or request.account.account_type != 'PROVIDER'):
            return True
        if not action:
            return True
        try:
            config = config_data()
            if action == 'create' and request.account.account_type == 'BOSS' and not config['self_order_enabled']:
                exc = APIException('自主下单暂时关闭，请联系客服')
                exc.status_code = 403
                raise exc
            if not config['enabled']:
                return True
            mode = current_mode(config)
            rates = config['limits'][mode][action]
            now = time.time()
            buckets = [('account_per_minute', str(request.account.pk), 60),
                       ('global_per_second', 'global', 1), ('global_per_minute', 'global', 60)]
            for field, identity, period in buckets:
                key = f'xa:order-limit:{action}:{field}:{identity}:{int(now // period)}'
                if atomic_count(key, ttl=period + 5) > rates[field]:
                    metric(action + '_limited')
                    wait = max(1, math.ceil(period - now % period))
                    exc = Throttled(wait=wait, detail=f'当前请求较多，请在 {wait} 秒后重试')
                    exc.traffic_mode = mode
                    exc.traffic_action = action
                    raise exc
            metric(action + '_admitted')
            return True
        except APIException:
            raise
        except Exception as exc:
            raise TrafficUnavailable() from exc
