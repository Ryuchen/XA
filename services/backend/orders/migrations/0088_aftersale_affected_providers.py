from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0087_service_settlement_clocks')]

    operations = [
        migrations.AddField(
            model_name='aftersale', name='affected_provider_account_ids',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
