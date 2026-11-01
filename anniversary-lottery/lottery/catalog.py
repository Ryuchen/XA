"""2026 半周年海报原始 108 格及公开的联合骰子概率（百万分之一）。"""
from datetime import datetime
from zoneinfo import ZoneInfo

DENOMINATOR = 1_000_000
STARTS_AT = datetime(2026, 10, 16, tzinfo=ZoneInfo('Asia/Shanghai'))
ENDS_AT = datetime(2026, 10, 26, tzinfo=ZoneInfo('Asia/Shanghai'))
THANKS = '感谢参与'
RETRY = '再来一次'
MATRICES = {
    'small': [
        [THANKS, '520积分', THANKS, '120积分', THANKS, '冠名5天'],
        ['240积分', THANKS, '高亮TAG*1', THANKS, '360积分', '5元店铺余额'],
        [THANKS, '冠名7天', RETRY, THANKS, '240积分', '95折优惠券'],
        ['10元店铺余额', THANKS, '120积分', '冠名3天', THANKS, '冠名7天'],
        [THANKS, '360积分', THANKS, '240积分', THANKS, '冠名5天'],
        ['95折优惠券', THANKS, THANKS, '360积分', '冠名3天', THANKS],
    ],
    'red': [
        ['冠名7天', THANKS, '360积分', THANKS, '520积分', THANKS],
        ['9折优惠券', THANKS, RETRY, '360积分', THANKS, '冠名14天'],
        [THANKS, '360积分', '高亮TAG*2', THANKS, '666积分', THANKS],
        ['冠名3天', THANKS, '20元店铺余额', THANKS, '10元店铺余额', '360积分'],
        [THANKS, '520积分', THANKS, '冠名7天', THANKS, '520积分'],
        [THANKS, '9折优惠券', THANKS, '360积分', THANKS, '冠名7天'],
    ],
    'grand': [
        [THANKS, '520积分', THANKS, '冠名14天', THANKS, THANKS],
        ['85折优惠券', THANKS, '3344积分', THANKS, '520积分', THANKS],
        ['40元店铺余额', THANKS, '冠名7天', '1314积分', THANKS, '520积分'],
        [THANKS, RETRY, THANKS, '冠名31天', '高亮TAG*3', THANKS],
        ['20元店铺余额', THANKS, '520积分', THANKS, '冠名14天', THANKS],
        [THANKS, '1314积分', THANKS, '85折优惠券', THANKS, '20元店铺余额'],
    ],
}
NAMES = {'small': '小红奖池', 'red': '大红奖池', 'grand': '大大红奖池'}
PITY_AMOUNTS = {
    'small': {30: 500, 50: 1000},
    'red': {30: 1000, 50: 2000},
    'grand': {30: 2000, 50: 4000},
}


def reward_spec(label):
    if label.endswith('积分'):
        value = int(label[:-2])
        return {'code': f'points-{value}', 'kind': 'POINTS', 'point_value': value,
                'weight': {120: 100, 240: 75, 360: 60, 520: 40, 666: 25, 1314: 10, 3344: 2}[value]}
    if label.endswith('元店铺余额'):
        value = int(label.split('元')[0])
        return {'code': f'balance-{value}', 'kind': 'BALANCE', 'balance_amount': value * 100,
                'weight': {5: 60, 10: 35, 20: 15, 40: 5}[value]}
    if label.startswith('冠名'):
        days = int(label[2:-1])
        return {'code': f'naming-{days}', 'kind': 'NAMING', 'duration_days': days,
                'weight': {3: 60, 5: 40, 7: 25, 14: 10, 31: 2}[days]}
    if label.startswith('高亮TAG*'):
        quantity = int(label.split('*')[1])
        return {'code': f'tag-{quantity}', 'kind': 'TAG', 'quantity': quantity,
                'highlighted': True, 'weight': {1: 12, 2: 6, 3: 2}[quantity]}
    rate = {'95折优惠券': 95, '9折优惠券': 90, '85折优惠券': 85}[label]
    return {'code': f'coupon-{rate}', 'kind': 'COUPON', 'coupon_rate': rate,
            'weight': {95: 80, 90: 60, 85: 35}[rate]}


def _allocate(cells, total, weights):
    """Largest remainder preserves the exact public category probability."""
    denominator = sum(weights)
    values = [total * weight // denominator for weight in weights]
    left = total - sum(values)
    priority = sorted(range(len(cells)), key=lambda i: (-(total * weights[i] % denominator), i))
    for index in priority[:left]:
        values[index] += 1
    for cell, probability in zip(cells, values):
        cell['probability'] = probability


def catalog_cells(slug):
    cells = []
    for row, labels in enumerate(MATRICES[slug], 1):
        for column, label in enumerate(labels, 1):
            kind = 'THANKS' if label == THANKS else 'RETRY' if label == RETRY else 'REWARD'
            cells.append({'row': row, 'column': column, 'label': label, 'kind': kind,
                          'reward_data': reward_spec(label) if kind == 'REWARD' else {}})
    for kind, total in [('THANKS', 600_000), ('RETRY', 30_000), ('REWARD', 370_000)]:
        group = [cell for cell in cells if cell['kind'] == kind]
        _allocate(group, total, [cell['reward_data'].get('weight', 1) for cell in group])
    return cells
