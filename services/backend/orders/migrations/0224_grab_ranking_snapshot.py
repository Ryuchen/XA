from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0223_addon_points_payment_funding_snapshot')]

    operations = [
        migrations.AddField(model_name='ordergrabsingleroundstate', name='ranking_snapshot',
                            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='ordergrabteamroundstate', name='ranking_snapshot',
                            field=models.JSONField(blank=True, default=dict)),
    ]
