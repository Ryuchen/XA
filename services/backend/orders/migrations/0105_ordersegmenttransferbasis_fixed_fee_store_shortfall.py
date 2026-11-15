from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0104_ordersegmenttransferbasis')]

    operations = [
        migrations.AddField(
            model_name='ordersegmenttransferbasis',
            name='fixed_fee_store_shortfall',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
