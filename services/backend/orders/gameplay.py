"""玩法目录与订单快照。随机结果只在创建订单的资金事务中产生一次。"""
import secrets

from rest_framework import serializers


def validate_pool(pool):
    if not isinstance(pool, list) or not 2 <= len(pool) <= 8:
        raise serializers.ValidationError('转盘需配置 2 至 8 个服务目标')
    names = set()
    clean = []
    for item in pool:
        if not isinstance(item, dict):
            raise serializers.ValidationError('奖池格式错误')
        name = str(item.get('name', '')).strip()
        description = str(item.get('description', '')).strip()
        weight = item.get('weight', 1)
        if not name or len(name) > 40 or name in names or len(description) > 300:
            raise serializers.ValidationError('服务目标名称需唯一且不超过 40 字，说明不超过 300 字')
        if type(weight) is not int or not 1 <= weight <= 10000:
            raise serializers.ValidationError('概率权重必须为 1 至 10000 的整数')
        names.add(name)
        clean.append({'name': name, 'description': description, 'weight': weight})
    return clean


def validate_service_gameplay(service, rounds=1, *, companion_assignment=False):
    from users.qualifications import service_requirement
    requirement=service_requirement(service)
    if requirement and requirement.get('invalid'):
        raise serializers.ValidationError('服务接单资格配置无效，请联系客服')
    if not service.is_active:
        raise serializers.ValidationError('该服务已下架')
    if service.game_category_id and not service.game_category.is_active:
        raise serializers.ValidationError('该游戏类目已停用')
    if service.service_category_id and not service.service_category.is_active:
        raise serializers.ValidationError('该服务分类已停用')
    if service.is_addon:
        raise serializers.ValidationError(service.purchase_note or '该费用须关联主订单，请联系客服办理')
    if (service.billing_provider_count == 2 and service.service_type == 'COMPANION'
            and service.price_unit in ('小时', '时', 'hour') and rounds != 1):
        raise serializers.ValidationError('双陪小时套餐每单1小时，请分单购买')
    if service.billing_provider_count == 2 and not companion_assignment:
        raise serializers.ValidationError('该双陪套餐需联系客服安排两名打手')
    if service.purchase_mode == 'CONSULT' and not (service.billing_provider_count == 2 and companion_assignment):
        raise serializers.ValidationError(service.purchase_note or '该服务需先联系客服确认方案与报价')
    if not service.gameplay_id:
        return
    if not service.gameplay.is_active:
        raise serializers.ValidationError('该玩法已停用')
    if not service.platform or not service.security_level:
        raise serializers.ValidationError('玩法商品尚未配置游戏平台与难度')
    if service.gameplay.kind == 'ROULETTE':
        if rounds != 1:
            raise serializers.ValidationError('转盘单每单一个服务目标，请分单购买')
        validate_pool(service.roulette_pool)


def public_pool(pool):
    total = sum(item['weight'] for item in pool)
    return [dict(item, probability=round(item['weight'] * 100 / total, 4)) for item in pool]


def make_order_snapshot(service, rounds, *, companion_assignment=False):
    if not service.gameplay_id:
        return {}, {}
    validate_service_gameplay(service, rounds, companion_assignment=companion_assignment)
    snapshot = {
        'id': service.gameplay_id, 'name': service.gameplay.name,
        'kind': service.gameplay.kind, 'platform': service.platform,
        'platform_name': service.get_platform_display(),
        'security_level': service.security_level,
        'security_level_name': service.get_security_level_display(),
        'rules': service.rules or service.gameplay.rules,
        'service_name': service.name, 'price_unit': service.price_unit,
        'billing_provider_count': service.billing_provider_count,
    }
    if service.gameplay.kind != 'ROULETTE':
        return snapshot, {}
    pool = validate_pool(service.roulette_pool)
    snapshot['pool'] = public_pool(pool)
    ticket = secrets.randbelow(sum(item['weight'] for item in pool))
    for index, item in enumerate(pool):
        ticket -= item['weight']
        if ticket < 0:
            return snapshot, {'index': index, 'name': item['name'], 'description': item['description']}


def fulfillment_text(order):
    snapshot = order.gameplay_snapshot
    if not snapshot:
        return ''
    parts = [snapshot['name'], snapshot['platform_name'], snapshot['security_level_name']]
    if order.roulette_result:
        parts.append('转盘目标：' + order.roulette_result['name'])
        if order.roulette_result.get('description'):
            parts.append(order.roulette_result['description'])
    return ' · '.join(parts)
