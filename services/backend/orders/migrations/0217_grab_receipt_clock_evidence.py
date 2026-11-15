from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0216_order_commission_pending')]

    operations = [
        migrations.AddField(model_name='ordergrabreceiptprobe', name='clock_evidence',
                            field=models.JSONField(blank=True, editable=False, null=True)),
    ]
