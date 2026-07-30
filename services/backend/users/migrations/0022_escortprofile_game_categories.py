from django.db import migrations, models


def backfill_escort_games_by_service_area(apps, schema_editor):
    """存量回填：按陪玩 service_area 文本匹配已有游戏类目名称，建立可接游戏关联。

    service_area 里包含某游戏类目名称（不区分大小写）即视为该陪玩可接此游戏。
    """
    EscortProfile = apps.get_model('users', 'EscortProfile')
    GameCategory = apps.get_model('orders', 'GameCategory')
    categories = list(GameCategory.objects.values_list('id', 'name'))
    if not categories:
        return
    for profile in EscortProfile.objects.iterator():
        area = (profile.service_area or '').lower()
        if not area:
            continue
        matched = [cid for cid, name in categories if name and name.lower() in area]
        if matched:
            profile.game_categories.add(*matched)


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0018_gamecategory_icon'),
        ('users', '0021_alter_customuser_openid_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='escortprofile',
            name='game_categories',
            field=models.ManyToManyField(blank=True, related_name='escorts', to='orders.gamecategory'),
        ),
        migrations.RunPython(backfill_escort_games_by_service_area, migrations.RunPython.noop),
    ]
