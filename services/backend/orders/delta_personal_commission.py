"""Payment-time Delta rule evidence and per-provider resolution.

These helpers do not authorize a payment or dispatch.  Callers must perform
normal eligibility checks and serialize the service, provider qualification
and game-rule reads with their respective writers inside the business
transaction before persisting either result.
"""
from copy import deepcopy

from .game_commission import RULE_FIELDS, service_scope, validate_rules
from .models import GameCommissionProfile


def mixed_funding_available():
    """Use existing ordinary funding authorization; do not authorize it here."""
    from django.conf import settings
    from .points_checkout_test import ordinary_settlement_enabled
    return bool(getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False)
                and ordinary_settlement_enabled())


def points_funding_available():
    """Use the existing points settlement capability, without another product switch."""
    from .points_checkout_test import settlement_enabled
    return settlement_enabled()


def verify_formal_points_checkout(data, *, account, service):
    """Validate D202 terms inside an existing bound checkout capability.

    This is evidence verification only. It does not authorize checkout or
    imply that assignment, refund, funding and settlement are available.
    Callers must also hold the ordinary service and provider-dispatch locks
    in their established order; the capability alone does not provide them.
    """
    from rest_framework.exceptions import ValidationError
    from stores.db import transaction
    from .models import Order
    from .points_business_access import permits
    if not permits(account=account, service=service, phase='checkout', kind='service'):
        raise ValidationError('积分付款事务与本店账号、服务不一致')
    people = data.get('provider_accounts') if isinstance(data, dict) else None
    evidence = data.get('personal_game_snapshots') if isinstance(data, dict) else None
    snapshot = data.get('commission_snapshot') if isinstance(data, dict) else None
    count = data.get('required_provider_count') if isinstance(data, dict) else None
    if (not isinstance(people, (list, tuple)) or type(count) is not int or count not in (1, 2)
            or not isinstance(snapshot, dict) or not isinstance(snapshot.get('game_commission_snapshot'), dict)
            or data.get('service') != service
            or data.get('dispatch_route') not in Order.DispatchRoute.values
            or getattr(getattr(data.get('service'), '_state', None), 'db', None) != transaction.get_connection().alias):
        raise ValidationError('积分个人抽成付款范围待核对')
    root = snapshot['game_commission_snapshot']
    if data.get('commission_allocation_pending'):
        if people or evidence or data.get('commission_rate') is not None or 'rate' in root:
            raise ValidationError('积分待分配抽成快照不一致')
        try:
            lock_and_verify_pending_snapshot(service.pk, root)
        except ValueError as exc:
            raise ValidationError('积分付款规则已变化，请重新确认') from exc
        return True
    if (len(people) != count or not isinstance(evidence, list) or len(evidence) != count
            or root != evidence[0]
            or any(type(getattr(person, 'pk', None)) is not int or person.pk <= 0
                   or getattr(getattr(person, '_state', None), 'db', None) != transaction.get_connection().alias
                   for person in people)
            or len({person.pk for person in people}) != count):
        raise ValidationError('积分个人抽成付款范围待核对')
    from .game_commission import lock_and_verify_payment_snapshot
    try:
        profiles = {profile.account_id: profile for profile in lock_providers([person.pk for person in people])}
        for person, expected in zip(people, evidence):
            lock_and_verify_payment_snapshot(service.pk, expected, provider=profiles[person.pk])
    except ValueError as exc:
        raise ValidationError('积分付款个人资格或抽成规则已变化，请重新确认') from exc
    if data.get('commission_rate') != evidence[0].get('rate'):
        raise ValidationError('积分付款个人抽成快照不一致')
    if count == 1:
        spec = data.get('commission_spec')
        if (not isinstance(spec, dict) or spec.get('commission_type', 'PERCENT') != 'PERCENT'
                or spec.get('commission_rate') != evidence[0].get('rate')):
            raise ValidationError('积分付款个人分账比例不一致')
    if count == 2:
        specs = data.get('provider_specs')
        if (not isinstance(specs, list) or len(specs) != count
                or any(not isinstance(spec, dict) or spec.get('commission_type', 'PERCENT') != 'PERCENT'
                       or spec.get('commission_rate') != item.get('rate')
                       for spec, item in zip(specs, evidence))):
            raise ValidationError('积分付款双陪个人抽成不一致')
    return True


def lock_providers(account_ids):
    """Hold provider locks in account order while resolving personal rules."""
    from stores.db import transaction
    from users.models import EscortProfile
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('本人抽成资格必须在业务事务中锁定')
    ids = sorted(set(account_ids))
    profiles = list(EscortProfile.objects.select_for_update().filter(account_id__in=ids)
                    .order_by('account_id', 'pk'))
    if len(profiles) != len(ids):
        raise ValueError('陪玩资料不存在，请刷新后重试')
    return profiles


def verify_payment_shares(accounts, snapshots, specs):
    """Match each quoted share to the same provider's locked rate evidence."""
    if (len(accounts) != len(snapshots) or len(accounts) != len(specs)
            or not accounts or len({account.pk for account in accounts}) != len(accounts)):
        raise ValueError('陪玩抽成份额与付款快照不一致，请重新报价')
    for account, evidence, spec in zip(accounts, snapshots, specs):
        qualification = evidence.get('qualification') if isinstance(evidence, dict) else None
        if (not isinstance(qualification, dict) or qualification.get('account_id') != account.pk
                or not isinstance(spec, dict) or spec.get('commission_type', 'PERCENT') != 'PERCENT'
                or type(spec.get('commission_rate')) is not int
                or spec['commission_rate'] != evidence.get('rate')):
            raise ValueError('陪玩抽成份额与付款快照不一致，请重新报价')


def freeze_rules(service, profile):
    """Freeze this store's rules for the purchased scope, before payment."""
    from users.qualifications import grade_options, service_requirement
    if (profile is None or profile.game != GameCommissionProfile.Game.DELTA
            or profile.game_category_id != service.game_category_id):
        raise ValueError('本店三角洲抽成配置与服务不一致')
    requirement = service_requirement(service)
    if not requirement or requirement.get('invalid'):
        raise ValueError('本店三角洲服务缺少可核实的资格档位')
    eligible_grades = grade_options(requirement['provider_kind'], requirement['platform'],
                                    requirement['security_level'])[requirement['rank']:]
    rules = validate_rules(profile.game, profile.rules)
    scope = service_scope(service)
    rows = [deepcopy(row) for row in rules['scopes']
            if all(row[key] == scope[key] for key in ('provider_kind', 'platform', 'security_level'))
            and (row['gameplay_id'] is None or row['gameplay_id'] == scope['gameplay_id'])]
    if not rows or not any(row['grade'] in eligible_grades for row in rows):
        raise ValueError('本店该三角洲服务尚未配置档位抽成')
    return {'kind': 'DELTA_PERSONAL_V1', 'game': profile.game,
            'game_category_id': profile.game_category_id, 'version': profile.version,
            'service_scope': scope, 'rules': rows}


def resolve_provider(frozen, service, provider, *, exclude_service_ids=()):
    """Resolve one person's qualified grade against frozen, not live, rules."""
    from users.qualifications import matching_qualification, service_requirement, service_skill

    if (not isinstance(frozen, dict) or frozen.get('kind') != 'DELTA_PERSONAL_V1'
            or frozen.get('game') != GameCommissionProfile.Game.DELTA
            or frozen.get('service_scope') != service_scope(service)
            or frozen.get('game_category_id') != service.game_category_id
            or type(frozen.get('version')) is not int or frozen['version'] < 1
            or not isinstance(frozen.get('rules'), list)):
        raise ValueError('三角洲订单抽成规则快照不完整，请先核对')
    requirement = service_requirement(service)
    qualification = matching_qualification(provider, requirement)
    if qualification is None or not service_skill(provider, service, exclude_service_ids=exclude_service_ids):
        raise ValueError('陪玩缺少订单范围内的资格或玩法授权')
    grade = qualification.grade
    rows = [row for row in frozen['rules'] if isinstance(row, dict)
            and row.get('grade') == grade]
    if len(rows) != 1:
        raise ValueError('本店未配置该陪玩的适用档位抽成，请先核对')
    row = rows[0]
    scope = frozen['service_scope']
    if (set(row) != {*RULE_FIELDS, 'gameplay_id', 'rate'}
            or any(row[key] != scope[key] for key in ('provider_kind', 'platform', 'security_level'))
            or row['gameplay_id'] not in (None, scope['gameplay_id'])
            or type(row['rate']) is not int or not 0 <= row['rate'] <= 100):
        raise ValueError('三角洲订单抽成规则快照无效，请先核对')
    return {'game_category_id': frozen['game_category_id'], 'game': frozen['game'],
            'version': frozen['version'], 'rate': row['rate'],
            'scope': {key: row[key] for key in (*RULE_FIELDS, 'gameplay_id')},
            'service_scope': deepcopy(scope),
            'qualification': {'id': qualification.pk, 'grade': grade,
                              'provider_id': provider.pk, 'account_id': provider.account_id}}


def verify_assignment_evidence(order, assignment):
    """Verify captured personal terms against payment rules, without live rates."""
    root = order.game_commission_snapshot
    frozen = root.get('personal_rules') if isinstance(root, dict) else None
    evidence = assignment.game_commission_snapshot
    if not frozen:
        return evidence == root
    if not isinstance(frozen, dict) or not isinstance(evidence, dict):
        return False
    qualification = evidence.get('qualification')
    scope = evidence.get('scope')
    service = frozen.get('service_scope')
    if (frozen.get('kind') != 'DELTA_PERSONAL_V1' or not isinstance(service, dict)
            or root.get('game') != frozen.get('game')
            or root.get('game_category_id') != frozen.get('game_category_id')
            or root.get('version') != frozen.get('version')
            or root.get('service_scope') != service
            or evidence.get('personal_rules') != frozen or not isinstance(qualification, dict)
            or not isinstance(scope, dict) or evidence.get('game') != GameCommissionProfile.Game.DELTA
            or evidence.get('game') != frozen.get('game')
            or evidence.get('game_category_id') != frozen.get('game_category_id')
            or evidence.get('version') != frozen.get('version')
            or evidence.get('service_scope') != frozen.get('service_scope')
            or type(qualification.get('account_id')) is not int
            or qualification['account_id'] != assignment.provider_account_id
            or type(qualification.get('id')) is not int or qualification['id'] < 1
            or type(qualification.get('provider_id')) is not int or qualification['provider_id'] < 1
            or qualification.get('grade') != scope.get('grade')
            or any(scope.get(key) != service.get(key) for key in ('provider_kind', 'platform', 'security_level'))
            or scope.get('gameplay_id') not in (None, service.get('gameplay_id'))
            or type(evidence.get('rate')) is not int
            or evidence.get('rate') != assignment.commission_rate
            or assignment.commission_type != 'PERCENT' or assignment.commission_fixed):
        return False
    rules = frozen.get('rules')
    if not isinstance(rules, list):
        return False
    matches = [row for row in rules if isinstance(row, dict) and
               {key: row.get(key) for key in (*RULE_FIELDS, 'gameplay_id')} == scope]
    return (len(matches) == 1 and type(matches[0].get('rate')) is int
            and 0 <= matches[0]['rate'] <= 100 and matches[0]['rate'] == evidence['rate'])


def verify_segment_evidence(segment, snapshot):
    """Check the participant's own entry rule, including after a transfer."""
    from types import SimpleNamespace
    evidence = snapshot.game_commission_snapshot
    root = segment.attribution.order.game_commission_snapshot
    personal_order = isinstance(root, dict) and bool(root.get('personal_rules'))
    if not isinstance(evidence, dict) or not evidence.get('personal_rules'):
        if personal_order:
            return False
        # Experience and other non-game commissions have no game snapshot.
        # For a game order, a later assignee may have a newer rule version or
        # rate, but its game and purchased scope must remain the original one.
        if not root:
            return not evidence
        if not isinstance(root, dict) or not isinstance(evidence, dict):
            return False
        return bool(root.get('game') in (GameCommissionProfile.Game.VALORANT,
                                        GameCommissionProfile.Game.LEAGUE)
            and evidence.get('game') == root.get('game')
            and evidence.get('game_category_id') == root.get('game_category_id')
            and evidence.get('service_scope') == root.get('service_scope')
            and isinstance(root.get('service_scope'), dict)
            and type(evidence.get('version')) is int and evidence['version'] > 0
            and type(evidence.get('rate')) is int and 0 <= evidence['rate'] <= 100
            and evidence['rate'] == snapshot.commission_rate
            and snapshot.commission_type == 'PERCENT' and snapshot.commission_fixed == 0)
    order = SimpleNamespace(game_commission_snapshot=evidence)
    assignment = SimpleNamespace(game_commission_snapshot=evidence,
        provider_account_id=segment.provider_account_id,
        commission_rate=snapshot.commission_rate,
        commission_type=snapshot.commission_type, commission_fixed=snapshot.commission_fixed)
    return verify_assignment_evidence(order, assignment)


def pending_payment_snapshot(service, profile):
    """Preserve rules without inventing a rate before a provider is known."""
    frozen = freeze_rules(service, profile)
    return {'game_category_id': frozen['game_category_id'], 'game': frozen['game'],
            'version': frozen['version'], 'service_scope': deepcopy(frozen['service_scope']),
            'personal_rules': frozen}


def lock_and_verify_pending_snapshot(service_id, expected):
    """Recheck frozen rules under the same service/category/config locks as payment."""
    from stores.db import transaction
    from .models import GameCategory, ServiceItem
    from users.models import EscortLevel
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('待分配抽成快照必须在付款事务内核对')
    service = ServiceItem.objects.select_for_update().get(pk=service_id)
    if service.required_level_id:
        service._state.fields_cache['required_level'] = EscortLevel.objects.select_for_update().get(
            pk=service.required_level_id)
    if not service.game_category_id:
        raise ValueError('游戏或服务范围已变化，请刷新后重试')
    GameCategory.objects.select_for_update().get(pk=service.game_category_id)
    profile = GameCommissionProfile.objects.select_for_update().filter(
        game_category_id=service.game_category_id).first()
    if pending_payment_snapshot(service, profile) != expected:
        raise ValueError('游戏抽成或服务范围已变化，请刷新后重试')


def resolve_order_candidate(order, provider):
    """Resolve one candidate from the order's frozen rules and purchased scope.

    A caller presenting a quote can use this read-only result. An assignment
    must repeat it after acquiring the provider and order transaction locks.
    """
    from .prestart_handoff import _purchased_service
    snapshot = order.game_commission_snapshot
    frozen = snapshot.get('personal_rules') if isinstance(snapshot, dict) else None
    if not frozen:
        raise ValueError('订单付款规则或陪玩资料不完整')
    from .state_machine import IllegalTransitionError
    try:
        purchased, _ = _purchased_service(order, order.service)
    except IllegalTransitionError as exc:
        raise ValueError(str(exc)) from exc
    scope_fields = ('game_category_id', 'provider_kind', 'platform', 'security_level',
                    'provider_grade', 'gameplay_id', 'catalog_family')
    exclude = ({order.service_id} if any(getattr(purchased, key) != getattr(order.service, key)
                                       for key in scope_fields) else ())
    evidence = resolve_provider(frozen, purchased, provider, exclude_service_ids=exclude)
    evidence['personal_rules'] = deepcopy(frozen)
    return evidence


def resolve_pending_order(order, providers, *, allow_reassignment=False):
    """Resolve candidates from payment evidence; caller owns all business locks.

    This is deliberately read-only: assignment, amounts and the pending flag
    must be saved together by the caller in the same transaction.
    """
    from stores.db import transaction
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('本人抽成分配必须在订单事务内进行')
    if ((not order.commission_allocation_pending and not allow_reassignment)
            or not order.game_commission_snapshot):
        raise ValueError('订单不是待分配的三角洲抽成')
    frozen = order.game_commission_snapshot.get('personal_rules')
    if (not frozen or not providers or len(providers) != order.required_provider_count
            or len({provider.account_id for provider in providers}) != len(providers)):
        raise ValueError('订单付款规则或陪玩资料不完整')
    return [resolve_order_candidate(order, provider) for provider in providers]


def pending_assignment_terms(order, providers, *, allow_reassignment=False):
    """Calculate each share and aggregate from the frozen payment rules."""
    from .settlement import aggregate_order_split, build_provider_shares
    evidence = resolve_pending_order(order, providers, allow_reassignment=allow_reassignment)
    shares = build_provider_shares(order.amount, [
        {'commission_type': 'PERCENT', 'commission_rate': item['rate']} for item in evidence
    ])
    inviter_rate = (order.inviter_commission_rate_snapshot
                    if order.inviter_id or order.inviter_account_id else 0)
    return shares, aggregate_order_split(order.amount, shares, inviter_rate), evidence
