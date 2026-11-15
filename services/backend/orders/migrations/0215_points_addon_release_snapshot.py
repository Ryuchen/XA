from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0214_work_original_shares')]

    operations = [
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='points_addon_snapshot',
                            field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='points_addon_hash',
                            field=models.CharField(blank=True, default='', max_length=64)),
    ]
