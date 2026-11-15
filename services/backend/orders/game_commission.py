"""Store-local game commissions with feature-gated payment snapshots."""
from .models import GameCommissionProfile, Gameplay

RULE_FIELDS = ('provider_kind', 'platform', 'security_level', 'grade')
VALID = {
    'provider_kind': {'GUARD', 'COMPANION', 'GRIND', 'RANK'},
    'platform': {'', 'PC', 'MOBILE'},
    'security_level': {'', 'ANY', 'CONFIDENTIAL', 'TOP_SECRET'},
    'grade': {'', 'ENTERTAINMENT', 'TECHNICAL', 'MASTER', 'KING', 'LEGEND', 'GLORY', 'STAR', 'OPEN'},
}


def _rate(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError('抽成比例须为 0–100 的整数，未配置请留空')
    return value


def validate_rules(game, rules):
    if game not in GameCommissionProfile.Game.values or not isinstance(rules, dict):
        raise ValueError('请选择有效的游戏分类与规则')
    if game != GameCommissionProfile.Game.DELTA:
        if set(rules) != {'companion', 'grind'}:
            raise ValueError('瓦罗兰特与英雄联盟须分别填写陪玩和代肝两类抽成')
        return {key: _rate(rules[key], nullable=True) for key in ('companion', 'grind')}
    if set(rules) != {'scopes'} or not isinstance(rules['scopes'], list) or len(rules['scopes']) > 300:
        raise ValueError('三角洲规则须为不超过 300 条的范围列表')
    clean = []
    gameplay_ids = set()
    for item in rules['scopes']:
        if not isinstance(item, dict) or set(item) != {*RULE_FIELDS, 'gameplay_id', 'rate'}:
            raise ValueError('每条三角洲规则须填写方向、端、难度、档位、玩法和比例')
        if any(type(item[key]) is not str or item[key] not in choices for key, choices in VALID.items()):
            raise ValueError('三角洲抽成范围包含无效选项')
        gameplay = item['gameplay_id']
        if gameplay is not None and (type(gameplay) is not int or gameplay <= 0):
            raise ValueError('玩法须选择有效项目或选择全部玩法')
        if gameplay is not None:
            gameplay_ids.add(gameplay)
        row = {key: item[key] for key in RULE_FIELDS}
        row.update(gameplay_id=gameplay, rate=_rate(item['rate']))
        # A wildcard and a concrete gameplay in the same scope would overlap.
        if any(all(row[key] == old[key] for key in RULE_FIELDS) and
               (row['gameplay_id'] is None or old['gameplay_id'] is None or
                row['gameplay_id'] == old['gameplay_id']) for old in clean):
            raise ValueError('三角洲抽成范围重复或与全部玩法规则冲突')
        clean.append(row)
    if gameplay_ids and set(Gameplay.objects.filter(pk__in=gameplay_ids).values_list('pk', flat=True)) != gameplay_ids:
        raise ValueError('抽成规则引用的玩法不存在')
    return {'scopes': clean}


def preview_rate(profile, service, *, validated_rules=None):
    """Return an explicit draft match; never infer a rate or a game name."""
    if profile.game_category_id != service.game_category_id:
        raise ValueError('服务不属于此游戏类目')
    rules = validate_rules(profile.game, profile.rules) if validated_rules is None else validated_rules
    if profile.game != GameCommissionProfile.Game.DELTA:
        kind = {'COMPANION': 'companion', 'GRIND': 'grind', 'RANK': 'grind'}.get(service.provider_kind)
        if kind is None:
            raise ValueError('该服务方向未明确映射陪玩或代肝抽成')
        rate = rules[kind]
        scope = {'provider_kind': kind}
    else:
        keys = {'provider_kind': service.provider_kind, 'platform': service.platform,
                'security_level': service.security_level, 'grade': service.provider_grade}
        match = [row for row in rules['scopes'] if all(row[key] == value for key, value in keys.items())
                 and (row['gameplay_id'] is None or row['gameplay_id'] == service.gameplay_id)]
        if len(match) > 1:
            raise ValueError('该服务匹配到多条抽成规则')
        rate = match[0]['rate'] if match else None
        scope = {key: match[0][key] for key in (*RULE_FIELDS, 'gameplay_id')} if match else keys
    if rate is None:
        raise ValueError('本店该服务尚未配置抽成')
    return {'game_category_id': profile.game_category_id, 'game': profile.game,
            'version': profile.version, 'rate': rate, 'scope': scope}


_UNFETCHED = object()


def verify_payment_shares(snapshot, specs):
    """A configured game rate must match every quoted provider share."""
    if not snapshot:
        return
    if (not isinstance(snapshot, dict) or type(snapshot.get('rate')) is not int
            or any(not isinstance(spec, dict) or spec.get('commission_type', 'PERCENT') != 'PERCENT'
                   or type(spec.get('commission_rate')) is not int
                   or spec['commission_rate'] != snapshot['rate'] for spec in specs)):
        raise ValueError('游戏抽成份额与付款快照不一致，请重新报价')


def payment_snapshot(service, *, profile=_UNFETCHED, existing_order=False, provider=None,
                     legacy_service_rate=False):
    """Resolve a regular-service rule; special services keep their own rules."""
    from django.conf import settings
    if not existing_order and not getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False):
        return {}
    if (service.experience_kind or service.commission_policy == 'EXPERIENCE'
            or service.is_addon or (service.service_category_id and service.service_category.is_gift)):
        return {}
    if service.commission_override:
        raise ValueError('该服务的独立抽成与游戏规则尚未完成衔接，请联系管理员')
    if not service.game_category_id:
        raise ValueError('本服务尚未关联游戏，不能计算抽成')
    if profile is _UNFETCHED:
        profile = GameCommissionProfile.objects.filter(game_category_id=service.game_category_id).first()
    if profile is None:
        raise ValueError('本店该游戏尚未配置抽成')
    if profile.game == GameCommissionProfile.Game.DELTA and not legacy_service_rate:
        if provider is None:
            from .delta_personal_commission import pending_payment_snapshot
            return pending_payment_snapshot(service, profile)
        from .delta_personal_commission import freeze_rules, resolve_provider
        frozen = freeze_rules(service, profile)
        data = resolve_provider(frozen, service, provider)
        data['personal_rules'] = frozen
        return data
    data = preview_rate(profile, service)
    data['service_scope'] = service_scope(service)
    return data


def service_scope(service):
    return {'service_id': service.pk, 'game_category_id': service.game_category_id,
        'gameplay_id': service.gameplay_id, 'provider_kind': service.provider_kind,
        'platform': service.platform, 'security_level': service.security_level,
        'grade': service.provider_grade, 'is_addon': service.is_addon,
        'experience_kind': service.experience_kind, 'commission_policy': service.commission_policy,
        'service_category_id': service.service_category_id, 'is_active': service.is_active,
        'commission_override': service.commission_override, 'catalog_family': service.catalog_family,
        'required_level_id': service.required_level_id,
        'required_level_rank': service.required_level.sort_order if service.required_level_id else None}


def lock_and_verify_payment_snapshot(service_id, expected, *, provider=None):
    """Lock service, then category and profile; compare with the prepared plan.

    Call after service and provider locks, before any debit. Configuration
    writes lock category then profile, so they cannot race the final check.
    """
    from stores.db import transaction
    from .models import GameCategory, ServiceItem
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抽成核对必须在付款事务内进行')
    service = ServiceItem.objects.select_for_update().get(pk=service_id)
    if service.required_level_id:
        from users.models import EscortLevel
        required_level = EscortLevel.objects.select_for_update().get(pk=service.required_level_id)
        service._state.fields_cache['required_level'] = required_level
    profile = None
    if service.game_category_id:
        GameCategory.objects.select_for_update().get(pk=service.game_category_id)
        profile = GameCommissionProfile.objects.select_for_update().filter(game_category_id=service.game_category_id).first()
    try:
        actual = payment_snapshot(service, profile=profile, provider=provider)
    except ValueError:
        raise ValueError('游戏抽成或服务配置已变化，请刷新报价后重试') from None
    if actual != expected:
        raise ValueError('游戏抽成或服务配置已变化，请刷新报价后重试')


def lock_transfer_snapshot(order, catalog_service, *, provider=None):
    """Capture the current store rule against the purchased, not mutable, scope.

    The caller holds the order and service rows and takes the category lock in
    the same order as the configuration writer. No money is settled here.
    """
    from django.conf import settings
    from stores.db import transaction
    from .models import GameCategory, GameCommissionProfile, OrderPaidServiceScope
    from .prestart_handoff import _purchased_service
    from .state_machine import IllegalTransitionError
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转入抽成快照必须在订单事务内生成')
    from .paid_service_scope import enabled as paid_scope_enabled
    if (not isinstance(order.game_commission_snapshot, dict) or not order.game_commission_snapshot
            or not paid_scope_enabled()
            or not OrderPaidServiceScope.objects.filter(order=order).exists()):
        raise IllegalTransitionError('原订单服务范围或游戏抽成未完成核对')
    purchased, _ = _purchased_service(order, catalog_service)
    if order.game_commission_snapshot.get('personal_rules') and provider is not None:
        from users.qualifications import service_skill
        scope_fields = ('game_category_id', 'provider_kind', 'platform', 'security_level',
                        'provider_grade', 'gameplay_id', 'catalog_family')
        if (any(getattr(purchased, key) != getattr(catalog_service, key) for key in scope_fields)
                and not service_skill(provider, purchased, exclude_service_ids={catalog_service.pk})):
            raise IllegalTransitionError('商品服务范围已变化，接替陪玩缺少可核实的原服务技能')
    if not purchased.game_category_id:
        raise IllegalTransitionError('原订单游戏分类缺失，请先核对')
    if not GameCategory.objects.select_for_update().filter(pk=purchased.game_category_id).exists():
        raise IllegalTransitionError('原订单游戏分类不存在，请先核对')
    profile = GameCommissionProfile.objects.select_for_update().filter(game_category_id=purchased.game_category_id).first()
    try:
        personal = bool(order.game_commission_snapshot.get('personal_rules'))
        if personal and (profile is None or profile.game != GameCommissionProfile.Game.DELTA):
            raise ValueError('原订单三角洲游戏规则已变化，请先核对')
        snapshot = payment_snapshot(purchased, profile=profile, existing_order=True,
                                    provider=provider, legacy_service_rate=not personal)
    except ValueError as exc:
        raise IllegalTransitionError(str(exc)) from exc
    if not snapshot:
        raise IllegalTransitionError('该订单不能按游戏抽成规则转单')
    # A promotion enabled after payment is a new-sale rule. The incoming
    # participant uses the store's current game rule for the purchased scope.
    return snapshot
