from django.db import migrations, models


def backfill_escort_service_items_by_games(apps, schema_editor):
    """存量回填：按陪玩已接游戏，把这些游戏下所有启用服务项默认加入可接服务项。

    避免上线后存量陪玩瞬间"无技能"；陪玩后续可在陪玩端「我的技能」页自行取消。
    """
    EscortProfile = apps.get_model('users', 'EscortProfile')
    ServiceItem = apps.get_model('orders', 'ServiceItem')
    for profile in EscortProfile.objects.iterator():
        game_ids = list(profile.game_categories.values_list('id', flat=True))
        if not game_ids:
            continue
        item_ids = list(
            ServiceItem.objects.filter(
                game_category_id__in=game_ids, is_active=True,
            ).values_list('id', flat=True)
        )
        if item_ids:
            profile.service_items.add(*item_ids)


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0018_gamecategory_icon'),
        ('users', '0022_escortprofile_game_categories'),
    ]

    operations = [
        migrations.AddField(
            model_name='escortprofile',
            name='service_items',
            field=models.ManyToManyField(blank=True, related_name='escorts', to='orders.serviceitem'),
        ),
        migrations.RunPython(backfill_escort_service_items_by_games, migrations.RunPython.noop),
    ]
