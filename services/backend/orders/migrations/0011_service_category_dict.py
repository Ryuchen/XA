import django.db.models.deletion
from django.db import migrations, models


def seed_and_map(apps, schema_editor):
    """建分类字典种子，并把旧 category 枚举值映射到新 FK。"""
    ServiceCategory = apps.get_model('orders', 'ServiceCategory')
    ServiceItem = apps.get_model('orders', 'ServiceItem')
    normal = ServiceCategory.objects.create(name='陪玩服务', is_gift=False, sort_order=1)
    gift = ServiceCategory.objects.create(name='礼品套餐', is_gift=True, sort_order=2)
    mapping = {'NORMAL': normal, 'GIFT': gift}
    for item in ServiceItem.objects.all():
        cat = mapping.get(item.category)
        if cat:
            item.service_category = cat
            item.save(update_fields=['service_category'])


def unmap(apps, schema_editor):
    ServiceCategory = apps.get_model('orders', 'ServiceCategory')
    ServiceCategory.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0010_order_game_nickname_order_game_region_order_game_uid'),
    ]

    operations = [
        migrations.CreateModel(
            name='GameCategory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True)),
                ('remark', models.CharField(blank=True, default='', max_length=255)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['sort_order', 'id']},
        ),
        migrations.CreateModel(
            name='ServiceCategory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True)),
                ('is_gift', models.BooleanField(default=False, help_text='是否礼物类：影响看板礼物/游戏流水拆分')),
                ('remark', models.CharField(blank=True, default='', max_length=255)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['sort_order', 'id']},
        ),
        migrations.AddField(
            model_name='serviceitem',
            name='game_category',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='service_items', to='orders.gamecategory',
            ),
        ),
        migrations.AddField(
            model_name='serviceitem',
            name='service_category',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='service_items', to='orders.servicecategory',
            ),
        ),
        migrations.RunPython(seed_and_map, unmap),
        migrations.RemoveField(
            model_name='serviceitem',
            name='category',
        ),
    ]
