"""Explicit grouping of the reviewed price book, independent of display names."""
from django.db.models import Q


def visible_service_q(prefix=''):
    """A disabled parent hides its products without modifying existing orders."""
    query = Q(**{prefix + 'is_active': True})
    for relation in ('game_category', 'service_category', 'gameplay'):
        query &= (Q(**{prefix + relation + '__isnull': True})
                  | Q(**{prefix + relation + '__is_active': True}))
    return query


def skill_service_q():
    return (visible_service_q() & Q(game_category__isnull=False, is_addon=False)
            & ~Q(service_category__is_gift=True))


FAMILY_ALIASES = {
    '物资银行加仓': ('物资银行单', '加仓'),
    '环太平洋预言家': ('环太平洋', '预言家'),
    '盲盒预言家': ('盲盒猜猜乐', '预言家'),
    '教学起装': ('甜蜜教学单', '起装'),
    '黄金矿工加购': ('黄金矿工·钓鱼版', '加购'),
    '跑刀附加': ('跑刀', '附加项目'),
    '瓦罗兰特技术': ('瓦罗兰特', '技术'),
    '瓦罗兰特加收': ('瓦罗兰特', '加收'),
}


def catalog_labels(row):
    family, prefix = FAMILY_ALIASES.get(row['family'], (row['family'], ''))
    return {'catalog_family': family, 'catalog_variant': ' · '.join(filter(None, [prefix, row['variant']]))}
