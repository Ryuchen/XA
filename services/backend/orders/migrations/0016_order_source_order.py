import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0015_order_support_contact'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='source_order',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='tip_orders', to='orders.order',
            ),
        ),
    ]
