from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0247_order_gift_provider_cycle')]
    operations = [migrations.AddField(
        model_name='ordergrabreceiptprobe', name='invitee_business_cycle',
        field=models.PositiveIntegerField(blank=True, editable=False, null=True),
    )]
