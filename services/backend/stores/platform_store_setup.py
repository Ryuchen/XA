"""Platform-super-admin setup without impersonating a store employee."""
from collections.abc import Mapping
import hashlib
import json
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction as directory_transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from console.capacity_config import CapacityConfigError, save_capacity
from console.grab_config import GrabConfigError, save_grab_window
from console.reservation_deadlines import ReservationDeadlineConfigError, save_deadlines
from console.room_confirmation_config import RoomConfirmationConfigError, save_room_confirmation
from console.points_redemption_config import PointsConfigError, save_points_terms
from console.points_usage import PointsUsageChangeError, save_points_usage
from console.deposit_rules import DepositConfigError, current_deposit_setup, save_deposit_targets, save_platform_deposit_percent
from console.settlement_clocks import ClockConfigError, save_clocks
from console.config_readiness import CONFIRMABLE, CONFIRM_RESOURCE, _last_confirmation, _snapshot, selected_configuration_evidence
from console.models import AdminAuditLog
from orders.capacity import current_capacity_rules
from orders.grab_config import current_rules as current_grab_rules
from orders.points_redemption_config import current_rules as current_points_terms
from stores.context import store_context
from stores.db import transaction as store_transaction
from wallet.withdraw_rules import (MAX_AMOUNT, WithdrawConfigChanged, lock_withdraw_rules,
    save_withdraw_rules, withdrawal_setup_snapshot)
from wallet.settlement_clocks import current_rules as current_clock_rules

from .hosts import is_admin_host
from .models import PlatformAuditEvent, Store
from .platform_accounts import AccountChangeError, _locked_actor
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_permissions import platform_may

_VIEW_PERMISSIONS = {'dictionary:view', 'dictionary:edit', 'order:view', 'deposit_rule:view',
                     'deposit_rule:edit', 'withdraw:view', 'withdraw:config',
                     'engagement:view', 'engagement:edit', 'escort:view', 'escort:edit',
                     'service:view', 'service:edit'}


class SetupError(ValueError):
    def __init__(self, message, status=409, code='SETUP_UNAVAILABLE'):
        super().__init__(message)
        self.status, self.code = status, code


def _membership_setup_snapshot():
    from engagement.models import ClubOperationConfig, RechargePackage, VipTier
    from engagement.serializers import PackageSerializer, TierSerializer
    from console.serializers import AdminBossTypeSerializer
    from users.models import BossType
    from django.core.serializers.json import DjangoJSONEncoder
    operation = ClubOperationConfig.objects.filter(pk=1).first()
    data = {'vip_enabled': operation.vip_enabled if operation else None,
            'base_points_rate': operation.base_points_rate if operation else None,
            'birthday_multiplier': operation.birthday_multiplier if operation else None,
            'boss_types': list(AdminBossTypeSerializer(BossType.objects.order_by('sort_order', 'pk'), many=True).data),
            'recharge_packages': list(PackageSerializer(RechargePackage.objects.order_by('amount', 'pk'), many=True).data),
            'vip_tiers': list(TierSerializer(VipTier.objects.select_related('boss_type').order_by('level', 'pk'), many=True).data)}
    # Canonical JSON provides an optimistic version over both existing lists,
    # without introducing another configuration record.
    encoded = json.dumps(data, cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return json.loads(encoded), hashlib.sha256(encoded.encode()).hexdigest()


def _pass_setup_snapshot():
    """Version actual saved values, including absence and the sales choice."""
    from console.config_readiness import PASS_KEYS
    from console.provider_pass import pass_config_data
    from users.pass_rules import PASS_SALES_ENABLED_KEY, pass_sales_enabled
    from wallet.models import SystemConfig
    keys = set(PASS_KEYS) | {PASS_SALES_ENABLED_KEY}
    raw = dict(SystemConfig.objects.filter(key__in=keys).values_list('key', 'value'))
    encoded = json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    try:
        sales = pass_sales_enabled()
    except ValueError:
        sales = None
    return {'configuration': pass_config_data(), 'sales_enabled': sales,
            'sales_configured': PASS_SALES_ENABLED_KEY in raw,
            'version': hashlib.sha256(encoded.encode()).hexdigest()}


def _store(pk, *, lock=False):
    query = Store.objects.select_for_update() if lock else Store.objects
    store = query.filter(pk=pk).first()
    if store is None:
        raise SetupError('门店不存在', 404, 'STORE_NOT_FOUND')
    from .dynamic_database import discover_store_connection
    try:
        available = discover_store_connection(store)
    except ValueError as exc:
        raise SetupError('门店数据库配置不可用，请联系平台运维') from exc
    if (store.initialized_at is None or store.status not in (Store.Status.CONFIGURING, Store.Status.ACTIVE, Store.Status.DISABLED)
            or not available):
        raise SetupError('门店业务库尚未完成初始化')
    return store


class PlatformSetupBase(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def allowed(self, request):
        return is_admin_host(request.get_host()) and platform_may(request.platform_account, 'stores:manage')

    @staticmethod
    def denied():
        return Response({'code': 403, 'errorCode': 'PLATFORM_FORBIDDEN', 'msg': '需要平台超级管理员权限'}, status=403)

    def mutate(self, request, pk, action, change):
        if not self.allowed(request):
            return self.denied()
        operation_id = uuid4().hex
        started = False
        slug = ''
        def finish_failure(outcome, code):
            if not started:
                return
            try:
                PlatformAuditEvent.objects.create(account=request.platform_account, action=f'setup.{action}_{outcome}',
                    target_store_slug=slug, target_type='store', target_id=str(pk),
                    detail={'operation_id': operation_id, 'error_code': code})
            except DatabaseError:
                pass  # The earlier committed intent remains available for review.
        try:
            # Persist intent first. Target and directory are separate DBs and
            # cannot share an atomic commit; operation_id links both audits.
            with directory_transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = _store(pk, lock=True)
                PlatformAuditEvent.objects.create(account=actor, action=f'setup.{action}_requested',
                    target_store_slug=store.slug, target_type='store', target_id=str(store.pk),
                    detail={'operation_id': operation_id})
                slug = store.slug
            started = True
            with directory_transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = _store(pk, lock=True)
                with store_context(store), store_transaction.atomic():
                    result = change(store, actor, operation_id)
                PlatformAuditEvent.objects.create(account=actor, action=f'setup.{action}_result',
                    target_store_slug=store.slug, target_type='store', target_id=str(store.pk),
                    detail={'operation_id': operation_id, 'outcome': 'completed'})
            return Response({'code': 0, 'data': result, 'msg': '配置操作已完成'})
        except (SetupError, AccountChangeError, CapacityConfigError, GrabConfigError, ReservationDeadlineConfigError, RoomConfirmationConfigError, ClockConfigError, DepositConfigError, PointsConfigError, PointsUsageChangeError) as exc:
            finish_failure('failed', getattr(exc, 'code', 'SETUP_UNAVAILABLE'))
            return Response({'code': exc.status, 'errorCode': getattr(exc, 'code', 'SETUP_UNAVAILABLE'),
                             'msg': str(exc)}, status=exc.status)
        except ValueError as exc:
            finish_failure('failed', 'SETUP_CONFIG_CHANGED')
            return Response({'code': 409, 'errorCode': 'SETUP_CONFIG_CHANGED', 'msg': str(exc)}, status=409)
        except DatabaseError:
            # The tenant transaction might already have committed; never
            # claim it rolled back or automatically retry the operation.
            finish_failure('uncertain', 'SETUP_RESULT_UNCERTAIN')
            return Response({'code': 503, 'errorCode': 'SETUP_RESULT_UNCERTAIN',
                             'msg': '配置结果需要核对，请重新读取门店配置'}, status=503)


class PlatformSetupView(PlatformSetupBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        try:
            store = _store(pk)
            with store_context(store):
                evidence = selected_configuration_evidence(_VIEW_PERMISSIONS)
                for row in evidence:
                    if row['key'] in CONFIRMABLE and row['status'] in ('SAVED', 'NEEDS_CONFIRMATION', 'CONFIRMED'):
                        try:
                            row['snapshot'] = _snapshot(row['key'])
                        except ValueError:
                            row['status'] = 'INVALID'
                            row['message'] = '配置已变化，请重新读取'
                try:
                    capacity = current_capacity_rules()
                except ValueError:
                    capacity = None
                try:
                    grab_window = current_grab_rules()
                except ValueError:
                    grab_window = None
                from orders.models import OrderTimeoutConfig
                from orders.timeout import DEFAULT_TIMEOUT_MINUTES
                timeout = OrderTimeoutConfig.objects.filter(pk=1).first()
                order_timeout = {'minutes': timeout.minutes if timeout else DEFAULT_TIMEOUT_MINUTES,
                                 'revision': timeout.revision if timeout else 0}
                from orders.reservation_deadlines import current_rules as current_reservation_rules
                try:
                    reservation_deadlines = current_reservation_rules()
                except ValueError:
                    reservation_deadlines = None
                from orders.room_confirmation_config import current_rules as current_room_confirmation
                try:
                    room_confirmation = current_room_confirmation()
                except ValueError:
                    room_confirmation = None
                try:
                    from console.points_redemption_config import redemption_configuration_data
                    points_terms = redemption_configuration_data(current_points_terms())
                except ValueError:
                    points_terms = None
                from engagement.points_usage import FIELDS, current_usage, enabled as points_usage_enabled
                points_usage = None
                if points_usage_enabled():
                    try:
                        issuance, switches = current_usage()
                        issuance_effective = True if issuance is None else issuance
                        points_usage = {**switches, 'issuance_enabled': issuance_effective,
                                        'issuance_configured': issuance is not None,
                                        'usage_enabled': bool(issuance_effective and all(switches[field] for field in FIELDS))}
                    except ValueError:
                        pass
                try:
                    clocks = current_clock_rules()
                except ValueError:
                    clocks = None
                try:
                    deposit = current_deposit_setup()
                except ValueError:
                    deposit = None
                try:
                    withdrawal = withdrawal_setup_snapshot()
                except ValueError:
                    withdrawal = None
                from coupons.monthly_cutover import cutover_status
                monthly_cutover = cutover_status()
                from orders.catalog_initial_prices import store_price_references
                price_references = store_price_references()
                from orders.models import GameCategory, GameCommissionProfile
                from console.game_commission import _serialized as serialize_commission
                profiles = {row.game_category_id: row for row in GameCommissionProfile.objects.all()}
                game_commissions = [{'id': game.pk, 'name': game.name, 'active': game.is_active,
                                     'configuration': serialize_commission(profiles.get(game.pk))}
                                    for game in GameCategory.objects.order_by('sort_order', 'id')]
                membership, membership_version = _membership_setup_snapshot()
                from users.pass_rules import pass_sales_enabled
                try:
                    provider_pass_sales = pass_sales_enabled()
                except ValueError:
                    provider_pass_sales = None
                from orders.models import ServiceItem
                from orders.catalog import visible_service_q
                account_field_targets = {
                    'games': list(GameCategory.objects.order_by('id').values('id', 'name', 'is_active')),
                    'services': list(ServiceItem.objects.filter(visible_service_q(), is_addon=False)
                        .exclude(service_category__is_gift=True).order_by('id')
                        .values('id', 'name', 'game_category_id')),
                }
            response = Response({'code': 0, 'data': {'store': {'id': store.pk, 'slug': store.slug,
                'name': store.name, 'status': store.status, 'currency_name': store.currency_name,
                'configured_boss_url': store.boss_url, 'configured_provider_url': store.provider_url,
                'configured_admin_url': store.admin_url},
                'evidence': evidence, 'capacity': capacity, 'grab_window': grab_window,
                'order_timeout': order_timeout,
                'reservation_deadlines': reservation_deadlines, 'room_confirmation': room_confirmation,
                'clocks': clocks, 'deposit': deposit,
                'points_terms': points_terms, 'points_usage': points_usage,
                'points_usage_enforcement_available': points_usage_enabled(),
                'withdrawal': withdrawal, 'monthly_cutover': monthly_cutover,
                'game_commissions': game_commissions, 'account_field_targets': account_field_targets,
                'membership': {**membership, 'version': membership_version},
                'provider_pass_sales_enabled': provider_pass_sales,
                'catalog_price_references': price_references, 'capability': 'PLATFORM_SETUP_V1'}})
            response['Cache-Control'] = 'no-store'
            return response
        except SetupError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '门店配置暂不可读取，请稍后重试'}, status=503)


class PlatformOpeningReadinessView(PlatformSetupBase):
    """Show actionable evidence without implicitly activating a store."""

    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        try:
            store = _store(pk)
            from .opening_readiness import opening_readiness
            with store_context(store):
                result = opening_readiness(store, _VIEW_PERMISSIONS)
            response = Response({'code': 0, 'data': result})
            response['Cache-Control'] = 'no-store'
            return response
        except SetupError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '开业配置暂不可核对，请稍后重试'}, status=503)

    def post(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not getattr(settings, 'STORE_FIRST_OPEN_ENABLED', False):
            return Response({'code': 503, 'msg': '首次开业尚未开放'}, status=503)
        if not isinstance(request.data, Mapping) or set(request.data) != {'confirm'} or request.data['confirm'] is not True:
            return Response({'code': 400, 'msg': '请确认提交开业核验'}, status=400)
        try:
            from .opening_readiness import opening_readiness
            from .license_status import status_for_store
            with directory_transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = _store(pk, lock=True)
                if store.status != Store.Status.CONFIGURING:
                    return Response({'code': 409, 'msg': '门店当前不处于待开业配置状态'}, status=409)
                license_status = status_for_store(store)
                with store_context(store):
                    result = opening_readiness(store, _VIEW_PERMISSIONS, license_status=license_status)
                if not result['can_open']:
                    return Response({'code': 409, 'errorCode': 'STORE_OPENING_BLOCKED',
                                     'msg': '开业条件尚未满足，请先核对配置和有效授权', 'data': result}, status=409)
                current_license = license_status['current']
                store.status = Store.Status.ACTIVE
                store.save(update_fields=['status', 'updated_at'])
                PlatformAuditEvent.objects.create(account=actor, action='store.first_opened',
                    target_store_slug=store.slug, target_type='store', target_id=str(store.pk),
                    detail={'checks': result['checks'], 'license': current_license})
                if getattr(settings, 'ISOLATED_CONTROL_PLANE', False):
                    from .models import StoreRoutingRevision
                    from .routing_snapshot import latest_revision, schedule
                    revision, _ = StoreRoutingRevision.objects.select_for_update().get_or_create(store=store)
                    revision.version = max(revision.version, latest_revision(store)) + 1
                    revision.save(update_fields=['version'])
                    schedule(store, revision.version)
                # The store lock serializes license changes. Clock boundaries
                # still move while configuration and routing evidence are read.
                now = timezone.now()
                scheduled = license_status['scheduled']
                if (now < parse_datetime(current_license['effective_at'])
                        or (scheduled and now >= parse_datetime(scheduled['effective_at']))):
                    raise SetupError('授权已变化，请重新核对开业条件', 409, 'STORE_OPENING_LICENSE_CHANGED')
                if now >= parse_datetime(current_license['ends_at']):
                    raise SetupError('授权已到期，请重新确认后再开业', 409, 'STORE_OPENING_LICENSE_EXPIRED')
            return Response({'code': 0, 'data': {'id': store.pk, 'status': store.status}, 'msg': '门店已开业'})
        except (SetupError, AccountChangeError) as exc:
            return Response({'code': exc.status, 'errorCode': getattr(exc, 'code', 'SETUP_UNAVAILABLE'),
                             'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '开业配置暂不可核对，请稍后重试'}, status=503)


class PlatformSetupMonthlyCutoverView(PlatformSetupBase):
    def post(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'confirm'} or request.data['confirm'] is not True:
            return Response({'code': 400, 'msg': '请确认月券切换规则'}, status=400)
        def change(store, actor, operation_id):
            from coupons.monthly_cutover import activate_cutover
            result = activate_cutover(platform_actor_id=actor.pk, operation_id=operation_id)
            AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                method='POST', path=request.path[:255], resource='vip-monthly-cutover', object_id='1',
                request_body={'actor_kind': 'platform', 'platform_account_id': str(actor.pk),
                              'operation_id': operation_id, 'activated_at': result['activated_at'],
                              'historic_zero_policy': True}, status_code=200)
            return result
        return self.mutate(request, pk, 'monthly_cutover', change)


class PlatformSetupCapacityView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'max_concurrent_orders', 'version'}:
            return Response({'code': 400, 'msg': '请填写接单名额和配置版本'}, status=400)
        def change(store, actor, operation_id):
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource='provider-capacity', object_id=str(row.pk),
                    request_body={'before': old, 'after': new, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return save_capacity(request.data['max_concurrent_orders'], request.data['version'], audit)
        return self.mutate(request, pk, 'capacity', change)


class PlatformSetupGrabWindowView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'window_seconds', 'version'}:
            return Response({'code': 400, 'msg': '请填写抢单窗口和配置版本'}, status=400)
        def change(store, actor, operation_id):
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource='automatic-grab-window', object_id=str(row.pk),
                    request_body={'before': old, 'after': new, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return save_grab_window(request.data['window_seconds'], request.data['version'], audit)
        return self.mutate(request, pk, 'grab_window', change)


class PlatformSetupProviderPassView(PlatformSetupBase):
    """Set the store's actual pass rules before first opening."""
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        try:
            store = _store(pk)
            with store_context(store):
                result = _pass_setup_snapshot()
            response = Response({'code': 0, 'data': result})
            response['Cache-Control'] = 'no-store'
            return response
        except SetupError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '门店通行证配置暂不可读取'}, status=503)

    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        data = request.data
        if (not isinstance(data, Mapping) or set(data) != {'kind', 'version', 'configuration'}
                or data['kind'] not in ('rules', 'availability', 'no_pass_delay')
                or not isinstance(data['version'], str) or len(data['version']) != 64
                or not isinstance(data['configuration'], Mapping)):
            return Response({'code': 400, 'msg': '请填写配置类型、版本和内容'}, status=400)

        def change(store, actor, operation_id):
            if store.status != Store.Status.CONFIGURING:
                raise SetupError('该入口仅用于首次开业前的通行证配置')
            from users.pass_rules import lock_pass_config, PASS_SALES_ENABLED_KEY
            from wallet.models import (SystemConfig, PASS_DAILY_PRICE_KEY_PREFIX,
                PASS_DELAY_SECONDS_KEY_PREFIX, get_pass_delay_seconds)
            from users.models import EscortProfile
            from console.provider_pass import PassConfigInput
            from rest_framework.exceptions import ValidationError
            lock_pass_config()
            before = _pass_setup_snapshot()
            if before['version'] != data['version']:
                raise SetupError('配置已变化，请刷新后重新核对', 409, 'CONFIG_CHANGED')
            values = data['configuration']
            if data['kind'] == 'rules':
                serializer = PassConfigInput(data=values)
                try:
                    serializer.is_valid(raise_exception=True)
                except ValidationError as exc:
                    raise SetupError(str(exc.detail), 400, 'CONFIG_INVALID') from exc
                parsed = serializer.validated_data
                for product in parsed['products']:
                    for prefix, field in ((PASS_DAILY_PRICE_KEY_PREFIX, 'daily_price'),
                                          (PASS_DELAY_SECONDS_KEY_PREFIX, 'delay_seconds')):
                        SystemConfig.objects.update_or_create(key=f"{prefix}{product['tier']}",
                            defaults={'value': str(product[field])})
                SystemConfig.objects.update_or_create(key=f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE',
                    defaults={'value': str(parsed['no_pass_delay_seconds'])})
            elif data['kind'] == 'no_pass_delay':
                if (set(values) != {'delay_seconds'} or type(values['delay_seconds']) is not int
                        or not 0 <= values['delay_seconds'] <= 86400):
                    raise SetupError('无通行证延迟须为 0 至 86400 秒整数', 400, 'CONFIG_INVALID')
                if any(values['delay_seconds'] < get_pass_delay_seconds(tier) for tier, _ in EscortProfile.PassTier.choices):
                    raise SetupError('无通行证延迟不能短于通行证档位延迟', 400, 'CONFIG_INVALID')
                SystemConfig.objects.update_or_create(key=f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE',
                    defaults={'value': str(values['delay_seconds']), 'remark': '无通行证订单可见延迟（秒）'})
            else:
                if set(values) != {'enabled'} or type(values['enabled']) is not bool:
                    raise SetupError('请明确选择是否销售通行证', 400, 'CONFIG_INVALID')
                if before['sales_enabled'] is None:
                    raise SetupError('通行证销售开关无效，请先核对')
                if values['enabled']:
                    snapshot = _snapshot('provider_pass', lock=True)
                    previous = _last_confirmation('provider_pass')
                    if not previous or not isinstance(previous.request_body, dict) or previous.request_body.get('fingerprint') != snapshot['fingerprint']:
                        raise SetupError('请先核对并确认当前通行证价格及可见延迟')
                SystemConfig.objects.update_or_create(key=PASS_SALES_ENABLED_KEY,
                    defaults={'value': '1' if values['enabled'] else '0', 'remark': '本店通行证销售开关'})
            after = _pass_setup_snapshot()
            AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150], method='PUT',
                path=request.path[:255], resource='initial-provider-pass', object_id=data['kind'], status_code=200,
                request_body={'before': before, 'after': after, 'actor_kind': 'platform',
                              'platform_account_id': str(actor.pk), 'operation_id': operation_id})
            return after
        return self.mutate(request, pk, f"provider_pass_{data['kind']}", change)


class PlatformSetupOrderTimeoutView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'minutes', 'revision'}:
            return Response({'code': 400, 'msg': '请填写待接单时限和配置版本'}, status=400)
        minutes, revision = request.data['minutes'], request.data['revision']
        if type(minutes) is not int or not 1 <= minutes <= 1440 or type(revision) is not int or revision < 0:
            return Response({'code': 400, 'msg': '时限须为 1 至 1440 的整数分钟，版本号须为非负整数'}, status=400)
        def change(store, actor, operation_id):
            from orders.timeout import current_timeout_config
            from orders.models import OrderTimeoutConfigHistory
            row = current_timeout_config(lock=True)
            if row.revision != revision:
                raise SetupError('配置已被其他管理员修改，请刷新后再保存', 409, 'CONFIG_CHANGED')
            previous = row.minutes
            row.minutes, row.revision = minutes, row.revision + 1
            row.save(update_fields=['minutes', 'revision', 'updated_at'])
            name = f'平台超级管理员：{actor.username}'[:150]
            OrderTimeoutConfigHistory.objects.create(operator_name=name, previous_minutes=previous,
                                                      minutes=minutes, revision=row.revision)
            AdminAuditLog.objects.create(operator_name=name, method='PUT', path=request.path[:255],
                resource='order-timeout', object_id=str(row.pk), request_body={
                    'before': {'minutes': previous, 'revision': revision},
                    'after': {'minutes': minutes, 'revision': row.revision},
                    'actor_kind': 'platform', 'platform_account_id': str(actor.pk), 'operation_id': operation_id,
                }, status_code=200)
            return {'minutes': row.minutes, 'revision': row.revision}
        return self.mutate(request, pk, 'order_timeout', change)


class PlatformSetupGameCommissionView(PlatformSetupBase):
    """Configure the same store-local game rules used by order pricing."""

    def get(self, request, pk, game_id):
        if not self.allowed(request):
            return self.denied()
        from console.game_commission import _serialized, _configuration_status
        from orders.models import GameCategory, GameCommissionProfile
        try:
            store = _store(pk)
            with store_context(store):
                game = GameCategory.objects.filter(pk=game_id).first()
                if not game:
                    return Response({'code': 404, 'msg': '游戏类目不存在'}, status=404)
                profile = GameCommissionProfile.objects.filter(game_category=game).first()
                data = {'game_category_id': game.pk, 'name': game.name, 'active': game.is_active,
                        'configuration': _serialized(profile), 'status': _configuration_status()}
            response = Response({'code': 0, 'data': data})
            response['Cache-Control'] = 'no-store'
            return response
        except SetupError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '门店配置暂不可读取，请稍后重试'}, status=503)

    def put(self, request, pk, game_id):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'game', 'version', 'rules'}:
            return Response({'code': 400, 'msg': '请填写游戏、抽成规则和配置版本'}, status=400)
        game, version = request.data['game'], request.data['version']
        if type(version) is not int or version < 0:
            return Response({'code': 400, 'msg': '配置版本须为非负整数'}, status=400)
        def change(store, actor, operation_id):
            from console.game_commission import _serialized, _configuration_status
            from orders.game_commission import validate_rules
            from orders.models import GameCategory, GameCommissionProfile
            category = GameCategory.objects.select_for_update().filter(pk=game_id).first()
            if not category:
                raise SetupError('游戏类目不存在', 404, 'GAME_NOT_FOUND')
            # Delta rules resolve gameplay IDs against the target store.  The
            # platform request itself runs on the control-plane connection.
            try:
                rules = validate_rules(game, request.data['rules'])
            except (ValueError, TypeError) as exc:
                raise SetupError(str(exc), 400, 'CONFIG_INVALID') from exc
            row = GameCommissionProfile.objects.select_for_update().filter(game_category=category).first()
            old = _serialized(row)
            if (row.version if row else 0) != version:
                raise SetupError('配置已变化，请刷新后重新核对', 409, 'CONFIG_CHANGED')
            if row is None:
                row = GameCommissionProfile(game_category=category, game=game, version=1, rules=rules)
            else:
                row.game, row.rules, row.version = game, rules, row.version + 1
            try:
                # A different category may concurrently claim the same game.
                # Keep the unique constraint authoritative and roll back only
                # the savepoint before returning a configuration conflict.
                with store_transaction.atomic():
                    row.save()
            except IntegrityError as exc:
                raise SetupError('该游戏已绑定其他类目，请刷新后核对', 409, 'CONFIG_CHANGED') from exc
            new = _serialized(row)
            AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150], method='PUT',
                path=request.path[:255], resource='game-commission-draft', object_id=str(category.pk),
                request_body={'before': old, 'after': new, 'actor_kind': 'platform',
                              'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return {'configuration': new, 'status': _configuration_status()}
        return self.mutate(request, pk, 'game_commission', change)


class PlatformSetupMembershipView(PlatformSetupBase):
    """Configure existing membership tables before the first store opening."""

    def put(self, request, pk, kind):
        if not self.allowed(request):
            return self.denied()
        if (kind not in ('boss-type', 'recharge-package', 'vip-tier', 'vip-switch', 'vip-settings', 'vip-reorder') or not isinstance(request.data, Mapping)
                or set(request.data) != {'version', 'id', 'configuration'}
                or not isinstance(request.data['version'], str)
                or (request.data['id'] is not None and
                    (type(request.data['id']) is not int or request.data['id'] < 1))
                or not isinstance(request.data['configuration'], Mapping)):
            return Response({'code': 400, 'msg': '请填写配置版本、目标和内容'}, status=400)

        def change(store, actor, operation_id):
            from club_accounts.models import ClubAccount
            from engagement.models import ClubOperationConfig, RechargePackage, VipTier
            from engagement.recharge_pricing import lock_recharge_packages
            from engagement.serializers import PackageSerializer, TierSerializer
            from console.serializers import AdminBossTypeSerializer
            from users.models import BossType
            from rest_framework.exceptions import ValidationError
            from club_accounts.license_quota import lock_category
            if store.status != Store.Status.CONFIGURING:
                raise SetupError('该入口仅用于门店首次开业前的初始化')
            lock_category('BOSS')
            # Structural tier and switch changes use the membership
            # transition and entitlement-preservation workflow below.
            lock_recharge_packages()
            locked_accounts = None
            if (kind in ('vip-switch', 'vip-reorder')
                    or (kind == 'vip-tier' and ClubAccount.objects.filter(account_type='BOSS').exists())):
                from engagement.vip_threshold_change import lock_all_boss_wallets
                locked_accounts = lock_all_boss_wallets()
            operation = ClubOperationConfig.objects.select_for_update().filter(pk=1).first()
            if operation is None:
                raise SetupError('门店会员基础配置尚未初始化')
            before, version = _membership_setup_snapshot()
            if request.data['version'] != version:
                raise SetupError('配置已变化，请刷新后重新核对', 409, 'CONFIG_CHANGED')
            if kind == 'vip-settings':
                config_values = request.data['configuration']
                if (request.data['id'] is not None or not config_values
                        or set(config_values) - {'base_points_rate', 'birthday_multiplier'}):
                    raise SetupError('请填写会员基础积分倍率和生日倍率', 400, 'CONFIG_INVALID')
                from engagement.serializers import ConfigSerializer
                serializer = ConfigSerializer(operation, data=config_values, partial=True)
                try:
                    serializer.is_valid(raise_exception=True)
                    serializer.save()
                except ValidationError as exc:
                    raise SetupError(str(exc.detail), 400, 'CONFIG_INVALID') from exc
                after, new_version = _membership_setup_snapshot()
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150], method='PUT',
                    path=request.path[:255], resource='initial-vip-settings', object_id='1',
                    request_body={'before': before, 'after': after, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
                return {'id': None, 'version': new_version, **after}
            if kind == 'vip-switch':
                config = request.data['configuration']
                if (request.data['id'] is not None or set(config) != {'vip_enabled'}
                        or type(config['vip_enabled']) is not bool):
                    raise SetupError('请填写有效的 VIP 开关', 400, 'CONFIG_INVALID')
                from .platform_setup_vip_switch import change_switch
                try:
                    change_switch(operation, config['vip_enabled'], locked_accounts, actor, operation_id, request)
                except ValidationError as exc:
                    raise SetupError(str(exc.detail), 400, 'CONFIG_INVALID') from exc
                after, new_version = _membership_setup_snapshot()
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150], method='PUT',
                    path=request.path[:255], resource='initial-vip-switch', object_id='1',
                    request_body={'before': before, 'after': after, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
                return {'id': None, 'version': new_version, **after}
            if kind == 'vip-reorder':
                from django.conf import settings
                from engagement.vip_transition import transition_guard_available
                from engagement.vip_reorder import preview, apply
                from types import SimpleNamespace
                if (request.data['id'] is not None or set(request.data['configuration']) != {'mapping'}):
                    raise SetupError('请提交完整等级顺序', 400, 'CONFIG_INVALID')
                if not getattr(settings, 'VIP_THRESHOLD_RECALC_ENABLED', False) or not transition_guard_available():
                    raise SetupError('会员重算和既有权益保护尚未开放')
                mapping = request.data['configuration']['mapping']
                try:
                    token = preview(mapping)['token']
                    audit_request = SimpleNamespace(platform_setup_actor=actor, platform_setup_operation_id=operation_id,
                                                    method=request.method, path=request.path)
                    result = apply(mapping, token, audit_request, locked_accounts)
                except ValidationError as exc:
                    raise SetupError(str(exc.detail), 400, 'CONFIG_INVALID') from exc
                from engagement.notifier import notify_vip_config_changed
                from coupons.notifier import notify_coupon_catalog_changed
                store_transaction.on_commit(notify_vip_config_changed)
                store_transaction.on_commit(notify_coupon_catalog_changed)
                after, new_version = _membership_setup_snapshot()
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150], method='PUT',
                    path=request.path[:255], resource='initial-vip-reorder', object_id='',
                    request_body={'before': before, 'after': after, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
                return {'id': None, 'version': new_version, 'recalculation': result, **after}
            model, serializer_type = {'boss-type': (BossType, AdminBossTypeSerializer),
                'recharge-package': (RechargePackage, PackageSerializer),
                'vip-tier': (VipTier, TierSerializer)}[kind]
            target_id = request.data['id']
            if kind == 'vip-tier' and locked_accounts is not None:
                type_ids = set(VipTier.objects.values_list('boss_type_id', flat=True))
                requested_type = request.data['configuration'].get('boss_type')
                if type(requested_type) is int and requested_type > 0:
                    type_ids.add(requested_type)
                list(BossType.objects.select_for_update().filter(pk__in=type_ids).order_by('pk').values_list('pk', flat=True))
            row = model.objects.select_for_update().filter(pk=target_id).first() if target_id else None
            if target_id and row is None:
                raise SetupError('配置记录不存在', 404, 'CONFIG_NOT_FOUND')
            serializer = serializer_type(row, data=request.data['configuration'], partial=row is not None)
            try:
                serializer.is_valid(raise_exception=True)
                if (kind == 'boss-type' and row and row.is_active
                        and serializer.validated_data.get('is_active') is False
                        and VipTier.objects.filter(boss_type=row, active=True).exists()):
                    raise ValidationError('该分级关联启用中的 VIP 等级，请先核对并停用')
                if kind == 'vip-tier':
                    from engagement.vip_reward_references import lock_and_validate_upgrade_rewards
                    lock_and_validate_upgrade_rewards(serializer.validated_data)
                    boss_type = serializer.validated_data.get('boss_type', row.boss_type if row else None)
                    active = serializer.validated_data.get('active', row.active if row else True)
                    if not boss_type or (active and not boss_type.is_active):
                        raise ValidationError('启用中的 VIP 等级须关联启用中的老板分级')
                if kind == 'vip-tier' and locked_accounts is not None:
                    from .platform_setup_vip_tier import save_tier
                    saved = save_tier(serializer, row, locked_accounts, actor, operation_id, request)
                else:
                    saved = serializer.save()
                if kind == 'boss-type':
                    from engagement.notifier import notify_vip_config_changed
                    store_transaction.on_commit(notify_vip_config_changed)
            except ValidationError as exc:
                raise SetupError(str(exc.detail), 400, 'CONFIG_INVALID') from exc
            after, new_version = _membership_setup_snapshot()
            AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150], method='PUT',
                path=request.path[:255], resource=f'initial-{kind}', object_id=str(saved.pk),
                request_body={'before': before, 'after': after, 'actor_kind': 'platform',
                              'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return {'id': saved.pk, 'version': new_version, **after}
        return self.mutate(request, pk, f'membership_{kind}', change)


class PlatformSetupReservationDeadlinesView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'confirmation_minutes', 'payment_minutes', 'version', 'slot_minutes', 'min_duration_minutes', 'max_duration_minutes', 'max_advance_days'}:
            return Response({'code': 400, 'msg': '请填写预约时限和配置版本'}, status=400)
        def change(store, actor, operation_id):
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource='reservation-deadlines', object_id=str(row.pk),
                    request_body={'before': old, 'after': new, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return save_deadlines(**request.data, audit=audit)
        return self.mutate(request, pk, 'reservation_deadlines', change)


class PlatformSetupRoomConfirmationView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'minutes', 'version'}:
            return Response({'code': 400, 'msg': '请填写房间确认时限和配置版本'}, status=400)
        def change(store, actor, operation_id):
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource='room-confirmation-deadline', object_id=str(row.pk),
                    request_body={'before': old, 'after': new, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return save_room_confirmation(**request.data, audit=audit)
        return self.mutate(request, pk, 'room_confirmation', change)


class PlatformSetupPointsTermsView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {
                'group_point_hundredths', 'group_coin_units', 'max_ratio_basis_points', 'version'}:
            return Response({'code': 400, 'msg': '请完整填写兑换组、抵扣上限和版本'}, status=400)
        def change(store, actor, operation_id):
            def audit(row, before, after):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource='points-redemption-terms', object_id=str(row.pk),
                    request_body={'before': before, 'after': after, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            from console.points_redemption_config import redemption_configuration_data
            return redemption_configuration_data(save_points_terms(**request.data, audit=audit))
        return self.mutate(request, pk, 'points_terms', change)


class PlatformSetupPointsUsageView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        from engagement.points_usage import enabled
        if not enabled():
            return Response({'code': 409, 'msg': '积分使用联动尚未开放'}, status=409)
        def change(store, actor, operation_id):
            def audit(row, before, after):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource='points-usage-switches', object_id=str(row.pk),
                    request_body={'before': before, 'after': after, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return save_points_usage(request.data, may_edit_issuance=True, audit=audit)
        return self.mutate(request, pk, 'points_usage', change)


class PlatformSetupClocksView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'aftersale_hours', 'income_freeze_hours', 'expected_version'}:
            return Response({'code': 400, 'msg': '请填写售后时限、收益冻结时限及配置版本'}, status=400)
        def change(store, actor, operation_id):
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource='settlement-clocks', object_id=str(row.pk),
                    request_body={'before': old, 'after': new, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return save_clocks(request.data, audit)
        return self.mutate(request, pk, 'clocks', change)


class PlatformSetupDepositView(PlatformSetupBase):
    def put(self, request, pk, kind):
        if not self.allowed(request):
            return self.denied()
        keys = {'targets': {'cash_target', 'earnings_target', 'expected_version'},
                'percent': {'deposit_percent', 'fingerprint'}}
        if kind not in keys or not isinstance(request.data, Mapping) or set(request.data) != keys[kind]:
            return Response({'code': 400, 'msg': '请重新读取并填写押金配置'}, status=400)
        def change(store, actor, operation_id):
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource=f'deposit-{kind}', object_id=str(row.pk),
                    request_body={'before': old, 'after': new, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            if kind == 'targets':
                return save_deposit_targets(request.data, audit)
            return save_platform_deposit_percent(request.data['deposit_percent'], request.data['fingerprint'], audit)
        return self.mutate(request, pk, f'deposit_{kind}', change)


class PlatformSetupWithdrawalView(PlatformSetupBase):
    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        fields = {'min_amount', 'balance_threshold', 'tax_rate', 'fingerprint'}
        if (not isinstance(request.data, Mapping) or set(request.data) != fields
                or any(type(request.data[k]) is not int or not 0 <= request.data[k] <= (100 if k == 'tax_rate' else MAX_AMOUNT)
                       for k in ('min_amount', 'balance_threshold', 'tax_rate'))
                or not isinstance(request.data['fingerprint'], str) or len(request.data['fingerprint']) != 64):
            return Response({'code': 400, 'msg': '请填写有效的最低金额、余额门槛和 0–100 的整数费率'}, status=400)
        def change(store, actor, operation_id):
            def audit(old, new):
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='PUT', path=request.path[:255], resource='withdraw-rules', object_id='withdraw',
                    request_body={'before': old, 'after': new, 'actor_kind': 'platform',
                                  'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            try:
                return save_withdraw_rules(request.data['min_amount'], request.data['tax_rate'],
                    request.data['balance_threshold'], fingerprint=request.data['fingerprint'], audit=audit)
            except WithdrawConfigChanged as exc:
                raise SetupError(str(exc), 409, 'WITHDRAW_CONFIG_CHANGED') from exc
        return self.mutate(request, pk, 'withdraw', change)


class PlatformSetupConfirmView(PlatformSetupBase):
    def post(self, request, pk, key):
        if not self.allowed(request):
            return self.denied()
        if (key not in CONFIRMABLE or not isinstance(request.data, Mapping) or set(request.data) != {'fingerprint'}
                or not isinstance(request.data['fingerprint'], str) or len(request.data['fingerprint']) != 64):
            return Response({'code': 400, 'msg': '请重新读取并核对配置'}, status=400)
        def change(store, actor, operation_id):
            if key == 'withdraw':
                lock_withdraw_rules()
            elif key in ('provider_pass', 'no_pass_delay'):
                from users.pass_rules import lock_pass_config
                lock_pass_config()
            elif key == 'gift_pricing':
                from console.catalog_write_lock import lock_catalog
                lock_catalog()
            snapshot = _snapshot(key, lock=True)
            if snapshot['fingerprint'] != request.data['fingerprint']:
                raise SetupError('配置已变化，请重新读取并核对', 409, 'SETUP_CONFIG_CHANGED')
            previous = _last_confirmation(key)
            if not previous or not isinstance(previous.request_body, dict) or previous.request_body.get('fingerprint') != snapshot['fingerprint']:
                AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                    method='POST', path=request.path[:255], resource=CONFIRM_RESOURCE, object_id=key,
                    request_body={**snapshot, 'actor_kind': 'platform', 'platform_account_id': str(actor.pk),
                                  'operation_id': operation_id}, status_code=200)
            return {'key': key, 'fingerprint': snapshot['fingerprint']}
        return self.mutate(request, pk, 'confirm', change)


class PlatformSetupAccountFieldsView(PlatformSetupBase):
    """Configure the same store-local game/service data requirements as console."""

    @staticmethod
    def _target(scope, target_id):
        from orders.models import GameCategory, ServiceItem
        if scope not in ('game', 'service'):
            raise SetupError('资料规则类型无效', 400, 'CONFIG_INVALID')
        model = GameCategory if scope == 'game' else ServiceItem
        target = model.objects.filter(pk=target_id).values('id', 'name').first()
        if not target:
            raise SetupError('游戏或服务不存在', 404, 'CONFIG_NOT_FOUND')
        return target

    def get(self, request, pk, scope, target_id):
        if not self.allowed(request):
            return self.denied()
        try:
            store = _store(pk)
            with store_context(store):
                from orders.account_fields import current
                target = self._target(scope, target_id)
                result = current(scope, target_id)
            response = Response({'code': 0, 'data': {**result, 'scope': scope, 'target': target}})
            response['Cache-Control'] = 'no-store'
            return response
        except SetupError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        except DatabaseError:
            return Response({'code': 503, 'msg': '门店资料规则暂不可读取'}, status=503)

    def put(self, request, pk, scope, target_id):
        data = request.data
        from orders.account_fields import FIELDS
        if (scope not in ('game', 'service') or not isinstance(data, Mapping)
                or set(data) != {'version', 'fields'} or type(data['version']) is not int
                or not 0 <= data['version'] < 2147483647
                or (data['fields'] is None and scope != 'service')
                or (data['fields'] is not None and (not isinstance(data['fields'], list)
                    or any(not isinstance(item, str) or item not in FIELDS for item in data['fields'])
                    or len(set(data['fields'])) != len(data['fields'])))):
            return Response({'code': 400, 'msg': '资料规则格式无效'}, status=400)

        def change(store, actor, operation_id):
            from orders.account_fields import key, lock_guard, parse
            from wallet.models import SystemConfig
            lock_guard()
            target = self._target(scope, target_id)
            config_key = key(scope, target_id)
            row = SystemConfig.objects.select_for_update().filter(key=config_key).first()
            old = parse(row.value if row else None, scope)
            if old['version'] != data['version']:
                raise SetupError('资料规则已变化，请刷新后重新核对', 409, 'CONFIG_CHANGED')
            fields = None if data['fields'] is None else [field for field in FIELDS if field in data['fields']]
            new = {'version': old['version'] + 1, 'fields': fields}
            if row:
                row.value = json.dumps(new, separators=(',', ':'))
                row.save(update_fields=['value', 'updated_at'])
            else:
                row = SystemConfig.objects.create(key=config_key, value=json.dumps(new, separators=(',', ':')),
                                                  remark='订单游戏资料规则')
            AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150], method='PUT',
                path=request.path[:255], resource='order-account-fields', object_id=str(row.pk),
                request_body={'scope': scope, 'target_id': target_id, 'before': old, 'after': new,
                              'actor_kind': 'platform', 'platform_account_id': str(actor.pk),
                              'operation_id': operation_id}, status_code=200)
            return {**new, 'scope': scope, 'target': target}
        return self.mutate(request, pk, 'account_fields', change)
