from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0215_points_addon_release_snapshot')]

    operations = [
        migrations.AddField(
            model_name='order', name='commission_allocation_pending',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='order', name='commission_rate',
            field=models.PositiveSmallIntegerField(default=0, null=True),
        ),
        migrations.AlterField(
            model_name='order', name='provider_income',
            field=models.PositiveIntegerField(default=0, null=True),
        ),
        migrations.AlterField(
            model_name='order', name='shop_income',
            field=models.PositiveIntegerField(default=0, null=True),
        ),
    ]
