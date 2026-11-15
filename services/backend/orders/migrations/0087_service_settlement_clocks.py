from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0086_order_gift_aftersale_hours_order_gift_freeze_hours')]
    operations = [
        migrations.AddField(model_name='order', name='service_aftersale_hours', field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='order', name='service_freeze_hours', field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='order', name='service_clock_version', field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='order', name='service_deposit_percent', field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True)),
    ]
