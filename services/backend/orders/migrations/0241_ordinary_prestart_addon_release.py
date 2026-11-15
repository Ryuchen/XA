from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0240_order_referral_cycle_snapshot')]
    operations = [
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='ordinary_prestart_snapshot',
            field=models.JSONField(null=True, blank=True)),
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='ordinary_prestart_hash',
            field=models.CharField(max_length=64, blank=True, default='')),
    ]
