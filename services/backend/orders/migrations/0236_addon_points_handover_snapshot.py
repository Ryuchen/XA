from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0235_addon_completion_points_posting')]
    operations = [
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='points_handover_snapshot',
                            field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='points_handover_hash',
                            field=models.CharField(blank=True, default='', max_length=64)),
    ]
