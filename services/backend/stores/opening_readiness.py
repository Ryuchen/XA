"""Read-only opening checks shared by diagnostics and the controlled gate.

The caller of the gate must hold the directory store lock across these checks
and the status change; a standalone read never authorizes a later change.
"""
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.http.request import validate_host
from django.utils import timezone

from console.config_readiness import selected_configuration_evidence
from orders.catalog import visible_service_q
from orders.game_commission import preview_rate
from orders.models import GameCommissionProfile, OrderTimeoutConfig, ServiceItem
from orders.purchase_benefits import account_service_q
from orders.reservation_deadlines import CONFIG_KEY as RESERVATION_KEY
from support.assignment import selectable_cards
from wallet.models import SystemConfig

from .business_certificates import certificate_evidence
from .hosts import DEFAULT_HOST_BINDINGS
from .license_status import status_for_store
from .media import verified_background_path
from .opening_materials import order_notice_evidence, store_logo_present


def admin_login_readiness():
    """Check configured methods, not successful delivery by external providers."""
    from club_accounts.models import OtpCredential
    from club_accounts.otp import decrypt_secret
    from console.models import AdminMembership

    sms_configured = all(getattr(settings, name, '') for name in (
        'SMS_SECRET_ID', 'SMS_SECRET_KEY', 'SMS_APP_ID', 'SMS_TEMPLATE_ID', 'SMS_SIGN_NAME'))
    mini_appid = getattr(settings, 'WECHAT_H5_APPID', '')
    mini_configured = bool(mini_appid and getattr(settings, 'WECHAT_H5_SECRET', ''))
    h5_appid = getattr(settings, 'WECHAT_H5_APPID', '')
    h5_configured = bool(h5_appid and getattr(settings, 'WECHAT_H5_SECRET', ''))
    memberships = AdminMembership.objects.filter(
        is_active=True, account__can_login=True, account__is_active=True,
        account__account_type='STAFF', roles__code='super_admin', roles__is_active=True,
    ).select_related('account').distinct()
    methods = set()
    for membership in memberships:
        account = membership.account
        if account.has_usable_password():
            credential = OtpCredential.objects.filter(account=account).first()
            if credential:
                try:
                    if decrypt_secret(credential.secret_ciphertext):
                        methods.add('OTP')
                except ValueError:
                    pass
            phone = account.phone or ''
            if (sms_configured and account.is_phone_verified and phone.isascii()
                    and phone.isdigit() and 5 <= len(phone) <= 20):
                methods.add('SMS_CONFIGURED')
        if (mini_configured and account.is_openid_bound and account.openid
                and account.wechat_appid == mini_appid):
            methods.add('WECHAT_H5_CONFIGURED')
        if h5_configured and account.is_openid_bound and account.wechat_identities.filter(
                channel='OFFICIAL_H5', appid=h5_appid).exclude(openid='').exists():
            methods.add('WECHAT_H5_CONFIGURED')
    return sorted(methods)


def opening_readiness(store, permissions, *, license_status=None):
    """Read the target store's actual configuration without creating defaults.

    Caller supplies store_context(store); directory reads are routed separately.
    A standalone READY row is only evidence for its named check.
    """
    checks = []

    def add(key, ok, message, *, details=None):
        row = {'key': key, 'status': 'READY' if ok else 'BLOCKED', 'message': message}
        if details is not None:
            row['details'] = details
        checks.append(row)

    if license_status is None:
        license_status = status_for_store(store)
    add('license', license_status['can_open_store'],
        '授权在有效期内' if license_status['can_open_store'] else '首次开业须先激活有效期内的门店授权',
        details={'status': license_status['status'], 'tier': license_status['tier']})
    certificate = certificate_evidence(store, timezone.now())
    certificate_messages = {'VALID': '本店证照已核对且在有效期内', 'MISSING': '请保存并核对本店营业执照',
                            'EXPIRED': '本店证照已到期，请登记新证照',
                            'DOCUMENT_UNAVAILABLE': '本店证照原件无法核对，请检查证照记录'}
    add('business_certificate', certificate['status'] == 'VALID', certificate_messages[certificate['status']],
        details=certificate)
    admin_methods = admin_login_readiness()
    add('store_admin', bool(admin_methods),
        '至少一名本店超级管理员须绑定 OTP，或有已验证手机号及短信配置、已绑定微信及入口配置；外部渠道仍需实际联调',
        details={'configured_methods': admin_methods})
    from engagement.models import ClubOperationConfig, RechargePackage, VipTier
    operation = ClubOperationConfig.objects.filter(pk=1).first()
    if operation is None:
        add('membership_configuration', False, '缺少门店会员基础配置')
    elif operation.vip_enabled:
        add('membership_configuration', VipTier.objects.filter(active=True, boss_type__is_active=True).exists(),
            '启用 VIP 时须配置至少一个有效等级和对应权益')
    else:
        add('membership_configuration', True, '门店已停用 VIP，可在后续配置并启用')

    if operation is None:
        add('points_configuration', False, '缺少门店积分基础配置')
    elif not operation.points_enabled:
        add('points_configuration', True, '门店已暂停积分使用与发放')
    else:
        from engagement.points_usage import FIELDS, current_usage
        from orders.points_redemption_config import current_rules as current_points_terms
        try:
            _, usage = current_usage()
            if not all(usage[field] for field in FIELDS):
                add('points_configuration', True, '门店已暂停积分使用，抵扣规则可后续配置')
            else:
                terms = current_points_terms()
                if terms['version'] > 0:
                    add('points_configuration', True, '积分抵扣规则已保存')
                elif usage['version'] == 0:
                    # No configured redemption terms and no explicit use
                    # policy: checkout already rejects redemption quotes.
                    add('points_configuration', True, '积分抵扣尚未配置，暂不可用')
                else:
                    add('points_configuration', False, '明确启用积分使用时须保存有效的整组抵扣规则')
        except ValueError:
            add('points_configuration', False, '积分使用开关或抵扣规则无效，请先核对')
    valid_identity = bool(store.name.strip() and store.currency_name.strip()
                          and re.fullmatch(r'[A-Z0-9]{2,4}', store.entity_prefix or '')
                          and re.fullmatch(r'[A-Z]{2,8}', store.provider_name_prefix or ''))
    add('store_identity', valid_identity, '门店名称、业务编号前缀、陪玩简称及店币名称须填写完整')
    bindings = getattr(settings, 'STORE_HOST_BINDINGS', DEFAULT_HOST_BINDINGS)
    from .models import StoreDomain
    domains = {host for host, slug in bindings.items() if slug == store.slug}
    domains.update(StoreDomain.objects.filter(store=store, active=True).values_list('hostname', flat=True))
    allowed_hosts = settings.ALLOWED_HOSTS or (['.localhost', '127.0.0.1', '[::1]'] if settings.DEBUG else [])
    usable_domains = sorted(host for host in domains if validate_host(host.lower(), allowed_hosts))
    add('store_domain', bool(usable_domains),
        '门店须配置绑定本店且在部署 ALLOWED_HOSTS 中允许的业务域名',
        details={'bound_domains': sorted(domains), 'allowed_domains': usable_domains})

    def usable_entry(value, path):
        try:
            parts = urlsplit(value or '')
            return bool(parts.scheme in ('http', 'https') and not parts.username and not parts.password
                        and parts.hostname in usable_domains and parts.path.rstrip('/') == path)
        except ValueError:
            return False

    boss_ready = usable_entry(store.boss_url, '/boss')
    provider_ready = usable_entry(store.provider_url, '/player')
    add('store_entries', boss_ready and provider_ready,
        '开业前须配置本店可访问的老板端 /boss 和陪玩端 /player 入口',
        details={'boss_configured': boss_ready, 'provider_configured': provider_ready})

    add('store_logo', store_logo_present(store, usable_domains), '请上传并保存可读取的本店 Logo')
    add('store_background', bool(verified_background_path(store.background_url)), '请上传并保存可读取的本店背景')
    add('store_introduction', bool(store.introduction.strip()), '请填写本店公开介绍')
    notice = order_notice_evidence(timezone.now())
    add('order_notice', notice is not None, '请发布老板端可查看的下单须知', details=notice)
    add('support_contact', selectable_cards().exists(), '请启用至少一张绑定本店在职后台员工的客服名片')

    catalog = ServiceItem.objects.filter(visible_service_q(), is_addon=False).exclude(service_category__is_gift=True)
    add('price_list', catalog.filter(account_service_q(None), price__gt=0).exists(),
        '请在本店服务目录公开至少一项有标价的陪玩服务')
    services = list(catalog.select_related('service_category', 'game_category', 'gameplay').order_by('id'))
    evidence = selected_configuration_evidence(permissions)
    # An unconfigured reservation is disabled, while a saved configuration
    # must be valid and confirmed. Room timing applies to published rooms.
    required = {'capacity', 'grab_window', 'settlement_clocks', 'deposit_targets',
                'deposit_percent', 'withdraw', 'order_account_fields'}
    from console.config_readiness import _configured_gifts
    gifts_allowed = (not getattr(settings, 'LICENSE_BENEFIT_ENFORCEMENT_ENABLED', True)
                     or (license_status['allow_new_benefits']
                         and license_status['feature_modes'].get('GIFTS') == 'FULL'))
    if gifts_allowed:
        if _configured_gifts().exists():
            required.add('gift_pricing')
        else:
            add('gift_pricing', True, '未启用礼物商品，无需核对礼物价格和抽成')
    else:
        add('gift_pricing', True, '当前门店授权不开放新赠礼')
    from users.pass_rules import pass_sales_enabled
    try:
        if pass_sales_enabled():
            required.add('provider_pass')
        else:
            add('provider_pass', True, '通行证新购已停用，既有通行证权益不受影响')
            required.add('no_pass_delay')
    except ValueError:
        add('provider_pass', False, '通行证销售开关无效，请先核对')
    if operation is not None and operation.points_enabled:
        required.add('points_award_rates')
    else:
        add('points_award_rates', operation is not None,
            '积分发放已停用' if operation is not None else '缺少门店积分基础配置')
    if RechargePackage.objects.filter(active=True).exists():
        # Prepaid principal is available without bonus tiers. If a store does
        # enable a bonus tier, its actual amount and gift must be reviewed.
        required.add('recharge_packages')
    else:
        add('recharge_packages', True, '未启用预存赠送档位，充值不产生档位赠送')
    if SystemConfig.objects.filter(key=RESERVATION_KEY).exists():
        required.add('reservation_deadlines')
    room_configured = any(service.room_enabled for service in services)
    if room_configured:
        required.add('room_confirmation')
    found = set()
    for row in evidence:
        if row['key'] in required:
            found.add(row['key'])
            acceptable = ('SAVED', 'CONFIRMED') if row['key'] == 'order_account_fields' else ('CONFIRMED',)
            add(row['key'], row['status'] in acceptable, row['message'])
    for key in sorted(required - found):
        add(key, False, '缺少开业配置核验结果，请联系平台核对')
    # A saved collection window is only a rule, not a working dispatch
    # pipeline. Both public customer orders and staff-arranged new orders use
    # the committed opening and the corresponding single/team arbitration.
    from orders.grab_clock_contract import production_enabled as grab_production_enabled
    from orders.grab_single_flow import enabled as single_grab_enabled
    from orders.grab_team_flow import enabled as team_grab_enabled
    from stores.db import connection
    missing_grab = []
    if connection.vendor != 'mysql':
        missing_grab.append('门店 MySQL 抢单时钟')
    if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        missing_grab.append('陪玩业务周期集成')
    if not getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False):
        missing_grab.append('付款后正式开池')
    if not getattr(settings, 'GRAB_SINGLE_WINDOW_ENABLED', False):
        missing_grab.append('单陪抢单窗口')
    if not getattr(settings, 'GRAB_TEAM_WINDOW_ENABLED', False):
        missing_grab.append('双陪抢单窗口')
    grab_ready = bool(grab_production_enabled() and single_grab_enabled() and team_grab_enabled())
    add('grab_dispatch', grab_ready,
        '正式单陪与双陪抢单可用' if grab_ready else '老板未指定及客服安排的新单须先启用正式单陪和双陪抢单能力',
        details={'missing': missing_grab, 'production_clock': grab_production_enabled(),
                 'single_window': bool(single_grab_enabled()), 'team_window': bool(team_grab_enabled())})
    if room_configured:
        room_enabled = all(getattr(settings, key, True) for key in (
            'ROOM_COMPLETION_REVIEW_ENABLED', 'ROOM_BOSS_CONFIRMATION_ENABLED',
            'ROOM_AUTO_CONFIRMATION_ENABLED'))
        add('room_completion', room_enabled, '已上架房间服务须启用完整的客服核实、老板确认和超时确认流程')

    timeout = OrderTimeoutConfig.objects.filter(pk=1).first()
    add('order_timeout', bool(timeout and timeout.revision > 0 and 1 <= timeout.minutes <= 1440),
        '订单总等待时间须由本店明确配置，不能仅使用默认值')

    add('services', bool(services), '至少配置一项上架的陪玩服务')
    issues = []
    profiles = {p.game_category_id: p for p in GameCommissionProfile.objects.all()}
    for service in services:
        problems = []
        if not service.game_category_id or not service.service_category_id:
            problems.append('须关联启用的游戏及服务分类')
        if service.price <= 0 or service.price % 100:
            problems.append('服务标价须大于零且为 10 店币的整数倍')
        if not service.price_unit.strip():
            problems.append('须填写计价单位')
        if not service.experience_kind and service.commission_policy != 'EXPERIENCE':
            profile = profiles.get(service.game_category_id)
            if service.commission_override:
                problems.append('服务独立抽成与游戏抽成尚未衔接')
            elif profile is None:
                problems.append('本店该游戏尚未配置抽成')
            else:
                try:
                    if profile.game == GameCommissionProfile.Game.DELTA:
                        # Delta uses each provider's own qualified grade.
                        # Check that payment can freeze rules for this service
                        # without assuming the service's minimum grade is the
                        # eventual provider's commission grade.
                        from orders.delta_personal_commission import freeze_rules
                        freeze_rules(service, profile)
                    else:
                        preview_rate(profile, service)
                except (ValueError, TypeError) as exc:
                    problems.append(str(exc))
        if problems:
            issues.append({'id': service.pk, 'name': service.name, 'issues': problems})
    add('service_configuration', bool(services) and not issues,
        '逐项核对本店上架服务的价格、分类及适用抽成', details=issues)
    commission_enabled = getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False)
    add('game_commission_enforcement', commission_enabled,
        '游戏抽成必须实际接入订单计价，不能仅保存草稿')

    blocked = sum(row['status'] == 'BLOCKED' for row in checks)
    enabled = bool(getattr(settings, 'STORE_FIRST_OPEN_ENABLED', False))
    return {'capability': 'OPENING_DIAGNOSTIC_V1', 'checks': checks, 'blocked_count': blocked,
            'opening_implemented': enabled, 'can_open': enabled and blocked == 0,
            'message': ('配置已核对，可由平台提交开业' if enabled and not blocked else
                        '开业条件尚未满足，请先处理待核对项目' if enabled else
                        '当前为部分只读核对；完整开业校验及启用事务尚未开放')}
