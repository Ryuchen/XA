from django.db import migrations


# 目标两级结构：顶层由 is_gift 表达（陪玩=False / 礼物=True），每条记录为子类。
PLAY_SUBCATS = [
    ('排位', 1),
    ('娱乐', 2),
    ('教学', 3),
]
GIFT_SUBCATS = [
    ('限定', 1),
    ('冠名', 2),
    ('常规', 3),
]

# 现有服务项 → 规范玩法子类的归属映射（按名称匹配，兼容“（联调）”后缀）。
PLAY_ITEM_MAP = {
    '王者荣耀排位陪玩': '排位',
    '王者荣耀 - 王者带飞': '排位',
    '无畏契约 - 英雄教学': '教学',
    '和平精英 - 甜蜜护航': '娱乐',
    '三角洲行动 - 战术护航': '娱乐',
    '暗区突围 - 武装护航': '娱乐',
    'CSGO - 完美搭档': '娱乐',
    '王者荣耀·排位上分（联调）': '排位',
    '和平精英·娱乐陪玩（联调）': '娱乐',
    '三角洲行动·战术护航（联调）': '娱乐',
    '无畏契约·英雄教学（联调）': '教学',
    '英雄联盟·语音陪伴（联调）': '娱乐',
    '永劫无间·通宵车队（联调）': '娱乐',
}
LEGACY_SUFFIX = '（联调）'


def normalize(apps, schema_editor):
    ServiceCategory = apps.get_model('orders', 'ServiceCategory')
    ServiceItem = apps.get_model('orders', 'ServiceItem')

    # 1. 建立/获取规范子类
    play_cats = {}
    for name, order in PLAY_SUBCATS:
        cat, _ = ServiceCategory.objects.get_or_create(
            name=name, defaults={'is_gift': False, 'sort_order': order, 'is_active': True},
        )
        play_cats[name] = cat
    gift_cats = {}
    for name, order in GIFT_SUBCATS:
        cat, _ = ServiceCategory.objects.get_or_create(
            name=name, defaults={'is_gift': True, 'sort_order': order, 'is_active': True},
        )
        gift_cats[name] = cat

    default_gift = gift_cats['常规']

    # 2. 迁移服务项归属
    for item in ServiceItem.objects.select_related('service_category').all():
        old_cat = item.service_category
        is_legacy = LEGACY_SUFFIX in item.name

        if old_cat is not None and old_cat.is_gift:
            # 礼物侧：默认归“常规”，除非命中已建的限定/冠名子类
            target = gift_cats.get(old_cat.name, default_gift)
        else:
            # 陪玩侧：按名称映射到玩法子类，兜底“娱乐”
            target = play_cats.get(PLAY_ITEM_MAP.get(item.name, ''), play_cats['娱乐'])

        update_fields = []
        if item.service_category_id != target.id:
            item.service_category = target
            update_fields.append('service_category')
        # 联调测试项：去掉“（联调）”后缀并下架，保留订单历史
        if is_legacy:
            item.name = item.name.replace(LEGACY_SUFFIX, '')
            update_fields.append('name')
            if item.is_active:
                item.is_active = False
                update_fields.append('is_active')
        if update_fields:
            item.save(update_fields=update_fields)

    # 3. 清理旧的、已无服务项引用的历史分类
    keep_names = set(play_cats) | set(gift_cats)
    for cat in ServiceCategory.objects.exclude(name__in=keep_names):
        if not cat.service_items.exists() and not cat.promotions.exists():
            cat.delete()


def noop(apps, schema_editor):
    # 数据规范化不可安全逆向（涉及历史归属与下架状态），保留为空以允许回滚迁移状态。
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0018_gamecategory_icon'),
        ('promotions', '0002_promotion_category_fk'),
    ]

    operations = [
        migrations.RunPython(normalize, noop),
    ]
