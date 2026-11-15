from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('engagement', '0035_default_payment_vip_enabled')]
    operations = [
        migrations.AddField(model_name='defaultpaymentpointssnapshot', name='vip_tier_id_snapshot',
                            field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='defaultpaymentpointssnapshot', name='vip_level_snapshot',
                            field=models.PositiveIntegerField(blank=True, null=True)),
    ]
