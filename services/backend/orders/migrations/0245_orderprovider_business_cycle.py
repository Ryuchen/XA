from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0244_points_handover_refund_invalidation')]

    operations = [
        migrations.AddField(model_name='orderprovider', name='provider_business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
    ]
