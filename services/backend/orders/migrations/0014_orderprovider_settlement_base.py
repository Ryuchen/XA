from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0013_orderprovider_commission_fixed_and_more')]

    operations = [
        migrations.AddField(
            model_name='orderprovider',
            name='settlement_base',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
