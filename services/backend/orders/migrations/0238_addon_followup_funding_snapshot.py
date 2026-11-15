from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0237_points_prestart_cancel_funding')]

    operations = [
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='points_followup_snapshot',
                            field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='points_followup_hash',
                            field=models.CharField(blank=True, default='', max_length=64)),
    ]
