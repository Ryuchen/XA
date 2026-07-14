import django.db.models.deletion
from django.db import migrations, models


def map_category(apps, schema_editor):
    """把旧 category 枚举字符串映射到分类字典 FK。"""
    Promotion = apps.get_model('promotions', 'Promotion')
    ServiceCategory = apps.get_model('orders', 'ServiceCategory')
    by_gift = {c.is_gift: c for c in ServiceCategory.objects.all()}
    normal = by_gift.get(False)
    gift = by_gift.get(True)
    mapping = {'NORMAL': normal, 'GIFT': gift}
    for promo in Promotion.objects.exclude(category_legacy=''):
        cat = mapping.get(promo.category_legacy)
        if cat:
            promo.category = cat
            promo.save(update_fields=['category'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('promotions', '0001_initial'),
        ('orders', '0011_service_category_dict'),
    ]

    operations = [
        migrations.RenameField(
            model_name='promotion',
            old_name='category',
            new_name='category_legacy',
        ),
        migrations.AddField(
            model_name='promotion',
            name='category',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='promotions', to='orders.servicecategory',
                verbose_name='适用分类',
            ),
        ),
        migrations.RunPython(map_category, noop),
        migrations.RemoveField(
            model_name='promotion',
            name='category_legacy',
        ),
    ]
