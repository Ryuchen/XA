"""Evidence and explicit confirmation for selected store setup rules."""
import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction
from orders.capacity import CONFIG_KEY as CAPACITY_KEY, UNCONFIGURED, parse_rules as parse_capacity
from orders.grab_config import CONFIG_KEY as GRAB_WINDOW_KEY, parse_rules as parse_grab_window
from orders.reservation_deadlines import CONFIG_KEY as RESERVATION_KEY, parse_rules as parse_reservation
from orders.room_confirmation_config import CONFIG_KEY as ROOM_CONFIRMATION_KEY, parse_rules as parse_room_confirmation
from users.models import EscortProfile
from wallet.club_rules import DEPOSIT_PERCENT_KEY, DEPOSIT_TARGET_KEY, parse_deposit_targets
from wallet.models import (MIN_WITHDRAW_AMOUNT_KEY, WITHDRAW_TAX_RATE_KEY, SystemConfig,
    PASS_DAILY_PRICE_KEY_PREFIX, PASS_DELAY_SECONDS_KEY_PREFIX)
from wallet.settlement_clocks import CONFIG_KEY as CLOCK_KEY, parse_rules as parse_clocks
from wallet.withdraw_rules import BALANCE_THRESHOLD_KEY, MAX_AMOUNT
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions

CONFIRM_RESOURCE = 'store-setup-confirmation'
PASS_TIERS = tuple(EscortProfile.PassTier.choices)
PASS_KEYS = tuple(f'{PASS_DAILY_PRICE_KEY_PREFIX}{tier}' for tier, _ in PASS_TIERS) + tuple(
    f'{PASS_DELAY_SECONDS_KEY_PREFIX}{tier}' for tier, _ in PASS_TIERS) + (f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE',)
CONFIRMABLE = {
    'capacity': {'keys': (CAPACITY_KEY,), 'permission': 'dictionary:edit'},
    'grab_window': {'keys': (GRAB_WINDOW_KEY,), 'permission': 'dictionary:edit'},
    'reservation_deadlines': {'keys': (RESERVATION_KEY,), 'permission': 'dictionary:edit'},
    'room_confirmation': {'keys': (ROOM_CONFIRMATION_KEY,), 'permission': 'dictionary:edit'},
    'settlement_clocks': {'keys': (CLOCK_KEY,), 'permission': 'deposit_rule:edit'},
    'deposit_targets': {'keys': (DEPOSIT_TARGET_KEY,), 'permission': 'deposit_rule:edit'},
    'deposit_percent': {'keys': (DEPOSIT_PERCENT_KEY,), 'permission': 'deposit_rule:edit'},
    'withdraw': {'keys': (MIN_WITHDRAW_AMOUNT_KEY, BALANCE_THRESHOLD_KEY, WITHDRAW_TAX_RATE_KEY),
                 'permission': 'withdraw:config'},
    'provider_pass': {'keys': PASS_KEYS, 'permission': 'escort:edit'},
    'no_pass_delay': {'keys': (f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE',), 'permission': 'escort:edit'},
    'recharge_packages': {'keys': (), 'permission': 'engagement:edit'},
    'points_award_rates': {'keys': (), 'permission': 'engagement:edit'},
    'gift_pricing': {'keys': (), 'permission': 'service:edit'},
}


def _configured_gifts(*, lock=False):
    """Catalogue entries the gift endpoint can offer now or in future."""
    from orders.models import ServiceItem
    query = ServiceItem.objects.filter(is_active=True, service_category__is_active=True,
        service_category__is_gift=True, purchase_mode='DIRECT', is_addon=False, gameplay__isnull=True
    ).filter(Q(gift_ends_at__isnull=True) | Q(gift_ends_at__gt=timezone.now())).order_by('pk')
    return query.select_for_update() if lock else query


def _snapshot(key, *, lock=False):
    if key == 'gift_pricing':
        from orders.gifts import GIFT_COMMISSION_RATE, GIFT_COMMISSION_THRESHOLD
        gifts = list(_configured_gifts(lock=lock).values('id', 'name', 'price', 'commission_rate',
            'gift_festival', 'gift_starts_at', 'gift_ends_at'))
        if not gifts:
            raise ValueError('当前没有启用的礼物商品')
        for item in gifts:
            if (not 1 <= item['price'] <= 2147483647
                    or (item['commission_rate'] is not None and not 0 <= item['commission_rate'] <= 100)
                    or (item['gift_festival'] and item['price'] < 2100)):
                raise ValueError('礼物价格或抽成无效，请先修改商品')
            for field in ('gift_starts_at', 'gift_ends_at'):
                item[field] = item[field].isoformat() if item[field] else None
        values = {'threshold': GIFT_COMMISSION_THRESHOLD, 'default_rate': GIFT_COMMISSION_RATE, 'gifts': gifts}
        fields = [{'label': '单笔抽成门槛', 'value': str(GIFT_COMMISSION_THRESHOLD)},
                  {'label': '默认抽成比例', 'value': f'{GIFT_COMMISSION_RATE}%'}]
        fields.extend({'label': item['name'], 'value': f"价格 {item['price']}（账务单位）；抽成 "
                       f"{GIFT_COMMISSION_RATE if item['commission_rate'] is None else item['commission_rate']}%"}
                      for item in gifts)
        encoded = json.dumps(values, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()
        return {'fingerprint': hashlib.sha256(encoded).hexdigest(), 'fields': fields}
    if key == 'points_award_rates':
        from engagement.models import ClubOperationConfig, VipTier
        operations = ClubOperationConfig.objects.filter(pk=1)
        if lock:
            operations = operations.select_for_update()
        operation = operations.first()
        if operation is None or not operation.points_enabled:
            raise ValueError('积分发放未启用，无需确认倍率')
        tiers = VipTier.objects.filter(active=True, boss_type__is_active=True).order_by('level', 'pk')
        if lock:
            tiers = tiers.select_for_update()
        values = {'base_points_rate': operation.base_points_rate, 'vip_enabled': operation.vip_enabled,
                  'tiers': list(tiers.values('id', 'level', 'points_rate')) if operation.vip_enabled else []}
        rates = [values['base_points_rate']] + [row['points_rate'] for row in values['tiers']]
        if any(type(rate) is not int or not 0 <= rate <= 1000 for rate in rates):
            raise ValueError('积分发放倍率无效，请先修改配置')
        if operation.vip_enabled and not values['tiers']:
            raise ValueError('VIP 已启用，须配置有效等级及积分倍率')
        fields = [{'label': '普通会员积分倍率', 'value': f"{values['base_points_rate']}%"}]
        fields.extend({'label': f"VIP {row['level']} 积分倍率", 'value': f"{row['points_rate']}%"}
                      for row in values['tiers'])
        encoded = json.dumps(values, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()
        return {'fingerprint': hashlib.sha256(encoded).hexdigest(), 'fields': fields}
    if key == 'recharge_packages':
        from engagement.models import RechargePackage
        rows = RechargePackage.objects.order_by('amount', 'pk')
        if lock:
            rows = rows.select_for_update()
        values = list(rows.values('id', 'amount', 'gift_amount', 'active'))
        if not values:
            raise ValueError('尚未保存预存档位；老板仍可按本店规则充值')
        if any(row['amount'] <= 0 or row['gift_amount'] < 0 for row in values):
            raise ValueError('预存档位无效，请先修改配置')
        from stores.context import current_store
        currency = current_store().currency_name if current_store() else '店币'
        fields = [{'label': f"充值 {format(Decimal(row['amount']) / 100, 'f')} 元",
                   'value': f"赠送 {format(Decimal(row['gift_amount']) / 10, 'f')} {currency}；"
                            + ('启用' if row['active'] else '停用')} for row in values]
        fields.append({'label': '消费积分', 'value': '按付款时会员等级及本店规则计算'})
        encoded = json.dumps(values, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()
        return {'fingerprint': hashlib.sha256(encoded).hexdigest(), 'fields': fields}
    spec = CONFIRMABLE[key]
    queryset = SystemConfig.objects.filter(key__in=spec['keys']).order_by('key')
    if lock:
        queryset = queryset.select_for_update()
    rows = list(queryset.values('id', 'key', 'value', 'updated_at'))
    if len(rows) != len(spec['keys']):
        raise ValueError('配置尚未完整保存，请先填写配置')
    raw = {row['key']: row['value'] for row in rows}
    from stores.context import current_store
    currency = current_store().currency_name if current_store() else '店币'
    def coins(value):
        return format(Decimal(value) / 10, 'f') + f' {currency}'
    if key == 'capacity':
        if raw[CAPACITY_KEY] == UNCONFIGURED:
            raise ValueError('接单名额尚未保存，请先填写配置')
        rules = parse_capacity(raw[CAPACITY_KEY])
        fields = [{'label': '同时进行订单上限', 'value': f"{rules['max_concurrent_orders']} 单"}]
    elif key == 'grab_window':
        rules = parse_grab_window(raw[GRAB_WINDOW_KEY])
        fields = [{'label': '抢单窗口', 'value': f"{rules['window_seconds']} 秒"}]
    elif key == 'reservation_deadlines':
        rules = parse_reservation(raw[RESERVATION_KEY])
        fields = [{'label': '预约确认时限', 'value': f"{rules['confirmation_minutes']} 分钟"},
                  {'label': '预约付款时限', 'value': f"{rules['payment_minutes']} 分钟"},
                  {'label': '时间粒度', 'value': f"{rules['slot_minutes']} 分钟"},
                  {'label': '最短时长', 'value': f"{rules['min_duration_minutes']} 分钟"},
                  {'label': '最长时长', 'value': f"{rules['max_duration_minutes']} 分钟"},
                  {'label': '最长提前期', 'value': f"{rules['max_advance_days']} 天"}]
    elif key == 'room_confirmation':
        rules = parse_room_confirmation(raw[ROOM_CONFIRMATION_KEY])
        fields = [{'label': '房间待老板确认时限', 'value': f"{rules['minutes']} 分钟"}]
    elif key == 'settlement_clocks':
        rules = parse_clocks(raw[CLOCK_KEY])
        fields = [{'label': '售后等待', 'value': f"{rules['aftersale_hours']} 小时"},
                  {'label': '收益冻结', 'value': f"{rules['income_freeze_hours']} 小时"}]
    elif key == 'deposit_targets':
        rules = parse_deposit_targets(raw[DEPOSIT_TARGET_KEY])
        fields = [{'label': '现金押金目标', 'value': coins(rules['cash_target'])},
                  {'label': '单抵押金目标', 'value': coins(rules['earnings_target'])}]
    elif key == 'deposit_percent':
        if not _whole(raw[DEPOSIT_PERCENT_KEY], 100):
            raise ValueError('单抵比例无效，请先修改配置')
        fields = [{'label': '单抵押金比例', 'value': f'{int(raw[DEPOSIT_PERCENT_KEY])}%'}]
    elif key == 'no_pass_delay':
        value = raw[f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE']
        if not _whole(value, 86400):
            raise ValueError('无通行证可见延迟无效，请先修改配置')
        fields = [{'label': '无通行证可见延迟', 'value': f'{value} 秒'}]
    elif key == 'provider_pass':
        prices = [raw[f'{PASS_DAILY_PRICE_KEY_PREFIX}{tier}'] for tier, _ in PASS_TIERS]
        delays = [raw[f'{PASS_DELAY_SECONDS_KEY_PREFIX}{tier}'] for tier, _ in PASS_TIERS]
        delays.append(raw[f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE'])
        if (not all(_whole(value, MAX_AMOUNT) and int(value) > 0 for value in prices)
                or not all(_whole(value, 86400) for value in delays)
                or [int(value) for value in delays] != sorted(int(value) for value in delays)):
            raise ValueError('通行证价格或可见延迟无效，请先修改配置')
        fields = [{'label': name, 'value': f'{coins(price)} / 天；可见延迟 {delay} 秒'}
                  for (_, name), price, delay in zip(PASS_TIERS, prices, delays)]
        fields.append({'label': '无通行证可见延迟', 'value': f'{delays[-1]} 秒'})
    else:
        if not all(_whole(raw[k], 100 if k == WITHDRAW_TAX_RATE_KEY else MAX_AMOUNT) for k in spec['keys']):
            raise ValueError('提现配置无效，请先修改配置')
        fields = [
            {'label': '单笔最低额', 'value': coins(raw[MIN_WITHDRAW_AMOUNT_KEY])},
            {'label': '可用余额须严格大于', 'value': coins(raw[BALANCE_THRESHOLD_KEY])},
            {'label': '提现扣除比例', 'value': f'{int(raw[WITHDRAW_TAX_RATE_KEY])}%'},
        ]
    identity = [{'id': row['id'], 'key': row['key'], 'value': row['value'],
                 'updated_at': row['updated_at'].isoformat()} for row in rows]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True, separators=(',', ':'),
                                       sort_keys=True).encode()).hexdigest()
    return {'fingerprint': digest, 'fields': fields}


def _last_confirmation(key):
    return AdminAuditLog.objects.filter(resource=CONFIRM_RESOURCE, object_id=key).order_by('-id').first()


class SetupEvidenceConfirmationView(APIView):
    """Record an explicitly reviewed snapshot; never changes the actual rule."""
    permission_classes = [IsConsoleUser]

    def _permission(self, request, key):
        permissions = get_account_permissions(request.account)
        spec = CONFIRMABLE.get(key)
        return (spec is not None and bool({'dictionary:view', 'dictionary:edit'}.intersection(permissions))
                and spec['permission'] in permissions)

    def get(self, request, key):
        if not self._permission(request, key):
            return Response({'code': 403, 'msg': '无权确认该项配置'}, status=403)
        try:
            snapshot = _snapshot(key)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        return Response({'code': 0, 'data': snapshot})

    def post(self, request, key):
        if not self._permission(request, key):
            return Response({'code': 403, 'msg': '无权确认该项配置'}, status=403)
        if not isinstance(request.data, Mapping):
            return Response({'code': 400, 'msg': '请先读取并核对当前配置'}, status=400)
        expected = request.data.get('fingerprint')
        if not isinstance(expected, str) or len(expected) != 64:
            return Response({'code': 400, 'msg': '请先读取并核对当前配置'}, status=400)
        try:
            with transaction.atomic():
                if key == 'withdraw':
                    from wallet.withdraw_rules import lock_withdraw_rules
                    lock_withdraw_rules()
                elif key in ('provider_pass', 'no_pass_delay'):
                    from users.pass_rules import lock_pass_config
                    lock_pass_config()
                elif key == 'recharge_packages':
                    from engagement.recharge_pricing import lock_recharge_packages
                    lock_recharge_packages()
                elif key == 'gift_pricing':
                    from .catalog_write_lock import lock_catalog
                    lock_catalog()
                snapshot = _snapshot(key, lock=True)
                if snapshot['fingerprint'] != expected:
                    return Response({'code': 409, 'errorCode': 'SETUP_CONFIG_CHANGED',
                                     'msg': '配置已变化，请重新读取并核对'}, status=409)
                previous = _last_confirmation(key)
                if not previous or not isinstance(previous.request_body, dict) or previous.request_body.get('fingerprint') != expected:
                    AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                        operator_name=(request.account.nickname or request.account.username)[:150],
                        method='POST', path=request.path[:255], resource=CONFIRM_RESOURCE, object_id=key,
                        request_body=snapshot, status_code=200)
        except (ValueError, IntegrityError) as exc:
            return Response({'code': 409, 'msg': str(exc) if isinstance(exc, ValueError) else '配置变化，请重新读取'}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'msg': '配置已确认；修改后需要重新核对'})


def _whole(raw, maximum):
    return (isinstance(raw, str) and raw.isascii() and raw.isdigit()
            and len(raw) <= 10 and int(raw) <= maximum)


def _account_fields_evidence():
    """Check explicit effective rules for saleable service orders, without writing defaults."""
    from orders.account_fields import PREFIX, key as rule_key, parse
    from orders.catalog import visible_service_q
    from orders.models import ServiceItem

    services = list(ServiceItem.objects.filter(visible_service_q(), is_addon=False)
                    .exclude(service_category__is_gift=True)
                    .order_by('id').values_list('id', 'game_category_id', 'name'))
    if not services:
        return 'MISSING', '没有可售的陪玩服务，暂无法核对游戏资料规则'
    keys = {rule_key('service', pk) for pk, _, _ in services}
    keys.update(rule_key('game', game_id) for _, game_id, _ in services if game_id)
    raw = dict(SystemConfig.objects.filter(key__in=keys).values_list('key', 'value'))
    covered, missing, invalid = 0, [], []
    for service_id, game_id, name in services:
        try:
            service = parse(raw.get(rule_key('service', service_id)), 'service')
            if service['fields'] is not None:
                covered += 1
            elif game_id and parse(raw.get(rule_key('game', game_id)), 'game')['version'] > 0:
                covered += 1
            else:
                missing.append(name)
        except ValueError:
            invalid.append(name)
    if invalid:
        return 'INVALID', f'{len(invalid)} 项可售服务的有效资料规则无效，例如：' + '、'.join(invalid[:3])
    if covered < len(services):
        return 'MISSING', f'已明确配置 {covered}/{len(services)} 项可售服务；仍使用默认选填，例如：' + '、'.join(missing[:3])
    return 'SAVED', f'已明确配置全部 {len(services)} 项可售服务；仍需结合完整开店条件核对'


def selected_configuration_evidence(permissions):
    """Return evidence only, honoring each existing editor's view permissions.

    Default values and legacy unversioned rows do not prove a store confirmed
    them. Read all selected values together without creating sentinel rows.
    """
    keys = (CAPACITY_KEY, GRAB_WINDOW_KEY, RESERVATION_KEY, ROOM_CONFIRMATION_KEY, CLOCK_KEY, DEPOSIT_TARGET_KEY, DEPOSIT_PERCENT_KEY,
            MIN_WITHDRAW_AMOUNT_KEY, WITHDRAW_TAX_RATE_KEY, BALANCE_THRESHOLD_KEY) + PASS_KEYS
    raw = dict(SystemConfig.objects.filter(key__in=keys).values_list('key', 'value'))
    entries = []

    def append(key, name, view, path, status, message):
        permitted = bool(permissions.intersection(view))
        entries.append({'key': key, 'name': name, 'status': status if permitted else 'RESTRICTED',
                        'message': message if permitted else '需要对应业务查看权限',
                        'path': path if permitted else None})

    def versioned(key, parser, *, unconfigured=None):
        value = raw.get(key)
        if value is None or value == unconfigured:
            return 'MISSING', '尚未保存；当前业务可能使用默认值'
        try:
            result = parser(value)
        except ValueError:
            return 'INVALID', '配置无效，请在对应页面核对'
        if result['version'] == 0:
            return 'NEEDS_CONFIRMATION', '存在初始值，尚不能证明门店已确认'
        return 'SAVED', '已保存有效配置；仍需结合完整开店条件核对'

    capacity = versioned(CAPACITY_KEY, parse_capacity, unconfigured=UNCONFIGURED)
    append('capacity', '陪玩接单名额', {'order:view', 'dictionary:view', 'dictionary:edit'},
           'orders' if 'order:view' in permissions else None, *capacity)
    window = versioned(GRAB_WINDOW_KEY, parse_grab_window)
    append('grab_window', '抢单窗口', {'order:view', 'dictionary:view', 'dictionary:edit'},
           'orders' if 'order:view' in permissions else None, *window)
    reservation = versioned(RESERVATION_KEY, parse_reservation)
    if reservation[0] == 'MISSING':
        reservation = ('MISSING', '尚未保存；本店暂不能发起新预约')
    append('reservation_deadlines', '预约确认和付款时限', {'order:view', 'dictionary:view', 'dictionary:edit'},
           None, *reservation)
    room = versioned(ROOM_CONFIRMATION_KEY, parse_room_confirmation)
    if room[0] == 'MISSING':
        room = ('MISSING', '房间确认时限尚未配置；暂不能启用自动确认')
    append('room_confirmation', '房间确认时限', {'order:view', 'dictionary:view', 'dictionary:edit'},
           None, *room)
    clocks = versioned(CLOCK_KEY, parse_clocks)
    append('settlement_clocks', '售后及收益冻结时限', {'deposit_rule:view', 'deposit_rule:edit'},
           'deposit-rules' if 'deposit_rule:view' in permissions else None, *clocks)
    targets = versioned(DEPOSIT_TARGET_KEY, parse_deposit_targets)
    append('deposit_targets', '现金及单抵押金目标', {'deposit_rule:view', 'deposit_rule:edit'},
           'deposit-rules' if 'deposit_rule:view' in permissions else None, *targets)
    percent = raw.get(DEPOSIT_PERCENT_KEY)
    if percent is None:
        state = ('MISSING', '尚未保存；当前业务可能使用默认值')
    elif not _whole(percent, 100):
        state = ('INVALID', '单抵比例无效，请在对应页面核对')
    else:
        state = ('NEEDS_CONFIRMATION', '旧配置未记录确认版本，须由门店核对')
    append('deposit_percent', '单抵押金比例', {'deposit_rule:view', 'deposit_rule:edit'},
           'deposit-rules' if 'deposit_rule:view' in permissions else None, *state)
    withdraw_keys = (MIN_WITHDRAW_AMOUNT_KEY, BALANCE_THRESHOLD_KEY, WITHDRAW_TAX_RATE_KEY)
    if any(key in raw and not _whole(raw[key], 100 if key == WITHDRAW_TAX_RATE_KEY else MAX_AMOUNT) for key in withdraw_keys):
        state = ('INVALID', '提现金额、门槛或费率无效，请在对应页面核对')
    elif any(key not in raw for key in withdraw_keys):
        state = ('MISSING', '单笔最低额、余额门槛或费率尚未全部保存')
    else:
        state = ('NEEDS_CONFIRMATION', '旧配置未记录确认版本，须由门店核对')
    append('withdraw', '提现规则', {'withdraw:view', 'withdraw:audit', 'withdraw:config'},
           'withdrawals' if 'withdraw:view' in permissions else None, *state)
    if any(key not in raw for key in PASS_KEYS):
        state = ('MISSING', '通行证价格或可见延迟尚未全部保存；当前业务可能使用默认值')
    else:
        try:
            _snapshot('provider_pass')
            state = ('NEEDS_CONFIRMATION', '已保存通行证规则，须由门店核对')
        except ValueError:
            state = ('INVALID', '通行证价格或可见延迟无效，请在对应页面核对')
    append('provider_pass', '通行证价格及可见延迟', {'escort:view', 'escort:edit'},
           'provider-passes' if {'escort:view', 'escort:edit'}.intersection(permissions) else None, *state)
    if {'escort:view', 'escort:edit'}.intersection(permissions):
        try:
            _snapshot('no_pass_delay')
            state = ('NEEDS_CONFIRMATION', '无通行证可见延迟须由门店核对')
        except ValueError as exc:
            state = ('MISSING', str(exc))
    else:
        state = ('RESTRICTED', '需要对应业务查看权限')
    append('no_pass_delay', '无通行证可见延迟', {'escort:view', 'escort:edit'},
           'provider-passes' if {'escort:view', 'escort:edit'}.intersection(permissions) else None, *state)
    if {'engagement:view', 'engagement:edit'}.intersection(permissions):
        from engagement.models import RechargePackage
        if not RechargePackage.objects.exists():
            state = ('MISSING', '尚未保存预存赠送档位；老板仍可按本店规则充值')
        else:
            try:
                _snapshot('recharge_packages')
                state = ('NEEDS_CONFIRMATION', '已保存预存档位，须由门店核对')
            except ValueError:
                state = ('INVALID', '预存档位无效，请在对应页面核对')
    else:
        state = ('RESTRICTED', '需要对应业务查看权限')
    append('recharge_packages', '预存档位及赠送', {'engagement:view', 'engagement:edit'},
           'club-operations' if {'engagement:view', 'engagement:edit'}.intersection(permissions) else None, *state)
    if {'engagement:view', 'engagement:edit'}.intersection(permissions):
        try:
            _snapshot('points_award_rates')
            state = ('NEEDS_CONFIRMATION', '积分发放倍率须由门店核对')
        except ValueError as exc:
            state = ('MISSING', str(exc))
    else:
        state = ('RESTRICTED', '需要对应业务查看权限')
    append('points_award_rates', '积分发放倍率', {'engagement:view', 'engagement:edit'},
           'club-operations' if {'engagement:view', 'engagement:edit'}.intersection(permissions) else None, *state)
    if {'service:view', 'service:edit'}.intersection(permissions):
        try:
            _snapshot('gift_pricing')
            state = ('NEEDS_CONFIRMATION', '上架礼物的价格及抽成须由门店核对')
        except ValueError as exc:
            state = ('MISSING', str(exc))
    else:
        state = ('RESTRICTED', '需要对应业务查看权限')
    append('gift_pricing', '礼物价格及抽成', {'service:view', 'service:edit'},
           'service-items' if {'service:view', 'service:edit'}.intersection(permissions) else None, *state)
    if {'dictionary:view', 'dictionary:edit'}.intersection(permissions):
        account_fields_state = _account_fields_evidence()
    else:
        account_fields_state = ('RESTRICTED', '需要对应业务查看权限')
    append('order_account_fields', '下单游戏资料规则', {'dictionary:view', 'dictionary:edit'},
           None, *account_fields_state)
    for entry in entries:
        key = entry['key']
        if key not in CONFIRMABLE or entry['status'] == 'RESTRICTED':
            continue
        entry['can_confirm'] = CONFIRMABLE[key]['permission'] in permissions
        if entry['status'] not in ('NEEDS_CONFIRMATION', 'SAVED'):
            continue
        try:
            snapshot = _snapshot(key)
        except ValueError:
            continue
        previous = _last_confirmation(key)
        if previous and isinstance(previous.request_body, dict) and previous.request_body.get('fingerprint') == snapshot['fingerprint']:
            entry['status'] = 'CONFIRMED'
            entry['message'] = f'已由 {previous.operator_name} 核对；配置变化后需重新确认'
    return entries
