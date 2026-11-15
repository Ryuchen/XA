"""Approved starting prices, separate from the historical source price book.

Amounts use the existing ledger unit (100 per yuan, 10 per store coin).
These are defaults for newly imported items, never automatic price updates.
"""

from django.core.management.base import CommandError


# catalog_key: (source family, platform, difficulty, original amount, initial amount)
APPROVED_INITIAL_PRICES = {
    'xa-price-048fb2e82e25ca1ad3fd98fc': ('物资银行加仓', 'MOBILE', 'TOP_SECRET', 12800, 10800),
    'xa-price-3871a8b2694a4d11bcad4ffe': ('环太平洋预言家', 'PC', 'TOP_SECRET', 5800, 6800),
    'xa-price-a3003ef726fd218305789c3b': ('盲盒预言家', 'PC', 'TOP_SECRET', 5800, 6800),
    'xa-price-2c7ee974e3bcf9d4e84f9cd0': ('环太平洋预言家', 'PC', 'CONFIDENTIAL', 5800, 4800),
    'xa-price-bb667d6fee99989d39749afc': ('盲盒预言家', 'PC', 'CONFIDENTIAL', 2800, 4800),
    'xa-price-817e8f1e23d0dd5ba034ab1c': ('跑刀附加', 'PC', 'ANY', 350, 400),
}

REFERENCE_NAMES = {
    '物资银行加仓': '物资银行加仓 · 追加保底',
    '环太平洋预言家': '环太平洋预言家 · 每局加购',
    '盲盒预言家': '盲盒预言家 · 每局加购',
    '跑刀附加': '跑刀附加 · 百万房卡',
}


def approved_initial_prices(rows):
    """Reject source/key drift instead of silently adjusting a different item."""
    by_key = {row['catalog_key']: row for row in rows}
    for key, (family, platform, difficulty, source_price, _) in APPROVED_INITIAL_PRICES.items():
        row = by_key.get(key)
        if (not row or not row.get('is_addon') or row.get('family') != family
                or row.get('platform') != platform or row.get('security_level') != difficulty
                or row.get('price') != source_price):
            raise CommandError('已确认的初始参考价与历史价目不匹配，请先核对：' + key)
    return {key: value[-1] for key, value in APPROVED_INITIAL_PRICES.items()}


def current_price_rules(row):
    """Do not copy obsolete embedded prices into new editable store rules."""
    rules = row['rules']
    if (row['platform'] == 'PC' and row['family'] in
            ('环太平洋', '环太平洋预言家', '盲盒猜猜乐', '盲盒预言家')):
        return rules.replace('+58r', '按本店预言家加购价').replace('+28r', '按本店预言家加购价')
    return rules


def store_price_references():
    """Expose approved references and the current tenant's independently set prices."""
    from .models import ServiceItem
    existing = {item.catalog_key: item for item in ServiceItem.objects.filter(
        catalog_key__in=APPROVED_INITIAL_PRICES).select_related('game_category')}
    result = []
    for key, (family, platform, difficulty, _, price) in APPROVED_INITIAL_PRICES.items():
        item = existing.get(key)
        identity_matches = bool(item and item.is_addon and item.game_category_id
            and item.game_category.name == '三角洲行动' and item.platform == platform
            and item.security_level == difficulty)
        result.append({'catalog_key': key, 'reference_name': REFERENCE_NAMES[family],
            'platform': platform, 'security_level': difficulty, 'reference_price': price,
            'status': 'NOT_CREATED' if not item else 'FOUND' if identity_matches else 'IDENTITY_REVIEW',
            'current': None if not item else {'id': item.pk, 'name': item.name, 'price': item.price,
                'is_active': item.is_active, 'is_addon': item.is_addon, 'platform': item.platform,
                'security_level': item.security_level,
                'game_name': item.game_category.name if item.game_category_id else ''}})
    return result
